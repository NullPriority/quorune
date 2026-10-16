from __future__ import annotations

"""CR 118.12/122.2/400.7: resolution costs use the original source object."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.fixed_effect_payment import FixedEffectPaymentSpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_fixed_tap_payments as tap_actions
import test_self_public_zone_casting as zone_actions


class FixedSourceCounterPaymentCompilerTests(unittest.TestCase):
    def test_paid_legacy_search_reuses_closed_leaf_validation_and_rejects_open_shapes(self):
        from quorune.compiler.library_search_templates import fixed_library_search_effect_template
        from quorune.semantic_choices.optional_effect import _represented_effect
        from quorune.semantic_choices.model import SemanticChoiceError
        for text in ('Search your library for a basic land card, put it onto the battlefield tapped, then shuffle.',
                'Search your library for up to two land cards, put them onto the battlefield tapped, then shuffle.',
                'Search your library for a creature card, reveal it, put it into your hand, then shuffle.'):
            with self.subTest(text=text):
                compiled=fixed_library_search_effect_template(text)
                self.assertIsNotNone(compiled)
                effect=dict(compiled.compiled()[1][0])
                _represented_effect(effect,actor='A',query=SimpleNamespace())
                for change in ({'shuffle_after':False},{'destination':'exile'},
                        {'unrepresented':'extra'},{'count':{'minimum':1,'maximum':'one'}}):
                    with self.subTest(change=change),self.assertRaises(SemanticChoiceError):
                        _represented_effect({**effect,**change},actor='A',query=SimpleNamespace())

    def test_public_counter_payment_uses_no_hidden_identity_and_rejects_stale_or_partial_resources(self):
        from quorune.semantic_choices.fixed_effect_payment import prepare_fixed_payment_cost,complete_fixed_payment_cost
        from quorune.semantic_choices.model import SemanticChoiceError
        spec=FixedEffectPaymentSpec('remove_source_counter',schema_version=4,amount=2,counter_name='oil',source_ref='source-ref')
        row=SimpleNamespace(ref='source-ref',logical_object_id='old-object',controller='D',phased_out=False,
            known_to_actor=False,counters={'oil':2})
        query=SimpleNamespace(object=lambda ref,**kwargs:row)
        context=SimpleNamespace(actor='A',query=query,stack_ref='stack-ref',stack_label='Public counters')
        prepared=prepare_fixed_payment_cost(spec,{'op':'offer_optional_mana_payment'},context)
        self.assertEqual((True,False),prepared.request.choice.legal_values)
        effect=prepared.continuation_effect
        completion=complete_fixed_payment_cost(spec,effect,{'pay':True},query)
        self.assertEqual(('A','source-ref',2),
            (completion.intents[0].actor,completion.intents[0].object_ref,completion.intents[0].amount))
        row.logical_object_id='new-object'
        with self.assertRaises(SemanticChoiceError):complete_fixed_payment_cost(spec,effect,{'pay':True},query)
        row.logical_object_id='old-object';row.counters['oil']=1
        with self.assertRaises(SemanticChoiceError):complete_fixed_payment_cost(spec,effect,{'pay':True},query)
        self.assertFalse(complete_fixed_payment_cost(spec,effect,{'pay':False},query).intents)

    def test_withdrawn_counter_cost_capability_prevents_promotion(self):
        from quorune.rules.capabilities import CapabilityRegistry
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(cap for cap in raw['capabilities'] if cap['id']=='cost.counter.source_fixed_resolution').update(
            status='blocked',blockers=['withdrawn'])
        ir=compile_oracle_card(record('Whenever this creature attacks, you may remove a +1/+1 counter from it. If you do, draw a card.','Creature — Human'),
            capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)

    def test_fixed_source_counter_costs_keep_named_amount_source_and_if_when_timing(self):
        registry=load_default_capability_registry()
        for cost,counter,count in (
                ('remove a +1/+1 counter from it','+1/+1',1),
                ('remove two oil counters from this creature','oil',2),
                ('remove three feeding counters from this permanent','feeding',3)):
            for link in ('If','When'):
                with self.subTest(cost=cost,link=link):
                    ir=compile_oracle_card(record(f'Whenever this creature attacks, you may {cost}. {link} you do, target player draws a card.','Creature — Human'),
                        capability_registry=registry,capability_profile='commander_review')
                    self.assertEqual('exact',ir.status,ir.material_residuals)
                    node=ir.faces[0].nodes[0];payment=node.effects[0]['payment']
                    self.assertEqual((4,'remove_source_counter',count,counter,'$source.zone_object'),
                        (payment['schema_version'],payment['kind'],payment['amount'],payment['counter_name'],payment['source_ref']))
                    self.assertEqual(link=='When',node.target_schema is None)
                    self.assertIn('cost.counter.source_fixed_resolution',node.capability_dependencies)

    def test_counter_cost_version_and_source_identity_round_trip_without_changing_old_payloads(self):
        spec=FixedEffectPaymentSpec('remove_source_counter',schema_version=4,amount=2,
            counter_name='oil',source_ref='$source.zone_object')
        self.assertEqual(spec,FixedEffectPaymentSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        for change in ({'schema_version':2},{'schema_version':3},{'amount':True},{'counter_name':''},
                {'counter_name':'Oil'},{'source_ref':''}):
            with self.subTest(change=tuple(change)),self.assertRaises(ValueError):replace(spec,**change)
        self.assertEqual({'schema_version':2,'kind':'life','amount':1,'requirements':None,'predicate':None},
            FixedEffectPaymentSpec('life').to_dict())
        with self.assertRaises(ValueError):FixedEffectPaymentSpec('life',counter_name='oil')
        from quorune.semantic_runtime.values import resolve_semantic_value
        item=SimpleNamespace(controller='A',source_object_id='source-id',context={'source_logical_object_id':'old-object'})
        for current,expected in (('old-object','source-ref'),('new-object',None)):
            host=SimpleNamespace(state=SimpleNamespace(cards={'source-id':SimpleNamespace(ref='source-ref',
                zone='battlefield',phased_out=False,logical_object_id=current)}))
            bound=FixedEffectPaymentSpec.from_dict(resolve_semantic_value(host,spec.to_dict(),item))
            self.assertEqual(expected,bound.source_ref)

    def test_unbound_pronouns_other_objects_and_variable_or_unnamed_counters_remain_residual(self):
        registry=load_default_capability_registry()
        for text in (
                'Whenever another creature enters, you may remove a +1/+1 counter from it. If you do, draw a card.',
                'Whenever this creature attacks, you may remove a counter from it. When you do, draw a card.',
                'Whenever this creature attacks, you may remove all +1/+1 counters from it. If you do, draw a card.',
                'Whenever this creature attacks, you may remove X +1/+1 counters from it. If you do, draw a card.',
                'Whenever this creature attacks, you may remove a +1/+1 counter from another creature. If you do, draw a card.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Creature — Human'),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)


class FixedSourceCounterPaymentRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast=zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed=zone_actions.SelfPublicZoneRuntimeTests.assert_closed
    advance_to=tap_actions.FixedTapPaymentRuntimeTests.advance_to
    choose=tap_actions.FixedTapPaymentRuntimeTests.choose
    reject_without_mutation=tap_actions.FixedTapPaymentRuntimeTests.reject_without_mutation

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name).resolve()/'fixed-source-counter-payments.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/event-player-subject-cards.json',
            ROOT/'tests/fixtures/fixed-damage-source-lki-cards.json',
            ROOT/'tests/fixtures/fixed-tap-payment-cards.json',
            ROOT/'tests/fixtures/fixed-source-counter-payment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source counter payment witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def attack(self,session,source):
        self.advance_to(session,'combat.attackers')
        accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(accepted.ok,accepted.summary)

    def test_actual_purestrain_removes_before_search_and_departed_or_returned_source_cannot_pay(self):
        for mode in ('pay','depart','return'):
            with self.subTest(mode=mode):
                session=self.session(405000+len(mode));engine=session.engine
                source=self.add(engine,'Purestrain Genestealer',zone='hand',ref='actual-purestrain-counter-payment')
                haste=self.add(engine,'Generic Tap Payment Vigilance',zone='hand',ref='purestrain-haste')
                kill=self.add(engine,'Generic Damage Source Kill',zone='hand',ref='purestrain-source-departure')
                reenter=self.add(engine,'Generic Event Subject Return',zone='hand',ref='purestrain-new-incarnation')
                self.add(engine,'Generic Bound Plains',ref='purestrain-reentry-mana')
                self.add(engine,'Generic Bound Body',seat='B',ref='purestrain-possible-blocker')
                self.assert_closed(session,source);self.prepare(session,mana={'C':2,'G':1,'W':1});self.checkpoint(session)
                self.cast(session,'A',source);self.resolve(session)
                self.assertEqual(2,session.state.cards[source.object_id].counters.get('+1/+1'))
                self.cast(session,'A',haste,[source.ref]);self.resolve(session);self.attack(session,source)
                old_identity=session.state.cards[source.object_id].logical_object_id
                if mode!='pay':
                    self.cast(session,'A',kill,[source.ref])
                    if mode=='return':
                        for _ in range(16):
                            if session.state.cards[source.object_id].zone=='graveyard':break
                            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                        else:self.fail('Real source destruction did not resolve')
                        self.cast(session,'A',reenter,[source.ref])
                self.assertEqual('semantic.choice',self.resolve(session).kind)
                library=len(session.state.players['A'].zones['library'])
                session=self.replay(session,load=True)
                if mode!='pay':
                    self.reject_without_mutation(session,pay=True);self.choose(session,pay=False);self.resolve(session)
                    self.assertEqual(library,len(session.state.players['A'].zones['library']))
                    current=session.state.cards[source.object_id]
                    if mode=='return':
                        self.assertNotEqual(old_identity,current.logical_object_id)
                        self.assertEqual(('battlefield',2),(current.zone,current.counters.get('+1/+1')))
                    else:self.assertEqual('graveyard',current.zone)
                else:
                    self.choose(session,pay=True)
                    self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1'))
                    self.assertEqual('semantic.search',self.resolve(session).kind)
                    land_id=session.state.players['A'].zones['library'][0];land=session.state.cards[land_id]
                    for viewer in 'BCD':self.assertNotIn(land.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
                    session=self.replay(session,load=True);self.choose(session,search_cards=[land.ref]);self.resolve(session)
                    current=session.state.cards[land_id]
                    self.assertEqual(('battlefield',True),(current.zone,current.tapped))
                    self.assertEqual(library-1,len(session.state.players['A'].zones['library']))
                self.replay(session,load=True)

    def test_actual_nazar_removes_foreign_controlled_source_counters_for_locked_trigger_controller(self):
        for steal in (False,True):
            with self.subTest(steal=steal):
                session=self.session(405020+steal);engine=session.engine
                source=self.add(engine,'Nazar, the Velvet Fang',zone='hand',ref='actual-nazar-counter-payment')
                gains=[self.add(engine,'Generic Tap Payment Spell',zone='hand',ref='nazar-life-'+str(i)) for i in range(3)]
                haste=self.add(engine,'Generic Tap Payment Vigilance',zone='hand',ref='nazar-haste')
                control=self.add(engine,'Generic Event Subject Control',seat='D',zone='hand',ref='nazar-real-control')
                private=self.add(engine,'Generic Bound Growth',seat='D',zone='hand',ref='nazar-private-hand')
                self.add(engine,'Generic Bound Plains',seat='D',ref='nazar-control-mana')
                self.add(engine,'Generic Bound Body',seat='B',ref='nazar-possible-blocker')
                self.assert_closed(session,source);self.prepare(session,mana={'C':3,'B':1});engine.state.players['D'].mana_pool['W']=1
                self.checkpoint(session);self.cast(session,'A',source);self.resolve(session)
                for gain in gains:self.cast(session,'A',gain);self.resolve(session)
                self.assertEqual((43,3),(session.state.players['A'].life,session.state.cards[source.object_id].counters.get('feeding')))
                self.cast(session,'A',haste,[source.ref]);self.resolve(session);self.attack(session,source)
                if steal:self.cast(session,'D',control,[source.ref])
                self.assertEqual('semantic.choice',self.resolve(session).kind)
                self.assertEqual('D' if steal else 'A',session.state.cards[source.object_id].controller)
                hand=len(session.state.players['A'].zones['hand'])
                self.reject_without_mutation(session,'D',pay=True)
                session=self.replay(session,load=True);self.choose(session,pay=True);self.resolve(session)
                self.assertEqual(0,session.state.cards[source.object_id].counters.get('feeding',0))
                self.assertEqual((40,40,hand+3),(session.state.players['A'].life,session.state.players['D'].life,
                    len(session.state.players['A'].zones['hand'])))
                for viewer in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
                self.replay(session,load=True)

    def test_actual_forgehammer_removes_two_oil_before_targeted_counterable_reflexive_frame(self):
        session=self.session(405030);engine=session.engine
        source=self.add(engine,'Forgehammer Centurion',zone='hand',ref='actual-forgehammer-counter-payment')
        fuels=[self.add(engine,'Generic Bound Body',ref='forgehammer-oil-fuel-'+str(i)) for i in range(2)]
        kills=[self.add(engine,'Generic Damage Source Kill',zone='hand',ref='forgehammer-kill-'+str(i)) for i in range(2)]
        haste=self.add(engine,'Generic Tap Payment Vigilance',zone='hand',ref='forgehammer-haste')
        blocker=self.add(engine,'Generic Bound Body',seat='B',ref='forgehammer-block-target')
        stifle=self.add(engine,'Stifle',seat='B',zone='hand',ref='forgehammer-reflexive-counter')
        self.add(engine,'Island',seat='B',ref='forgehammer-counter-mana')
        self.assert_closed(session,source);self.assert_closed(session,stifle)
        self.prepare(session,mana={'C':2,'R':1});engine.state.players['B'].mana_pool['U']=1
        self.checkpoint(session);self.cast(session,'A',source);self.resolve(session)
        for kill,fuel in zip(kills,fuels):self.cast(session,'A',kill,[fuel.ref]);self.resolve(session)
        self.assertEqual(2,session.state.cards[source.object_id].counters.get('oil'))
        self.cast(session,'A',haste,[source.ref]);self.resolve(session);self.attack(session,source)
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertFalse(session.state.stack[-1].targets)
        session=self.replay(session,load=True);self.choose(session,pay=True)
        self.assertEqual(0,session.state.cards[source.object_id].counters.get('oil',0))
        self.assertEqual('semantic.target',session.state.pending_decision.kind)
        child=session.state.stack[-1];self.choose(session,targets=[blocker.ref])
        self.cast(session,'B',stifle,[child.ref]);self.resolve(session)
        self.assertEqual(0,session.state.cards[source.object_id].counters.get('oil',0))
        self.assertFalse(any(item.ref==child.ref for item in session.state.stack))
        self.replay(session,load=True)


if __name__=='__main__':unittest.main()
