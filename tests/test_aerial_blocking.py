from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from common import keep_all, load_assets, make_session
from quorune.aerial_blocking import (
    AerialBlockVerdict,
    aerial_block_verdict,
)
from quorune.keyword_abilities import (
    EffectiveKeywordError,
    normalized_characteristic_keywords,
    normalized_effective_keywords,
)
from quorune.model import CombatState
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)


class _Host:
    @staticmethod
    def _effective_card_data(card, *, ignore_face_down=False):
        return card.effective


def _card(*keywords: object):
    return SimpleNamespace(effective={"keywords": keywords})


def _keywords(host: _Host, *keywords: object) -> frozenset[str]:
    return normalized_effective_keywords(host, _card(*keywords))


class AerialBlockingTests(unittest.TestCase):
    def test_flying_attacker_requires_flying_or_reach_blocker(self):
        host = _Host()

        self.assertEqual(
            AerialBlockVerdict(False, "attacker_has_flying"),
            aerial_block_verdict(
                _keywords(host, "Flying"), _keywords(host)
            ),
        )
        self.assertTrue(
            aerial_block_verdict(
                _keywords(host, "Flying"),
                _keywords(host, "Flying"),
            ).allowed
        )
        self.assertTrue(
            aerial_block_verdict(
                _keywords(host, "Flying"),
                _keywords(host, "Reach"),
            ).allowed
        )

    def test_ground_attacker_is_outside_aerial_restriction(self):
        host = _Host()

        self.assertTrue(
            aerial_block_verdict(_keywords(host), _keywords(host)).allowed
        )
        self.assertTrue(
            aerial_block_verdict(
                _keywords(host), _keywords(host, "Reach")
            ).allowed
        )

    def test_current_keywords_are_case_insensitive_and_redundant(self):
        host = _Host()

        self.assertTrue(
            aerial_block_verdict(
                _keywords(host, "flying", "FLYING"),
                _keywords(host, "reach", "REACH"),
            ).allowed
        )

    def test_malformed_keyword_snapshot_fails_closed(self):
        host = _Host()

        with self.assertRaisesRegex(EffectiveKeywordError, "keywords"):
            normalized_effective_keywords(host, _card("Flying", 1))
        with self.assertRaisesRegex(EffectiveKeywordError, "mapping"):
            normalized_effective_keywords(
                host,
                SimpleNamespace(effective=None),
            )
        with self.assertRaisesRegex(EffectiveKeywordError, "keywords"):
            normalized_characteristic_keywords({"keywords": ["Flying", 7]})
        with self.assertRaisesRegex(ValueError, "attacker"):
            aerial_block_verdict({"flying"}, frozenset())

    def test_verdict_shape_is_closed(self):
        with self.assertRaisesRegex(ValueError, "boolean"):
            AerialBlockVerdict(1)
        with self.assertRaisesRegex(ValueError, "rejection"):
            AerialBlockVerdict(True, "attacker_has_flying")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            AerialBlockVerdict(False, "other")


class AerialBlockingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int):
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
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def token(engine, controller: str, name: str, *, keywords=()):
        ref = engine.create_token(
            controller,
            name=name,
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
            temporary_keywords=tuple(keywords),
        )[0]
        return engine._resolve_object(
            controller,
            ref,
            zones={"battlefield"},
        )

    def test_current_flying_and_reach_share_offer_and_command_legality(self):
        session = self.make_session(70209017)
        engine = session.engine
        attacker = self.token(
            engine, "A", "Effect-Granted Flyer", keywords=("Flying",)
        )
        reach = self.token(
            engine, "B", "Effect-Granted Reach", keywords=("Reach",)
        )
        ground = self.token(engine, "B", "Ground Blocker")
        attacker.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            attackers={attacker.object_id: "B"},
            defending_players=["B"],
        )
        engine._begin_blocker_decisions()

        decision = session.packet("pilot:B", full=True)["decision"]
        self.assertEqual([attacker.ref], decision["ctx"]["legal_blocks"][reach.ref])
        self.assertNotIn(ground.ref, decision["ctx"]["legal_blocks"])
        self.assertIsNone(session.packet("pilot:C", full=True)["decision"])
        self.assertIsNone(session.packet("pilot:D", full=True)["decision"])
        session.initial_checkpoint = checkpoint_envelope(session.state)

        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:B",
            {"a": "block", "blk": {ground.ref: attacker.ref}},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

        accepted = session.act(
            "pilot:B",
            {"a": "block", "blk": {reach.ref: attacker.ref}},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        current_reach = engine._resolve_object(
            "B", reach.ref, zones={"battlefield"}
        )
        self.assertEqual(attacker.object_id, current_reach.blocking)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "aerial-block"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(1, replay["commands"])


if __name__ == "__main__":
    unittest.main()
