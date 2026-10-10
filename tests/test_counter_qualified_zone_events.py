from __future__ import annotations

"""Independent CR122.2/603.6/603.10/700.4 named-counter occurrence contract.

Departures use counters and controller immediately before the event; entries
use the committed new incarnation after entry replacements. Counters disappear
on departure, but the sealed event fact survives. Replacement exile is no death.
One qualifying object triggers each subscription once, including observers
that leave simultaneously. Missing facts never become a known-empty snapshot.
"""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.compiler.unlock_frontier import analyze_card_unlocks
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.trigger_discovery import semantic_event_condition_matches
from quorune.zone_trigger_events import ZoneChangeOccurrence, ZoneTriggerEventError, normalized_zone_trigger_events
from scripts.build_test_database import build_fixture_database
from scripts.work_selection_cohort_measurements import _bound_effect_program_measurement, _matches_probe
import test_bound_effect_programs as bound_witnesses
from test_qualified_zone_event_queries import query_record
from test_zone_trigger_events import occurrence


COUNTER_CAPABILITY = 'trigger.event.counter_qualified_zone_change'
FIXTURES = [bound_witnesses.FIXTURE, ROOT/'tests/fixtures/counter-qualified-zone-cards.json',
            ROOT/'tests/fixtures/event-card-return.json', ROOT/'tests/fixtures/copied-self-entry-counter-cards.json',
            ROOT/'tests/fixtures/qualified-zone-event-cards.json', ROOT/'tests/fixtures/linked-exile-return-cards.json']


