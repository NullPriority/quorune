"""Keep the chosen exiled spell's mana permission inside its cast."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorune.mana_restrictions import (
    ability_mana_spend_context,
    bind_exiled_spell_mana_spending,
    exiled_spell_any_type_payer,
    mana_restriction_allows,
    spell_mana_spend_context,
)
from quorune.rules.casting.spell_mana_payments import cast_mana_spend_context
from quorune.semantic_runtime.mana_spending import current_mana_spending_policy


class ExiledSpellManaSpendingTests(unittest.TestCase):
    def test_only_the_authorized_card_and_actor_bind_the_mana_permission(self):
        chosen = SimpleNamespace(zone="exile")
        other = SimpleNamespace(zone="exile")
        host = SimpleNamespace(_spell_mana_spend_context=spell_mana_spend_context)
        ordinary = spell_mana_spend_context("Artifact")
        with patch(
            "quorune.private_opponent_library_cast_model.has_private_opponent_library_cast_access",
            side_effect=lambda _host, *, actor, card: actor == "A" and card is chosen,
        ):
            bound = cast_mana_spend_context(host, "A", chosen, "Artifact")
            self.assertEqual("A", exiled_spell_any_type_payer(bound))
            self.assertEqual(ordinary, cast_mana_spend_context(host, "B", chosen, "Artifact"))
            self.assertEqual(ordinary, cast_mana_spend_context(host, "A", other, "Artifact"))
            chosen.zone = "stack"
            self.assertEqual(ordinary, cast_mana_spend_context(host, "A", chosen, "Artifact"))

        with patch("quorune.semantic_runtime.mana_spending.controller_action_permissions", return_value=()):
            policy = current_mana_spending_policy(host, "A", bound)
            self.assertTrue(policy.permits("G", "C"))
            self.assertFalse(policy.permits("G", "S"))
            self.assertIsNone(current_mana_spending_policy(host, "B", bound))
            self.assertIsNone(current_mana_spending_policy(host, "A", ordinary))

    def test_wrapping_preserves_spell_restrictions_and_rejects_an_ability(self):
        ordinary = spell_mana_spend_context("Artifact")
        self.assertEqual("mana-spend-v1;kind=spell;types=artifact;subtypes=;supertypes=", ordinary)
        bound = bind_exiled_spell_mana_spending(ordinary, payer="seat;A")
        self.assertEqual("seat;A", exiled_spell_any_type_payer(bound))
        self.assertTrue(mana_restriction_allows("artifact_spell_only", bound))
        self.assertFalse(mana_restriction_allows("creature_spell_only", bound))
        self.assertIsNone(exiled_spell_any_type_payer(bound + "unexpected"))
        self.assertFalse(mana_restriction_allows("artifact_spell_only", bound + "unexpected"))
        with self.assertRaises(ValueError):
            bind_exiled_spell_mana_spending(ability_mana_spend_context("Artifact"), payer="A")
