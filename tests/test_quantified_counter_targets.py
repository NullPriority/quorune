from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.counter_target_set_shapes import QUANTIFIED_COUNTER_CAPABILITY as CAP,validate_quantified_counter_target_effect
from quorune.compiler.quantified_counter_targets import quantified_counter_target_template
from scripts.build_test_database import build_fixture_database
import test_any_number_targets as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=("Nature's Panoply",'Solidarity of Heroes','Vineshaper Mystic','Sweet-Gum Recluse')


class QuantifiedCounterTargetTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AnyNumberTargetTests.session
    add=controls.AnyNumberTargetTests.add
    prepare=controls.AnyNumberTargetTests.prepare
    checkpoint=controls.AnyNumberTargetTests.checkpoint
    resolve=controls.AnyNumberTargetTests.resolve
    replay=controls.AnyNumberTargetTests.replay
    binding=controls.AnyNumberTargetTests.binding
    actions=controls.AnyNumberTargetTests.actions
    action=controls.AnyNumberTargetTests.action
    cast=controls.AnyNumberTargetTests.cast
    reject=controls.AnyNumberTargetTests.reject
    private_replay=controls.AnyNumberTargetTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'counter-targets.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/quantified-counter-target-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Counter sets',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_complete_counter_templates_keep_predicates_quantities_and_fail_closed_boundaries(self):
        text='Choose any number of target creatures. Double the number of +1/+1 counters on each of them.'
        template=quantified_counter_target_template(text);self.assertIsNotNone(template)
        effect=template[1][0];self.assertEqual((True,True),validate_quantified_counter_target_effect(effect))
        for change in ({'schema_version':True},{'maximum_targets':True},{'amount':True},{'amount':0},{'amount':{'kind':'mystery','schema_version':1}},{'announced_target_count':1},{'unknown':1}):
            with self.assertRaises(ValueError):validate_quantified_counter_target_effect({**effect,**change})
        for text in ('Distribute three +1/+1 counters among any number of target creatures.',
                'Choose any number of target creatures. Put X +1/+1 counters on each of them.',
                'Double the number of each kind of counter on any number of target permanents.',
                'Put a +1/+1 counter on each of any number of target creatures and a charge counter on each of any number of target artifacts.'):
            self.assertIsNone(quantified_counter_target_template(text),text)
        fixed=quantified_counter_target_template('Put a +1/+1 counter on each of up to two target Merfolk you control.')
        self.assertIsNotNone(fixed);self.assertEqual(['merfolk'],fixed[2]['subtypes_any']);self.assertEqual('you',fixed[2]['controller_relation'])
        from types import SimpleNamespace
        from quorune.semantic_runtime.counter_placement_handlers import FixedCounterPlacementTargetSetHandler
        legacy={'op':'place_counters_on_targets','cards':['body'],'maximum_targets':1,'counter':'+1/+1',
            'amount':'existing_named_counter_count','source':'source'}
        with self.assertRaises(ValueError):FixedCounterPlacementTargetSetHandler().lower(legacy,SimpleNamespace(actor='A',default_reason='Counter check'))

    def test_four_originals_have_complete_normal_admission_and_payload_owned_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(795000)
        for name in NAMES:
            card=self.add(s.engine,name,zone='hand');binding=self.binding(s,card)
            self.assertTrue(binding['strict_capability_ready'],(name,binding['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            program=next(p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if CAP in p.capability_dependencies)
            for cap in (CAP,*(['counter.producer.named_doubling'] if name=='Solidarity of Heroes' else [])):
                deps=tuple(d for d in program.capability_dependencies if d!=cap)
                changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_original_panoply_zero_one_and_nine_targets_pay_strive_and_place_each_counter(self):
        for count in (0,1,9):
            s=self.session(795010+count);source=self.add(s.engine,"Nature's Panoply",zone='hand')
            bodies=[self.add(s.engine,'Generic Bound Body',seat='B',ref='panoply-'+str(i)) for i in range(9)]
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'G':max(1,count),'C':2*max(0,count-1)});self.checkpoint(s)
            action=self.action(s,source);command={'action_id':action['id'],'targets':[c.ref for c in bodies[:count]],'pay':'auto'}
            self.reject(s,command,'pilot:D')
            if count:self.reject(s,{**command,'targets':[bodies[0].ref]*2})
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            for index,body in enumerate(bodies):self.assertEqual(1 if index<count else 0,s.state.cards[body.object_id].counters.get('+1/+1',0))
            self.private_replay(s,private)

    def test_original_solidarity_snapshots_each_current_named_count_and_skips_zero(self):
        s=self.session(795030);source=self.add(s.engine,'Solidarity of Heroes',zone='hand')
        bodies=[self.add(s.engine,'Generic Bound Body',seat=seat,ref='solidarity-'+seat) for seat in 'ABD']
        for body,count in zip(bodies,(2,3,0)):body.counters.update({'+1/+1':count,'charge':4})
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'G':3,'C':3});self.checkpoint(s)
        self.cast(s,'A',source,[b.ref for b in bodies]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        for body,count in zip(bodies,(4,6,0)):
            current=s.state.cards[body.object_id];self.assertEqual(count,current.counters.get('+1/+1',0));self.assertEqual(4,current.counters['charge'])
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.private_replay(s,private)

    def test_original_solidarity_zero_targets_resolves_without_placing_or_refunding(self):
        s=self.session(795031);source=self.add(s.engine,'Solidarity of Heroes',zone='hand')
        self.add(s.engine,'Doubling Season');self.add(s.engine,'Hardened Scales')
        body=self.add(s.engine,'Generic Bound Body');body.counters['+1/+1']=2
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'G':1,'C':1});self.checkpoint(s);self.cast(s,'A',source)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,s.state.cards[body.object_id].counters['+1/+1'])
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.private_replay(s,private)

    def test_original_vineshaper_preserves_own_merfolk_target_domain(self):
        s=self.session(795040);source=self.add(s.engine,'Vineshaper Mystic',zone='hand')
        own=[self.add(s.engine,'Generic Counter Merfolk',ref='vineshaper-own-'+str(i)) for i in range(2)]
        foreign=self.add(s.engine,'Generic Counter Merfolk',seat='B');human=self.add(s.engine,'Generic Bound Body')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'G':1,'C':2});self.checkpoint(s);self.cast(s,'A',source)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.target',pending.kind)
        for target in (foreign,human):self.reject(s,{'action_id':'choose','targets':[target.ref]})
        s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','targets':[c.ref for c in own]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        for card in own:self.assertEqual(1,s.state.cards[card.object_id].counters.get('+1/+1',0))
        for card in (foreign,human):self.assertEqual(0,s.state.cards[card.object_id].counters.get('+1/+1',0))
        self.private_replay(s,private)

    def test_original_recluse_chooses_only_actual_current_turn_entries_after_cascade(self):
        s=self.session(795050);source=self.add(s.engine,'Sweet-Gum Recluse',zone='hand')
        old=self.add(s.engine,'Generic Bound Body',ref='recluse-old');old.entered_battlefield_turn_sequence=0
        entered=self.add(s.engine,'Generic Bound Body',zone='hand',ref='recluse-entered')
        revealed=self.add(s.engine,'Generic Bound Growth',zone='library',ref='recluse-cascade')
        library=s.state.players['A'].zones['library'];library.remove(revealed.object_id);library.insert(0,revealed.object_id)
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand',ref='recluse-private')
        self.prepare(s,mana={'W':1,'G':2,'C':4});self.checkpoint(s)
        self.cast(s,'A',entered);self.assertIsNone(self.resolve(s));self.cast(s,'A',source)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('selection.exile_cast',pending.kind)
        result=s.act('pilot:A',{'action_id':'decline'});self.assertTrue(result.ok,result.summary)
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.target',pending.kind)
        self.reject(s,{'action_id':'choose','targets':[old.ref]})
        s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','targets':[entered.ref,source.ref]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        for card in (entered,source):self.assertEqual(3,s.state.cards[card.object_id].counters.get('+1/+1',0))
        self.assertEqual(0,s.state.cards[old.object_id].counters.get('+1/+1',0));self.private_replay(s,private)

    def test_named_replacement_choices_are_per_recipient_private_and_persistent(self):
        s=self.session(795060);source=self.add(s.engine,'Solidarity of Heroes',zone='hand')
        self.add(s.engine,'Doubling Season');self.add(s.engine,'Hardened Scales')
        own=self.add(s.engine,'Generic Bound Body',ref='counter-replace-own');own.counters['+1/+1']=2
        empty=self.add(s.engine,'Generic Bound Body',ref='counter-replace-empty')
        foreign=self.add(s.engine,'Generic Bound Body',seat='B',ref='counter-replace-foreign');foreign.counters['+1/+1']=3
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'G':3,'C':3});self.checkpoint(s);self.cast(s,'A',source,[own.ref,empty.ref,foreign.ref])
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('replacement.order',pending.kind)
        self.assertEqual(2,s.state.cards[own.object_id].counters['+1/+1'])
        for seat in 'BCD':self.assertNotIn('replacement.order',json.dumps(s.packet('pilot:'+seat,full=True)))
        s=self.replay(s,load=True)
        for _ in range(8):
            pending=s.state.pending_decision
            if pending is None or pending.kind!='replacement.order':break
            options=s.packet('pilot:A',full=True)['decision']['ctx']['options'];self.assertTrue(options)
            command={'action_id':'choose','replacement':options[0]['id']};self.reject(s,command,'pilot:B')
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.resolve(s)
        else:self.fail('Named target replacement choices did not terminate')
        self.assertIn(s.state.cards[own.object_id].counters['+1/+1'],(7,8))
        self.assertEqual(0,s.state.cards[empty.object_id].counters.get('+1/+1',0))
        self.assertEqual(6,s.state.cards[foreign.object_id].counters['+1/+1']);self.private_replay(s,private)

    def test_copy_doubles_current_counts_again_without_repaying_strive(self):
        s=self.session(795070);source=self.add(s.engine,'Solidarity of Heroes',zone='hand');copy=self.add(s.engine,'Reverberate',seat='B',zone='hand')
        bodies=[self.add(s.engine,'Generic Bound Body',ref='counter-copy-'+str(i)) for i in range(2)]
        for body,count in zip(bodies,(2,3)):body.counters['+1/+1']=count
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['R']=2;self.prepare(s,mana={'G':2,'C':2});self.checkpoint(s)
        self.cast(s,'A',source,[b.ref for b in bodies]);self.cast(s,'B',copy,[s.state.stack[-1].ref])
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
        s=self.replay(s,load=True);result=s.act('pilot:B',{'action_id':'choose','targets':[b.ref for b in bodies]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        for body,count in zip(bodies,(8,12)):self.assertEqual(count,s.state.cards[body.object_id].counters['+1/+1'])
        for seat in 'AB':self.assertEqual(0,sum(s.state.players[seat].mana_pool.values()))
        self.private_replay(s,private)

    def test_pending_named_target_batch_rejects_a_changed_quantity_before_any_placement(self):
        s=self.session(795075);source=self.add(s.engine,'Solidarity of Heroes',zone='hand')
        self.add(s.engine,'Doubling Season');self.add(s.engine,'Hardened Scales')
        body=self.add(s.engine,'Generic Bound Body');body.counters['+1/+1']=2
        other=self.add(s.engine,'Generic Bound Body',seat='B',ref='counter-seal-other');other.counters['+1/+1']=3
        self.prepare(s,mana={'G':2,'C':2});self.checkpoint(s);self.cast(s,'A',source,[body.ref,other.ref])
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('replacement.order',pending.kind)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options'];self.assertTrue(options)
        from quorune.counter_state import CounterChange,plan_counter_changes,commit_counter_changes
        commit_counter_changes(s.engine,plan_counter_changes(s.engine,(CounterChange('permanent',other.object_id,'+1/+1',1),)))
        self.reject(s,{'action_id':'choose','replacement':options[0]['id']})
        self.assertEqual(2,s.state.cards[body.object_id].counters['+1/+1'])

    def test_partial_and_all_target_departures_keep_the_paid_price_and_original_incarnations(self):
        for all_illegal in (False,True):
            s=self.session(795080+all_illegal);source=self.add(s.engine,'Solidarity of Heroes',zone='hand')
            bodies=[self.add(s.engine,'Generic Bound Body',seat='B',ref='counter-loss-'+str(i)) for i in range(2)]
            for body in bodies:body.counters['+1/+1']=2
            removals=[self.add(s.engine,'Murder',seat='C',zone='hand',ref='counter-loss-spell-'+str(i)) for i in range(2 if all_illegal else 1)]
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            s.state.players['C'].mana_pool.update({'B':2*len(removals),'C':len(removals)})
            self.prepare(s,mana={'G':2,'C':2});self.checkpoint(s);self.cast(s,'A',source,[b.ref for b in bodies])
            for removal,body in zip(removals,bodies):self.cast(s,'C',removal,[body.ref])
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            self.assertEqual('graveyard',s.state.cards[bodies[0].object_id].zone)
            last=s.state.cards[bodies[1].object_id];self.assertEqual('graveyard' if all_illegal else 'battlefield',last.zone)
            if not all_illegal:self.assertEqual(4,last.counters['+1/+1'])
            self.private_replay(s,private)

    def test_verified_dependency_withdrawal_blocks_complete_source_without_unverified_evidence(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn quantified counters'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup('Solidarity of Heroes'),capability_registry=registry,capability_profile='commander_review')
        blockers=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers)
        self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_fresh_compiler_and_relative_amount_omissions_are_killed(self):
        method='test_four_originals_have_complete_normal_admission_and_payload_owned_minima'
        with patch('quorune.compiler.quantified_counter_targets.quantified_counter_target_template',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune import counter_placement_targets
        actual=counter_placement_targets.resolve_counter_placement_targets
        method='test_original_solidarity_snapshots_each_current_named_count_and_skips_zero'
        with patch('quorune.semantic_choices.intent_host.resolve_counter_placement_targets',side_effect=lambda *args,**kwargs:actual(*args,**{**kwargs,'amount':1})):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
