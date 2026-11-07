from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.endure_templates import fixed_endure_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.endure_model import ENDURE_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.endure_shapes import fixed_endure_node_capabilities
from quorune.semantic_choices.endure import SourceEndureChoiceHandler
from quorune.semantic_choices.intent_replacement import counter_intent_identity, validate_counter_intent_identity
from quorune.semantic_runtime.intents import PlaceCountersIntent
from scripts.build_test_database import build_fixture_database
import test_fixed_counter_additional_costs as counter_witnesses
import test_token_creation_triggers as creation_witnesses


class EndureCompilerTests(unittest.TestCase):
    def test_fixed_endure_requires_a_source_and_complete_amount(self):
        arguments = {"source_name": "Endure Witness", "source_is_permanent": True}
        direct = "This creature endures 2."
        _, effects, schema, mechanics = fixed_endure_effect_template(direct, **arguments)
        self.assertEqual((ENDURE_CAPABILITY,), fixed_endure_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics,
        ))
        self.assertEqual((), fixed_endure_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=(),
        ))
        for text in ("It endures 2.", "Target creature endures 2.", "This creature endures X.",
                     "This creature endures 2, then draw a card."):
            with self.subTest(text=text):
                self.assertIsNone(fixed_endure_effect_template(text, **arguments))
        for changes in ({"amount": True}, {"amount": -1}, {"player": "$controller"}, {"schema_version": 2}):
            with self.subTest(changes=changes):
                self.assertEqual((), fixed_endure_node_capabilities(
                    effects=({**effects[0], **changes},), target_schema=None, mechanic_ids=mechanics,
                ))

    def test_endure_zero_has_no_choice_or_intent(self):
        query = SimpleNamespace(seats=("A", "B"), active_seats=("A", "B"))
        context = SimpleNamespace(actor="A", query=query)
        result = SourceEndureChoiceHandler().prepare({
            "op": "endure", "schema_version": 1, "amount": 0, "player": "A",
            "object_ref": "old", "logical_object_id": "old@1", "reason": "Endure zero",
        }, context)
        self.assertIsNone(result.request)
        self.assertEqual((), result.preparation_intents)

    def test_counter_intent_uses_canonical_provenance_fields(self):
        effect = PlaceCountersIntent("A", ("source",), "+1/+1", 1, "ordinary placement")
        ordinary = counter_intent_identity(effect)
        self.assertNotIn("effect_generated", ordinary)
        self.assertEqual(ordinary, validate_counter_intent_identity(ordinary))
        cost = counter_intent_identity(replace(effect, effect_generated=False))
        self.assertEqual({**ordinary, "effect_generated": False}, cost)
        self.assertEqual(cost, validate_counter_intent_identity(cost))


class SourceEndureRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    Witnesses = counter_witnesses.CounterCostReplacementChainTests
    session = Witnesses.session
    add = Witnesses.add
    ready = Witnesses.ready
    checkpoint = Witnesses.checkpoint
    resolve = Witnesses.resolve
    replay = Witnesses.replay
    assert_normal_admission = Witnesses.assert_normal_admission

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "endure.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/counter-prohibition-cards.json",
            ROOT / "tests/fixtures/counter-replacement-cards.json",
            ROOT / "tests/fixtures/endure-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Endure", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 16),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def _cast(self, session, seat, card, targets=(), *, payment=None):
        packet = session.packet(f"pilot:{seat}", full=True)
        offered = next(row for row in packet["decision"]["ctx"]["legal"]["actions"] if row.get("card") == card.ref)
        command = {
            "action_id": offered["id"], "targets": list(targets), "pay": "auto",
        }
        if payment is not None:
            command.update(pay="manual", payment=payment)
        result = session.act(f"pilot:{seat}", command)
        self.assertTrue(result.ok, result.summary)

    def _advance(self, session, predicate):
        for _ in range(24):
            if predicate():
                return
            self.assertEqual("priority", session.state.pending_decision.kind)
            passed = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(passed.ok, passed.summary)
        self.fail("Endure journey did not reach the requested priority window")

    def _await_b_response(self, session, stack_ref=None):
        self._advance(session, lambda: (
            len(session.state.stack) == 1 and session.state.stack[-1].kind == "triggered_ability"
            and (stack_ref is None or session.state.stack[-1].ref == stack_ref)
            and session.pending_principals() == ["pilot:B"]
        ))
        return session.state.stack[-1].ref

    def _tokens(self, session):
        return [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]

    def test_original_endure_producer_and_capability_withdrawal_keep_residuals(self):
        source = self.db.lookup("Kin-Tree Nurturer", fuzzy=False)

        def compile_with(registry):
            return compile_oracle_card(source, capability_registry=registry, capability_profile="commander_review")

        self.assertEqual("exact", compile_with(self.registry).status)
        with patch("quorune.compiler.endure_templates.fixed_endure_effect_template", return_value=None):
            self.assertTrue(compile_with(self.registry).material_residuals)
        value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        required = next(row for row in value["capabilities"] if row["id"] == "counter.placement.quantity_replacement")
        required.update(status="blocked", blockers=["test capability withdrawal"])
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        self.assertTrue(compile_with(registry).material_residuals)

    def test_actual_endure_uses_current_controller_and_counter_cost_replacements(self):
        for branching in (False, True):
            with self.subTest(branching=branching):
                session = self.session(733101 + branching)
                engine = session.engine
                source = self.add(engine, "Kin-Tree Nurturer", zone="hand")
                steal = self.add(engine, "Word of Seizing", seat="B", zone="hand")
                season = self.add(engine, "Doubling Season", seat="B")
                cards = [source, steal, season]
                if branching:
                    cards.extend(self.add(engine, "Branching Evolution", seat="B", ref=f"endure-branching-{index}")
                                 for index in range(2))
                private = self.add(engine, "Generic Bound Growth", seat="D", zone="hand")
                for card in cards:
                    self.assert_normal_admission(session, card)
                engine.state.players["B"].mana_pool.update({"C": 3, "R": 2})
                self.ready(session, source, {"C": 5, "B": 2})
                self.checkpoint(session)
                self._cast(session, "A", source)
                self._await_b_response(session)
                self._cast(session, "B", steal, (source.ref,))
                self.assertEqual("semantic.choice", self.resolve(session).kind)
                self.assertEqual(["pilot:B"], session.pending_principals())
                self.assertEqual("B", session.state.cards[source.object_id].controller)
                packet = session.packet("pilot:A", full=True)
                self.assertIsNone(packet["decision"])
                self.assertNotIn(private.ref, json.dumps(packet))
                before = authoritative_state_hash(session.state)
                command = {"action_id": "choose", "choice": "counters"}
                self.assertFalse(session.act("pilot:A", command).ok)
                self.assertFalse(session.act("pilot:B", {**command, "choice": "both"}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                if not branching:
                    session = self.replay(session, load=True)
                paid = session.act("pilot:B", command)
                self.assertTrue(paid.ok, paid.summary)
                if branching:
                    self.assertEqual("replacement.order", session.state.pending_decision.kind)
                    self.assertIs(session.state.pending_decision.continuation["counter_intent"]["effect_generated"], False)
                    choices = session.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
                    self.assertEqual({card.ref for card in cards[-2:]}, {option["source"] for option in choices})
                    applied = session.act("pilot:B", {"action_id": "choose", "replacement": choices[0]["id"]})
                    self.assertTrue(applied.ok, applied.summary)
                    self.assertEqual({}, session.state.cards[source.object_id].counters)
                    session = self.replay(session, load=True)
                    choices = session.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
                    doubling = next(option["id"] for option in choices if option["source"] == season.ref)
                    applied = session.act("pilot:B", {"action_id": "choose", "replacement": doubling})
                    self.assertTrue(applied.ok, applied.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(8 if branching else 1, session.state.cards[source.object_id].counters["+1/+1"])
                self.assertEqual([], self._tokens(session))
                self.replay(session)

    def test_actual_endure_after_blink_creates_spirit_for_last_controller(self):
        session = self.session(733110)
        engine = session.engine
        source = self.add(engine, "Kin-Tree Nurturer", zone="hand")
        steal = self.add(engine, "Word of Seizing", seat="B", zone="hand")
        blink = self.add(engine, "Ephemerate", seat="B", zone="hand")
        for card in (source, steal, blink):
            self.assert_normal_admission(session, card)
        engine.state.players["B"].mana_pool.update({"C": 3, "R": 2, "W": 1})
        self.ready(session, source, {"C": 5, "B": 2})
        self.checkpoint(session)
        self._cast(session, "A", source)
        old_stack = self._await_b_response(session)
        old_incarnation = session.state.cards[source.object_id].logical_object_id
        self._cast(session, "B", steal, (source.ref,), payment={"C": 3, "R": 2})
        self._await_b_response(session, old_stack)
        self.assertEqual(1, session.state.players["B"].mana_pool["W"])
        self._cast(session, "B", blink, (source.ref,))
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        current = session.state.cards[source.object_id]
        self.assertNotEqual(old_incarnation, current.logical_object_id)
        self.assertEqual("A", current.controller)
        self.assertEqual(["pilot:A"], session.pending_principals())
        paid = session.act("pilot:A", {"action_id": "choose", "choice": "counters"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[source.object_id].counters["+1/+1"])
        spirit, = self._tokens(session)
        self.assertEqual(("B", "Spirit Token"), (spirit.controller, spirit.printed_name))
        self.replay(session, load=True)

    def test_actual_endure_creates_the_spirit_if_counters_are_prohibited(self):
        session = self.session(733120)
        source = self.add(session.engine, "Dusyut Earthcarver", zone="hand")
        solemnity = self.add(session.engine, "Solemnity", seat="B")
        for card in (source, solemnity):
            self.assert_normal_admission(session, card)
        self.ready(session, source, {"C": 20, "G": 4})
        self.checkpoint(session)
        self._cast(session, "A", source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({}, session.state.cards[source.object_id].counters)
        spirit, = self._tokens(session)
        effective = session.engine._effective_card_data(spirit)
        self.assertEqual(("A", "Spirit Token"), (spirit.controller, spirit.printed_name))
        self.assertEqual((3, 3, ["W"]), (int(effective["power"]), int(effective["toughness"]), effective["colors"]))
        self.replay(session)

    def test_actual_anafenza_endures_after_simultaneous_source_and_creature_death(self):
        session = self.session(733130)
        engine = session.engine
        source = self.add(engine, "Anafenza, Unyielding Lineage")
        ally = self.add(engine, "Generic Bound Body", ref="endure-death-ally")
        rival = self.add(engine, "Generic Bound Body", seat="C", ref="endure-death-rival")
        wrath = self.add(engine, "Wrath of God", zone="hand")
        for card in (source, ally, rival, wrath):
            self.assert_normal_admission(session, card)
        self.ready(session, wrath, {"C": 2, "W": 2})
        self.checkpoint(session)
        self._cast(session, "A", wrath)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[card.object_id].zone == "graveyard" for card in (source, ally, rival)))
        spirit, = self._tokens(session)
        effective = session.engine._effective_card_data(spirit)
        self.assertEqual(("A", 2, 2), (spirit.controller, int(effective["power"]), int(effective["toughness"])))
        self.replay(session)

    def test_actual_sinkhole_surveyor_loses_life_before_choosing_spirit(self):
        session = self.session(733140)
        source = self.add(session.engine, "Sinkhole Surveyor")
        source.acquired_control_turn_count = -1
        self.assert_normal_admission(session, source)
        creation_witnesses.TokenCreationTriggerNativeTests.start(self, session)
        self._advance(session, lambda: session.state.pending_decision.kind == "combat.attackers")
        attacked = session.act("pilot:A", {"action_id": "attack", "attackers": {source.ref: "B"}})
        self.assertTrue(attacked.ok, attacked.summary)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(39, session.state.players["A"].life)
        self.assertEqual([], self._tokens(session))
        declined = session.act("pilot:A", {"action_id": "choose", "choice": "spirit"})
        self.assertTrue(declined.ok, declined.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(0, session.state.cards[source.object_id].counters.get("+1/+1", 0))
        spirit, = self._tokens(session)
        self.assertEqual(("A", "Spirit Token"), (spirit.controller, spirit.printed_name))
        self.replay(session)
