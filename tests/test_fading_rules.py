from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.semantics import SemanticRegistry
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.node_capability_shapes import fixed_self_counter_keyword_action_node_capabilities
from quorune.semantic_choices.self_counter_keyword_actions import FixedSelfCounterKeywordActionHandler
from quorune.semantic_choices.context import SemanticChoiceContext
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantic_runtime import RemoveCountersIntent, ZoneMoveIntent
from quorune.object_query import ObjectQueryResult
from quorune.replacement.immutable import FrozenMap
from quorune.record import authoritative_state_hash
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as witnesses


CAP = "counter.lifecycle.fading"
EFFECT = {"op": "fixed_self_counter_keyword_action", "action": "fading", "amount": 1, "source": "$source"}


class FadingCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "fading.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fading-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_fading_compiles_entry_and_upkeep_as_separate_closed_nodes(self):
        for name in ("Blastoderm", "Cloudskate", "Skyshroud Ridgeback", "Rusting Golem", "Phyrexian Prowler"):
            with self.subTest(name=name):
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry)
                entry = next(n for n in ir.faces[0].nodes if n.template_id == "fading-fixed-entry-counter-v1")
                upkeep = next(n for n in ir.faces[0].nodes if n.template_id == "fading-fixed-upkeep-v1")
                self.assertTrue(entry.exact)
                self.assertTrue(upkeep.exact)
                self.assertEqual(entry.span, upkeep.span)
                self.assertEqual((EFFECT,), upkeep.effects)
                self.assertEqual("step.begin", upkeep.event)
                self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, upkeep.runtime_coverage)
                self.assertEqual((CAP, "counter.placement.quantity_replacement"), upkeep.capability_dependencies)
                self.assertEqual("exact", ir.status)
                program = compile_best_available_card_program(self.db, self.db.lookup(name), semantic_registry=SemanticRegistry(), capability_registry=self.registry, capability_profile="commander_review")
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        repeated = replace(self.db.lookup("Skyshroud Ridgeback"), oracle_text="Fading 2, Fading 3", keywords=("Fading",))
        ir = compile_oracle_card(repeated, capability_registry=self.registry)
        self.assertEqual(2, sum(n.template_id == "fading-fixed-upkeep-v1" for n in ir.faces[0].nodes))
        self.assertEqual(2, sum(n.template_id == "fading-fixed-entry-counter-v1" for n in ir.faces[0].nodes))
        sibling = compile_oracle_card(self.db.lookup("Parallax Wave"), capability_registry=self.registry)
        self.assertNotEqual("exact", sibling.status)

    def test_fading_shape_and_blocked_dependencies_fail_closed(self):
        shape = dict(effects=(EFFECT,), target_schema=None, mechanic_ids=("fading", "cr-122-counters"))
        self.assertEqual((CAP,), fixed_self_counter_keyword_action_node_capabilities(**shape))
        for mutation in ({"amount": 2}, {"amount": True}, {"source": "$target.0"}, {"extra": 0}):
            self.assertFalse(fixed_self_counter_keyword_action_node_capabilities(**{**shape, "effects": ({**EFFECT, **mutation},)}))
        for cap in (CAP, "counter.removal.fixed_effect", "zone.change.destination_replacement", "trigger.placement.apnap"):
            value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
            for row in value["capabilities"]:
                if row["id"] == cap:
                    row.update(status="blocked", blockers=["independent owner unavailable"])
            ir = compile_oracle_card(self.db.lookup("Skyshroud Ridgeback"), capability_registry=CapabilityRegistry(value))
            self.assertNotEqual("exact", ir.status)
        for text in ("Fading X", "Fading 0", "Fading 2 with haste", "Vanishing X", "Graft 2"):
            record = replace(self.db.lookup("Skyshroud Ridgeback"), oracle_text=text, keywords=(text.split()[0],))
            self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=self.registry).status)

    def test_resolution_revalidates_source_identity_controller_and_descriptor(self):
        source = ObjectQueryResult(object_id="source", ref="$source", printed_name="Fading source", owner="A", controller="A", zone="battlefield", logical_object_id="old", counters=FrozenMap({"fade": 1}))
        class Query:
            def object(self, ref):
                return source
        query = Query()
        context = SemanticChoiceContext(actor="A", stack_ref="stack", stack_controller="A", stack_label="Fading", source_ref="$source", card_ref=None, semantic_program_id="fading", semantic_program_version=1, query=query, source_logical_object_id="old")
        handler = FixedSelfCounterKeywordActionHandler()
        result = handler.prepare(EFFECT, context)
        self.assertIsInstance(result.preparation_intents[0], RemoveCountersIntent)
        source = replace(source, controller="B")
        self.assertIsInstance(handler.prepare(EFFECT, context).preparation_intents[0], RemoveCountersIntent)
        source = replace(source, counters=FrozenMap())
        self.assertFalse(handler.prepare(EFFECT, context).preparation_intents)
        source = replace(source, controller="A")
        self.assertIsInstance(handler.prepare(EFFECT, context).preparation_intents[0], ZoneMoveIntent)
        for mutation in ({"logical_object_id": "new"}, {"zone": "exile"}, {"phased_out": True}):
            previous = source
            source = replace(source, **mutation)
            self.assertFalse(handler.prepare(EFFECT, context).preparation_intents)
            source = previous
        for mutation in ({"amount": 2}, {"amount": True}, {"extra": 0}, {"source": "other"}):
            with self.assertRaises(SemanticChoiceError):
                handler.prepare({**EFFECT, **mutation}, context)
        source = replace(source, counters=FrozenMap({"fade": -1}))
        with self.assertRaises(SemanticChoiceError):
            handler.prepare(EFFECT, context)

    def test_fading_action_requires_its_own_capability_certificate(self):
        program = compile_best_available_card_program(self.db, self.db.lookup("Skyshroud Ridgeback"), semantic_registry=SemanticRegistry(), capability_registry=self.registry, capability_profile="commander_review")
        ability = next(p for p in program.abilities if p.provenance.get("template_id") == "fading-fixed-upkeep-v1")
        dependencies = ("counter.placement.quantity_replacement",)
        forged = replace(ability, capability_dependencies=dependencies,
                         capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + CAP, binding["blockers"])
        for mutation in ({"amount": 2}, {"source": "$target.0"}, {"extra": 0}):
            forged = replace(ability, effects=({**EFFECT, **mutation},))
            binding = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile="commander_review")
            self.assertIn("runtime_effect:invalid_fading_action", binding["blockers"])


class FadingRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "fading.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json", ROOT / "tests/fixtures/fading-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fading witness", [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    session = witnesses.BoundEffectProgramRuntimeTests.session
    add = witnesses.BoundEffectProgramRuntimeTests.add
    resolve = witnesses.BoundEffectProgramRuntimeTests.resolve
    checkpoint = witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay = witnesses.BoundEffectProgramRuntimeTests.replay

    def ready(self, session, source, mana):
        # Exercise the full trusted CardProgram path; the compatibility helper
        # intentionally promotes only its established partial ability families.
        program = compile_best_available_card_program(
            self.db, self.db.lookup(source.printed_name), semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile="commander_review",
        )
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        for ability in program.abilities:
            session.engine.semantics.put(ability)
        return witnesses.BoundEffectProgramRuntimeTests.ready(self, session, source, mana)

    def upkeep(self, session, seat="A"):
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.started = True
        engine.state.active_player = seat
        engine.state.phase_index = TURN_STEPS.index(("beginning", "upkeep"))
        engine._enter_step()
        engine.pump()

    def test_actual_cast_entry_last_counter_and_next_upkeep_sacrifice(self):
        session = self.session(293001)
        engine = session.engine
        source = self.add(engine, "Skyshroud Ridgeback", zone="hand")
        action = self.ready(session, source, {"G": 1})
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        wrong = session.act("pilot:B", {"action_id": action["id"], "pay": "auto"})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual(2, source.counters["fade"])
        self.replay(session)
        source.counters["fade"] = 1
        self.upkeep(session)
        self.assertTrue(engine.state.stack)
        self.checkpoint(session)
        resumed = self.replay(session, load=True)
        self.resolve(resumed)
        current = resumed.state.cards[source.object_id]
        self.assertEqual(0, current.counters.get("fade", 0))
        self.assertEqual("battlefield", current.zone)
        self.replay(resumed)
        self.upkeep(resumed)
        self.checkpoint(resumed)
        self.resolve(resumed)
        self.assertEqual("graveyard", current.zone)
        self.replay(resumed)

    def test_other_seat_upkeep_does_not_trigger_and_pending_source_does_not_follow_blink(self):
        session = self.session(293002)
        engine = session.engine
        source = self.add(engine, "Cloudskate")
        source.counters["fade"] = 2
        self.upkeep(session, "C")
        self.assertFalse(engine.state.stack)
        self.upkeep(session)
        self.assertTrue(engine.state.stack)
        old = source.logical_object_id
        engine.move_card(source.object_id, "exile", log=False)
        engine.move_card(source.object_id, "battlefield", controller="A", log=False)
        self.assertNotEqual(old, source.logical_object_id)
        self.assertEqual(3, source.counters["fade"])
        self.checkpoint(session)
        self.resolve(session)
        self.assertEqual(3, source.counters["fade"])
        self.replay(session)

    def test_trigger_control_change_removes_counter_but_cannot_sacrifice_new_controller_permanent(self):
        session = self.session(293003)
        engine = session.engine
        source = self.add(engine, "Skyshroud Ridgeback")
        source.counters["fade"] = 1
        self.upkeep(session)
        # The trigger's controller is fixed, whereas the permanent can change controller.
        engine.state.players["A"].zones["battlefield"].remove(source.object_id)
        engine.state.players["B"].zones["battlefield"].append(source.object_id)
        source.controller = "B"
        self.checkpoint(session)
        self.resolve(session)
        self.assertEqual(0, source.counters.get("fade", 0))
        self.assertEqual("battlefield", source.zone)
        self.replay(session)

        source.controller = "A"
        engine.state.players["B"].zones["battlefield"].remove(source.object_id)
        engine.state.players["A"].zones["battlefield"].append(source.object_id)
        self.upkeep(session)
        source.controller = "B"
        engine.state.players["A"].zones["battlefield"].remove(source.object_id)
        engine.state.players["B"].zones["battlefield"].append(source.object_id)
        self.checkpoint(session)
        self.resolve(session)
        self.assertEqual("battlefield", source.zone)
        self.replay(session)

    def test_fading_last_counter_sacrifice_mutant_is_killed(self):
        original = FixedSelfCounterKeywordActionHandler.prepare
        def premature_sacrifice(handler, effect, context):
            preparation = original(handler, effect, context)
            if effect.get("action") == "fading" and preparation.preparation_intents:
                first = preparation.preparation_intents[0]
                if isinstance(first, RemoveCountersIntent):
                    preparation = replace(preparation, preparation_intents=(ZoneMoveIntent(
                        actor=first.actor, object_ref=first.object_ref, expected_zones=("battlefield",),
                        destination="graveyard", reason="incorrectly sacrifice after final counter", controlled_only=True,
                    ),))
            return preparation
        with patch.object(FixedSelfCounterKeywordActionHandler, "prepare", premature_sacrifice):
            with self.assertRaises(AssertionError):
                self.test_actual_cast_entry_last_counter_and_next_upkeep_sacrifice()

    def test_actual_counter_cost_exhaustion_waits_for_upkeep_and_replays(self):
        session = self.session(293004)
        engine = session.engine
        source = self.add(engine, "Phyrexian Prowler")
        source.counters["fade"] = 1
        action = self.ready(session, source, {})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual(0, source.counters.get("fade", 0))
        self.assertEqual("battlefield", source.zone)
        self.assertEqual(4, engine._numeric_stat(source.object_id, "power"))
        self.replay(session)
        self.upkeep(session)
        self.checkpoint(session)
        self.resolve(session)
        self.assertEqual("graveyard", source.zone)
        self.replay(session)

    def test_actual_rusting_golem_last_removal_stabilizes_zero_toughness(self):
        session = self.session(293005)
        engine = session.engine
        source = self.add(engine, "Rusting Golem")
        source.counters["fade"] = 1
        self.assertEqual(1, engine._numeric_stat(source.object_id, "toughness"))
        self.upkeep(session)
        self.checkpoint(session)
        self.resolve(session)
        self.assertEqual("graveyard", source.zone)
        self.replay(session)

    def test_current_ability_loss_stops_future_upkeep_but_not_a_pending_trigger(self):
        session = self.session(293006)
        engine = session.engine
        source = self.add(engine, "Cloudskate")
        source.counters["fade"] = 2
        self.upkeep(session)
        self.assertTrue(engine.state.stack)
        humility = self.add(engine, "Humility", seat="B")
        self.checkpoint(session)
        self.resolve(session)
        self.assertEqual(1, source.counters["fade"])
        self.replay(session)
        self.upkeep(session)
        self.assertFalse(engine.state.stack)
        engine.move_card(humility.object_id, "graveyard", log=False)
        self.upkeep(session)
        self.assertTrue(engine.state.stack)
        self.checkpoint(session)
        self.resolve(session)
        self.assertEqual(0, source.counters.get("fade", 0))
        self.assertEqual("battlefield", source.zone)
        self.replay(session)


if __name__ == "__main__":
    unittest.main()
