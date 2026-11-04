from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from common import keep_all, load_assets, make_session
import quorune.drawing.coordinator as coordinator
from quorune.drawing import (
    DrawPermission,
    QueuedDraw,
    validate_prepared_draw,
)
from quorune.record import checkpoint_envelope, replay_record
from quorune.replacement import (
    PreventDraw,
    ReplacementClass,
    ReplacementEffect,
)


class _PermissionSink:
    def __init__(self) -> None:
        self.last_issue: dict[str, object] | None = None
        self.decision: SimpleNamespace | None = None

    def issue(self, **kwargs):
        self.last_issue = dict(kwargs)
        self.decision = SimpleNamespace(
            actors=list(kwargs["actors"]),
            responses={},
            continuation=dict(kwargs["continuation"]),
        )


class _IterationHost:
    def __init__(self, library_size: int = 0) -> None:
        self.active_seats = ["A"]
        self.state = SimpleNamespace(
            game_id="draw-iteration",
            turn_sequence=1,
            event_sequence=0,
            # This isolated trampoline host has synthetic library entries,
            # without card definitions or intrinsic reveal declarations.
            cards={},
            players={
                "A": SimpleNamespace(
                    zones={"library": list(range(library_size))},
                    stats={},
                )
            },
        )
        self.permissions = _PermissionSink()
        self.committed_kinds: list[str] = []
        self.resumed: list[str] = []

    def _semantic_event_sources(self, *, zones=None):
        return []

    def _require_seat(self, seat: str, *, in_game: bool = False):
        if seat not in self.state.players:
            raise AssertionError(f"unknown test seat {seat}")
        return self.state.players[seat]

    def apnap_order(self) -> list[str]:
        return ["A"]

    def commit(self, supplied_host, prepared):
        if supplied_host is not self:
            raise AssertionError("draw commit changed coordinator host")
        validate_prepared_draw(prepared, apnap_order=self.apnap_order())
        resolution = prepared.resolution
        assert resolution is not None
        self.committed_kinds.append(resolution.kind)
        self.state.event_sequence += 1
        library = self.state.players[resolution.player].zones["library"]
        if resolution.kind == "draw" and library:
            library.pop()
        return SimpleNamespace(result_draws=())

    def _complete_draw_step_entry(self, active: str) -> None:
        self.resumed.append(f"turn_draw:{active}")

    def _continue_resolution(self, **kwargs) -> None:
        self.resumed.append(f"semantic:{kwargs['stack_ref']}")


