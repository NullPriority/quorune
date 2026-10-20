from __future__ import annotations

"""Resolved query amounts may be zero without weakening source validation."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.target_characteristic_sets import decode_target_characteristics
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as witnesses


class ResolvedTargetCharacteristicZeroCompilerTests(unittest.TestCase):
    def test_zero_resolution_optin_keeps_compiled_and_malformed_instruction_contracts(self):
        effect={'op':'apply_source_characteristics_until_end_of_turn','schema_version':6,'cards':[],
            'maximum_targets':1,'power':0,'toughness':0,'keywords':[]}
        with self.assertRaises(ValueError):decode_target_characteristics(effect)
        self.assertEqual((),decode_target_characteristics(effect,allow_zero_result=True))
        for changed in ({'power':False},{'toughness':None},{'maximum_targets':0},{'maximum_targets':7},
                {'schema_version':True},{'keywords':['UnknownAbility']},{'unknown':True}):
            with self.assertRaises((ValueError,TypeError)):
                decode_target_characteristics({**effect,**changed},allow_zero_result=True)


class ResolvedTargetCharacteristicZeroRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'resolved-zero-sets.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/target-characteristic-sets.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Resolved zero target sets',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def assert_admitted(self,session,source):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.preflight import card_semantic_status
        record=self.db.lookup(source.printed_name)
        program=compile_best_available_card_program(self.db,record,semantic_registry=session.engine.semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        self.assertEqual('fully_playable',card_semantic_status(record,session.engine.semantics,db=self.db,
            capability_registry=self.registry,capability_profile='commander_review')['status'])

    def private_replay(self,session,private):
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.assertIn(private.ref,json.dumps(session.packet('pilot:C',full=True)))
        return self.replay(session,load=True)

    def test_zero_and_nonzero_query_results_resolve_and_remain_locked_after_actual_land_play(self):
        for count in (0,2):
            session=self.session(730601+count);spell=self.add(session.engine,'Generic Scalar Target Group',zone='hand')
            target=self.add(session.engine,'Generic Bound Body',seat='B')
            for index in range(count):self.add(session.engine,'Forest',ref='resolved-forest-'+str(index))
            later=self.add(session.engine,'Forest',zone='hand',ref='later-forest')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            action=self.ready(session,spell,{'W':1});self.checkpoint(session);self.assert_admitted(session,spell)
            before=authoritative_state_hash(session.state)
            bad=session.act('pilot:B',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
            self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual('graveyard',session.state.cards[spell.object_id].zone);self.assertFalse(session.state.stack)
            expected=(2+count,6+count)
            self.assertEqual(expected,(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            land=next(row for row in actions if row.get('card')==later.ref)
            played=session.act('pilot:A',{'action_id':land['id']});self.assertTrue(played.ok,played.summary)
            self.assertEqual(expected,(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
            self.private_replay(session,private)

    def test_zero_weakening_and_keyword_only_results_preserve_their_complete_consequences(self):
        for name,count,expected,flying in (('Generic Scalar Target Weakening',0,(2,6),False),
                ('Generic Scalar Target Weakening',2,(0,4),False),('Generic Zero Stat Flying Set',0,(2,6),True)):
            session=self.session(730610+count+int(flying));spell=self.add(session.engine,name,zone='hand')
            target=self.add(session.engine,'Generic Bound Body',seat='B')
            for index in range(count):self.add(session.engine,'Forest',ref='weakening-forest-'+str(index))
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            action=self.ready(session,spell,{'W':1,'U':1});self.checkpoint(session);self.assert_admitted(session,spell)
            accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual(expected,(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
            self.assertEqual(flying,'flying' in session.engine._combat_keywords(session.state.cards[target.object_id]))
            self.private_replay(session,private)

    def test_zero_result_valid_without_targets_differs_from_all_chosen_targets_becoming_illegal(self):
        for chosen in (False,True):
            session=self.session(730620+int(chosen));spell=self.add(session.engine,'Generic Scalar Target Group',zone='hand')
            target=self.add(session.engine,'Generic Bound Body',seat='B')
            bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            session.state.players['B'].mana_pool.update({'U':1})
            action=self.ready(session,spell,{'W':1});self.checkpoint(session);self.assert_admitted(session,spell)
            accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref] if chosen else [],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            if chosen:
                for _ in range(8):
                    if session.state.priority_player=='B':break
                    self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                offered=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
                response=next(row for row in offered if row.get('card')==bounce.ref)
                result=session.act('pilot:B',{'action_id':response['id'],'targets':[target.ref],'pay':'auto'})
                self.assertTrue(result.ok,result.summary)
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual('hand' if chosen else 'battlefield',session.state.cards[target.object_id].zone)
            self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            self.private_replay(session,private)

    def test_fresh_query_witness_kills_restored_runtime_empty_result_rejection(self):
        original=decode_target_characteristics
        def legacy(effect,**kwargs):return original(effect)
        method='test_zero_and_nonzero_query_results_resolve_and_remain_locked_after_actual_land_play'
        with patch('quorune.rules.target_characteristic_sets.decode_target_characteristics',legacy):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_fresh_query_witness_kills_omitted_nonzero_target_characteristic_application(self):
        method='test_zero_and_nonzero_query_results_resolve_and_remain_locked_after_actual_land_play'
        with patch('quorune.continuous_effect_state.apply_target_characteristics',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


if __name__=='__main__':unittest.main()
