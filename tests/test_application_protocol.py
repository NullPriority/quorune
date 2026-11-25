from __future__ import annotations

import base64
import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from common import keep_all, load_assets, make_session
from quorune import (
    CommandEnvelope,
    GameService,
    PROTOCOL_VERSION,
)
from quorune.record import authoritative_state_hash, replay_record


ROOT = Path(__file__).resolve().parents[1]


class ApplicationProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.command_schema = json.loads(
            (ROOT / "schemas" / "command-envelope.schema.json").read_text(
                encoding="utf-8"
            )
        )
        cls.packet_schema = json.loads(
            (ROOT / "schemas" / "decision-packet.schema.json").read_text(
                encoding="utf-8"
            )
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_service(self, seed: int = 31001):
        session = make_session(
            self.db, self.mishra, self.zimone, seed=seed
        )
        return session, GameService(session)

    def envelope(
        self,
        session,
        *,
        principal: str = "pilot:A",
        command_id: str = "cmd-1",
        action_id: str = "keep",
        choices: dict | None = None,
    ) -> CommandEnvelope:
        capability = session.engine.permissions.capability_for(principal)
        self.assertIsNotNone(capability)
        decision = session.state.pending_decision
        self.assertIsNotNone(decision)
        return CommandEnvelope(
            protocol_version=PROTOCOL_VERSION,
            game_id=session.state.game_id,
            command_id=command_id,
            decision_id=decision.decision_id,
            action_id=action_id,
            capability=capability.token,
            expected_view_revision=session.state.revision,
            choices=choices or {},
        )

    def test_command_mapping_is_strict_and_has_no_principal(self):
        session, _ = self.make_service()
        envelope = self.envelope(session)
        body = {
            "protocol_version": envelope.protocol_version,
            "game_id": envelope.game_id,
            "command_id": envelope.command_id,
            "decision_id": envelope.decision_id,
            "action_id": envelope.action_id,
            "capability": envelope.capability,
            "expected_view_revision": envelope.expected_view_revision,
            "choices": {},
        }
        parsed = CommandEnvelope.from_mapping(body)
        self.assertEqual(envelope, parsed)
        Draft202012Validator(self.command_schema).validate(body)

        with self.assertRaisesRegex(ValueError, "unknown field"):
            CommandEnvelope.from_mapping({**body, "principal": "pilot:B"})
        with self.assertRaisesRegex(ValueError, "unknown field"):
            CommandEnvelope.from_mapping({**body, "semantic_key": "forged"})
        with self.assertRaisesRegex(ValueError, "missing field"):
            CommandEnvelope.from_mapping(
                {key: value for key, value in body.items() if key != "command_id"}
            )

    def test_current_turn_pass_intent_rejects_stale_scope_and_unknown_activations(self):
        session, service = self.make_service(31017)
        keep_all(session)
        principal = session.pending_principals()[0]
        baseline = authoritative_state_hash(session.state)
        turn = session.state.turn_sequence
        for key, choices in (
            ("old-turn", {"pass_turn_sequence": turn - 1}),
            ("forged-ability", {"pass_turn_sequence": turn, "ignored_activation_ids": ["activate:missing:ab1"]}),
            ("missing-scope", {"ignored_activation_ids": []}),
        ):
            rejected = service.command(self.envelope(
                session, principal=principal, command_id=key, action_id="pass", choices=choices,
            ), principal=principal)
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(baseline, authoritative_state_hash(session.state))
        accepted = service.command(self.envelope(
            session, principal=principal, command_id="turn-pass", action_id="pass",
            choices={"pass_turn_sequence": turn},
        ), principal=principal)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(turn, session.commands[-1]["payload"]["pass_turn_sequence"])
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, session.card_db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(authoritative_state_hash(session.state), replay["final_state_hash"])

    def test_projected_packet_is_protocol_v3_strict_and_uses_full_game_id(self):
        session, service = self.make_service(31002)
        packet = service.observe("pilot:A", full=True)
        self.assertEqual(PROTOCOL_VERSION, packet["v"])
        self.assertEqual(session.state.game_id, packet["state"]["game"]["id"])
        self.assertEqual(session.state.revision, packet["view_revision"])
        Draft202012Validator(self.packet_schema).validate(packet)
        blade = self.db.lookup("Tithing Blade")
        definition = session.projector._definition(blade.oracle_id)
        self.assertEqual(
            ["Tithing Blade", "Consuming Sepulcher"],
            [face["n"] for face in definition["faces"]],
        )
        self.assertIn("beginning of your upkeep", definition["faces"][1]["o"])

    def test_projected_choice_form_and_command_fields_share_one_adapter(self):
        session, service = self.make_service(31009)
        packet = service.observe("pilot:A", full=True)
        mulligan = next(
            action
            for action in packet["decision"]["legal_actions"]
            if action["id"] == "mulligan"
        )
        self.assertEqual(1, mulligan["form"]["v"])
        self.assertEqual(
            "override_reason", mulligan["form"]["fields"][0]["name"]
        )

        result = service.command(
            self.envelope(
                session,
                command_id="form-mulligan-1",
                action_id="mulligan",
                choices={"override_reason": "Protocol form coverage"},
            ),
            principal="pilot:A",
        )
        self.assertTrue(result.ok, result.summary)

    def test_capability_uses_256_bits_of_randomness(self):
        session, _ = self.make_service(31003)
        token = session.engine.permissions.capability_for("pilot:A").token
        encoded = token.removeprefix("c_")
        padding = "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode(encoded + padding)
        self.assertEqual(32, len(raw))

    def test_stale_revision_rejects_without_consuming_capability(self):
        session, service = self.make_service(31004)
        envelope = self.envelope(session)
        envelope = replace(
            envelope,
            expected_view_revision=envelope.expected_view_revision + 1,
        )
        capability = session.engine.permissions.capability_for("pilot:A")
        before_revision = session.state.revision
        result = service.command(envelope, principal="pilot:A")
        self.assertFalse(result.ok)
        self.assertEqual("stale_view", result.code)
        self.assertEqual(before_revision, session.state.revision)
        self.assertFalse(capability.consumed)

    def test_action_choices_reject_server_derived_and_unknown_fields(self):
        session, service = self.make_service(31005)
        envelope = self.envelope(
            session,
            choices={"seat": "B", "semantic_key": "forged"},
        )
        capability = session.engine.permissions.capability_for("pilot:A")
        result = service.command(envelope, principal="pilot:A")
        self.assertFalse(result.ok)
        self.assertEqual("invalid_choices", result.code)
        self.assertFalse(capability.consumed)
        self.assertFalse(session.commands)

    def test_accepted_command_is_idempotent_and_audits_client_id(self):
        session, service = self.make_service(31006)
        envelope = self.envelope(session, command_id="browser-A-0001")
        first = service.command(envelope, principal="pilot:A")
        second = service.command(envelope, principal="pilot:A")
        self.assertTrue(first.ok)
        self.assertFalse(first.replayed)
        self.assertTrue(second.ok)
        self.assertTrue(second.replayed)
        self.assertEqual(first.state_revision, second.state_revision)
        self.assertEqual(1, len(session.commands))
        self.assertEqual(
            "browser-A-0001", session.commands[0]["client_command_id"]
        )
        self.assertNotIn(envelope.capability, json.dumps(session.commands))
        command_schema = json.loads(
            (ROOT / "schemas" / "game-record-v3-command.schema.json")
            .read_text(encoding="utf-8")
        )
        Draft202012Validator(command_schema).validate(session.commands[0])

    def test_reusing_command_id_for_another_request_conflicts(self):
        session, service = self.make_service(31007)
        envelope = self.envelope(session, command_id="same-id")
        accepted = service.command(envelope, principal="pilot:A")
        self.assertTrue(accepted.ok)
        changed = CommandEnvelope(
            protocol_version=envelope.protocol_version,
            game_id=envelope.game_id,
            command_id=envelope.command_id,
            decision_id=envelope.decision_id,
            action_id="mulligan",
            capability=envelope.capability,
            expected_view_revision=envelope.expected_view_revision,
            choices={},
        )
        conflict = service.command(changed, principal="pilot:A")
        self.assertFalse(conflict.ok)
        self.assertEqual("idempotency_conflict", conflict.code)
        self.assertEqual(1, len(session.commands))

    def test_wrong_principal_cannot_use_another_seat_capability(self):
        session, service = self.make_service(31008)
        envelope = self.envelope(session)
        result = service.command(envelope, principal="pilot:B")
        self.assertFalse(result.ok)
        self.assertEqual("unauthorized_capability", result.code)
        self.assertFalse(
            session.engine.permissions.capability_for("pilot:A").consumed
        )


if __name__ == "__main__":
    unittest.main()
