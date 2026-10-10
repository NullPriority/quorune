from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin
from quorune.scalar_effect_amounts import resolve_scalar_effect_amount,scalar_source_context
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class SourceCounterAmountCompilerTests(unittest.TestCase):
    def test_source_counter_amounts_preserve_typed_identity_and_span(self):
        for text in ('When this creature dies, draw a card for each +1/+1 counter on it.',
                     'When this creature leaves the battlefield, you gain life equal to the number of age counters on it.',
                     '{1}, Sacrifice this artifact: Draw a card for each charge counter on this artifact.'):
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertEqual((0,len(text)),(node.span.start,node.span.end))
            self.assertIn('quantity_expression.scalar_effect_amount',node.capability_dependencies)
            self.assertIn('counter_name',str(node.effects))

    def test_v2_counter_codec_keeps_v1_shape_and_rejects_ambiguous_origins(self):
        old=ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,characteristic='power').to_dict()
        self.assertNotIn('counter_name',old);self.assertEqual(old,ScalarEffectAmountSpec.from_dict(old).to_dict())
        spec=ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,counter_name='charge',schema_version=2)
        self.assertEqual(spec,ScalarEffectAmountSpec.from_dict(spec.to_dict()))
        for changes in ({'schema_version':True},{'schema_version':1},{'counter_name':'Charge'},{'counter_name':''},
                        {'origin':'target_characteristic'},{'characteristic':'power'},{'extra':1}):
            with self.assertRaises(ValueError):ScalarEffectAmountSpec.from_dict({**spec.to_dict(),**changes})
        text='When another creature dies, draw a card for each +1/+1 counter on it.'
        self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)


class SourceCounterAmountRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'counter-amount.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/repeatable-object-action-triggers.json',
            ROOT/'tests/fixtures/source-counter-amounts.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Counter quantities',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def test_actual_death_draw_uses_predeparture_counters_and_replays(self):
        session=self.session(305001);engine=session.engine
        walker=self.add(engine,'Marketback Walker');walker.counters['+1/+1']=3
        feeder=self.add(engine,'Carrion Feeder')
        action=self.ready(session,feeder,{});self.checkpoint(session)
        hand=len(session.state.players['A'].zones['hand']);before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id'],'cost_cards':[walker.ref]});self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[walker.ref]});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual({},session.state.cards[walker.object_id].counters)
        self.resolve(session);self.assertEqual(hand+3,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_actual_sacrifice_cost_preserves_original_counter_count(self):
        session=self.session(305002);engine=session.engine
        source=self.add(engine,'Culling Dais');source.counters['charge']=4
        self.ready(session,source,{'C':1});self.checkpoint(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a['id'].startswith('activate:'+source.ref+':') and 'ab2' in a['id'])
        hand=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual({},session.state.cards[source.object_id].counters)
        self.resolve(session);self.assertEqual(hand+4,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_missing_counter_lki_mutant_is_killed_by_actual_source_sacrifice(self):
        session=self.session(305003);engine=session.engine
        walker=self.add(engine,'Marketback Walker');walker.counters['+1/+1']=3;feeder=self.add(engine,'Carrion Feeder')
        action=self.ready(session,feeder,{});hand=len(session.state.players['A'].zones['hand'])
        original=scalar_source_context
        def omit(host,source,effects,**kwargs):
            value=original(host,source,effects,**kwargs)
            if value:value['scalar_reference_snapshots']['source']['counters']={}
            return value
        with patch('quorune.scalar_effect_amounts.scalar_source_context',side_effect=omit):
            result=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[walker.ref]});self.assertTrue(result.ok,result.summary)
            self.resolve(session)
            with self.assertRaises(AssertionError):self.assertEqual(hand+3,len(session.state.players['A'].zones['hand']))

    def test_actual_live_counter_changes_are_read_at_resolution_then_blink_uses_lki(self):
        for blink_source in (False,True):
            session=self.session(305004 if blink_source else 305005);engine=session.engine
            source=self.add(engine,'Generic Source Counter Reader');source.counters['charge']=2
            response=self.add(engine,'Generic Source Counter Add',zone='hand')
            blink=self.add(engine,'Cloudshift',zone='hand')
            self.ready(session,source,{'W':2});self.checkpoint(session)
            action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('source')==source.ref)
            before=len(session.state.players['A'].zones['hand'])
            accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            chosen=blink if blink_source else response
            action=next(a for a in actions if a.get('card')==chosen.ref)
            accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
            self.resolve(session)
            self.assertEqual(before-1+(2 if blink_source else 5),len(session.state.players['A'].zones['hand']))
            self.assertEqual(0 if blink_source else 5,session.state.cards[source.object_id].counters.get('charge',0))
            self.replay(session,load=True)

    def test_zero_and_missing_counter_snapshots_are_distinct(self):
        from test_scalar_effect_amounts import ScalarEffectAmountValueTests
        host,item,card,data,_=ScalarEffectAmountValueTests().host_and_item()
        spec=ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,counter_name='charge',schema_version=2)
        item.context.update(scalar_source_context(host,card,({'op':'draw','count':spec.to_dict()},)))
        self.assertEqual(0,resolve_scalar_effect_amount(host,spec.to_dict(),item))
        card.zone='graveyard';card.logical_object_id='new-object'
        item.context['scalar_reference_snapshots']['source'].pop('counters')
        with self.assertRaises(ValueError):resolve_scalar_effect_amount(host,spec.to_dict(),item)

    def test_actual_maga_entry_and_pending_target_read_replaced_current_count(self):
        session=self.session(305006);engine=session.engine
        maga=self.add(engine,'Maga, Traitor to Mortals',zone='hand')
        self.add(engine,'Hardened Scales')
        action=self.ready(session,maga,{'B':3,'C':8});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'x':3,'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(24):
            if session.state.pending_decision and session.state.pending_decision.kind=='semantic.target':break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(4,session.state.cards[maga.object_id].counters['+1/+1'])
        self.assertEqual('semantic.target',session.state.pending_decision.kind)
        resumed=self.replay(session,load=True)
        accepted=resumed.act('pilot:A',{'action_id':'choose','targets':['B']});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(resumed)
        self.assertEqual(36,resumed.state.players['B'].life);self.replay(resumed)


if __name__=='__main__':unittest.main()