class CounterQualifiedZoneCompilerTests(unittest.TestCase):
    def test_counter_zone_probe_separates_whole_closure_from_leaf_gains(self):
        text = 'Whenever a creature you control with a +1/+1 counter on it dies, draw a card.'
        records = (
            replace(query_record(text), oracle_id='fixture:counter-zone-probe-1'),
            replace(query_record(text+'\nWhenever a player sneezes, draw a card.'), oracle_id='fixture:counter-zone-probe-2'),
        )
        probe = 'counter-qualified-zone-event-existing-owner-v1'
        self.assertTrue(_matches_probe(probe, text, card_record=records[0]))
        self.assertFalse(_matches_probe('qualified-zone-event-query-existing-owner-v1', text, card_record=records[0]))
        self.assertFalse(_matches_probe(probe, 'Whenever a creature you control dies, draw a card.', card_record=records[0]))
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row = next(r for r in raw['capabilities'] if r['id'] == COUNTER_CAPABILITY)
        row.update(status='blocked', blockers=['Constructed pre-expansion counter-fact boundary'])
        before_registry = CapabilityRegistry(raw)
        before = [analyze_card_unlocks(compile_oracle_card(record, capability_registry=before_registry,
            capability_profile='commander_review'), program=None, program_error=None,
            capabilities=before_registry, profile='commander_review') for record in records]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'probe.sqlite3'
            build_fixture_database([bound_witnesses.FIXTURE], path)
            with CardDatabase(path) as db:
                measured = _bound_effect_program_measurement(frontier={'cards':before},
                    bundle_id='bundle:counter-qualified-zone-event', probe_id=probe,
                    cards_by_oracle_id={r.oracle_id:r for r in records}, database=db,
                    cohort_fingerprint='constructed-counter-zone-boundary',
                    coverage={'minimum_complete_card_gain':50,'minimum_exact_ability_gain':100,'minimum_material_residual_reduction':100})
        self.assertEqual(2, measured['affected_commander_cards'])
        self.assertEqual(2, measured['exact_ability_gain'])
        self.assertEqual(1, measured['complete_card_gain'])
        self.assertEqual('retired_below_harvest_floor', measured['decision'])
        self.assertFalse(measured['grants_gameplay_trust'])

    def test_counter_occurrence_versions_freeze_both_timings_and_preserve_unknown(self):
        previous = {'+1/+1': 2, 'bounty': 0}
        event = occurrence(origin='battlefield', destination='graveyard', schema_version=2,
                           previous_counters=previous, current_counters={})
        previous.clear()
        self.assertEqual({'+1/+1': 2, 'bounty': 0}, dict(event.previous_counters))
        self.assertEqual(event, ZoneChangeOccurrence(**event.to_dict()))
        died = normalized_zone_trigger_events(event)
        self.assertTrue(all(tuple(e.context['counter_names']) == ('+1/+1',) for e in died))
        entered = normalized_zone_trigger_events(occurrence(schema_version=2,
            previous_counters={'bounty': 3}, current_counters={'+1/+1': 1}))
        self.assertTrue(all(tuple(e.context['counter_names']) == ('+1/+1',) for e in entered))
        old = occurrence()
        self.assertNotIn('previous_counters', old.to_dict())
        self.assertEqual(old, ZoneChangeOccurrence(**old.to_dict()))
        self.assertTrue(all('counter_names' not in e.context for e in normalized_zone_trigger_events(old)))
        for fields in ({'previous_counters':{}}, {'schema_version':2},
                       {'schema_version':2,'previous_counters':{'+1/+1':True},'current_counters':{}},
                       {'schema_version':2,'previous_counters':{' Bounty ':1},'current_counters':{}},
                       {'schema_version':2,'previous_counters':{'bounty':-1},'current_counters':{}}):
            with self.subTest(fields=fields), self.assertRaises(ZoneTriggerEventError):
                occurrence(**fields)

    def test_counter_qualified_programs_bind_named_counter_and_dependency(self):
        registry = load_default_capability_registry()
        for subject, event in (
            ('a creature you control with a +1/+1 counter on it', 'creature.dies'),
            ('another nontoken creature you control with a bounty counter on it', 'creature.dies'),
            ('a permanent an opponent controls with a slime counter on it', 'permanent.graveyard'),
        ):
            with self.subTest(subject=subject):
                compiled = compile_oracle_card(query_record(f'Whenever {subject} dies, draw a card.'),
                    capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', compiled.status)
                node = compiled.faces[0].nodes[0]
                self.assertEqual(event, node.event)
                self.assertIn(COUNTER_CAPABILITY, node.capability_dependencies)
                self.assertEqual(1, node.span.line)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'counter-programs.sqlite3'
            build_fixture_database(FIXTURES, path)
            with CardDatabase(path) as db:
                for name in ('Skyclave Shadowcat', 'Meltstrider Eulogist', 'Tributary Instructor'):
                    row = db.lookup(name)
                    program = compile_best_available_card_program(db, row, semantic_registry=SemanticRegistry(),
                        capability_registry=registry, capability_profile='commander_review')
                    binding = bind_card_program_runtime(program, capability_registry=registry, profile='commander_review')
                    with self.subTest(name=name):
                        self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
                        trigger = next(p for p in program.abilities if p.event == 'creature.dies')
                        self.assertIn('counter_names', json.dumps(trigger.event_condition))

    def test_counter_qualified_boundaries_and_missing_owner_fail_closed(self):
        registry = load_default_capability_registry()
        for qualifier in ('with two +1/+1 counters on it', 'with a counter on it',
                          'with no counters on it', 'with a counter of the chosen kind on it',
                          'that entered this turn', 'that was dealt damage this turn'):
            record = query_record(f'Whenever a creature you control {qualifier} dies, draw a card.')
            with self.subTest(qualifier=qualifier):
                self.assertNotEqual('exact', compile_oracle_card(record,
                    capability_registry=registry, capability_profile='commander_review').status)
        text = 'Whenever a creature you control with a +1/+1 counter on it dies, draw a card.'
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for dependency in (COUNTER_CAPABILITY, 'trigger.event.qualified_zone_change', 'trigger.event.normalized_zone_change'):
            mutated = deepcopy(raw)
            row = next(r for r in mutated['capabilities'] if r['id'] == dependency)
            row.update(status='blocked', blockers=['Constructed missing counter occurrence owner'])
            self.assertNotEqual('exact', compile_oracle_card(query_record(text),
                capability_registry=CapabilityRegistry(mutated), capability_profile='commander_review').status)

    def test_counter_fact_absence_cannot_read_the_current_object(self):
        condition = {'field':'counter_names','op':'contains_any','value':['+1/+1']}
        source = SimpleNamespace(controller='A', owner='A', ref='observer', object_id='observer', counters={'+1/+1':4})
        host = SimpleNamespace(state=SimpleNamespace(active_player='A'))
        self.assertFalse(semantic_event_condition_matches(host, condition, source=source, context={'counter_names':[]}))
        self.assertTrue(semantic_event_condition_matches(host, condition, source=source, context={'counter_names':['+1/+1']}))
        for context in ({}, {'counter_names':None}, {'counter_names':'bounty'}, {'counter_names':[True]},
                        {'counter_names':['bounty','bounty']}):
            with self.subTest(context=context), self.assertRaises(GameRuleError):
                semantic_event_condition_matches(host, condition, source=source, context=context)


class CounterQualifiedZoneActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'counter-zone.sqlite3'
        build_fixture_database(FIXTURES, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Counter zone witness', [
            DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)
        ], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    session = bound_witnesses.BoundEffectProgramRuntimeTests.session
    add = bound_witnesses.BoundEffectProgramRuntimeTests.add
    ready = bound_witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint = bound_witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve = bound_witnesses.BoundEffectProgramRuntimeTests.resolve
    replay = bound_witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_sacrifice_uses_counter_lki_and_replays_private_draw(self):
        session = self.session(281001)
        engine = session.engine
        observer = self.add(engine, 'Skyclave Shadowcat')
        feeder = self.add(engine, 'Carrion Feeder')
        victim = self.add(engine, 'Generic Bound Body')
        victim.counters['+1/+1'] = 1
        action = self.ready(session, feeder, {})
        self.assertTrue(engine.semantic_program_is_current_trusted(next(
            p for p in engine.semantics.programs_for_oracle(observer.oracle_id) if p.event == 'creature.dies')))
        hand = len(engine.state.players['A'].zones['hand'])
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:B', {'action_id':action['id'], 'cost_cards':[victim.ref]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id':action['id'], 'cost_cards':[victim.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual({}, engine.state.cards[victim.object_id].counters)
        self.assertEqual('graveyard', engine.state.cards[victim.object_id].zone)
        self.resolve(session)
        self.assertEqual(hand+1, len(engine.state.players['A'].zones['hand']))
        self.assertEqual(1, engine.state.cards[feeder.object_id].counters.get('+1/+1'))
        private_card = engine.state.cards[engine.state.players['A'].zones['hand'][-1]]
        for seat in 'BCD':
            self.assertNotIn(private_card.ref, json.dumps(session.packet('pilot:'+seat, full=True)))
        self.replay(session, load=True)

    def test_zero_wrong_counter_and_previous_controller_do_not_qualify(self):
        session = self.session(281002)
        engine = session.engine
        self.add(engine, 'Skyclave Shadowcat')
        self.add(engine, 'Skyclave Shadowcat', seat='B', ref='observer-b')
        feeder = self.add(engine, 'Carrion Feeder')
        hand_a = len(engine.state.players['A'].zones['hand'])
        hand_b = len(engine.state.players['B'].zones['hand'])
        for index, counters in enumerate(({}, {'bounty':1}, {'+1/+1':0})):
            victim = self.add(engine, 'Generic Bound Body', ref='counterexample-'+str(index))
            victim.counters.update(counters)
            action = self.ready(session, feeder, {})
            accepted = session.act('pilot:A', {'action_id':action['id'], 'cost_cards':[victim.ref]})
            self.assertTrue(accepted.ok, accepted.summary)
            self.resolve(session)
        self.assertEqual(hand_a, len(engine.state.players['A'].zones['hand']))
        # A owns this body, but B controls it immediately before its death.
        victim = self.add(engine, 'Generic Bound Body', ref='stolen')
        victim.counters['+1/+1'] = 1
        engine.change_control(victim.object_id, 'B', reason='Counter trigger controller distinction')
        feeder_b = self.add(engine, 'Carrion Feeder', seat='B', ref='feeder-b')
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority('B')
        engine.pump()
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        action = next(a for a in actions if a['id'].startswith('activate:'+feeder_b.ref+':'))
        accepted = session.act('pilot:B', {'action_id':action['id'], 'cost_cards':[victim.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(hand_a, len(engine.state.players['A'].zones['hand']))
        self.assertEqual(hand_b+1, len(engine.state.players['B'].zones['hand']))
        self.assertIn(victim.object_id, engine.state.players['A'].zones['graveyard'])

    def test_simultaneous_counter_deaths_keep_departing_observers_apnap_and_replay(self):
        session = self.session(281003)
        engine = session.engine
        sources = {seat:self.add(engine, 'Skyclave Shadowcat', seat=seat, ref='observer-'+seat) for seat in 'AC'}
        bodies = {seat:self.add(engine, 'Generic Bound Body', seat=seat, ref='body-'+seat) for seat in 'AC'}
        for card in (*sources.values(), *bodies.values()):
            card.counters['+1/+1'] = 1
        wipe = self.add(engine, 'Day of Judgment', zone='hand')
        action = self.ready(session, wipe, {'W':2,'C':2})
        hand = {seat:len(engine.state.players[seat].zones['hand']) for seat in 'AC'}
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(12):
            if not any(item.kind == 'spell' for item in engine.state.stack):
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id':'pass'}).ok)
        # Two triggers per departing observer require ordinary controller ordering.
        self.assertEqual('trigger.order', engine.state.pending_decision.kind)
        session = self.replay(session, load=True)
        engine = session.engine
        ordered = []
        for _ in range(4):
            if engine.state.pending_decision.kind != 'trigger.order':
                break
            principal = session.pending_principals()[0]
            rows = session.packet(principal, full=True)['decision']['ctx']['triggers']
            accepted = session.act(principal, {'action_id':'order','triggers':[row['id'] for row in rows]})
            self.assertTrue(accepted.ok, accepted.summary)
            ordered.append(principal)
        self.assertEqual(['pilot:A','pilot:C'], ordered)
        self.assertEqual(['A','A','C','C'], [item.controller for item in engine.state.stack])
        self.assertTrue(all(engine.state.cards[card.object_id].zone == 'graveyard'
                            and not engine.state.cards[card.object_id].counters for card in (*sources.values(), *bodies.values())))
        self.resolve(session)
        self.assertEqual(hand['A']-1+2, len(engine.state.players['A'].zones['hand']))
        self.assertEqual(hand['C']+2, len(engine.state.players['C'].zones['hand']))
        self.replay(session, load=True)

    def test_counter_entry_uses_initialized_new_incarnation_and_replays(self):
        session = self.session(281004)
        engine = session.engine
        self.add(engine, 'Generic Counter Entry Observer')
        self.add(engine, 'Generic Bounty Entry Observer')
        body = self.add(engine, 'Generic Copied Counter Creature', zone='hand')
        body.counters.update({'+1/+1':7, 'bounty':4})
        action = self.ready(session, body, {'W':1})
        hand = len(engine.state.players['A'].zones['hand'])
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('battlefield', body.zone)
        self.assertEqual({'+1/+1':2}, body.counters)
        self.assertEqual(hand, len(engine.state.players['A'].zones['hand']))  # Cast one; draw once, despite two counters.
        self.assertEqual(40, engine.state.players['A'].life)  # Previous bounty counters cannot qualify entry.
        self.replay(session, load=True)

    def test_replaced_exile_has_counter_qualified_leave_without_death(self):
        session = self.session(281005)
        engine = session.engine
        self.add(engine, 'Skyclave Shadowcat')
        self.add(engine, 'Generic Counter Leave Observer')
        replacement = self.add(engine, 'Generic Query Exiler', seat='B')
        programs = engine.semantics.programs_for_oracle(replacement.oracle_id)
        self.assertTrue(programs)
        self.assertTrue(all(engine.semantic_program_is_current_trusted(p) for p in programs))
        feeder = self.add(engine, 'Carrion Feeder')
        body = self.add(engine, 'Generic Bound Body')
        body.counters['+1/+1'] = 1
        action = self.ready(session, feeder, {})
        hand = len(engine.state.players['A'].zones['hand'])
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id':action['id'],'cost_cards':[body.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('exile', body.zone)
        self.assertEqual({'sample':1}, body.counters)  # Replacement counters belong to the new exile incarnation.
        self.assertEqual(hand, len(engine.state.players['A'].zones['hand']))
        self.assertEqual(42, engine.state.players['A'].life)
        self.assertEqual(1, feeder.counters.get('+1/+1'))  # Sacrifice price remains paid when replaced.
        self.replay(session, load=True)

    def test_nontoken_counter_death_creates_one_printed_token_and_excludes_tokens(self):
        session = self.session(281006)
        engine = session.engine
        observer = self.add(engine, 'Rayblade Trooper')
        self.assertTrue(all(engine.semantic_program_is_current_trusted(p)
                            for p in engine.semantics.programs_for_oracle(observer.oracle_id)))
        feeder = self.add(engine, 'Carrion Feeder')
        excluded = self.add(engine, 'Generic Bound Body', ref='excluded-token')
        excluded.is_token = True
        excluded.object_kind = 'token'
        excluded.counters['+1/+1'] = 1
        action = self.ready(session, feeder, {})
        accepted = session.act('pilot:A', {'action_id':action['id'],'cost_cards':[excluded.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual([], [card for card in engine.state.cards.values() if card.is_token and card.zone == 'battlefield'])
        body = self.add(engine, 'Generic Bound Body', ref='eligible-body')
        body.counters['+1/+1'] = 3
        action = self.ready(session, feeder, {})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id':action['id'],'cost_cards':[body.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        tokens = [card for card in engine.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(1, len(tokens))
        data = engine._effective_card_data(tokens[0])
        self.assertEqual('A', tokens[0].controller)
        self.assertEqual({'human','soldier'}, set(engine._type_parts(data['type_line'])[1]))
        self.assertEqual(('1','1'), (str(data['power']), str(data['toughness'])))
        self.assertEqual(('W',), tuple(data['colors']))
        self.replay(session, load=True)

    def test_departure_counter_omission_mutant_is_killed_by_actual_action(self):
        original = normalized_zone_trigger_events
        def omit_counters(event):
            return original(replace(event, schema_version=1, previous_counters=None, current_counters=None))
        with patch('quorune.zone_trigger_processing.normalized_zone_trigger_events', side_effect=omit_counters):
            with self.assertRaisesRegex(AssertionError, 'Counter-qualified zone event requires sealed counter facts'):
                self.test_actual_sacrifice_uses_counter_lki_and_replays_private_draw()

    def test_pending_counter_result_cannot_modify_blinked_observer_incarnation(self):
        for name, seed in (('Generic Ordinary Counter Renewal Observer',281007), ('Generic Counter Renewal Observer',281008)):
            with self.subTest(name=name):
                self._counter_observer_blink_witness(name, seed)

    def _counter_observer_blink_witness(self, name, seed):
        session = self.session(seed)
        engine = session.engine
        observer = self.add(engine, name)
        feeder = self.add(engine, 'Carrion Feeder')
        victim = self.add(engine, 'Generic Bound Body')
        victim.counters['+1/+1'] = 1
        blink = self.add(engine, 'Generic Immediate Blink', zone='hand')
        action = self.ready(session, feeder, {'W':1})
        incarnation = observer.logical_object_id
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id':action['id'],'cost_cards':[victim.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(any(item.source_object_id == observer.object_id for item in engine.state.stack))
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        response = next(a for a in actions if a.get('card') == blink.ref)
        accepted = session.act('pilot:A', {'action_id':response['id'],'targets':[observer.ref],'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(12):
            if not any(item.kind == 'spell' for item in engine.state.stack):
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id':'pass'}).ok)
        self.assertEqual('battlefield', observer.zone)
        self.assertNotEqual(incarnation, observer.logical_object_id)
        self.assertTrue(any(item.source_object_id == observer.object_id for item in engine.state.stack))
        self.resolve(session)
        self.assertEqual({}, observer.counters)
        self.assertEqual(1, feeder.counters.get('+1/+1'))
        self.replay(session, load=True)
        # The new incarnation can receive a subsequent trigger of its own.
        next_victim = self.add(engine, 'Generic Bound Body', ref='next-victim')
        next_victim.counters['+1/+1'] = 1
        next_action = self.ready(session, feeder, {})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id':next_action['id'],'cost_cards':[next_victim.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(1, observer.counters.get('+1/+1'))
        self.assertEqual(2, feeder.counters.get('+1/+1'))
        self.replay(session, load=True)

    def test_self_counter_reference_alias_mutant_is_killed_by_actual_blink(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        def alias_source(value):
            if isinstance(value, dict):
                return {key:alias_source(child) for key,child in value.items()}
            if isinstance(value, (list,tuple)):
                return [alias_source(child) for child in value]
            return '$source' if value == '$source.zone_object' else value
        def unsafe_resolve(host, value, item):
            return resolve_semantic_value(host, alias_source(value), item)
        with patch('quorune.engine.resolve_semantic_value', side_effect=unsafe_resolve):
            with self.assertRaisesRegex(AssertionError, "\\+1/\\+1"):
                self._counter_observer_blink_witness('Generic Counter Renewal Observer',281009)
