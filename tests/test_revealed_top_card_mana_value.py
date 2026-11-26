"""Original reveal-to-hand effects quote the actual revealed card mana value."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.library_selection_capability_shapes import revealed_top_hand_life_effect
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CONFIDANT = "Dark Confidant"
TUTELAGE = "Dark Tutelage"
SEER = "Pain Seer"
AUGUR = "Darkstar Augur"
AUGUR_ORACLE = "67d2a021-a042-4e5f-b6e4-39cb35514794"


class RevealedTopCardManaValueTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "revealed-top-card-mana-value.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/revealed-top-card-mana-value-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Reveal exact library top", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_share_the_public_transfer_and_bound_mana_value(self):
        for name, event in ((CONFIDANT, "step.begin"), (TUTELAGE, "step.begin"),
                            (SEER, "permanent.untap"), (AUGUR, "step.begin")):
            with self.subTest(card=name):
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                if name == AUGUR:
                    self.assertEqual(AUGUR_ORACLE, card.oracle_id)
                    self.assertEqual("normal", card.layout)
                programs = semantics.programs_for_oracle(card.oracle_id)
                trigger, = (p for p in programs if p.event == event)
                self.assertEqual([revealed_top_hand_life_effect()], trigger.effects)
                self.assertIsNone(trigger.target_schema)
                self.assertIn("life.change.effect", trigger.capability_dependencies)
                for text in (
                    card.oracle_text.replace("reveal the top card", "look at the top card"),
                    card.oracle_text.replace("mana value", "power"),
                    card.oracle_text.replace("put that card into your hand", "put that card into your graveyard"),
                ):
                    ir = compile_oracle_card(
                        replace(card, oracle_text=text), capability_registry=self.registry, capability_profile="commander_review",
                    )
                    self.assertNotEqual("exact", ir.status)

    def wait_for_upkeep(self, game, sources):
        def triggered(current):
            return all(self.trigger_count(current, card) == 1 for card in sources)

        for _ in range(len(game.state.players) + 1):
            self.until(game, lambda current: triggered(current)
                       or current.state.pending_decision.kind == "cleanup.discard")
            if triggered(game):
                break
            principal = game.pending_principals()[0]
            ctx = game.packet(principal, full=True)["decision"]["ctx"]
            result = game.act(principal, {
                "action_id": "discard", "cards": [row["id"] for row in ctx["hand"][:ctx["count"]]],
            })
            self.assertTrue(result.ok, result.summary)
        self.assertTrue(triggered(game))
        self.assertEqual(("A", "upkeep"), (game.state.active_player, game.state.step))
        self.assertTrue(all(not item.targets for item in game.state.stack))

    def test_actual_confidant_publicly_reveals_and_moves_the_x_card_for_printed_value_without_drawing(self):
        game = self.session(20203061)
        source = self.add(game.engine, CONFIDANT)
        deeper = self.add(game.engine, "Swamp", zone="library", ref="confidant-unrevealed-deeper")
        card = self.add(game.engine, "Feral Hydra", zone="library", ref="confidant-revealed-physical-top")
        self.assert_closed(game, source)
        self.prepare(game, actor="D", active="D")
        self.checkpoint(game)
        self.wait_for_upkeep(game, (source,))
        observer = json.dumps(game.packet("pilot:B", full=True))
        self.assertNotIn(card.ref, observer)
        self.assertNotIn(deeper.ref, observer)
        game = self.replay(game, load=True)
        hand = list(game.state.players["A"].zones["hand"])
        life = game.state.players["A"].life
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 1, game.state.players["A"].life)
        self.assertEqual(hand + [card.object_id], game.state.players["A"].zones["hand"])
        codes = [event.code for event in game.state.events[cursor:]]
        self.assertLess(codes.index("library.look"), codes.index("library.selection"))
        self.assertLess(codes.index("library.selection"), codes.index("effect.life"))
        self.assertNotIn("card.draw", codes)
        observer = json.dumps(game.packet("pilot:B", full=True))
        self.assertIn(card.ref, observer)
        self.assertNotIn(deeper.ref, observer)
        self.replay(game, load=True)

    def test_actual_tutelage_handles_a_zero_value_land_and_an_empty_library(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                game = self.session(20203062 + int(empty))
                source = self.add(game.engine, TUTELAGE)
                land = None if empty else self.add(game.engine, "Forest", zone="library", ref="tutelage-zero-cost-land")
                if empty:
                    for oid in list(game.state.players["A"].zones["library"]):
                        game.engine.move_card(oid, "exile", log=False, semantic_events=False)
                self.assert_closed(game, source)
                self.prepare(game, actor="D", active="D")
                self.checkpoint(game)
                self.wait_for_upkeep(game, (source,))
                life = game.state.players["A"].life
                hand = list(game.state.players["A"].zones["hand"])
                cursor = len(game.state.events)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(life, game.state.players["A"].life)
                self.assertEqual(hand + ([land.object_id] if land is not None else []), game.state.players["A"].zones["hand"])
                codes = [event.code for event in game.state.events[cursor:]]
                self.assertNotIn("effect.life", codes)
                self.assertEqual(not empty, "library.selection" in codes)
                self.replay(game, load=True)

    def test_actual_pain_seer_untap_reads_the_current_top_after_the_enclosing_spells_draw(self):
        game = self.session(20203064)
        source = self.add(game.engine, SEER)
        source.tapped = True
        wisps = self.add(game.engine, "Cerulean Wisps", zone="hand")
        reveal = self.add(game.engine, "Fire // Ice", zone="library", ref="next-after-wisps")
        drawn = self.add(game.engine, "Island", zone="library", ref="drawn-by-wisps")
        self.assert_closed(game, source)
        self.assert_closed(game, wisps)
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        life = game.state.players["A"].life
        self.cast_for(game, "A", wisps, [source.ref])
        self.resolve_top(game)
        self.until(game, lambda state: self.trigger_count(state, source) == 1)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual("hand", game.state.cards[drawn.object_id].zone)
        self.assertEqual("library", game.state.cards[reveal.object_id].zone)
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 4, game.state.players["A"].life)
        self.assertEqual("hand", game.state.cards[reveal.object_id].zone)
        self.assertFalse(any(event.code.startswith("card.draw") for event in game.state.events[cursor:]))
        observer = json.dumps(game.packet("pilot:B", full=True))
        self.assertIn(reveal.ref, observer)
        self.assertNotIn(drawn.ref, observer)
        self.replay(game, load=True)

    def test_actual_paid_augur_copies_the_whole_upkeep_result_and_retains_flying(self):
        game = self.session(20203065)
        original = self.add(game.engine, AUGUR, zone="hand")
        second = self.add(game.engine, "Plains", zone="library", ref="augur-second-public-top")
        first = self.add(game.engine, "Feral Hydra", zone="library", ref="augur-first-public-top")
        self.assertEqual(AUGUR_ORACLE, original.oracle_id)
        self.assert_closed(game, original)
        self.prepare(game, mana={"C": 2, "B": 2})
        self.checkpoint(game)
        action = self.action(game, "A", original)
        option, = (item for item in action["cost_options"] if "+offspring:" in item["id"])
        command = {"action_id": action["id"], "cost_option": option["id"], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        cast = game.act("pilot:A", command)
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve(game))
        copy, = (row for row in game.state.cards.values() if row.zone == "battlefield" and row.is_token
                 and row.oracle_id == original.oracle_id)
        copiable = game.engine._effective_card_data(copy)
        self.assertEqual((1, 1), (int(copiable["power"]), int(copiable["toughness"])))
        self.assertIn("flying", {word.casefold() for word in copiable["keywords"]})
        self.wait_for_upkeep(game, (original, copy))
        life = game.state.players["A"].life
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 1, game.state.players["A"].life)
        self.assertEqual([first.object_id, second.object_id], game.state.players["A"].zones["hand"][-2:])
        self.replay(game, load=True)
