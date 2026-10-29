from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import DB_PATH, ROOT, keep_all, make_session
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase, CardRecord
from quorune.cumulative_upkeep import (
    CumulativeUpkeepError,
    FixedLifeCumulativeUpkeepSpec,
    FixedManaCumulativeUpkeepSpec,
    compile_fixed_life_cumulative_upkeep,
    compile_fixed_mana_cumulative_upkeep,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance, StackItem
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
from quorune.rules.cumulative_upkeep_capability_shapes import (
    fixed_life_cumulative_upkeep_node_capabilities,
    fixed_mana_cumulative_upkeep_node_capabilities,
)
from quorune.semantics import SemanticProgram
from quorune.trigger_processing import collect_trigger_items


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def cumulative_record(text: str, suffix: int) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-9001-{suffix:012d}",
        name=f"Cumulative Upkeep Fixture {suffix}",
        mana_cost="{1}{U}",
        mana_value=2.0,
        type_line="Enchantment",
        oracle_text=text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=("Cumulative Upkeep",),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class CumulativeUpkeepCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.database = CardDatabase(DB_PATH)

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def test_fixed_mana_cumulative_upkeep_is_source_spanned_and_capability_closed(
        self,
    ):
        record = cumulative_record("Cumulative upkeep {1}{U}", 1)
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        node = next(
            value
            for face in ir.faces
            for value in face.nodes
            if value.template_id == "fixed-mana-cumulative-upkeep-v1"
        )

        self.assertEqual("exact", ir.status)
        self.assertEqual("triggered_ability", node.kind)
        self.assertEqual("step.begin", node.event)
        self.assertEqual(
            ("counter.producer.cumulative_upkeep_fixed_mana",),
            node.capability_dependencies,
        )
        self.assertEqual(record.oracle_text, record.oracle_text[node.span.start : node.span.end])
        self.assertEqual(
            {
                "op": "cumulative_upkeep",
                "player": "$controller",
                "source": "$source",
                "cost_per_counter": {
                    "GENERIC": 1,
                    "W": 0,
                    "U": 1,
                    "B": 0,
                    "R": 0,
                    "G": 0,
                    "C": 0,
                },
            },
            node.effects[0],
        )
        self.assertEqual(
            ("counter.producer.cumulative_upkeep_fixed_mana",),
            fixed_mana_cumulative_upkeep_node_capabilities(
                effects=node.effects,
                event_condition=node.event_condition,
                target_schema=node.target_schema,
                mechanic_ids=node.mechanics,
            ),
        )
        program = next(
            value
            for value in generated_programs(
                self.database,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if value.provenance.get("template_id")
            == "fixed-mana-cumulative-upkeep-v1"
        )
        self.assertTrue(program.capability_closure["trusted"])
        self.assertEqual(
            {"line": 1, "start": 0, "end": len(record.oracle_text)},
            program.provenance["source_span"],
        )

    def test_fixed_life_cumulative_upkeep_is_source_spanned_and_capability_closed(
        self,
    ):
        record = cumulative_record("Cumulative upkeep—Pay 2 life.", 2)
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        node = next(
            value
            for face in ir.faces
            for value in face.nodes
            if value.template_id == "fixed-life-cumulative-upkeep-v1"
        )

        self.assertEqual("exact", ir.status)
        self.assertEqual("triggered_ability", node.kind)
        self.assertEqual("step.begin", node.event)
        self.assertEqual(
            ("counter.producer.cumulative_upkeep_fixed_life",),
            node.capability_dependencies,
        )
        self.assertEqual(
            record.oracle_text,
            record.oracle_text[node.span.start : node.span.end],
        )
        self.assertEqual(
            {
                "op": "cumulative_upkeep_life",
                "player": "$controller",
                "source": "$source",
                "life_per_counter": 2,
            },
            node.effects[0],
        )
        self.assertEqual(
            ("counter.producer.cumulative_upkeep_fixed_life",),
            fixed_life_cumulative_upkeep_node_capabilities(
                effects=node.effects,
                event_condition=node.event_condition,
                target_schema=node.target_schema,
                mechanic_ids=node.mechanics,
            ),
        )
        program = next(
            value
            for value in generated_programs(
                self.database,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if value.provenance.get("template_id")
            == "fixed-life-cumulative-upkeep-v1"
        )
        self.assertTrue(program.capability_closure["trusted"])
        self.assertEqual(
            {"line": 1, "start": 0, "end": len(record.oracle_text)},
            program.provenance["source_span"],
        )

    def test_unsupported_cumulative_upkeep_costs_remain_material_residuals(self):
        for suffix, text in enumerate(
            (
                "Cumulative upkeep {W} or {U}",
                "Cumulative upkeep {S}",
                "Cumulative upkeep {W/U}",
                "Cumulative upkeep {X}",
                "Cumulative upkeep {0}",
                "Cumulative upkeep {1}, cumulative upkeep {1}",
            ),
            start=10,
        ):
            with self.subTest(text=text):
                self.assertIsNone(compile_fixed_mana_cumulative_upkeep(text))
                ir = compile_oracle_card(
                    cumulative_record(text, suffix),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_life_descriptor_and_adjacent_costs_fail_closed(self):
        spec = compile_fixed_life_cumulative_upkeep(
            "Cumulative upkeep—Pay 2 life."
        )
        self.assertIsNotNone(spec)
        self.assertEqual(
            spec,
            FixedLifeCumulativeUpkeepSpec.from_dict(spec.to_dict()),
        )
        for value in (
            {"cost_text": "Pay 1 life", "life_per_counter": True},
            {"cost_text": "Pay 1 life", "life_per_counter": 2},
            {"cost_text": "Pay 0 life", "life_per_counter": 0},
            {
                "cost_text": "Pay 1 life",
                "life_per_counter": 1,
                "extra": True,
            },
        ):
            with self.subTest(value=value):
                with self.assertRaises(CumulativeUpkeepError):
                    FixedLifeCumulativeUpkeepSpec.from_dict(value)
        for suffix, text in enumerate(
            (
                "Cumulative upkeep—Pay 0 life.",
                "Cumulative upkeep—Pay 1 life and {B}.",
                "Cumulative upkeep—An opponent gains 1 life.",
                "Cumulative upkeep—Pay X life.",
            ),
            start=40,
        ):
            with self.subTest(text=text):
                self.assertIsNone(compile_fixed_life_cumulative_upkeep(text))
                ir = compile_oracle_card(
                    cumulative_record(text, suffix),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_descriptor_and_node_shape_reject_malformed_values(self):
        spec = compile_fixed_mana_cumulative_upkeep("Cumulative upkeep {2}{G}")
        self.assertIsNotNone(spec)
        self.assertEqual(spec, FixedManaCumulativeUpkeepSpec.from_dict(spec.to_dict()))
        for value in (
            {"cost_text": "{1}", "mana_cost": {"GENERIC": True}},
            {"cost_text": "{S}", "mana_cost": {}},
            {"cost_text": "{1}", "mana_cost": {}, "extra": True},
        ):
            with self.subTest(value=value):
                with self.assertRaises(CumulativeUpkeepError):
                    FixedManaCumulativeUpkeepSpec.from_dict(value)
        node = compile_oracle_card(
            cumulative_record("Cumulative upkeep {1}", 20),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        ).faces[0].nodes[0]
        malformed = dict(node.effects[0])
        malformed["cost_per_counter"] = dict(malformed["cost_per_counter"])
        malformed["cost_per_counter"]["GENERIC"] = True
        self.assertEqual(
            (),
            fixed_mana_cumulative_upkeep_node_capabilities(
                effects=(malformed,),
                event_condition=node.event_condition,
                target_schema=None,
                mechanic_ids=node.mechanics,
            ),
        )

    def test_dependency_and_compiler_mutations_fail_closed(self):
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in registry["capabilities"]
            if row["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            cumulative_record("Cumulative upkeep {1}", 30),
            capability_registry=CapabilityRegistry(registry),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(
            any(
                "counter.placement.quantity_replacement" in blocker
                for residual in ir.material_residuals
                for blocker in residual.blockers
            )
        )
        with patch(
            "quorune.compiler.keyword_nodes.fixed_mana_cumulative_upkeep_node",
            return_value=None,
        ):
            mutated = compile_oracle_card(
                cumulative_record("Cumulative upkeep {1}", 31),
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertNotEqual("exact", mutated.status)
        self.assertTrue(mutated.material_residuals)

    def test_fixed_life_dependency_and_compiler_mutations_fail_closed(self):
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in registry["capabilities"]
            if row["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            cumulative_record("Cumulative upkeep—Pay 1 life.", 50),
            capability_registry=CapabilityRegistry(registry),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(
            any(
                "counter.placement.quantity_replacement" in blocker
                for residual in ir.material_residuals
                for blocker in residual.blockers
            )
        )
        with patch(
            "quorune.compiler.keyword_nodes.fixed_life_cumulative_upkeep_node",
            return_value=None,
        ):
            mutated = compile_oracle_card(
                cumulative_record("Cumulative upkeep—Pay 1 life.", 51),
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertNotEqual("exact", mutated.status)
        self.assertTrue(mutated.material_residuals)


class CumulativeUpkeepRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "cumulative-upkeep.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
                ROOT
                / "tests"
                / "fixtures"
                / "fixed-life-cumulative-upkeep-cards.json",
            ],
            database,
        )
        cls.db = CardDatabase(database)
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
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_permanent(
        self, engine, *, name: str, ref: str, controller: str = "A"
    ) -> CardInstance:
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=controller,
            controller=controller,
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[controller].zones["battlefield"].append(card.object_id)
        return card

    def begin_upkeep(self, session, source: CardInstance) -> StackItem:
        item = self.queue_upkeep(session, source)
        session.engine._continue_resolution(
            stack_ref=item.ref,
            effects=[
                dict(value)
                for value in session.engine.semantics.get(item.semantic_key).effects
            ],
            destination=None,
            note="",
        )
        return item

    def begin_life_upkeep(
        self,
        session,
        source: CardInstance,
        *,
        life_per_counter: int,
    ) -> StackItem:
        item = self.queue_life_upkeep(
            session,
            source,
            life_per_counter=life_per_counter,
        )
        session.engine._prepare_stack_resolution()
        return item

    def queue_upkeep(self, session, source: CardInstance) -> StackItem:
        engine = session.engine
        program = SemanticProgram(
            key=f"test:cumulative-upkeep:{source.ref}",
            label="Fixed-mana cumulative upkeep",
            effects=[
                {
                    "op": "cumulative_upkeep",
                    "player": source.controller,
                    "source": source.ref,
                    "cost_per_counter": {
                        "GENERIC": 1,
                        "W": 0,
                        "U": 0,
                        "B": 0,
                        "R": 0,
                        "G": 0,
                        "C": 0,
                    },
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id=f"stack-{source.ref}",
            ref=f"S-{source.ref}",
            kind="triggered_ability",
            controller=source.controller,
            label=program.label,
            semantic_key=program.key,
            source_object_id=source.object_id,
            visibility=list(engine.seats),
            context={
                "source_logical_object_id": source.logical_object_id,
            },
        )
        engine.state.stack.append(item)
        return item

    def queue_life_upkeep(
        self,
        session,
        source: CardInstance,
        *,
        life_per_counter: int,
    ) -> StackItem:
        engine = session.engine
        from uuid import NAMESPACE_URL, uuid5
        identity = str(uuid5(NAMESPACE_URL, f'life-upkeep-fixture:{source.object_id}:{life_per_counter}'))
        record = replace(
            self.db.by_oracle_id(source.oracle_id),
            oracle_id=identity, name='Generic Life Upkeep Runtime Body',
            oracle_text=(
                f"Cumulative upkeep—Pay {life_per_counter} life. "
                "(At the beginning of your upkeep, put an age counter on "
                "this permanent, then sacrifice it unless you pay its upkeep "
                "cost for each age counter on it.)"
            ),
            keywords=("Cumulative upkeep",),
        )
        if not hasattr(self, '_life_upkeep_records'):
            self._life_upkeep_records = {}
            by_oracle_id = self.db.by_oracle_id
            self.enterContext(patch.object(self.db, 'by_oracle_id', side_effect=lambda value:
                self._life_upkeep_records[value] if value in self._life_upkeep_records else by_oracle_id(value)))
        self._life_upkeep_records[identity] = record
        source.oracle_id = identity
        source.printed_name = record.name
        program = next(
            value
            for value in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=load_default_capability_registry(),
                capability_profile="commander_review",
            )
            if value.provenance.get("template_id")
            == "fixed-life-cumulative-upkeep-v1"
        )
        self.assertTrue(program.capability_closure["trusted"])
        self.assertEqual(
            ["counter.producer.cumulative_upkeep_fixed_life"],
            program.capability_dependencies,
        )
        engine.semantics.put(program)
        triggered = collect_trigger_items(
            engine,
            "step.begin",
            {
                "phase": "beginning",
                "step": "upkeep",
                "player": source.controller,
            },
        )
        matching = [
            item
            for item in triggered
            if item.source_object_id == source.object_id
            and item.semantic_key == program.key
        ]
        self.assertEqual(1, len(matching))
        item = matching[0]
        engine.state.stack.append(item)
        return item

    def test_quantity_replacement_changes_age_counter_and_payment_together(self):
        session = self.session(70224001)
        engine = session.engine
        source = self.add_permanent(engine, name="Mystic Remora", ref="remora")
        self.add_permanent(engine, name="Doubling Season", ref="doubling")
        engine.state.players["A"].mana_pool["C"] = 6

        self.begin_upkeep(session, source)

        self.assertEqual(2, source.counters["age"])
        decision = engine.state.pending_decision
        self.assertEqual("semantic.choice", decision.kind)
        payload = decision.payload_by_actor["A"]
        self.assertEqual(2, payload["age_counters"])
        self.assertEqual(2, payload["cost"]["GENERIC"])
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": True,
                "plan": "KEEP_PERMANENT",
                "reason": "Pay for both committed age counters.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(4, engine.state.players["A"].mana_pool["C"])
        self.assertEqual("battlefield", source.zone)

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        self.begin_upkeep(session, source)
        self.assertEqual(4, source.counters["age"])
        payload = engine.state.pending_decision.payload_by_actor["A"]
        self.assertEqual(4, payload["age_counters"])
        self.assertEqual(4, payload["cost"]["GENERIC"])

    def test_four_player_replacement_choice_and_payment_are_seat_scoped(self):
        session = self.session(70224002, players=4)
        engine = session.engine
        source = self.add_permanent(engine, name="Mystic Remora", ref="private-remora")
        self.add_permanent(engine, name="Doubling Season", ref="doubling")
        self.add_permanent(
            engine,
            name="Doc Samson, Super Psychiatrist",
            ref="additional",
        )
        self.begin_upkeep(session, source)

        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        for seat in "BCD":
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        serialized = json.dumps(projected, sort_keys=True)
        self.assertNotIn(source.object_id, serialized)
        selected = projected["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {"action_id": "choose", "choices": {"replacement": selected}},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual(["A"], engine.state.pending_decision.actors)
        payload = engine.state.pending_decision.payload_by_actor["A"]
        self.assertEqual(source.counters["age"], payload["age_counters"])
        self.assertEqual(source.counters["age"], payload["cost"]["GENERIC"])

    def test_quantity_replacement_resume_replays_exactly(self):
        session = self.session(70224003, players=4)
        engine = session.engine
        source = self.add_permanent(engine, name="Mystic Remora", ref="replay-remora")
        self.add_permanent(engine, name="Doubling Season", ref="doubling")
        self.add_permanent(
            engine,
            name="Doc Samson, Super Psychiatrist",
            ref="additional",
        )
        self.begin_upkeep(session, source)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        projected = StateProjector(self.db, engine.state)._decision("pilot:A")
        selected = projected["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {"action_id": "choose", "choices": {"replacement": selected}},
        )
        self.assertTrue(result.ok, result.summary)
        actual_age = source.counters["age"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": False,
                "plan": "DECLINE_UPKEEP",
                "reason": "Decline the upkeep payment.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(engine.state)
        self.assertGreater(actual_age, 1)
        self.assertEqual("graveyard", source.zone)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "cumulative-upkeep-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_fixed_life_payment_uses_replaced_age_count(self):
        session = self.session(70224010)
        engine = session.engine
        source = self.add_permanent(
            engine,
            name="Fixed-Life Upkeep Fixture",
            ref="life-remora",
        )
        self.add_permanent(engine, name="Doubling Season", ref="life-doubling")
        before_life = engine.state.players["A"].life

        self.begin_life_upkeep(
            session,
            source,
            life_per_counter=2,
        )

        self.assertEqual(2, source.counters["age"])
        decision = engine.state.pending_decision
        self.assertEqual("semantic.choice", decision.kind)
        payload = decision.payload_by_actor["A"]
        self.assertEqual(2, payload["age_counters"])
        self.assertEqual(4, payload["life_cost"])
        self.assertTrue(payload["payable"])
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": True,
                "plan": "KEEP_PERMANENT",
                "reason": "Pay life for the committed age counters.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(before_life - 4, engine.state.players["A"].life)
        self.assertEqual("battlefield", source.zone)

    def test_fixed_life_replacement_choice_is_private_and_replays(self):
        session = self.session(70224011, players=4)
        engine = session.engine
        source = self.add_permanent(
            engine,
            name="Fixed-Life Upkeep Fixture",
            ref="private-life-remora",
        )
        self.add_permanent(engine, name="Doubling Season", ref="life-double")
        self.add_permanent(
            engine,
            name="Doc Samson, Super Psychiatrist",
            ref="life-add",
        )
        self.begin_life_upkeep(
            session,
            source,
            life_per_counter=1,
        )
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        for seat in "BCD":
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))
        selected = projected["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {"action_id": "choose", "choices": {"replacement": selected}},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        payload = engine.state.pending_decision.payload_by_actor["A"]
        self.assertEqual(source.counters["age"], payload["life_cost"])
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": True,
                "plan": "KEEP_PERMANENT",
                "reason": "Pay the replacement-adjusted life cost.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "cumulative-upkeep-life-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_fixed_life_unpayable_decline_and_stale_payment_fail_closed(self):
        session = self.session(70224012)
        engine = session.engine
        source = self.add_permanent(
            engine,
            name="Fixed-Life Upkeep Fixture",
            ref="unpayable-life-remora",
        )
        engine.state.players["A"].life = 1
        self.begin_life_upkeep(
            session,
            source,
            life_per_counter=2,
        )
        payload = engine.state.pending_decision.payload_by_actor["A"]
        self.assertEqual(2, payload["life_cost"])
        self.assertFalse(payload["payable"])
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": True,
                "plan": "KEEP_PERMANENT",
                "reason": "Attempt an unaffordable payment.",
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(1, engine.state.players["A"].life)
        self.assertEqual(
            "battlefield",
            engine.state.cards[source.object_id].zone,
        )
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": False,
                "plan": "DECLINE_UPKEEP",
                "reason": "Decline the unaffordable payment.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            "graveyard",
            engine.state.cards[source.object_id].zone,
        )

        stale_session = self.session(70224013)
        stale_engine = stale_session.engine
        stale = self.add_permanent(
            stale_engine,
            name="Fixed-Life Upkeep Fixture",
            ref="stale-life-remora",
        )
        self.begin_life_upkeep(
            stale_session,
            stale,
            life_per_counter=1,
        )
        before_life = stale_engine.state.players["A"].life
        stale_engine.state.players["A"].zones["battlefield"].remove(
            stale.object_id
        )
        stale_engine.state.players["A"].zones["graveyard"].append(
            stale.object_id
        )
        stale.zone = "graveyard"
        stale.zone_change_counter += 1
        result = stale_session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": True,
                "plan": "KEEP_PERMANENT",
                "reason": "Reject a stale source payment.",
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(before_life, stale_engine.state.players["A"].life)
        self.assertEqual("graveyard", stale.zone)

    def test_intervening_source_identity_and_control_change_are_respected(self):
        session = self.session(70224004)
        engine = session.engine
        source = self.add_permanent(
            engine,
            name="Mystic Remora",
            ref="departed-remora",
        )
        item = self.queue_upkeep(session, source)
        original_logical = source.logical_object_id
        engine.state.players["A"].zones["battlefield"].remove(source.object_id)
        engine.state.players["A"].zones["graveyard"].append(source.object_id)
        source.zone = "graveyard"
        source.zone_change_counter += 1
        source.zone = "battlefield"
        engine.state.players["A"].zones["graveyard"].remove(source.object_id)
        engine.state.players["A"].zones["battlefield"].append(source.object_id)
        self.assertNotEqual(original_logical, source.logical_object_id)

        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[
                dict(value)
                for value in engine.semantics.get(item.semantic_key).effects
            ],
            destination=None,
            note="",
        )
        self.assertIsNone(engine.state.pending_decision)
        self.assertNotIn("age", source.counters)

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        controlled = self.add_permanent(
            engine,
            name="Mystic Remora",
            ref="controlled-remora",
        )
        item = self.queue_upkeep(session, controlled)
        engine.state.players["A"].zones["battlefield"].remove(
            controlled.object_id
        )
        engine.state.players["B"].zones["battlefield"].append(
            controlled.object_id
        )
        controlled.controller = "B"
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[
                dict(value)
                for value in engine.semantics.get(item.semantic_key).effects
            ],
            destination=None,
            note="",
        )
        self.assertEqual(1, controlled.counters["age"])
        self.assertEqual(["A"], engine.state.pending_decision.actors)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": False,
                "plan": "DECLINE_UPKEEP",
                "reason": "The source is no longer controlled.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", controlled.zone)
        self.assertEqual("B", controlled.controller)

    def test_malformed_stage_fails_and_stale_source_is_noop_before_mutation(self):
        for suffix, mutate in (
            ("stage", {"stage": "unknown"}),
            ("field", {"unexpected": True}),
        ):
            with self.subTest(suffix=suffix):
                session = self.session(70224100 if suffix == "stage" else 70224101)
                engine = session.engine
                source = self.add_permanent(
                    engine, name="Mystic Remora", ref=f"malformed-{suffix}"
                )
                program = SemanticProgram(
                    key=f"test:malformed-cumulative:{suffix}",
                    label="Malformed cumulative upkeep",
                    effects=[
                        {
                            "op": "cumulative_upkeep",
                            "player": "A",
                            "source": source.ref,
                            "cost_per_counter": {"GENERIC": 1},
                            **mutate,
                        }
                    ],
                    trust_level="provisional",
                )
                engine.semantics.put(program)
                item = StackItem(
                    stack_id=f"stack-malformed-{suffix}",
                    ref=f"S-malformed-{suffix}",
                    kind="triggered_ability",
                    controller="A",
                    label=program.label,
                    semantic_key=program.key,
                    source_object_id=source.object_id,
                    visibility=list(engine.seats),
                )
                engine.state.stack.append(item)
                with self.assertRaisesRegex(Exception, "cumulative upkeep"):
                    engine._continue_resolution(
                        stack_ref=item.ref,
                        effects=[dict(program.effects[0])],
                        destination=None,
                        note="",
                    )
                self.assertNotIn("age", source.counters)
                self.assertEqual("battlefield", source.zone)

        session = self.session(70224102)
        engine = session.engine
        source = self.add_permanent(
            engine, name="Mystic Remora", ref="stale-source"
        )
        source.zone = "graveyard"
        engine.state.players["A"].zones["battlefield"].remove(source.object_id)
        engine.state.players["A"].zones["graveyard"].append(source.object_id)
        self.begin_upkeep(session, source)
        self.assertIsNone(engine.state.pending_decision)
        self.assertNotIn("age", source.counters)
        self.assertEqual("graveyard", source.zone)


if __name__ == "__main__":
    unittest.main()
