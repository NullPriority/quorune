from __future__ import annotations

"""CR 107.14/602.2: exact fixed energy costs use the activation payer."""

import json
from pathlib import Path
import tempfile
import unittest

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
import test_fixed_tap_payments as tap_actions
import test_self_public_zone_casting as zone_actions


class EnergyActivationCompilerTests(unittest.TestCase):
    def test_withdrawn_energy_activation_capability_prevents_exact_promotion(self):
        from quorune.rules.capabilities import CapabilityRegistry
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(cap for cap in raw['capabilities'] if cap['id']=='activation.energy.fixed_payment').update(
            status='blocked',blockers=['withdrawn'])
        ir=compile_oracle_card(record('Pay {E}: Draw a card.','Artifact'),
            capability_registry=CapabilityRegistry(raw),capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)

    def test_complete_energy_clauses_keep_exact_symbol_or_word_amount_and_cost_ownership(self):
        registry=load_default_capability_registry()
        for cost,amount,tap in (
                ('{T}, Pay {E}',1,True),('Pay {E}{E}',2,False),
                ('{2}{U}, {T}, Pay {E}{E}{E}',3,True),
                ('Pay {E} {E} {E} {E} {E}',5,False),
                ('Pay {E}, Pay {E}{E}',3,False),
                ('{T}, Pay eight {E}',8,True),('Pay ten {E}',10,False)):
            with self.subTest(cost=cost):
                card=record(cost+': Draw a card.','Artifact')
                ir=compile_oracle_card(card,capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0]
                self.assertEqual((amount,tap),(node.cost['energy_payment'],node.cost['tap_source']))
                self.assertEqual([],node.cost['complex_symbols']);self.assertEqual([],node.cost['uncompiled_costs'])
                self.assertIn('activation.energy.fixed_payment',node.capability_dependencies)
                ability=compile_activated_ability_catalog(card)['front'][0]
                self.assertEqual(amount,ability.energy_payment)
                self.assertEqual(ability,ActivatedAbility.from_dict(json.loads(json.dumps(ability.to_dict()))))

    def test_open_mixed_or_stray_energy_symbols_cannot_be_erased_by_a_valid_clause(self):
        registry=load_default_capability_registry()
        for cost in ('Pay {E}{R}','Pay two {E}{E}','Pay X {E}','Pay {E/P}',
                'Pay {E}, {E}','{E}, Pay one {E}','Pay {E} for each creature you control',
                'Pay {E} or {E}{E}','Pay {E}, Pay two {E}{E}'):
            with self.subTest(cost=cost):
                ir=compile_oracle_card(record(cost+': Draw a card.','Artifact'),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status)
                ability=compile_activated_ability_catalog(record(cost+': Draw a card.','Artifact'))['front'][0]
                self.assertFalse(ability.compiled_cost)

    def test_existing_counted_word_energy_payload_and_ordinary_mana_cost_round_trip(self):
        for text,amount in (('Pay eight {E}: Draw a card.',8),('{2}{U}, {T}: Draw a card.',0)):
            ability=compile_activated_ability_catalog(record(text,'Artifact'))['front'][0]
            self.assertEqual(amount,ability.energy_payment)
            self.assertTrue(ability.compiled_cost)
            payload=json.loads(json.dumps(ability.to_dict()))
            self.assertEqual(payload,ActivatedAbility.from_dict(payload).to_dict())


