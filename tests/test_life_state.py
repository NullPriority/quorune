from __future__ import annotations

from types import SimpleNamespace
import unittest

from quorune.life_state import (
    LifeChange,
    LifeStateError,
    commit_life_changes,
    pay_life_cost,
    plan_life_changes,
)
from quorune.model import PlayerState


class LifeStateTests(unittest.TestCase):
    @staticmethod
    def host():
        state = SimpleNamespace(
            players={
                "A": PlayerState("A", "A", life=20),
                "B": PlayerState("B", "B", life=40),
            },
            active_seats=lambda: ["A", "B"],
        )
        return SimpleNamespace(state=state)

    def test_gain_and_loss_share_one_typed_plan(self):
        host = self.host()
        plan = plan_life_changes(
            host,
            (
                LifeChange("A", 3),
                LifeChange("B", -5),
                LifeChange("A", 2),
            ),
        )

        self.assertEqual(20, host.state.players["A"].life)
        self.assertEqual(40, host.state.players["B"].life)
        transitions = commit_life_changes(host, plan)

        self.assertEqual(25, host.state.players["A"].life)
        self.assertEqual(35, host.state.players["B"].life)
        self.assertEqual(3, len(transitions))
        self.assertEqual(("A", "B"), plan.changed_players)

    def test_stale_life_plan_fails_before_mutation(self):
        host = self.host()
        plan = plan_life_changes(
            host,
            (LifeChange("A", 3), LifeChange("B", -5)),
        )
        host.state.players["B"].life = 39

        with self.assertRaisesRegex(
            LifeStateError, "stale|changed before commit"
        ):
            commit_life_changes(host, plan)
        self.assertEqual(20, host.state.players["A"].life)
        self.assertEqual(39, host.state.players["B"].life)

    def test_inactive_player_and_malformed_change_fail_closed(self):
        host = self.host()
        host.state.active_seats = lambda: ["A"]
        with self.assertRaisesRegex(LifeStateError, "not active"):
            plan_life_changes(host, (LifeChange("B", -1),))
        with self.assertRaisesRegex(LifeStateError, "integers"):
            LifeChange("A", 1.5)  # type: ignore[arg-type]
        with self.assertRaisesRegex(LifeStateError, "require a player"):
            LifeChange("", 1)

    def test_life_payment_cannot_exceed_available_life(self):
        host = self.host()
        transition = pay_life_cost(host, "A", 20)
        self.assertEqual(0, transition.after)

        before = host.state.players["B"].life
        with self.assertRaisesRegex(LifeStateError, "more life"):
            pay_life_cost(host, "B", 41)
        self.assertEqual(before, host.state.players["B"].life)

        with self.assertRaisesRegex(LifeStateError, "nonnegative"):
            pay_life_cost(host, "B", -1)


    def test_zero_life_payment_at_negative_zero_and_positive_totals_neither_changes_life_nor_records_loss(self):
        from quorune.life_state import prepare_life_payment,validate_life_payment,commit_life_payment
        for total in (-1,-9,0,20):
            with self.subTest(total=total):
                host=self.host();host.state.players['A'].life=total;events=[]
                host._record_turn_history=lambda *args,**kwargs:events.append((args,kwargs))
                try:
                    prepared=prepare_life_payment(host,'A',0)
                    validate_life_payment(host,prepared)
                    transition=commit_life_payment(host,prepared)
                except LifeStateError as error:self.fail('CR119.4b permits zero life payment: '+str(error))
                self.assertEqual((total,total,0),(transition.before,transition.after,transition.requested_delta))
                self.assertEqual((),prepared.plan.changed_players)
                self.assertEqual(total,host.state.players['A'].life);self.assertEqual([],events)

    def test_independently_prepared_zero_life_request_at_negative_total_validates_and_commits(self):
        from quorune.life_state import PreparedLifePayment,validate_life_payment,commit_life_payment
        host=self.host();host.state.players['A'].life=-1
        prepared=PreparedLifePayment('A',0,plan_life_changes(host,(LifeChange('A',0),)))
        try:
            validate_life_payment(host,prepared);transition=commit_life_payment(host,prepared)
        except LifeStateError as error:self.fail('A matching zero-cost plan must validate: '+str(error))
        self.assertEqual(-1,transition.after);self.assertEqual(-1,host.state.players['A'].life)

    def test_zero_life_request_still_rejects_unknown_inactive_stale_and_malformed_requests(self):
        from quorune.life_state import prepare_life_payment,validate_life_payment
        host=self.host();prepared=prepare_life_payment(host,'A',0)
        host.state.players['A'].life=19
        with self.assertRaisesRegex(LifeStateError,'stale|changed before commit'):validate_life_payment(host,prepared)
        self.assertEqual(19,host.state.players['A'].life)
        host=self.host();prepared=prepare_life_payment(host,'A',0);host.state.active_seats=lambda:['B']
        with self.assertRaisesRegex(LifeStateError,'not active'):prepare_life_payment(host,'A',0)
        with self.assertRaisesRegex(LifeStateError,'not active'):validate_life_payment(host,prepared)
        with self.assertRaisesRegex(LifeStateError,'not active'):prepare_life_payment(host,'UNKNOWN',0)
        for amount in (-1,True,False,0.0,'0'):
            with self.subTest(amount=amount),self.assertRaises(LifeStateError):prepare_life_payment(self.host(),'A',amount)

    def test_zero_life_request_does_not_accept_a_different_player_or_nonzero_delta_plan(self):
        from quorune.life_state import PreparedLifePayment,validate_life_payment
        host=self.host()
        for changes in ((LifeChange('B',0),),(LifeChange('A',-1),)):
            plan=plan_life_changes(host,changes)
            with self.subTest(changes=changes),self.assertRaisesRegex(LifeStateError,'does not match'):
                validate_life_payment(host,PreparedLifePayment('A',0,plan))
        self.assertEqual((20,40),(host.state.players['A'].life,host.state.players['B'].life))


if __name__ == "__main__":
    unittest.main()
