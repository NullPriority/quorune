from __future__ import annotations

"""CR 118.3/118.11/701.9: exact random hand costs precede activation results."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.abilities import ActivatedAbility
from quorune.carddb import CardDatabase
from quorune.compiler.activated_ability_catalog import compile_activated_ability_catalog
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_energy_activation_costs as energy_actions
import test_self_public_zone_casting as zone_actions


class RandomDiscardActivationCompilerTests(unittest.TestCase):
    def test_withdrawn_random_cost_capability_prevents_promotion(self):
        from quorune.rules.capabilities import CapabilityRegistry
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(cap for cap in raw['capabilities'] if cap['id']=='activation.random_discard.fixed').update(
            status='blocked',blockers=['withdrawn'])
        ir=compile_oracle_card(record('Discard a card at random: Draw a card.','Artifact'),
            capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)

    def test_granted_and_mana_owners_cannot_drop_a_random_resource_cost(self):
        registry=load_default_capability_registry()
        for text,kind in (
                ('Discard a card at random: Add {G}.','Artifact'),
                ('{T}, Discard a card at random: Add {R}.','Artifact'),
                ('Enchant creature\nEnchanted creature has "Discard a card at random: Draw a card."','Enchantment — Aura')):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,kind),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)

    def test_fixed_random_hand_costs_are_automatic_exact_resources_with_owned_capability(self):
        registry=load_default_capability_registry()
        for cost,amount in (('Discard a card at random',1),('{2}, Discard two cards at random',2),
                ('{T}, Discard three cards at random',3),('{2}{R}, {T}, Discard four cards at random',4)):
            with self.subTest(cost=cost):
                card=record(cost+': Draw a card.','Artifact')
                ir=compile_oracle_card(card,capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0]
                self.assertEqual(amount,node.cost['random_discard_payment'])
                self.assertEqual([],node.cost['choices'])
                self.assertIn('activation.random_discard.fixed',node.capability_dependencies)
                ability=compile_activated_ability_catalog(card)['front'][0]
                self.assertEqual(amount,ability.random_discard_payment)
                self.assertEqual(amount,ability.compact(source_ref='source',zone='battlefield')['random_discard'])
                self.assertEqual(ability,ActivatedAbility.from_dict(json.loads(json.dumps(ability.to_dict()))))

    def test_random_cost_payload_is_optional_strict_and_preserves_older_ability_bytes(self):
        old=compile_activated_ability_catalog(record('{2}, {T}: Draw a card.','Artifact'))['front'][0]
        payload=json.loads(json.dumps(old.to_dict()))
        self.assertNotIn('random_discard_payment',payload)
        self.assertEqual(payload,ActivatedAbility.from_dict(payload).to_dict())
        for amount in (True,-1,5,1.5):
            with self.subTest(amount=amount),self.assertRaises(ValueError):replace(old,random_discard_payment=amount)

    def test_open_random_qualities_counts_and_other_object_costs_remain_residual(self):
        registry=load_default_capability_registry()
        for cost in ('Discard X cards at random','Discard your hand at random',
                'Discard a creature card at random','Discard five cards at random',
                'Discard a card at random, Sacrifice this artifact',
                'Discard a card at random, Discard a card'):
            with self.subTest(cost=cost):
                ir=compile_oracle_card(record(cost+': Draw a card.','Artifact'),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)

    def test_random_replacement_identity_cannot_claim_an_unselected_card(self):
        from quorune.rules.activation_zone_change_costs import activation_zone_change_cost_reference
        response={'source':'source-ref','from':'battlefield','cost_summary':{'random_discard':2},
            '_mana_payment_id':'server-payment','_random_discard_cost_refs':['first','second']}
        check=lambda data,ref,origin='hand',destination='graveyard':activation_zone_change_cost_reference(
            data,origin=origin,destination=destination,object_ref=ref)
        self.assertEqual('first',check(response,'first'))
        for changed in ({**response,'_random_discard_cost_refs':['first']},
                {**response,'_random_discard_cost_refs':['first','first']},
                {**response,'_mana_payment_id':''},
                {**response,'cost_summary':{'random_discard':2,'sac_self':1}},
                {**response,'cost_summary':{'random_discard':2,'choose_cost':[]}}):self.assertIsNone(check(changed,'first'))
        self.assertIsNone(check(response,'foreign'))
        self.assertIsNone(check(response,'first',destination='exile'))


class RandomDiscardActivationRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast=zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed=zone_actions.SelfPublicZoneRuntimeTests.assert_closed
    activate=energy_actions.EnergyActivationRuntimeTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name).resolve()/'random-discard-activation.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/event-player-subject-cards.json',
            ROOT/'tests/fixtures/fixed-resolution-payment-cards.json',
            ROOT/'tests/fixtures/random-discard-activation-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Random activation witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def random_action(self,session,source,amount,*,actor='A',required=True):
        rows=session.packet('pilot:'+actor,full=True)['decision']['ctx']['legal']['actions']
        action=next((r for r in rows if r['id'].startswith('activate:'+source.ref+':')
            and r['cost_summary'].get('random_discard')==amount),None)
        if required:self.assertIsNotNone(action,'Missing supported random-discard activation')
        return action

    def reject(self,session,actor='A',**response):
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:'+actor,response)
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))

    def assert_hand_private(self,session,seat,object_ids):
        for viewer in 'ABCD':
            if viewer==seat:continue
            packet=json.dumps(session.packet('pilot:'+viewer,full=True))
            for oid in object_ids:self.assertNotIn(session.state.cards[oid].ref,packet)

    def test_actual_stormbind_pays_randomly_before_resolution_and_repeated_activations_have_distinct_keys(self):
        session=self.session(407000);engine=session.engine
        source=self.add(engine,'Stormbind',ref='actual-random-stormbind')
        stifle=self.add(engine,'Stifle',seat='B',zone='hand',ref='random-stormbind-counter')
        self.add(engine,'Island',seat='B',ref='random-stormbind-counter-mana')
        for name in ['Generic Bound Growth','Generic Bound Draw Loss','Generic Bound Counter Sequence']:
            self.add(engine,name,zone='hand',ref='random-private-'+name.replace(' ','-'))
        self.prepare(session,mana={'C':4});self.checkpoint(session)
        action=self.random_action(session,source,1);self.assert_closed(session,source)
        self.assertNotIn('choose_cost',action['cost_summary'])
        from quorune.choice_forms import build_action_form,delegated_choice_fields
        self.assertIsNotNone(build_action_form(action,decision_kind='priority',context={}))
        fields=delegated_choice_fields(action,decision_kind='priority',context={})
        self.assertNotIn('cost_cards',fields);self.assertNotIn('cost_objects',fields)
        hand=set(session.state.players['A'].zones['hand'])
        from quorune.random_discard import resolve_random_discard
        keys=[]
        for counter in (False,True):
            action=self.random_action(session,source,1)
            self.reject(session,'B',action_id=action['id'],targets=['C'])
            self.reject(session,action_id=action['id'],targets=['C'],cost_cards=[session.state.cards[next(iter(hand))].ref])
            self.reject(session,action_id=action['id'],targets=['C'],_mana_payment_id='caller-seed')
            before=set(session.state.players['A'].zones['hand'])
            with patch('quorune.rules.activation.random_discard_costs.resolve_random_discard',wraps=resolve_random_discard) as observed:
                self.activate(session,action,['C'])
            keys.extend(call.args[1].randomization_key for call in observed.call_args_list)
            after=set(session.state.players['A'].zones['hand'])
            self.assertEqual(1,len(before-after));self.assertEqual(before- after,{oid for oid in before if session.state.cards[oid].zone=='graveyard'})
            self.assertEqual(40 if not counter else 38,session.state.players['C'].life)
            activated=session.state.stack[-1];session=self.replay(session,load=True)
            if counter:self.cast(session,'B',stifle,[activated.ref])
            self.resolve(session);self.assertEqual(38,session.state.players['C'].life)
            self.assert_hand_private(session,'A',after)
        self.assertEqual(2,len(set(keys)))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.replay(session,load=True)

    def test_actual_meteor_two_card_random_cost_keeps_subset_across_replacement_load_and_replay(self):
        self.exercise_random_replacement_cost('Meteor Storm',2,4,407010)

    def test_actual_stormbind_single_random_cost_keeps_subset_across_replacement_load_and_replay(self):
        self.exercise_random_replacement_cost('Stormbind',1,2,407011)

    def exercise_random_replacement_cost(self,name,count,damage,seed):
        session=self.session(seed);engine=session.engine
        source=self.add(engine,name,ref='actual-random-replacement-source')
        for seat,name in [('B','Generic Payment Destination Replacement'),('C','Generic Payment Competing Replacement')]:
            replacement=self.add(engine,name,seat=seat,ref='random-discard-replacement-'+seat)
            self.assert_closed(session,replacement)
        mana={'C':2,'R':1,'G':1} if count==2 else {'C':2}
        self.assert_closed(session,source);self.prepare(session,mana=mana);self.checkpoint(session)
        original=set(session.state.players['A'].zones['hand']);refs={session.state.cards[oid].ref for oid in original}
        self.activate(session,self.random_action(session,source,count),['D'])
        self.assertIn('replacement',session.state.pending_decision.kind)
        self.assertEqual(original,set(session.state.players['A'].zones['hand']))
        self.assertEqual(sum(mana.values()),sum(session.state.players['A'].mana_pool.values()))
        self.assert_hand_private(session,'A',original)
        pinned=None
        for _ in range(8):
            decision=session.state.pending_decision
            if decision is None or 'replacement' not in decision.kind:break
            continuation=decision.continuation
            selected=continuation['priority_response']['_random_discard_cost_refs']
            self.assertEqual(count,len(selected));self.assertTrue(set(selected)<=refs)
            if pinned is None:pinned=tuple(selected)
            else:self.assertEqual(pinned,tuple(selected))
            session=self.replay(session,load=True)
            packet=session.packet('pilot:A',full=True)['decision']
            result=session.act('pilot:A',{'action_id':'choose','replacement':packet['ctx']['options'][0]['id']})
            self.assertTrue(result.ok,result.summary)
        else:self.fail('Random cost replacement choices did not finish')
        remaining=set(session.state.players['A'].zones['hand']);paid=original-remaining
        self.assertEqual(count,len(paid));self.assertEqual(set(pinned),{session.state.cards[oid].ref for oid in paid})
        self.assertTrue(all(session.state.cards[oid].zone=='exile' for oid in paid))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(40,session.state.players['D'].life)
        self.resolve(session);self.assertEqual(40-damage,session.state.players['D'].life)
        self.assert_hand_private(session,'A',remaining);self.replay(session,load=True)

    def test_actual_mage_control_change_pays_new_controller_hand_and_taps_before_damage(self):
        session=self.session(407020);engine=session.engine
        source=self.add(engine,'Mage il-Vec',zone='hand',ref='actual-random-mage')
        control=self.add(engine,'Generic Event Subject Control',seat='D',zone='hand',ref='random-mage-real-control')
        self.add(engine,'Generic Bound Plains',seat='D',ref='random-mage-control-mana')
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'R':1});self.checkpoint(session)
        self.cast(session,'A',source);self.resolve(session)
        self.assertIsNone(self.random_action(session,source,1,required=False))
        self.cast(session,'D',control,[source.ref]);self.resolve(session)
        for _ in range(12):
            if session.state.priority_player=='D':break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        action=self.random_action(session,source,1,actor='D')
        hands={seat:set(player.zones['hand']) for seat,player in session.state.players.items()}
        self.reject(session,'A',action_id=action['id'],targets=['C'])
        self.activate(session,action,['C'],actor='D')
        self.assertTrue(session.state.cards[source.object_id].tapped)
        for seat in 'ABC':self.assertEqual(hands[seat],set(session.state.players[seat].zones['hand']))
        self.assertEqual(1,len(hands['D']-set(session.state.players['D'].zones['hand'])))
        self.assertEqual(40,session.state.players['C'].life)
        session=self.replay(session,load=True);self.resolve(session)
        self.assertEqual(39,session.state.players['C'].life);self.replay(session,load=True)

    def test_exact_random_cost_cannot_pay_with_too_few_cards_despite_mana_and_other_hands(self):
        for count in (0,1):
            with self.subTest(count=count):
                session=self.session(407030+count);engine=session.engine
                source=self.add(engine,'Meteor Storm',ref='actual-unaffordable-random-cost')
                player=session.state.players['A']
                for oid in list(player.zones['hand'])[count:]:
                    player.zones['hand'].remove(oid);player.zones['library'].append(oid);session.state.cards[oid].zone='library'
                self.assert_closed(session,source);self.prepare(session,mana={'C':9,'R':9,'G':9});self.checkpoint(session)
                self.assertIsNone(self.random_action(session,source,2,required=False))
                ability=next(a for a in session.engine._activated_abilities(source) if a.random_discard_payment==2)
                self.reject(session,action_id='activate:'+source.ref+':'+ability.ability_id,targets=['C'])
                self.assertEqual(count,len(session.state.players['A'].zones['hand']))
                self.assertFalse(session.state.stack)


if __name__=='__main__':unittest.main()
