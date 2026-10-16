from __future__ import annotations

"""CR 118.12/603.12: exact owned graveyard exile costs preserve result timing."""

import unittest
import json
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.fixed_effect_payment import FixedEffectPaymentSpec
from quorune.object_predicate import ObjectQuerySpec
from scripts.build_test_database import build_fixture_database
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


class GraveyardExilePaymentCompilerTests(unittest.TestCase):
    def test_return_target_binding_rejects_a_new_incarnation_without_reading_it(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        from quorune.return_to_hand import return_graveyard_card_to_owner_hand
        item=SimpleNamespace(targets=['stable-ref'],context={'target_snapshots':{'stable-ref':{
            'object_id':'target-object','zone_change_counter':1}}})
        bound=resolve_semantic_value(None,{'op':'return_graveyard_card_to_owner_hand','card':'$target.0'},item)
        self.assertEqual({'ref':'stable-ref','object_id':'target-object','zone_change_counter':1},bound['target_snapshot'])
        host=SimpleNamespace(state=SimpleNamespace(cards={'target-object':SimpleNamespace(ref='stable-ref',zone_change_counter=3)}),
            _resolve_object=lambda *args,**kwargs:self.fail('New target incarnation must not be read or moved'))
        self.assertIsNone(return_graveyard_card_to_owner_hand(host,'stable-ref',actor='A',reason='Original target only',
            target_snapshot=bound['target_snapshot']))
        literal=resolve_semantic_value(None,{'op':'return_graveyard_card_to_owner_hand','card':'stable-ref'},item)
        self.assertNotIn('target_snapshot',literal)
        from quorune.semantic_runtime.intents import ReturnGraveyardCardToOwnerHandIntent
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.model import SemanticChoiceContinuation,SemanticChoiceFrame,SemanticChoiceCompletion
        from quorune.semantic_choices.completed_choice import encode_remaining_completion,decode_remaining_completion
        intent=ReturnGraveyardCardToOwnerHandIntent(actor='A',object_ref='stable-ref',reason='Original target',
            target_snapshot=FrozenMap(bound['target_snapshot']))
        continuation=SemanticChoiceContinuation(handler_id='choice.payment.optional-fixed-effect.v1',handler_version=1,
            stack_ref='original-stack',effect=FrozenMap({'op':'offer_optional_mana_payment'}),remaining=(),destination=None,note='',
            semantic_frame=SemanticChoiceFrame(semantic_program_id='fixture:trigger',semantic_program_version=1,
                stack_object='original-stack',instruction_pointer=0,controller='A'))
        completion=SemanticChoiceCompletion(intents=(intent,))
        encoded=encode_remaining_completion(continuation,'A',{},completion,0)
        restored=decode_remaining_completion(json.loads(json.dumps(encoded)))
        self.assertEqual(completion.intents,restored[3].intents)
    def test_new_exile_schema_round_trips_and_old_payment_payload_is_unchanged(self):
        spec=FixedEffectPaymentSpec('exile',schema_version=3,amount=2,predicate=ObjectQuerySpec(
            zones=('graveyard',),owner='$controller',token=False,known_to_actor=True))
        self.assertEqual(spec,FixedEffectPaymentSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        for change in ({'schema_version':2},{'amount':True},{'amount':0},
                       {'predicate':replace(spec.predicate,zones=('hand',))},
                       {'predicate':replace(spec.predicate,controller='A')},
                       {'predicate':replace(spec.predicate,token=None)},
                       {'predicate':replace(spec.predicate,known_to_actor=False)}):
            with self.subTest(change=tuple(change)),self.assertRaises(ValueError):replace(spec,**change)
        self.assertEqual({'schema_version':2,'kind':'life','amount':2,'requirements':None,'predicate':None},
            FixedEffectPaymentSpec('life',amount=2).to_dict())
    def test_owned_graveyard_exile_costs_use_versioned_card_predicates_and_keep_if_when_timing(self):
        registry=load_default_capability_registry()
        for link in ('If','When'):
            for cost,amount,types in [('exile a creature card from your graveyard',1,('creature',)),
                    ('exile two cards from your graveyard',2,()),
                    ('exile a creature or enchantment card from your graveyard',1,('creature','enchantment'))]:
                with self.subTest(link=link,cost=cost):
                    text=f'When this creature enters, you may {cost}. {link} you do, target player draws a card.'
                    ir=compile_oracle_card(record(text,'Creature — Human'),capability_registry=registry,capability_profile='commander_review')
                    self.assertEqual('exact',ir.status,ir.material_residuals)
                    node=ir.faces[0].nodes[0];effect=node.effects[0];payment=effect['payment']
                    self.assertEqual(3,payment['schema_version']);self.assertEqual('exile',payment['kind'])
                    self.assertEqual(amount,payment['amount']);self.assertEqual('$controller',payment['predicate']['owner'])
                    self.assertFalse(payment['predicate']['token']);self.assertIsNone(payment['predicate']['controller'])
                    self.assertEqual(set(types),set(payment['predicate']['types_all'])|set(payment['predicate']['types_any']))
                    self.assertEqual(link=='When',node.target_schema is None)
                    self.assertIn('cost.exile.owned_graveyard_resolution',node.capability_dependencies)
        from quorune.rules.capabilities import CapabilityRegistry
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(cap for cap in raw['capabilities'] if cap['id']=='cost.exile.owned_graveyard_resolution').update(
            status='blocked',blockers=['withdrawn'])
        ir=compile_oracle_card(record(text,'Creature — Human'),capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)

    def test_source_specific_hidden_variable_and_linked_exile_costs_remain_residual(self):
        registry=load_default_capability_registry()
        for cost in ('exile it','exile a card from your hand','exile all cards from your graveyard',
                     'exile X cards from your graveyard','exile a card named Example from your graveyard'):
            with self.subTest(cost=cost):
                ir=compile_oracle_card(record(f'When this creature enters, you may {cost}. When you do, draw a card.','Creature — Human'),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)


class GraveyardExilePaymentRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast=zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed=zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name).resolve()/'graveyard-exile-payments.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/fixed-damage-source-lki-cards.json',
            ROOT/'tests/fixtures/event-player-subject-cards.json',
            ROOT/'tests/fixtures/graveyard-exile-payment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Exile payment witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def choose(self,session,actor='A',**response):
        result=session.act('pilot:'+actor,{'action_id':'choose',**response})
        self.assertTrue(result.ok,result.summary)

    def reject_without_mutation(self,session,actor='A',**response):
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:'+actor,{'action_id':'choose',**response}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))

    def legal_cost_refs(self,session):
        action=session.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'][0]
        return action['choice_schema']['legal_refs']

    def test_actual_kishla_exiles_owned_card_despite_foreign_stored_controller_then_selects_result(self):
        session=self.session(403001);engine=session.engine
        source=self.add(engine,'Kishla Trawlers',zone='hand',ref='actual-kishla-exile-payment')
        owned=self.add(engine,'Memnite',zone='graveyard',ref='kishla-owned-foreign-controller');owned.controller='D'
        foreign=self.add(engine,'Memnite',seat='B',zone='graveyard',ref='kishla-foreign-owner');foreign.controller='A'
        result=self.add(engine,'Generic Damage Source Kill',zone='graveyard',ref='kishla-return-spell')
        private=self.add(engine,'Generic Bound Growth',seat='D',zone='hand',ref='kishla-private')
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'U':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertFalse(session.state.stack[-1].targets)
        self.assertIn(owned.ref,self.legal_cost_refs(session));self.assertNotIn(foreign.ref,self.legal_cost_refs(session))
        self.reject_without_mutation(session,'B',cards=[owned.ref]);self.reject_without_mutation(session,cards=[foreign.ref])
        session=self.replay(session,load=True);self.choose(session,cards=[owned.ref])
        self.assertEqual('exile',session.state.cards[owned.object_id].zone)
        self.assertEqual('semantic.target',session.state.pending_decision.kind)
        session=self.replay(session,load=True);self.choose(session,targets=[result.ref]);self.resolve(session)
        self.assertEqual('hand',session.state.cards[result.object_id].zone)
        for viewer in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)

    def test_actual_young_necromancer_requires_two_distinct_cards_before_return_targets(self):
        session=self.session(403010);engine=session.engine
        source=self.add(engine,'Young Necromancer',zone='hand',ref='actual-two-card-exile-payment')
        first=self.add(engine,'Island',zone='graveyard',ref='young-first-payment')
        second=self.add(engine,'Generic Damage Source Kill',zone='graveyard',ref='young-second-payment')
        target=self.add(engine,'Generic Bound Body',zone='graveyard',ref='young-return-creature')
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'B':1});self.checkpoint(session)
        self.cast(session,'A',source);self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.reject_without_mutation(session,cards=[first.ref]);self.reject_without_mutation(session,cards=[first.ref,first.ref])
        session=self.replay(session,load=True);self.choose(session,cards=[first.ref,second.ref])
        self.assertEqual('exile',session.state.cards[first.object_id].zone)
        self.assertEqual('exile',session.state.cards[second.object_id].zone)
        self.assertEqual('semantic.target',session.state.pending_decision.kind)
        session=self.replay(session,load=True);self.choose(session,targets=[target.ref]);self.resolve(session)
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone)
        self.replay(session,load=True)

    def test_actual_skald_selects_before_cost_and_exiled_target_does_not_return(self):
        for same in (False,True):
            with self.subTest(same=same):
                session=self.session(403020+same);engine=session.engine
                source=self.add(engine,'Master Skald',zone='hand',ref='actual-skald-exile-payment')
                target=self.add(engine,'Perilous Myr',zone='graveyard',ref='skald-artifact-creature-target')
                other=self.add(engine,'Generic Bound Body',zone='graveyard',ref='skald-other-creature-cost')
                self.assert_closed(session,source);self.prepare(session,mana={'C':4,'W':1});self.checkpoint(session)
                self.cast(session,'A',source);self.assertEqual('semantic.target',self.resolve(session).kind)
                self.choose(session,targets=[target.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
                session=self.replay(session,load=True);payment=target if same else other
                self.choose(session,cards=[payment.ref]);self.resolve(session)
                self.assertEqual('exile',session.state.cards[payment.object_id].zone)
                self.assertEqual('exile' if same else 'hand',session.state.cards[target.object_id].zone)
                self.replay(session,load=True)

    def test_actual_tymaret_mills_before_union_cost_and_decline_keeps_the_mandatory_prefix(self):
        for pay in (False,True):
            with self.subTest(pay=pay):
                session=self.session(403030+pay);engine=session.engine
                source=self.add(engine,'Tymaret Calls the Dead',zone='hand',ref='actual-tymaret-exile-payment')
                enchantment=self.add(engine,'Generic Damage Flash Aura',zone='graveyard',ref='tymaret-enchantment-cost')
                creature=self.add(engine,'Memnite',zone='graveyard',ref='tymaret-creature-cost')
                land=self.add(engine,'Island',zone='graveyard',ref='tymaret-ineligible-land')
                self.assert_closed(session,source);self.prepare(session,mana={'C':2,'B':1});self.checkpoint(session)
                before=len(session.state.players['A'].zones['library'])
                self.cast(session,'A',source);self.assertEqual('semantic.choice',self.resolve(session).kind)
                self.assertEqual(before-3,len(session.state.players['A'].zones['library']))
                legal=self.legal_cost_refs(session)
                self.assertIn(enchantment.ref,legal);self.assertIn(creature.ref,legal);self.assertNotIn(land.ref,legal)
                session=self.replay(session,load=True);self.choose(session,cards=[enchantment.ref] if pay else [])
                self.resolve(session)
                self.assertEqual('exile' if pay else 'graveyard',session.state.cards[enchantment.object_id].zone)
                zombies=[card for card in session.state.cards.values() if card.zone=='battlefield' and card.is_token]
                self.assertEqual(1 if pay else 0,len(zombies))
                self.replay(session,load=True)
