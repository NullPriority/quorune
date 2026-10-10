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
from quorune.rules.temporary_ability_grant import BACKUP_CAPABILITY,decode_temporary_ability_grant,backup_node_capabilities
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class BackupCompilerTests(unittest.TestCase):
    def test_source_order_keeps_only_complete_following_printed_keyword_bundle(self):
        text='Flash\nBackup 2\nFlying, lifelink'
        ir=compile_oracle_card(replace(query_record(text),keywords=('Flash','Backup','Flying','Lifelink')),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        backup=next(n for f in ir.faces for n in f.nodes if 'backup' in n.mechanics)
        self.assertEqual('permanent.enter',backup.event);self.assertEqual(2,backup.effects[0]['amount'])
        self.assertEqual({'Flying','Lifelink'},set(backup.effects[1]['keywords']));self.assertNotIn('Flash',backup.effects[1]['keywords'])
        self.assertEqual({'field':'card','op':'eq','value':'$source.ref'},backup.event_condition)
        for text in ('Backup 1\nFlying\nThis creature rewrites history.','Backup X\nFlying'):
            self.assertNotEqual('exact',compile_oracle_card(replace(query_record(text),keywords=('Backup','Flying')),capability_registry=load_default_capability_registry()).status)
        ir=compile_oracle_card(replace(query_record('Backup 1, backup 1\nFlying'),keywords=('Backup','Flying')),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status);backups=[n for n in ir.faces[0].nodes if 'backup' in n.mechanics]
        self.assertEqual(2,len(backups));self.assertEqual(2,len({n.node_id for n in backups}))

    def test_triggered_and_activated_donations_use_independent_typed_children(self):
        for line,kind in (('Whenever this creature attacks, draw a card.','granted_triggered'),('{1}, Sacrifice this creature: Draw a card.','granted_activated')):
            ir=compile_oracle_card(replace(query_record('Backup 1\n'+line),keywords=('Backup',)),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            backup=next(n for n in ir.faces[0].nodes if 'backup' in n.mechanics)
            self.assertEqual(kind,backup.effects[1]['ability_fragments'][0]['kind'])
            self.assertTrue(any(n.kind.startswith('granted_') for n in ir.faces[0].nodes))

    def test_malformed_bundle_and_counter_dependency_fail_closed(self):
        value={'op':'apply_source_characteristics_until_end_of_turn','schema_version':7,'card':'$target.0','keywords':['Flying'],'ability_fragments':[],'exclude_source':True,'printed_only':True}
        self.assertEqual(('Flying',),decode_temporary_ability_grant(value)[0])
        for changes in ({'schema_version':True},{'keywords':[['Flying']]},{'keywords':['Flying','Flying']},{'exclude_source':1},{'ability_fragments':[{'kind':'static_component','value':{}}]},{'extra':1}):
            with self.assertRaises(ValueError):decode_temporary_ability_grant({**value,**changes})
        counter={'op':'place_counters','card':'$target.0','counter':'+1/+1','amount':1,'source':'$source'}
        schema={'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],'count':1}
        self.assertTrue(backup_node_capabilities(effects=(counter,value),target_schema=schema,mechanic_ids=('backup',)))
        for bad in (None,'counter',[],{**counter,'amount':True},{**counter,'source':'B'}):
            self.assertFalse(backup_node_capabilities(effects=(bad,value),target_schema=schema,mechanic_ids=('backup',)))
        registry_data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));row=next(r for r in registry_data['capabilities'] if r['id']=='counter.producer.fixed_effect');row['status']='blocked';row['blockers']=['omitted-counter-owner']
        registry=CapabilityRegistry(registry_data);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('Backup 1\nFlying'),keywords=('Backup','Flying')),capability_registry=registry).status)

    def test_omitting_source_order_mutant_is_killed(self):
        from quorune.compiler import backup_nodes
        original=backup_nodes._bundle
        def all_lines(record,face,nodes):return original(record,face,tuple(n for n in face.nodes if n not in nodes and n.text=='Flash')+nodes)
        with patch.object(backup_nodes,'_bundle',all_lines):
            with self.assertRaises(AssertionError):self.test_source_order_keeps_only_complete_following_printed_keyword_bundle()


class BackupRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'backup.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/backup-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Backup',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_backup_cast_donates_printed_bundle_to_other_creature_and_replays(self):
        s=self.session(319001);e=s.engine;source=self.add(e,'Boon-Bringer Valkyrie',zone='hand');recipient=self.add(e,'Generic Bound Body',seat='B')
        action=self.ready(s,source,{'W':2,'C':3});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(s);self.assertEqual('semantic.target',pending.kind)
        before=authoritative_state_hash(s.state)
        rejected=s.act('pilot:B',{'action_id':'choose','targets':[recipient.ref]});self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:A',{'action_id':'choose','targets':[recipient.ref]});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(1,s.state.cards[recipient.object_id].counters['+1/+1']);self.assertTrue({'flying','first strike','lifelink'}<=e._combat_keywords(s.state.cards[recipient.object_id]));self.replay(s,load=True)

    def test_actual_donated_sacrifice_activation_uses_recipient_and_private_draw_replay(self):
        s=self.session(319002);e=s.engine;source=self.add(e,'Scorn-Blade Berserker',zone='hand');recipient=self.add(e,'Generic Bound Body',seat='B')
        action=self.ready(s,source,{'B':1});s.state.players['B'].mana_pool['C']=1;self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.assertEqual('semantic.target',self.resolve(s).kind)
        accepted=s.act('pilot:A',{'action_id':'choose','targets':[recipient.ref]});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        for _ in range(8):
            if s.pending_principals()[0]=='pilot:B':break
            accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        actions=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a['id'].startswith('activate:'+recipient.ref+':'))
        count=len(s.state.players['B'].zones['hand'])
        accepted=s.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',s.state.cards[recipient.object_id].zone);self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.resolve(s);self.assertEqual(count+1,len(s.state.players['B'].zones['hand']));self.replay(s,load=True)

    def test_self_target_only_gets_counters_and_donated_keywords_expire(self):
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        for self_target in (True,False):
            s=self.session(319003 if self_target else 319004);e=s.engine;source=self.add(e,'Boon-Bringer Valkyrie',zone='hand');recipient=self.add(e,'Generic Bound Body')
            action=self.ready(s,source,{'W':2,'C':3});self.checkpoint(s)
            accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            target=source if self_target else recipient
            accepted=s.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            self.assertEqual(1,s.state.cards[target.object_id].counters['+1/+1'])
            if self_target:self.assertFalse(s.state.continuous_effects)
            else:self.assertTrue({'flying','first strike','lifelink'}<=e._combat_keywords(s.state.cards[target.object_id]))
            self.replay(s)
            expire_end_of_turn_continuous_effects(s.state)
            if not self_target:self.assertFalse(e._combat_keywords(s.state.cards[target.object_id]).intersection({'flying','first strike','lifelink'}))

    def test_actual_copy_entry_preserves_printed_order_and_excludes_gained_keyword(self):
        s=self.session(319005);e=s.engine;source=self.add(e,'Boon-Bringer Valkyrie');source.temporary_keywords.append('Haste')
        recipient=self.add(e,'Generic Bound Body',seat='B');spell=self.add(e,'Cackling Counterpart',zone='hand')
        action=self.ready(s,spell,{'U':2,'C':1});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.assertEqual('semantic.target',self.resolve(s).kind)
        accepted=s.act('pilot:A',{'action_id':'choose','targets':[recipient.ref]});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertEqual(1,s.state.cards[recipient.object_id].counters['+1/+1'])
        keywords=e._combat_keywords(s.state.cards[recipient.object_id]);self.assertTrue({'flying','first strike','lifelink'}<=keywords);self.assertNotIn('haste',keywords)
        self.replay(s,load=True)

    def test_actual_source_departure_preserves_locked_bundle_and_target_blink_invalidates(self):
        for response_name in ('Unsummon','Ephemerate'):
            s=self.session(319006 if response_name=='Unsummon' else 319007);e=s.engine;source=self.add(e,'Boon-Bringer Valkyrie',zone='hand');recipient=self.add(e,'Generic Bound Body',seat='B')
            response=self.add(e,response_name,seat='B',zone='hand');action=self.ready(s,source,{'W':2,'C':3});s.state.players['B'].mana_pool.update({'U':1,'W':1});self.checkpoint(s)
            accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            accepted=s.act('pilot:A',{'action_id':'choose','targets':[recipient.ref]});self.assertTrue(accepted.ok,accepted.summary)
            for _ in range(8):
                if s.pending_principals()[0]=='pilot:B':break
                accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
            action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==response.ref)
            accepted=s.act('pilot:B',{'action_id':action['id'],'targets':[source.ref if response_name=='Unsummon' else recipient.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            current=s.state.cards[recipient.object_id]
            if response_name=='Unsummon':
                self.assertEqual('hand',s.state.cards[source.object_id].zone);self.assertEqual(1,current.counters['+1/+1']);self.assertIn('lifelink',e._combat_keywords(current))
            else:self.assertFalse(current.counters);self.assertNotIn('lifelink',e._combat_keywords(current))
            self.replay(s,load=True)

    def test_actual_donated_attack_trigger_uses_recipient_target_and_replays(self):
        s=self.session(319008);e=s.engine;source=self.add(e,'Bola Slinger',zone='hand');recipient=self.add(e,'Generic Bound Body');target=self.add(e,'Generic Bound Body',seat='B',ref='opponent-tap-target')
        recipient.temporary_keywords.append('Haste')
        action=self.ready(s,source,{'W':1,'C':3});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        accepted=s.act('pilot:A',{'action_id':'choose','targets':[recipient.ref]});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        for _ in range(32):
            if s.state.pending_decision.kind=='combat.attackers':break
            self.assertEqual('priority',s.state.pending_decision.kind)
            accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('combat.attackers',s.state.pending_decision.kind)
        accepted=s.act('pilot:A',{'a':'attack','atk':{recipient.ref:'B'}});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.target',self.resolve(s).kind)
        accepted=s.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        self.assertTrue(s.state.cards[target.object_id].tapped);self.assertFalse(s.state.cards[source.object_id].tapped);self.replay(s,load=True)

    def test_counter_replacement_pending_reload_retains_separate_following_grant(self):
        from quorune.session import CommanderSession
        s=self.session(319009);e=s.engine;self.add(e,'Hardened Scales');self.add(e,'Doubling Season')
        source=self.add(e,'Boon-Bringer Valkyrie',zone='hand');recipient=self.add(e,'Generic Bound Body')
        action=self.ready(s,source,{'W':2,'C':3});self.checkpoint(s)
        accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        accepted=s.act('pilot:A',{'action_id':'choose','targets':[recipient.ref]});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',self.resolve(s).kind);self.assertFalse(s.state.cards[recipient.object_id].counters);self.assertNotIn('lifelink',e._combat_keywords(s.state.cards[recipient.object_id]))
        before=authoritative_state_hash(s.state)
        rejected=s.act('pilot:B',{'action_id':'choose','replacement':'wrong'});self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pending';s.save(path);s=CommanderSession.load(self.db,path)
        choices=s.packet('pilot:A',full=True)['decision']['ctx']['options'];scales=next(c for c in choices if 'hardened' in str(c).casefold())
        accepted=s.act('pilot:A',{'action_id':'choose','replacement':scales['id']});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
        current=s.state.cards[recipient.object_id];self.assertEqual(4,current.counters['+1/+1']);self.assertTrue({'flying','first strike','lifelink'}<=s.engine._combat_keywords(current))
        self.assertEqual(1,len(s.state.continuous_effects));self.replay(s,load=True)

    def test_actual_static_donations_apply_recipient_block_and_damage_queries(self):
        from quorune.toughness_assignment import current_toughness_assignment
        for name in ('Chomping Kavu','Streetwise Negotiator'):
            s=self.session(319010 if name=='Chomping Kavu' else 319011);e=s.engine;source=self.add(e,name,zone='hand');recipient=self.add(e,'Generic Bound Body');blocker=self.add(e,'Generic Bound Body',seat='B',ref='static-donation-blocker')
            action=self.ready(s,source,{'G':1,'C':3});self.checkpoint(s)
            accepted=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            accepted=s.act('pilot:A',{'action_id':'choose','targets':[recipient.ref]});self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)
            self.replay(s,load=True)
            current=s.state.cards[recipient.object_id]
            if name=='Streetwise Negotiator':self.assertTrue(current_toughness_assignment(e,current,power=3,toughness=7));self.assertFalse(current_toughness_assignment(e,s.state.cards[blocker.object_id],power=2,toughness=6))
            else:
                current.attacking='B'
                self.assertFalse(e._can_block(current,s.state.cards[blocker.object_id])[0])
