"""Native instant-timed whole hand and source sacrifice payment."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.model import ReplacementEffectError
from quorune.replacement.replay import ReplacementContinuation
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.casting.whole_zone_cost_frame import WHOLE_ZONE_COST_FRAME_FIELD
from quorune.rules.whole_hand_mana import CAPABILITY, HANDLER_ID
from scripts.build_test_database import build_fixture_database


NAME = "Lion's Eye Diamond"


class WholeHandManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        path = Path(cls.temp.name) / "whole-hand-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/whole-hand-mana-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Whole-hand mana test", [DeckEntry(commander, 1, "commander"),
                                                          DeckEntry("Plains", 30)], [commander])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def accepted(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def rejected(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def scene(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, NAME)
        for oid in tuple(game.state.players["A"].zones["hand"]):
            game.engine.move_card(oid, "library", log=False)
        self.assert_closed(game, source)
        return game, source

    def offer(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (r for r in actions if r.get("source") == source.ref)
        self.assertEqual(1, action["cost_summary"]["discard_hand"])
        self.assertEqual(1, action["cost_summary"]["sac_self"])
        return action

    def test_whole_hand_mana_requires_its_exact_instant_timed_typed_carrier(self):
        game, source = self.scene(3045001)
        record = self.db.by_oracle_id(source.oracle_id)
        def compile_card(card):
            return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", compile_card(record).status)
        for changed in (record.oracle_text.replace("an instant", "a sorcery"),
                        record.oracle_text.replace("your hand", "a card")):
            with self.subTest(changed=changed):
                self.assertNotEqual("exact", compile_card(replace(record, oracle_text=changed)).status)
        owner, = (p for p in game.engine.semantics.programs_for_oracle(source.oracle_id)
                  if CAPABILITY in p.capability_dependencies)
        invalid = replace(owner, handlers=[h for h in owner.handlers if h.get("handler_id") != HANDLER_ID])
        proof = bind_semantic_program_runtime(invalid, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(proof["strict"], proof)
        self.assertTrue(any("matching exact catalog and mana handler" in b for b in proof["blockers"]), proof)

    def test_actual_diamond_keeps_priority_and_cannot_pay_for_a_spell_being_announced(self):
        game, source = self.scene(3045002)
        own = self.add(game.engine, "Lightning Bolt", zone="hand", ref="own-discard")
        second = self.add(game.engine, "Plains", zone="hand", ref="second-discard")
        incoming = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="incoming-bolt")
        self.prepare(game)
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.assertNotIn(source.ref, [r.ref for r in game.engine.available_mana_sources("A")])
        self.rejected(game, "A", {"a": "cast", "card": own.ref, "targets": ["C"], "pay": "auto"})
        self.cast(game, "B", incoming, ["C"])
        action = self.offer(game, source)
        allowed = {"action_id": action["id"], "mana_output": {"R": 3}, "pay": "auto"}
        self.rejected(game, "B", allowed)
        self.rejected(game, "A", {**allowed, "cost_cards": [second.ref]})
        self.rejected(game, "A", {**allowed, "mana_output": {"R": 2, "W": 1}})
        self.accepted(game, "A", allowed)
        self.assertEqual([incoming.object_id], [item.card_object_id for item in game.state.stack])
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[own.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[second.object_id].zone)
        self.assertEqual([], game.state.players["A"].zones["hand"])
        self.assertEqual(3, game.state.players["A"].mana_pool["R"])
        self.replay(game, load=True)

    def test_actual_diamond_accepts_an_empty_hand_and_produces_one_color(self):
        game, source = self.scene(3045003)
        self.prepare(game)
        self.checkpoint(game)
        action = self.offer(game, source)
        modes = {tuple(row["value"].items()) for row in action["choice_schema"]["mana_output"]["options"]}
        self.assertEqual({((color, 3),) for color in "WUBRG"}, modes)
        self.accepted(game, "A", {"action_id": action["id"], "mana_output": {"U": 3}, "pay": "auto"})
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual([], game.state.players["A"].zones["hand"])
        self.assertEqual(3, game.state.players["A"].mana_pool["U"])
        self.replay(game, load=True)

    def test_actual_diamond_replacement_resume_keeps_the_whole_private_cost_sealed(self):
        game, source = self.scene(3045004)
        cards = [self.add(game.engine, "Plains", zone="hand", ref=f"whole-private-{index}") for index in range(2)]
        self.add(game.engine, "Rest in Peace", seat="B")
        self.add(game.engine, "Leyline of the Void", seat="B")
        self.prepare(game)
        self.checkpoint(game)
        action = self.offer(game, source)
        self.accepted(game, "A", {"action_id": action["id"], "mana_output": {"B": 3}, "pay": "auto"})
        phases = set()
        for index in range(8):
            pending = game.state.pending_decision
            if pending.kind != "replacement.order":
                break
            frame = pending.continuation["priority_response"][WHOLE_ZONE_COST_FRAME_FIELD]
            phases.add(frame["groups"][-1]["origin"])
            self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
            self.assertTrue(all(game.state.cards[card.object_id].zone == "hand" for card in cards))
            self.assertEqual(0, game.state.players["A"].mana_pool["B"])
            for principal in ("spectator", "pilot:B", "pilot:C", "pilot:D"):
                view = json.dumps(game.packet(principal, full=True))
                self.assertTrue(all(card.ref not in view for card in cards))
            if index == 0:
                bad = deepcopy(pending.continuation)
                bad["priority_response"][WHOLE_ZONE_COST_FRAME_FIELD]["groups"][-1]["members"].clear()
                with self.assertRaises(ReplacementEffectError):
                    ReplacementContinuation.from_dict(bad)
                game = self.replay(game, load=True)
            choice = game.packet("pilot:A", full=True)["decision"]["ctx"]
            command = {"action_id": "choose", "replacement": choice["options"][0]["id"]}
            if choice.get("event_order_options"):
                command["replacement_event"] = choice["event_order_options"][0]
            self.rejected(game, "D", command)
            self.accepted(game, "A", command)
        self.assertEqual({"hand", "battlefield"}, phases)
        self.assertEqual({"exile"}, {game.state.cards[c.object_id].zone for c in (source, *cards)})
        self.assertEqual(3, game.state.players["A"].mana_pool["B"])
        self.replay(game, load=True)
