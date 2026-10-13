from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.transmute import (TRANSMUTE_CAPABILITY,
    is_closed_transmute_program, retained_transmute_mana_value, transmute_search_effect, transmute_node_capabilities)
from quorune.transmute import finalize_transmute_discard
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record, BoundEffectProgramRuntimeTests


class TransmuteCompilerTests(unittest.TestCase):
    def compile(self, text='Transmute {1}{U}{U}', registry=None):
        return compile_oracle_card(replace(record(text, 'Instant'), keywords=('Transmute',)), capability_registry=registry or load_default_capability_registry(),
            capability_profile='commander_review')

    def test_printed_transmute_lowers_complete_hand_activation_and_bound_search(self):
        # CR702.53a: fixed mana and discard cost, hand only, sorcery timing,
        # equal discarded-card mana value, reveal/hand/shuffle.
        ir=self.compile();self.assertEqual('exact',ir.status)
        node=ir.faces[0].nodes[0];self.assertEqual('hand',node.active_zone)
        self.assertTrue(node.cost['discard_source']);self.assertEqual((transmute_search_effect(),),node.effects)
        ability=node.handlers[0]['ability'];self.assertTrue(ability['sorcery_speed'])
        self.assertTrue(node.handlers[0]['requires_complete_card_program'])
        self.assertEqual('Transmute {1}{U}{U}',node.text)
        self.assertEqual((TRANSMUTE_CAPABILITY,),transmute_node_capabilities(effects=node.effects,
            target_schema=None,mechanic_ids=('transmute',)))
        program=SimpleNamespace(event='activate',active_zone='hand',effects=node.effects,target_schema=None,
            coverage=('generated_oracle_ir','runtime_static_handler','transmute'),
            capability_dependencies=node.capability_dependencies,handlers=node.handlers)
        self.assertTrue(is_closed_transmute_program(program))
        for field,value in (('discard_source',False),('sorcery_speed',False),('tap_source',True),('zones',['battlefield'])):
            handlers=json.loads(json.dumps(node.handlers));handlers[0]['ability'][field]=value
            self.assertFalse(is_closed_transmute_program(SimpleNamespace(**{**vars(program),'handlers':handlers})),field)

    def test_transmute_open_costs_companions_shape_and_dependency_mutants_fail_closed(self):
        for text in ('Transmute {X}', 'Transmute {U/B}', 'Transmute {S}', 'Transmute—Pay 2 life',
                     'Transmute {1}{U}{U}\nWhenever a player smiles, draw three cards.'):
            self.assertNotEqual('exact',self.compile(text).status,text)
        for field,value in (('reveal',False),('shuffle_after',False),('destination','battlefield'),
                            ('selector',{'mana_value':{'equal':2}})):
            effect={**transmute_search_effect(),field:value}
            self.assertFalse(transmute_node_capabilities(effects=(effect,),target_schema=None,mechanic_ids=('transmute',)))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for dependency in (TRANSMUTE_CAPABILITY,'activation.source_zone_change.fixed','zone.change.destination_replacement'):
            value=json.loads(json.dumps(raw));row=next(r for r in value['capabilities'] if r['id']==dependency)
            row.update(status='blocked',blockers=['Transmute dependency witness']);registry=CapabilityRegistry(value)
            registry.mark_evidence_verified('0'*64);self.assertNotEqual('exact',self.compile(registry=registry).status)
        with patch('quorune.compiler.transmute_nodes.fixed_transmute_ability',return_value=None):
            self.assertNotEqual('exact',self.compile().status)

    def test_retained_discard_snapshot_is_strict_and_does_not_query_current_source(self):
        item=SimpleNamespace(source_object_id='source',context={'source_logical_object_id':'before-discard',
            'transmute_discard_snapshot':{'schema_version':1,'source_object_id':'source',
                'source_logical_object_id':'before-discard','mana_value':0,'characteristics_defined':True}})
        self.assertEqual(0,retained_transmute_mana_value(item))
        for field,value in (('schema_version',True),('source_object_id','other'),
                            ('source_logical_object_id','after-reentry'),('mana_value',True),('mana_value',float('nan'))):
            changed=SimpleNamespace(source_object_id=item.source_object_id,context=json.loads(json.dumps(item.context)))
            changed.context['transmute_discard_snapshot'][field]=value
            with self.assertRaises(ValueError):retained_transmute_mana_value(changed)
        host=SimpleNamespace(active_seats=tuple('ABCD'))
        source=SimpleNamespace(zone='library',face_down=False,revealed_to=['A'])
        finalize_transmute_discard(host,source,item.context)
        self.assertIsNone(retained_transmute_mana_value(item))
        source.revealed_to=list('ABCD');finalize_transmute_discard(host,source,item.context)
        self.assertEqual(0,retained_transmute_mana_value(item))

    def test_undefined_discard_quality_finds_nothing_through_canonical_private_search(self):
        from quorune.selection.searching import HiddenSearchOwnerMixin
        from quorune.semantic_runtime.values import resolve_semantic_value
        item=SimpleNamespace(source_object_id='discarded',context={'source_logical_object_id':'discarded@0',
            'transmute_discard_snapshot':{'schema_version':1,'source_object_id':'discarded',
                'source_logical_object_id':'discarded@0','mana_value':2,'characteristics_defined':False}})
        effect=resolve_semantic_value(SimpleNamespace(),transmute_search_effect(),item)
        self.assertEqual({'mana_value':{'equal':None}},effect['selector'])
        self.assertTrue(HiddenSearchOwnerMixin._search_is_restrictive(effect['selector']))
        from quorune.model import CardInstance
        row=record('', 'Creature');card=CardInstance(object_id='matching',ref='matching',oracle_id=row.oracle_id,
            printed_name=row.name,owner='A',controller='A',zone='library')
        host=SimpleNamespace(card_record=lambda card:row,_effective_card_data=lambda card:{'type_line':'Creature','mana_value':2},
            _type_parts=lambda text:({'creature'},set(),set()))
        self.assertFalse(HiddenSearchOwnerMixin._search_candidate_matches(host,card,effect['selector']))