class DrawCoordinatorIterationTests(unittest.TestCase):
    @staticmethod
    def _permission(_host, seat: str) -> DrawPermission:
        return DrawPermission(player=seat, drawn_this_turn=0)

    def _patch_coordinator(self, host, *, effects=()):
        effect_source = effects if callable(effects) else lambda *_: effects
        return (
            mock.patch.object(
                coordinator,
                "_instruction_replacement_effects",
                return_value=(),
            ),
            mock.patch.object(
                coordinator,
                "_draw_permission",
                side_effect=self._permission,
            ),
            mock.patch.object(
                coordinator,
                "_replacement_effects",
                side_effect=effect_source,
            ),
            mock.patch.object(
                coordinator,
                "commit_prepared_draw_result",
                side_effect=host.commit,
            ),
        )

    def _begin(self, host, count: int, *, effects=()) -> None:
        patches = self._patch_coordinator(host, effects=effects)
        with patches[0], patches[1], patches[2], patches[3]:
            coordinator.begin_draw_sequence(
                host,
                "A",
                count,
                reason="iterative draw fixture",
                private=True,
            )

    def test_zero_one_several_and_past_library_counts_are_iterative(self):
        for count, library_size, expected_remaining in (
            (0, 3, 3),
            (1, 3, 2),
            (7, 7, 0),
            (7, 3, 0),
        ):
            with self.subTest(count=count, library_size=library_size):
                host = _IterationHost(library_size)
                self._begin(host, count)
                self.assertEqual(count, len(host.committed_kinds))
                self.assertEqual(
                    expected_remaining,
                    len(host.state.players["A"].zones["library"]),
                )

    def test_two_thousand_replacement_free_draws_do_not_recurse(self):
        host = _IterationHost(2000)

        self._begin(host, 2000)

        self.assertEqual(2000, len(host.committed_kinds))
        self.assertEqual({"draw"}, set(host.committed_kinds))
        self.assertEqual([], host.state.players["A"].zones["library"])

    def test_two_thousand_prevented_draws_do_not_recurse(self):
        host = _IterationHost(0)
        effect = ReplacementEffect(
            effect_id="prevent:draw:large",
            source_id="fixture:prevent-large-draw",
            event_kind="draw",
            replacement_class=ReplacementClass.OTHER,
            conditions={"affected_player": {"eq": "A"}},
            operations=(PreventDraw(),),
        )

        self._begin(host, 2000, effects=(effect,))

        self.assertEqual(2000, len(host.committed_kinds))
        self.assertEqual({"prevented"}, set(host.committed_kinds))

    def test_large_sequence_suspends_and_resumes_from_remaining_count(self):
        host = _IterationHost(2000)
        effect = ReplacementEffect(
            effect_id="prevent:draw:mid-sequence",
            source_id="fixture:mid-sequence-choice",
            event_kind="draw",
            replacement_class=ReplacementClass.OTHER,
            conditions={"affected_player": {"eq": "A"}},
            operations=(PreventDraw(),),
            optional=True,
            label="Replace the midpoint draw",
        )

        def effects(current_host, _seat, _excluded_effect_ids=()):
            library = current_host.state.players["A"].zones["library"]
            return (effect,) if len(library) == 1500 else ()

        patches = self._patch_coordinator(host, effects=effects)
        with patches[0], patches[1], patches[2], patches[3]:
            coordinator.begin_draw_sequence(
                host,
                "A",
                2000,
                reason="large suspended instruction",
                private=True,
            )
            self.assertEqual(500, len(host.committed_kinds))
            self.assertIsNotNone(host.permissions.decision)
            assert host.permissions.decision is not None
            self.assertEqual(
                1500,
                host.permissions.decision.continuation["remaining_draws"],
            )

            host.permissions.decision.responses = {"A": {"choice": "draw"}}
            coordinator.complete_draw_replacement(
                host,
                host.permissions.decision,
            )

        self.assertEqual(2000, len(host.committed_kinds))
        self.assertEqual({"draw"}, set(host.committed_kinds))
        self.assertEqual([], host.state.players["A"].zones["library"])

    def test_large_zero_count_batch_is_trampolined(self):
        host = _IterationHost(0)
        draws = tuple(
            QueuedDraw(
                player="A",
                count=0,
                reason=f"queued instruction {index}",
            )
            for index in range(2000)
        )
        patches = self._patch_coordinator(host)

        with patches[0], patches[1], patches[2], patches[3]:
            coordinator.begin_draw_batch(host, draws)

        self.assertEqual([], host.committed_kinds)


class DrawCoordinatorReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_draw_past_library_records_each_empty_attempt(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=121207,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        library = engine.state.players["A"].zones["library"]
        for object_id in list(library[:-2]):
            engine.move_card(
                object_id,
                "exile",
                log=False,
                semantic_events=False,
            )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        hand_before = len(engine.state.players["A"].zones["hand"])

        engine._begin_draw_sequence(
            "A",
            5,
            reason="draw past library fixture",
        )

        self.assertEqual(
            hand_before + 2,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertTrue(engine.state.players["A"].attempted_empty_draw)
        self.assertEqual(
            3,
            sum(
                event.code == "card.draw.empty"
                for event in engine.state.events
            ),
        )

    def test_resumed_multi_draw_sequence_is_private_and_replays_exactly(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=121206,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Life from the Loam"
        )
        engine.move_card(
            source.object_id,
            "graveyard",
            log=False,
            semantic_events=False,
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._begin_draw_sequence(
            "B",
            32,
            reason="resumed replay instruction",
            private=True,
        )

        affected = session.packet("pilot:B", full=True)
        opponent = session.packet("pilot:A", full=True)
        self.assertEqual("draw.replacement", affected["decision"]["kind"])
        self.assertEqual(32, affected["decision"]["ctx"]["remaining_draws"])
        self.assertIsNone(opponent["decision"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        hand_before = len(engine.state.players["B"].zones["hand"])

        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "choice": source.ref,
                "reason": "Use the replacement, then resume every remaining draw.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertNotEqual(
            "draw.replacement",
            engine.state.pending_decision.kind,
        )
        self.assertEqual(
            hand_before + 32,
            len(engine.state.players["B"].zones["hand"]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "iterative-draw-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(1, replay["commands"])


if __name__ == "__main__":
    unittest.main()
