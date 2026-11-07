"""Sealed public upkeep hand counts through response, departure, and replay."""

import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.model import StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.scheduled_player import (
    UPKEEP_HAND_COUNT_CAPABILITY,
    UPKEEP_HAND_COUNT_LKI,
    UpkeepHandDeparture,
    UpkeepParticipantHandCondition,
    upkeep_hand_condition_holds,
)
from quorune.trigger_batches import PendingTriggerItem, create_pending_trigger_batch
from quorune.trigger_processing import pin_upkeep_hand_at_departure
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_scheduled_player_triggers as scheduled


class UpkeepParticipantHandConditionTests(unittest.TestCase):
    def test_upkeep_hand_condition_pins_stack_and_pending_contexts_atomically(self):
        subject = SimpleNamespace(in_game=True, zones={'hand': ['private-one']})
        state = SimpleNamespace(step='upkeep', active_player='B', players={'B': subject},
                                stack=[], pending_trigger_batches=[])
        program = SimpleNamespace(capability_dependencies=(UPKEEP_HAND_COUNT_CAPABILITY,))
        host = SimpleNamespace(state=state, semantics={'hand': program})
        context = {'event': 'step.begin', 'step': 'upkeep', 'player': 'B'}
        item = StackItem('first-id', 'first', 'triggered_ability', 'A', 'Upkeep',
                         semantic_key='hand', context=dict(context))
        other = StackItem('second-id', 'second', 'triggered_ability', 'D', 'Upkeep',
                          semantic_key='hand', context=dict(context))
        state.stack.append(item)

        def batch(trigger):
            return create_pending_trigger_batch(
                batch_id='batch-id', ref='batch', items=[PendingTriggerItem.from_dict(trigger.to_dict())],
                apnap_order=('B', 'C', 'D', 'A'), turn_sequence=1, priority_epoch=1)

        malformed = replace(other, context={**context, UPKEEP_HAND_COUNT_LKI: {}})
        state.pending_trigger_batches.append(batch(malformed))
        with self.assertRaisesRegex(GameRuleError, 'sealed before departure'):
            pin_upkeep_hand_at_departure(host, ('B',))
        self.assertEqual(context, item.context)

        state.pending_trigger_batches[0] = batch(other)
        pin_upkeep_hand_at_departure(host, ('B',))
        expected = {'schema_version': 1, 'seat': 'B', 'count': 1}
        self.assertEqual(expected, item.context[UPKEEP_HAND_COUNT_LKI])
        pending = state.pending_trigger_batches[0].groups[0].items[0].to_dict()['context']
        self.assertEqual(expected, pending[UPKEEP_HAND_COUNT_LKI])

        subject.in_game = False
        subject.zones['hand'].clear()
        empty = UpkeepParticipantHandCondition('at_most', 0)
        self.assertFalse(upkeep_hand_condition_holds(state, pending, empty))
        with patch.object(UpkeepHandDeparture, 'from_dict', return_value=UpkeepHandDeparture('B', 0)):
            with self.assertRaises(AssertionError):
                self.assertFalse(upkeep_hand_condition_holds(state, pending, empty))
        for corrupt in (None, {**expected, 'seat': 'C'}, {**expected, 'count': True},
                        {**expected, 'schema_version': 2}, {**expected, 'cards': ['private-one']}):
            with self.assertRaises(GameRuleError):
                upkeep_hand_condition_holds(state, {**pending, UPKEEP_HAND_COUNT_LKI: corrupt}, empty)

    def test_upkeep_hand_compiler_requires_scope_and_condition_authority(self):
        registry = load_default_capability_registry()
        cases = (
            ('no', 'at_most', 0),
            ('two or fewer', 'at_most', 2),
            ('seven or more', 'at_least', 7),
        )
        for wording, comparison, threshold in cases:
            with self.subTest(wording=wording):
                text = ("At the beginning of each opponent's upkeep, if that player has "
                        f'{wording} cards in hand, they lose 2 life.')
                ir = compile_oracle_card(record(text, 'Enchantment'), capability_registry=registry,
                                         capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(UpkeepParticipantHandCondition(comparison, threshold).to_dict(),
                                 node.event_condition['all'][-1]['condition'])
                self.assertIn(UPKEEP_HAND_COUNT_CAPABILITY, node.capability_dependencies)

        good = "At the beginning of each player's upkeep, if that player has no cards in hand, draw a card."
        for text in (good.replace('upkeep', 'draw step'), good.replace("each player's", 'your'),
                     good.replace('no cards', 'X or fewer cards')):
            self.assertNotEqual('exact', compile_oracle_card(record(text, 'Enchantment'),
                capability_registry=registry, capability_profile='commander_review').status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row = next(item for item in raw['capabilities'] if item['id'] == UPKEEP_HAND_COUNT_CAPABILITY)
        row.update(status='blocked', blockers=['Upkeep hand capability mutation'])
        altered = CapabilityRegistry(raw)
        altered.mark_evidence_verified(registry.evidence_fingerprint)
        blocked = compile_oracle_card(record(good, 'Enchantment'), capability_registry=altered,
                                      capability_profile='commander_review')
        self.assertNotEqual('exact', blocked.status)
        self.assertTrue(any(UPKEEP_HAND_COUNT_CAPABILITY in blocker
                            for residual in blocked.material_residuals for blocker in residual.blockers))


class UpkeepParticipantHandActionTests(InheritedNativeFixture, unittest.TestCase):
    enter_upkeep = scheduled.ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / 'upkeep-hand-count.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/draw-rules-cards.json',
            ROOT / 'tests/fixtures/upkeep-participant-hand-cards.json',
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Upkeep hand-count originals', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20),
        ], ['Generic Bound Commander'])

    def test_original_asylum_visitor_uses_current_and_last_known_upkeep_hand_count(self):
        cases = ((False, True), (True, False), (True, True))
        for index, (draw_response, depart) in enumerate(cases):
            with self.subTest(draw_response=draw_response, depart=depart):
                session = self.session(800_404_101 + index)
                visitor = self.add(session.engine, 'Asylum Visitor')
                stone = self.add(session.engine, 'Mind Stone', seat='B')
                self.add(session.engine, 'Generic Bound Plains', seat='B', ref='upkeep-response-mana')
                self.assert_closed(session, visitor)
                self.assert_closed(session, stone)
                for oid in tuple(session.state.players['B'].zones['hand']):
                    session.engine.move_card(oid, 'library', log=False)
                before_a = len(session.state.players['A'].zones['hand'])
                self.assertEqual([], session.state.players['B'].zones['hand'])
                self.enter_upkeep(session, 'B')
                trigger, = [item for item in session.state.stack if item.source_object_id == visitor.object_id]
                self.assertEqual(('A', 'B'), (trigger.controller, trigger.context['player']))

                private = None
                if draw_response:
                    actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
                    action_id = f'activate:{stone.ref}:ab2'
                    self.assertIn(action_id, {row['id'] for row in actions})
                    command = {'action_id': action_id, 'pay': 'auto'}
                    before = authoritative_state_hash(session.state)
                    self.assertFalse(session.act('pilot:A', command).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    accepted = session.act('pilot:B', command)
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.resolve_top(session)
                    drawn, = session.state.players['B'].zones['hand']
                    private = session.state.cards[drawn].ref
                    self.assertIn(private, json.dumps(session.packet('pilot:B', full=True)))
                if depart:
                    self.priority(session, 'B')
                    accepted = session.act('pilot:B', {'action_id': 'concede', 'choices': {'confirm_concede': True}})
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.assertFalse(session.state.players['B'].in_game)
                    self.assertEqual([], session.state.players['B'].zones['hand'])
                    pending, = [item for item in session.state.stack if item.ref == trigger.ref]
                    self.assertEqual({'schema_version': 1, 'seat': 'B', 'count': int(draw_response)},
                                     pending.context[UPKEEP_HAND_COUNT_LKI])
                    if draw_response:
                        session = self.replay(session, load=True)
                if private is not None:
                    for viewer in 'ACD':
                        self.assertNotIn(private, json.dumps(session.packet('pilot:' + viewer, full=True)))

                self.assertIsNone(self.resolve(session))
                self.assertEqual(before_a + int(not draw_response), len(session.state.players['A'].zones['hand']))
                self.assertEqual(40 - int(not draw_response), session.state.players['A'].life)
                self.replay(session)

    def test_original_netsuke_counts_only_the_upkeep_participant_and_damages_them(self):
        session = self.session(800_404_109)
        visitor = self.add(session.engine, 'Asylum Visitor')
        netsuke = self.add(session.engine, 'Ebony Owl Netsuke')
        for card in (visitor, netsuke):
            self.assert_closed(session, card)
        for oid in tuple(session.state.players['A'].zones['hand']):
            session.engine.move_card(oid, 'library', log=False)
        self.assertEqual(7, len(session.state.players['B'].zones['hand']))
        self.enter_upkeep(session, 'B')
        self.assertEqual(0, self.trigger_count(session, visitor))
        self.assertEqual(1, self.trigger_count(session, netsuke))
        self.assertIsNone(self.resolve(session))
        self.assertEqual({'A': 40, 'B': 36, 'C': 40, 'D': 40},
                         {seat: player.life for seat, player in session.state.players.items()})
        self.replay(session)

    def test_upkeep_hand_runtime_requires_the_scope_and_capability(self):
        session = self.session(800_404_110)
        visitor = self.add(session.engine, 'Asylum Visitor')
        self.assert_closed(session, visitor)
        program = next(program for program in session.engine.semantics.programs_for_oracle(visitor.oracle_id)
                       if UPKEEP_HAND_COUNT_CAPABILITY in program.capability_dependencies)
        dependencies = tuple(value for value in program.capability_dependencies if value != UPKEEP_HAND_COUNT_CAPABILITY)
        bad_scope = deepcopy(program.event_condition)
        bad_scope['all'][0]['all'][0]['value'] = 'draw'
        malformed = deepcopy(program.event_condition)
        malformed['all'][-1]['condition']['threshold'] = True
        cases = (
            (replace(program, event_condition=bad_scope), 'runtime_event_subscriptions:invalid:'),
            (replace(program, event_condition=malformed), 'runtime_event_subscriptions:invalid:'),
            (replace(program, capability_dependencies=dependencies,
                     capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict()),
             'capability:undeclared_runtime_dependency:' + UPKEEP_HAND_COUNT_CAPABILITY),
        )
        for candidate, prefix in cases:
            binding = bind_semantic_program_runtime(candidate, capability_registry=self.registry,
                                                    profile='commander_review')
            self.assertFalse(binding['strict'])
            self.assertTrue(any(value.startswith(prefix) for value in binding['blockers']), binding)