class TransmuteRuntimeTests(unittest.TestCase):
    add=BoundEffectProgramRuntimeTests.add
    ready=BoundEffectProgramRuntimeTests.ready
    checkpoint=BoundEffectProgramRuntimeTests.checkpoint
    resolve=BoundEffectProgramRuntimeTests.resolve
    replay=BoundEffectProgramRuntimeTests.replay

    def session(self,seed):
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import register_generated_programs
        session=BoundEffectProgramRuntimeTests.session(self,seed)
        session.engine.semantics=SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(self.db,session.engine.semantics,tuple(self.db.iter_cards()),
            trust_level='provisional',capability_registry=self.registry,capability_profile='commander_review',
            promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        return session

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'transmute.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/transmute-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Generic Transmute Deck',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def prepare(self,session,name='Tolaria West'):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        source=self.add(session.engine,name,zone='hand',ref='transmute-source')
        self.ready(session,source,{'U':2,'C':1})
        program=compile_best_available_card_program(self.db,self.db.lookup(name),semantic_registry=session.engine.semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(r for r in actions if r['id'].startswith('activate:'+source.ref+':'))
        return source,action['id']

    def test_original_transmute_uses_actual_cost_private_search_reveal_shuffle_and_replay(self):
        for index,name in enumerate(('Tolaria West','Dizzy Spell')):
            with self.subTest(name=name):
                session=self.session(702530+index);source,action=self.prepare(session,name)
                match=self.add(session.engine,'Generic Bound Plains' if index==0 else 'Generic Bound Body',
                    zone='library',ref='correct-mana-value')
                wrong=self.add(session.engine,'Generic Bound Body' if index==0 else 'Generic Bound Mentor',
                    zone='library',ref='incorrect-mana-value')
                self.checkpoint(session);before=authoritative_state_hash(session.state)
                rejected=session.act('pilot:C',{'action_id':action,'pay':'auto'})
                self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
                accepted=session.act('pilot:A',{'action_id':action,'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual('graveyard',source.zone);self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
                pending=self.replay(session,load=True);self.assertEqual('semantic.search',self.resolve(pending).kind)
                ctx=pending.packet('pilot:A',full=True)['decision']['ctx'];refs={r['id'] for r in ctx['search_cards']}
                self.assertIn(match.ref,refs);self.assertNotIn(wrong.ref,refs)
                for seat in 'BCD':self.assertNotIn(match.ref,json.dumps(pending.packet('pilot:'+seat,full=True)))
                prior=authoritative_state_hash(pending.state)
                rejected=pending.act('pilot:A',{'action_id':'choose','cards':[wrong.ref]})
                self.assertFalse(rejected.ok);self.assertEqual(prior,authoritative_state_hash(pending.state))
                pending=self.replay(pending,load=True)
                accepted=pending.act('pilot:A',{'action_id':'choose','cards':[match.ref]});self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual('hand',pending.state.cards[match.object_id].zone)
                for seat in 'BCD':self.assertIn(match.ref,json.dumps(pending.packet('pilot:'+seat,full=True)))
                self.assertEqual(1,sum(e.code=='library.shuffle' for e in pending.state.events))
                self.replay(pending)

    def test_transmute_timing_zone_and_unpayable_offers_match_rejection_and_rollback(self):
        session=self.session(702533);source,action=self.prepare(session)
        engine=session.engine
        for mode in ('opponents_turn','combat','empty_mana','battlefield'):
            with self.subTest(mode=mode):
                engine.permissions.invalidate_current();engine.state.pending_decision=None
                engine.state.priority_passes=[];engine.state.active_player='B' if mode=='opponents_turn' else 'A'
                engine.state.phase='combat' if mode=='combat' else 'precombat_main';engine.state.step='begin_combat' if mode=='combat' else 'main'
                engine.state.players['A'].mana_pool.update({'U':0 if mode=='empty_mana' else 2,'C':1})
                if mode=='battlefield':
                    engine.move_card(source.object_id,'battlefield',log=False)
                    # CR702.53b retains the activation characteristic in every
                    # zone while CR702.53a restricts actual activation to hand.
                    self.assertTrue(any(a.discard_source and a.sorcery_speed for a in engine._activated_abilities(source)))
                engine._grant_priority('A');engine.pump()
                actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                self.assertNotIn(action,{r['id'] for r in actions})
                before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:A',{'action_id':action,'pay':'auto'}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))

    def test_transmute_restrictive_search_may_fail_to_find_and_still_shuffles(self):
        session=self.session(702534);source,action=self.prepare(session,'Dizzy Spell');self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action,'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.search',self.resolve(session).kind)
        accepted=session.act('pilot:A',{'action_id':'choose','cards':[]});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',source.zone);self.assertEqual(1,sum(e.code=='library.shuffle' for e in session.state.events))
        self.replay(session)

    def test_original_transmute_discard_replacement_retains_mana_value_and_replays(self):
        session=self.session(702535);source,action=self.prepare(session,'Dizzy Spell');engine=session.engine
        self.add(engine,'Dauthi Voidwalker',seat='B',ref='discard-replacement-one')
        self.add(engine,'Dauthi Voidwalker',seat='C',ref='discard-replacement-two')
        result_card=self.add(engine,'Generic Bound Body',zone='library',ref='replacement-search-result')
        engine.permissions.invalidate_current();engine.state.pending_decision=None;engine.state.priority_passes=[]
        engine._grant_priority('A');engine.pump();self.checkpoint(session)
        before_mana=dict(engine.state.players['A'].mana_pool)
        accepted=session.act('pilot:A',{'action_id':action,'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual('hand',source.zone);self.assertFalse(session.state.stack)
        self.assertEqual(before_mana,session.state.players['A'].mana_pool)
        for seat in 'BCD':self.assertIsNone(session.packet('pilot:'+seat,full=True).get('decision'))
        session=self.replay(session,load=True)
        replacement=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':replacement});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('exile',session.state.cards[source.object_id].zone)
        snapshot=session.state.stack[-1].context['transmute_discard_snapshot']
        self.assertEqual(1,snapshot['mana_value'])
        self.assertNotEqual(snapshot['source_logical_object_id'],session.state.cards[source.object_id].logical_object_id)
        self.assertEqual('semantic.search',self.resolve(session).kind)
        accepted=session.act('pilot:A',{'action_id':'choose','cards':[result_card.ref]});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('hand',session.state.cards[result_card.object_id].zone)
        self.assertEqual(1,sum(e.code=='library.shuffle' for e in session.state.events));self.replay(session)

    def test_residual_companion_card_cannot_expose_or_accept_transmute(self):
        session=self.session(702536);engine=session.engine
        source=self.add(engine,'Grozoth',zone='hand',ref='partial-transmute-source')
        ir=compile_oracle_card(self.db.lookup('Grozoth'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)
        ability=next(n.handlers[0]['ability'] for f in ir.faces for n in f.nodes if n.template_id=='fixed-transmute-activation-v1')
        engine.permissions.invalidate_current();engine.state.pending_decision=None;engine.state.priority_passes=[]
        engine.state.active_player='A';engine.state.phase='precombat_main';engine.state.step='main';engine.state.started=True
        engine.state.players['A'].mana_pool.update({'U':2,'C':1});engine._grant_priority('A');engine.pump()
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(r['id'].startswith('activate:'+source.ref+':') for r in actions))
        before=authoritative_state_hash(session.state)
        denied=session.act('pilot:A',{'action_id':'activate:'+source.ref+':'+ability['ability_id'],'pay':'auto'})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_transmute_is_unavailable_while_an_actual_spell_is_on_stack(self):
        session=self.session(702537);source,transmute_action=self.prepare(session)
        spell=self.add(session.engine,'Generic Bound Draw Loss',zone='hand',ref='transmute-timing-spell')
        offered=self.ready(session,spell,{'U':2,'C':1,'B':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':['A'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertTrue(session.state.stack)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertNotIn(transmute_action,{r['id'] for r in actions})
        before=authoritative_state_hash(session.state)
        denied=session.act('pilot:A',{'action_id':transmute_action,'pay':'auto'})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertEqual('hand',source.zone);self.replay(session,load=True)
