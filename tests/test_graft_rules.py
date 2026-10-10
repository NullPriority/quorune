from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.graft_capability_shapes import graft_node_capabilities
from quorune.semantics import SemanticRegistry
from quorune.rules.counter_movement import execute_counter_movement
from quorune.semantic_runtime.counter_movement_intent import MoveCountersIntent
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class GraftCompilerTests(unittest.TestCase):
    def test_fixed_graft_has_independent_entry_and_nontargeted_movement(self):
        registry = load_default_capability_registry()
        ir = compile_oracle_card(replace(query_record('Graft 2'), keywords=('Graft',)), capability_registry=registry)
        self.assertEqual('exact', ir.status)
        entry, trigger = ir.faces[0].nodes
        self.assertEqual('graft-fixed-entry-counter-v1', entry.template_id)
        self.assertEqual('graft-fixed-counter-movement-v1', trigger.template_id)
        self.assertIsNone(trigger.target_schema)
        self.assertEqual(('counter.lifecycle.graft',), graft_node_capabilities(
            effects=trigger.effects, target_schema=None, mechanic_ids=('graft',)))
        self.assertIn('intervening_condition', trigger.runtime_coverage)
        repeated = compile_oracle_card(replace(query_record('Graft 2, Graft 3'), keywords=('Graft',)), capability_registry=registry)
        self.assertEqual(2, sum(n.template_id=='graft-fixed-counter-movement-v1' for n in repeated.faces[0].nodes))

    def test_graft_open_values_shapes_and_dependency_omission_fail_closed(self):
        registry = load_default_capability_registry()
        for text in ('Graft X', 'Graft 0', 'Graft 2 with haste'):
            self.assertNotEqual('exact', compile_oracle_card(replace(query_record(text), keywords=('Graft',)), capability_registry=registry).status)
        record = replace(query_record('Graft 2'), keywords=('Graft',))
        trigger = compile_oracle_card(record, capability_registry=registry).faces[0].nodes[1]
        for change in ({'to_identity': True}, {'from_ref': '$target.0'}, {'extra': 1}):
            self.assertFalse(graft_node_capabilities(effects=({**trigger.effects[0], **change},), target_schema=None, mechanic_ids=('graft',)))
        value = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in value['capabilities'] if r['id']=='counter.lifecycle.graft').update(status='blocked', blockers=['omitted movement owner'])
        blocked = CapabilityRegistry(value)
        blocked.mark_evidence_verified(registry.evidence_fingerprint)
        self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=blocked).status)


class GraftRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'graft.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json', ROOT/'tests/fixtures/graft-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Graft', [DeckEntry('Generic Bound Commander',1,'commander'), DeckEntry('Generic Bound Plains',30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    session = witnesses.BoundEffectProgramRuntimeTests.session
    add = witnesses.BoundEffectProgramRuntimeTests.add
    ready = witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint = witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay = witnesses.BoundEffectProgramRuntimeTests.replay
    resolve = counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def choose(self, session, choice):
        decision = session.state.pending_decision
        self.assertEqual('semantic.choice', decision.kind)
        before = authoritative_state_hash(session.state)
        wrong = session.act('pilot:B', {'action_id':'choose', 'choice':choice})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id':'choose', 'choice':choice})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)

    def cast(self, session, card, targets=()):
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        matches = [a for a in actions if a.get('card') == card.ref]
        self.assertTrue(matches, (card.ref, session.state.players['A'].mana_pool, actions))
        action = matches[0]
        accepted = session.act('pilot:A', {'action_id':action['id'], 'targets':list(targets), 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        return self.resolve(session)

    def test_actual_graft_cast_and_other_entry_transfer_decline_and_replay(self):
        s = self.session(322001)
        e = s.engine
        source = self.add(e, 'Aquastrand Spider', zone='hand')
        first = self.add(e, 'Generic Bound Body', zone='hand', ref='first-recipient')
        second = self.add(e, 'Generic Bound Body', zone='hand', ref='second-recipient')
        self.ready(s, source, {'G':3, 'W':4, 'C':8})
        self.checkpoint(s)
        self.cast(s, source)
        self.assertEqual({'+1/+1':2}, e.state.cards[source.object_id].counters)
        self.assertFalse(e.state.stack)
        self.cast(s, first)
        self.assertEqual(1, len(e.state.stack))
        self.choose(s, 'move')
        self.assertEqual(1, e.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual(1, e.state.cards[first.object_id].counters['+1/+1'])
        self.cast(s, second)
        self.choose(s, 'decline')
        self.assertEqual({}, e.state.cards[second.object_id].counters)
        self.assertEqual(1, e.state.cards[source.object_id].counters['+1/+1'])
        self.replay(s, load=True)

    def test_actual_foreign_entry_is_nontargeted_and_pending_reload_replays(self):
        s = self.session(322002)
        e = s.engine
        source = self.add(e, 'Aquastrand Spider')
        source.counters['+1/+1'] = 2
        foreign = self.add(e, 'Generic Bound Body', seat='B', zone='hand', ref='foreign-recipient')
        probe = self.add(e, 'Unsummon', zone='hand')
        self.ready(s, probe, {'U':1})
        e.permissions.invalidate_current()
        e.state.pending_decision = None
        e.state.active_player = 'B'
        e.state.players['B'].mana_pool['W'] = 2
        e._grant_priority('B')
        e.pump()
        self.checkpoint(s)
        action = next(a for a in s.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == foreign.ref)
        accepted = s.act('pilot:B', {'action_id':action['id'], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(s)
        self.assertEqual((), tuple(e.state.stack[-1].targets))
        from quorune.session import CommanderSession
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'pending-graft'
            s.save(path)
            resumed = CommanderSession.load(self.db, path)
        self.choose(resumed, 'move')
        self.assertEqual(1, resumed.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual(1, resumed.state.cards[foreign.object_id].counters['+1/+1'])
        self.assertEqual('B', resumed.state.cards[foreign.object_id].controller)
        self.replay(resumed, load=True)

    def test_departed_recipient_does_not_remove_source_counter_and_replays(self):
        s = self.session(322003)
        e = s.engine
        source = self.add(e, 'Aquastrand Spider')
        source.counters['+1/+1'] = 2
        body = self.add(e, 'Generic Bound Body', zone='hand')
        bounce = self.add(e, 'Unsummon', zone='hand')
        self.ready(s, body, {'U':2, 'W':2, 'C':6})
        self.checkpoint(s)
        action = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == body.ref)
        self.assertTrue(s.act('pilot:A', {'action_id':action['id'], 'pay':'auto'}).ok)
        while any(item.card_object_id == body.object_id for item in e.state.stack):
            self.assertTrue(s.act(s.pending_principals()[0], {'action_id':'pass'}).ok)
        self.cast(s, bounce, (body.ref,))
        self.assertEqual('hand', e.state.cards[body.object_id].zone)
        self.assertEqual(2, e.state.cards[source.object_id].counters['+1/+1'])
        self.assertFalse(e.state.stack)
        self.replay(s, load=True)

    def test_replacement_order_defers_removal_then_moves_one_and_places_replaced_amount(self):
        s = self.session(322004)
        e = s.engine
        source = self.add(e, 'Aquastrand Spider')
        source.counters['+1/+1'] = 2
        self.add(e, 'Hardened Scales')
        self.add(e, 'Doubling Season')
        body = self.add(e, 'Generic Bound Body', zone='hand')
        self.ready(s, body, {'W':2})
        self.checkpoint(s)
        self.cast(s, body)
        accepted = s.act('pilot:A', {'action_id':'choose', 'choice':'move'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('replacement.order', e.state.pending_decision.kind)
        self.assertEqual(2, e.state.cards[source.object_id].counters['+1/+1'])
        self.assertFalse(e.state.cards[body.object_id].counters)
        resumed = self.replay(s, load=True)
        before = authoritative_state_hash(resumed.state)
        packet = resumed.packet('pilot:A', full=True)['decision']
        option = packet['ctx']['options'][0]['id']
        self.assertFalse(resumed.act('pilot:B', {'action_id':'choose', 'replacement':option}).ok)
        self.assertEqual(before, authoritative_state_hash(resumed.state))
        while resumed.state.pending_decision and resumed.state.pending_decision.kind == 'replacement.order':
            packet = resumed.packet('pilot:A', full=True)['decision']
            result = resumed.act('pilot:A', {'action_id':'choose', 'replacement':packet['ctx']['options'][0]['id']})
            self.assertTrue(result.ok, result.summary)
        self.resolve(resumed)
        self.assertEqual(1, resumed.state.cards[source.object_id].counters['+1/+1'])
        self.assertIn(resumed.state.cards[body.object_id].counters['+1/+1'], (3,4))
        self.replay(resumed, load=True)

    def test_omitting_atomic_movement_mutant_is_killed(self):
        with patch('quorune.rules.counter_movement.execute_counter_movement', return_value=()):
            with self.assertRaises(AssertionError):
                self.test_actual_graft_cast_and_other_entry_transfer_decline_and_replay()

    def test_source_bounce_before_graft_resolution_keeps_recipient_unchanged_and_replays(self):
        s = self.session(322005)
        e = s.engine
        source = self.add(e, 'Aquastrand Spider')
        source.counters['+1/+1'] = 2
        body = self.add(e, 'Generic Bound Body', zone='hand')
        bounce = self.add(e, 'Unsummon', zone='hand')
        self.ready(s, body, {'U':2, 'W':2})
        self.checkpoint(s)
        action = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == body.ref)
        self.assertTrue(s.act('pilot:A', {'action_id':action['id'], 'pay':'auto'}).ok)
        while any(item.card_object_id == body.object_id for item in e.state.stack):
            self.assertTrue(s.act(s.pending_principals()[0], {'action_id':'pass'}).ok)
        self.cast(s, bounce, (source.ref,))
        self.assertEqual('hand', e.state.cards[source.object_id].zone)
        self.assertFalse(e.state.cards[body.object_id].counters)
        self.assertFalse(e.state.stack)
        self.replay(s, load=True)

    def test_model_movement_rejects_same_stale_phased_empty_and_zero_placement(self):
        s = self.session(322006)
        e = s.engine
        source = self.add(e, 'Aquastrand Spider')
        source.counters['+1/+1'] = 2
        body = self.add(e, 'Generic Bound Body')
        intent = MoveCountersIntent('A', source.ref, source.logical_object_id,
            body.ref, body.logical_object_id, '+1/+1', 1, 'Graft', 'model-source')
        for change in ({'to_ref':source.ref, 'to_identity':source.logical_object_id},
                       {'from_identity':'different'}, {'to_identity':'different'}, {'amount':3}):
            before = authoritative_state_hash(e.state)
            self.assertEqual((), execute_counter_movement(e, replace(intent, **change)))
            self.assertEqual(before, authoritative_state_hash(e.state))
        for card in (source, body):
            card.phased_out = True
            before = authoritative_state_hash(e.state)
            self.assertEqual((), execute_counter_movement(e, intent))
            self.assertEqual(before, authoritative_state_hash(e.state))
            card.phased_out = False
        from quorune.counter_placement import prepare_counter_placements
        prepared = prepare_counter_placements(e, ())
        with patch('quorune.rules.counter_movement.prepare_counter_placements', return_value=prepared):
            before = authoritative_state_hash(e.state)
            self.assertEqual((), execute_counter_movement(e, intent))
            self.assertEqual(before, authoritative_state_hash(e.state))
        from quorune.counter_placement import CounterPlacementRequest
        prepared = prepare_counter_placements(e, (CounterPlacementRequest('permanent',body.object_id,'+1/+1',1,'A'),))
        event = prepared.events[0]
        zero = replace(event, payload={**event.payload, 'amount':0})
        prepared = replace(prepared, events=(zero,))
        with patch('quorune.rules.counter_movement.prepare_counter_placements', return_value=prepared):
            before = authoritative_state_hash(e.state)
            self.assertEqual((), execute_counter_movement(e, intent))
            self.assertEqual(before, authoritative_state_hash(e.state))
        for amount in (True,0,-1):
            with self.assertRaises(ValueError):
                replace(intent, amount=amount)

    def test_choice_model_keeps_controller_and_both_incarnations_and_rejects_open_fields(self):
        from quorune.object_query import ObjectQueryResult
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.context import SemanticChoiceContext
        from quorune.semantic_choices.graft import GraftCounterMovementHandler
        from quorune.semantic_choices.model import SemanticChoiceContinuation, SemanticChoiceError, SemanticChoiceFrame
        source = ObjectQueryResult('source', 'source', 'Graft model', 'A', 'B', 'battlefield',
            counters=FrozenMap({'+1/+1':2}), logical_object_id='source@0')
        recipient = ObjectQueryResult('recipient', 'recipient', 'Recipient model', 'C', 'C', 'battlefield',
            types=('creature',), logical_object_id='recipient@0')
        class Query:
            active_seats = ('A','B','C','D')
            def object(self, ref, *, zones=None):
                result = source if ref=='source' else recipient if ref=='recipient' else None
                return result if result and (zones is None or result.zone in zones) else None
        query = Query()
        handler = GraftCounterMovementHandler()
        effect = {'op':handler.operation, 'player':'A', 'from_ref':'source', 'from_identity':'source@0',
                  'to_ref':'recipient', 'to_identity':'recipient@0'}
        context = SemanticChoiceContext('A','stack','A','Graft','source',None,'graft',1,query)
        prepared = handler.prepare(effect, context)
        self.assertIsNotNone(prepared.request)
        continuation = SemanticChoiceContinuation(handler.handler_id,1,'stack',prepared.continuation_effect,
            (),None,'Graft',SemanticChoiceFrame('graft',1,'stack',0,'A'))
        self.assertEqual('A', handler.complete(continuation, {'choice':'move'}, query).intents[0].actor)
        for change in ({'logical_object_id':'source@1'}, {'zone':'exile'}, {'phased_out':True}, {'counters':FrozenMap()}):
            previous = source
            source = replace(source, **change)
            self.assertIsNone(handler.prepare(effect, context).request)
            self.assertFalse(handler.complete(continuation, {'choice':'move'}, query).intents)
            source = previous
        recipient = replace(recipient, logical_object_id='recipient@1')
        self.assertIsNone(handler.prepare(effect, context).request)
        for change in ({'extra':1}, {'from_identity':True}, {'player':'B'}):
            with self.assertRaises(SemanticChoiceError):
                handler.prepare({**effect, **change}, context)
        for choice in (True, [], 'put'):
            with self.assertRaises(SemanticChoiceError):
                handler.complete(continuation, {'choice':choice}, query)

    def test_zero_source_counter_does_not_trigger_on_actual_creature_entry(self):
        s = self.session(322007)
        e = s.engine
        self.add(e, 'Aquastrand Spider')
        body = self.add(e, 'Generic Bound Body', zone='hand')
        self.ready(s, body, {'W':2})
        self.checkpoint(s)
        self.cast(s, body)
        self.assertFalse(e.state.stack)
        self.assertFalse(e.state.cards[body.object_id].counters)
        self.replay(s, load=True)