class EnergyActivationRuntimeTests(unittest.TestCase):
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

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name).resolve()/'energy-activation-costs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/fixed-tap-payment-cards.json',
            ROOT/'tests/fixtures/event-player-subject-cards.json',
            ROOT/'tests/fixtures/energy-activation-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Energy activation witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def energy_action(self,session,source,amount,*,actor='A',required=True):
        rows=session.packet('pilot:'+actor,full=True)['decision']['ctx']['legal']['actions']
        action=next((r for r in rows if r['id'].startswith('activate:'+source.ref+':')
            and r['cost_summary'].get('energy',0)==amount),None)
        if required:self.assertIsNotNone(action,f'Missing supported energy-{amount} activation for {source.printed_name}')
        return action

    def activate(self,session,action,targets=(),*,actor='A'):
        result=session.act('pilot:'+actor,{'action_id':action['id'],'targets':list(targets),'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def reject_activation(self,session,action_id,targets=(),*,actor='A'):
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:'+actor,{'action_id':action_id,'targets':list(targets),'pay':'auto'})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_actual_artist_uses_own_energy_without_tap_and_control_change_changes_payer(self):
        session=self.session(406000);engine=session.engine
        source=self.add(engine,'Spontaneous Artist',ref='actual-energy-artist')
        target=self.add(engine,'Generic Bound Body',ref='energy-haste-target')
        control=self.add(engine,'Generic Event Subject Control',seat='D',zone='hand',ref='energy-artist-real-control')
        self.add(engine,'Generic Bound Plains',seat='D',ref='energy-artist-control-mana')
        private=self.add(engine,'Generic Bound Growth',seat='D',zone='hand',ref='energy-artist-private')
        engine.state.players['A'].energy=1;engine.state.players['D'].energy=1;engine.state.players['B'].energy=9
        engine.state.players['A'].poison=2
        self.prepare(session,mana={'C':7});self.checkpoint(session)
        action=self.energy_action(session,source,1)
        self.assert_closed(session,source)
        self.reject_activation(session,action['id'],[target.ref],actor='B')
        mana=dict(session.state.players['A'].mana_pool)
        self.activate(session,action,[target.ref])
        self.assertEqual((0,1,9,2),(session.state.players['A'].energy,session.state.players['D'].energy,
            session.state.players['B'].energy,session.state.players['A'].poison))
        self.assertEqual(mana,dict(session.state.players['A'].mana_pool))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        session=self.replay(session,load=True);self.resolve(session)
        self.assertIn('haste',[k.casefold() for k in session.engine._effective_card_data(session.state.cards[target.object_id])['keywords']])
        self.assertIsNone(self.energy_action(session,source,1,required=False))
        self.reject_activation(session,action['id'],[target.ref])
        self.cast(session,'D',control,[source.ref]);self.resolve(session)
        self.assertEqual('D',session.state.cards[source.object_id].controller)
        for _ in range(12):
            if session.state.priority_player=='D':break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        else:self.fail('New controller did not receive priority')
        action=self.energy_action(session,source,1,actor='D')
        self.reject_activation(session,action['id'],[target.ref],actor='A')
        self.activate(session,action,[target.ref],actor='D');self.resolve(session)
        self.assertEqual((0,0),(session.state.players['A'].energy,session.state.players['D'].energy))
        for viewer in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)

    def test_actual_zealot_tap_cost_needs_haste_and_rejects_illegal_target_without_paying(self):
        session=self.session(406010);engine=session.engine
        source=self.add(engine,'Solstice Zealot',zone='hand',ref='actual-energy-zealot')
        haste=self.add(engine,'Generic Tap Payment Vigilance',zone='hand',ref='energy-zealot-haste')
        target=self.add(engine,'Generic Bound Body',seat='B',ref='energy-zealot-target')
        self.assert_closed(session,source);self.prepare(session,mana={'C':2,'W':1});self.checkpoint(session)
        self.cast(session,'A',source);self.resolve(session)
        self.assertEqual(2,session.state.players['A'].energy)
        self.assertIsNone(self.energy_action(session,source,1,required=False))
        ability=next(a for a in session.engine._activated_abilities(session.state.cards[source.object_id]) if a.energy_payment==1)
        action_id='activate:'+source.ref+':'+ability.ability_id
        self.reject_activation(session,action_id,[target.ref])
        self.cast(session,'A',haste,[source.ref]);self.resolve(session)
        action=self.energy_action(session,source,1)
        self.reject_activation(session,action['id'],['D'])
        self.assertEqual((2,False),(session.state.players['A'].energy,session.state.cards[source.object_id].tapped))
        self.activate(session,action,[target.ref])
        self.assertEqual((1,True,False),(session.state.players['A'].energy,
            session.state.cards[source.object_id].tapped,session.state.cards[target.object_id].tapped))
        session=self.replay(session,load=True);self.resolve(session)
        self.assertTrue(session.state.cards[target.object_id].tapped)
        self.replay(session,load=True)

    def test_actual_turret_energy_persists_phases_and_countering_never_refunds_the_cost(self):
        for counter in (False,True):
            with self.subTest(counter=counter):
                session=self.session(406020+counter);engine=session.engine
                source=self.add(engine,'Consulate Turret',zone='hand',ref='actual-energy-turret')
                untaps=[self.add(engine,'Generic Energy Activation Untap',zone='hand',ref='energy-turret-untap-'+str(i)) for i in range(3)]
                attacker=self.add(engine,'Generic Bound Body',ref='energy-phase-attacker');attacker.acquired_control_turn_count=-1
                stifle=self.add(engine,'Stifle',seat='B',zone='hand',ref='energy-turret-counter')
                self.add(engine,'Island',seat='B',ref='energy-turret-counter-mana')
                self.assert_closed(session,source);self.assert_closed(session,stifle)
                self.prepare(session,mana={'C':4});self.checkpoint(session)
                self.cast(session,'A',source);self.resolve(session)
                for count,untap in enumerate(untaps,1):
                    self.activate(session,self.energy_action(session,source,0));self.resolve(session)
                    self.assertEqual(count,session.state.players['A'].energy)
                    self.cast(session,'A',untap,[source.ref]);self.resolve(session)
                self.assertEqual(1,session.state.players['A'].mana_pool['C'])
                self.advance_to(session,'combat.attackers')
                self.assertEqual((3,0),(session.state.players['A'].energy,sum(session.state.players['A'].mana_pool.values())))
                self.assertTrue(session.act('pilot:A',{'action_id':'attack','attackers':{}}).ok)
                action=self.energy_action(session,source,3);self.activate(session,action,['C'])
                self.assertEqual((0,True),(session.state.players['A'].energy,session.state.cards[source.object_id].tapped))
                activated=session.state.stack[-1]
                session=self.replay(session,load=True)
                if counter:self.cast(session,'B',stifle,[activated.ref])
                self.resolve(session)
                self.assertEqual(40 if counter else 38,session.state.players['C'].life)
                self.assertEqual((0,True),(session.state.players['A'].energy,session.state.cards[source.object_id].tapped))
                self.replay(session,load=True)


if __name__=='__main__':unittest.main()
