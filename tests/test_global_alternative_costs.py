from __future__ import annotations

"""CR118.9/107.3b: a current permanent may supply one fixed alternate mana price."""

from pathlib import Path
from copy import deepcopy
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_provoke_rules as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Fist of Suns','Jodah, Archmage Eternal','Rooftop Storm','Omniscience','Dracogenesis')
CAPABILITY='casting.alternative_cost.fixed_battlefield_grant'


class GlobalAlternativeCostTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ProvokeTests.session
    add=controls.ProvokeTests.add
    prepare=controls.ProvokeTests.prepare
    checkpoint=controls.ProvokeTests.checkpoint
    resolve=controls.ProvokeTests.resolve
    replay=controls.ProvokeTests.replay
    binding=controls.ProvokeTests.binding
    private_replay=controls.ProvokeTests.private_replay
    priority=controls.ProvokeTests.priority
    actions=controls.ProvokeTests.actions
    advance=controls.ProvokeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'global-alternative-costs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/global-alternative-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Global alternative costs',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_complete_global_alternative_cost_originals_have_normal_admission(self):
        for i,name in enumerate(NAMES):
            session=self.session(760010+i);source=self.add(session.engine,name,zone='hand')
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            bound=self.binding(session,source)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))

    def cast_row(self,session,card,seat='A'):
        packet=session.packet('pilot:'+seat,full=True)
        decision=packet.get('decision') or {}
        actions=decision.get('ctx',{}).get('legal',{}).get('actions',[])
        return next((r for r in actions if r['id']=='cast:'+card.ref),None)

    def grant_options(self,session,card,seat='A'):
        row=self.cast_row(session,card,seat)
        return [o for o in row.get('cost_options',[]) if o['id'].startswith('battlefield-alternative:')] if row else []

    def cast(self,session,card,payment,targets=(),*,grant=False,seat='A',option=None,**choices):
        self.priority(session,seat)
        row=self.cast_row(session,card,seat);self.assertIsNotNone(row,card.printed_name)
        command={'action_id':row['id'],'pay':'manual','payment':payment,'targets':list(targets),**choices}
        if grant:
            rows=self.grant_options(session,card,seat)
            self.assertEqual(1,len(rows),rows);command['cost_option']=rows[0]['id']
        if option is not None:command['cost_option']=option
        self.reject(session,command,principal='pilot:D')
        result=session.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def reject(self,session,command,*,principal='pilot:A'):
        before=authoritative_state_hash(session.state)
        result=session.act(principal,command);self.assertFalse(result.ok,result.summary)
        self.assertEqual(before,authoritative_state_hash(session.state))

    def source_paid_witness(self,name,cost,seed):
        session=self.session(seed);source=self.add(session.engine,name,zone='hand')
        bolt=self.add(session.engine,'Lightning Bolt',zone='hand',ref='global-five-bolt')
        normal=self.add(session.engine,'Lightning Bolt',zone='hand',ref='normal-one-bolt')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        pool={'W':1,'U':1,'B':1,'R':2,'G':1}
        for key,amount in cost.items():pool[key]=pool.get(key,0)+amount
        self.prepare(session,mana=pool);self.checkpoint(session)
        self.cast(session,source,cost);self.assertIsNone(self.resolve(session))
        if name=='Jodah, Archmage Eternal':self.assertIn('flying',session.engine._combat_keywords(source))
        rows=self.grant_options(session,bolt);self.assertEqual(1,len(rows))
        self.assertEqual({'W':1,'U':1,'B':1,'R':1,'G':1},
            {k:v for k,v in rows[0]['requirements'].items() if v})
        self.cast(session,bolt,{'W':1,'U':1,'B':1,'R':1,'G':1},['B'],grant=True)
        self.assertEqual(1,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(37,session.state.players['B'].life)
        self.cast(session,session.state.cards[normal.object_id],{'R':1},['B'])
        self.assertIsNone(self.resolve(session));self.assertEqual(34,session.state.players['B'].life)
        self.private_replay(session,private)

    def test_actual_fist_offers_optional_wubrg_and_keeps_printed_payment(self):
        self.source_paid_witness('Fist of Suns',{'C':3},760030)

    def test_actual_jodah_keeps_flying_and_supplies_the_same_optional_price(self):
        self.source_paid_witness('Jodah, Archmage Eternal',{'C':1,'U':1,'R':1,'W':1},760031)

    def test_actual_rooftop_only_discounts_own_zombie_creatures(self):
        session=self.session(760032);source=self.add(session.engine,'Rooftop Storm',zone='hand')
        zombie=self.add(session.engine,'Walking Corpse',zone='hand')
        bolt=self.add(session.engine,'Lightning Bolt',zone='hand')
        foreign=self.add(session.engine,'Walking Corpse',seat='B',zone='hand',ref='foreign-corpse')
        self.prepare(session,mana={'C':5,'U':1,'R':1});self.checkpoint(session)
        self.cast(session,source,{'C':5,'U':1});self.assertIsNone(self.resolve(session))
        self.assertEqual([],self.grant_options(session,bolt))
        self.cast(session,zombie,{},grant=True)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[zombie.object_id].zone)
        self.cast(session,session.state.cards[bolt.object_id],{'R':1},['B'])
        self.assertIsNone(self.resolve(session));self.assertEqual(37,session.state.players['B'].life)
        self.priority(session,'B');self.assertEqual([],self.grant_options(session,foreign,'B'))
        self.replay(session)

    def test_actual_omniscience_casts_no_cost_vision_and_draws_privately(self):
        session=self.session(760033);source=self.add(session.engine,'Omniscience',zone='hand')
        vision=self.add(session.engine,'Ancestral Vision',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':7,'U':3});self.checkpoint(session)
        self.cast(session,source,{'C':7,'U':3});self.assertIsNone(self.resolve(session))
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,vision,{},['A'],grant=True)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(before+2,len(session.state.players['A'].zones['hand']))
        self.assertEqual('graveyard',session.state.cards[vision.object_id].zone)
        self.private_replay(session,private)

    def test_actual_dracogenesis_keeps_dragon_characteristics_and_ordinary_actions(self):
        session=self.session(760034);source=self.add(session.engine,'Dracogenesis',zone='hand')
        dragon=self.add(session.engine,'Volcanic Dragon',zone='hand')
        bolt=self.add(session.engine,'Lightning Bolt',zone='hand')
        self.prepare(session,mana={'C':6,'R':4});self.checkpoint(session)
        self.cast(session,source,{'C':6,'R':2});self.assertIsNone(self.resolve(session))
        self.assertEqual([],self.grant_options(session,bolt))
        self.cast(session,dragon,{},grant=True);self.assertIsNone(self.resolve(session))
        dragon=session.state.cards[dragon.object_id]
        self.assertIn('flying',session.engine._combat_keywords(dragon))
        self.assertIn('haste',session.engine._combat_keywords(dragon))
        self.assertEqual(4,session.engine._numeric_stat(dragon.object_id,'power'))
        self.cast(session,bolt,{'R':1},['B']);self.assertIsNone(self.resolve(session))
        self.assertEqual(37,session.state.players['B'].life)
        self.advance(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:A',{'action_id':'attack','attacks':{dragon.ref:'B'}})
        self.assertTrue(result.ok,result.summary)
        self.advance(session,lambda s:s.state.phase=='postcombat_main')
        self.assertEqual(33,session.state.players['B'].life)
        self.replay(session,load=True)

    def test_actual_alternative_keeps_mandatory_declared_x_cost_and_increase(self):
        session=self.session(760040);self.add(session.engine,'Omniscience')
        self.add(session.engine,'Sphere of Resistance')
        spell=self.add(session.engine,'Sickening Dreams',zone='hand')
        cost=self.add(session.engine,'Lightning Bolt',zone='hand')
        self.prepare(session,mana={'C':1});self.checkpoint(session)
        self.cast(session,spell,{'C':1},grant=True,x=1,discard_cards=[cost.ref])
        self.assertEqual('graveyard',cost.zone)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual([39]*4,[session.state.players[seat].life for seat in 'ABCD'])
        self.replay(session)

    def test_actual_alternative_preserves_optional_kicker_price_and_effect(self):
        session=self.session(760041);self.add(session.engine,'Omniscience')
        spell=self.add(session.engine,'Burst Lightning',zone='hand')
        self.prepare(session,mana={'C':4,'R':1});self.checkpoint(session)
        options=self.grant_options(session,spell)
        kicked=[o for o in options if 'kicked' in o['id']]
        self.assertEqual(1,len(kicked),options)
        ids=[o['id'] for o in self.cast_row(session,spell)['cost_options']]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertIn('kicked',ids)
        self.cast(session,spell,{'C':4},['B'],option=kicked[0]['id'])
        self.assertEqual(1,session.state.players['A'].mana_pool['R'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(36,session.state.players['B'].life)
        self.replay(session)

    def test_actual_alternative_preserves_nonmana_buyback_payment_and_return(self):
        session=self.session(760067);self.add(session.engine,'Omniscience')
        spell=self.add(session.engine,'Slaughter',zone='hand')
        first=self.add(session.engine,'Generic Bound Body',seat='B',ref='buyback-first-body')
        second=self.add(session.engine,'Generic Bound Body',seat='B',ref='buyback-second-body')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        options=self.grant_options(session,spell)
        buyback=[o for o in options if 'buyback' in o['id']]
        self.assertEqual(1,len(buyback),options)
        self.cast(session,spell,{},[first.ref],option=buyback[0]['id'])
        self.assertEqual(36,session.state.players['A'].life)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(('hand','graveyard'),(session.state.cards[spell.object_id].zone,session.state.cards[first.object_id].zone))
        spell=session.state.cards[spell.object_id]
        plain=[o for o in self.grant_options(session,spell) if 'buyback' not in o['id']]
        self.assertEqual(1,len(plain))
        self.cast(session,spell,{},[second.ref],option=plain[0]['id'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(('graveyard','graveyard',36),(spell.zone,session.state.cards[second.object_id].zone,session.state.players['A'].life))
        self.private_replay(session,private)

    def test_actual_mana_x_is_zero_and_positive_forgery_rolls_back(self):
        session=self.session(760042);self.add(session.engine,'Omniscience')
        spell=self.add(session.engine,'Blaze',zone='hand')
        self.prepare(session);self.checkpoint(session)
        options=self.grant_options(session,spell);self.assertEqual(1,len(options),options)
        command={'action_id':'cast:'+spell.ref,'cost_option':options[0]['id'],'pay':'manual',
            'payment':{},'x':5,'targets':['B']}
        self.reject(session,command)
        self.cast(session,session.state.cards[spell.object_id],{},['B'],option=options[0]['id'],x=0)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(40,session.state.players['B'].life)
        self.replay(session)

    def test_actual_source_ability_loss_removes_future_alternative_offers(self):
        session=self.session(760043);source=self.add(session.engine,'Jodah, Archmage Eternal')
        humble=self.add(session.engine,'Humble',zone='hand')
        bolt=self.add(session.engine,'Lightning Bolt',zone='hand')
        self.prepare(session,mana={'C':1,'W':2,'U':1,'B':1,'R':1,'G':1});self.checkpoint(session)
        options=self.grant_options(session,bolt);self.assertEqual(1,len(options))
        self.cast(session,humble,{'C':1,'W':1},[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual([],self.grant_options(session,bolt))
        command={'action_id':'cast:'+bolt.ref,'cost_option':options[0]['id'],'pay':'auto','targets':['B']}
        self.reject(session,command)
        self.cast(session,session.state.cards[bolt.object_id],{'R':1},['B']);self.assertIsNone(self.resolve(session))
        self.assertEqual(37,session.state.players['B'].life)
        self.replay(session,load=True)

    def test_actual_battlefield_grants_do_not_create_graveyard_permission(self):
        session=self.session(760044);self.add(session.engine,'Omniscience')
        legal=self.add(session.engine,'Lightning Bolt',zone='hand',ref='global-hand-bolt')
        foreign_zone=self.add(session.engine,'Lightning Bolt',zone='graveyard',ref='global-grave-bolt')
        self.prepare(session);self.checkpoint(session)
        options=self.grant_options(session,legal);self.assertEqual(1,len(options))
        self.assertIsNone(self.cast_row(session,foreign_zone))
        self.reject(session,{'action_id':'cast:'+foreign_zone.ref,'cost_option':options[0]['id'],
            'pay':'manual','payment':{},'targets':['B']})
        self.cast(session,session.state.cards[legal.object_id],{},['B'],grant=True)
        self.assertIsNone(self.resolve(session));self.assertEqual(37,session.state.players['B'].life)
        self.replay(session,load=True)

    def test_actual_global_price_preserves_commander_tax(self):
        session=self.session(760060);self.add(session.engine,'Fist of Suns')
        commanders=[session.state.cards[i] for i in session.state.players['A'].zones['command']
            if session.state.cards[i].is_commander]
        self.assertEqual(1,len(commanders));commander=commanders[0]
        session.state.players['A'].commander_casts[commander.oracle_id]=1
        self.prepare(session,mana={'C':2,'W':1,'U':1,'B':1,'R':1,'G':1});self.checkpoint(session)
        options=self.grant_options(session,commander);self.assertEqual(1,len(options))
        self.assertEqual(2,options[0]['requirements']['GENERIC'])
        self.cast(session,commander,{'C':2,'W':1,'U':1,'B':1,'R':1,'G':1},grant=True)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[commander.object_id].zone)
        self.assertEqual(2,session.state.players['A'].commander_casts[commander.oracle_id])
        self.replay(session)

    def test_actual_flashback_only_permission_excludes_other_alternatives(self):
        session=self.session(760061);self.add(session.engine,'Fist of Suns')
        spell=self.add(session.engine,'Think Twice',zone='graveyard')
        hand=self.add(session.engine,'Lightning Bolt',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':2,'W':1,'U':2,'B':1,'R':1,'G':1});self.checkpoint(session)
        hand_options=self.grant_options(session,hand);self.assertEqual(1,len(hand_options))
        self.assertEqual([],self.grant_options(session,spell))
        self.reject(session,{'action_id':'cast:'+spell.ref,'cost_option':hand_options[0]['id'],
            'pay':'manual','payment':{'W':1,'U':1,'B':1,'R':1,'G':1}})
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,session.state.cards[spell.object_id],{'C':2,'U':1},option='flashback')
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[spell.object_id].zone)
        self.assertEqual(before+1,len(session.state.players['A'].zones['hand']))
        self.private_replay(session,private)

    def test_actual_ordinary_graveyard_permission_can_use_rooftop(self):
        session=self.session(760062);self.add(session.engine,'Rooftop Storm')
        self.add(session.engine,'Walking Corpse')
        spell=self.add(session.engine,'Gravecrawler',zone='graveyard')
        self.prepare(session);self.checkpoint(session)
        self.cast(session,spell,{},grant=True)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[spell.object_id].zone)
        self.assertEqual(2,session.engine._numeric_stat(spell.object_id,'power'))
        self.assertEqual(1,session.engine._numeric_stat(spell.object_id,'toughness'))
        self.replay(session)

    def test_actual_impulse_exile_uses_fist_and_not_hand_only_omniscience(self):
        session=self.session(760063);self.add(session.engine,'Fist of Suns');self.add(session.engine,'Omniscience')
        impulse=self.add(session.engine,'Reckless Impulse',zone='hand')
        second=self.add(session.engine,'Generic Bound Body',zone='library',ref='impulse-second')
        bolt=self.add(session.engine,'Lightning Bolt',zone='library',ref='impulse-top')
        self.prepare(session,mana={'C':1,'W':1,'U':1,'B':1,'R':2,'G':1});self.checkpoint(session)
        self.cast(session,impulse,{'C':1,'R':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(('exile','exile'),(bolt.zone,second.zone))
        options=self.grant_options(session,bolt);self.assertEqual(1,len(options))
        self.assertEqual(5,sum(options[0]['requirements'].values()))
        self.cast(session,bolt,{'W':1,'U':1,'B':1,'R':1,'G':1},['B'],grant=True)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[bolt.object_id].zone)
        self.assertEqual(37,session.state.players['B'].life)
        self.replay(session)

    def test_actual_copied_source_grants_its_own_current_alternative(self):
        session=self.session(760064);target=self.add(session.engine,'Fist of Suns',seat='B')
        copier=self.add(session.engine,'Mirrormade',zone='hand')
        bolt=self.add(session.engine,'Lightning Bolt',zone='hand')
        self.prepare(session,mana={'C':1,'W':1,'U':3,'B':1,'R':1,'G':1});self.checkpoint(session)
        self.assertEqual([],self.grant_options(session,bolt))
        self.cast(session,copier,{'C':1,'U':2})
        decision=self.resolve(session);self.assertIsNotNone(decision)
        self.assertEqual('replacement.order',decision.kind)
        session=self.replay(session,load=True)
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        choices=[r for r in options if target.ref in r['id'] and not r['id'].startswith('decline:')]
        self.assertEqual(1,len(choices),options)
        result=session.act('pilot:A',{'action_id':'choose','replacement':choices[0]['id']})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('Fist of Suns',session.engine.display_name(copier.object_id))
        self.cast(session,session.state.cards[bolt.object_id],{'W':1,'U':1,'B':1,'R':1,'G':1},['B'],grant=True)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(37,session.state.players['B'].life)
        self.replay(session)

    def test_actual_blink_changes_future_price_identity_but_keeps_a_paid_spell(self):
        session=self.session(760065);source=self.add(session.engine,'Jodah, Archmage Eternal')
        first=self.add(session.engine,'Lightning Bolt',zone='hand',ref='before-blink-bolt')
        second=self.add(session.engine,'Lightning Bolt',zone='hand',ref='after-blink-bolt')
        blink=self.add(session.engine,'Cloudshift',zone='hand')
        self.prepare(session,mana={'W':3,'U':2,'B':2,'R':3,'G':2});self.checkpoint(session)
        options=self.grant_options(session,first);self.assertEqual(1,len(options));old_id=options[0]['id']
        old_logical=source.logical_object_id
        self.cast(session,first,{'W':1,'U':1,'B':1,'R':1,'G':1},['B'],grant=True)
        self.cast(session,blink,{'W':1},[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertNotEqual(old_logical,session.state.cards[source.object_id].logical_object_id)
        self.assertEqual(37,session.state.players['B'].life)
        options=self.grant_options(session,second);self.assertEqual(1,len(options));self.assertNotEqual(old_id,options[0]['id'])
        self.reject(session,{'action_id':'cast:'+second.ref,'cost_option':old_id,'pay':'auto','targets':['B']})
        self.cast(session,session.state.cards[second.object_id],{'W':1,'U':1,'B':1,'R':1,'G':1},['B'],grant=True)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(34,session.state.players['B'].life)
        self.replay(session)

    def test_selected_adventure_spell_does_not_inherit_the_dragon_price(self):
        from quorune.rules.casting.proposal import build_cast_offer
        session=self.session(760066);self.add(session.engine,'Dracogenesis')
        spell=self.add(session.engine,'Young Blue Dragon',zone='hand')
        self.prepare(session,mana={'C':1,'U':1});self.checkpoint(session)
        front=build_cast_offer(session.engine,'A',spell,face_name='Young Blue Dragon')
        adventure=build_cast_offer(session.engine,'A',spell,face_name='Sand Augury')
        self.assertEqual('payable',front.status)
        self.assertEqual('payable',adventure.status)
        self.assertTrue(any(o.option_id.startswith('battlefield-alternative:') for o in front.cost_options))
        self.assertFalse(any(o.option_id.startswith('battlefield-alternative:') for o in adventure.cost_options))
        self.cast(session,spell,{},grant=True)
        self.assertIsNone(self.resolve(session));self.assertEqual('battlefield',spell.zone)
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[spell.object_id]))
        self.replay(session,load=True)

    def test_global_alternative_descriptor_and_grammar_are_closed(self):
        from quorune.compiler.global_alternative_cost_templates import fixed_global_alternative_cost_handler
        from quorune.rules.global_alternative_cost_model import GlobalAlternativeCostSpec
        from quorune.semantic_runtime.global_alternative_costs import GlobalAlternativeCostHandler
        descriptor=fixed_global_alternative_cost_handler(self.db.lookup('Fist of Suns').oracle_text)[1]
        owner=GlobalAlternativeCostHandler();spec=owner.validate(descriptor)
        self.assertEqual(spec,GlobalAlternativeCostSpec.from_dict(spec.to_dict()))
        for changed in ({**descriptor,'requires_complete_card_program':False},
            {**descriptor,'schema_version':True}, {**descriptor,'extra':1}):
            with self.assertRaises(ValueError):owner.validate(changed)
        for change in ({'origin':'graveyard'},{'without_mana_cost':True},{'schema_version':2}):
            with self.assertRaises(ValueError):GlobalAlternativeCostSpec.from_dict({**spec.to_dict(),**change})
        for text in ('You may pay {X} rather than pay the mana cost for spells you cast.',
            'You may pay 3 life rather than pay the mana cost for spells you cast.',
            'You may cast spells from your library without paying their mana costs.',
            'You may cast a spell without paying its mana cost.'):
            self.assertIsNone(fixed_global_alternative_cost_handler(text),text)

    def test_registered_descriptor_owner_is_an_independent_runtime_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(760050);source=self.add(session.engine,'Fist of Suns',zone='hand')
        programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies]
        self.assertEqual(1,len(programs));program=programs[0]
        deps=tuple(d for d in program.capability_dependencies if d!=CAPABILITY)
        stripped=replace(program,capability_dependencies=deps,
            capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])

    def test_canonically_verified_owner_withdrawal_keeps_full_originals_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['withdrawn current battlefield price owner'])
        registry=CapabilityRegistry(raw)
        decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAPABILITY+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_fresh_compiler_cost_integration_and_current_ability_omissions_are_killed(self):
        from quorune.engine import CommanderEngine
        original=CommanderEngine._effective_static_component_key_map
        def ignore_loss(host):
            result={key:tuple(value) for key,value in original(host).items()}
            for source in host._semantic_event_sources(zones={'battlefield'}):
                result[source.object_id]=tuple(p.key for p in host.semantics.programs_for_oracle(source.oracle_id))
            return result
        for owner,value,method in (
            ('quorune.compiler.global_alternative_cost_templates.fixed_global_alternative_cost_handler',lambda text:None,
             'test_complete_global_alternative_cost_originals_have_normal_admission'),
            ('quorune.rules.casting.global_alternatives.battlefield_alternative_base_options',lambda *args,**kwargs:[],
             'test_actual_fist_offers_optional_wubrg_and_keeps_printed_payment'),
            ('quorune.engine.CommanderEngine._effective_static_component_key_map',ignore_loss,
             'test_actual_source_ability_loss_removes_future_alternative_offers')):
            with patch(owner,value):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


if __name__=='__main__':unittest.main()
