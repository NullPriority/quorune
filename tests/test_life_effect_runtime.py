from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

from quorune.effect_runtime import life_effects
from quorune.errors import GameRuleError
from quorune.replacement import (
    MultiplyAmount,
    ReplacementClass,
    ReplacementEffect,
)


class _State:
    def __init__(self, seats: tuple[str, ...]) -> None:
        self._seats = seats
        self.revision = 0
        self.event_sequence = 0
        self.players = {
            seat: SimpleNamespace(life=40)
            for seat in seats
            if seat != "missing"
        }

    def active_seats(self) -> tuple[str, ...]:
        return self._seats


class _Host:
    def __init__(self, seats: tuple[str, ...] = ("A", "B", "C")) -> None:
        self.state = _State(seats)
        self.active_seats = list(seats)
        self.logs: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.semantic_events: list[
            tuple[str, dict[str, object], dict[str, object]]
        ] = []

    def _log(self, *args, **kwargs) -> None:
        self.logs.append((args, kwargs))

    def apnap_order(self, *, start: str | None = None) -> list[str]:
        seats = list(self.active_seats)
        if start is None or start not in seats:
            return seats
        index = seats.index(start)
        return [*seats[index:], *seats[:index]]

    def _semantic_event_sources(self, *, zones=None) -> list[object]:
        return []

    def _dispatch_semantic_event(
        self,
        event: str,
        context: dict[str, object],
        **kwargs,
    ) -> None:
        self.semantic_events.append((event, dict(context), dict(kwargs)))

    def semantic_program_is_current_trusted(self, program: object) -> bool:
        return False


class LifeEffectRuntimeTests(unittest.TestCase):
    @staticmethod
    def apply(host: _Host, effect: dict[str, object], actor: str = "A"):
        operation = str(effect["op"])
        return life_effects.apply_effect(
            host,
            effect,
            actor=actor,
            operation=operation,
            reason="test life effect",
        )

    def test_life_and_loss_commit_through_the_typed_batch_owner(self):
        host = _Host()

        self.assertEqual(
            43,
            self.apply(host, {"op": "life", "player": "A", "delta": 3}),
        )
        self.assertEqual(
            35,
            self.apply(
                host,
                {"op": "lose_life", "player": "B", "amount": 5},
            ),
        )
        self.assertEqual(43, host.state.players["A"].life)
        self.assertEqual(35, host.state.players["B"].life)
        self.assertEqual(
            [("life.gained", {"player": "A", "amount": 3})],
            [
                (event, context)
                for event, context, _kwargs in host.semantic_events
            ],
        )

    def test_table_wide_loss_and_drain_are_simultaneous_typed_batches(self):
        host = _Host()

        self.assertEqual(
            2,
            self.apply(host, {"op": "lose_life_each_opponent", "amount": 2}),
        )
        self.assertEqual((40, 38, 38), self._life(host))

        self.assertEqual(
            1,
            self.apply(host, {"op": "drain_each_opponent", "amount": 1}),
        )
        self.assertEqual((41, 37, 37), self._life(host))
        self.assertEqual(
            [("life.gained", {"player": "A", "amount": 1})],
            [
                (event, context)
                for event, context, _kwargs in host.semantic_events
            ],
        )

    def test_invalid_batch_member_rolls_back_every_life_change(self):
        host = _Host(("A", "B", "missing"))

        with self.assertRaises(GameRuleError):
            self.apply(host, {"op": "lose_life_each_opponent", "amount": 2})

        self.assertEqual(40, host.state.players["B"].life)

    def test_all_player_loss_uses_one_batch_and_rejects_contradictory_scope(self):
        host = _Host()
        host.state.players["D"] = SimpleNamespace(life=40)
        with mock.patch.object(life_effects, "_commit", wraps=life_effects._commit) as commit:
            self.assertEqual(3, self.apply(host, {
                "op": "lose_life", "scope": "all_active_players", "amount": 3,
            }, actor="C"))
        self.assertEqual(1, commit.call_count)
        self.assertEqual(["A", "B", "C"], [change.player for change in commit.call_args.args[1]])
        self.assertEqual((37, 37, 37), self._life(host))
        self.assertEqual(40, host.state.players["D"].life)
        details = host.logs[-1][0][3]
        self.assertEqual(["A", "B", "C"], details["players"])
        self.assertEqual({"A", "B", "C"}, {event["player"] for event in details["life_events"]})
        for additions in (
            {"scope": "some_players"}, {"scope": None}, {"player": "A"},
            {"amount": True}, {"amount": -1}, {"amount": 1.5},
        ):
            with self.subTest(additions=additions):
                before = tuple(player.life for player in host.state.players.values())
                logs = len(host.logs)
                with self.assertRaises(GameRuleError):
                    self.apply(host, {"op": "lose_life", "scope": "all_active_players", "amount": 2, **additions})
                self.assertEqual(before, tuple(player.life for player in host.state.players.values()))
                self.assertEqual(logs, len(host.logs))

    def test_drain_log_reports_final_replacement_adjusted_values(self):
        host = _Host()
        effects = (
            ReplacementEffect(
                effect_id="zero-b-loss",
                source_id="source:zero-b-loss",
                event_kind="life.change",
                replacement_class=ReplacementClass.OTHER,
                conditions={
                    "affected_player": {"eq": "B"},
                    "direction": {"eq": "loss"},
                },
                operations=(MultiplyAmount(field="amount", factor=0),),
            ),
            ReplacementEffect(
                effect_id="double-a-gain",
                source_id="source:double-a-gain",
                event_kind="life.change",
                replacement_class=ReplacementClass.OTHER,
                conditions={
                    "affected_player": {"eq": "A"},
                    "direction": {"eq": "gain"},
                },
                operations=(MultiplyAmount(field="amount", factor=2),),
            ),
        )

        with mock.patch.object(
            life_effects,
            "collect_life_change_replacement_effects",
            return_value=effects,
        ):
            self.apply(host, {"op": "drain_each_opponent", "amount": 1})

        self.assertEqual((42, 40, 39), self._life(host))
        args, kwargs = host.logs[-1]
        self.assertEqual(
            "B lost 0 life; C lost 1 life; A gained 2 life.",
            args[2],
        )
        details = args[3]
        self.assertEqual(2, details["gained_amount"])
        self.assertEqual({"B": 0, "C": -1, "A": 2}, details["deltas"])
        self.assertEqual(["A", "C"], list(kwargs["changed_players"]))
        self.assertEqual(
            [("life.gained", {"player": "A", "amount": 2})],
            [
                (event, context)
                for event, context, _kwargs in host.semantic_events
            ],
        )

    def test_family_rejects_an_operation_it_does_not_own(self):
        host = _Host()

        with self.assertRaisesRegex(GameRuleError, "Unsupported owned effect"):
            life_effects.apply_effect(
                host,
                {"op": "damage"},
                actor="A",
                operation="damage",
                reason="wrong family",
            )

    @staticmethod
    def _life(host: _Host) -> tuple[int, ...]:
        return tuple(host.state.players[seat].life for seat in ("A", "B", "C"))


if __name__ == "__main__":
    unittest.main()
