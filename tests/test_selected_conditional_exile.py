"""Resolution choices and immediate source-departure returns for complete originals."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_exile_return_model import SELECTED_CONDITIONAL_EXILE_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class SelectedConditionalExileTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "selected-conditional-exile.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/selected-conditional-exile-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Selected conditional exile", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_keep_every_sibling_and_require_their_source_trigger(self):
        for name, count in (("Glorious Protector", 4), ("Abdel Adrian, Gorion's Ward", 2)):
            with self.subTest(name=name):
                admitted, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                programs = semantics.programs_for_oracle(card.oracle_id)
                self.assertEqual(count, len(programs))
                trigger, = (program for program in programs if program.event == "permanent.enter.self")
                self.assertIsNone(trigger.target_schema)
                instruction, = trigger.effects
                self.assertIn(SELECTED_CONDITIONAL_EXILE_FIELD, instruction)
                forged = {**instruction, "player": "$opponent"}
                for changed in (replace(trigger, effects=[forged]), replace(trigger, event="step.begin")):
                    self.assertFalse(bind_semantic_program_runtime(
                        changed, capability_registry=self.registry, profile="commander_review",
                    )["strict"])
                altered_text = card.oracle_text.replace("you control until", "an opponent controls until")
                self.assertNotEqual("exact", compile_oracle_card(
                    replace(card, oracle_text=altered_text), capability_registry=self.registry,
                    capability_profile="commander_review",
                ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def offer(self, game, seat="A"):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", [seat]), (pending.kind, pending.actors))
        spec = pending.payload_by_actor[seat]["legal_actions"][0]["choice_schema"]
        return set(spec["legal_refs"])

    def select(self, game, seat, refs, *, allowed=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", "objects": list(refs)})
        self.assertEqual(allowed, result.ok, result.summary)
        if not allowed:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_foretold_protector_chooses_live_nonangels_and_returns_on_departure(self):
        game = self.playing(61031101)
        source = self.add(game.engine, "Glorious Protector", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", ref="owned-protector-bear")
        angel = self.add(game.engine, "Angel of Mercy")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign-protector-bear")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        for index in range(3):
            self.add(game.engine, "Plains", ref=f"protector-foretell-mana-{index}")
        for card in (source, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        stage, = (row for row in actions if row.get("action") == "stage_cast_lifecycle" and row.get("card") == source.ref)
        result = game.act("pilot:A", {"action_id": stage["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(self.current(game, source).face_down)
        self.assertNotIn("Glorious Protector", json.dumps(game.packet("pilot:B", full=True)))
        self.until(game, lambda current: current.state.active_player == "B" and current.state.step == "upkeep")
        self.priority(game, "A")
        foretold = self.current(game, source)
        offer = self.action(game, "A", foretold)
        result = game.act("pilot:A", {"action_id": offer["id"], "from": "exile", "cost_option": "foretell", "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual({bear.ref}, self.offer(game))
        self.assertIn("flying", game.engine._combat_keywords(self.current(game, source)))
        for actor, refs in (("A", [angel.ref]), ("A", [foreign.ref]), ("B", [bear.ref]), ("A", [bear.ref, bear.ref])):
            self.select(game, actor, refs, allowed=False)
        game = self.replay(game, load=True)
        self.select(game, "A", [bear.ref])
        self.assertIsNone(self.resolve(game))
        held = self.current(game, bear)
        self.assertEqual("exile", held.zone)
        exile_incarnation = held.logical_object_id
        self.cast_for(game, "B", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "A"), (self.current(game, bear).zone, self.current(game, bear).controller))
        self.assertNotEqual(exile_incarnation, self.current(game, bear).logical_object_id)
        self.assertEqual("hand", self.current(game, source).zone)
        self.assertEqual("battlefield", self.current(game, angel).zone)
        self.replay(game, load=True)

    def test_actual_type_loss_allows_the_protector_to_choose_itself_and_return_immediately(self):
        game = self.session(61031102)
        source = self.add(game.engine, "Glorious Protector", zone="hand")
        strip = self.add(game.engine, "Nameless Inversion", seat="B", zone="hand")
        for card in (source, strip):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 2})
        game.state.players["B"].mana_pool.update({"C": 1, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        first_identity = self.current(game, source).logical_object_id
        self.cast_for(game, "B", strip, [source.ref])
        self.resolve_top(game)
        self.assertEqual({source.ref}, self.offer(game))
        self.select(game, "A", [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", self.current(game, source).zone)
        self.assertNotEqual(first_identity, self.current(game, source).logical_object_id)
        self.assertIn("flying", game.engine._combat_keywords(self.current(game, source)))
        self.replay(game, load=True)

    def soldiers(self, game):
        return {card.object_id: card for card in game.state.cards.values()
                if card.is_token and card.zone == "battlefield" and card.printed_name == "Soldier Token"}

    def test_actual_abdel_exiles_token_and_artifact_then_creates_two_soldiers(self):
        game = self.session(61031103)
        source = self.add(game.engine, "Abdel Adrian, Gorion's Ward", zone="hand")
        raise_tokens = self.add(game.engine, "Raise the Alarm", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        ring = self.add(game.engine, "Sol Ring")
        land = self.add(game.engine, "Plains", ref="abdel-excluded-land")
        for card in (source, raise_tokens, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "W": 2})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", raise_tokens, reserve={"C": 4, "W": 1})
        self.assertIsNone(self.resolve(game))
        originals = self.soldiers(game)
        self.assertEqual(2, len(originals))
        selected, = tuple(originals.values())[:1]
        self.cast_for(game, "A", source)
        offered = self.offer(game)
        self.assertEqual({ring.ref, *(card.ref for card in originals.values())}, offered)
        self.select(game, "A", [land.ref], allowed=False)
        self.select(game, "A", [source.ref], allowed=False)
        declined = self.replay(game, load=True)
        self.select(declined, "A", [])
        self.assertIsNone(self.resolve(declined))
        self.assertEqual(set(originals), set(self.soldiers(declined)))
        self.assertEqual("battlefield", self.current(declined, ring).zone)
        self.replay(declined, load=True)
        self.select(game, "A", [selected.ref, ring.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", self.current(game, ring).zone)
        self.assertEqual(2, len(set(self.soldiers(game)) - set(originals)))
        self.assertNotIn(selected.object_id, self.soldiers(game))
        self.cast_for(game, "B", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", self.current(game, ring).zone)
        self.assertEqual(3, len(self.soldiers(game)))
        self.assertNotIn(selected.object_id, self.soldiers(game))
        self.replay(game, load=True)

    def test_actual_original_source_departure_before_resolution_skips_exile_and_tokens(self):
        game = self.session(61031104)
        source = self.add(game.engine, "Abdel Adrian, Gorion's Ward", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        for card in (source, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "W": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.cast_for(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", self.current(game, source).zone)
        self.assertEqual("battlefield", self.current(game, bear).zone)
        self.assertEqual({}, self.soldiers(game))
        self.replay(game, load=True)
