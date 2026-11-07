"""CR 107.1b and 603.4 for public creature power thresholds."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.continuous_conditions import FixedPublicStateConditionError, FixedPublicStateConditionSpec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_characteristic_aggregate_model import (
    DISTINCT_PUBLIC_CHARACTERISTIC_COUNT_CAPABILITY, PUBLIC_POWER_THRESHOLD_CAPABILITY,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_public_history_queries as history_controls


_COVEN = "you control three or more creatures with different powers"
_FORMIDABLE = "creatures you control have total power 8 or greater"


class PublicPowerThresholdCompilerTests(unittest.TestCase):
    def test_power_thresholds_declare_closed_signed_reductions(self):
        registry = load_default_capability_registry()
        for text, amount, reduction in ((_COVEN, 3, "distinct_count"), (_FORMIDABLE, 8, "sum")):
            with self.subTest(text=text):
                spec = fixed_public_state_condition(text, source_name="Power witness")
                self.assertIsNotNone(spec)
                self.assertEqual((amount, reduction), (spec.amount, spec.aggregate.reduction))
                self.assertEqual(spec, FixedPublicStateConditionSpec.from_dict(spec.to_dict()))
                ir = compile_oracle_card(record(
                    f"At the beginning of your end step, if {text}, draw a card.", "Creature",
                ), capability_registry=registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertIn(PUBLIC_POWER_THRESHOLD_CAPABILITY, node.capability_dependencies)
                self.assertEqual(reduction == "distinct_count",
                                 DISTINCT_PUBLIC_CHARACTERISTIC_COUNT_CAPABILITY in node.capability_dependencies)
        spec = fixed_public_state_condition(_COVEN, source_name="Power witness")
        for changes in ({"amount": True}, {"schema_version": 8}, {"counter_name": "charge"}, {"extra": 1}):
            with self.subTest(changes=changes), self.assertRaises(FixedPublicStateConditionError):
                FixedPublicStateConditionSpec.from_dict({**spec.to_dict(), **changes})
        for field, value in (("controller_relation", "any"), ("characteristic", "toughness")):
            mutated = deepcopy(spec.to_dict())
            mutated["aggregate"][field] = value
            with self.subTest(field=field), self.assertRaises(FixedPublicStateConditionError):
                FixedPublicStateConditionSpec.from_dict(mutated)
        self.assertIsNone(fixed_public_state_condition(
            "you control exactly three creatures with different powers", source_name="Power witness",
        ))

    def test_ordered_if_requires_a_complete_prefix_and_keeps_the_span(self):
        registry = load_default_capability_registry()
        tail = f"Coven — Then if {_COVEN}, draw a card."
        text = "Target creature gets +2/+2 until end of turn.\n" + tail
        ir = compile_oracle_card(record(text, "Instant"), capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(text)), (node.span.start, node.span.end))
        self.assertEqual(2, len(node.effects))
        self.assertEqual("apply_if_public_condition", node.effects[-1]["op"])
        self.assertNotEqual("exact", compile_oracle_card(record(tail, "Instant"), capability_registry=registry).status)

    def test_clamping_signed_power_mutation_is_killed(self):
        from quorune import public_characteristic_aggregates as owner

        def witness():
            self.assertEqual(6, owner.reduce_public_characteristic_values([-2, 1, 2, 2, 3], "sum"))
            self.assertEqual(3, owner.reduce_public_characteristic_values([-2, 0, 0, 3], "distinct_count"))
            with self.assertRaises(ValueError):
                owner.reduce_public_characteristic_values([2, None], "sum")

        witness()
        original = owner.reduce_public_characteristic_values

        def clamp(values, reduction):
            return original([max(0, value) if type(value) is int else value for value in values], reduction)

        with patch.object(owner, "reduce_public_characteristic_values", side_effect=clamp):
            with self.assertRaises(AssertionError):
                witness()


class PublicPowerThresholdNativeTests(InheritedNativeFixture, unittest.TestCase):
    end_step = history_controls.PublicHistoryQueryNativeTests.end_step

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "public-power-thresholds.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "creature-power-multiple-cards",
            "state-based-destination-cards", "public-power-threshold-cards",
        )
        build_fixture_database([ROOT / "tests/fixtures" / f"{name}.json" for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public power thresholds", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_actual_coven_counts_signed_values_and_rechecks_after_response(self):
        for index, mode in enumerate(("duplicate", "signed", "response")):
            with self.subTest(mode=mode):
                session = self.session(603_697_101 + index)
                engine = session.engine
                wardens = self.add(engine, "Dawnhart Wardens")
                wall = self.add(engine, "Wall of Stone")
                self.add(engine, "Ornithopter")
                self.add(engine, "Island", seat="B")
                weaken = self.add(engine, "Disfigure", seat="B", zone="hand")
                bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
                self.assert_closed(session, wardens)
                self.prepare(session)
                session.state.players["B"].mana_pool["B"] = 1
                self.checkpoint(session)
                if mode != "duplicate":
                    self.cast_for(session, "B", weaken, [wall.ref])
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual(-2, engine._numeric_stat(wall.object_id, "power"))
                self.until(session, lambda game: game.state.step == "beginning_combat")
                self.assertEqual(
                    mode != "duplicate",
                    any(item.source_object_id == wardens.object_id for item in session.state.stack),
                )
                if mode == "response":
                    self.cast_for(session, "B", bounce, [session.state.cards[wall.object_id].ref])
                    self.resolve_top(session)
                    self.assertEqual("hand", session.state.cards[wall.object_id].zone)
                    session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(4 if mode == "signed" else 3, session.engine._numeric_stat(wardens.object_id, "power"))
                self.replay(session)

    def test_actual_total_power_uses_signed_values_for_activation_and_end_step(self):
        for index, weakened in enumerate((False, True)):
            with self.subTest(weakened=weakened):
                session = self.session(603_697_111 + index)
                engine = session.engine
                owl = self.add(engine, "Owlbear Shepherd")
                breaker = self.add(engine, "Atarka Beastbreaker")
                self.add(engine, "Hill Giant")
                self.add(engine, "Grizzly Bears")
                wall = self.add(engine, "Wall of Stone")
                weaken = self.add(engine, "Disfigure", seat="B", zone="hand")
                for source in (owl, breaker):
                    self.assert_closed(session, source)
                self.prepare(session, mana={"C": 4, "G": 1})
                session.state.players["B"].mana_pool["B"] = 1
                self.checkpoint(session)
                if weakened:
                    self.cast_for(session, "B", weaken, [wall.ref])
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual(-2, engine._numeric_stat(wall.object_id, "power"))
                self.priority(session, "A")
                actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                offers = [row for row in actions if row["id"].startswith("activate:" + breaker.ref + ":")]
                self.assertEqual(not weakened, bool(offers))
                if not weakened:
                    command = {"action_id": offers[0]["id"], "pay": "auto"}
                    before = authoritative_state_hash(session.state)
                    self.assertFalse(session.act("pilot:C", command).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    accepted = session.act("pilot:A", command)
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.cast_for(session, "B", weaken, [wall.ref])
                    self.resolve_top(session)
                    self.assertEqual(-2, engine._numeric_stat(wall.object_id, "power"))
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual(6, engine._numeric_stat(breaker.object_id, "power"))
                self.end_step(session)
                self.assertEqual(
                    not weakened, any(item.source_object_id == owl.object_id for item in session.state.stack),
                )
                hand = len(session.state.players["A"].zones["hand"])
                self.assertIsNone(self.resolve(session))
                self.assertEqual(hand + int(not weakened), len(session.state.players["A"].zones["hand"]))
                self.replay(session)


    def test_actual_circle_is_a_conditional_payment_source(self):
        for index, weakened in enumerate((False, True)):
            with self.subTest(weakened=weakened):
                session = self.session(603_697_121 + index)
                engine = session.engine
                circle = self.add(engine, "Circle of Elders")
                circle.acquired_control_turn_count = -1
                self.add(engine, "Owlbear Shepherd")
                self.add(engine, "Hill Giant")
                self.add(engine, "Grizzly Bears")
                wall = self.add(engine, "Wall of Stone")
                weaken = self.add(engine, "Disfigure", seat="B", zone="hand")
                spell = self.add(engine, "Darksteel Ingot", zone="hand")
                self.assert_closed(session, circle)
                self.assert_closed(session, spell)
                self.prepare(session)
                session.state.players["B"].mana_pool["B"] = 1
                self.checkpoint(session)
                if weakened:
                    self.cast_for(session, "B", weaken, [wall.ref])
                    self.assertIsNone(self.resolve(session))
                self.priority(session, "A")
                actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                rows = [row for row in actions if row.get("card") == spell.ref]
                self.assertEqual(not weakened, bool(rows))
                if weakened:
                    before = authoritative_state_hash(session.state)
                    denied = session.act("pilot:A", {"action_id": "cast:" + spell.ref, "pay": "auto"})
                    self.assertFalse(denied.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    self.assertFalse(session.state.cards[circle.object_id].tapped)
                else:
                    self.cast(session, "A", spell)
                    self.assertTrue(session.state.cards[circle.object_id].tapped)
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual("battlefield", session.state.cards[spell.object_id].zone)
                self.replay(session)

    def test_actual_might_checks_after_the_targeted_power_change(self):
        for index, choose_duplicate in enumerate((False, True)):
            with self.subTest(choose_duplicate=choose_duplicate):
                session = self.session(603_697_131 + index)
                engine = session.engine
                first = self.add(engine, "Grizzly Bears", ref="might-bear-one")
                self.add(engine, "Grizzly Bears", ref="might-bear-two")
                giant = self.add(engine, "Hill Giant")
                source = self.add(engine, "Might of the Old Ways", zone="hand")
                self.assert_closed(session, source)
                self.prepare(session, mana={"C": 1, "G": 1})
                self.checkpoint(session)
                before = len(session.state.players["A"].zones["hand"])
                target = first if choose_duplicate else giant
                self.cast(session, "A", source, [target.ref])
                self.assertIsNone(self.resolve(session))
                self.assertEqual(4 if choose_duplicate else 5, engine._numeric_stat(target.object_id, "power"))
                self.assertEqual(before - 1 + int(choose_duplicate), len(session.state.players["A"].zones["hand"]))
                self.replay(session)
