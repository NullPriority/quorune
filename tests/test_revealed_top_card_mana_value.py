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

    def _ad_nauseam_start(self, game, source):
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "B": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [])

    def _ad_nauseam_choice(self, game, choice):
        before = authoritative_state_hash(game.state)
        payload = {"action_id": "choose", "choice": choice}
        self.assertFalse(game.act("pilot:B", payload).ok)
        self.assertFalse(game.act("pilot:A", {**payload, "choice": "unoffered"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", payload)
        self.assertTrue(result.ok, result.summary)

    def test_ad_nauseam_reveals_and_moves_each_chosen_top_before_offering_the_next_iteration(self):
        game = self.session(20203101)
        source = self.add(game.engine, "Ad Nauseam", zone="hand")
        hidden = self.add(game.engine, "Swamp", zone="library", ref="nauseam-unrevealed")
        second = self.add(game.engine, "Fire // Ice", zone="library", ref="nauseam-split-value")
        first = self.add(game.engine, "Feral Hydra", zone="library", ref="nauseam-x-value")
        self._ad_nauseam_start(game, source)
        life = game.state.players["A"].life
        cursor = len(game.state.events)
        original = game.state.stack[-1].ref
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(["pilot:A"], game.pending_principals())
        self.assertEqual(original, game.state.stack[-1].ref)
        self.assertEqual(life - 1, game.state.players["A"].life)
        self.assertEqual("hand", game.state.cards[first.object_id].zone)
        self.assertEqual("library", game.state.cards[second.object_id].zone)
        for principal in ("pilot:B", "pilot:C", "spectator"):
            packet = game.packet(principal, full=True)
            self.assertIsNone(packet["decision"])
            encoded = json.dumps(packet)
            if principal.startswith("pilot:"):
                self.assertIn(first.ref, encoded)
            self.assertNotIn(second.ref, encoded)
            self.assertNotIn(hidden.ref, encoded)
        codes = [event.code for event in game.state.events[cursor:]
                 if event.code in {"library.look", "library.selection", "effect.life", "card.draw"}]
        self.assertEqual(["library.look", "library.selection", "effect.life"], codes)
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self._ad_nauseam_choice(game, "repeat")
        self.assertEqual(original, game.state.stack[-1].ref)
        self.assertEqual(["pilot:A"], game.pending_principals())
        self.assertEqual(life - 5, game.state.players["A"].life)
        self.assertEqual("hand", game.state.cards[second.object_id].zone)
        self.assertIn(second.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:B", full=True)))
        codes = [event.code for event in game.state.events[cursor:]
                 if event.code in {"library.look", "library.selection", "effect.life", "card.draw"}]
        self.assertEqual(["library.look", "library.selection", "effect.life"], codes)
        game = self.replay(game, load=True)
        self._ad_nauseam_choice(game, "stop")
        self.assertIsNone(self.resolve(game))
        self.assertEqual("library", game.state.cards[hidden.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_ad_nauseam_with_an_empty_library_or_last_zero_cost_card_finishes(self):
        for with_land in (False, True):
            with self.subTest(with_land=with_land):
                game = self.session(20203102 + with_land)
                source = self.add(game.engine, "Ad Nauseam", zone="hand")
                for oid in tuple(game.state.players["A"].zones["library"]):
                    game.engine.move_card(oid, "exile", log=False, semantic_events=False)
                land = self.add(game.engine, "Swamp", zone="library", ref="nauseam-last-land") if with_land else None
                self._ad_nauseam_start(game, source)
                life = game.state.players["A"].life
                cursor = len(game.state.events)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(life, game.state.players["A"].life)
                self.assertEqual([], game.state.players["A"].zones["library"])
                if land is not None:
                    self.assertEqual("hand", game.state.cards[land.object_id].zone)
                codes = [event.code for event in game.state.events[cursor:]]
                self.assertEqual(int(with_land), codes.count("library.look"))
                self.assertNotIn("card.draw", codes)
                self.assertNotIn("effect.life", codes)
                self.replay(game, load=True)

    def test_ad_nauseam_can_continue_at_negative_life_until_the_spell_finishes(self):
        game = self.session(20203106)
        source = self.add(game.engine, "Ad Nauseam", zone="hand")
        next_card = self.add(game.engine, "Feral Hydra", zone="library", ref="nauseam-negative-second")
        self.add(game.engine, "Fire // Ice", zone="library", ref="nauseam-negative-first")
        game.state.players["A"].life = 1
        self._ad_nauseam_start(game, source)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(-3, game.state.players["A"].life)
        self.assertTrue(game.state.players["A"].in_game)
        game = self.replay(game, load=True)
        self._ad_nauseam_choice(game, "repeat")
        self.assertEqual("hand", game.state.cards[next_card.object_id].zone)
        self.assertEqual(-4, game.state.players["A"].life)
        self.assertTrue(game.state.players["A"].in_game)
        self._ad_nauseam_choice(game, "stop")
        self.assertFalse(game.state.players["A"].in_game)
        self.replay(game, load=True)

    def test_ad_nauseam_exact_lowering_requires_the_printed_public_transfer_and_repeat(self):
        from unittest.mock import patch

        from quorune.rules.repeat_top_hand_life import REPEAT_TOP_HAND_LIFE_FIELD
        record = self.db.lookup("Ad Nauseam", fuzzy=False)
        def witness():
            ir = compile_oracle_card(record, capability_registry=self.registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            effect = ir.faces[0].nodes[0].effects[0]
            self.assertEqual({"schema_version": 1, "stage": "start"}, effect[REPEAT_TOP_HAND_LIFE_FIELD])
        witness()
        with patch("quorune.compiler.fixed_library_selection_templates.RevealedTopHandLifeEffectTemplate.compiled",
                   return_value=(None, (), None, ())):
            with self.assertRaises(AssertionError):
                witness()
        for text in (record.oracle_text.replace("Reveal", "Look at"),
                     record.oracle_text.replace("mana value", "power"),
                     record.oracle_text.replace("any number of times", "exactly three times")):
            ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry)
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
