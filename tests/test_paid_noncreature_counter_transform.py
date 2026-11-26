"""Actual paid spell occurrences and ordered original-source transformation."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.paid_noncreature_counter_transform import CAPABILITY, paid_cast_transform_runtime_dependencies
from scripts.build_test_database import build_fixture_database

FRONT = "The Emperor of Palamecia"
BACK = "The Lord Master of Hell"
CARD = FRONT + " // " + BACK
FIXTURES = ROOT / "tests" / "fixtures"


class PaidNoncreatureCounterTransformTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {"Inspiration", "Goblin Electromancer", "Hardened Scales", "Cloudshift",
                   "Grizzly Bears", "Plains", "Island", "Sol Ring", "Twiddle", "Generic Bound Growth",
                   "Generic Bound Commander", "Generic Bound Plains"}
        pieces = [FIXTURES / "mv-trigger-mana-transform-cards.json"]
        for name in ("bound-effect-program-cards", "increment-cards", "activation-restriction-material-cards",
                     "granted-static-characteristic-cards"):
            document = json.loads((FIXTURES / (name + ".json")).read_bytes())
            cards = [row for row in document["cards"] if row["name"] in support]
            support.difference_update(row["name"] for row in cards)
            if cards:
                piece = folder / (name + ".json")
                piece.write_text(json.dumps({"schema_version": 1, "cards": cards}), encoding="utf-8")
                pieces.append(piece)
        assert not support, support
        path = folder / "paid-counter-transform.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid counter transform", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def compile_original(self, registry=None):
        return compile_oracle_card(self.db.lookup(CARD), capability_registry=registry or self.registry,
                                   capability_profile="commander_review")

    def source(self, game, count=0):
        source = self.add(game.engine, CARD)
        source.active_face = FRONT
        source.acquired_control_turn_count = -1
        if count:
            source.counters["+1/+1"] = count
        self.assert_closed(game, source)
        return source

    def activate_mana(self, game, source, color):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(row for row in actions if row["id"].startswith("activate:" + source.ref + ":"))
        command = {"action_id": offer["id"], "mana_output": {color: 1}, "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, game.state.players["A"].mana_pool[color])

    def test_original_wire_payment_and_transform_authorities_are_closed(self):
        actual = self.compile_original()
        self.assertEqual("exact", actual.status, actual.material_residuals)
        self.assertEqual([2, 1], [len(face.nodes) for face in actual.faces])
        game = self.session(8021440)
        source = self.source(game)
        program = next(p for p in game.engine.semantics.programs_for_oracle(source.oracle_id)
                       if CAPABILITY in p.capability_dependencies)
        self.assertIn(CAPABILITY, paid_cast_transform_runtime_dependencies(program))
        for changed in (replace(program, effects=tuple(reversed(program.effects))),
                        replace(program, event_condition={"field": "mana_value", "op": "gte", "value": 4}),
                        replace(program, effects=(program.effects[0], {**program.effects[1], "card": "$target.0"}))):
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    paid_cast_transform_runtime_dependencies(changed)
        for capability in (CAPABILITY, "counter.producer.fixed_effect", "permanent.transform.face_change"):
            with self.subTest(capability=capability):
                raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
                entry = next(row for row in raw["capabilities"] if row["id"] == capability)
                entry.update(status="blocked", blockers=["Scoped original"])
                self.assertNotEqual("exact", self.compile_original(CapabilityRegistry(raw)).status)

    def test_actual_exact_payment_places_then_transforms_and_back_attacks_after_replay(self):
        game = self.session(8021441)
        source = self.source(game, 2)
        spell = self.add(game.engine, "Inspiration", zone="hand")
        untap = self.add(game.engine, "Twiddle", zone="hand")
        self.add(game.engine, "Island")
        for name in ("Sol Ring", "Grizzly Bears", "Plains"):
            self.add(game.engine, name, zone="graveyard")
        hidden = self.add(game.engine, "Generic Bound Growth", zone="hand", seat="C", ref="emperor-private")
        self.prepare(game, mana={"C": 3})
        self.checkpoint(game)
        self.activate_mana(game, source, "U")
        self.cast(game, "A", spell, ["A"])
        trigger, = [item for item in game.state.stack if item.kind == "triggered_ability"]
        self.assertEqual((4, 0), (trigger.context["mana_spent_total"], trigger.context["source_transform_count"]))
        self.assertEqual(2, game.state.cards[source.object_id].counters["+1/+1"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        live = game.state.cards[source.object_id]
        self.assertEqual((BACK, 3, 1), (live.active_face, live.counters["+1/+1"], live.transform_count))
        self.assertEqual(source.logical_object_id, live.logical_object_id)
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:D", full=True)))
        game = self.replay(game, load=True)
        self.cast(game, "A", untap, [game.state.cards[source.object_id].ref])
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        result = game.act("pilot:A", {"action_id": "choose", "choice": "untap"})
        self.assertTrue(result.ok, result.summary)
        self.settle(game)
        self.attack(game, [(game.state.cards[source.object_id], "B")])
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 37, 37, 37], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_three_paid_mana_and_a_foreign_four_paid_spell_do_not_trigger(self):
        for actor in ("A", "B"):
            with self.subTest(actor=actor):
                game = self.session(8021442 + (actor == "B"))
                source = self.source(game, 2)
                spell = self.add(game.engine, "Inspiration", seat=actor, zone="hand")
                if actor == "A":
                    self.add(game.engine, "Goblin Electromancer")
                    self.prepare(game, mana={"U": 1, "C": 2})
                else:
                    game.state.players[actor].mana_pool.update({"U": 1, "C": 3})
                    self.prepare(game)
                self.checkpoint(game)
                self.cast(game, actor, spell, [actor])
                self.assertEqual(0, self.trigger_count(game, source))
                self.assertEqual(3 if actor == "A" else 4, game.state.stack[-1].context["mana_spent_total"])
                self.assertIsNone(self.resolve(game))
                live = game.state.cards[source.object_id]
                self.assertEqual((FRONT, 2, 0), (live.active_face, live.counters["+1/+1"], live.transform_count))
                self.replay(game, load=True)

    def test_actual_counter_replacement_and_source_departure_use_the_original_incarnation(self):
        for departure in (False, True):
            with self.subTest(departure=departure):
                game = self.session(8021445 + departure)
                source = self.source(game, 1)
                spell = self.add(game.engine, "Inspiration", zone="hand")
                self.add(game.engine, "Hardened Scales")
                blink = self.add(game.engine, "Cloudshift", zone="hand")
                self.prepare(game, mana={"U": 1, "C": 3, "W": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", spell, ["A"], reserve={"W": 1})
                self.assertEqual(1, self.trigger_count(game, source))
                if departure:
                    original_identity = game.state.cards[source.object_id].logical_object_id
                    self.cast(game, "A", blink, [source.ref])
                    self.resolve_top(game)
                    self.assertNotEqual(original_identity, game.state.cards[source.object_id].logical_object_id)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                live = game.state.cards[source.object_id]
                self.assertEqual((FRONT, 0, 0) if departure else (BACK, 3, 1),
                                 (live.active_face or FRONT, live.counters.get("+1/+1", 0), live.transform_count))
                self.replay(game, load=True)

    def test_actual_red_mana_cannot_pay_a_creature_spell(self):
        game = self.session(8021447)
        source = self.source(game)
        creature = self.add(game.engine, "Grizzly Bears", zone="hand")
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)
        self.activate_mana(game, source, "R")
        self.priority(game, "A")
        self.assertFalse(any(row["id"] == "cast:" + creature.ref
                            for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "cast:" + creature.ref, "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.replay(game, load=True)

    def test_dropping_the_original_paid_transform_is_killed(self):
        def witness():
            self.assertEqual("exact", self.compile_original().status)
        witness()
        with patch("quorune.compiler.controlled_spell_counter_nodes.paid_noncreature_counter_transform_node", return_value=None):
            with self.assertRaises(AssertionError):
                witness()
