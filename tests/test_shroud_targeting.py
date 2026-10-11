from __future__ import annotations

from dataclasses import replace
import hashlib
import tempfile
import unittest
from pathlib import Path

from common import keep_all, load_assets, make_session
from quorune.attachments import attach_objects, detach_object
from quorune.keyword_abilities import normalized_characteristic_keywords
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_protection import (
    TargetProtectionSnapshot,
    TargetProtectionVerdict,
    target_protection_verdict,
)
from quorune.targets import TargetGroup


class ShroudTargetProtectionTests(unittest.TestCase):
    def test_shroud_blocks_every_controller_and_multiple_instances_are_redundant(
        self,
    ):
        redundant = normalized_characteristic_keywords(
            {"keywords": ["Shroud", "SHROUD", "shroud"]}
        )
        self.assertEqual(frozenset({"shroud"}), redundant)
        for acting_controller in ("A", "B"):
            for protected_controller in ("A", "B"):
                for keywords in (
                    frozenset({"shroud"}),
                    frozenset({"hexproof", "shroud"}),
                    redundant,
                ):
                    with self.subTest(
                        acting_controller=acting_controller,
                        protected_controller=protected_controller,
                        keywords=keywords,
                    ):
                        self.assertEqual(
                            TargetProtectionVerdict.SHROUD,
                            target_protection_verdict(
                                TargetProtectionSnapshot(
                                    acting_controller=acting_controller,
                                    protected_controller=protected_controller,
                                    target_keywords=keywords,
                                )
                            ),
                        )


class ShroudCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def fixture(self, *, oracle_text: str, keywords=("Shroud",), type_line=None):
        base = self.db.lookup("Wight of the Reliquary")
        identity = hashlib.sha256(
            f"{oracle_text}\0{type_line}".encode("utf-8")
        ).hexdigest()[:16]
        return replace(
            base,
            oracle_id=f"fixture-shroud-{identity}",
            name="Fixture Shroud",
            oracle_text=oracle_text,
            keywords=keywords,
            type_line=type_line or "Creature — Test",
        )

    def test_shroud_compiles_source_spanned_and_capability_closed(self):
        capabilities = load_default_capability_registry()
        for text, type_line, expected in (
            (
                "Shroud",
                "Creature — Test",
                {"target.protection.shroud_permanent"},
            ),
            (
                "Flying, shroud",
                "Creature — Test",
                {
                    "combat.block.flying",
                    "target.protection.shroud_permanent",
                },
            ),
            (
                "Target creature gains shroud until end of turn.",
                "Instant",
                {
                    "continuous.resolution.fixed_characteristics_until_end_of_turn",
                    "target.protection.shroud_permanent",
                    "target.revalidate_resolution",
                },
            ),
        ):
            with self.subTest(text=text):
                record = self.fixture(
                    oracle_text=text,
                    type_line=type_line,
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )

                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual(0, len(ir.material_residuals))
                self.assertEqual(1, len(ir.faces[0].nodes))
                node = ir.faces[0].nodes[0]
                self.assertEqual(
                    text,
                    record.oracle_text[node.span.start : node.span.end],
                )
                self.assertTrue(
                    expected.issubset(set(node.capability_dependencies)),
                    node.capability_dependencies,
                )

    def test_player_and_nonkeyword_shroud_wording_remain_residual(self):
        capabilities = load_default_capability_registry()
        for text, type_line, keywords in (
            ("You have shroud as long as you control an Island.", "Creature — Test", ("Shroud",)),
            (
                "This creature can't be the target of spells or abilities.",
                "Creature — Test",
                (),
            ),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    self.fixture(
                        oracle_text=text,
                        keywords=keywords,
                        type_line=type_line,
                    ),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)


