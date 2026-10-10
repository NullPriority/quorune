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
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.player_target_protection import PlayerTargetProtectionHandler,PLAYER_TARGET_PROTECTION_CAPABILITY_ID
from quorune.target_protection import TargetProtectionSnapshot,TargetProtectionVerdict,target_protection_verdict
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class PlayerProtectionCompilerTests(unittest.TestCase):
    def test_fixed_player_keywords_have_closed_source_spans_and_shared_capability(self):
        for keyword in ('hexproof','shroud'):
            ir=compile_oracle_card(query_record('You have '+keyword+'.'),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertEqual('battlefield',node.active_zone)
            self.assertEqual(keyword,node.handlers[0]['keyword']);self.assertIn(PLAYER_TARGET_PROTECTION_CAPABILITY_ID,node.capability_dependencies)
        for text in ('You have hexproof from red.','You and permanents you control have hexproof.','As long as you control an Island, you have shroud.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)

    def test_player_descriptor_dependency_and_malformed_shapes_fail_closed(self):
        handler=PlayerTargetProtectionHandler();value={'handler_id':handler.handler_id,'schema_version':1,'event':handler.event,'keyword':'hexproof'}
        self.assertEqual('hexproof',handler.validate(value))
        for changes in ({'schema_version':True},{'keyword':'protection'},{'keyword':[]},{'event':'spell.cast'},{'player':'B'}):
            with self.assertRaises(SemanticNodeError):handler.validate({**value,**changes})
        registry_data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row=next(r for r in registry_data['capabilities'] if r['id']=='target.revalidate_resolution');row['status']='blocked';row['blockers']=['omitted-target-owner']
        registry=CapabilityRegistry(registry_data);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(query_record('You have hexproof.'),capability_registry=registry).status)

    def test_player_controller_grid_and_omission_mutant_distinguish_shroud(self):
        for acting in 'ABCD':
            for keyword in ('hexproof','shroud'):
                value=TargetProtectionSnapshot(acting_controller=acting,protected_controller='A',target_is_player=True,target_keywords=frozenset({keyword}))
                expected=TargetProtectionVerdict.ALLOWED if keyword=='hexproof' and acting=='A' else TargetProtectionVerdict(keyword)
                self.assertEqual(expected,target_protection_verdict(value))
        from quorune import target_protection
        with patch.object(target_protection,'target_protection_verdict',return_value=TargetProtectionVerdict.ALLOWED):
            value=TargetProtectionSnapshot(acting_controller='B',protected_controller='A',target_is_player=True,target_keywords=frozenset({'hexproof'}))
            with self.assertRaises(AssertionError):self.assertEqual(TargetProtectionVerdict.HEXPROOF,target_protection.target_protection_verdict(value))


class PlayerProtectionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'player-protection.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/player-target-protection-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Player protection',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_sources_share_offers_commands_privacy_and_replay(self):
        for name,keyword in (('Aegis of the Gods','hexproof'),('True Believer','shroud')):
            s=self.session(317001 if keyword=='hexproof' else 317002);e=s.engine
            self.add(e,name,seat='B');self.add(e,name,seat='B',ref='second-protection')
            spell=self.add(e,'Shock',zone='hand');action=self.ready(s,spell,{'R':1});self.checkpoint(s)
            self.assertNotIn('B',action['target_schema']['legal_refs']);self.assertIn('C',action['target_schema']['legal_refs'])
            before=authoritative_state_hash(s.state)
            rejected=s.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'});self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
            wrong=s.act('pilot:B',{'action_id':action['id'],'targets':['C'],'pay':'auto'});self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
            own_row={'category':'player','ref':'B','zone':'player'}
            from quorune.target_protection_engine_adapter import target_protection_verdict_for_row
            self.assertEqual(TargetProtectionVerdict.ALLOWED if keyword=='hexproof' else TargetProtectionVerdict.SHROUD,target_protection_verdict_for_row(e,acting_controller='B',row=own_row,source_ref=None))
            accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['C'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            self.assertEqual(38,s.state.players['C'].life);self.assertEqual(40,s.state.players['B'].life);self.replay(s,load=True)

    def test_phased_removed_or_untrusted_source_component_does_not_grant_player_keywords(self):
        from quorune.semantic_runtime.player_target_protection import current_player_target_keywords
        s=self.session(317006);e=s.engine;source=self.add(e,'Aegis of the Gods',seat='B')
        self.assertEqual(frozenset({'hexproof'}),current_player_target_keywords(e,'B'))
        source.phased_out=True;self.assertEqual(frozenset(),current_player_target_keywords(e,'B'))
        source.phased_out=False
        with patch.object(e,'semantic_program_is_current_trusted',return_value=False):self.assertEqual(frozenset(),current_player_target_keywords(e,'B'))
        with patch.object(e,'_effective_ability_fragments',return_value=()):self.assertEqual(frozenset(),current_player_target_keywords(e,'B'))
        e.move_card(source.object_id,'hand');self.assertEqual(frozenset(),current_player_target_keywords(e,'B'))

    def test_actual_own_target_is_allowed_by_hexproof_and_rejected_by_shroud(self):
        for name,allowed in (('Aegis of the Gods',True),('True Believer',False)):
            s=self.session(317007 if allowed else 317008);e=s.engine;self.add(e,name)
            spell=self.add(e,'Shock',zone='hand');action=self.ready(s,spell,{'R':1});self.checkpoint(s)
            self.assertEqual(allowed,'A' in action['target_schema']['legal_refs'])
            before=authoritative_state_hash(s.state)
            accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['A'],'pay':'auto'});self.assertEqual(allowed,accepted.ok,accepted.summary)
            if allowed:self.resolve(s);self.assertEqual(38,s.state.players['A'].life)
            else:
                self.assertEqual(before,authoritative_state_hash(s.state))
                accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['C'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            self.replay(s)

    def test_actual_source_departure_restores_target_after_unsummon_and_replay(self):
        s=self.session(317003);e=s.engine;source=self.add(e,'Aegis of the Gods',seat='B')
        bounce=self.add(e,'Unsummon',zone='hand');shock=self.add(e,'Shock',zone='hand')
        action=self.ready(s,bounce,{'U':1,'R':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'];action=next(r for r in actions if r.get('card')==shock.ref)
        self.assertIn('B',action['target_schema']['legal_refs'])
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(38,s.state.players['B'].life);self.replay(s)

    def test_actual_control_change_moves_player_protection_and_replays(self):
        s=self.session(317004);e=s.engine;source=self.add(e,'Aegis of the Gods',seat='B')
        theft=self.add(e,'Act of Treason',zone='hand');shock=self.add(e,'Shock',zone='hand')
        action=self.ready(s,theft,{'R':4,'C':2});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual('A',s.state.cards[source.object_id].controller)
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'];action=next(r for r in actions if r.get('card')==shock.ref)
        self.assertIn('B',action['target_schema']['legal_refs']);self.assertIn('A',action['target_schema']['legal_refs'])
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(38,s.state.players['B'].life);self.replay(s)

    def test_actual_flash_entry_invalidates_existing_player_target_at_resolution(self):
        s=self.session(317005);e=s.engine;shock=self.add(e,'Shock',zone='hand')
        self.add(e,'Vedalken Orrery',seat='B');source=self.add(e,'Aegis of the Gods',seat='B',zone='hand')
        action=self.ready(s,shock,{'R':1});s.state.players['B'].mana_pool.update({'W':2,'C':2});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(8):
            if s.pending_principals()[0]=='pilot:B':break
            accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref)
        accepted=s.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.assertEqual(40,s.state.players['B'].life);self.replay(s,load=True)
