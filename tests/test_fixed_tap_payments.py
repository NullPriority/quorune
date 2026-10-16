from __future__ import annotations

"""CR 118.12/302.6/701.26: fixed selected tap costs during resolution."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_effect_payment import FixedEffectPaymentSpec
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_reflexive_payments as reflexive
import test_self_public_zone_casting as zone_actions


class FixedTapPaymentCompilerTests(unittest.TestCase):
    def test_another_exclusion_binds_only_the_original_source_incarnation(self):
        from quorune.compiler.fixed_effect_payment_templates import fixed_effect_payment_spec
        from quorune.semantic_runtime.values import resolve_semantic_value
        payment=fixed_effect_payment_spec('tap another untapped creature you control')
        self.assertIsNotNone(payment)
        for identity,excluded in (('original-object','source-ref'),('new-object',None)):
            item=SimpleNamespace(controller='A',source_object_id='source-id',context={'source_logical_object_id':'original-object'})
            host=SimpleNamespace(state=SimpleNamespace(cards={'source-id':SimpleNamespace(
                ref='source-ref',zone='battlefield',phased_out=False,logical_object_id=identity)}))
            bound=FixedEffectPaymentSpec.from_dict(resolve_semantic_value(host,payment.to_dict(),item))
            self.assertEqual(excluded,bound.predicate.exclude_ref)
            self.assertEqual('A',bound.predicate.controller)

    def test_withdrawn_tap_payment_capability_prevents_promotion(self):
        from quorune.rules.capabilities import CapabilityRegistry
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(cap for cap in raw['capabilities'] if cap['id']=='cost.tap.controlled_untapped_resolution').update(
            status='blocked',blockers=['withdrawn'])
        ir=compile_oracle_card(record('When this creature enters, you may tap another untapped creature you control. If you do, draw a card.','Creature — Human'),
            capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)

    def test_fixed_untapped_controlled_costs_preserve_quality_count_and_result_timing(self):
        registry=load_default_capability_registry()
        for cost,count,field,values,other in (
            ('tap two untapped creatures you control',2,'types_all',('creature',),False),
            ('tap another untapped Citizen you control',1,'subtypes_all',('citizen',),True),
            ('tap an untapped non-Human creature you control',1,'excluded_subtypes',('human',),False),
            ('tap two other untapped creatures you control',2,'types_all',('creature',),True),
        ):
            for link in ('If','When'):
                with self.subTest(cost=cost,link=link):
                    ir=compile_oracle_card(record(f'When this creature enters, you may {cost}. {link} you do, target player draws a card.','Creature — Human'),
                        capability_registry=registry,capability_profile='commander_review')
                    self.assertEqual('exact',ir.status,ir.material_residuals)
                    node=ir.faces[0].nodes[0];payment=node.effects[0]['payment']
                    self.assertEqual(('tap',3,count),(payment['kind'],payment['schema_version'],payment['amount']))
                    predicate=ObjectQuerySpec.from_dict(payment['predicate'])
                    self.assertEqual(('$controller',False,True),(predicate.controller,predicate.tapped,predicate.known_to_actor))
                    self.assertEqual(values,getattr(predicate,field))
                    self.assertEqual('$source.zone_object' if other else None,predicate.exclude_ref)
                    self.assertEqual(link=='When',node.target_schema is None)
                    self.assertIn('cost.tap.controlled_untapped_resolution',node.capability_dependencies)

    def test_tap_payload_rejects_wrong_version_or_open_predicates(self):
        spec=FixedEffectPaymentSpec('tap',schema_version=3,amount=2,predicate=ObjectQuerySpec(
            zones=('battlefield',),controller='$controller',types_all=('creature',),tapped=False,known_to_actor=True))
        self.assertEqual(spec,FixedEffectPaymentSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        for change in ({'schema_version':2},{'amount':True},{'amount':0},
                {'predicate':replace(spec.predicate,tapped=True)},
                {'predicate':replace(spec.predicate,controller=None)},
                {'predicate':replace(spec.predicate,owner='A')},
                {'predicate':replace(spec.predicate,zones=('graveyard',))},
                {'predicate':replace(spec.predicate,keywords_all=('haste',))}):
            with self.subTest(change=tuple(change)),self.assertRaises(ValueError):replace(spec,**change)
        self.assertEqual(2,FixedEffectPaymentSpec('life',amount=1).to_dict()['schema_version'])

    def test_source_variable_linked_and_combined_tap_costs_stay_residual(self):
        registry=load_default_capability_registry()
        for cost in ('tap it','tap X untapped creatures you control','tap any number of untapped creatures you control',
                'tap two untapped creatures you control that share a color with it',
                'tap an untapped creature','tap an untapped creature you control and pay {1}'):
            with self.subTest(cost=cost):
                ir=compile_oracle_card(record(f'When this creature enters, you may {cost}. When you do, draw a card.','Creature — Human'),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)


class FixedTapPaymentRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast=zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed=zone_actions.SelfPublicZoneRuntimeTests.assert_closed
    advance_to=reflexive.ReflexivePaymentRuntimeTests.advance_to
    choose=reflexive.ReflexivePaymentRuntimeTests.choose

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name).resolve()/'fixed-tap-payments.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/event-player-subject-cards.json',
            ROOT/'tests/fixtures/fixed-damage-source-lki-cards.json',
            ROOT/'tests/fixtures/fixed-tap-payment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Tap payment witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def legal_cost_refs(self,session):
        return set(session.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']['legal_refs'])

    def reject_without_mutation(self,session,actor='A',**response):
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:'+actor,{'action_id':'choose',**response}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_actual_raff_taps_exactly_two_fresh_creatures_with_locked_payer_and_private_replay(self):
        for steal in (False,True):
            with self.subTest(steal=steal):
                session=self.session(404000+steal);engine=session.engine
                source=self.add(engine,'Raff, Weatherlight Stalwart',zone='hand',ref='actual-raff-tap-payment')
                spell=self.add(engine,'Generic Tap Payment Spell',zone='hand',ref='raff-cast-trigger')
                first=self.add(engine,'Generic Tap Payment Goblin',ref='raff-first-fresh-cost')
                second=self.add(engine,'Generic Tap Payment Goblin',ref='raff-second-fresh-cost')
                foreign=self.add(engine,'Generic Tap Payment Goblin',seat='B',ref='raff-foreign-cost')
                private=self.add(engine,'Generic Bound Growth',seat='D',zone='hand',ref='raff-private-hand')
                observer=self.add(engine,'Generic Tap Payment Observer',ref='raff-tap-observer')
                control=self.add(engine,'Generic Event Subject Control',seat='D',zone='hand',ref='raff-real-control')
                for card in (source,spell,observer,control):self.assert_closed(session,card)
                self.prepare(session,mana={'W':1,'U':1});engine.state.players['D'].mana_pool['W']=1
                self.checkpoint(session);self.cast(session,'A',source);self.resolve(session)
                self.cast(session,'A',spell)
                if steal:self.cast(session,'D',control,[source.ref])
                self.assertEqual('semantic.choice',self.resolve(session).kind)
                self.assertEqual('D' if steal else 'A',session.state.cards[source.object_id].controller)
                legal=self.legal_cost_refs(session)
                self.assertTrue({first.ref,second.ref}<=legal);self.assertNotIn(foreign.ref,legal)
                self.assertEqual(not steal,source.ref in legal)
                selected=[first.ref,second.ref if steal else source.ref]
                self.reject_without_mutation(session,'B',cards=selected)
                self.reject_without_mutation(session,cards=selected[:1])
                self.reject_without_mutation(session,cards=[first.ref,first.ref])
                self.reject_without_mutation(session,cards=[first.ref,foreign.ref])
                hands=len(session.state.players['A'].zones['hand'])
                session=self.replay(session,load=True);self.choose(session,cards=selected)
                self.assertTrue(all(session.state.cards[c.object_id].tapped for c in (first,second if steal else source)))
                self.assertEqual(hands+1,len(session.state.players['A'].zones['hand']))
                self.assertEqual('stack',session.state.cards[spell.object_id].zone)
                self.assertEqual('trigger.order',self.resolve(session).kind)
                triggers=session.packet('pilot:A',full=True)['decision']['ctx']['triggers']
                self.assertEqual(2,len(triggers))
                session=self.replay(session,load=True)
                ordered=session.act('pilot:A',{'action_id':'order','triggers':[row['id'] for row in triggers]})
                self.assertTrue(ordered.ok,ordered.summary);self.assertIsNone(self.resolve(session))
                self.assertEqual(43,session.state.players['A'].life)
                for viewer in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
                self.replay(session,load=True)

    def test_actual_civil_servant_excludes_vigilant_source_and_uses_currently_controlled_citizen(self):
        session=self.session(404010);engine=session.engine
        source=self.add(engine,'Civil Servant',zone='hand',ref='actual-civil-tap-payment')
        grant=self.add(engine,'Generic Tap Payment Vigilance',zone='hand',ref='civil-haste-vigilance')
        citizen=self.add(engine,'Generic Tap Payment Citizen',ref='civil-fresh-citizen')
        citizen.owner='D'
        other=self.add(engine,'Generic Tap Payment Goblin',ref='civil-wrong-subtype')
        foreign=self.add(engine,'Generic Tap Payment Citizen',seat='B',ref='civil-foreign-citizen')
        self.assert_closed(session,source);self.prepare(session,mana={'G':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',source);self.resolve(session);self.cast(session,'A',grant,[source.ref]);self.resolve(session)
        self.advance_to(session,'combat.attackers')
        accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertFalse(session.state.cards[source.object_id].tapped)
        legal=self.legal_cost_refs(session)
        self.assertIn(citizen.ref,legal)
        for ref in (source.ref,other.ref,foreign.ref):self.assertNotIn(ref,legal)
        self.reject_without_mutation(session,cards=[source.ref])
        session=self.replay(session,load=True);self.choose(session,cards=[citizen.ref]);self.resolve(session)
        self.assertTrue(session.state.cards[citizen.object_id].tapped)
        current=session.state.cards[source.object_id];data=session.engine._effective_card_data(current)
        self.assertEqual((3,3),(int(data['power']),int(data['toughness'])))
        self.assertIn('lifelink',[k.casefold() for k in data['keywords']])
        self.replay(session,load=True)

    def test_actual_redcap_can_tap_its_vigilant_attacking_self_without_leaving_combat_or_can_decline(self):
        for pay in (False,True):
            with self.subTest(pay=pay):
                session=self.session(404020+pay);engine=session.engine
                source=self.add(engine,'Redcap Raiders',zone='hand',ref='actual-redcap-tap-payment')
                grant=self.add(engine,'Generic Tap Payment Vigilance',zone='hand',ref='redcap-haste-vigilance')
                human=self.add(engine,'Generic Bound Body',ref='redcap-human-not-eligible')
                goblin=self.add(engine,'Generic Tap Payment Goblin',ref='redcap-fresh-nonhuman')
                self.assert_closed(session,source);self.prepare(session,mana={'C':2,'R':1});self.checkpoint(session)
                self.cast(session,'A',source);self.resolve(session);self.cast(session,'A',grant,[source.ref]);self.resolve(session)
                self.advance_to(session,'combat.attackers')
                accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual('semantic.choice',self.resolve(session).kind)
                legal=self.legal_cost_refs(session)
                self.assertTrue({source.ref,goblin.ref}<=legal);self.assertNotIn(human.ref,legal)
                self.reject_without_mutation(session,cards=[human.ref])
                session=self.replay(session,load=True);self.choose(session,cards=[source.ref] if pay else [])
                self.resolve(session)
                self.assertEqual(pay,session.state.cards[source.object_id].tapped)
                self.assertIn(source.object_id,session.state.combat.attackers)
                data=session.engine._effective_card_data(session.state.cards[source.object_id])
                self.assertEqual(3+pay,int(data['power']));self.assertEqual(2+pay,int(data['toughness']))
                self.assertEqual(pay,'trample' in [k.casefold() for k in data['keywords']])
                self.replay(session,load=True)


if __name__=='__main__':unittest.main()
