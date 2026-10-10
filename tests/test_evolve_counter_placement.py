from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.deck import DeckLoader
from quorune.evolve import (
    EvolveCharacteristics,
    EvolveError,
    evolve_condition_holds,
)
from quorune.errors import GameRuleError
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "evolve.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "evolve-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class EvolveCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.record = cls.db.lookup("Cloudfin Raptor")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_evolve_model_is_strict_and_compares_power_or_toughness(self):
        source = EvolveCharacteristics(
            is_creature=True,
            power=2,
            toughness=3,
        )
        self.assertTrue(
            evolve_condition_holds(
                source,
                EvolveCharacteristics(True, 3, 1),
            )
        )
        self.assertTrue(
            evolve_condition_holds(
                source,
                EvolveCharacteristics(True, 1, 4),
            )
        )
        self.assertFalse(
            evolve_condition_holds(
                source,
                EvolveCharacteristics(True, 2, 3),
            )
        )
        self.assertFalse(
            evolve_condition_holds(
                source,
                EvolveCharacteristics(False, 99, 99),
            )
        )
        for malformed in (
            {"is_creature": 1, "power": 1, "toughness": 1},
            {"is_creature": True, "power": True, "toughness": 1},
            {"is_creature": True, "power": 1, "toughness": 1.5},
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(EvolveError):
                    EvolveCharacteristics(**malformed)

    def test_evolve_keyword_lowers_each_instance_with_intervening_condition(self):
        text = "Flying, evolve, evolve"
        record = replace(
            self.record,
            oracle_text=text,
            keywords=("Flying", "Evolve"),
        )
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        nodes = [
            node
            for node in ir.faces[0].nodes
            if node.template_id == "evolve-creature-enter-counter-v1"
        ]
        self.assertEqual("exact", ir.status)
        self.assertEqual(2, len(nodes))
        self.assertEqual(2, len({node.node_id for node in nodes}))
        self.assertEqual(2, len({(node.span.start, node.span.end) for node in nodes}))
        for node in nodes:
            self.assertTrue(node.exact)
            self.assertEqual("creature.enter", node.event)
            self.assertEqual(
                {
                    "field": "evolve_entered_creature_is_larger",
                    "op": "truthy",
                },
                node.event_condition,
            )
            self.assertEqual(("intervening_condition",), node.runtime_coverage)
            self.assertEqual(
                (
                    {
                        "op": "place_counters",
                        "card": "$source",
                        "counter": "+1/+1",
                        "amount": 1,
                        "source": "$source",
                    },
                ),
                node.effects,
            )
            self.assertEqual(
                ("counter.producer.evolve", "counter.producer.fixed_effect"),
                node.capability_dependencies,
            )
            self.assertEqual(
                "evolve",
                text[node.span.start : node.span.end].casefold(),
            )

        programs = [
            program
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "evolve-creature-enter-counter-v1"
        ]
        self.assertEqual(2, len(programs))
        self.assertEqual(2, len({program.key for program in programs}))
        self.assertTrue(all("intervening_condition" in p.coverage for p in programs))
        self.assertTrue(all(p.capability_closure["trusted"] for p in programs))

    def test_unsupported_evolve_wording_remains_material_residual(self):
        for text in ("Evolve 2", "Evolve — Whenever a land enters"):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(
                        self.record,
                        oracle_text=text,
                        keywords=("Evolve",),
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertTrue(
                    any(
                        "evolve-unsupported-wording" in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    )
                )

    def test_evolve_dependency_mutation_fails_closed(self):
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "counter.producer.fixed_effect"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            self.record,
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(
            any(
                "counter.producer.fixed_effect" in blocker
                for residual in ir.material_residuals
                for blocker in residual.blockers
            )
        )

    def test_evolve_compiler_mutant_is_killed(self):
        with patch("quorune.oracle_ir.evolve_keyword_node", return_value=None):
            ir = compile_oracle_card(
                self.record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            programs = generated_programs(
                self.db,
                self.record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(
                node.template_id == "evolve-creature-enter-counter-v1"
                for node in ir.faces[0].nodes
            )
        )
        self.assertFalse(
            any(
                program.event == "creature.enter" and program.effects
                for program in programs
            )
        )


class EvolveRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
    ) -> CardInstance:
        record = self.db.lookup(name)
        visible = list(engine.seats) if zone == "battlefield" else [seat]
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=visible,
            revealed_to=visible if zone == "battlefield" else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def register_evolve(self, engine, source: CardInstance, *, repeated=False):
        record = self.db.by_oracle_id(source.oracle_id)
        if repeated:
            record = replace(
                record,
                oracle_text="Evolve, evolve",
                keywords=("Evolve",),
            )
        for program in tuple(engine.semantics.programs_for_oracle(source.oracle_id)):
            if program.event == "creature.enter":
                engine.semantics.remove(program.key)
        programs = [
            program
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "evolve-creature-enter-counter-v1"
        ]
        self.assertEqual(2 if repeated else 1, len(programs))
        for program in programs:
            engine.semantics.put(program)
        return programs

    @staticmethod
    def resolve_top(engine):
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def enter(self, engine, card: CardInstance, *, controller: str):
        engine.move_card(
            card.object_id,
            "battlefield",
            controller=controller,
            semantic_events=True,
        )
        self.assertFalse(engine._stabilize())

    def test_larger_creature_triggers_and_places_replacement_aware_counter(self):
        session = self.session(70210001)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Cloudfin Raptor",
            ref="evolve-source",
            zone="battlefield",
        )
        entered = self.add_card(
            engine,
            seat="A",
            name="Evolve Test Beast",
            ref="larger-creature",
            zone="hand",
        )
        program = self.register_evolve(engine, source)[0]

        self.enter(engine, entered, controller="A")

        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual(source.logical_object_id, engine.state.stack[-1].context["source_logical_object_id"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters["+1/+1"])

    def test_multiple_evolve_instances_trigger_separately(self):
        session = self.session(70210002)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Cloudfin Raptor",
            ref="double-evolve-source",
            zone="battlefield",
        )
        entered = self.add_card(
            engine,
            seat="A",
            name="Evolve Test Beast",
            ref="double-evolve-entry",
            zone="hand",
        )
        programs = self.register_evolve(engine, source, repeated=True)

        engine.move_card(
            entered.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertTrue(engine._stabilize())
        self.assertEqual("trigger.order", engine.state.pending_decision.kind)
        trigger_refs = [
            item.ref for item in engine.state.pending_trigger_batches[0].items
        ]
        result = session.act(
            "pilot:A",
            {"action_id": "order", "triggers": trigger_refs},
        )
        self.assertTrue(result.ok, result.summary)

        self.assertEqual(
            {program.key for program in programs},
            {item.semantic_key for item in engine.state.stack},
        )
        self.resolve_top(engine)
        self.resolve_top(engine)
        self.assertEqual(2, source.counters["+1/+1"])

    def test_equal_smaller_opponent_and_noncreature_entries_do_not_trigger(self):
        cases = (
            ("A", "Evolve Test Minnow", "equal"),
            ("B", "Evolve Test Beast", "opponent"),
            ("A", "Evolve Test Relic", "noncreature"),
        )
        for offset, (seat, name, label) in enumerate(cases):
            with self.subTest(label=label):
                session = self.session(70210100 + offset)
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Cloudfin Raptor",
                    ref=f"{label}-source",
                    zone="battlefield",
                )
                entered = self.add_card(
                    engine,
                    seat=seat,
                    name=name,
                    ref=f"{label}-entry",
                    zone="hand",
                )
                self.register_evolve(engine, source)

                self.enter(engine, entered, controller=seat)

                self.assertFalse(engine.state.stack)
                self.assertFalse(engine.state.pending_trigger_batches)

    def test_malformed_evolve_event_rejects_without_mutation(self):
        session = self.session(70210150)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Cloudfin Raptor",
            ref="malformed-event-source",
            zone="battlefield",
        )
        self.register_evolve(engine, source)
        before = authoritative_state_hash(engine.state)

        with self.assertRaises(GameRuleError):
            engine._dispatch_semantic_event(
                "creature.enter",
                {
                    "card": "",
                    "card_zone_change_counter": True,
                    "controller": "A",
                },
                sources=[source],
            )

        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertFalse(engine.state.stack)
        self.assertFalse(engine.state.pending_trigger_batches)

    def test_intervening_condition_uses_current_characteristics_and_identity(self):
        session = self.session(70210201)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Cloudfin Raptor",
            ref="current-characteristics-source",
            zone="battlefield",
        )
        entered = self.add_card(
            engine,
            seat="A",
            name="Evolve Test Beast",
            ref="current-characteristics-entry",
            zone="hand",
        )
        self.register_evolve(engine, source)
        self.enter(engine, entered, controller="A")
        source.counters["+1/+1"] = 3

        self.resolve_top(engine)

        self.assertEqual(3, source.counters["+1/+1"])

        session = self.session(70210202)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Cloudfin Raptor",
            ref="identity-source",
            zone="battlefield",
        )
        entered = self.add_card(
            engine,
            seat="A",
            name="Evolve Test Beast",
            ref="identity-entry",
            zone="hand",
        )
        self.register_evolve(engine, source)
        self.enter(engine, entered, controller="A")
        engine.move_card(entered.object_id, "exile", log=False)
        engine.move_card(entered.object_id, "battlefield", controller="A", log=False)

        self.resolve_top(engine)

        self.assertNotIn("+1/+1", source.counters)

        session = self.session(70210203)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Cloudfin Raptor",
            ref="source-identity-source",
            zone="battlefield",
        )
        entered = self.add_card(
            engine,
            seat="A",
            name="Evolve Test Beast",
            ref="source-identity-entry",
            zone="hand",
        )
        self.register_evolve(engine, source)
        self.enter(engine, entered, controller="A")
        engine.move_card(source.object_id, "exile", log=False)
        engine.move_card(source.object_id, "battlefield", controller="A", log=False)

        self.resolve_top(engine)

        self.assertNotIn("+1/+1", source.counters)

    def test_quantity_replacement_and_control_change_use_trigger_controller(self):
        session = self.session(70210301)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Cloudfin Raptor",
            ref="replacement-source",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="A",
            name="Doubling Season",
            ref="replacement-doubling",
            zone="battlefield",
        )
        entered = self.add_card(
            engine,
            seat="A",
            name="Evolve Test Beast",
            ref="replacement-entry",
            zone="hand",
        )
        self.register_evolve(engine, source)
        self.enter(engine, entered, controller="A")
        self.resolve_top(engine)
        self.assertEqual(2, source.counters["+1/+1"])

        session = self.session(70210302)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Cloudfin Raptor",
            ref="control-source",
            zone="battlefield",
        )
        entered = self.add_card(
            engine,
            seat="A",
            name="Evolve Test Beast",
            ref="control-entry",
            zone="hand",
        )
        self.register_evolve(engine, source)
        self.enter(engine, entered, controller="A")
        self.assertEqual("A", engine.state.stack[-1].controller)
        engine.change_control(source.object_id, "B", reason="Evolve fixture")

        self.resolve_top(engine)

        self.assertEqual("B", source.controller)
        self.assertEqual(1, source.counters["+1/+1"])

    def test_four_player_evolve_triggers_are_apnap_and_public(self):
        session = self.session(70210401, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        source_a = self.add_card(
            engine,
            seat="A",
            name="Cloudfin Raptor",
            ref="apnap-source-a",
            zone="battlefield",
        )
        source_b = self.add_card(
            engine,
            seat="B",
            name="Cloudfin Raptor",
            ref="apnap-source-b",
            zone="battlefield",
        )
        entered_a = self.add_card(
            engine,
            seat="A",
            name="Evolve Test Beast",
            ref="apnap-entry-a",
            zone="hand",
        )
        entered_b = self.add_card(
            engine,
            seat="B",
            name="Evolve Test Beast",
            ref="apnap-entry-b",
            zone="hand",
        )
        private_a = self.add_card(
            engine,
            seat="A",
            name="Evolve Test Minnow",
            ref="private-a",
            zone="hand",
        )
        private_b = self.add_card(
            engine,
            seat="B",
            name="Evolve Test Minnow",
            ref="private-b",
            zone="hand",
        )
        self.register_evolve(engine, source_a)

        engine._move_cards_simultaneously(
            (
                (entered_b.object_id, "battlefield"),
                (entered_a.object_id, "battlefield"),
            ),
            reason="simultaneous Evolve fixture",
        )

        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(["A", "B"], [group["controller"] for group in batch["groups"]])
        self.assertEqual(
            {source_a.object_id, source_b.object_id},
            {item["source_object_id"] for item in batch.items},
        )
        projector = StateProjector(self.db, engine.state)
        projected_a = json.dumps(projector._snapshot("pilot:A"), sort_keys=True)
        projected_b = json.dumps(projector._snapshot("pilot:B"), sort_keys=True)
        self.assertNotIn(private_b.ref, projected_a)
        self.assertNotIn(private_a.ref, projected_b)
        self.assertIn(entered_a.ref, projected_b)
        self.assertIn(entered_b.ref, projected_a)

    def test_evolve_counter_placement_replays_exactly(self):
        session = self.session(70210501, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Cloudfin Raptor",
            ref="replay-source",
            zone="battlefield",
        )
        entered = self.add_card(
            engine,
            seat="A",
            name="Evolve Test Beast",
            ref="replay-entry",
            zone="hand",
        )
        self.register_evolve(engine, source)
        self.enter(engine, entered, controller="A")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for seat in engine.seats:
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)

        self.assertEqual(1, source.counters["+1/+1"])
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "evolve-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
