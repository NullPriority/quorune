from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses
import test_resolution_public_conditions as combat_witnesses


class AggregatedZoneTriggerCompilerTests(unittest.TestCase):
    def test_singular_qualified_damage_keeps_each_source_occurrence(self):
        registry = load_default_capability_registry()
        for subject in ('an Assassin', 'a legendary creature', 'a black creature', 'a red or green creature',
            'a Goblin or Orc', 'a Kraken, Leviathan, Octopus, or Serpent'):
            text = 'Whenever ' + subject + ' you control deals combat damage to a player, draw a card.'
            with self.subTest(subject=subject):
                ir = compile_oracle_card(query_record(text), capability_registry=registry)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual('damage.dealt', node.event)
                self.assertNotIn('one_or_more_event_batch', node.runtime_coverage)
                self.assertIn('current_ability_fragment_required', node.runtime_coverage)
                self.assertEqual(text, node.text)

    def test_singular_qualified_damage_predicates_and_omission_fail_closed(self):
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
        from quorune.compiler.fixed_public_action_event_bindings import _qualified_damage_singular_spec
        from quorune.trigger_discovery import semantic_event_matches
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.model import CardInstance
        source = CardInstance(object_id='observer', ref='observer', oracle_id='fixture:singular',
            printed_name='Generic Singular Observer', owner='A', controller='A', zone='battlefield')
        host = SimpleNamespace(active_seats=tuple('ABCD'), state=SimpleNamespace(cards={}, active_player='A'))
        text = 'Whenever an Assassin you control deals combat damage to an opponent, draw a card.'
        binding = fixed_counter_trigger_binding(text)
        program = SimpleNamespace(event=binding.event.value, active_zone='battlefield', event_condition=binding.public_condition)
        event = {'source_controller': 'A', 'source_types': ['creature'], 'source_subtypes': ['assassin'],
            'source_colors': ['B'], 'source_zone': 'battlefield', 'target_kind': 'player', 'player': 'B', 'combat': True}
        self.assertTrue(semantic_event_matches(host, program, source, 'damage.dealt', event))
        for change in ({'source_controller': 'C'}, {'source_subtypes': ['human']}, {'combat': False},
            {'target_kind': 'permanent'}, {'source_zone': 'stack'}, {'player': 'A'}):
            self.assertFalse(semantic_event_matches(host, program, source, 'damage.dealt', {**event, **change}), change)
        for subject in ('chosen creature', 'modified creature', 'creature with flying', 'double-faced creature'):
            self.assertIsNone(_qualified_damage_singular_spec(
                'Whenever a ' + subject + ' you control deals combat damage to a player, draw a card.'))
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == 'trigger.event.qualified_damage_singular').update(
            status='blocked', blockers=['singular damage authority withdrawn'])
        self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=CapabilityRegistry(raw)).status)
        with patch('quorune.compiler.fixed_public_action_event_bindings._qualified_damage_singular_spec', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=load_default_capability_registry()).status)

    def test_explicit_trigger_sources_use_canonical_order_independent_of_card_dict_insertion(self):
        from quorune.trigger_discovery import _ordered_event_sources
        first = SimpleNamespace(zone_timestamp=1, object_id='b', logical_object_id='b@1')
        second = SimpleNamespace(zone_timestamp=2, object_id='a', logical_object_id='a@0')
        tie = SimpleNamespace(zone_timestamp=1, object_id='a', logical_object_id='a@1')
        host = SimpleNamespace(_semantic_event_sources=lambda: [second, first, tie])
        expected = [tie, first, second]
        self.assertEqual(expected, _ordered_event_sources(host, None))
        for sources in ([second, first, tie], [first, tie, second], [tie, second, first]):
            self.assertEqual(expected, _ordered_event_sources(host, sources))
            self.assertEqual(3, len(sources))

    def test_qualified_damage_batch_keeps_subtype_and_per_player_aggregation(self):
        text = 'Whenever one or more Faeries you control deal combat damage to a player, draw a card.'
        ir = compile_oracle_card(query_record(text), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual('damage.dealt', node.event)
        self.assertIn('one_or_more_event_batch', node.runtime_coverage)
        self.assertIn('current_ability_fragment_required', node.runtime_coverage)
        self.assertIn('source_subtypes', json.dumps(node.event_condition))
        self.assertIn('faerie', json.dumps(node.event_condition))
        self.assertEqual(text, node.text)

    def test_qualified_damage_batch_rejects_unknown_snapshot_fields_and_keeps_predicate_contrasts(self):
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
        from quorune.compiler.fixed_public_action_event_bindings import _qualified_damage_batch_spec
        from quorune.trigger_discovery import semantic_event_matches
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.model import CardInstance
        source = CardInstance(object_id='observer', ref='OBSERVER', oracle_id='fixture:batch-observer',
            printed_name='Generic Batch Observer', owner='A', controller='A', zone='battlefield')
        host = SimpleNamespace(active_seats=tuple('ABCD'), state=SimpleNamespace(cards={}, active_player='A'))
        text = 'Whenever one or more Faeries you control deal combat damage to a player, draw a card.'
        binding = fixed_counter_trigger_binding(text)
        program = SimpleNamespace(event=binding.event.value, active_zone='battlefield', event_condition=binding.public_condition)
        event = {'source_controller':'A', 'source_types':['creature'], 'source_subtypes':['faerie'],
                 'source_colors':['U'], 'source_zone':'battlefield', 'target_kind':'player', 'player':'B', 'combat':True}
        self.assertTrue(semantic_event_matches(host, program, source, 'damage.dealt', event))
        for change in ({'source_controller':'C'}, {'source_subtypes':['human']}, {'combat':False},
                       {'target_kind':'permanent'}, {'source_zone':'stack'}):
            self.assertFalse(semantic_event_matches(host, program, source, 'damage.dealt', {**event, **change}), change)
        self.assertTrue(semantic_event_matches(host, program, source, 'damage.dealt',
            {**event, 'source_types':['enchantment','kindred']}))
        for subject in ('creature tokens', 'nontoken Faeries', 'face-down creatures',
                        'creatures with +1/+1 counters on them', 'creatures with power 4 or greater'):
            self.assertIsNone(_qualified_damage_batch_spec(
                f'Whenever one or more {subject} you control deal combat damage to a player, draw a card.'))
        with patch('quorune.compiler.fixed_public_action_event_bindings._qualified_damage_batch_spec', return_value=None):
            with self.assertRaises(AssertionError):self.test_qualified_damage_batch_keeps_subtype_and_per_player_aggregation()
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in ('trigger.event.qualified_damage_batch', 'trigger.event.normalized_damage'):
            changed = deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked', blockers=['qualified damage test'])
            self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=CapabilityRegistry(changed)).status)

    def test_qualified_damage_batch_preserves_closed_source_counter_consequence(self):
        text = 'Whenever one or more Heroes you control deal damage to a player, put two +1/+1 counters on this creature.'
        ir = compile_oracle_card(query_record(text), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertIn('one_or_more_event_batch', node.runtime_coverage)
        self.assertEqual('place_counters', node.effects[0]['op'])
        self.assertEqual('$source.zone_object', node.effects[0]['card'])
        self.assertEqual(2, node.effects[0]['amount'])
    def test_aggregate_query_preserves_source_span_and_batch_coverage(self):
        for text in (
            'Whenever one or more creatures you control with mana value 3 or less enter, draw a card.',
            'Whenever one or more other Elves you control enter, draw a card.',
            'Whenever one or more other creatures you control die, put a +1/+1 counter on this creature.',
            'Whenever one or more legendary creatures you control enter, draw a card.',
        ):
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertIn('one_or_more_event_batch',node.runtime_coverage)
            self.assertIn('current_ability_fragment_required',node.runtime_coverage)
            self.assertEqual((0,len(text)),(node.span.start,node.span.end))

    def test_exact_single_event_limit_integrates_without_widening_leaf_parser(self):
        text='Whenever this creature becomes tapped, draw a card. This ability triggers only once each turn.'
        from quorune.compiler.tap_state_event_bindings import tap_state_event_binding_spec
        binding=tap_state_event_binding_spec(text,card_name='Generic Query Observer')
        self.assertIsNotNone(binding)
        self.assertTrue(binding.body.endswith('This ability triggers only once each turn.'))
        ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertIsNotNone(ir.faces[0].nodes[0].trigger_limit)

    def test_open_plural_references_and_unsupported_limiting_departures_stay_residual(self):
        for text in (
            'Whenever one or more creatures enter, put a +1/+1 counter on each of them.',
            'Whenever one or more cards are discarded, draw a card.',
            'Whenever one or more other creatures die, draw a card. This ability triggers only once each turn.',
        ):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)


