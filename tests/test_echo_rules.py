from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import DB_PATH, ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.control_history import (
    CONTROL_HISTORY_VERSION,
    begin_upkeep_control_epoch,
    record_control_acquisition,
)
from quorune.echo import (
    ECHO_CONTROL_CONDITION_FIELD,
    EchoError,
    FixedManaEchoSpec,
    compile_fixed_mana_echo,
)
from quorune.deck import DeckLoader
from quorune.engine import CommanderEngine, TURN_STEPS
from quorune.model import CardInstance, GameState
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.record_control_history import (
    validate_control_history_provenance,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.rules.echo_capability_shapes import (
    fixed_mana_echo_node_capabilities,
)
from quorune.semantics import SemanticProgram
from quorune.trigger_processing import collect_trigger_items


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def echo_record(text: str, suffix: int) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-9002-{suffix:012d}",
        name=f"Echo Fixture {suffix}",
        mana_cost="{1}{G}",
        mana_value=2.0,
        type_line="Creature — Troll",
        oracle_text=text,
        power="3",
        toughness="3",
        loyalty=None,
        defense=None,
        colors=("G",),
        color_identity=("G",),
        keywords=("Echo",),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class EchoCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.database = CardDatabase(DB_PATH)

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def test_fixed_mana_echo_is_source_spanned_and_capability_closed(self):
        for suffix, text, generic, green in (
            (1, "Echo {1}{G}", 1, 1),
            (2, "Echo {0}", 0, 0),
        ):
            with self.subTest(text=text):
                record = echo_record(text, suffix)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                node = next(
                    value
                    for face in ir.faces
                    for value in face.nodes
                    if value.template_id == "fixed-mana-echo-v1"
                )
                self.assertEqual("exact", ir.status)
                self.assertEqual("triggered_ability", node.kind)
                self.assertEqual("step.begin", node.event)
                self.assertEqual(
                    ("trigger.keyword.echo.fixed_mana",),
                    node.capability_dependencies,
                )
                self.assertEqual(
                    record.oracle_text,
                    record.oracle_text[node.span.start : node.span.end],
                )
                self.assertEqual(generic, node.effects[0]["cost"]["GENERIC"])
                self.assertEqual(green, node.effects[0]["cost"]["G"])
                self.assertEqual(
                    {
                        "all": [
                            {
                                "field": "step",
                                "op": "eq",
                                "value": "upkeep",
                            },
                            {
                                "field": ECHO_CONTROL_CONDITION_FIELD,
                                "op": "truthy",
                            },
                        ]
                    },
                    node.event_condition,
                )
                self.assertEqual(("intervening_condition",), node.runtime_coverage)
                self.assertEqual(
                    ("trigger.keyword.echo.fixed_mana",),
                    fixed_mana_echo_node_capabilities(
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
                    == "fixed-mana-echo-v1"
                )
                self.assertTrue(program.capability_closure["trusted"])

    def test_unsupported_echo_variants_remain_precise_residuals(self):
        for suffix, text in enumerate(
            (
                "Echo {W/U}",
                "Echo {S}",
                "Echo {X}",
                "Echo—Pay 2 life.",
                "Echo {1} or {G}",
            ),
            start=10,
        ):
            with self.subTest(text=text):
                self.assertIsNone(compile_fixed_mana_echo(text))
                ir = compile_oracle_card(
                    echo_record(text, suffix),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_descriptor_shape_and_dependency_mutations_fail_closed(self):
        spec = compile_fixed_mana_echo("Echo {2}{G}")
        self.assertIsNotNone(spec)
        self.assertEqual(spec, FixedManaEchoSpec.from_dict(spec.to_dict()))
        for value in (
            {"cost_text": "{1}", "mana_cost": {"GENERIC": True}},
            {"cost_text": "{S}", "mana_cost": {}},
            {"cost_text": "{1}", "mana_cost": {}, "extra": True},
        ):
            with self.subTest(value=value):
                with self.assertRaises(EchoError):
                    FixedManaEchoSpec.from_dict(value)

        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in registry["capabilities"]
            if row["id"] == "trigger.placement.apnap"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            echo_record("Echo {1}", 30),
            capability_registry=CapabilityRegistry(registry),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(
            any(
                "trigger.placement.apnap" in blocker
                for residual in ir.material_residuals
                for blocker in residual.blockers
            )
        )
        with patch(
            "quorune.compiler.keyword_nodes.fixed_mana_echo_node",
            return_value=None,
        ):
            mutated = compile_oracle_card(
                echo_record("Echo {1}", 31),
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertNotEqual("exact", mutated.status)
        self.assertTrue(mutated.material_residuals)


class EchoRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
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

    def add_echo_permanent(
        self,
        engine,
        *,
        name: str = "Karmic Guide",
        ref: str,
        controller: str = "A",
    ) -> CardInstance:
        record = self.db.lookup(name)
        timestamp = engine._next_zone_timestamp()
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=controller,
            controller=controller,
            zone="battlefield",
            zone_timestamp=timestamp,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        record_control_acquisition(
            card,
            controller_turns_begun=engine.state.players[
                controller
            ].turns_begun,
            timestamp=timestamp,
            history_version=engine.state.control_history_version,
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[controller].zones["battlefield"].append(
            card.object_id
        )
        return card

    def echo_program(self, engine, source: CardInstance) -> SemanticProgram:
        existing = [
            program
            for program in engine.semantics.programs_for_oracle(
                source.oracle_id,
                active_zone="battlefield",
            )
            if program.provenance.get("template_id") == "fixed-mana-echo-v1"
        ]
        if existing:
            return existing[0]
        program = next(
            value
            for value in generated_programs(
                self.db,
                self.db.lookup(source.printed_name),
                trust_level="trusted",
                capability_registry=load_default_capability_registry(),
                capability_profile="commander_review",
            )
            if value.provenance.get("template_id")
            == "fixed-mana-echo-v1"
        )
        engine.semantics.put(program)
        return program

    def discover_upkeep(self, engine, player: str):
        previous = begin_upkeep_control_epoch(
            engine.state.players[player],
            timestamp=engine.state.timestamp_sequence,
            history_version=engine.state.control_history_version,
        )
        return collect_trigger_items(
            engine,
            "step.begin",
            {
                "phase": "beginning",
                "step": "upkeep",
                "player": player,
                "previous_upkeep_timestamp": previous,
            },
        )

    def begin_echo_resolution(self, engine, item) -> None:
        engine.state.stack.append(item)
        engine._prepare_stack_resolution()

    def test_prior_upkeep_control_history_triggers_once(self):
        session = self.session(70230001)
        engine = session.engine
        source = self.add_echo_permanent(engine, ref="echo-once")
        self.echo_program(engine, source)

        first = self.discover_upkeep(engine, "A")
        self.assertEqual(1, len(first))
        self.assertEqual(
            source.acquired_control_timestamp,
            first[0].context["echo_control_acquisition_timestamp"],
        )
        self.assertEqual([], self.discover_upkeep(engine, "A"))

        engine.change_control(source.object_id, "B", reason="Echo fixture")
        second_controller = self.discover_upkeep(engine, "B")
        self.assertEqual(1, len(second_controller))
        self.assertEqual("B", second_controller[0].controller)

    def test_engine_upkeep_step_records_boundary_before_echo_discovery(self):
        session = self.session(70230008)
        engine = session.engine
        source = self.add_echo_permanent(engine, ref="echo-engine-step")
        self.echo_program(engine, source)
        previous = engine.state.players["A"].last_upkeep_timestamp
        engine.state.active_player = "A"
        engine.state.phase_index = TURN_STEPS.index(("beginning", "upkeep"))

        engine._enter_step()

        self.assertGreaterEqual(
            engine.state.players["A"].last_upkeep_timestamp,
            previous,
        )
        pending_sources = {
            item.source_object_id
            for batch in engine.state.pending_trigger_batches
            for group in batch.groups
            for item in group.items
        }
        stack_sources = {item.source_object_id for item in engine.state.stack}
        self.assertIn(source.object_id, pending_sources | stack_sources)

    def test_pay_keeps_and_decline_sacrifices_through_typed_intents(self):
        session = self.session(70230002)
        engine = session.engine
        source = self.add_echo_permanent(engine, ref="echo-pay")
        self.echo_program(engine, source)
        item = self.discover_upkeep(engine, "A")[0]
        engine.state.players["A"].mana_pool.update(
            {"W": 2, "C": 3}
        )
        self.begin_echo_resolution(engine, item)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": True,
                "plan": "PAY_ECHO",
                "reason": "Keep the Echo permanent.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", source.zone)

        session = self.session(70230003)
        engine = session.engine
        source = self.add_echo_permanent(engine, ref="echo-decline")
        self.echo_program(engine, source)
        item = self.discover_upkeep(engine, "A")[0]
        self.begin_echo_resolution(engine, item)
        decision = engine.state.pending_decision.payload_by_actor["A"]
        self.assertEqual([False], decision["legal_actions"][0]["choice_schema"]["legal_values"])
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": False,
                "plan": "DECLINE_ECHO",
                "reason": "Decline the Echo payment.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", source.zone)

    def test_zero_echo_is_payable_without_mana(self):
        session = self.session(70230009)
        engine = session.engine
        source = self.add_echo_permanent(
            engine,
            name="Shah of Naar Isle",
            ref="echo-zero",
        )
        self.echo_program(engine, source)
        item = self.discover_upkeep(engine, "A")[0]
        self.begin_echo_resolution(engine, item)
        decision = engine.state.pending_decision.payload_by_actor["A"]
        self.assertTrue(decision["payable"])
        self.assertEqual(
            [True, False],
            decision["legal_actions"][0]["choice_schema"]["legal_values"],
        )
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": True,
                "plan": "PAY_ECHO",
                "reason": "Pay the zero Echo cost.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", source.zone)

    def test_control_change_and_zone_change_preserve_trigger_time_echo_facts(self):
        session = self.session(70230004)
        engine = session.engine
        source = self.add_echo_permanent(engine, ref="echo-control")
        self.echo_program(engine, source)
        item = self.discover_upkeep(engine, "A")[0]
        engine.change_control(source.object_id, "B", reason="in response")
        self.begin_echo_resolution(engine, item)
        self.assertEqual(["A"], engine.state.pending_decision.actors)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": False,
                "plan": "DECLINE_ECHO",
                "reason": "Cannot sacrifice another player's permanent.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", source.zone)
        self.assertEqual("B", source.controller)

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        source = self.add_echo_permanent(engine, ref="echo-returned")
        self.echo_program(engine, source)
        item = self.discover_upkeep(engine, "A")[0]
        returned_logical = source.logical_object_id
        engine.move_card(source.object_id, "graveyard", log=False)
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertNotEqual(returned_logical, source.logical_object_id)
        self.begin_echo_resolution(engine, item)
        self.assertIsNone(engine.state.pending_decision)
        self.assertEqual("battlefield", source.zone)

    def test_stale_source_and_malformed_history_fail_before_mutation(self):
        session = self.session(70230005)
        engine = session.engine
        source = self.add_echo_permanent(engine, ref="echo-malformed")
        program = self.echo_program(engine, source)
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(Exception, "previous-upkeep"):
            engine._semantic_event_condition_matches(
                program.event_condition,
                source=source,
                context={
                    "step": "upkeep",
                    "player": "A",
                    "previous_upkeep_timestamp": True,
                },
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_control_history_version_preserves_legacy_record_hash_mode(self):
        session = self.session(70230010)
        payload = copy.deepcopy(session.state.to_dict())
        payload.pop("control_history_version")
        for card in payload["cards"].values():
            card.pop("acquired_control_timestamp", None)
        for player in payload["players"].values():
            player.pop("last_upkeep_timestamp", None)
        legacy = GameState.from_dict(payload)
        self.assertIsNone(legacy.control_history_version)
        validate_control_history_provenance({"format": {}}, None)

        for invalid in (True, CONTROL_HISTORY_VERSION + 1, "1"):
            malformed = copy.deepcopy(payload)
            malformed["control_history_version"] = invalid
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    ValueError,
                    "[Cc]ontrol-history version",
                ):
                    GameState.from_dict(malformed)

        engine = CommanderEngine(self.db, legacy, session.engine.semantics)
        source = self.add_echo_permanent(engine, ref="echo-legacy")
        before_timestamp = engine.state.timestamp_sequence
        engine.change_control(source.object_id, "B", reason="legacy replay")
        self.assertEqual(before_timestamp, engine.state.timestamp_sequence)
        previous = begin_upkeep_control_epoch(
            engine.state.players["B"],
            timestamp=engine.state.timestamp_sequence,
            history_version=engine.state.control_history_version,
        )
        self.assertEqual(0, previous)
        serialized = engine.state.to_dict()
        self.assertNotIn("control_history_version", serialized)
        self.assertNotIn(
            "acquired_control_timestamp",
            serialized["cards"][source.object_id],
        )
        self.assertNotIn(
            "last_upkeep_timestamp",
            serialized["players"]["B"],
        )

    def test_multiple_echo_sources_share_apnap_trigger_placement(self):
        session = self.session(70230006, players=4)
        engine = session.engine
        first = self.add_echo_permanent(engine, ref="echo-a1")
        second = self.add_echo_permanent(engine, ref="echo-a2")
        self.echo_program(engine, first)
        items = self.discover_upkeep(engine, "A")
        self.assertEqual(
            {first.object_id, second.object_id},
            {item.source_object_id for item in items},
        )
        self.assertTrue(all(item.controller == "A" for item in items))

    def test_four_player_echo_choice_is_seat_scoped_private_and_replays(self):
        session = self.session(70230007, players=4)
        engine = session.engine
        source = self.add_echo_permanent(engine, ref="echo-replay")
        self.echo_program(engine, source)
        item = self.discover_upkeep(engine, "A")[0]
        self.begin_echo_resolution(engine, item)

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        self.assertIsNotNone(projected)
        for seat in "BCD":
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        serialized = json.dumps(projected, sort_keys=True)
        self.assertNotIn(source.object_id, serialized)

        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": False,
                "plan": "DECLINE_ECHO",
                "reason": "Decline the Echo payment.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "echo-replay"
            session.save(record_dir)
            manifest_path = record_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                CONTROL_HISTORY_VERSION,
                manifest["format"]["control_history_version"],
            )
            tampered = copy.deepcopy(manifest)
            tampered["format"]["control_history_version"] = 0
            manifest_path.write_text(
                json.dumps(tampered),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "Control-history provenance",
            ):
                replay_record(record_dir, self.db, verify=True)
            manifest_path.write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(1, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
