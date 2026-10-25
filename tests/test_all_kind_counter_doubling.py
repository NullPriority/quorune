from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from copy import deepcopy
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.counter_doubling import snapshot_all_counter_doubling,CounterDoublingError
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.all_kind_counter_shapes import ALL_KIND_COUNTER_CAPABILITY as CAP,validate_all_kind_counter_effect
from quorune.compiler.all_kind_counter_doubling import all_kind_counter_doubling_template
from scripts.build_test_database import build_fixture_database
from test_counter_doubling import permanent
import test_quantified_counter_targets as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Deepglow Skate','Vorel of the Hull Clade','Gilder Bairn','Zimone, Paradox Sculptor','Arcade Cabinet','The Thing')


class AllKindCounterTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.QuantifiedCounterTargetTests.session
    add=controls.QuantifiedCounterTargetTests.add
    prepare=controls.QuantifiedCounterTargetTests.prepare
    checkpoint=controls.QuantifiedCounterTargetTests.checkpoint
    resolve=controls.QuantifiedCounterTargetTests.resolve
    replay=controls.QuantifiedCounterTargetTests.replay
    binding=controls.QuantifiedCounterTargetTests.binding
    actions=controls.QuantifiedCounterTargetTests.actions
    action=controls.QuantifiedCounterTargetTests.action
    cast=controls.QuantifiedCounterTargetTests.cast
    reject=controls.QuantifiedCounterTargetTests.reject
    private_replay=controls.QuantifiedCounterTargetTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'all-kind.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/all-kind-counter-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('All kind counters',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_all_kind_snapshot_freezes_every_positive_kind_and_rejects_malformed_maps(self):
        rows=(permanent(1,{'+1/+1':2,'charge':7,'flying':1,'stun':0}),permanent(2,{'charge':3}),permanent(3,{}))
        requests=snapshot_all_counter_doubling(rows,actor='A',source_ref='spell')
        self.assertEqual({('object-1','+1/+1',2),('object-1','charge',7),('object-1','flying',1),('object-2','charge',3)},
            {(r.subject_id,r.counter_name,r.amount) for r in requests})
        self.assertTrue(all(r.placing_player=='A' and r.source_ref=='spell' and r.effect_generated for r in requests))
        self.assertEqual((),snapshot_all_counter_doubling((rows[2],),actor='A'))
        for bad in ({'charge':True},{'charge':-1},{'charge':'2'},{'Charge':2},{'':1}):
            with self.assertRaises(CounterDoublingError):snapshot_all_counter_doubling((replace(rows[0],counters=bad),),actor='A')
        for row in (replace(rows[0],zone='graveyard'),replace(rows[0],known_to_actor=False),replace(rows[0],logical_object_id='')):
            with self.assertRaises(CounterDoublingError):snapshot_all_counter_doubling((row,),actor='A')
        template=all_kind_counter_doubling_template('Double the number of each kind of counter on any number of target permanents.')
        self.assertIsNotNone(template)
        for update in ({'schema_version':True},{'amount':2},{'counter':'charge'},{'source':'$controller'},{'maximum_targets':0},{'announced_target_count':1}):
            with self.assertRaises(ValueError):validate_all_kind_counter_effect({**template[1][0],**update})

    def test_originals_have_complete_registration_and_independent_all_kind_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(796000)
        for name in NAMES:
            source=self.add(s.engine,name,zone='hand');bound=self.binding(s,source)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
            deps=tuple(d for d in program.capability_dependencies if d!=CAP)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,result['blockers'])

    def test_original_deepglow_selects_three_permanents_and_doubles_all_current_kinds(self):
        s=self.session(796010);source=self.add(s.engine,'Deepglow Skate',zone='hand')
        bodies=[self.add(s.engine,'Generic Bound Body',seat=seat,ref='deepglow-'+seat) for seat in 'ABD']
        maps=({'+1/+1':2,'charge':3,'flying':1},{'quest':4,'stun':2},{})
        for body,counters in zip(bodies,maps):body.counters.update(counters)
        other=self.add(s.engine,'Generic Bound Body',seat='C');other.counters['charge']=5
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'U':1,'C':4});self.checkpoint(s);self.cast(s,'A',source)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.target',pending.kind)
        self.reject(s,{'action_id':'choose','targets':[b.ref for b in bodies]},'pilot:B')
        s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','targets':[b.ref for b in bodies]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        for body,counters in zip(bodies,maps):self.assertEqual({k:2*v for k,v in counters.items()},s.state.cards[body.object_id].counters)
        self.assertEqual(5,s.state.cards[other.object_id].counters['charge']);self.private_replay(s,private)

    def test_fresh_source_and_all_kind_quantity_omissions_are_killed(self):
        method='test_originals_have_complete_registration_and_independent_all_kind_authority'
        with patch('quorune.compiler.all_kind_counter_doubling.all_kind_counter_doubling_template',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune import counter_doubling
        actual=counter_doubling.snapshot_all_counter_doubling
        method='test_original_deepglow_selects_three_permanents_and_doubles_all_current_kinds'
        with patch.object(counter_doubling,'snapshot_all_counter_doubling',side_effect=lambda *args,**kwargs:tuple(r for r in actual(*args,**kwargs) if r.counter_name=='+1/+1')):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_original_vorel_accepts_artifact_creature_or_land_and_rejects_enchantment(self):
        for index,name in enumerate(('Sol Ring','Generic Bound Body','Forest')):
            s=self.session(796020+index);source=self.add(s.engine,'Vorel of the Hull Clade');source.acquired_control_turn_count=-1
            target=self.add(s.engine,name,seat='B');target.counters.update({'charge':3,'quest':1})
            invalid=self.add(s.engine,'Glorious Anthem',seat='B')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'G':1,'U':1});self.checkpoint(s)
            action=next(a for a in self.actions(s) if a['id'].startswith('activate:'+source.ref+':'))
            self.reject(s,{'action_id':action['id'],'targets':[invalid.ref],'pay':'auto'})
            result=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.assertTrue(s.state.cards[source.object_id].tapped)
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual({'charge':6,'quest':2},s.state.cards[target.object_id].counters);self.private_replay(s,private)

    def test_original_gilder_pays_hybrid_mana_and_untaps_as_an_actual_cost(self):
        s=self.session(796030);source=self.add(s.engine,'Gilder Bairn');source.acquired_control_turn_count=-1;source.tapped=True
        target=self.add(s.engine,'Generic Bound Body',seat='B');target.counters.update({'+1/+1':2,'flying':1,'charge':3})
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'U':1,'C':2});self.checkpoint(s)
        action=next(a for a in self.actions(s) if a['id'].startswith('activate:'+source.ref+':'))
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertFalse(s.state.cards[source.object_id].tapped)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual({'+1/+1':4,'flying':2,'charge':6},s.state.cards[target.object_id].counters);self.private_replay(s,private)

    def test_original_zimone_zero_or_two_preserves_own_creature_artifact_domain(self):
        for count in (0,2):
            s=self.session(796040+count);source=self.add(s.engine,'Zimone, Paradox Sculptor');source.acquired_control_turn_count=-1
            targets=[self.add(s.engine,'Generic Bound Body'),self.add(s.engine,'Sol Ring',ref='zimone-own-ring')]
            for target in targets:target.counters.update({'charge':2,'quest':1})
            foreign=self.add(s.engine,'Sol Ring',seat='B',ref='zimone-foreign-ring');land=self.add(s.engine,'Forest')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'G':1,'U':1});self.checkpoint(s)
            action=next(a for a in self.actions(s) if a['id'].startswith('activate:'+source.ref+':'))
            for bad in (foreign,land):self.reject(s,{'action_id':action['id'],'targets':[bad.ref],'pay':'auto'})
            result=s.act('pilot:A',{'action_id':action['id'],'targets':[t.ref for t in targets[:count]],'pay':'auto'})
            self.assertTrue(result.ok,result.summary);self.assertTrue(s.state.cards[source.object_id].tapped)
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            for target in targets:self.assertEqual({'charge':4 if count else 2,'quest':2 if count else 1},s.state.cards[target.object_id].counters)
            self.private_replay(s,private)

    def test_original_thing_creates_targets_only_after_reflexive_payment(self):
        for pay in (False,True):
            s=self.session(796050+pay);source=self.add(s.engine,'The Thing');source.acquired_control_turn_count=-1
            source.counters['quest']=2
            target=self.add(s.engine,'Generic Bound Body',ref='thing-target');target.counters['charge']=3
            growth=self.add(s.engine,'Generic Bound Growth',zone='hand')
            lands=[self.add(s.engine,name,ref='thing-mana-'+str(i)) for i,name in enumerate(('Forest','Island','Mountain','Generic Bound Plains'))]
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='thing-private')
            self.prepare(s,mana={'G':1});self.checkpoint(s);self.cast(s,'A',growth,[target.ref]);self.assertIsNone(self.resolve(s))
            for _ in range(80):
                s.engine.pump();pending=s.state.pending_decision;self.assertIsNotNone(pending)
                if pending.kind=='combat.attackers':break
                self.assertEqual('priority',pending.kind)
                result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
            else:self.fail('Combat attackers did not arrive')
            self.assertEqual(4,s.state.cards[source.object_id].counters.get('+1/+1',0))
            result=s.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}});self.assertTrue(result.ok,result.summary)
            pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
            self.assertFalse(s.state.stack[-1].targets)
            self.reject(s,{'action_id':'choose','pay':pay},'pilot:D')
            s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','pay':pay});self.assertTrue(result.ok,result.summary)
            pending=self.resolve(s)
            if pay:
                self.assertIsNotNone(pending);self.assertEqual('semantic.target',pending.kind)
                result=s.act('pilot:A',{'action_id':'choose','targets':[source.ref,target.ref]});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s))
            else:self.assertIsNone(pending)
            self.assertEqual(8 if pay else 4,s.state.cards[source.object_id].counters.get('+1/+1',0))
            self.assertEqual(4 if pay else 2,s.state.cards[source.object_id].counters['quest'])
            self.assertEqual(6 if pay else 3,s.state.cards[target.object_id].counters['charge'])
            self.assertEqual(4 if pay else 0,sum(s.state.cards[l.object_id].tapped for l in lands));self.private_replay(s,private)

    def test_original_arcade_entry_and_token_sacrifice_use_both_complete_abilities(self):
        s=self.session(796060);source=self.add(s.engine,'Arcade Cabinet',zone='hand')
        alarm=self.add(s.engine,'Raise the Alarm',zone='hand')
        body=self.add(s.engine,'Generic Bound Body');body.counters['charge']=2
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':6,'W':1});self.checkpoint(s)
        action=self.action(s,source);result=s.act('pilot:A',{'action_id':action['id'],'pay':'manual','payment':{'C':3}})
        self.assertTrue(result.ok,result.summary);pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.target',pending.kind)
        result=s.act('pilot:A',{'action_id':'choose','targets':[body.ref]});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[body.object_id].counters['+1/+1'])
        self.cast(s,'A',alarm);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.is_token and c.controller=='A' and c.zone=='battlefield'];self.assertEqual(2,len(tokens))
        action=next(a for a in self.actions(s) if a['id'].startswith('activate:'+source.ref+':'))
        self.reject(s,{'action_id':action['id'],'targets':[body.ref],'cost_cards':[body.ref],'pay':'auto'})
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'cost_cards':[tokens[0].ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertTrue(s.state.cards[source.object_id].tapped)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,s.state.cards[body.object_id].counters['+1/+1']);self.assertEqual(4,s.state.cards[body.object_id].counters['charge'])
        self.assertEqual(1,sum(c.is_token and c.zone=='battlefield' and c.controller=='A' for c in s.state.cards.values()))
        self.private_replay(s,private)

    def test_all_kind_zero_and_nine_targets_resolve_without_a_fixed_ceiling(self):
        for count in (0,9):
            s=self.session(796070+count);source=self.add(s.engine,'Generic All Counter Burst',zone='hand')
            bodies=[self.add(s.engine,'Generic Bound Body',seat='B',ref='all-nine-'+str(i)) for i in range(9)]
            for index,body in enumerate(bodies):body.counters.update({'charge':index+1,'flying':1})
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'G':1});self.checkpoint(s);self.assertTrue(self.binding(s,source)['strict_capability_ready'])
            self.cast(s,'A',source,[b.ref for b in bodies[:count]]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            for index,body in enumerate(bodies):self.assertEqual({'charge':(index+1)*(2 if index<count else 1),'flying':2 if index<count else 1},s.state.cards[body.object_id].counters)
            self.private_replay(s,private)

    def test_all_kind_replacement_order_is_atomic_private_and_reads_each_kind_once(self):
        s=self.session(796090);source=self.add(s.engine,'Generic All Counter Burst',zone='hand')
        self.add(s.engine,'Doubling Season');self.add(s.engine,'Hardened Scales')
        body=self.add(s.engine,'Generic Bound Body');body.counters.update({'+1/+1':2,'charge':3,'flying':1})
        other=self.add(s.engine,'Generic Bound Body',seat='B',ref='all-replacement-foreign');other.counters['charge']=1
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'G':1});self.checkpoint(s);self.cast(s,'A',source,[body.ref,other.ref])
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('replacement.order',pending.kind)
        self.assertEqual({'+1/+1':2,'charge':3,'flying':1},s.state.cards[body.object_id].counters)
        for seat in 'BCD':self.assertNotIn('replacement.order',json.dumps(s.packet('pilot:'+seat,full=True)))
        s=self.replay(s,load=True)
        for _ in range(10):
            pending=s.state.pending_decision
            if pending is None or pending.kind!='replacement.order':break
            options=s.packet('pilot:A',full=True)['decision']['ctx']['options'];self.assertTrue(options)
            command={'action_id':'choose','replacement':options[0]['id']};self.reject(s,command,'pilot:D')
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.resolve(s)
        else:self.fail('All-kind replacements did not terminate')
        current=s.state.cards[body.object_id].counters
        self.assertIn(current['+1/+1'],(7,8));self.assertEqual(9,current['charge']);self.assertEqual(3,current['flying'])
        self.assertEqual(2,s.state.cards[other.object_id].counters['charge']);self.private_replay(s,private)

    def test_pending_all_kind_choice_rejects_an_added_counter_kind(self):
        s=self.session(796091);source=self.add(s.engine,'Generic All Counter Burst',zone='hand')
        self.add(s.engine,'Doubling Season');self.add(s.engine,'Hardened Scales')
        body=self.add(s.engine,'Generic Bound Body');body.counters.update({'+1/+1':2,'charge':3})
        self.prepare(s,mana={'G':1});self.checkpoint(s);self.cast(s,'A',source,[body.ref])
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('replacement.order',pending.kind)
        option=s.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
        from quorune.counter_state import CounterChange,plan_counter_changes,commit_counter_changes
        commit_counter_changes(s.engine,plan_counter_changes(s.engine,(CounterChange('permanent',body.object_id,'quest',1),)))
        self.reject(s,{'action_id':'choose','replacement':option})
        self.assertEqual(2,s.state.cards[body.object_id].counters['+1/+1']);self.assertEqual(3,s.state.cards[body.object_id].counters['charge'])

    def test_all_kind_copy_uses_current_maps_without_a_second_payment(self):
        s=self.session(796100);source=self.add(s.engine,'Generic All Counter Burst',zone='hand');copy=self.add(s.engine,'Reverberate',seat='B',zone='hand')
        body=self.add(s.engine,'Generic Bound Body');body.counters.update({'charge':2,'quest':3})
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['R']=2;self.prepare(s,mana={'G':1});self.checkpoint(s)
        self.cast(s,'A',source,[body.ref]);self.cast(s,'B',copy,[s.state.stack[-1].ref]);pending=self.resolve(s)
        self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
        s=self.replay(s,load=True);result=s.act('pilot:B',{'action_id':'choose','targets':[body.ref]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual({'charge':8,'quest':12},s.state.cards[body.object_id].counters)
        for seat in 'AB':self.assertEqual(0,sum(s.state.players[seat].mana_pool.values()))
        self.private_replay(s,private)

    def test_partial_and_all_illegal_targets_keep_cost_and_original_incarnations(self):
        for all_illegal in (False,True):
            s=self.session(796110+all_illegal);source=self.add(s.engine,'Generic All Counter Burst',zone='hand')
            bodies=[self.add(s.engine,'Generic Bound Body',seat='B',ref='all-loss-'+str(i)) for i in range(2)]
            for body in bodies:body.counters.update({'charge':2,'flying':1})
            removals=[self.add(s.engine,'Murder',seat='C',zone='hand',ref='all-loss-spell-'+str(i)) for i in range(2 if all_illegal else 1)]
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            s.state.players['C'].mana_pool.update({'B':2*len(removals),'C':len(removals)})
            self.prepare(s,mana={'G':1});self.checkpoint(s);self.cast(s,'A',source,[b.ref for b in bodies])
            for removal,body in zip(removals,bodies):self.cast(s,'C',removal,[body.ref])
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            self.assertEqual('graveyard',s.state.cards[bodies[0].object_id].zone)
            last=s.state.cards[bodies[1].object_id];self.assertEqual('graveyard' if all_illegal else 'battlefield',last.zone)
            if not all_illegal:self.assertEqual({'charge':4,'flying':2},last.counters)
            self.private_replay(s,private)

    def test_unowned_player_affected_set_and_companion_results_remain_residual(self):
        s=self.session(796120)
        for name in ('Aetheric Amplifier','Ferrafor, Young Yew','Arna Kennerüd, Skycaptain','The First Tyrannic War','Miles Morales // Ultimate Spider-Man'):
            source=self.add(s.engine,name,zone='hand');self.assertFalse(self.binding(s,source)['strict_capability_ready'],name)
        for text in ('Double the number of each kind of counter on target player.',
                'Triple the number of each kind of counter on target permanent.',
                'Double the number of each kind of counter on each creature you control.',
                'Double the number of each kind of counter on target permanent. Draw a card.'):
            self.assertIsNone(all_kind_counter_doubling_template(text),text)

    def test_verified_all_kind_withdrawal_rejects_complete_sources_without_unverified_evidence(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn all-kind producer'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in ('Deepglow Skate','The Thing'):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
