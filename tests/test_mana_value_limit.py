"""Pinned CR202.3 comparisons consume characteristics rather than costs paid."""

from dataclasses import replace
import unittest

from quorune.mana_value_limit import ManaValueLimitSpec


class ManaValueLimitTests(unittest.TestCase):
    def test_limit_is_inclusive_and_zero_can_match_a_zero_mana_value(self):
        limit=ManaValueLimitSpec(3)
        for value in (0,1,2,3,3.0):self.assertTrue(limit.permits(value))
        for value in (3.5,4,20):self.assertFalse(limit.permits(value))
        self.assertTrue(ManaValueLimitSpec(0).permits(0))
        self.assertFalse(ManaValueLimitSpec(0).permits(1))
        self.assertFalse(limit.permits(10**1000))

    def test_limit_and_wire_schema_are_closed_and_boolean_values_reject(self):
        limit=ManaValueLimitSpec(3)
        self.assertEqual(limit,ManaValueLimitSpec.from_dict(limit.to_dict()))
        for bad in (True,False,-1,3.0,'3',None):
            with self.subTest(maximum=bad),self.assertRaises(ValueError):ManaValueLimitSpec(bad)
        for value in ({'maximum':3},{'schema_version':1,'maximum':3,'extra':0},
                      {'schema_version':True,'maximum':3}):
            with self.subTest(value=value),self.assertRaises(ValueError):ManaValueLimitSpec.from_dict(value)
        with self.assertRaises(ValueError):replace(limit,schema_version=2)

    def test_invalid_characteristic_values_do_not_become_zero_or_a_boolean_integer(self):
        for value in (None,True,False,-1,'3',float('nan'),float('inf')):
            with self.subTest(value=value),self.assertRaises(ValueError):ManaValueLimitSpec(3).permits(value)

    def test_wire_values_do_not_alias_the_immutable_limit(self):
        limit=ManaValueLimitSpec(3);wire=limit.to_dict();wire['maximum']=100
        self.assertEqual(3,limit.maximum);self.assertFalse(limit.permits(4))
