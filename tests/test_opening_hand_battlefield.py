from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.session import CommanderSession
from quorune.model import GameConfig
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.semantic_runtime.action_permissions import default_action_permission_registry
from quorune.semantic_runtime.action_permissions import OpeningHandBattlefieldPermissionHandler
from quorune.card_programs.admission import program_has_complete_card_program_admission
from quorune.oracle_ir import generated_programs
from quorune.errors import GameRuleError
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.record import authoritative_state_hash
from quorune.opening_hand import OpeningHandFrame
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_spree_mode_costs as spree
import test_public_condition_surfaces as surfaces


TEXT='If this card is in your opening hand, you may begin the game with it on the battlefield.'
CAP='game.opening_hand.battlefield'


class OpeningHandBattlefieldCompilerTests(unittest.TestCase):
    def test_complete_clause_has_original_span_and_hand_scoped_permission(self):
        ir=compile_oracle_card(record(TEXT,'Enchantment'),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        self.assertEqual((TEXT,0,len(TEXT)),(node.text,node.span.start,node.span.end))
        self.assertEqual('hand',node.active_zone)
        self.assertIn(CAP,node.capability_dependencies)

    def test_conditional_paid_and_reveal_variants_and_malformed_descriptors_remain_closed(self):
        for text in (TEXT.replace('opening hand','library'),TEXT.replace('you may begin','you may pay {2} to begin'),
                TEXT.replace('on the battlefield','revealed'),TEXT+' You win the game.'):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Enchantment'),
                capability_registry=load_default_capability_registry()).status)
        ir=compile_oracle_card(record(TEXT,'Enchantment'),capability_registry=load_default_capability_registry())
        descriptor,=ir.faces[0].nodes[0].handlers
        for field,value in (('schema_version',True),('permission','additional_land_play'),('unknown',True)):
            with self.subTest(field=field),self.assertRaises(SemanticNodeError):
                default_action_permission_registry().validate({**descriptor,field:value})

    def test_opening_permission_omission_blocks_the_complete_source(self):
        with patch('quorune.compiler.runtime_templates.opening_hand_battlefield_permission_handler',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record(TEXT,'Enchantment'),
                capability_registry=load_default_capability_registry()).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAP).update(status='blocked',blockers=['Opening permission omitted'])
        self.assertNotEqual('exact',compile_oracle_card(record(TEXT,'Enchantment'),capability_registry=CapabilityRegistry(raw)).status)

    def test_named_self_and_complete_admission_certificates_are_source_pinned(self):
        row=replace(record(TEXT,'Creature'),name='Generic Named Opener',
            oracle_text='If Generic Named Opener is in your opening hand, you may begin the game with him on the battlefield.')
        registry=load_default_capability_registry()
        ir=compile_oracle_card(row,capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        programs=generated_programs(SimpleNamespace(rulings=lambda _:()),row,trust_level='trusted',
            capability_registry=registry,capability_profile='commander_review')
        program,=[p for p in programs if p.active_zone=='hand']
        self.assertTrue(program_has_complete_card_program_admission(program))
        for value in ({},{'schema_version':1,'oracle_ir_status':'partial','material_residual_count':1},
                {'schema_version':1,'oracle_ir_status':'exact','material_residual_count':True}):
            changed=deepcopy(program);changed.provenance['card_program_admission']=value
            self.assertFalse(program_has_complete_card_program_admission(changed))
        self.assertNotEqual('exact',compile_oracle_card(replace(row,
            oracle_text=row.oracle_text.replace('Generic Named Opener is','Another Opener is')),
            capability_registry=registry).status)

    def test_opening_frame_has_closed_shape_stable_roundtrip_and_ordered_seats(self):
        raw={'schema_version':1,'seats':['A','B'],'index':0,'subjects':[
            {'object_id':'one','logical_object_id':'one:hand','ref':'ONE','player':'A'}]}
        self.assertEqual(raw,OpeningHandFrame.from_dict(raw).to_dict())
        for field,value in (('schema_version',True),('index',True),('index',3),('seats',['A','A']),('unknown',1)):
            with self.subTest(field=field),self.assertRaises(GameRuleError):OpeningHandFrame.from_dict({**raw,field:value})


class OpeningHandBattlefieldRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    add=spree.SpreeModeCostRuntimeTests.add
    resolve=spree.SpreeModeCostRuntimeTests.resolve
    prepare=spree.SpreeModeCostRuntimeTests.prepare
    cast=surfaces.PublicConditionSurfaceRuntimeTests.cast
    checkpoint=spree.SpreeModeCostRuntimeTests.checkpoint
    replay=spree.SpreeModeCostRuntimeTests.replay
    reject=spree.SpreeModeCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'opening-hand.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/opening-hand-battlefield-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def session(self,seed=103610,names_by_seat=None,first='A'):
        names_by_seat={'A':['Leyline of Sanctity']} if names_by_seat is None else names_by_seat
        decks={}
        for seat in 'ABCD':
            names=names_by_seat.get(seat,[])
            decks[seat]=DeckDefinition('Opening-hand control '+seat,
                [DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',7-len(names))]
                +[DeckEntry(n,1) for n in names],['Generic Bound Commander'])
        session=CommanderSession.create(self.db,decks,first_player=first,seed=seed,
            config=GameConfig(seed=seed,auto_pass_empty_priority=False))
        # Keep the seven-card opening hands deterministic, with a remaining
        # private library card for native entry draws and the first draw step.
        for seat in 'ABCD':
            card=self.add(session.engine,'Generic Bound Plains',seat=seat,zone='library',ref='opening-spare-'+seat)
            card.known_to=[];card.revealed_to=[]
        self.checkpoint(session)
        return session

    def keep(self,session):
        for seat in session.state.turn_order:
            result=session.act('pilot:'+seat,{'action_id':'keep'})
            self.assertTrue(result.ok,result.summary)

    def choose(self,session,refs=(),actor='A'):
        result=session.act('pilot:'+actor,{'action_id':'choose','choices':{'refs':list(refs)}})
        self.assertTrue(result.ok,result.summary)

    def card(self,session,name,seat='A',zone='hand'):
        return next(session.state.cards[oid] for oid in session.state.players[seat].zones[zone]
            if session.state.cards[oid].printed_name==name)

    def priority(self,session,seat):
        for _ in range(4):
            decision=session.state.pending_decision
            self.assertEqual('priority',decision.kind)
            if decision.actors==[seat]:return
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.fail('Requested seat did not receive its priority')

    def test_actual_kept_leyline_gets_private_atomic_loaded_action_before_first_turn(self):
        session=self.session()
        card=next(session.state.cards[oid] for oid in session.state.players['A'].zones['hand']
            if session.state.cards[oid].printed_name=='Leyline of Sanctity')
        self.keep(session)
        self.assertFalse(session.state.started)
        self.assertEqual('opening.hand',session.state.pending_decision.kind)
        self.assertIsNone(session.state.current_turn)
        self.assertFalse(session.state.stack)
        self.assertIsNone(session.state.priority_player)
        for seat in 'BCD':
            packet=session.packet('pilot:'+seat,full=True)
            self.assertIsNone(packet['decision']);self.assertNotIn(card.ref,json.dumps(packet))
        self.reject(session,'choose','B',choices={'refs':[card.ref]})
        self.reject(session,'choose',choices={'refs':[card.ref,card.ref]})
        self.reject(session,'choose',choices={'refs':['unknown']})
        session=self.replay(session,load=True)
        self.choose(session,[card.ref])
        self.assertEqual('battlefield',session.state.cards[card.object_id].zone)
        self.assertEqual(6,len(session.state.players['A'].zones['hand']))
        self.assertTrue(session.state.started)
        self.assertEqual(1,session.state.turn_sequence)
        self.assertEqual(('A','upkeep'),(session.state.active_player,session.state.step))
        self.replay(session,load=True)

    def test_decline_all_retains_original_hand_and_starts_exactly_one_turn(self):
        session=self.session(103611)
        self.keep(session);self.choose(session)
        self.assertTrue(session.state.started)
        self.assertEqual(1,session.state.turn_sequence)
        self.assertEqual(7,len(session.state.players['A'].zones['hand']))
        self.assertFalse(session.state.players['A'].zones['battlefield'])
        self.replay(session,load=True)

    def test_original_with_unclosed_siblings_never_receives_partial_opening_permission(self):
        session=self.session(103619,{'A':['Leyline of Punishment']})
        card=self.card(session,'Leyline of Punishment')
        program=compile_best_available_card_program(self.db,session.engine.card_record(card),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        self.assertFalse(bind_card_program_runtime(program,capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])
        self.keep(session)
        self.assertTrue(session.state.started,'A trusted fragment cannot authorize an unadmitted whole source')
        self.assertEqual('hand',session.state.cards[card.object_id].zone)

    def test_all_originals_are_admitted_and_enter_without_casts_or_auto_equipping(self):
        names=['Leyline of Lightning','Leyline of Anticipation','Leyline of Lifeforce','Leyline of the Meek',
            'Leyline of Sanctity','Leyline of Vitality','Leyline Axe']
        session=self.session(103612,{'A':names,'B':['Quicksilver, Brash Blur']})
        cards=[self.card(session,name) for name in names]
        quicksilver=self.card(session,'Quicksilver, Brash Blur','B')
        for card in (*cards,quicksilver):
            program=compile_best_available_card_program(self.db,session.engine.card_record(card),
                semantic_registry=session.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
            self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,
                profile='commander_review')['strict_capability_ready'])
        self.keep(session)
        for index,card in enumerate(cards):
            self.assertFalse(session.state.started)
            self.assertEqual(['A'],list(session.state.pending_decision.actors))
            self.choose(session,[card.ref])
            self.assertEqual('battlefield',session.state.cards[card.object_id].zone)
            if index+1<len(cards):
                self.assertFalse(session.state.stack);self.assertFalse(session.state.pending_trigger_batches)
        self.assertFalse(session.state.started)
        self.choose(session,[quicksilver.ref],actor='B')
        self.assertEqual('battlefield',session.state.cards[quicksilver.object_id].zone)
        self.assertFalse(session.state.stack)
        self.assertFalse(session.state.pending_trigger_batches)
        self.assertEqual((),session.engine._current_turn_history('spell_cast'))
        self.assertIsNone(session.state.cards[cards[-1].object_id].attached_to)
        self.assertEqual(1,session.state.turn_sequence)
        self.replay(session,load=True)

    def test_starting_player_order_and_entry_trigger_wait_for_first_upkeep(self):
        session=self.session(103613,{'A':['Leyline of Vitality','Generic Opening Creature'],
            'B':['Leyline Axe'],'C':['Leyline of Sanctity']},first='C')
        self.keep(session)
        c=self.card(session,'Leyline of Sanctity','C')
        self.assertEqual(['C'],list(session.state.pending_decision.actors));self.choose(session,[c.ref],actor='C')
        self.assertEqual(['A'],list(session.state.pending_decision.actors))
        vitality=self.card(session,'Leyline of Vitality');body=self.card(session,'Generic Opening Creature')
        future=self.card(session,'Leyline Axe','B')
        self.assertNotIn(future.ref,json.dumps(session.packet('pilot:A',full=True)))
        for viewer in 'BCD':
            packet=session.packet('pilot:'+viewer,full=True)
            self.assertIsNone(packet['decision'])
            self.assertNotIn(body.ref,json.dumps(packet));self.assertNotIn(vitality.ref,json.dumps(packet))
        self.choose(session,[vitality.ref]);self.choose(session,[body.ref])
        self.assertFalse(session.state.started);self.assertFalse(session.state.stack)
        self.assertEqual(40,session.state.players['A'].life)
        self.assertTrue(session.state.pending_trigger_batches)
        self.assertEqual(['B'],list(session.state.pending_decision.actors))
        self.assertEqual(2,session.state.cards[body.object_id].counters['+1/+1'])
        session=self.replay(session,load=True)
        self.choose(session,[self.card(session,'Leyline Axe','B').ref],actor='B')
        self.assertEqual(('C','upkeep'),(session.state.active_player,session.state.step))
        self.assertEqual(1,len(session.state.stack));self.assertEqual('A',session.state.stack[-1].controller)
        decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
        result=session.act('pilot:A',{'action_id':'choose','choice':'apply'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(41,session.state.players['A'].life)
        self.replay(session,load=True)

    def test_entry_counter_order_resumes_same_opening_hand_before_the_first_turn(self):
        for first in ('Generic Opening Doubler','Generic Opening Scales'):
            with self.subTest(first=first):
                session=self.session(103614,{'A':['Generic Opening Doubler','Generic Opening Scales','Generic Opening Creature']})
                self.keep(session)
                doubler=self.card(session,'Generic Opening Doubler');scales=self.card(session,'Generic Opening Scales')
                body=self.card(session,'Generic Opening Creature')
                self.choose(session,[doubler.ref]);self.choose(session,[scales.ref]);self.choose(session,[body.ref])
                self.assertEqual('replacement.order',session.state.pending_decision.kind)
                self.assertFalse(session.state.started);self.assertFalse(session.state.stack)
                self.assertEqual('hand',session.state.cards[body.object_id].zone)
                self.assertEqual({},session.state.cards[body.object_id].counters)
                session=self.replay(session,load=True)
                options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
                source=doubler if first=='Generic Opening Doubler' else scales
                option=next(row for row in options if row['source'] in {source.object_id,source.ref})
                self.reject(session,'choose','B',choices={'replacement':option['id']})
                result=session.act('pilot:A',{'action_id':'choose','choices':{'replacement':option['id']}})
                self.assertTrue(result.ok,result.summary)
                self.assertEqual('battlefield',session.state.cards[body.object_id].zone)
                self.assertEqual(5 if first=='Generic Opening Doubler' else 6,session.state.cards[body.object_id].counters['+1/+1'])
                self.assertTrue(session.state.started);self.assertEqual(1,session.state.turn_sequence)
                self.replay(session,load=True)

    def test_nonspell_aura_entry_uses_untargeted_subject_choice_and_pending_replay(self):
        session=self.session(103615,{'A':['Generic Opening Shroud'],'B':['Generic Opening Aura']})
        self.keep(session)
        body=self.card(session,'Generic Opening Shroud')
        self.choose(session,[body.ref])
        aura=self.card(session,'Generic Opening Aura','B')
        self.choose(session,[aura.ref],actor='B')
        self.assertEqual('aura.entry',session.state.pending_decision.kind)
        self.assertFalse(session.state.started);self.assertFalse(session.state.stack)
        self.assertEqual('hand',session.state.cards[aura.object_id].zone)
        session=self.replay(session,load=True)
        self.reject(session,'choose','A',aura_target=body.ref)
        result=session.act('pilot:B',{'action_id':'choose','aura_target':body.ref})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(body.object_id,session.state.cards[aura.object_id].attached_to)
        self.assertEqual('B',session.state.cards[aura.object_id].controller)
        self.assertEqual((3,7),tuple(session.engine._numeric_stat(body.object_id,k) for k in ('power','toughness')))
        self.assertTrue(session.state.started)
        self.replay(session,load=True)

    def test_aura_that_cannot_enter_stays_available_after_another_opening_action(self):
        session=self.session(103622,{'A':['Generic Opening Aura','Generic Opening Shroud']})
        self.keep(session)
        aura=self.card(session,'Generic Opening Aura');body=self.card(session,'Generic Opening Shroud')
        self.choose(session,[aura.ref])
        self.assertEqual('hand',session.state.cards[aura.object_id].zone)
        self.assertFalse(session.state.started)
        self.choose(session,[body.ref])
        self.assertFalse(session.state.started,'An entry prevented in the original hand cannot consume its later opening action')
        self.assertEqual('opening.hand',session.state.pending_decision.kind)
        session=self.replay(session,load=True)
        self.choose(session,[aura.ref])
        self.assertEqual('aura.entry',session.state.pending_decision.kind)
        result=session.act('pilot:A',{'action_id':'choose','aura_target':body.ref})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(body.object_id,session.state.cards[aura.object_id].attached_to)
        self.assertTrue(session.state.started)
        self.replay(session,load=True)

    def test_bottomed_leyline_is_not_in_the_final_opening_hand(self):
        session=self.session(103616)
        result=session.act('pilot:A',{'action_id':'mulligan'});self.assertTrue(result.ok,result.summary)
        for seat in 'BCD':
            result=session.act('pilot:'+seat,{'action_id':'keep'});self.assertTrue(result.ok,result.summary)
        result=session.act('pilot:A',{'action_id':'mulligan'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('mulligan.bottom',session.state.pending_decision.kind)
        card=self.card(session,'Leyline of Sanctity')
        result=session.act('pilot:A',{'action_id':'bottom','cards':[card.ref]});self.assertTrue(result.ok,result.summary)
        result=session.act('pilot:A',{'action_id':'keep'});self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.started)
        self.assertEqual('library',session.state.cards[card.object_id].zone)
        self.assertFalse(session.state.players['A'].zones['battlefield'])
        self.assertEqual(6,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_named_self_opener_keeps_pregame_entry_out_of_first_turn_power_up_discount(self):
        session=self.session(103617,{'A':['Quicksilver, Brash Blur']})
        card=self.card(session,'Quicksilver, Brash Blur')
        self.keep(session)
        self.assertFalse(session.state.started)
        self.choose(session,[card.ref])
        self.assertEqual('battlefield',session.state.cards[card.object_id].zone)
        self.assertEqual(0,session.state.cards[card.object_id].entered_battlefield_turn_sequence)
        self.assertEqual(1,session.state.turn_sequence)
        self.replay(session,load=True)
        self.prepare(session,mana={'C':3,'R':1});self.checkpoint(session)
        def offered():return [r for r in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if r.get('action')=='activate' and r.get('source')==card.ref]
        self.assertFalse(offered())
        self.prepare(session,mana={'C':4,'R':1});self.checkpoint(session)
        action,=offered()
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual({'+1/+1':1,'double strike':1},session.state.cards[card.object_id].counters)
        self.replay(session,load=True)
        self.prepare(session,mana={'C':4,'R':1});self.checkpoint(session)
        self.assertFalse(offered())
        self.reject(session,action['id'],pay='auto')
        self.replay(session,load=True)

    def test_no_opening_sources_preserve_normal_start_and_multiplayer_draw(self):
        session=self.session(103618,{})
        self.keep(session)
        self.assertTrue(session.state.started)
        self.assertEqual('priority',session.state.pending_decision.kind)
        for seat in 'ABCD':
            result=session.act('pilot:'+seat,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('draw',session.state.step)
        self.assertEqual(8,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_corrupt_or_stale_opening_frame_is_rejected_before_entry(self):
        session=self.session(103620)
        self.keep(session)
        original=deepcopy(session.state.pending_decision.continuation)
        for field,value in (('index',True),('seats',['B','A','C','D'])):
            session.state.pending_decision.continuation['opening_hand'][field]=value
            self.reject(session,'choose',choices={'refs':[]})
            session.state.pending_decision.continuation=deepcopy(original)
        card=self.card(session,'Leyline of Sanctity')
        session.engine.move_card(card.object_id,'graveyard',reason='Stale opening-hand witness')
        self.reject(session,'choose',choices={'refs':[card.ref]})
        self.assertEqual('graveyard',session.state.cards[card.object_id].zone)
        self.assertFalse(session.state.started)

    def test_permission_and_whole_source_omissions_fail_the_independent_native_controls(self):
        with patch.object(OpeningHandBattlefieldPermissionHandler,'lower',return_value=()):
            with self.assertRaises(AssertionError):self.test_actual_kept_leyline_gets_private_atomic_loaded_action_before_first_turn()
        with patch('quorune.opening_hand.program_has_complete_card_program_admission',return_value=True):
            with self.assertRaises(AssertionError):self.test_original_with_unclosed_siblings_never_receives_partial_opening_permission()

    def test_original_openers_retain_native_timing_targeting_counter_damage_token_life_and_equip_rules(self):
        session=self.session(103621,{'A':['Leyline of Sanctity','Leyline of Anticipation','Leyline of Lifeforce',
            'Leyline of Lightning','Leyline of Vitality','Grizzly Bears','Generic Entry One Copy'],
            'B':['Shock','Counterspell'],'C':['Leyline of the Meek'],'D':['Leyline Axe','Grizzly Bears']},first='C')
        bears=self.card(session,'Grizzly Bears');copy_spell=self.card(session,'Generic Entry One Copy')
        shock=self.card(session,'Shock','B');counter=self.card(session,'Counterspell','B')
        d_body=self.card(session,'Grizzly Bears','D');axe=self.card(session,'Leyline Axe','D')
        self.keep(session)
        for _ in range(7):
            decision=session.state.pending_decision;self.assertEqual('opening.hand',decision.kind)
            seat=decision.actors[0]
            candidates=session.packet('pilot:'+seat,full=True)['decision']['ctx']['candidates']
            self.choose(session,[candidates[0]['id']],actor=seat)
        self.assertTrue(session.state.started);self.assertFalse(session.state.stack)
        self.replay(session,load=True)
        session.state.players['B'].mana_pool.update({'U':2,'R':1})
        self.prepare(session,actor='A',active='C',mana={'C':3,'G':1,'W':1});self.checkpoint(session)
        result=session.act('pilot:A',{'action_id':'cast:'+bears.ref,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.target',session.state.pending_decision.kind)
        result=session.act('pilot:A',{'action_id':'choose','targets':['B']});self.assertTrue(result.ok,result.summary)
        spell_ref=next(item.ref for item in session.state.stack if item.card_object_id==bears.object_id)
        self.priority(session,'B')
        result=session.act('pilot:B',{'action_id':'cast:'+counter.ref,'targets':[spell_ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual('graveyard',session.state.cards[counter.object_id].zone)
        self.assertEqual('stack',session.state.cards[bears.object_id].zone)
        result=session.act('pilot:A',{'action_id':'choose','pay':True});self.assertTrue(result.ok,result.summary)
        self.assertEqual(39,session.state.players['B'].life)
        decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
        result=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual(41,session.state.players['A'].life)
        self.assertEqual((2,3),tuple(session.engine._numeric_stat(bears.object_id,k) for k in ('power','toughness')))
        self.priority(session,'B')
        self.reject(session,'cast:'+shock.ref,'B',targets=['A'],pay='auto')
        self.cast(session,shock,('C',),actor='B');self.assertEqual(38,session.state.players['C'].life)
        self.priority(session,'A')
        result=session.act('pilot:A',{'action_id':'cast:'+copy_spell.ref,'targets':[bears.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.target',session.state.pending_decision.kind)
        result=session.act('pilot:A',{'action_id':'choose','targets':['B']});self.assertTrue(result.ok,result.summary)
        decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
        result=session.act('pilot:A',{'action_id':'choose','pay':False});self.assertTrue(result.ok,result.summary)
        decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
        result=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual(42,session.state.players['A'].life)
        token,=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual((3,4),tuple(session.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')))
        self.replay(session,load=True)
        self.prepare(session,actor='D',active='D',mana={'C':4,'G':1});self.checkpoint(session)
        self.cast(session,d_body,actor='D')
        rows=session.packet('pilot:D',full=True)['decision']['ctx']['legal']['actions']
        equip,=[r for r in rows if r.get('action')=='activate' and r.get('source')==axe.ref]
        self.reject(session,equip['id'],'D',targets=[bears.ref],pay='auto')
        result=session.act('pilot:D',{'action_id':equip['id'],'targets':[d_body.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(d_body.object_id,session.state.cards[axe.object_id].attached_to)
        self.assertEqual((3,3),tuple(session.engine._numeric_stat(d_body.object_id,k) for k in ('power','toughness')))
        self.assertTrue({'double strike','trample'}.issubset(session.engine._combat_keywords(session.state.cards[d_body.object_id])))
        self.assertFalse(sum(session.state.players['D'].mana_pool.values()))
        self.replay(session,load=True)
