"""Pinned CR107.1/119.4b: current-payer half-life prices and distinct old formats."""
from dataclasses import FrozenInstanceError
import json, unittest
from unittest.mock import patch
from quorune.fractional_quantity_model import HalfQuantitySpec
from quorune.fractional_life_payment_model import FractionalLifePaymentSpec as Spec, ResolvedFractionalLifePayment as Bound
from quorune.compiler.fractional_life_payment_templates import fractional_life_payment_from_text, recover_half_life_price_from_line

ORIGINAL = ('Recover—Pay half your life, rounded up. (When another creature is put into your graveyard '
    'from the battlefield, you may pay half your life, rounded up. If you do, return this card from '
    'your graveyard to your hand. Otherwise, exile this card.)')


class FractionalLifePaymentModelTests(unittest.TestCase):
    def test_closed_immutable_price_round_trips_with_existing_explicit_upward_half_quantity(self):
        spec = Spec(); self.assertEqual(spec, Spec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        self.assertEqual(HalfQuantitySpec('up'), spec.quantity)
        with self.assertRaises(FrozenInstanceError):spec.schema_version = 2

    def test_odd_even_one_zero_and_negative_current_life_use_canonical_half_arithmetic(self):
        for current, amount in ((13, 7), (12, 6), (1, 1), (0, 0), (-1, 0), (-3, 0), (101, 51)):
            self.assertEqual(amount, Spec().amount(current))
            self.assertEqual(Bound(amount), Bound.from_dict(json.loads(json.dumps(Bound(amount).to_dict()))))
        for bad in (True, 13.0, '13', None):
            with self.assertRaises(ValueError):Spec().amount(bad)

    def test_unknown_origins_rounding_divisors_versions_and_context_selectors_reject(self):
        raw = Spec().to_dict()
        for change in ({'schema_version': True}, {'schema_version': 2}, {'kind': 'half_opponent_life'},
                {'actor': 'B'}, {'current_life': 13}, {'history': 'life_lost'}):
            with self.assertRaises(ValueError):Spec.from_dict(raw | change)
        for quantity in (HalfQuantitySpec('down'), None, 2):
            with self.assertRaises(ValueError):Spec(quantity)
        for amount in (True, -1, 1.5):
            with self.assertRaises(ValueError):Bound(amount)
        for version in (True, 2, 12, 14):
            with self.assertRaises(ValueError):Bound(0, version)

    def test_old_positive_fixed_life_and_fixed_mana_recover_codecs_keep_their_meanings(self):
        from quorune.fixed_effect_payment import FixedEffectPaymentSpec
        from quorune.recover_model import RecoverSpec
        from quorune.replacement.immutable import FrozenMap
        old = FixedEffectPaymentSpec('life', amount=7); self.assertEqual(old, FixedEffectPaymentSpec.from_dict(old.to_dict()))
        with self.assertRaises(ValueError):FixedEffectPaymentSpec('life', amount=0)
        with self.assertRaises(ValueError):FixedEffectPaymentSpec.from_dict(Bound(0).to_dict())
        with self.assertRaises(ValueError):RecoverSpec(Spec())
        mana = FixedEffectPaymentSpec('mana', requirements=FrozenMap({'GENERIC': 0, 'W': 0, 'U': 0, 'B': 1, 'R': 0, 'G': 0, 'C': 0}))
        self.assertEqual(RecoverSpec(mana), RecoverSpec.from_dict(RecoverSpec(mana).to_dict()))

    def test_fresh_downward_rounding_substitution_is_killed_after_positive_odd_price(self):
        self.assertEqual(7, Spec().amount(13))
        with patch.object(HalfQuantitySpec, 'amount', lambda spec, value: max(0, value) // 2):
            with self.assertRaises(AssertionError):self.assertEqual(7, Spec().amount(13))


class FractionalLifePaymentGrammarTests(unittest.TestCase):
    def test_complete_original_reminder_and_standalone_price_keep_exact_current_payer_role(self):
        self.assertEqual(Spec(), recover_half_life_price_from_line(ORIGINAL))
        self.assertEqual(Spec(), recover_half_life_price_from_line(ORIGINAL.split(' (')[0]))
        self.assertEqual(Spec(), fractional_life_payment_from_text('Pay half your life, rounded up'))

    def test_changed_lifecycle_rounding_owner_cost_and_material_siblings_remain_unrepresented(self):
        for text in (ORIGINAL.replace('rounded up', 'rounded down'), ORIGINAL.replace('another creature', 'a creature'),
                ORIGINAL.replace('your graveyard', 'an opponent\'s graveyard'), ORIGINAL + ' Draw a card.',
                ORIGINAL.replace('Otherwise, exile this card.', 'Otherwise, return this card to your hand.'),
                ORIGINAL.replace('half your life', 'half your starting life'), ORIGINAL.replace('Recover—', 'Recover ')):
            self.assertIsNone(recover_half_life_price_from_line(text), text)
        for text in ('Pay half that player\'s life, rounded up.', 'Pay 7 life.', 'Pay half your life, rounded up. Draw a card.'):
            self.assertIsNone(fractional_life_payment_from_text(text))


if __name__ == '__main__':unittest.main()
