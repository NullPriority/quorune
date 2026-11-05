"""Native and admission checks for a condition shared by multiple events."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
from scripts.build_test_database import build_fixture_database


class ConditionedMultiEventTriggerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / 'conditioned-triggers.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/qualified-attack-cards.json',
            ROOT / 'tests/fixtures/conditioned-multi-event-cards.json',
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Conditioned subscriptions', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20),
        ], ['Generic Bound Commander'])

    def samurai(self, session):
        return [card for card in session.state.cards.values()
                if card.is_token and card.zone == 'battlefield' and card.controller == 'A']

    def test_naomi_own_entry_and_attack_resolve_even_after_source_leaves(self):
        session = self.session(603_040_101)
        source = self.add(session.engine, 'Naomi, Pillar of Order', zone='hand')
        ring = self.add(session.engine, 'Sol Ring')
        haste = self.add(session.engine, 'Fervor')
        unrelated = self.add(session.engine, 'Universal Automaton', zone='hand')
        response = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
        self.add(session.engine, 'Island', seat='B')
        private = self.add(session.engine, 'Generic Bound Body', seat='C', zone='hand',
                           ref='private-conditioned-event')
        for card in (source, ring, haste, unrelated, response):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 4, 'W': 1, 'B': 1})
        self.checkpoint(session)

        self.cast(session, 'A', source)
        self.resolve_top(session)
        self.assertEqual(1, self.trigger_count(session, source))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, len(self.samurai(session)))
        self.cast(session, 'A', unrelated)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, len(self.samurai(session)))

        self.attack(session, [(source, 'B')])
        self.assertEqual(1, self.trigger_count(session, source))
        self.cast_for(session, 'B', response, [source.ref])
        self.resolve_top(session)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertEqual(1, self.trigger_count(session, source))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        tokens = self.samurai(session)
        self.assertEqual(2, len(tokens))
        for token in tokens:
            self.assertIn('Samurai', session.engine._effective_card_data(token)['type_line'])
            self.assertEqual((2, 2), tuple(session.engine._numeric_stat(token.object_id, stat)
                                           for stat in ('power', 'toughness')))
            self.assertIn('vigilance', session.engine._combat_keywords(token))
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.assertIn(private.ref, json.dumps(session.packet('pilot:C', full=True)))
        self.replay(session)

    def test_naomi_skips_false_entry_and_rechecks_the_attack_condition(self):
        session = self.session(603_040_102)
        source = self.add(session.engine, 'Naomi, Pillar of Order', zone='hand')
        ring = self.add(session.engine, 'Sol Ring', zone='hand')
        self.add(session.engine, 'Sol Ring', seat='C', ref='opposing-sol-ring')
        self.add(session.engine, 'Fervor')
        response = self.add(session.engine, 'Disenchant', seat='B', zone='hand')
        self.add(session.engine, 'Plains', seat='B')
        self.add(session.engine, 'Wastes', seat='B')
        for card in (source, ring, response):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 4, 'W': 1, 'B': 1})
        self.checkpoint(session)

        self.cast(session, 'A', source)
        self.resolve_top(session)
        self.assertEqual(0, self.trigger_count(session, source))
        self.cast(session, 'A', ring)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([], self.samurai(session))
        self.attack(session, [(source, 'B')])
        self.assertEqual(1, self.trigger_count(session, source))

        self.priority(session, 'B')
        offered = self.action(session, 'B', session.state.cards[response.object_id])
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:B', {'action_id': offered['id'],
                                          'targets': [source.ref], 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast_for(session, 'B', response, [ring.ref])
        self.resolve_top(session)
        self.assertEqual('graveyard', session.state.cards[ring.object_id].zone)
        self.assertEqual(1, self.trigger_count(session, source))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([], self.samurai(session))
        self.assertTrue(any(event.code == 'stack.trigger.removed'
                            and event.details.get('reason') == 'intervening_condition_false'
                            for event in session.state.events))
        self.replay(session)

    def test_conditioned_subscriptions_require_the_shared_predicate_and_coverage(self):
        session = self.session(603_040_103)
        source = self.add(session.engine, 'Naomi, Pillar of Order')
        self.assert_closed(session, source)
        program, = session.engine.semantics.programs_for_oracle(source.oracle_id)
        subscriptions = FixedEventSubscriptionSet.from_condition(program.event_condition)
        self.assertIsNotNone(subscriptions)
        self.assertEqual(['permanent.enter', 'creature.attacks'],
                         [subscription.event for subscription in subscriptions.subscriptions])
        self.assertIsNotNone(subscriptions.shared_public_state_condition())
        self.assertEqual(program.event_condition, subscriptions.to_condition())

        mismatched = subscriptions.to_condition()
        mismatched['event_subscriptions'][1]['condition']['all'][-1]['value'] = False
        bad_coverage = tuple(value for value in program.coverage if value != 'intervening_condition')
        dependency = 'trigger.condition.fixed_public_state'
        reduced = tuple(value for value in program.capability_dependencies if value != dependency)
        cases = (
            (replace(program, event_condition=mismatched), 'runtime_event_subscription'),
            (replace(program, coverage=bad_coverage), 'runtime_event_subscription'),
            (replace(program, capability_dependencies=reduced,
                     capability_closure=self.registry.closure(reduced, profile='commander_review').to_dict()),
             'capability:undeclared_runtime_dependency:' + dependency),
        )
        for altered, blocker in cases:
            result = bind_semantic_program_runtime(altered, capability_registry=self.registry,
                                                   profile='commander_review')
            self.assertFalse(result['strict'])
            self.assertTrue(any(value.startswith(blocker) for value in result['blockers']), result)
