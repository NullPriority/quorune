from __future__ import annotations

from dataclasses import replace
from collections.abc import Mapping
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.counter_removal import CounterRemoval, commit_counter_removals, plan_counter_removals, CounterRemovalError
from quorune.counter_removal_events import capture_counter_removal_events
from quorune.counter_state import CounterTransition
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_fading_rules as fading_witnesses


CAP = "counter.lifecycle.vanishing"
EVENT_CAP = "trigger.event.normalized_counter_removal"


class VanishingCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "vanishing.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/vanishing-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def program(self, record, registry=None):
        return compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(), capability_registry=registry or self.registry, capability_profile="commander_review")

    def test_fixed_bare_zero_and_repeated_vanishing_programs_are_closed(self):
        source = self.db.lookup("Calciderm")
        for text, entries, triggers in (("Vanishing 4", 1, 2), ("Vanishing", 0, 2), ("Vanishing 0", 0, 2), ("Vanishing 2, Vanishing 3", 2, 4)):
            with self.subTest(text=text):
                record = replace(source, oracle_text=text, keywords=("Vanishing",))
                ir = compile_oracle_card(record, capability_registry=self.registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                program = self.program(record)
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                self.assertEqual(entries, sum(bool(p.handlers) for p in program.abilities))
                self.assertEqual(triggers, sum(p.event in {"step.begin", "counter.removed"} for p in program.abilities))
                self.assertEqual(len(program.abilities), len({p.key for p in program.abilities}))
        for name in ("Calciderm", "Aven Riftwatcher"):
            program = self.program(self.db.lookup(name))
            binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
            self.assertTrue(binding["strict_capability_ready"], (name, binding["blockers"]))
        self.assertNotEqual("exact", compile_oracle_card(self.db.lookup("Ravaging Riftwurm"), capability_registry=self.registry).status)
        self.assertNotEqual("exact", compile_oracle_card(self.db.lookup("Chronozoa"), capability_registry=self.registry).status)
        record = replace(source, oracle_text="Fading 2, Fading 3", keywords=("Fading",))
        program = self.program(record)
        self.assertEqual(4, len(program.abilities))
        self.assertEqual(4, len({p.key for p in program.abilities}))

    def test_lifecycle_dependencies_and_variant_certificates_fail_closed(self):
        source = self.db.lookup("Calciderm")
        for cap in (CAP, EVENT_CAP, "trigger.condition.fixed_public_state", "counter.removal.rule_generated", "trigger.placement.apnap"):
            value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
            for row in value["capabilities"]:
                if row["id"] == cap:
                    row.update(status="blocked", blockers=["independent owner unavailable"])
            self.assertNotEqual("exact", compile_oracle_card(source, capability_registry=CapabilityRegistry(value)).status)
        program = self.program(source)
        for ability in (p for p in program.abilities if p.effects and p.effects[0].get("action", "").startswith("vanishing")):
            dependencies = ("counter.placement.quantity_replacement",)
            forged = replace(ability, capability_dependencies=dependencies,
                             capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
            binding = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile="commander_review")
            self.assertIn("capability:undeclared_runtime_dependency:" + CAP, binding["blockers"])
            forged = replace(ability, effects=({**ability.effects[0], "amount": 2},))
            self.assertIn("runtime_effect:invalid_vanishing_action", bind_semantic_program_runtime(forged, capability_registry=self.registry, profile="commander_review")["blockers"])
        for text in ("Vanishing X", "Vanishing -1", "Vanishing 2 with haste"):
            self.assertNotEqual("exact", compile_oracle_card(replace(source, oracle_text=text), capability_registry=self.registry).status)


class VanishingRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "vanishing.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json", ROOT / "tests/fixtures/vanishing-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Vanishing witness", [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    session = fading_witnesses.FadingRuntimeTests.session
    add = fading_witnesses.FadingRuntimeTests.add
    ready = fading_witnesses.FadingRuntimeTests.ready
    resolve = fading_witnesses.FadingRuntimeTests.resolve
    checkpoint = fading_witnesses.FadingRuntimeTests.checkpoint
    replay = fading_witnesses.FadingRuntimeTests.replay
    upkeep = fading_witnesses.FadingRuntimeTests.upkeep

    def resolve_top(self, session):
        top = session.state.stack[-1].stack_id
        for _ in range(48):
            if all(item.stack_id != top for item in session.state.stack):
                return
            result = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("The original top item did not finish resolving")

    def is_sacrifice(self, engine, item):
        program = engine.semantics.get(item["semantic_key"] if isinstance(item, Mapping) else item.semantic_key)
        return bool(program and program.effects and program.effects[0].get("action") == "vanishing_sacrifice")

    def test_upkeep_last_counter_and_separate_sacrifice_replay(self):
        session = self.session(294001)
        engine = session.engine
        source = self.add(engine, "Calciderm", zone="hand")
        action = self.ready(session, source, {"W": 4})
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:B", {"action_id": action["id"], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual(4, source.counters["time"])
        self.replay(session)
        source.counters["time"] = 1
        self.upkeep(session)
        self.checkpoint(session)
        self.resolve_top(session)
        self.assertEqual("battlefield", source.zone)
        self.assertEqual(0, source.counters.get("time", 0))
        self.assertEqual(1, len(engine.state.stack))
        self.assertTrue(self.is_sacrifice(engine, engine.state.stack[-1]))
        resumed = self.replay(session, load=True)
        self.resolve(resumed)
        self.assertEqual("graveyard", resumed.state.cards[source.object_id].zone)
        self.replay(resumed)

    def test_removal_batch_seals_actual_transitions_and_discovers_after_commit(self):
        session = self.session(294002)
        engine = session.engine
        first = self.add(engine, "Generic Vanishing One", ref="first")
        second = self.add(engine, "Generic Vanishing One", ref="second")
        for card in (first, second):
            card.counters["time"] = 1
        plan = plan_counter_removals(engine, tuple(CounterRemoval(c.object_id, "time", 1, expected_logical_object_id=c.logical_object_id) for c in (first, second)))
        snapshot = capture_counter_removal_events(engine, plan.counter_plan.transitions)
        self.assertEqual(2, len(snapshot.occurrences))
        self.assertEqual([1, 1], [r["counter_before"] for r in snapshot.occurrences])
        observed = []
        original = engine._dispatch_semantic_event
        def inspect(event, context, **kwargs):
            if event == "counter.removed":
                observed.append(tuple(c.counters.get("time", 0) for c in (first, second)))
            return original(event, context, **kwargs)
        with patch.object(engine, "_dispatch_semantic_event", inspect):
            commit_counter_removals(engine, plan)
        self.assertEqual([(0, 0), (0, 0)], observed)
        queued = [item for batch in engine.state.pending_trigger_batches for group in batch.groups for item in group.items]
        self.assertEqual({first.object_id, second.object_id}, {item.source_object_id for item in queued})
        self.assertTrue(all(self.is_sacrifice(engine, item) for item in queued))
        self.assertEqual([1, 1], [r["counter_before"] for r in snapshot.occurrences])
        with self.assertRaises(TypeError):
            snapshot.occurrences[0]["counter_after"] = 99

    def test_zero_other_kind_and_stale_identity_do_not_queue_last_counter_trigger(self):
        session = self.session(294003)
        engine = session.engine
        source = self.add(engine, "Generic Vanishing One")
        self.upkeep(session)
        self.assertFalse(engine.state.stack)
        source.counters.update({"time": 2, "charge": 1})
        for counter in ("charge", "time"):
            commit_counter_removals(engine, plan_counter_removals(engine, (CounterRemoval(source.object_id, counter, 1),)))
            self.assertFalse(engine.state.pending_trigger_batches)
        before = authoritative_state_hash(session.state)
        plan = plan_counter_removals(engine, (CounterRemoval(source.object_id, "time", 1, expected_logical_object_id=source.logical_object_id),))
        engine.move_card(source.object_id, "exile", log=False)
        engine.move_card(source.object_id, "battlefield", controller="A", log=False)
        before = authoritative_state_hash(session.state)
        with self.assertRaises(CounterRemovalError):
            commit_counter_removals(engine, plan)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_actual_counter_cost_places_last_removal_trigger_above_activation(self):
        session = self.session(294004)
        engine = session.engine
        source = self.add(engine, "Generic Vanishing Counter Cost")
        source.counters["time"] = 1
        action = self.ready(session, source, {})
        self.checkpoint(session)
        hand = len(engine.state.players["A"].zones["hand"])
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, len(engine.state.stack))
        self.assertTrue(self.is_sacrifice(engine, engine.state.stack[-1]))
        self.assertEqual(0, source.counters.get("time", 0))
        self.resolve_top(session)
        self.assertEqual("graveyard", source.zone)
        self.assertEqual(hand, len(engine.state.players["A"].zones["hand"]))
        self.resolve(session)
        self.assertEqual(hand + 1, len(engine.state.players["A"].zones["hand"]))
        self.replay(session)

    def test_pending_sacrifice_control_change_blink_and_new_counters(self):
        for variant in ("new_counter", "controller", "blink", "ability_loss", "phased_out"):
            with self.subTest(variant=variant):
                session = self.session(294005)
                engine = session.engine
                source = self.add(engine, "Generic Vanishing One")
                source.counters["time"] = 1
                self.upkeep(session)
                self.resolve_top(session)
                self.assertTrue(self.is_sacrifice(engine, engine.state.stack[-1]))
                if variant == "new_counter":
                    source.counters["time"] = 2
                elif variant == "controller":
                    source.controller = "B"
                    engine.state.players["A"].zones["battlefield"].remove(source.object_id)
                    engine.state.players["B"].zones["battlefield"].append(source.object_id)
                elif variant == "blink":
                    engine.move_card(source.object_id, "exile", log=False)
                    engine.move_card(source.object_id, "battlefield", controller="A", log=False)
                elif variant == "ability_loss":
                    self.add(engine, "Humility", seat="B")
                else:
                    source.phased_out = True
                self.checkpoint(session)
                self.resolve(session)
                self.assertEqual("graveyard" if variant in {"new_counter", "ability_loss"} else "battlefield", source.zone)
                self.replay(session)

    def test_upkeep_intervening_condition_and_current_ability_loss(self):
        session = self.session(294006)
        engine = session.engine
        source = self.add(engine, "Generic Vanishing One")
        source.counters["time"] = 1
        self.upkeep(session)
        source.counters.clear()
        self.checkpoint(session)
        self.resolve(session)
        self.assertEqual("battlefield", source.zone)
        self.assertFalse(engine.state.stack)
        self.replay(session)
        source.counters["time"] = 1
        humility = self.add(engine, "Humility", seat="B")
        self.upkeep(session)
        self.assertFalse(engine.state.stack)
        commit_counter_removals(engine, plan_counter_removals(engine, (CounterRemoval(source.object_id, "time", 1),)))
        self.assertFalse(engine.state.pending_trigger_batches)
        engine.move_card(humility.object_id, "graveyard", log=False)
        source.counters["time"] = 1
        self.upkeep(session)
        self.add(engine, "Humility", seat="B", ref="later-humility")
        self.checkpoint(session)
        self.resolve_top(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual(0, source.counters.get("time", 0))
        self.assertEqual("battlefield", source.zone)
        self.replay(session)

    def test_countered_sacrifice_trigger_leaves_zero_counters_without_future_upkeep(self):
        session = self.session(294008)
        engine = session.engine
        source = self.add(engine, "Calciderm")
        source.counters["time"] = 1
        self.upkeep(session)
        self.resolve_top(session)
        sacrifice = engine.state.stack[-1]
        self.assertTrue(self.is_sacrifice(engine, sacrifice))
        spell = self.add(engine, "Stifle", zone="hand")
        action = self.ready(session, spell, {"U": 1})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [sacrifice.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual("battlefield", source.zone)
        self.assertEqual(0, source.counters.get("time", 0))
        self.replay(session)
        self.upkeep(session)
        self.assertFalse(engine.state.stack)

    def test_repeated_vanishing_instances_queue_distinct_last_removal_triggers(self):
        session = self.session(294009)
        engine = session.engine
        source = self.add(engine, "Generic Repeated Vanishing")
        source.counters["time"] = 1
        commit_counter_removals(engine, plan_counter_removals(engine, (CounterRemoval(source.object_id, "time", 1),)))
        queued = [item for batch in engine.state.pending_trigger_batches for group in batch.groups for item in group.items]
        self.assertEqual(2, len(queued))
        self.assertEqual(2, len({item.source_ability_id for item in queued}))
        self.assertTrue(all(self.is_sacrifice(engine, item) for item in queued))

    def test_counter_removal_reads_post_event_characteristics_before_stabilization(self):
        session = self.session(294010)
        engine = session.engine
        source = self.add(engine, "Tidewalker")
        source.counters["time"] = 1
        self.assertEqual(1, engine._numeric_stat(source.object_id, "toughness"))
        commit_counter_removals(engine, plan_counter_removals(engine, (CounterRemoval(source.object_id, "time", 1),)))
        queued = [item for batch in engine.state.pending_trigger_batches for group in batch.groups for item in group.items]
        self.assertEqual(1, len(queued))
        context = queued[0].payload["context"]
        self.assertEqual((1, 0), (context["counter_before"], context["counter_after"]))
        self.assertEqual((0, 0), (context["power"], context["toughness"]))
        self.assertEqual("battlefield", source.zone)
        # Counter-change discovery precedes the ordinary zero-toughness SBA.
        engine._stabilize()
        self.assertEqual("graveyard", source.zone)

        session = self.session(294011)
        engine = session.engine
        source = self.add(engine, "Generic Vanishing One")
        source.counters["time"] = 1
        original = engine._effective_card_data
        observed = []
        def current_after_removal(card):
            data = original(card)
            if (card if isinstance(card, str) else card.object_id) == source.object_id:
                observed.append(source.counters.get("time", 0))
                if not source.counters.get("time", 0):
                    data = {**data, "ability_fragments": ()}
            return data
        plan = plan_counter_removals(engine, (CounterRemoval(source.object_id, "time", 1),))
        with patch.object(engine, "_effective_card_data", current_after_removal):
            commit_counter_removals(engine, plan)
        self.assertTrue(observed)
        self.assertTrue(all(count == 0 for count in observed))
        self.assertFalse(engine.state.pending_trigger_batches)

    def test_missing_counter_event_mutant_is_killed(self):
        with patch("quorune.counter_removal_events.dispatch_counter_removal_events", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_upkeep_last_counter_and_separate_sacrifice_replay()

    def test_actual_riftwatcher_single_and_all_counter_effects_preserve_separate_trigger(self):
        session = self.session(294007)
        engine = session.engine
        source = self.add(engine, "Aven Riftwatcher", zone="hand")
        action = self.ready(session, source, {"W": 3})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual(3, source.counters["time"])
        self.assertEqual(42, engine.state.players["A"].life)
        self.replay(session)
        spell = self.add(engine, "Generic Remove Time Counter", zone="hand")
        action = self.ready(session, spell, {})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [source.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual(2, source.counters["time"])
        self.assertEqual("battlefield", source.zone)
        self.assertFalse(engine.state.stack)
        self.replay(session)
        source.counters["charge"] = 2
        spell = self.add(engine, "Generic Remove All Counters", zone="hand")
        action = self.ready(session, spell, {})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [source.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)
        self.assertFalse(source.counters)
        self.assertEqual("battlefield", source.zone)
        self.assertEqual(1, len(engine.state.stack))
        self.assertTrue(self.is_sacrifice(engine, engine.state.stack[-1]))
        self.resolve(session)
        self.assertEqual("graveyard", source.zone)
        self.assertEqual(44, engine.state.players["A"].life)
        self.assertTrue(all(engine.state.players[seat].life == 40 for seat in "BCD"))
        self.replay(session)


if __name__ == "__main__":
    unittest.main()
