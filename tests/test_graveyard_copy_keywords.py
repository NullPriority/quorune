from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.token_copy_recipes import TokenCopyExceptionSpec,TokenCopyRecipeError,apply_copy_exception
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class GraveyardCopyCompilerTests(unittest.TestCase):
    def test_fixed_cost_keywords_lower_to_one_owner_exile_and_versioned_copy_exception(self):
        for keyword in ('Embalm','Eternalize'):
            ir=compile_oracle_card(replace(query_record(keyword+' {2}{W}'),keywords=(keyword,)),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertEqual('graveyard',node.active_zone);self.assertTrue(node.cost['exile_source'])
            ability=node.handlers[0]['ability'];self.assertTrue(ability['sorcery_speed']);self.assertEqual(['graveyard'],ability['zones'])
            spec=node.effects[0]['copy_spec']['exception'];self.assertEqual(2,spec['schema_version']);self.assertTrue(spec['remove_mana_cost'])
            self.assertEqual(['zombie'],spec['add_subtypes']);self.assertEqual(['W'] if keyword=='Embalm' else ['B'],spec['colors'])
            self.assertEqual(None if keyword=='Embalm' else 4,spec['power'])
        for text in ('Embalm {X}{W}','Eternalize—{2}{W}, Discard a card.','Embalm {W/U}'):
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),keywords=('Eternalize' if text.startswith('Eternalize') else 'Embalm',)),capability_registry=load_default_capability_registry()).status)

    def test_copy_exception_preserves_v1_bytes_and_rejects_forged_versions(self):
        legacy=TokenCopyExceptionSpec().to_dict();self.assertNotIn('remove_mana_cost',legacy);self.assertEqual(legacy,TokenCopyExceptionSpec.from_dict(legacy).to_dict())
        value=TokenCopyExceptionSpec(remove_mana_cost=True,schema_version=2).to_dict()
        self.assertEqual(value,TokenCopyExceptionSpec.from_dict(value).to_dict())
        for changes in ({'schema_version':True},{'remove_mana_cost':1},{'schema_version':1},{'unknown':True},{'colors':[['W']]},{'add_subtypes':[[]]}):
            with self.assertRaises(TokenCopyRecipeError):TokenCopyExceptionSpec.from_dict({**value,**changes})
        printed={'mana_cost':'{3}{G}','mana_value':4,'type_line':'Creature — Cat','power':'2','toughness':'3','keywords':['Lifelink'],'ability_fragments':[],'colors':['G']}
        result=apply_copy_exception(printed,TokenCopyExceptionSpec(add_subtypes=('zombie',),colors=('B',),power=4,toughness=4,remove_mana_cost=True,schema_version=2))
        self.assertEqual(('',0,'4','4', ['B']),(result['mana_cost'],result['mana_value'],result['power'],result['toughness'],result['colors']))
        self.assertIn('Zombie',result['type_line']);self.assertIn('Cat',result['type_line']);self.assertEqual('{3}{G}',printed['mana_cost'])

    def test_copy_dependency_omission_and_unsupported_sibling_fail_closed(self):
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row=next(r for r in value['capabilities'] if r['id']=='token.creation.fixed_copy');row['status']='blocked';row['blockers']=['omitted-copy-owner']
        registry=CapabilityRegistry(value);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('Embalm {W}'),keywords=('Embalm',)),capability_registry=registry).status)
        partial=replace(query_record('Embalm {W}\nThis creature can rewrite history.'),keywords=('Embalm',))
        ir=compile_oracle_card(partial,capability_registry=load_default_capability_registry());self.assertNotEqual('exact',ir.status)
        from types import SimpleNamespace
        from quorune.compiler.program_generation import generated_programs
        from quorune.card_programs.admission import program_has_complete_card_program_admission
        programs=generated_programs(SimpleNamespace(rulings=lambda _record:()),partial,capability_registry=load_default_capability_registry())
        activation=next(p for p in programs if p.event=='activate')
        self.assertTrue(any(h.get('requires_complete_card_program') for h in activation.handlers));self.assertFalse(program_has_complete_card_program_admission(activation))

    def test_keyword_marker_and_graveyard_snapshot_guard_reject_forged_input(self):
        from quorune.semantic_runtime.graveyard_copy_keyword import GraveyardCopyKeywordHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.token_creation import create_tokens,TokenCreationError
        handler=GraveyardCopyKeywordHandler();value={'handler_id':handler.handler_id,'schema_version':1,'event':'activate','keyword':'embalm','requires_complete_card_program':True}
        self.assertEqual('embalm',handler.validate(value))
        for changes in ({'schema_version':True},{'requires_complete_card_program':False},{'keyword':[]},{'event':'continuous'},{'source':'forged'}):
            with self.assertRaises(SemanticNodeError):handler.validate({**value,**changes})
        from types import SimpleNamespace
        host=SimpleNamespace(_require_seat=lambda *args,**kwargs:None)
        with self.assertRaisesRegex(TokenCreationError,'pinned snapshot'):create_tokens(host,'A',name='',copy_of='missing',copy_source_zone='graveyard')

    def test_omitting_no_mana_cost_exception_mutant_is_killed(self):
        from quorune import token_copy_recipes
        original=token_copy_recipes.apply_copy_exception
        def omitted(characteristics,exception):return original(characteristics,replace(exception,remove_mana_cost=False))
        with patch('test_graveyard_copy_keywords.apply_copy_exception',side_effect=omitted):
            with self.assertRaises(AssertionError):self.test_copy_exception_preserves_v1_bytes_and_rejects_forged_versions()


class GraveyardCopyRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'graveyard-copy.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/graveyard-copy-keyword-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Graveyard copy',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_fixed_embalm_and_eternalize_pay_exile_create_correct_tokens_and_replay(self):
        for name,color,stats,mana in (('Sacred Cat','W',(1,1),{'W':1}),('Adorned Pouncer','B',(4,4),{'W':2,'C':3})):
            s=self.session(318001 if color=='W' else 318002);e=s.engine;source=self.add(e,name,zone='graveyard')
            action=self.ready(s,source,mana);self.checkpoint(s);before=authoritative_state_hash(s.state)
            wrong=s.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
            accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.assertEqual('exile',s.state.cards[source.object_id].zone)
            self.resolve(s);tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A'];self.assertEqual(1,len(tokens))
            token=tokens[0];data=e._effective_card_data(token);self.assertEqual([color],data['colors']);self.assertEqual('',data['mana_cost']);self.assertEqual(0,data['mana_value'])
            self.assertIn('Zombie',data['type_line']);self.assertFalse(token.is_commander)
            self.assertEqual(stats,(e._numeric_stat(token.object_id,'power'),e._numeric_stat(token.object_id,'toughness')))
            self.assertEqual('Sacred Cat' if color=='W' else 'Adorned Pouncer',data['name']);self.replay(s,load=True)

    def test_actual_embalm_token_keeps_entry_trigger_and_copyable_exceptions(self):
        s=self.session(318003);e=s.engine;source=self.add(e,'Anointer Priest',zone='graveyard')
        action=self.ready(s,source,{'W':1,'C':3});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(s)
        if pending is not None and pending.kind=='trigger.order':
            counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers(self,s);self.resolve(s)
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertEqual(1,len(tokens));self.assertEqual(41,s.state.players['A'].life)
        for token in tokens:
            data=e._effective_card_data(token);self.assertEqual(0,data['mana_value']);self.assertEqual(['W'],data['colors']);self.assertIn('Zombie',data['type_line'])
        self.replay(s,load=True)
        from quorune.token_creation import token_copy_snapshot
        snapshot=token_copy_snapshot(e,tokens[0])
        self.assertEqual('',snapshot['characteristics']['mana_cost']);self.assertEqual(0,snapshot['characteristics']['mana_value'])

    def test_canonical_source_move_after_exile_cost_preserves_original_copy_snapshot(self):
        s=self.session(318004);e=s.engine;source=self.add(e,'Sacred Cat',zone='graveyard')
        action=self.ready(s,source,{'W':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        e.move_card(source.object_id,'graveyard');self.resolve(s)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertEqual(1,len(tokens));self.assertEqual('',e._effective_card_data(tokens[0])['mana_cost'])

    def test_canonical_token_replacement_pending_reload_retains_paid_source_snapshot(self):
        from common import register_token_quantity_multiplier
        from quorune.session import CommanderSession
        s=self.session(318006);e=s.engine
        for name in ('Anointed Procession','Doubling Season'):
            source=self.add(e,name);register_token_quantity_multiplier(self.db,e,source,self.registry)
        source=self.add(e,'Sacred Cat',zone='graveyard');action=self.ready(s,source,{'W':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',self.resolve(s).kind);self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.assertEqual(0,len([c for c in s.state.cards.values() if c.is_token]))
        before=authoritative_state_hash(s.state)
        rejected=s.act('pilot:B',{'action_id':'choose','replacement':'wrong'});self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pending';s.save(path);s=CommanderSession.load(self.db,path)
        for _ in range(8):
            if s.state.pending_decision is None or s.state.pending_decision.kind!='replacement.order':break
            principal=s.pending_principals()[0];ctx=s.packet(principal,full=True)['decision']['ctx']
            accepted=s.act(principal,{'action_id':'choose','replacement':ctx['options'][0]['id']});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(s);tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertEqual(4,len(tokens))
        for token in tokens:
            data=s.engine._effective_card_data(token);self.assertEqual('',data['mana_cost']);self.assertEqual(0,data['mana_value']);self.assertEqual(['W'],data['colors'])
        self.replay(s,load=True)

    def test_sorcery_timing_insufficient_payment_and_reused_source_reject_atomically(self):
        s=self.session(318005);e=s.engine;source=self.add(e,'Sacred Cat',zone='graveyard');action=self.ready(s,source,{'W':1});self.checkpoint(s)
        from quorune.rules.activation.availability import activation_availability
        from quorune.rules.activation.query import activated_abilities
        ability=activated_abilities(e,source)[0]
        e.state.active_player='B';self.assertEqual(('unavailable','sorcery_timing'),activation_availability(e,'A',source,ability));e.state.active_player='A'
        phase,step=e.state.phase,e.state.step;e.state.phase='combat';e.state.step='begin_combat'
        self.assertEqual(('unavailable','sorcery_timing'),activation_availability(e,'A',source,ability));e.state.phase=phase;e.state.step=step
        before=authoritative_state_hash(s.state)
        rejected=s.act('pilot:A',{'action_id':action['id'],'pay':{'U':1}});self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        before=authoritative_state_hash(s.state);rejected=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state));self.resolve(s);self.replay(s)
