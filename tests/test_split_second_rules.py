from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.semantic_runtime.static_cast_rules import SPLIT_SECOND_CAPABILITY,SplitSecondHandler,STATIC_CAST_RULE_EVENT,SPLIT_SECOND_HANDLER_ID
from quorune.semantic_runtime import SemanticNodeError
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class SplitSecondCompilerTests(unittest.TestCase):
    def test_keyword_is_a_stack_local_source_spanned_static_declaration(self):
        text='Split second\nThis spell deals 2 damage to any target.'
        c=replace(query_record(text),type_line='Instant',keywords=('Split second',))
        ir=compile_oracle_card(c,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0];self.assertEqual('stack',node.active_zone);self.assertEqual(STATIC_CAST_RULE_EVENT,node.event)
        self.assertEqual((0,len('Split second')),(node.span.start,node.span.end));self.assertIn(SPLIT_SECOND_CAPABILITY,node.capability_dependencies)
        self.assertEqual(SPLIT_SECOND_HANDLER_ID,node.handlers[0]['handler_id'])

    def test_malformed_descriptors_and_dependency_omission_fail_closed(self):
        valid={'handler_id':SPLIT_SECOND_HANDLER_ID,'schema_version':1,'event':STATIC_CAST_RULE_EVENT}
        self.assertTrue(SplitSecondHandler().validate(valid))
        for changes in ({'schema_version':True},{'event':'battlefield'},{'extra':1}):
            with self.assertRaises(SemanticNodeError):SplitSecondHandler().validate({**valid,**changes})
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row=next(r for r in raw['capabilities'] if r['id']==SPLIT_SECOND_CAPABILITY);row.update(status='blocked',blockers=['stack priority owner unavailable'])
        registry=CapabilityRegistry(raw);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('Split second\nDraw a card.'),type_line='Instant',keywords=('Split second',)),capability_registry=registry).status)

    def test_combined_flash_and_split_second_keep_independent_keyword_declarations(self):
        text='Flash, split second\nFlying'
        c=replace(query_record(text),keywords=('Flash','Split second','Flying'))
        ir=compile_oracle_card(c,capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=next(n for f in ir.faces for n in f.nodes if any(h.get('handler_id')==SPLIT_SECOND_HANDLER_ID for h in n.handlers))
        self.assertEqual('split second',text[node.span.start:node.span.end].lower());self.assertEqual('stack',node.active_zone)


class SplitSecondRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'split-second.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/split-second-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Split second',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def test_actual_sudden_shock_blocks_casts_and_nonmana_abilities_but_allows_mana(self):
        s=self.session(315001);e=s.engine
        spell=self.add(e,'Sudden Shock',zone='hand');response=self.add(e,'Unsummon',seat='B',zone='hand')
        ability=self.add(e,'Generic Bound Mentor',seat='B');land=self.add(e,'Island',seat='B')
        other=self.add(e,'Generic Bound Body',seat='B')
        ability.acquired_control_turn_count=-1
        action=self.ready(s,spell,{'R':1,'C':1});s.state.players['B'].mana_pool.update({'U':1,'C':2});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['D'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(8):
            if s.pending_principals()[0]=='pilot:B':break
            accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        actions=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a.get('card')==response.ref or a['id'].startswith('activate:'+ability.ref+':') for a in actions))
        mana=next(a for a in actions if a['id'].startswith('activate:'+land.ref+':'))
        before=authoritative_state_hash(s.state)
        rejected=s.act('pilot:B',{'a':'cast','card':response.ref,'targets':[ability.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        chosen=e._activated_abilities(s.state.cards[ability.object_id])[0]
        rejected=s.act('pilot:B',{'action_id':'activate:'+ability.ref+':'+chosen.ability_id,'targets':[other.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:B',{'action_id':mana['id']});self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(s.state.cards[land.object_id].tapped);self.resolve(s)
        self.assertEqual(38,s.state.players['D'].life);self.replay(s,load=True)
        e.permissions.invalidate_current();s.state.pending_decision=None;e.state.active_player='B';e.state.phase='precombat_main';e.state.step='main';e._grant_priority('B');e.pump();self.checkpoint(s)
        actions=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        self.assertTrue(any(a.get('card')==response.ref for a in actions));self.assertTrue(any(a['id'].startswith('activate:'+ability.ref+':') for a in actions))

    def test_cast_trigger_resolves_above_split_second_without_unlocking_priority(self):
        from quorune.semantic_runtime.static_cast_rules import split_second_is_active
        s=self.session(315002);e=s.engine
        observer=self.add(e,'Generic Split Second Cast Observer');spell=self.add(e,'Sudden Shock',zone='hand')
        action=self.ready(s,spell,{'R':1,'C':1});self.checkpoint(s);hand=len(s.state.players['A'].zones['hand'])
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['D'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(16):
            if any(i.kind=='triggered_ability' for i in s.state.stack):break
            accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(any(i.kind=='triggered_ability' for i in s.state.stack));self.assertTrue(split_second_is_active(e))
        for _ in range(32):
            if len(s.state.stack)==1:break
            accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(hand,len(s.state.players['A'].zones['hand']));self.assertTrue(split_second_is_active(e))
        self.resolve(s);self.replay(s,load=True)

    def test_split_second_copy_restricts_until_last_spell_copy_leaves(self):
        from quorune.semantic_runtime.static_cast_rules import split_second_is_active
        s=self.session(315003);e=s.engine;spell=self.add(e,'Sudden Shock',zone='hand')
        action=self.ready(s,spell,{'R':1,'C':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['D'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        original=next(i for i in s.state.stack if i.kind=='spell')
        copied=e._copy_stack_item(target=original,controller='B',targets=['C'],target_groups={},reason='Split second copied-characteristic contract')
        self.assertTrue(split_second_is_active(e))
        e.state.stack.remove(original);e.move_card(spell.object_id,'graveyard',log=False,semantic_events=False)
        self.assertTrue(split_second_is_active(e))
        for _ in range(16):
            if not s.state.stack:break
            accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(38,s.state.players['C'].life);self.assertFalse(split_second_is_active(e))
        self.assertEqual(40,s.state.players['D'].life)

    def test_split_second_query_omission_mutant_is_killed_by_actual_offer(self):
        with patch('quorune.semantic_runtime.static_cast_rules.split_second_is_active',return_value=False):
            with self.assertRaises(AssertionError):self.test_actual_sudden_shock_blocks_casts_and_nonmana_abilities_but_allows_mana()

    def test_only_trusted_stack_spell_sources_publish_the_restriction(self):
        from quorune.semantic_runtime.static_cast_rules import split_second_is_active
        s=self.session(315005);e=s.engine;spell=self.add(e,'Sudden Shock',zone='hand')
        action=self.ready(s,spell,{'R':1,'C':1});accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['D'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(split_second_is_active(e))
        with patch.object(e,'semantic_program_is_current_trusted',return_value=False):self.assertFalse(split_second_is_active(e))
        item=next(i for i in s.state.stack if i.kind=='spell');item.kind='triggered_ability'
        self.assertFalse(split_second_is_active(e));item.kind='spell'

    def test_actual_morph_face_up_special_action_remains_legal_during_split_second(self):
        from quorune.semantic_runtime.static_cast_rules import split_second_is_active
        s=self.session(315004);e=s.engine
        morph=self.add(e,'Battering Craghorn',zone='hand');spell=self.add(e,'Sudden Shock',zone='hand')
        self.ready(s,morph,{'R':3,'C':9})
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='cast-morph:'+morph.ref)
        self.checkpoint(s);accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertTrue(s.state.cards[morph.object_id].face_down)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spell.ref and a['id']=='cast:'+spell.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['D'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(split_second_is_active(e))
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='turn-face-up:'+morph.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertFalse(s.state.cards[morph.object_id].face_down);self.assertTrue(split_second_is_active(e));self.resolve(s);self.replay(s,load=True)


if __name__=='__main__':unittest.main()
