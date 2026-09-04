from __future__ import annotations

import unittest

from common import DB_PATH
from high_risk_interaction_support import (
    ALL_HIGH_RISK_BOUNDARY_PAIRS,
    CAST_COST_MODIFIER_AND_DAMAGE_PREVENTION_PAIR,
    CONTINUOUS_LAYER_AND_REGENERATION_RESIDUAL_PAIR,
    DESTROY_DAMAGE_PREVENTION_PAIR,
    DESTROY_REGENERATION_PAIR,
    FIXED_SET_DAMAGE_AND_REGENERATION_PAIRS,
    FIXED_SET_DAMAGE_AND_REPLACEMENT_ORDERING_PAIRS,
    FIXED_SELF_ENTRY_AND_REPLACEMENT_PAIRS,
    IMPULSE_ACCESS_AND_CHOICE_PAIRS,
    PUBLIC_SET_AND_CHOICE_PAIRS,
    REGENERATION_PROHIBITION_AND_CONTINUOUS_PAIRS,
    REGENERATION_PROHIBITION_AND_REPLACEMENT_PAIRS,
    SAGA_CHAPTER_HIGH_RISK_BOUNDARY_PAIRS,
    TAP_STATE_HIGH_RISK_BOUNDARY_PAIRS,
    TOKEN_AND_DAMAGE_PREVENTION_PAIR,
    TYPED_ATTACHMENT_AND_CONTINUOUS_PAIRS,
    assert_high_risk_boundary_pairs,
)
from quorune.carddb import CardDatabase


class HighRiskInteractionAssuranceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db = CardDatabase(DB_PATH)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def test_all_declared_residual_pairs_fail_closed_at_runtime_boundary(
        self,
    ) -> None:
        self.assertEqual(142, len(ALL_HIGH_RISK_BOUNDARY_PAIRS))
        assert_high_risk_boundary_pairs(
            self,
            ALL_HIGH_RISK_BOUNDARY_PAIRS,
            database=self.db,
        )

    def test_cost_modifier_with_residual_prevention_fails_closed(
        self,
    ) -> None:
        assert_high_risk_boundary_pairs(
            self,
            (CAST_COST_MODIFIER_AND_DAMAGE_PREVENTION_PAIR,),
            database=self.db,
        )

    def test_saga_chapter_residual_pairs_fail_closed_at_runtime_boundary(
        self,
    ) -> None:
        self.assertEqual(20, len(SAGA_CHAPTER_HIGH_RISK_BOUNDARY_PAIRS))
        assert_high_risk_boundary_pairs(
            self,
            SAGA_CHAPTER_HIGH_RISK_BOUNDARY_PAIRS,
            database=self.db,
        )

    def test_destination_replacement_multiple_targets_pair_fails_closed(
        self,
    ) -> None:
        assert_high_risk_boundary_pairs(
            self,
            (
                (
                    "capability.zone.change.destination_replacement",
                    "residual.target_or_choice.multiple-targets",
                ),
            ),
            database=self.db,
        )

    def test_fixed_self_entry_residual_pairs_fail_closed_at_runtime_boundary(
        self,
    ) -> None:
        self.assertEqual(3, len(FIXED_SELF_ENTRY_AND_REPLACEMENT_PAIRS))
        assert_high_risk_boundary_pairs(
            self,
            FIXED_SELF_ENTRY_AND_REPLACEMENT_PAIRS,
            database=self.db,
        )

    def test_public_set_residual_pairs_fail_closed_at_runtime_boundary(
        self,
    ) -> None:
        self.assertEqual(2, len(PUBLIC_SET_AND_CHOICE_PAIRS))
        assert_high_risk_boundary_pairs(
            self,
            PUBLIC_SET_AND_CHOICE_PAIRS,
            database=self.db,
        )

    def test_typed_enchant_residual_pairs_fail_closed_at_runtime_boundary(
        self,
    ) -> None:
        self.assertEqual(6, len(TYPED_ATTACHMENT_AND_CONTINUOUS_PAIRS))
        assert_high_risk_boundary_pairs(
            self,
            TYPED_ATTACHMENT_AND_CONTINUOUS_PAIRS,
            database=self.db,
        )

    def test_tap_state_residual_pairs_fail_closed_at_runtime_boundary(
        self,
    ) -> None:
        self.assertEqual(7, len(TAP_STATE_HIGH_RISK_BOUNDARY_PAIRS))
        assert_high_risk_boundary_pairs(
            self,
            TAP_STATE_HIGH_RISK_BOUNDARY_PAIRS,
            database=self.db,
        )

    def test_compiled_destruction_with_unresolved_damage_prevention_fails_closed(
        self,
    ) -> None:
        assert_high_risk_boundary_pairs(
            self,
            (DESTROY_DAMAGE_PREVENTION_PAIR,),
            database=self.db,
        )

    def test_fixed_token_with_damage_prevention_residual_fails_closed_at_runtime_boundary(
        self,
    ) -> None:
        assert_high_risk_boundary_pairs(
            self,
            (TOKEN_AND_DAMAGE_PREVENTION_PAIR,),
            database=self.db,
        )

    def test_impulse_access_residual_choice_pairs_fail_closed_at_runtime_boundary(
        self,
    ) -> None:
        self.assertEqual(3, len(IMPULSE_ACCESS_AND_CHOICE_PAIRS))
        assert_high_risk_boundary_pairs(
            self,
            IMPULSE_ACCESS_AND_CHOICE_PAIRS,
            database=self.db,
        )

    def test_compiled_destruction_with_regeneration_residual_fails_closed(
        self,
    ) -> None:
        assert_high_risk_boundary_pairs(
            self,
            (DESTROY_REGENERATION_PAIR,),
            database=self.db,
        )

    def test_regeneration_prohibition_residual_pairs_fail_closed_at_runtime_boundary(
        self,
    ) -> None:
        self.assertEqual(
            2,
            len(REGENERATION_PROHIBITION_AND_REPLACEMENT_PAIRS),
        )
        assert_high_risk_boundary_pairs(
            self,
            REGENERATION_PROHIBITION_AND_REPLACEMENT_PAIRS,
            database=self.db,
        )

    def test_regeneration_prohibition_continuous_residual_pairs_fail_closed_at_runtime_boundary(
        self,
    ) -> None:
        self.assertEqual(
            2,
            len(REGENERATION_PROHIBITION_AND_CONTINUOUS_PAIRS),
        )
        assert_high_risk_boundary_pairs(
            self,
            REGENERATION_PROHIBITION_AND_CONTINUOUS_PAIRS,
            database=self.db,
        )

    def test_continuous_layer_regeneration_residual_pair_fails_closed_at_runtime_boundary(
        self,
    ) -> None:
        assert_high_risk_boundary_pairs(
            self,
            (CONTINUOUS_LAYER_AND_REGENERATION_RESIDUAL_PAIR,),
            database=self.db,
        )

    def test_fixed_set_damage_with_regeneration_residual_fails_closed(
        self,
    ) -> None:
        self.assertEqual(4, len(FIXED_SET_DAMAGE_AND_REGENERATION_PAIRS))
        assert_high_risk_boundary_pairs(
            self,
            FIXED_SET_DAMAGE_AND_REGENERATION_PAIRS,
            database=self.db,
        )

    def test_fixed_set_damage_with_entry_replacement_residuals_fails_closed(
        self,
    ) -> None:
        self.assertEqual(
            2,
            len(FIXED_SET_DAMAGE_AND_REPLACEMENT_ORDERING_PAIRS),
        )
        assert_high_risk_boundary_pairs(
            self,
            FIXED_SET_DAMAGE_AND_REPLACEMENT_ORDERING_PAIRS,
            database=self.db,
        )


if __name__ == "__main__":
    unittest.main()
