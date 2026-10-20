"""CR107.3a/601.2b/601.2h: one declared X for additional prices and results."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.object_predicate import ObjectQuerySpec
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from test_granted_skulk import record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost
from scripts.build_test_database import build_fixture_database
import test_scalar_mill_wording as controls


class AnnouncedAdditionalXModelTests(unittest.TestCase):
    def test_declared_life_descriptor_rejects_unknown_fields_and_parameters(self):
        from quorune.rules.casting_additional_cost_groups import AnnouncedXLifeAdditionalCost
        cost=AnnouncedXLifeAdditionalCost()
        self.assertEqual(cost,AnnouncedXLifeAdditionalCost.from_descriptor(cost.to_descriptor()))
        self.assertEqual(0,cost.amount_for(None));self.assertEqual(0,cost.amount_for(0))
        self.assertEqual(40,cost.amount_for(40))
        for changes in ({'schema_version':True},{'schema_version':2},{'schema_version':1.0},
                {'kind':'fixed_life_payment'},{'parameter':'mana_spent'},{'parameter':True},
                {'amount':0},{'future':True}):
            with self.assertRaises(ValueError):
                AnnouncedXLifeAdditionalCost.from_descriptor({**cost.to_descriptor(),**changes})
        for value in (True,-1,1.5,'2'):
            with self.assertRaises(ValueError):cost.amount_for(value)

    def test_dependent_exile_form_retains_the_server_declared_x_link(self):
        from quorune.choice_forms import build_action_form
        action={'id':'cast:TEST','action':'cast','cost_options':[{'id':'normal','label':'Cast',
            'choice_schema':{'x':{'type':'integer','minimum':0,'maximum':3},
                'exile_cards':{'type':'object_ref_array','count':0,'count_from':'x','legal_refs':['C1','C2','C3']}}}]}
        form=build_action_form(action,decision_kind='priority',context={})
        field=next(f for f in form['variants']['options'][0]['fields'] if f['name']=='exile_cards')
        self.assertEqual('x',field['count_from']);self.assertEqual('refs',field['control'])

    def test_new_cost_capabilities_withdraw_to_residual_without_losing_result_scope(self):
        for cap,text in (('casting.additional_cost.announced_x_life','As an additional cost to cast this spell, pay X life.\nTarget creature gets +X/+0 until end of turn.'),
                ('casting.additional_cost.announced_x_graveyard_exile','As an additional cost to cast this spell, exile X cards from your graveyard.\nYou draw X cards and you lose X life.')):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn declared-X additional cost'])
            ir=compile_oracle_card(record(text,'Instant'),capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)

    def test_declared_graveyard_count_has_no_guessed_fixed_count_and_keeps_legacy_shape(self):
        predicate=ObjectQuerySpec(zones=('graveyard',),owner='$actor',known_to_actor=True)
        cost=FixedZoneChangeAdditionalCost(operation='exile_one_from_graveyard',
            choice_field='exile_cards',predicate=predicate,schema_version=3,count=None,count_parameter='x')
        self.assertNotIn('count',cost.to_descriptor())
        self.assertEqual('x',cost.to_descriptor()['count_parameter'])
        self.assertEqual(cost,FixedZoneChangeAdditionalCost.from_descriptor(cost.to_descriptor()))
        self.assertEqual(0,cost.required_count(0));self.assertEqual(2,cost.required_count(2))
        for value in (True,-1,1.5,'2'):
            with self.assertRaises(ValueError):cost.required_count(value)
        fixed=FixedZoneChangeAdditionalCost(operation='exile_one_from_graveyard',choice_field='exile_cards',predicate=predicate)
        self.assertNotIn('count_parameter',fixed.to_descriptor());self.assertEqual(1,fixed.required_count(8))
        for changes in ({'count_parameter':'mana_spent'},{'count':2},{'schema_version':1},
                {'operation':'sacrifice_one'},{'future':True}):
            with self.assertRaises(ValueError):FixedZoneChangeAdditionalCost.from_descriptor({**cost.to_descriptor(),**changes})


class AnnouncedAdditionalXRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ScalarMillWordingRuntimeTests.session
    add=controls.ScalarMillWordingRuntimeTests.add
    prepare=controls.ScalarMillWordingRuntimeTests.prepare
    checkpoint=controls.ScalarMillWordingRuntimeTests.checkpoint
    cast=controls.ScalarMillWordingRuntimeTests.cast
    resolve=controls.ScalarMillWordingRuntimeTests.resolve
    replay=controls.ScalarMillWordingRuntimeTests.replay
    until=controls.ScalarMillWordingRuntimeTests.until
    binding=controls.ScalarMillWordingRuntimeTests.binding

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'additional-x.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/announced-x-additional-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Additional X',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def actions(self,session):return session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']

    def action(self,session,source):return next(r for r in self.actions(session) if r['id']=='cast:'+source.ref)

    def private_replay(self,session,private):
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.assertIn(private.ref,json.dumps(session.packet('pilot:C',full=True)))
        return self.replay(session,load=True)

    def test_original_hatred_pays_declared_life_before_resolution_and_uses_same_x(self):
        session=self.session(72202001);source=self.add(session.engine,'Hatred',zone='hand')
        body=self.add(session.engine,'Generic Bound Body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':2,'C':3});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source);before=authoritative_state_hash(session.state)
        command={'action_id':action['id'],'targets':[body.ref],'x':2,'pay':'auto'}
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(38,session.state.players['A'].life)
        self.assertEqual(2,session.engine._numeric_stat(body.object_id,'power'))
        self.assertEqual(2,session.state.stack[-1].x_value)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(4,session.engine._numeric_stat(body.object_id,'power'))
        self.assertEqual(38,session.state.players['A'].life);self.private_replay(session,private)

    def test_original_scrying_exiles_exact_declared_cards_before_draw_and_life_loss(self):
        session=self.session(72202002);source=self.add(session.engine,'Skeletal Scrying',zone='hand')
        cards=[self.add(session.engine,'Generic Bound Body',zone='graveyard',ref='scrying-cost-'+str(i)) for i in range(2)]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':1,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source);before_hand=len(session.state.players['A'].zones['hand'])
        result=session.act('pilot:A',{'action_id':action['id'],'x':2,'exile_cards':[c.ref for c in cards],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' for c in cards))
        self.assertEqual(40,session.state.players['A'].life)
        self.assertEqual(before_hand-1,len(session.state.players['A'].zones['hand']))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(38,session.state.players['A'].life)
        self.assertEqual(before_hand+1,len(session.state.players['A'].zones['hand']))
        self.private_replay(session,private)

    def test_original_chill_announces_x_without_mana_x_and_filters_creature_payment(self):
        session=self.session(72202003);source=self.add(session.engine,'Chill Haunting',zone='hand')
        body=self.add(session.engine,'Generic Bound Body')
        cards=[self.add(session.engine,'Generic Bound Body',zone='graveyard',ref='chill-cost-'+str(i)) for i in range(2)]
        invalid=self.add(session.engine,'Generic Bound Growth',zone='graveyard',ref='noncreature-cost')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':1,'C':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source);before=authoritative_state_hash(session.state)
        bad=session.act('pilot:A',{'action_id':action['id'],'x':2,'targets':[body.ref],
            'exile_cards':[cards[0].ref,invalid.ref],'pay':'auto'})
        self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id'],'x':2,'targets':[body.ref],
            'exile_cards':[c.ref for c in cards],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(2,session.state.stack[-1].x_value)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(0,session.engine._numeric_stat(body.object_id,'power'))
        self.assertEqual(4,session.engine._numeric_stat(body.object_id,'toughness'))
        self.private_replay(session,private)

    def test_omitting_declared_life_cost_is_killed_by_a_fresh_native_hatred(self):
        method='test_original_hatred_pays_declared_life_before_resolution_and_uses_same_x'
        with patch('quorune.rules.casting.commit._pay_fixed_life_additional_cost',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_omitting_declared_exile_cost_is_killed_by_a_fresh_native_scrying(self):
        method='test_original_scrying_exiles_exact_declared_cards_before_draw_and_life_loss'
        with patch('quorune.rules.casting.commit._fixed_zone_change_commit_entries',return_value=[]):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_executable_declared_cost_rejects_undeclared_resource_authority(self):
        for index,name,cap in ((0,'Hatred','casting.additional_cost.announced_x_life'),
                (1,'Skeletal Scrying','casting.additional_cost.announced_x_graveyard_exile')):
            session=self.session(72202090+index);source=self.add(session.engine,name,zone='hand')
            program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
            self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
            dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
            missing=replace(program,capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict() if dependencies else None)
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_original_zero_x_costs_need_no_life_or_graveyard_objects(self):
        for index,name,mana in ((0,'Hatred',{'B':2,'C':3}),(1,'Skeletal Scrying',{'B':1}),
                (2,'Chill Haunting',{'B':1,'C':1})):
            session=self.session(72202010+index);source=self.add(session.engine,name,zone='hand')
            body=self.add(session.engine,'Generic Bound Body')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana=mana);self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            command={'action_id':self.action(session,source)['id'],'x':0,'pay':'auto'}
            if name!='Skeletal Scrying':command['targets']=[body.ref]
            result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
            self.assertEqual(0,session.state.stack[-1].x_value)
            self.assertEqual(40,session.state.players['A'].life)
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual(40,session.state.players['A'].life)
            self.assertEqual(2,session.engine._numeric_stat(body.object_id,'power'))
            self.assertFalse(session.state.players['A'].zones['exile'])
            self.private_replay(session,private)

    def test_actual_scrying_rejects_bad_x_and_short_duplicate_foreign_or_source_payments(self):
        session=self.session(72202004);source=self.add(session.engine,'Skeletal Scrying',zone='hand')
        cards=[self.add(session.engine,'Generic Bound Body',zone='graveyard',ref='bounded-cost-'+str(i)) for i in range(2)]
        foreign=self.add(session.engine,'Generic Bound Body',seat='B',zone='graveyard',ref='foreign-cost')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':1,'C':3});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source)
        for x,refs in ((-1,[c.ref for c in cards]),(True,[cards[0].ref]),(1.5,[cards[0].ref]),
                ('2',[c.ref for c in cards]),(3,[c.ref for c in cards]),(2,[]),(2,[cards[0].ref]),
                (2,[cards[0].ref,cards[0].ref]),(2,[cards[0].ref,foreign.ref]),(2,[cards[0].ref,source.ref])):
            before=authoritative_state_hash(session.state)
            result=session.act('pilot:A',{'action_id':action['id'],'x':x,'exile_cards':refs,'pay':'auto'})
            self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertEqual(40,session.state.players['A'].life)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in cards))
        self.private_replay(session,private)

    def test_countering_original_hatred_does_not_refund_declared_life(self):
        session=self.session(72202005);source=self.add(session.engine,'Hatred',zone='hand')
        body=self.add(session.engine,'Generic Bound Body')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':2})
        self.prepare(session,mana={'B':2,'C':3});self.checkpoint(session)
        for card in (source,counter):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        result=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'x':2,'targets':[body.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(38,session.state.players['A'].life)
        self.cast(session,'B',counter,[session.state.stack[-1].ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(38,session.state.players['A'].life)
        self.assertEqual(2,session.engine._numeric_stat(body.object_id,'power'))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_countering_original_scrying_keeps_paid_exile_and_skips_results(self):
        session=self.session(72202006);source=self.add(session.engine,'Skeletal Scrying',zone='hand')
        cards=[self.add(session.engine,'Generic Bound Body',zone='graveyard',ref='countered-cost-'+str(i)) for i in range(2)]
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':2})
        self.prepare(session,mana={'B':1,'C':2});self.checkpoint(session)
        for card in (source,counter):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        before_hand=len(session.state.players['A'].zones['hand'])
        result=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'x':2,
            'exile_cards':[c.ref for c in cards],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',counter,[session.state.stack[-1].ref]);session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' for c in cards))
        self.assertEqual(40,session.state.players['A'].life)
        self.assertEqual(before_hand-1,len(session.state.players['A'].zones['hand']))
        self.private_replay(session,private)

    def test_mana_reduction_changes_price_without_changing_paid_or_resolved_x(self):
        session=self.session(72202007);source=self.add(session.engine,'Skeletal Scrying',zone='hand')
        reducer=self.add(session.engine,'Jet Medallion')
        cards=[self.add(session.engine,'Generic Bound Body',zone='graveyard',ref='reduced-cost-'+str(i)) for i in range(3)]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':1,'C':2});self.checkpoint(session)
        for card in (source,reducer):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        result=session.act('pilot:A',{'action_id':self.action(session,source)['id'],'x':3,
            'exile_cards':[c.ref for c in cards],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual(3,session.state.stack[-1].x_value)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' for c in cards))
        self.assertIsNone(self.resolve(session));self.assertEqual(37,session.state.players['A'].life)
        self.private_replay(session,private)

    def test_original_target_departure_keeps_paid_life_and_graveyard_exile(self):
        for name in ('Hatred','Chill Haunting'):
            session=self.session(72202009);source=self.add(session.engine,name,zone='hand')
            body=self.add(session.engine,'Generic Bound Body',seat='D')
            murder=self.add(session.engine,'Murder',seat='B',zone='hand')
            cards=[self.add(session.engine,'Generic Bound Body',zone='graveyard',ref='departed-target-cost-'+str(i)) for i in range(2)]
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            session.state.players['B'].mana_pool.update({'B':2,'C':1})
            self.prepare(session,mana={'B':2,'C':3} if name=='Hatred' else {'B':1,'C':1})
            self.checkpoint(session)
            for card in (source,murder):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
            command={'action_id':self.action(session,source)['id'],'x':2,'targets':[body.ref],'pay':'auto'}
            if name=='Chill Haunting':command['exile_cards']=[c.ref for c in cards]
            result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
            self.cast(session,'B',murder,[body.ref]);session=self.replay(session,load=True)
            self.assertIsNone(self.resolve(session))
            self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
            self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
            self.assertEqual(38 if name=='Hatred' else 40,session.state.players['A'].life)
            self.assertTrue(all(session.state.cards[c.object_id].zone==('graveyard' if name=='Hatred' else 'exile') for c in cards))
            self.private_replay(session,private)

    def test_real_cascade_forces_mana_x_zero_but_pays_nonmana_additional_x(self):
        for index,name,x in ((0,'Hatred',2),(1,'Chill Haunting',2),(2,'Skeletal Scrying',0)):
            session=self.session(72202020+index);party=self.add(session.engine,'Boarding Party',zone='hand')
            source=self.add(session.engine,name,zone='library')
            library=session.state.players['A'].zones['library']
            library.remove(source.object_id);library.insert(0,source.object_id)
            body=self.add(session.engine,'Generic Bound Body')
            cards=[self.add(session.engine,'Generic Bound Body',zone='graveyard',ref='cascade-cost-'+str(i)) for i in range(2)]
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'R':1,'C':5});self.checkpoint(session)
            for card in (party,source):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
            self.cast(session,'A',party);decision=self.resolve(session)
            self.assertIsNotNone(decision);self.assertEqual('selection.exile_cast',decision.kind)
            ctx=session.packet('pilot:A',full=True)['decision']['ctx']
            self.assertTrue(ctx['cast_options'])
            if name=='Skeletal Scrying':
                self.assertEqual(0,ctx['cast_options'][0]['choice_schema']['x']['maximum'])
                before=authoritative_state_hash(session.state)
                bad=session.act('pilot:A',{'action_id':'cast','x':2,'exile_cards':[c.ref for c in cards]})
                self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            command={'action_id':'cast','x':x}
            if name!='Skeletal Scrying':command['targets']=[body.ref]
            if name=='Chill Haunting':command['exile_cards']=[c.ref for c in cards]
            result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
            item=next(item for item in session.state.stack if item.card_object_id==source.object_id)
            self.assertEqual(x,item.x_value)
            self.assertEqual(38 if name=='Hatred' else 40,session.state.players['A'].life)
            if name=='Chill Haunting':self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' for c in cards))
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual(4 if name=='Hatred' else 0 if name=='Chill Haunting' else 2,
                session.engine._numeric_stat(body.object_id,'power'))
            self.assertEqual(38 if name=='Hatred' else 40,session.state.players['A'].life)
            self.private_replay(session,private)

    def test_original_hatred_rejects_overpayment_but_paying_all_life_eliminates_caster(self):
        session=self.session(72202008);source=self.add(session.engine,'Hatred',zone='hand')
        body=self.add(session.engine,'Generic Bound Body',seat='B')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':2,'C':3});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        action=self.action(session,source);before=authoritative_state_hash(session.state)
        bad=session.act('pilot:A',{'action_id':action['id'],'x':41,'targets':[body.ref],'pay':'auto'})
        self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id'],'x':40,'targets':[body.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,session.state.players['A'].life);self.assertFalse(session.state.players['A'].in_game)
        self.assertFalse(any(item.card_object_id==source.object_id for item in session.state.stack))
        self.assertEqual(2,session.engine._numeric_stat(body.object_id,'power'))
        self.private_replay(session,private)
