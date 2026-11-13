"""Tribute decisions belong to an opponent and one entering incarnation."""
from dataclasses import replace
import unittest

from quorune.tribute_model import TributeSpec,TributeEntryOutcome


class TributeModelTests(unittest.TestCase):
    def test_unpaid_entry_fact_cannot_transfer_to_a_later_incarnation_or_ability(self):
        spec=TributeSpec(3,'front:tribute:1')
        outcome=TributeEntryOutcome(spec,'entry:1','creature@1','A','C',False)
        self.assertTrue(outcome.is_unpaid_entry(link_id=spec.link_id,logical_object_id='creature@1'))
        self.assertFalse(outcome.is_unpaid_entry(link_id=spec.link_id,logical_object_id='creature@2'))
        self.assertFalse(outcome.is_unpaid_entry(link_id='front:tribute:2',logical_object_id='creature@1'))
        self.assertFalse(replace(outcome,paid=True).is_unpaid_entry(link_id=spec.link_id,logical_object_id='creature@1'))
        self.assertEqual(spec,TributeSpec.from_dict(spec.to_dict()))
        self.assertEqual(outcome,TributeEntryOutcome.from_dict(outcome.to_dict()))

    def test_saved_specifications_and_outcomes_reject_forged_types_and_self_choosers(self):
        spec=TributeSpec(2,'front:tribute:2')
        for fields in ({'amount':True},{'amount':0},{'amount':-1},{'amount':2.0},
                {'link_id':''},{'schema_version':True},{'schema_version':2},{'unknown':1}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):TributeSpec.from_dict({**spec.to_dict(),**fields})
        outcome=TributeEntryOutcome(spec,'entry:2','object@2','A','B',True)
        for fields in ({'paid':1},{'paid':None},{'paid':'false'},{'opponent':'A'},
                {'controller':''},{'logical_object_id':''},{'unknown':True}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):TributeEntryOutcome.from_dict({**outcome.to_dict(),**fields})