class AggregatedZoneTriggerRuntimeTests(unittest.TestCase):
    finish_combat = combat_witnesses.IndependentDualSourceEventRuntimeTests.finish_combat

    def test_original_ezio_singular_damage_draws_for_each_assassin_and_replays(self):
        from quorune.model import CombatState
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        for index, (defenders, remove_ability) in enumerate(((('B', 'B'), False), (('B', 'C'), False), (('B', 'B'), True))):
            with self.subTest(defenders=defenders, remove_ability=remove_ability):
                session = self.session(311030 + index)
                engine = session.engine
                source = self.add(engine, 'Ezio, Blade of Vengeance', zone='hand')
                removal = self.add(engine, 'Snakeform', zone='hand')
                assassins = [self.add(engine, 'Assassin Initiate', ref='singular-assassin-' + str(n)) for n in range(2)]
                wrong = self.add(engine, 'Generic Bound Body', ref='singular-wrong-subtype')
                offered = self.ready(session, source, {'B': 1, 'U': 2, 'C': 5})
                self.checkpoint(session)
                accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.resolve(session)
                if remove_ability:
                    actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                    offered = next(a for a in actions if a.get('card') == removal.ref)
                    self.checkpoint(session)
                    accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [source.ref], 'pay': 'auto'})
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.resolve(session)
                    self.replay(session)
                program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name),
                    semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
                self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
                    profile='commander_review')['strict_capability_ready'])
                for card in [*assassins, wrong]:
                    card.acquired_control_turn_count = -1
                engine.permissions.invalidate_current()
                engine.state.pending_decision = None
                engine.state.priority_player = None
                engine.state.priority_passes = []
                engine.state.phase = 'combat'
                engine.state.step = 'declare_attackers'
                engine.state.phase_index = 5
                engine.state.combat = CombatState()
                engine._issue_attackers()
                self.checkpoint(session)
                hand_size = len(engine.state.players['A'].zones['hand'])
                attackers = {card.ref: defenders[n] for n, card in enumerate(assassins)}
                attackers[wrong.ref] = 'D'
                before = authoritative_state_hash(session.state)
                denied = session.act('pilot:C', {'action_id': 'attack', 'attackers': attackers})
                self.assertFalse(denied.ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': attackers})
                self.assertTrue(accepted.ok, accepted.summary)
                session = self.finish_combat(session, pending_reload=True)
                self.assertEqual(hand_size + (0 if remove_ability else 2), len(session.state.players['A'].zones['hand']))
                self.assertEqual(0 if remove_ability else 2, sum(event.code == 'stack.trigger' and event.details.get('source') == source.ref
                    for event in session.state.events))
                for seat in 'BCD':
                    packet = str(session.packet('pilot:' + seat, full=True))
                    for oid in session.state.players['A'].zones['hand']:
                        self.assertNotIn(session.state.cards[oid].ref, packet)
                self.replay(session)



    def test_original_kraken_observers_keep_separate_triggers_and_create_complete_tokens(self):
        from quorune.model import CombatState
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        session = self.session(311033)
        engine = session.engine
        first = self.add(engine, 'Spawning Kraken', zone='hand', ref='singular-kraken-A')
        second = self.add(engine, 'Spawning Kraken', ref='singular-kraken-other')
        for source in (first, second):
            program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name),
                semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
            self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
                profile='commander_review')['strict_capability_ready'])
        offered = self.ready(session, first, {'U': 1, 'C': 5})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        for source in (first, second):
            source.acquired_control_turn_count = -1
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.phase = 'combat'
        engine.state.step = 'declare_attackers'
        engine.state.phase_index = 5
        engine.state.combat = CombatState()
        engine._issue_attackers()
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': {first.ref: 'B'}})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(40):
            decision = session.state.pending_decision
            if decision.kind == 'combat.blockers':
                accepted = session.act('pilot:B', {'action_id': 'block', 'blocks': {}})
            elif decision.kind == 'trigger.order':
                self.assertEqual(['A'], list(decision.actors))
                self.order_triggers(session)
                continue
            else:
                accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
            if len(engine.state.stack) == 2 and all(item.context.get('event') == 'damage.dealt' for item in engine.state.stack):
                break
        self.assertEqual(['A', 'A'], [item.controller for item in engine.state.stack])
        self.assertEqual({first.object_id, second.object_id}, {item.source_object_id for item in engine.state.stack})
        session = self.replay(session, load=True)
        session = self.finish_combat(session)
        engine = session.engine
        for seat in 'AB':
            tokens = [card for card in engine.state.cards.values() if card.is_token and card.zone == 'battlefield' and card.controller == seat]
            self.assertEqual(2 if seat == 'A' else 0, len(tokens))
            if not tokens:
                continue
            token = tokens[0]
            self.assertEqual((9, 9), (engine._numeric_stat(token.object_id, 'power'), engine._numeric_stat(token.object_id, 'toughness')))
            self.assertIn('Kraken', engine._effective_card_data(token)['type_line'])
        self.replay(session)

    def test_actual_original_squirrel_damage_batch_coalesces_per_player_and_replays(self):
        from quorune.model import CombatState
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        for index, defenders in enumerate((('B', 'B'), ('B', 'C'))):
            with self.subTest(defenders=defenders):
                session = self.session(305100 + index); engine = session.engine
                sources = [self.add(engine, name, zone='hand', ref='keeper-' + str(n))
                    for n, name in enumerate(('Keeper of Fables', 'The Odd Acorn Gang'))]
                squirrels = [self.add(engine, 'Generic Damage Batch Squirrel', ref='batch-squirrel-' + str(n)) for n in range(2)]
                human = self.add(engine, 'Generic Damage Batch Human', ref='batch-human')
                counter_source = self.add(engine, 'The Thing, Ben Grimm', ref='hero-counter-observer')
                program = compile_best_available_card_program(self.db, self.db.lookup(counter_source.printed_name),
                    semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
                self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
                    profile='commander_review')['strict_capability_ready'])
                for body in [*squirrels, human]:body.acquired_control_turn_count = -1
                self.add(engine, 'Swamp', ref='acorn-black-mana')
                self.add(engine, 'Generic Damage Green Source', ref='acorn-green-mana')
                self.ready(session, sources[0], {'G': 3, 'C': 6});self.checkpoint(session)
                for source in sources:
                    program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name),
                        semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
                    self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
                        profile='commander_review')['strict_capability_ready'])
                    action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==source.ref)
                    self.assertTrue(session.act('pilot:A', {'action_id':action['id'], 'pay':'auto'}).ok)
                    self.resolve(session)
                engine.permissions.invalidate_current();engine.state.pending_decision=None
                engine.state.priority_player=None;engine.state.priority_passes=[]
                engine.state.phase='combat';engine.state.step='declare_attackers';engine.state.phase_index=5
                engine.state.combat=CombatState();engine._issue_attackers()
                self.checkpoint(session)
                hand_size = len(session.state.players['A'].zones['hand'])
                before = authoritative_state_hash(session.state)
                attackers = {squirrels[n].ref:defenders[n] for n in range(2)}
                attackers[human.ref] = 'D'
                self.assertFalse(session.act('pilot:C', {'action_id':'attack', 'attackers':attackers}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                self.assertTrue(session.act('pilot:A', {'action_id':'attack', 'attackers':attackers}).ok)
                resumed = self.finish_combat(session, pending_reload=index == 0)
                self.assertEqual(hand_size + 2 * len(set(defenders)), len(resumed.state.players['A'].zones['hand']))
                for source in sources:
                    count = sum(event.code=='stack.trigger' and event.details.get('source')==source.ref for event in resumed.state.events)
                    self.assertEqual(len(set(defenders)), count)
                self.assertEqual(2 * len(set(defenders)), resumed.state.cards[counter_source.object_id].counters.get('+1/+1', 0))
                for seat in 'BCD':
                    packet = str(resumed.packet('pilot:' + seat, full=True))
                    for key in resumed.state.players['A'].zones['hand']:self.assertNotIn(resumed.state.cards[key].ref, packet)
                self.replay(resumed, load=False)
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'aggregate.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/aggregated-zone-event-triggers.json',
            ROOT/'tests/fixtures/conditional-exile-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Aggregate zone',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def cast(self,session,spell):
        for _ in range(8):
            if session.pending_principals()[0]=='pilot:'+spell.owner:break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        action=next(a for a in session.packet(session.pending_principals()[0],full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spell.ref)
        result=session.act(session.pending_principals()[0],{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.resolve(session)

    def test_actual_token_batch_triggers_once_and_limiter_rejects_later_batch(self):
        session=self.session(304001);engine=session.engine
        source=self.add(engine,'Tocasia\'s Welcome')
        spells=[self.add(engine,'Raise the Alarm',zone='hand',ref='alarm-'+str(i)) for i in range(2)]
        self.ready(session,spells[0],{'W':4,'C':8});self.checkpoint(session)
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spells[0].ref)
        before_hash=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertFalse(rejected.ok)
        self.assertEqual(before_hash,authoritative_state_hash(session.state))
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,spells[0]);self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.cast(session,spells[1]);self.assertEqual(before-1,len(session.state.players['A'].zones['hand']))
        tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A']
        self.assertEqual(4,len(tokens));self.replay(session,load=True)

    def test_separate_real_token_batches_each_trigger_once_without_turn_limit(self):
        session=self.session(304002);engine=session.engine
        source=self.add(engine,'Generic Aggregate Entry Observer')
        spells=[self.add(engine,'Raise the Alarm',zone='hand',ref='alarm-'+str(i)) for i in range(2)]
        self.ready(session,spells[0],{'W':4,'C':8});self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,spells[0]);self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.cast(session,spells[1]);self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_singular_control_triggers_once_per_token_in_the_same_batch(self):
        session=self.session(304003);engine=session.engine
        source=self.add(engine,'Generic Singular Entry Observer')
        spell=self.add(engine,'Raise the Alarm',zone='hand')
        self.ready(session,spell,{'W':1,'C':1});self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,spell);self.assertEqual(before+1,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_nonqualifying_entry_does_not_consume_limited_aggregate(self):
        session=self.session(304006);engine=session.engine
        source=self.add(engine,'Tocasia\'s Welcome')
        big=self.add(engine,'Generic Aggregate Large Entrant',zone='hand')
        alarm=self.add(engine,'Raise the Alarm',zone='hand')
        self.ready(session,big,{'W':2,'C':8});self.checkpoint(session)
        self.cast(session,big)
        self.assertNotIn('once_per_turn_triggers',session.state.cards[source.object_id].annotations)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,alarm);self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_actual_mass_death_batch_produces_one_source_counter(self):
        session=self.session(304004);engine=session.engine
        source=self.add(engine,'Vengeful Townsfolk')
        for i in range(2):self.add(engine,'Generic Aggregate Victim',ref='victim-'+str(i))
        spell=self.add(engine,'Generic Aggregate Small Sweeper',zone='hand')
        self.ready(session,spell,{});self.checkpoint(session)
        self.cast(session,spell)
        self.assertEqual(1,session.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual(2,sum(c.zone=='graveyard' and c.ref.startswith('victim-') for c in session.state.cards.values()))
        self.replay(session,load=True)

    def test_omitted_aggregation_mutant_is_killed_by_two_token_batch(self):
        import quorune.compiler.fixed_counter_trigger_nodes as nodes
        with patch.object(nodes,'_ONE_OR_MORE_PUBLIC_EVENT_VARIANTS',nodes._ONE_OR_MORE_PUBLIC_EVENT_VARIANTS-{nodes.AGGREGATED_ZONE_VARIANT}):
            session=self.session(304005)
            source=self.add(session.engine,'Generic Aggregate Entry Observer')
            spell=self.add(session.engine,'Raise the Alarm',zone='hand')
            self.ready(session,spell,{'W':1,'C':1});before=len(session.state.players['A'].zones['hand'])
            self.cast(session,spell)
            with self.assertRaises(AssertionError):self.assertEqual(before,len(session.state.players['A'].zones['hand']))


if __name__=='__main__':unittest.main()