class ShroudTargetingIntegrationTests(unittest.TestCase):
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
    def blue_token(engine, controller: str, name: str, *, shroud: bool):
        ref = engine.create_token(
            controller,
            name=name,
            characteristics={
                "type_line": "Token Creature — Test",
                "colors": ["U"],
                "power": "2",
                "toughness": "2",
                "keywords": ["Shroud"] if shroud else [],
            },
        )[0]
        return engine._resolve_object(
            controller,
            ref,
            zones={"battlefield"},
        )

    def prepare_reb(self, session):
        engine = session.engine
        reb = self.card(engine, "A", "Red Elemental Blast")
        engine.move_card(reb.object_id, "hand")
        engine.state.players["A"].mana_pool["R"] = 1
        engine.state.priority_player = "A"
        engine._issue_priority("A")
        action = next(
            action
            for action in session.packet("pilot:A", full=True)["decision"][
                "legal_actions"
            ]
            if action.get("card") == reb.ref
        )
        return reb, action

    @staticmethod
    def resolve_stack(session) -> None:
        for _ in range(16):
            if not session.engine.state.stack:
                return
            principal = session.pending_principals()[0]
            passed = session.act(principal, {"a": "pass"})
            if not passed.ok:
                raise AssertionError(passed.summary)
        raise AssertionError("stack did not resolve")

    def test_offer_command_privacy_rollback_and_replay_share_shroud_legality(
        self,
    ):
        session = self.session(70218001)
        engine = session.engine
        controlled_shroud = self.blue_token(
            engine, "A", "Controlled Shroud", shroud=True
        )
        opposing_shroud = self.blue_token(
            engine, "B", "Opposing Shroud", shroud=True
        )
        legal_target = self.card(engine, "A", "Emry, Lurker of the Loch")
        engine.move_card(
            legal_target.object_id,
            "battlefield",
            controller="A",
        )
        _, action = self.prepare_reb(session)

        legal_refs = action["target_schema"]["legal_refs"]
        self.assertNotIn(controlled_shroud.ref, legal_refs)
        self.assertNotIn(opposing_shroud.ref, legal_refs)
        self.assertIn(legal_target.ref, legal_refs)
        for seat in ("B", "C", "D"):
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])

        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        before = authoritative_state_hash(session.state)
        for illegal_ref in (controlled_shroud.ref, opposing_shroud.ref):
            rejected = session.act(
                "pilot:A",
                {
                    "action_id": action["id"],
                    "modes": ["destroy"],
                    "targets": [illegal_ref],
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
                "modes": ["destroy"],
                "targets": [legal_target.ref],
                "pay": "manual",
                "payment": {"R": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_stack(session)
        self.assertEqual(
            "graveyard",
            engine.state.cards[legal_target.object_id].zone,
        )
        self.assertEqual(
            "battlefield",
            engine.state.cards[controlled_shroud.object_id].zone,
        )
        self.assertEqual(
            "battlefield",
            engine.state.cards[opposing_shroud.object_id].zone,
        )

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shroud-targeting"
            session.save(output)
            replay = replay_record(output, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_shroud_gained_after_selection_is_illegal_at_resolution(self):
        session = self.session(70218002)
        engine = session.engine
        target = self.card(engine, "A", "Emry, Lurker of the Loch")
        engine.move_card(target.object_id, "battlefield", controller="A")
        _, action = self.prepare_reb(session)
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["destroy"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"R": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)

        engine.state.cards[target.object_id].temporary_keywords.append("Shroud")
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual(
            "battlefield",
            engine.state.cards[target.object_id].zone,
        )
        self.assertFalse(engine.state.stack)

    def test_attached_continuous_shroud_updates_advertised_target_legality(self):
        session = self.session(70218004)
        engine = session.engine
        greaves = self.card(engine, "A", "Lightning Greaves")
        target = self.card(engine, "A", "Emry, Lurker of the Loch")
        for card in (greaves, target):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
            )
        attach_objects(
            engine.state.cards,
            greaves,
            target,
            source_timestamp=engine._next_zone_timestamp(),
        )
        legal_target = self.blue_token(
            engine,
            "B",
            "Unprotected Blue Witness",
            shroud=False,
        )

        self.assertIn("Shroud", engine._effective_card_data(target)["keywords"])
        _, protected_action = self.prepare_reb(session)
        self.assertNotIn(
            target.ref,
            protected_action["target_schema"]["legal_refs"],
        )
        self.assertIn(
            legal_target.ref,
            protected_action["target_schema"]["legal_refs"],
        )

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        detach_object(engine.state.cards, greaves)

        self.assertNotIn("Shroud", engine._effective_card_data(target)["keywords"])
        _, unprotected_action = self.prepare_reb(session)
        self.assertIn(
            target.ref,
            unprotected_action["target_schema"]["legal_refs"],
        )

    def test_shroud_does_not_prohibit_nontarget_attachment_checks(self):
        session = self.session(70218003)
        engine = session.engine
        target = self.blue_token(engine, "B", "Attachable Shroud", shroud=True)
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
