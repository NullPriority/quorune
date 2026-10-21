from __future__ import annotations

"""CR608.2h/101.3: random return reads the current own eligible graveyard."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_self_exile_return_spells as controls
import test_scheduled_player_triggers as upkeep_controls


class RandomGraveyardReturnTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.SelfExileReturnSpellTests.session
    add=controls.SelfExileReturnSpellTests.add
    prepare=controls.SelfExileReturnSpellTests.prepare
    checkpoint=controls.SelfExileReturnSpellTests.checkpoint
    resolve=controls.SelfExileReturnSpellTests.resolve
    replay=controls.SelfExileReturnSpellTests.replay
    binding=controls.SelfExileReturnSpellTests.binding
    actions=controls.SelfExileReturnSpellTests.actions
    private_replay=controls.SelfExileReturnSpellTests.private_replay
    assert_known_returns=controls.SelfExileReturnSpellTests.assert_known_returns
    cast=controls.SelfExileReturnSpellTests.cast
    advance=controls.SelfExileReturnSpellTests.advance
    top=controls.SelfExileReturnSpellTests.top
    priority=controls.SelfExileReturnSpellTests.priority

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'random-graveyard.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/random-graveyard-return-cards.json',ROOT/'tests/fixtures/source-keyword-choice-cards.json',
            ROOT/'tests/fixtures/announced-x-additional-cost-cards.json',ROOT/'tests/fixtures/commander-characteristic-grants.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Random graveyard',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def original_return(self,name,seed,payment,*,x=None):
        session=self.session(seed);source=self.add(session.engine,name,zone='hand')
        pool=[self.add(session.engine,kind,zone='graveyard',ref='random-own-'+str(i))
            for i,kind in enumerate(('Grizzly Bears','Generic Bound Growth','Generic Bound Plains','Grizzly Bears'))]
        foreign=[self.add(session.engine,'Grizzly Bears',seat=s,zone='graveyard',ref='random-foreign-'+s) for s in 'BCD']
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original '+name+' must admit')
        self.prepare(session,mana=payment);self.checkpoint(session)
        command={'action_id':'cast:'+source.ref,'pay':'manual','payment':payment}
        if x is not None:command['x']=x
        self.assertTrue(any(a['id']==command['action_id'] for a in self.actions(session)))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in pool))
        if x is not None:self.assertEqual(x,session.state.stack[-1].x_value)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        returned=[c for c in pool if session.state.cards[c.object_id].zone=='hand']
        self.assertEqual(2,len(returned));self.assertEqual(2,len({c.object_id for c in returned}))
        self.assert_known_returns(session,returned)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in foreign))
        self.assertEqual('exile' if x is not None else 'graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_actual_make_a_wish_returns_two_distinct_random_own_cards_without_targets(self):
        self.original_return('Make a Wish',751200,{'G':1,'C':3})

    def test_actual_reap_the_past_pays_announced_x_then_returns_random_cards_and_self_exiles(self):
        self.original_return('Reap the Past',751201,{'R':1,'G':1,'C':2},x=2)

    def test_actual_ghoulraiser_cast_returns_only_a_current_own_zombie(self):
        session=self.session(751210);source=self.add(session.engine,'Ghoulraiser',zone='hand')
        eligible=[self.add(session.engine,'Ghoulraiser',zone='graveyard',ref='zombie-'+str(i)) for i in range(2)]
        wrong=self.add(session.engine,'Grizzly Bears',zone='graveyard')
        foreign=self.add(session.engine,'Ghoulraiser',seat='B',zone='graveyard',ref='foreign-zombie')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session,mana={'B':2,'C':1});self.checkpoint(session);self.cast(session,'A',source,payment={'B':2,'C':1})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        returned=[c for c in eligible if session.state.cards[c.object_id].zone=='hand'];self.assertEqual(1,len(returned))
        self.assert_known_returns(session,returned);self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        for c in (wrong,foreign):self.assertEqual('graveyard',session.state.cards[c.object_id].zone)
        self.private_replay(session,private)

    def test_actual_fengraf_pays_tap_sacrifice_and_mana_before_random_creature_return(self):
        session=self.session(751211);source=self.add(session.engine,'Haunted Fengraf')
        eligible=[self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='fengraf-body-'+str(i)) for i in range(2)]
        wrong=self.add(session.engine,'Generic Bound Growth',zone='graveyard')
        foreign=self.add(session.engine,'Grizzly Bears',seat='B',zone='graveyard',ref='foreign-fengraf-body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='fengraf-private-hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session,mana={'C':3});self.checkpoint(session)
        offered=[a for a in self.actions(session) if a['id'].startswith('activate:'+source.ref+':') and not a.get('mana_ability')]
        self.assertEqual(1,len(offered));command={'action_id':offered[0]['id'],'pay':'manual','payment':{'C':3}}
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        returned=[c for c in eligible if session.state.cards[c.object_id].zone=='hand'];self.assertEqual(1,len(returned))
        self.assert_known_returns(session,returned)
        for c in (source,wrong,foreign):self.assertEqual('graveyard',session.state.cards[c.object_id].zone)
        self.private_replay(session,private)

    def test_actual_woodland_sleuth_uses_a_real_foreign_death_but_own_creature_pool(self):
        session=self.session(751212);source=self.add(session.engine,'Woodland Sleuth',zone='hand')
        eligible=[self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='sleuth-body-'+str(i)) for i in range(2)]
        victim=self.add(session.engine,'Grizzly Bears',seat='B',ref='morbid-victim')
        bolt=self.add(session.engine,'Lightning Bolt',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session,mana={'R':1,'G':1,'C':3});self.checkpoint(session)
        self.cast(session,'A',bolt,[victim.ref],{'R':1});self.assertIsNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[victim.object_id].zone)
        self.cast(session,'A',source,payment={'G':1,'C':3});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        returned=[c for c in eligible if session.state.cards[c.object_id].zone=='hand'];self.assertEqual(1,len(returned))
        self.assert_known_returns(session,returned);self.assertEqual('graveyard',session.state.cards[victim.object_id].zone)
        self.private_replay(session,private)

    def upkeep_return(self,name,seed,*,creatures=False,count=2):
        session=self.session(seed);source=self.add(session.engine,name)
        kind='Grizzly Bears' if creatures else 'Generic Bound Growth'
        eligible=[self.add(session.engine,kind,zone='graveyard',ref='upkeep-pool-'+str(i)) for i in range(count)]
        foreign=self.add(session.engine,kind,seat='B',zone='graveyard',ref='foreign-upkeep-pool')
        body=self.add(session.engine,'Grizzly Bears',ref='upkeep-response-body')
        wrong=self.add(session.engine,'Generic Bound Plains' if creatures else 'Grizzly Bears',zone='graveyard',ref='wrong-upkeep-pool')
        spell=self.add(session.engine,'Generic Bound Growth',zone='hand',ref='after-return-spell')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        upkeep_controls.ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap(self,session,'A')
        self.assertIsNone(self.resolve(session))
        returned=[c for c in eligible if session.state.cards[c.object_id].zone=='hand'];self.assertEqual(1,len(returned))
        self.assert_known_returns(session,returned)
        for c in (wrong,foreign):self.assertEqual('graveyard',session.state.cards[c.object_id].zone)
        self.private_replay(session,private)
        return session,source,body,spell,private

    def test_actual_charmbreaker_upkeep_filters_spells_and_retains_its_cast_bonus(self):
        session,source,body,spell,private=self.upkeep_return('Charmbreaker Devils',751213)
        self.prepare(session,mana={'G':1});self.checkpoint(session)
        self.cast(session,'A',spell,[body.ref],{'G':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(8,session.engine._numeric_stat(source.object_id,'power'));self.private_replay(session,private)

    def test_actual_viconia_upkeep_requires_four_creatures_and_retains_background(self):
        self.upkeep_return('Viconia, Drow Apostate',751214,creatures=True,count=4)

    def test_actual_viconia_background_pair_casts_both_commanders_and_keeps_random_upkeep(self):
        self.deck=DeckDefinition('Viconia paired',[DeckEntry('Viconia, Drow Apostate',1,'commander'),
            DeckEntry('Flaming Fist',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Viconia, Drow Apostate','Flaming Fist'])
        session=self.session(751217);del self.deck
        commanders=[session.state.cards[oid] for oid in session.state.players['A'].zones['command']]
        self.assertEqual(2,len(commanders));source=next(c for c in commanders if c.printed_name=='Viconia, Drow Apostate')
        background=next(c for c in commanders if c.printed_name=='Flaming Fist')
        pool=[self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='paired-pool-'+str(i)) for i in range(4)]
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        for c in commanders:self.assertTrue(self.binding(session,c)['strict_capability_ready'])
        self.prepare(session,mana={'B':1,'W':1,'C':4});self.checkpoint(session)
        self.cast(session,'A',source,payment={'B':1,'C':2});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',background,payment={'W':1,'C':2});self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='battlefield' for c in commanders));self.private_replay(session,private)
        upkeep_controls.ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap(self,session,'A');self.assertIsNone(self.resolve(session))
        returned=[c for c in pool if session.state.cards[c.object_id].zone=='hand'];self.assertEqual(1,len(returned))
        self.assert_known_returns(session,returned);self.assertTrue(session.state.cards[source.object_id].is_commander)
        self.assertEqual('battlefield',session.state.cards[background.object_id].zone);self.private_replay(session,private)

    def test_actual_exalted_flamer_upkeep_filters_spells_and_retains_opponent_damage(self):
        session,source,body,spell,private=self.upkeep_return('Exalted Flamer of Tzeentch',751215)
        self.prepare(session,mana={'G':1});self.checkpoint(session)
        self.cast(session,'A',spell,[body.ref],{'G':1});self.assertIsNone(self.resolve(session))
        self.assertEqual([39,39,39],[session.state.players[s].life for s in 'BCD']);self.private_replay(session,private)

    def test_actual_surreal_memoir_returns_again_on_rebound_without_a_second_price(self):
        session=self.session(751216);source=self.add(session.engine,'Surreal Memoir',zone='hand')
        pool=[self.add(session.engine,'Generic Bound Growth',zone='graveyard',ref='rebound-instant-'+str(i)) for i in range(2)]
        wrong=self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='rebound-wrong-type')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='rebound-private')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session,mana={'R':1,'C':3});self.checkpoint(session);self.cast(session,'A',source,payment={'R':1,'C':3})
        self.assertIsNone(self.resolve(session));self.assertEqual('exile',session.state.cards[source.object_id].zone)
        self.assertEqual(1,sum(session.state.cards[c.object_id].zone=='hand' for c in pool))
        for _ in range(4):
            turn=session.state.turn_sequence
            self.advance(session,lambda s:s.state.turn_sequence>turn)
            if session.state.active_player=='A':break
        self.assertEqual('A',session.state.active_player)
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='priority' and bool(s.state.stack))
        pending=self.resolve(session);self.assertIsNotNone(pending);self.assertEqual('selection.exile_cast',pending.kind)
        session=self.replay(session,load=True)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action':'choose','choice':'cast'}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        chosen=session.act('pilot:A',{'action':'choose','choice':'cast'});self.assertTrue(chosen.ok,chosen.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assert_known_returns(session,pool);self.assertEqual('graveyard',session.state.cards[wrong.object_id].zone)
        self.private_replay(session,private)

    def test_zero_empty_and_undersized_pools_are_legal_and_do_not_take_foreign_cards(self):
        for i,(name,available,x,payment,expected) in enumerate((('Make a Wish',0,None,{'G':1,'C':3},0),
                ('Make a Wish',1,None,{'G':1,'C':3},1),('Reap the Past',2,0,{'R':1,'G':1},0),
                ('Reap the Past',1,4,{'R':1,'G':1,'C':4},1))):
            with self.subTest(name=name,available=available,x=x):
                session=self.session(751220+i);source=self.add(session.engine,name,zone='hand')
                pool=[self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='small-pool-'+str(n)) for n in range(available)]
                foreign=self.add(session.engine,'Grizzly Bears',seat='B',zone='graveyard',ref='foreign-small-pool')
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                self.prepare(session,mana=payment);self.checkpoint(session)
                command={'action_id':'cast:'+source.ref,'pay':'manual','payment':payment}
                if x is not None:command['x']=x
                result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                returned=[c for c in pool if session.state.cards[c.object_id].zone=='hand'];self.assertEqual(expected,len(returned))
                self.assert_known_returns(session,returned);self.assertEqual('graveyard',session.state.cards[foreign.object_id].zone)
                self.assertEqual('exile' if x is not None else 'graveyard',session.state.cards[source.object_id].zone);self.private_replay(session,private)

    def test_current_pool_is_read_after_a_real_instant_enters_graveyard_before_resolution(self):
        session=self.session(751230);source=self.add(session.engine,'Make a Wish',zone='hand')
        old=self.add(session.engine,'Grizzly Bears',zone='graveyard')
        response=self.add(session.engine,'Generic Bound Growth',zone='hand')
        body=self.add(session.engine,'Grizzly Bears',ref='late-pool-body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='late-pool-private')
        self.prepare(session,mana={'G':2,'C':3});self.checkpoint(session);self.cast(session,'A',source,payment={'G':1,'C':3})
        self.cast(session,'A',response,[body.ref],{'G':1});self.top(session)
        self.assertEqual('graveyard',session.state.cards[response.object_id].zone)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assert_known_returns(session,[old,response])
        self.private_replay(session,private)

    def test_random_selection_is_untargeted_and_commander_replacement_does_not_reroll(self):
        session=self.session(751231);source=self.add(session.engine,'Make a Wish',zone='hand')
        commander_id=session.state.players['A'].zones['command'][0];commander=session.state.cards[commander_id]
        other=self.add(session.engine,'Grizzly Bears',zone='graveyard')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'G':1,'C':3});session.engine.move_card(commander_id,'graveyard',reason='Fixture random commander boundary')
        session.engine.permissions.invalidate_current();session.state.pending_decision=None;self.assertTrue(session.engine._stabilize())
        kept=session.act('pilot:A',{'a':'choose','choice':'remain'});self.assertTrue(kept.ok,kept.summary)
        self.prepare(session,mana={'G':1,'C':3});self.checkpoint(session);self.cast(session,'A',source,payment={'G':1,'C':3})
        pending=self.resolve(session);self.assertIsNotNone(pending);self.assertEqual('replacement.order',pending.kind)
        self.assertEqual('graveyard',session.state.cards[other.object_id].zone)
        session=self.replay(session,load=True);options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        apply=[o for o in options if not o.get('decline')];self.assertEqual(1,len(apply));before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'choose','replacement':apply[0]['id']}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{'action_id':'choose','replacement':'unoffered-random-choice'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        chosen=session.act('pilot:A',{'action_id':'choose','replacement':apply[0]['id']});self.assertTrue(chosen.ok,chosen.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('command',session.state.cards[commander_id].zone)
        self.assert_known_returns(session,[other]);self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.private_replay(session,private)

    def test_intervening_conditions_do_not_create_returns_before_morbid_or_four_creatures(self):
        for i,name in enumerate(('Woodland Sleuth','Viconia, Drow Apostate')):
            with self.subTest(name=name):
                session=self.session(751240+i);source=self.add(session.engine,name,zone='hand' if i==0 else 'battlefield')
                pool=[self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='below-condition-'+str(n)) for n in range(3)]
                private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.assertTrue(self.binding(session,source)['strict_capability_ready'])
                if i==0:
                    self.prepare(session,mana={'G':1,'C':3});self.checkpoint(session)
                    self.cast(session,'A',source,payment={'G':1,'C':3})
                else:upkeep_controls.ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap(self,session,'A')
                self.assertIsNone(self.resolve(session));self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in pool))
                self.private_replay(session,private)

    def test_upkeep_controller_is_locked_after_actual_source_control_change(self):
        session=self.session(751242);source=self.add(session.engine,'Charmbreaker Devils')
        own=self.add(session.engine,'Generic Bound Growth',zone='graveyard',ref='locked-own-instant')
        foreign=self.add(session.engine,'Generic Bound Growth',seat='B',zone='graveyard',ref='locked-foreign-instant')
        steal=self.add(session.engine,'Word of Seizing',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='locked-private-hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.assertTrue(self.binding(session,steal)['strict_capability_ready'])
        upkeep_controls.ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap(self,session,'A')
        self.assertEqual(1,len(session.state.stack));self.assertEqual('A',session.state.stack[-1].controller)
        session.state.players['B'].mana_pool.update({'R':2,'C':3});self.checkpoint(session)
        self.cast(session,'B',steal,[source.ref],{'R':2,'C':3});self.top(session)
        self.assertEqual('B',session.state.cards[source.object_id].controller)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assert_known_returns(session,[own])
        self.assertEqual('graveyard',session.state.cards[foreign.object_id].zone);self.private_replay(session,private)

    def test_countering_keeps_reap_price_paid_and_prevents_random_return_and_self_exile(self):
        session=self.session(751243);source=self.add(session.engine,'Reap the Past',zone='hand')
        pool=[self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='countered-random-'+str(i)) for i in range(2)]
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1,'G':1,'C':2});session.state.players['B'].mana_pool.update({'U':2});self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'x':2,'pay':'manual','payment':{'R':1,'G':1,'C':2}});self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',counter,[session.state.stack[-1].ref],{'U':2});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in pool))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.private_replay(session,private)

    def test_canonically_verified_random_owner_withdrawal_rejects_full_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        cap='zone.return.random_own_graveyard'
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed=deepcopy(raw)
        next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn random selection owner'])
        registry=CapabilityRegistry(changed);declarations=json.loads((ROOT/'platform/capability-evidence-declarations.json').read_text(encoding='utf-8'))
        index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in ('Make a Wish','Reap the Past','Ghoulraiser','Haunted Fengraf','Woodland Sleuth','Charmbreaker Devils','Viconia, Drow Apostate','Exalted Flamer of Tzeentch','Surreal Memoir'):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,name)
            blockers=[b for f in ir.faces for r in f.residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers))
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_random_return_subject_and_runtime_descriptors_are_closed_and_capability_bound(self):
        from quorune.rules.random_graveyard_returns import RandomGraveyardReturnSpec,CAPABILITY
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.compiler.random_graveyard_returns import random_graveyard_return_template
        spec=RandomGraveyardReturnSpec('creature card')
        self.assertEqual(spec,RandomGraveyardReturnSpec.from_dict(spec.to_dict()))
        for data in ({**spec.to_dict(),'schema_version':True},{**spec.to_dict(),'extra':1},
                {**spec.to_dict(),'subject':'opponent card'},{**spec.to_dict(),'subtype':'NotACreatureType'}):
            with self.assertRaises((TypeError,ValueError)):RandomGraveyardReturnSpec.from_dict(data)
        for text in ('Return two target cards at random from your graveyard to your hand.',
                "Return two cards at random from an opponent's graveyard to your hand.",
                'Return two cards at random from your graveyard to the battlefield.',
                'Return two cards at random from your graveyard to your hand, then draw a card.'):
            self.assertIsNone(random_graveyard_return_template(text),text)
        session=self.session(751202);source=self.add(session.engine,'Make a Wish',zone='hand')
        programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies]
        self.assertEqual(1,len(programs));program=programs[0]
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
        stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
        for effect in ({**program.effects[0],'extra':True},{**program.effects[0],'count':True},
                {**program.effects[0],'selection':{**spec.to_dict(),'subject':'opponent card'}}):
            bad=replace(program,effects=[effect])
            self.assertFalse(bind_semantic_program_runtime(bad,capability_registry=self.registry,profile='commander_review')['strict'])

    def test_fresh_random_return_producer_sampling_and_commit_omissions_are_killed(self):
        method='test_actual_make_a_wish_returns_two_distinct_random_own_cards_without_targets'
        cases=(('quorune.compiler.random_graveyard_returns.random_graveyard_return_template',lambda *a,**k:None),
            ('quorune.rules.random_graveyard_returns.sample_identity_rows',lambda *a,**k:()),
            ('quorune.semantic_choices.intent_host.SemanticChoiceIntentHostMixin.move_objects_simultaneously_intent',lambda *a,**k:()))
        for owner,mutation in cases:
            with patch(owner,mutation):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        model='test_random_return_subject_and_runtime_descriptors_are_closed_and_capability_bound'
        with patch('quorune.card_programs.runtime_requirements.random_graveyard_return_runtime_dependencies',lambda *a:()):
            with self.assertRaises(AssertionError):getattr(type(self)(model),model)()
