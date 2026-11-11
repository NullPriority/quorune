from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from common import keep_all, load_assets, make_session
from quorune.protection import ProtectionVerdict
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.target_protection import (
    TargetProtectionError,
    TargetProtectionSnapshot,
    TargetProtectionVerdict,
    target_protection_verdict,
)
from quorune.targets import TargetGroup


class TargetProtectionTests(unittest.TestCase):
    @staticmethod
    def snapshot(**overrides):
        values = {
            "acting_controller": "A",
            "protected_controller": "B",
            "target_keywords": frozenset(),
        }
        values.update(overrides)
        return TargetProtectionSnapshot(**values)

    def test_plain_hexproof_depends_on_current_controller(self):
        opponent = self.snapshot(target_keywords=frozenset({"hexproof"}))
        controller = self.snapshot(
            protected_controller="A",
            target_keywords=frozenset({"hexproof"}),
        )

        self.assertEqual(
            TargetProtectionVerdict.HEXPROOF,
            target_protection_verdict(opponent),
        )
        self.assertEqual(
            TargetProtectionVerdict.ALLOWED,
            target_protection_verdict(controller),
        )

    def test_targeting_restrictions_are_closed_and_cumulative(self):
        self.assertEqual(
            TargetProtectionVerdict.SHROUD,
            target_protection_verdict(
                self.snapshot(
                    protected_controller="A",
                    target_keywords=frozenset({"hexproof", "shroud"}),
                )
            ),
        )
        self.assertEqual(
            TargetProtectionVerdict.CONTROLLER_HEXPROOF_FROM_COLOR,
            target_protection_verdict(
                self.snapshot(
                    source_colors=frozenset({"U"}),
                    controller_hexproof_colors=frozenset({"B", "U"}),
                )
            ),
        )
        self.assertEqual(
            TargetProtectionVerdict.PROTECTION,
            target_protection_verdict(
                self.snapshot(protection_verdict=ProtectionVerdict.BLOCKED)
            ),
        )
        self.assertEqual(
            TargetProtectionVerdict.UNRESOLVED_PROTECTION,
            target_protection_verdict(
                self.snapshot(
                    protection_verdict=ProtectionVerdict.UNRESOLVED
                )
            ),
        )

    def test_plain_hexproof_holds_across_controller_keyword_grid(self):
        for protected_controller in ("A", "B"):
            for has_hexproof in (False, True):
                with self.subTest(
                    protected_controller=protected_controller,
                    has_hexproof=has_hexproof,
                ):
                    snapshot = self.snapshot(
                        protected_controller=protected_controller,
                        target_keywords=(
                            frozenset({"hexproof"})
                            if has_hexproof
                            else frozenset()
                        ),
                    )
                    expected = (
                        TargetProtectionVerdict.HEXPROOF
                        if has_hexproof and protected_controller != "A"
                        else TargetProtectionVerdict.ALLOWED
                    )
                    self.assertEqual(
                        expected,
                        target_protection_verdict(snapshot),
                    )
                    self.assertEqual(
                        expected,
                        target_protection_verdict(snapshot),
                    )

    def test_malformed_snapshot_fails_closed(self):
        for overrides, pattern in (
            ({"acting_controller": ""}, "acting_controller"),
            ({"target_is_player": 1}, "boolean"),
            ({"target_keywords": frozenset({"Hexproof"})}, "canonical"),
            ({"source_colors": frozenset({"u"})}, "canonical"),
            ({"target_keywords": ("hexproof",)}, "frozenset"),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(TargetProtectionError, pattern):
                    self.snapshot(**overrides)
        with self.assertRaisesRegex(TargetProtectionError, "Snapshot"):
            target_protection_verdict({})


class HexproofTargetingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
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

    @staticmethod
    def card(engine, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    @staticmethod
    def blue_hexproof_token(engine, controller: str, name: str):
        ref = engine.create_token(
            controller,
            name=name,
            characteristics={
                "type_line": "Token Creature — Test",
                "colors": ["U"],
                "power": "2",
                "toughness": "2",
                "keywords": ["Hexproof"],
            },
        )[0]
        return engine._resolve_object(
            controller,
            ref,
            zones={"battlefield"},
        )

    @classmethod
    def blue_hexproof_permanent(
        cls,
        engine,
        *,
        owner: str,
        controller: str,
    ):
        card = cls.card(engine, owner, "Emry, Lurker of the Loch")
        engine.move_card(card.object_id, "battlefield", controller=controller)
        card.temporary_keywords.append("Hexproof")
        return card

    @staticmethod
    def reb_action(session, reb_ref: str):
        return next(
            action
            for action in session.packet("pilot:A", full=True)["decision"][
                "legal_actions"
            ]
            if action.get("card") == reb_ref
        )

    def prepare_reb(self, session):
        engine = session.engine
        reb = self.card(engine, "A", "Red Elemental Blast")
        engine.move_card(reb.object_id, "hand")
        engine.state.players["A"].mana_pool["R"] = 1
        engine.state.priority_player = "A"
        engine._issue_priority("A")
        return reb, self.reb_action(session, reb.ref)

    def test_offer_command_rollback_privacy_and_replay_share_hexproof_legality(self):
        session = self.session(70211001)
        engine = session.engine
        opposing = self.blue_hexproof_permanent(
            engine,
            owner="C",
            controller="B",
        )
        controlled = self.blue_hexproof_permanent(
            engine,
            owner="A",
            controller="A",
        )
        _, action = self.prepare_reb(session)

        legal_refs = action["target_schema"]["legal_refs"]
        self.assertNotIn(opposing.ref, legal_refs)
        self.assertIn(controlled.ref, legal_refs)
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])
        self.assertIsNone(session.packet("pilot:C", full=True)["decision"])
        self.assertIsNone(session.packet("pilot:D", full=True)["decision"])
        session.initial_checkpoint = checkpoint_envelope(session.state)

        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["mode_2"],
                "targets": [opposing.ref],
                "pay": "manual",
                "payment": {"R": 1},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["mode_2"],
                "targets": [controlled.ref],
                "pay": "manual",
                "payment": {"R": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        stack_ref = engine.state.stack[-1].ref
        for _ in range(12):
            if not any(item.ref == stack_ref for item in engine.state.stack):
                break
            principal = session.pending_principals()[0]
            passed = session.act(principal, {"a": "pass"})
            self.assertTrue(passed.ok, passed.summary)

        self.assertEqual(
            "graveyard",
            engine.state.cards[controlled.object_id].zone,
        )
        self.assertEqual(
            "battlefield",
            engine.state.cards[opposing.object_id].zone,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "hexproof-targeting"
            session.save(output)
            replay = replay_record(output, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)

    def test_control_change_recomputes_offer_and_resolution_legality(self):
        session = self.session(70211002)
        engine = session.engine
        target = self.blue_hexproof_token(engine, "A", "Controlled Hexproof")
        _, action = self.prepare_reb(session)
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])

        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["mode_2"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"R": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        engine.change_control(target.object_id, "B", reason="test control change")
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual("battlefield", target.zone)
        self.assertEqual("B", target.controller)
        self.assertFalse(engine.state.stack)

    def test_hexproof_does_not_prohibit_nontarget_attachment_checks(self):
        session = self.session(70211003)
        engine = session.engine
        target = self.blue_hexproof_token(engine, "B", "Opposing Hexproof")
        source = self.card(engine, "A", "Red Elemental Blast")
        group = TargetGroup.from_mapping(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "types_all": ["creature"],
            }
        )
        row = next(
            row
            for row in engine._target_candidate_rows("A", group)
            if row["ref"] == target.ref
        )

        self.assertFalse(
            engine._target_row_matches(
                "A", group, row, source_ref=source.ref, as_target=True
            )
        )
        self.assertTrue(
            engine._target_row_matches(
                "A", group, row, source_ref=source.ref, as_target=False
            )
        )


if __name__ == "__main__":
    unittest.main()
