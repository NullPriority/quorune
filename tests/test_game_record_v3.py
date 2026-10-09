from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jsonschema

from common import keep_all, load_assets, make_session
from quorune.record import (
    event_for_trace,
    inspect_game,
    migrate_v2_game,
    replay_record,
)
from quorune.semantic_runtime import (
    DRAW_INSTRUCTION_MULTIPLIER_HANDLER_ID,
    DRAW_MAXIMUM_HANDLER_ID,
    default_semantic_handler_registry,
    runtime_component_inventory,
)
from quorune.session import CommanderSession


class GameRecordV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_nonsemantic_action_records_without_loading_unused_binding_registry(self):
        session = make_session(self.db, self.mishra, self.zimone, players=2,
                               seed=31001, auto_pass_empty=False)
        principal = session.pending_principals()[0]
        session.packet(principal, full=True)
        with patch("quorune.record_trust.load_default_capability_registry",
                   side_effect=AssertionError("unused semantic provenance input")):
            result = session.act(principal, {"action_id":"keep"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, len(session.commands))
        self.assertNotIn("programs_used", session.commands[0])

    def test_archived_control_provenance_is_rejected_before_historical_commands(self):
        # The sanitized recipe is extracted from a genuine pre-repair record,
        # independently inspected for readable v1 payloads and hash preservation.
        # It records explicit incompatibility, not execution under the old engine.
        from quorune.record import ENGINE_VERSION, semantics_fingerprint
        from quorune.record_trust import runtime_trust_provenance
        from quorune.semantics import SemanticRegistry
        recipe = json.loads((Path(__file__).parent / 'fixtures/historical-control-provenance-recipe.json').read_text(encoding='utf-8'))
        self.assertEqual(1, recipe['control_history_version'])
        semantics = SemanticRegistry()
        historical = runtime_trust_provenance()
        historical.update(recipe['runtime_trust_fingerprints'])
        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory)
            manifest = {
                'schema_version': 3, 'engine_version': ENGINE_VERSION,
                'semantics_fingerprint': semantics_fingerprint(semantics),
                'runtime_trust': historical,
                'format': {'control_history_version': 1},
            }
            (record_dir / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with patch('quorune.record.SemanticRegistry', return_value=semantics), \
                    patch('quorune.record.read_initial_checkpoint') as checkpoint:
                with self.assertRaisesRegex(ValueError, recipe['current_replay_disposition']):
                    replay_record(record_dir, self.db, verify=True)
                checkpoint.assert_not_called()

    def test_pre_rebrand_runtime_identity_namespace_remains_stable(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=30,
            auto_pass_empty=False,
        )
        session.engine.state.game_id = "legacy-game"
        self.assertEqual(
            "ca79a4cafc575ec88c6e032576fb5bfc",
            session.engine._stable_runtime_id("stack", "S1"),
        )

    def test_v3_save_omits_raw_capabilities_and_replays(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=31,
            auto_pass_empty=False,
        )
        principal = session.pending_principals()[0]
        packet = session.packet(principal, full=True)
        raw_token = packet["decision"]["cap"]
        result = session.act(
            principal,
            {
                "action_id": "keep",
                "reason": "Functional opening hand.",
                "plan": ["develop mana"],
                "confidence": 0.9,
                "model_id": "test-pilot",
            },
        )
        self.assertTrue(result.ok, result.summary)
        seat_log = session.decisions[0]
        self.assertTrue(
            all(
                item["id"].startswith("A")
                for item in seat_log["decision_context"]["hand"]
            )
        )
        self.assertIsNotNone(seat_log["projected_state_hash"])
        self.assertLessEqual(len(seat_log["reason"]), 160)
        pending_principal = session.pending_principals()[0]
        old_pending_token = session.engine.permissions.capability_for(pending_principal).token

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "game"
            session.save(record_dir)
            expected = {
                "manifest.json",
                "checkpoint.json",
                "commands.jsonl",
                "events.jsonl",
                "decisions.jsonl",
                "review.json",
                "review.md",
                "initial-checkpoint.json.gz",
            }
            self.assertTrue(expected.issubset({path.name for path in record_dir.iterdir()}))
            self.assertFalse((record_dir / "game.json").exists())
            for path in record_dir.iterdir():
                if path.suffix == ".gz" or not path.is_file():
                    continue
                self.assertNotIn(raw_token, path.read_text(encoding="utf-8"))
            with gzip.open(record_dir / "initial-checkpoint.json.gz", "rt", encoding="utf-8") as handle:
                self.assertNotIn(raw_token, handle.read())
            checkpoint = json.loads((record_dir / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["state"]["capabilities"], {})
            self.assertTrue(
                all(not item["id"].startswith("c_") for item in checkpoint["active_capabilities"])
            )
            manifest_path = record_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_schema = json.loads(
                (
                    Path(__file__).resolve().parents[1]
                    / "schemas"
                    / "game-record-v3-manifest.schema.json"
                ).read_text(encoding="utf-8")
            )
            jsonschema.Draft202012Validator(manifest_schema).validate(manifest)
            self.assertEqual(2, manifest["card_programs"]["schema_version"])
            self.assertEqual(
                session.engine.semantics.card_program_fingerprints(),
                manifest["card_programs"]["fingerprints"],
            )
            self.assertEqual(
                set(manifest["card_programs"]["fingerprints"]),
                set(manifest["card_programs"]["trust"]),
            )
            self.assertEqual(
                64,
                len(
                    manifest["runtime_trust"][
                        "capability_evidence_fingerprint"
                    ]
                ),
            )
            inventory = manifest["runtime_trust"]["runtime_component_inventory"]
            self.assertEqual(runtime_component_inventory(), inventory)
            inventory_ids = {item["handler_id"] for item in inventory}
            self.assertTrue(
                {
                    "continuous.basic_land_type.add_all_lands.v1",
                    DRAW_INSTRUCTION_MULTIPLIER_HANDLER_ID,
                    DRAW_MAXIMUM_HANDLER_ID,
                }.issubset(inventory_ids)
            )
            semantic_inventory = manifest["runtime_trust"][
                "semantic_handler_inventory"
            ]
            self.assertEqual(
                default_semantic_handler_registry().inventory(),
                semantic_inventory,
            )
            self.assertTrue(
                {
                    "effect.zone-attachment.reanimate_attached_creature_aura.v1",
                    "generic.fixed-counter-placement-set.v1",
                    "generic.fixed-player-counter-placement.v1",
                }.issubset(
                    {item["handler_id"] for item in semantic_inventory}
                ),
            )
            self.assertEqual(
                64,
                len(
                    manifest["runtime_trust"][
                        "semantic_handler_registry_fingerprint"
                    ]
                ),
            )
            command = json.loads(
                (record_dir / "commands.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(
                2, command["semantics"]["card_program_schema_version"]
            )
            self.assertEqual({}, command["semantics"]["card_programs_used"])
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"])
            self.assertEqual(replay["commands"], 1)
            fingerprint_key = next(
                iter(manifest["card_programs"]["fingerprints"])
            )
            original_runtime_fingerprint = manifest["runtime_trust"][
                "runtime_component_registry_fingerprint"
            ]
            manifest["runtime_trust"][
                "runtime_component_registry_fingerprint"
            ] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError, "Runtime trust provenance mismatch"
            ):
                replay_record(record_dir, self.db, verify=True)
            manifest["runtime_trust"][
                "runtime_component_registry_fingerprint"
            ] = original_runtime_fingerprint
            original_basis = manifest["card_programs"]["trust"][
                fingerprint_key
            ]["trust_basis"]
            manifest["card_programs"]["trust"][fingerprint_key][
                "trust_basis"
            ] = "provisional"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError, "CardProgram trust provenance mismatch"
            ):
                replay_record(record_dir, self.db, verify=True)
            manifest["card_programs"]["trust"][fingerprint_key][
                "trust_basis"
            ] = original_basis
            manifest["card_programs"]["fingerprints"][fingerprint_key] = (
                "0" * 64
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError, "CardProgram fingerprint mismatch"
            ):
                replay_record(record_dir, self.db, verify=True)
            manifest["card_programs"]["fingerprints"][fingerprint_key] = (
                session.engine.semantics.card_program_fingerprints()[
                    fingerprint_key
                ]
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            manifest["card_programs"] = "malformed"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError, "CardProgram section is malformed"
            ):
                replay_record(record_dir, self.db, verify=True)
            manifest["card_programs"] = {
                "schema_version": 2,
                "fingerprints": (
                    session.engine.semantics.card_program_fingerprints()
                ),
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            loaded = CommanderSession.load(
                self.db,
                record_dir,
                semantics_path=record_dir / "semantics.json",
            )
            next_principal = loaded.pending_principals()[0]
            refreshed = loaded.packet(next_principal, full=True)["decision"]["cap"]
            self.assertNotEqual(refreshed, old_pending_token)

    def test_trace_levels_remove_bookkeeping_but_debug_retains_it(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=2,
            auto_pass_empty=False,
        )
        event = next(event for event in session.state.events if event.code == "card.draw.private")
        self.assertIsNotNone(event_for_trace(event, "debug"))
        self.assertIsNotNone(event_for_trace(event, "standard"))
        self.assertIsNone(event_for_trace(event, "minimal"))
        session.engine._log("A", "priority.pass", "A passed priority.", importance=0)
        pass_event = session.state.events[-1]
        self.assertIsNotNone(event_for_trace(pass_event, "debug"))
        self.assertIsNone(event_for_trace(pass_event, "standard"))
        self.assertIsNone(event_for_trace(pass_event, "minimal"))

    def test_rejected_attempt_is_a_decision_but_not_a_replay_command(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=12,
            auto_pass_empty=False,
        )
        principal = session.pending_principals()[0]
        session.packet(principal, full=True)
        result = session.act(
            principal,
            {
                "action_id": "cast:not-a-real-object",
                "reason": "Intentional invalid-action regression.",
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(len(session.commands), 0)
        self.assertEqual(len(session.decisions), 1)
        self.assertFalse(session.decisions[0]["accepted"])
        self.assertEqual(session.pending_principals()[0], principal)

    def test_inspect_and_migrate_synthetic_v2_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "synthetic-v2.json"
            source = make_session(
                self.db,
                self.mishra,
                self.zimone,
                players=2,
                seed=32,
                auto_pass_empty=False,
            )
            keep_all(source)
            source.state.game_over = True
            source.state.winner = "A"
            source.state.save(fixture)
            inspection = inspect_game(fixture)
            self.assertEqual(inspection["record_version"], 2)
            self.assertGreater(inspection["events"], 0)

            output = Path(temporary) / "migrated"
            manifest = migrate_v2_game(fixture, output, self.db)
            replay = replay_record(output, self.db, verify=True)
            self.assertTrue(replay["ok"])
            self.assertEqual(replay["mode"], "legacy_snapshot")
            self.assertEqual("complete", manifest["status"])


if __name__ == "__main__":
    unittest.main()
