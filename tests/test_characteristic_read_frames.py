"""Characteristic memoization is a pure read frame, never a state revision cache."""

import unittest
import gc
import weakref
from types import SimpleNamespace
from unittest.mock import patch

from quorune.characteristic_read_frames import characteristic_read_frame, memoized_characteristic_read


class CharacteristicReadFrameTests(unittest.TestCase):
    def test_priority_catalog_reuses_only_its_current_pure_read_and_ends_before_mutation(self):
        import quorune.rules.action_catalog as catalog
        host=SimpleNamespace(state=SimpleNamespace());value={'power':1};calls=[]
        def read():calls.append(value['power']);return dict(value)
        def offers(*_args):
            first=memoized_characteristic_read(host,'current-characteristic',read)
            second=memoized_characteristic_read(host,'current-characteristic',read)
            self.assertEqual(first,second);return [],[],[]
        def abilities(*_args):offers();return [],[],[],[]
        with patch.object(catalog,'_cast_offers',side_effect=offers),patch.object(catalog,'_land_offers',return_value=([],[])),\
                patch.object(catalog,'_ability_offers',side_effect=abilities),patch.object(catalog,'_turn_face_up_offers',return_value=([],[])),\
                patch.object(catalog,'_suspend_offers',return_value=([],[])),patch.object(catalog,'_staged_cast_offers',return_value=([],[])),\
                patch.object(catalog,'priority_actions_with_mana_undo',return_value=[]),patch.object(catalog,'_concede_offer',return_value={'id':'concede'}):
            expected=catalog.build_priority_action_catalog.__wrapped__(host,'A');self.assertEqual([1,1,1,1],calls)
            calls.clear();self.assertEqual(expected,catalog.build_priority_action_catalog(host,'A'));self.assertEqual([1],calls)
            value['power']=4;catalog.build_priority_action_catalog(host,'A');self.assertEqual([1,4],calls)

    def test_prospective_identity_cannot_be_reused_inside_a_frame_and_is_released_afterward(self):
        class Prospective:
            pass
        host=object()
        with characteristic_read_frame(host):
            prospective=Prospective();reference=weakref.ref(prospective)
            key=('card',id(prospective))
            memoized_characteristic_read(host,key,lambda:{'controller':'A'},identity=prospective)
            del prospective;gc.collect()
            self.assertIsNotNone(reference())
            other=Prospective()
            self.assertNotEqual(key,('card',id(other)))
        gc.collect();self.assertIsNone(reference())

    def test_only_completed_nested_reads_share_results_and_return_independent_values(self):
        host=object();calls=[]
        def read():
            calls.append('read');return {'keywords':['flying']}
        with characteristic_read_frame(host):
            first=memoized_characteristic_read(host,('card',1),read)
            first['keywords'].append('haste')
            second=memoized_characteristic_read(host,('card',1),read)
            self.assertEqual(['flying'],second['keywords'])
            second['keywords'].clear()
            self.assertEqual(['flying'],memoized_characteristic_read(host,('card',1),read)['keywords'])
        self.assertEqual(['read'],calls)

    def test_top_level_reads_recompute_after_mutation_and_hosts_never_share_frames(self):
        first=object();second=object();state={'power':1};calls=[]
        def read():calls.append(state['power']);return dict(state)
        self.assertEqual(1,memoized_characteristic_read(first,'same',read)['power'])
        state['power']=4
        self.assertEqual(4,memoized_characteristic_read(first,'same',read)['power'])
        with characteristic_read_frame(first):
            self.assertEqual(4,memoized_characteristic_read(first,'same',read)['power'])
            with characteristic_read_frame(second):
                self.assertEqual(9,memoized_characteristic_read(second,'same',lambda:{'power':9})['power'])
            self.assertEqual(4,memoized_characteristic_read(first,'same',lambda:self.fail('Outer host frame lost'))['power'])
        self.assertEqual([1,4,4],calls)

    def test_failed_and_reentrant_reads_do_not_publish_provisional_values(self):
        host=object();calls=[]
        def outer():
            calls.append('outer')
            inner=memoized_characteristic_read(host,'recursive',lambda:{'value':'inner'})
            self.assertEqual('inner',inner['value']);return {'value':'complete'}
        with characteristic_read_frame(host):
            self.assertEqual('complete',memoized_characteristic_read(host,'recursive',outer)['value'])
            self.assertEqual('complete',memoized_characteristic_read(host,'recursive',lambda:self.fail('Completed value lost'))['value'])
            with self.assertRaisesRegex(ValueError,'failed'):
                memoized_characteristic_read(host,'failure',lambda:(_ for _ in ()).throw(ValueError('failed')))
            self.assertEqual({'value':'recovered'},memoized_characteristic_read(host,'failure',lambda:{'value':'recovered'}))
        self.assertEqual(['outer'],calls)
        self.assertEqual({'value':'new-root'},memoized_characteristic_read(host,'recursive',lambda:{'value':'new-root'}))

    def test_layer_flags_and_prospective_object_identities_are_independent_keys(self):
        host=object();live=object();prospective=object()
        with characteristic_read_frame(host):
            keys=(('card',id(live),False,False,5,True),('card',id(live),False,False,None,True),
                  ('card',id(live),True,False,5,True),('card',id(live),False,True,5,True),
                  ('card',id(live),False,False,5,False),('card',id(prospective),False,False,5,True))
            for index,key in enumerate(keys):self.assertEqual(index,memoized_characteristic_read(host,key,lambda value=index:value))
            for index,key in enumerate(keys):self.assertEqual(index,memoized_characteristic_read(host,key,lambda:self.fail('Distinct read key lost')))


if __name__=='__main__':unittest.main()
