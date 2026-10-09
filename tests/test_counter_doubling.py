from __future__ import annotations

"""CR701.10e: place existing counts at this instruction, before replacements."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.compiler.counter_doubling_templates import named_counter_doubling_effect_template
from quorune.counter_doubling import CounterDoublingError, snapshot_named_counter_doubling
from quorune.counter_names import EXISTING_COUNTER_AMOUNT
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as bound_witnesses
from test_qualified_zone_event_queries import query_record


def permanent(index, counters):
    return ObjectQueryResult(object_id=f'object-{index}',logical_object_id=f'incarnation-{index}',
        ref=f'permanent-{index}',printed_name='Counter witness',owner='B',controller='C',zone='battlefield',counters=counters)


class CounterDoublingCompilerTests(unittest.TestCase):
    def test_named_counter_request_snapshots_add_original_counts_and_skip_zero(self):
        rows=(permanent(1,{'+1/+1':2,'charge':7}),permanent(2,{'+1/+1':5}),permanent(3,{}))
        requests=snapshot_named_counter_doubling(rows,actor='A',counter_name='+1/+1',source_ref='spell')
        self.assertEqual((2,5),tuple(r.amount for r in requests))
        self.assertEqual(('A','A'),tuple(r.placing_player for r in requests))
        self.assertTrue(all(r.effect_generated for r in requests))
        self.assertEqual(2,rows[0].counters['+1/+1'])
        self.assertEqual((),snapshot_named_counter_doubling((rows[-1],),actor='A',counter_name='+1/+1'))

    def test_named_counter_requests_reject_unknown_invalid_or_unpinned_subjects(self):
        row=permanent(1,{'+1/+1':2})
        for rows in ((row,row),(replace(row,logical_object_id=''),),(replace(row,phased_out=True),),
                     (replace(row,known_to_actor=False),),(replace(row,zone='graveyard'),)):
            with self.subTest(rows=rows),self.assertRaises(CounterDoublingError):
                snapshot_named_counter_doubling(rows,actor='A',counter_name='+1/+1')
        for amount in (None,True,-1,'2'):
            with self.subTest(amount=amount),self.assertRaises(CounterDoublingError):
                snapshot_named_counter_doubling((permanent(1,{'+1/+1':amount}),),actor='A',counter_name='+1/+1')

    def test_named_doubling_grammar_and_amount_shape_stay_closed(self):
        registry=load_default_capability_registry()
        for text in ('Double the number of +1/+1 counters on target creature.',
                     '{1}: Double the number of charge counters on this artifact.',
                     'Double the number of +1/+1 counters on each creature you control.'):
            with self.subTest(text=text):
                row=query_record(text)
                row=replace(row,type_line='Sorcery' if 'target' in text or 'each' in text else 'Artifact')
                ir=compile_oracle_card(row,capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status)
                node=ir.faces[0].nodes[0]
                self.assertIn('counter.producer.named_doubling',node.capability_dependencies)
                self.assertEqual({'kind':EXISTING_COUNTER_AMOUNT,'schema_version':1},node.effects[0]['amount'])
        for text in ('Double the number of each kind of counters on target creature.',
                     'Double the number of each kind of counter on target creature.',
                     'Double the number of chosen counters on target creature.',
                     'Double the number of poison counters on target player.'):
            with self.subTest(text=text):
                self.assertIsNone(named_counter_doubling_effect_template(text,card_name='Generic'))
        from quorune.rules.counter_placement_capability_shapes import fixed_counter_placement_node_capabilities
        template=named_counter_doubling_effect_template('Double the number of +1/+1 counters on target creature.',card_name='Generic')
        _,effects,schema,mechanics=template
        for changed in (1,{'kind':EXISTING_COUNTER_AMOUNT,'schema_version':True},
                        {'kind':EXISTING_COUNTER_AMOUNT,'schema_version':1,'extra':True}):
            self.assertEqual((),fixed_counter_placement_node_capabilities(effects=({**effects[0],'amount':changed},),
                target_schema=schema,mechanic_ids=mechanics))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        blocked=next(r for r in raw['capabilities'] if r['id']=='counter.producer.named_doubling')
        blocked.update(status='blocked',blockers=['Constructed unavailable named-count owner'])
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record('Double the number of +1/+1 counters on target creature.'),type_line='Instant'),
            capability_registry=CapabilityRegistry(raw),capability_profile='commander_review').status)


class CounterDoublingActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'doubling.sqlite3'
        build_fixture_database([bound_witnesses.FIXTURE,ROOT/'tests/fixtures/counter-doubling-cards.json',ROOT/'tests/fixtures/counter-replacement-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Counter doubling witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=bound_witnesses.BoundEffectProgramRuntimeTests.session
    add=bound_witnesses.BoundEffectProgramRuntimeTests.add
    ready=bound_witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=bound_witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=bound_witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=bound_witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_surge_adds_then_doubles_current_target_count_with_replay(self):
        session=self.session(282001);engine=session.engine
        body=self.add(engine,'Generic Bound Body',seat='B');body.counters['+1/+1']=2
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-c-growth')
        engine.change_control(body.object_id,'A',reason='Counter owner-controller distinction')
        spell=self.add(engine,'Invigorating Surge',zone='hand')
        action=self.ready(session,spell,{'G':1,'C':2})
        self.assertIn(body.ref,action['target_schema']['legal_refs'])
        self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:C',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        body=engine.state.cards[body.object_id]
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(6,body.counters['+1/+1'])
        self.assertEqual('B',body.owner);self.assertEqual('A',body.controller)
        for seat in 'ABD':
            self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_quantity_replacement_modifies_additional_count_once(self):
        session=self.session(282002);engine=session.engine
        replacement=self.add(engine,'Doubling Season')
        self.assertTrue(engine.semantics.programs_for_oracle(replacement.oracle_id))
        body=self.add(engine,'Generic Bound Body');body.counters['+1/+1']=2
        spell=self.add(engine,'Invigorating Surge',zone='hand')
        action=self.ready(session,spell,{'G':1,'C':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(12,body.counters['+1/+1'])  # Add2 ->4; doubling places4, replaced by8.
        self.replay(session,load=True)

    def test_fixed_entry_dependency_closes_original_hydras_and_landfall_replays(self):
        session=self.session(282003);engine=session.engine
        for name in ('Mossborn Hydra','Kalonian Hydra'):
            record=self.db.lookup(name)
            program=compile_best_available_card_program(self.db,record,semantic_registry=SemanticRegistry(),
                capability_registry=self.registry,capability_profile='commander_review')
            binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
            self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        self.add(engine,'Doubling Season')
        hydra=self.add(engine,'Mossborn Hydra',zone='hand')
        land=self.add(engine,'Generic Bound Plains',zone='hand',ref='landfall-land')
        action=self.ready(session,hydra,{'G':1,'C':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(2,hydra.counters.get('+1/+1'))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        play=next(a for a in actions if a.get('card')==land.ref)
        accepted=session.act('pilot:A',{'action_id':play['id']})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(6,hydra.counters.get('+1/+1'))  # Two old; place two, doubled to four.
        self.replay(session,load=True)

    def test_hydra_growth_reads_current_attachment_then_replays(self):
        session=self.session(282004);engine=session.engine
        for seat in 'ABCD':
            for ident in tuple(engine.state.players[seat].zones['hand'])[1:]:
                engine.move_card(ident,'library',semantic_events=False,log=False)
        body=self.add(engine,'Generic Bound Body',seat='B')
        aura=self.add(engine,"Hydra's Growth",zone='hand')
        action=self.ready(session,aura,{'G':1,'C':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(1,body.counters.get('+1/+1'))
        for _ in range(160):
            if engine.state.active_player=='A' and engine.state.phase=='beginning' and engine.state.step=='upkeep':
                break
            decision=engine.state.pending_decision
            kind=decision.kind
            response=({'action_id':'pass'} if kind=='priority' else {'action_id':'attack','attackers':[]}
                if kind=='combat.attackers' else {'action_id':'block','blocks':[]})
            self.assertIn(kind,('priority','combat.attackers','combat.blockers'))
            self.assertTrue(session.act(session.pending_principals()[0],response).ok)
        else:self.fail('Did not reach the next A upkeep')
        self.resolve(session)
        self.assertEqual(2,body.counters.get('+1/+1'))
        self.replay(session,load=True)

    def test_named_set_snapshots_heterogeneous_counts_and_zero_uses_no_event(self):
        session=self.session(282005);engine=session.engine
        a=self.add(engine,'Generic Bound Body',ref='two-counter');a.counters.update({'+1/+1':2,'charge':7})
        b=self.add(engine,'Generic Bound Body',ref='five-counter');b.counters['+1/+1']=5
        zero=self.add(engine,'Generic Bound Body',ref='zero-counter');zero.counters['charge']=3
        enemy=self.add(engine,'Generic Bound Body',seat='C',ref='enemy-counter');enemy.counters['+1/+1']=4
        spell=self.add(engine,'Generic Named Counter Set Doubler',zone='hand')
        action=self.ready(session,spell,{'G':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual((4,10,0,4),tuple(card.counters.get('+1/+1',0) for card in (a,b,zero,enemy)))
        self.assertEqual(7,a.counters['charge']);self.assertEqual(3,zero.counters['charge'])
        self.replay(session,load=True)

    def test_zero_named_counter_activation_pays_tap_without_placement_event(self):
        session=self.session(282006);engine=session.engine
        source=self.add(engine,'Generic Named Counter Artifact')
        source.counters['+1/+1']=2
        action=self.ready(session,source,{});self.checkpoint(session)
        from quorune.counter_placement import place_counters
        with patch('quorune.counter_doubling.place_counters',wraps=place_counters) as placements:
            accepted=session.act('pilot:A',{'action_id':action['id']})
            self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
            self.assertTrue(placements.called)
            self.assertEqual((),placements.call_args.args[1])
        self.assertTrue(source.tapped);self.assertEqual({'+1/+1':2},source.counters)
        self.replay(session,load=True)

    def test_named_count_replacement_choice_is_private_persistent_and_not_recomputed(self):
        session=self.session(282007);engine=session.engine
        self.add(engine,'Doubling Season');self.add(engine,'Hardened Scales')
        body=self.add(engine,'Generic Bound Body');body.counters['+1/+1']=2
        spell=self.add(engine,'Generic Named Counter Set Doubler',zone='hand')
        action=self.ready(session,spell,{'G':1});self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        pending=self.resolve(session)
        self.assertEqual('replacement.order',pending.kind)
        before=authoritative_state_hash(session.state)
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        rejected=session.act('pilot:B',{'action_id':'choose','replacement':options[0]['id']})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        for seat in 'BCD':
            self.assertNotIn('replacement.order',json.dumps(session.packet('pilot:'+seat,full=True)))
        session=self.replay(session,load=True);engine=session.engine
        for _ in range(6):
            if engine.state.pending_decision.kind!='replacement.order':break
            options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
            choice=options[0]['id']
            self.assertTrue(session.act('pilot:A',{'action_id':'choose','replacement':choice}).ok)
            self.resolve(session)
        else:self.fail('Counter replacement did not terminate')
        self.assertIn(engine.state.cards[body.object_id].counters['+1/+1'],(7,8))
        self.replay(session,load=True)

    def test_stale_named_counter_choice_rolls_back_after_quantity_changes(self):
        session=self.session(282008);engine=session.engine
        self.add(engine,'Doubling Season');self.add(engine,'Hardened Scales')
        body=self.add(engine,'Generic Bound Body');body.counters['+1/+1']=2
        spell=self.add(engine,'Generic Named Counter Set Doubler',zone='hand')
        action=self.ready(session,spell,{'G':1})
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        pending=self.resolve(session);self.assertEqual('replacement.order',pending.kind)
        option=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
        # Owner diagnostic: external state change behind an already sealed choice.
        from quorune.counter_state import CounterChange, plan_counter_changes, commit_counter_changes
        commit_counter_changes(engine,plan_counter_changes(engine,(CounterChange('permanent',body.object_id,'+1/+1',1),)))
        before=authoritative_state_hash(session.state)
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':option})
        self.assertFalse(accepted.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))

    def test_current_count_omission_mutant_is_killed_by_actual_action(self):
        original=snapshot_named_counter_doubling
        def premature(rows,**kwargs):
            return tuple(replace(request,amount=request.amount*2) for request in original(rows,**kwargs))
        with patch('quorune.counter_doubling.snapshot_named_counter_doubling',side_effect=premature):
            with self.assertRaises(AssertionError):
                self.test_actual_surge_adds_then_doubles_current_target_count_with_replay()
