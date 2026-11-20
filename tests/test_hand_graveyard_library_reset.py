"""Pinned CR 121.2c/701.24: whole hidden hand plus public graveyard reset."""
from dataclasses import replace,asdict
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT,packet_without_decision_capability
from quorune.graveyard_recycling_model import (
    GraveyardRecyclingSpec,GRAVEYARD_RECYCLING_OPERATION as OP,
    GRAVEYARD_RECYCLING_CAPABILITY as OLD_CAP,HAND_GRAVEYARD_RECYCLING_CAPABILITY as CAP,
)
from quorune.compiler.graveyard_recycling_templates import graveyard_recycling_template
from quorune.rules.graveyard_recycling import (
    graveyard_recycling_node_capabilities,graveyard_recycling_runtime_dependencies,
    resolve_graveyard_recycling,
)
import test_graveyard_recycling as old
import test_public_condition_self_replacements as controls
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry,CapabilityRegistry
from quorune.oracle_ir import compile_oracle_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database

FIXTURE=ROOT/'tests/fixtures/hand-graveyard-library-reset-cards.json'
CANDIDATES=('Timetwister','Time Reversal','Echo of Eons','Game Plan','Temporal Cascade')

class HandGraveyardResetModelTests(unittest.TestCase):
    def effect(self,scope='all_players',player='all'):
        return {'op':OP,'recycling':GraveyardRecyclingSpec(scope,2).to_dict(),'player':player}
    def test_v2_has_closed_mixed_origins_while_every_v1_payload_retains_its_encoding(self):
        for scope in ('controller','target_player','all_players'):
            raw={'scope':scope,'schema_version':1};spec=GraveyardRecyclingSpec.from_dict(raw)
            self.assertEqual(raw,spec.to_dict());self.assertEqual(raw,asdict(spec));self.assertEqual(('graveyard',),spec.origins)
        for scope in ('controller','all_players'):
            raw={'scope':scope,'schema_version':2,'include_hand':True}
            spec=GraveyardRecyclingSpec.from_dict(json.loads(json.dumps(raw)))
            self.assertEqual(raw,spec.to_dict());self.assertEqual(('hand','graveyard'),spec.origins)
        raw=self.effect()['recycling']
        for value in ({'scope':'all_players','schema_version':2},{**raw,'schema_version':True},
                {**raw,'include_hand':1},{**raw,'include_hand':False},{**raw,'scope':'target_player'},
                {**raw,'zones':['hand','graveyard','battlefield']},{**raw,'schema_version':1}):
            with self.assertRaises(ValueError):GraveyardRecyclingSpec.from_dict(value)
    def test_only_whole_fixed_hands_and_graveyards_lower_through_the_existing_instruction(self):
        for text,scope in (('Each player shuffles their hand and graveyard into their library.','all_players'),
                ('Shuffle your hand and graveyard into your library.','controller')):
            t=graveyard_recycling_template(text);self.assertEqual(scope,t.spec.scope)
            _,effects,schema,mechanics=t.compiled();self.assertIn(CAP,graveyard_recycling_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics))
            self.assertEqual(OP,effects[0]['op']);self.assertIsNone(schema)
        for text in ('Target player shuffles their hand and graveyard into their library.',
                'Each opponent shuffles their hand and graveyard into their library.',
                'Shuffle up to two cards from your hand and graveyard into your library.',
                'Shuffle your hand, graveyard, and permanents into your library.',
                'Shuffle your hand and graveyard into your library, then draw seven cards.'):
            self.assertIsNone(graveyard_recycling_template(text))
    def test_runtime_minimum_is_payload_driven_even_when_coverage_is_removed(self):
        effect=self.effect();program=SimpleNamespace(effects=(effect,),handlers=(),target_schema=None,coverage=())
        self.assertIn(CAP,graveyard_recycling_runtime_dependencies(program))
        self.assertIn(OLD_CAP,graveyard_recycling_runtime_dependencies(program))
        for change in ({'player':'$controller'},{'recycling':{**effect['recycling'],'include_hand':False}},{'extra':1}):
            program.effects=({**effect,**change},)
            with self.assertRaises(ValueError):graveyard_recycling_runtime_dependencies(program)
    def host(self):
        host,calls=old.GraveyardRecyclingModelTests.host(self)
        for player in 'ABCD':host.state.players[player].zones['hand']=[]
        for player in 'AC':
            oid='hand-'+player;host.state.players[player].zones['hand'].append(oid)
            host.state.cards[oid]=SimpleNamespace(ref=oid+'@1',zone='hand',owner=player)
        return host,calls
    def test_complete_mixed_private_public_batch_commits_before_every_shuffle_in_apnap_order(self):
        host,calls=self.host();result=resolve_graveyard_recycling(host,self.effect(),actor='A',reason='reset')
        self.assertEqual(('grave-B@1','hand-C@1','hand-A@1','grave-A@1'),result)
        self.assertEqual(['move','shuffle','shuffle','shuffle','shuffle'],[r[0] for r in calls])
        self.assertEqual(('hand','graveyard'),calls[0][1].expected_zones)
        self.assertEqual(list('BCDA'),[r[1] for r in calls[1:]])
        for player in 'ABCD':
            for zone in ('hand','graveyard'):host.state.players[player].zones[zone].clear()
        calls.clear();resolve_graveyard_recycling(host,self.effect(),actor='A',reason='empty')
        self.assertEqual([('shuffle',p) for p in 'BCDA'],calls)
        host,calls=self.host()
        result=resolve_graveyard_recycling(host,self.effect('controller','A'),actor='A',reason='one owner')
        self.assertEqual(('hand-A@1','grave-A@1'),result)
        self.assertEqual(['move','shuffle'],[r[0] for r in calls]);self.assertEqual('A',calls[1][1])
        self.assertEqual(['hand-C'],host.state.players['C'].zones['hand'])
        host,calls=self.host()
        with self.assertRaises(ValueError):resolve_graveyard_recycling(host,self.effect('controller','A'),actor='B',reason='foreign controller')
        self.assertFalse(calls)
    def test_every_hidden_and_public_member_validates_before_mutation_and_replacement_pause(self):
        for defect in ('missing','wrong_owner','stale','duplicate'):
            host,calls=self.host()
            if defect=='missing':del host.state.cards['hand-C']
            elif defect=='wrong_owner':host.state.cards['hand-C'].owner='D'
            elif defect=='stale':host.state.cards['hand-C'].zone='library'
            else:host.state.players['A'].zones['hand'].append('hand-C')
            with self.assertRaises(ValueError):resolve_graveyard_recycling(host,self.effect(),actor='A',reason='invalid')
            self.assertFalse(calls)
        host,calls=self.host()
        class Paused(Exception):pass
        def suspend(intent):calls.append(('pending',intent));raise Paused()
        host.move_objects_simultaneously_intent=suspend
        with self.assertRaises(Paused):resolve_graveyard_recycling(host,self.effect(),actor='A',reason='pending')
        self.assertEqual(['pending'],[r[0] for r in calls])

class HandGraveyardResetCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()
    def record(self,text):return old.GraveyardRecyclingCompilerTests.record(self,text)
    def test_complete_wheel_retains_ordered_draw_and_unsupported_extra_zone_siblings(self):
        text='Each player shuffles their hand and graveyard into their library, then draws seven cards.'
        ir=compile_oracle_card(self.record(text),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals);node=ir.faces[0].nodes[0]
        self.assertEqual([OP,'draw_each_player'],[e['op'] for e in node.effects]);self.assertIn(CAP,node.capability_dependencies)
        self.assertEqual(7,node.effects[1]['count'])
        for bad in (text.replace('hand and graveyard','hand, graveyard, and permanents'),text+' You exile the top ten cards of your library.'):
            self.assertNotEqual('exact',compile_oracle_card(self.record(bad),capability_registry=self.registry).status)
    def test_new_dependency_withdrawal_preserves_every_old_graveyard_scope(self):
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['Hand reset withdrawn'])
        registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        for text,exact in (('Each player shuffles their hand and graveyard into their library.',False),
                ('Shuffle your graveyard into your library.',True),('Each player shuffles their graveyard into their library.',True)):
            self.assertEqual(exact,compile_oracle_card(self.record(text),capability_registry=registry).status=='exact')

class HandGraveyardResetRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.PublicConditionSelfReplacementTests.session
    start=controls.PublicConditionSelfReplacementTests.start
    add=controls.PublicConditionSelfReplacementTests.add
    prepare=controls.PublicConditionSelfReplacementTests.prepare
    ready=controls.PublicConditionSelfReplacementTests.ready
    checkpoint=controls.PublicConditionSelfReplacementTests.checkpoint
    resolve=controls.PublicConditionSelfReplacementTests.resolve
    replay=controls.PublicConditionSelfReplacementTests.replay
    binding=controls.PublicConditionSelfReplacementTests.binding
    priority=controls.PublicConditionSelfReplacementTests.priority
    private=controls.PublicConditionSelfReplacementTests.private
    cast=controls.PublicConditionSelfReplacementTests.cast
    reject=controls.PublicConditionSelfReplacementTests.reject
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'hand-graveyard-reset.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Hand graveyard resets',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()
    def test_originals_have_complete_normal_registration_and_material_neighbors_remain_unready(self):
        s=self.start(790000)
        for name in CANDIDATES:
            card=self.add(s.engine,name,zone='hand');b=self.binding(s,card)
            self.assertTrue(b['strict_capability_ready'],(name,b['blockers']));self.assertEqual('capability_closed',b['trust_basis'])
        for name in ('Midnight Clock','Time Spiral',"Lich's Mirror"):
            card=self.add(s.engine,name,zone='hand');self.assertFalse(self.binding(s,card)['strict_capability_ready'],name)
    def setup_reset(self,name,seed):
        s=self.start(seed);source=self.add(s.engine,name,zone='hand')
        self.assertTrue(self.binding(s,source)['strict_capability_ready'])
        secret=self.add(s.engine,'Llanowar Elves',seat='A',zone='hand',ref='reset-own-secret')
        for seat in 'BCD':self.add(s.engine,'Island',seat=seat,zone='hand',ref='reset-hidden-'+seat)
        for seat in 'ABCD':self.add(s.engine,'Forest',seat=seat,zone='graveyard',ref='reset-grave-'+seat)
        private=self.private(s);self.ready(s);return s,source,private,secret
    def private_replay(self,s,private):
        current=s.state.cards[private.object_id]
        for seat in 'ABCD':
            raw=s.packet('pilot:'+seat,full=True)
            if 'pilot:'+seat in s.pending_principals():self.assertIsInstance(raw['decision']['cap'],str)
            else:self.assertNotIn('cap',raw.get('decision') or {})
            packet=json.dumps(packet_without_decision_capability(raw),sort_keys=True)
            if current.zone=='hand' and seat==current.owner:self.assertIn(private.ref,packet)
            else:self.assertNotIn(private.ref,packet)
        self.replay(s,load=True)
    def pools(self,s):
        return {p:set(s.state.players[p].zones['library']+s.state.players[p].zones['hand']+s.state.players[p].zones['graveyard']) for p in 'ABCD'}
    def counts(self,s):return {p:s.state.players[p].stats.get('shuffle_count',0) for p in 'ABCD'}
    def cast_offered(self,s,source,**fields):
        self.priority(s,'A');rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        row=next(r for r in rows if r['id']=='cast:'+source.ref)
        command={'action_id':row['id'],'targets':[],'pay':'auto',**fields};self.reject(s,command,'D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);return row
    def observed_draws(self,s,source,before,*,shuffle_delta=1):
        from quorune import engine as draw_entry
        original=draw_entry.begin_draw_sequence;calls=[]
        def observe(host,seat,count,**kwargs):
            self.assertEqual({p:before[p]+shuffle_delta for p in 'ABCD'},self.counts(s))
            self.assertEqual('stack',s.state.cards[source.object_id].zone)
            calls.append((seat,count));return original(host,seat,count,**kwargs)
        with patch.object(draw_entry,'begin_draw_sequence',side_effect=observe):self.assertIsNone(self.resolve(s))
        self.assertEqual([(p,7) for p in 'ABCD'],calls)
    def assert_reset(self,s,source,pools,before,*,destination='graveyard',draws=None):
        for seat in 'ABCD':
            current=s.state.players[seat]
            self.assertEqual((draws or {}).get(seat,7),len(current.zones['hand']))
            self.assertEqual(pools[seat],set(current.zones['hand']+current.zones['library']))
            self.assertEqual(before[seat]+1,current.stats.get('shuffle_count',0))
            for oid in current.zones['library']:
                self.assertFalse(s.state.cards[oid].known_to)
        self.assertEqual(destination,s.state.cards[source.object_id].zone)
        self.assertEqual([source.object_id] if destination=='graveyard' else [],s.state.players['A'].zones['graveyard'])
    def ordinary_reset(self,name,seed,destination):
        s,source,private,secret=self.setup_reset(name,seed);self.cast_offered(s,source)
        pools=self.pools(s);self.assertNotIn(source.object_id,pools['A']);before=self.counts(s)
        s=self.replay(s,load=True);self.observed_draws(s,source,before)
        self.assert_reset(s,source,pools,before,destination=destination);self.private_replay(s,private)
    def test_actual_timetwister_shuffles_all_hidden_and_public_zones_before_apnap_draws(self):self.ordinary_reset('Timetwister',790010,'graveyard')
    def test_actual_time_reversal_keeps_itself_out_of_the_shuffle_and_finishes_in_exile(self):self.ordinary_reset('Time Reversal',790011,'exile')
    def test_actual_game_plan_solo_preserves_assist_sibling_and_mandatory_self_exile(self):self.ordinary_reset('Game Plan',790012,'exile')
    def test_actual_echo_cast_and_flashback_each_reset_four_players_with_exile_after_flashback(self):
        from quorune.flashback import FLASHBACK_CAST_OPTION_ID
        s,source,private,secret=self.setup_reset('Echo of Eons',790020);self.cast_offered(s,source)
        pools=self.pools(s);before=self.counts(s);self.observed_draws(s,source,before)
        self.assert_reset(s,source,pools,before);self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id']=='cast:'+source.ref)
        self.assertTrue(any(p['id']==FLASHBACK_CAST_OPTION_ID for p in row['cost_options']))
        command={'action_id':row['id'],'from':'graveyard','cost_option':FLASHBACK_CAST_OPTION_ID,'targets':[],'pay':'auto'}
        self.reject(s,command,'D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        pools=self.pools(s);before=self.counts(s);s=self.replay(s,load=True);self.observed_draws(s,source,before)
        self.assert_reset(s,source,pools,before,destination='exile');self.private_replay(s,private)
    def test_actual_cascade_offers_each_mode_and_paid_entwine_preserves_both_in_printed_order(self):
        for modes in (['mode_1'],['mode_2'],['mode_2','mode_1']):
            with self.subTest(modes=modes):
                s,source,private,secret=self.setup_reset('Temporal Cascade',790030+len(modes)+(modes[0]=='mode_2'))
                before=self.counts(s);initial_hands={p:len(s.state.players[p].zones['hand']) for p in 'ABCD'}
                mana_before=sum(s.state.players['A'].mana_pool.values())
                self.priority(s,'A');offered=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id']=='cast:'+source.ref)
                if len(modes)==2:
                    cheap=next(r['id'] for r in offered['cost_options'] if len(r.get('target_schema',{}).get('legal_modes',[]))==1)
                    self.reject(s,{'action_id':offered['id'],'modes':modes,'targets':[],'pay':'auto','cost_option':cheap},'A')
                row=self.cast_offered(s,source,modes=modes)
                self.assertEqual(9 if len(modes)==2 else 7,mana_before-sum(s.state.players['A'].mana_pool.values()))
                if len(modes)==2:self.assertEqual(['mode_1','mode_2'],s.state.stack[-1].modes)
                pools=self.pools(s);s=self.replay(s,load=True)
                if modes==['mode_1']:
                    self.assertIsNone(self.resolve(s))
                    for p in 'ABCD':self.assertFalse(s.state.players[p].zones['hand']);self.assertEqual(pools[p],set(s.state.players[p].zones['library']))
                elif modes==['mode_2']:
                    self.observed_draws(s,source,before,shuffle_delta=0)
                    for p in 'ABCD':self.assertEqual(initial_hands[p]-(p=='A')+7,len(s.state.players[p].zones['hand']))
                else:
                    self.observed_draws(s,source,before);self.assert_reset(s,source,pools,before)
                for p in 'ABCD':self.assertEqual(before[p]+(modes!=['mode_2']),s.state.players[p].stats.get('shuffle_count',0))
                self.private_replay(s,private)
    def test_new_capability_is_required_without_declared_coverage_and_old_wire_stays_independent(self):
        s=self.start(790040);source=self.add(s.engine,'Timetwister',zone='hand')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.effects)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAP).update(status='blocked',blockers=['Hand recycling omitted'])
        registry=CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        for candidate in (program,replace(program,coverage=(),capability_dependencies=(),capability_closure=None)):
            b=bind_semantic_program_runtime(candidate,capability_registry=registry,profile='commander_review')
            self.assertFalse(b['strict']);self.assertIn(CAP,b['required_registered_capabilities'])
    def test_fresh_hand_omission_mutation_is_killed_by_actual_complete_timetwister(self):
        original=GraveyardRecyclingSpec.origins
        with patch.object(GraveyardRecyclingSpec,'origins',new=property(lambda spec:('graveyard',))):
            with self.assertRaises(AssertionError):self.test_actual_timetwister_shuffles_all_hidden_and_public_zones_before_apnap_draws()

    def test_actual_assist_helper_pays_generic_mana_and_decline_preserves_the_original(self):
        from quorune.rules.casting.assist_frame import AssistCastFrame
        unavailable,spell,_,_=self.setup_reset('Game Plan',790049)
        for seat in 'AB':unavailable.state.players[seat].mana_pool.update(dict.fromkeys('CWUBRG',0))
        unavailable.state.players['B'].mana_pool['U']=6;self.prepare(unavailable,mana={'C':1});self.checkpoint(unavailable)
        self.priority(unavailable,'A');rows=unavailable.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(r['id']=='cast:'+spell.ref for r in rows))
        self.reject(unavailable,{'action_id':'cast:'+spell.ref,'targets':[],'pay':'auto','assist_helper':'B'},'A')
        for decline in (False,True):
            with self.subTest(decline=decline):
                s,source,private,secret=self.setup_reset('Game Plan',790050+decline)
                for seat in 'AB':s.state.players[seat].mana_pool.update(dict.fromkeys('CWUBRG',0))
                s.state.players['B'].mana_pool.update({'U':6});self.prepare(s,mana={'U':1});self.checkpoint(s)
                self.cast_offered(s,source,assist_helper='B')
                self.assertEqual('cast.assist',s.state.pending_decision.kind)
                self.assertEqual('helper_mana',AssistCastFrame.from_dict(s.state.pending_decision.continuation).stage)
                self.assertNotIn(secret.ref,json.dumps(s.packet('pilot:B',full=True)))
                for seat in ('B','A'):
                    command={'action_id':'assist:finish-mana'};self.reject(s,command,'D')
                    result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
                self.assertEqual('helper_payment',AssistCastFrame.from_dict(s.state.pending_decision.continuation).stage)
                self.reject(s,{'action_id':'assist:contribute','assist_amount':6},'B')
                s=self.replay(s,load=True)
                result=s.act('pilot:B',{'action_id':'assist:contribute','assist_amount':0 if decline else 5});self.assertTrue(result.ok,result.summary)
                if decline:
                    self.assertFalse(s.state.stack);self.assertEqual('hand',s.state.cards[source.object_id].zone)
                    self.assertEqual(1,sum(s.state.players['A'].mana_pool.values()));self.assertEqual(6,sum(s.state.players['B'].mana_pool.values()))
                    self.assertEqual({p:0 for p in 'ABCD'},self.counts(s));self.private_replay(s,private);continue
                self.assertEqual('caster_payment',AssistCastFrame.from_dict(s.state.pending_decision.continuation).stage)
                s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'assist:pay'});self.assertTrue(result.ok,result.summary)
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.assertEqual(1,sum(s.state.players['B'].mana_pool.values()))
                self.assertEqual(6,s.state.stack[-1].mana_spent_counts.to_dict()['amounts']['U'])
                pools=self.pools(s);before=self.counts(s);self.observed_draws(s,source,before)
                self.assert_reset(s,source,pools,before,destination='exile');self.private_replay(s,private)
    def test_actual_commander_owner_choice_suspends_private_and_public_members_before_any_shuffle(self):
        for index,(name,decline) in enumerate((('Timetwister',False),('Timetwister',True),('Temporal Cascade',False),('Temporal Cascade',True))):
            with self.subTest(name=name,decline=decline):
                s,source,private,secret=self.setup_reset(name,790060+index)
                commander_id=s.state.players['A'].zones['command'][0]
                s.engine.move_card(commander_id,'graveyard',reason='Fixture commander graveyard boundary')
                s.engine.permissions.invalidate_current();s.state.pending_decision=None;self.assertTrue(s.engine._stabilize())
                result=s.act('pilot:A',{'a':'choose','choice':'remain'});self.assertTrue(result.ok,result.summary)
                self.ready(s);self.cast_offered(s,source,**({'modes':['mode_2','mode_1']} if name=='Temporal Cascade' else {}));before=self.counts(s);pools=self.pools(s)
                self.assertEqual('replacement.order',self.resolve(s).kind)
                self.assertEqual(before,self.counts(s));self.assertEqual('hand',s.state.cards[secret.object_id].zone)
                for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
                s=self.replay(s,load=True);options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
                chosen=next(r for r in options if bool(r.get('decline'))==decline)
                command={'action_id':'choose','replacement':chosen['id']};self.reject(s,command,'D')
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                for _ in range(4):
                    pending=self.resolve(s)
                    if pending is None:break
                    self.assertEqual('replacement.order',pending.kind);self.assertEqual(['A'],pending.actors)
                    options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
                    draw_keep=next(r for r in options if r.get('decline'))
                    command={'action_id':'choose','replacement':draw_keep['id']};self.reject(s,command,'D')
                    s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                else:self.fail('Commander draw replacement did not finish')
                if not decline:
                    pools['A'].remove(commander_id);self.assertEqual('command',s.state.cards[commander_id].zone)
                else:self.assertIn(s.state.cards[commander_id].zone,('hand','library'))
                self.assert_reset(s,source,pools,before);self.private_replay(s,private)
    def test_fault_after_two_actual_shuffles_rolls_back_hidden_pool_order_and_draws_then_retries(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import GameRuleError
        s,source,private,secret=self.setup_reset('Timetwister',790070);self.cast_offered(s,source)
        pools=self.pools(s);before_counts=self.counts(s);original=ZoneTransitionOwner.shuffle_library;calls=[];failed=None
        def fault(owner,seat,**kwargs):
            result=original(owner,seat,**kwargs);calls.append(seat)
            if len(calls)==2:raise GameRuleError('Injected hand reset shuffle failure')
            return result
        with patch.object(ZoneTransitionOwner,'shuffle_library',new=fault):
            for _ in range(8):
                principal=s.pending_principals()[0];before=authoritative_state_hash(s.state)
                result=s.act(principal,{'action_id':'pass'})
                if not result.ok:
                    self.assertIn('Injected hand reset shuffle failure',result.summary);self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
        self.assertIsNotNone(failed);self.assertEqual(['A','B'],calls);self.assertEqual(before_counts,self.counts(s))
        self.assertEqual('hand',s.state.cards[secret.object_id].zone);self.assertEqual(pools,self.pools(s))
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assert_reset(s,source,pools,before_counts);self.private_replay(s,private)
    def test_actual_wheel_uses_canonical_draw_doubling_after_every_shuffle_commits(self):
        s,source,private,secret=self.setup_reset('Timetwister',790080)
        reflection=self.add(s.engine,'Thought Reflection',seat='C');self.assertTrue(self.binding(s,reflection)['strict_capability_ready'])
        self.ready(s);self.cast_offered(s,source);pools=self.pools(s);before=self.counts(s)
        self.observed_draws(s,source,before);self.assert_reset(s,source,pools,before,draws={'C':14});self.private_replay(s,private)
    def test_short_library_failure_waits_until_other_players_draw_and_source_self_exiles(self):
        from quorune import engine as draw_entry
        s,source,private,secret=self.setup_reset('Time Reversal',790090)
        for zone in ('library','hand','graveyard'):
            for oid in tuple(s.state.players['B'].zones[zone]):s.engine.move_card(oid,'exile',reason='Fixture empty reset pool')
        self.ready(s);self.cast_offered(s,source);before=self.counts(s);short=len(self.pools(s)['B']);self.assertLess(short,7)
        original=draw_entry.begin_draw_sequence;calls=[]
        def observe(host,seat,count,**kwargs):
            self.assertEqual({p:before[p]+1 for p in 'ABCD'},self.counts(s));calls.append(seat)
            if seat=='C':
                self.assertIn('B',s.engine.active_seats);self.assertEqual(short,len(s.state.players['B'].zones['hand']))
            return original(host,seat,count,**kwargs)
        with patch.object(draw_entry,'begin_draw_sequence',side_effect=observe):self.assertIsNone(self.resolve(s))
        self.assertEqual(list('ABCD'),calls);self.assertNotIn('B',s.engine.active_seats)
        for seat in 'ACD':self.assertEqual(7,len(s.state.players[seat].zones['hand']))
        self.assertEqual('exile',s.state.cards[source.object_id].zone);self.private_replay(s,private)
