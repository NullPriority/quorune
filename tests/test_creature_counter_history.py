"""Observe actual creature counter placement before each end step."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_history_query_model import (
    CREATURE_COUNTER_PLACEMENT_HISTORY_CAPABILITY, PublicHistoryQuerySpec,
    current_creature_counter_placement_query, matching_public_history_events,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.turn_history_model import CREATURE_COUNTER_PLACEMENT_EVENT, TurnHistory, TurnHistoryEvent
from scripts.build_test_database import build_fixture_database


CARD = 'Lord Jyscal Guado'
PROFILE = 'commander_review'
MODE = 'creature_counter_placement_history_version'


class CreatureCounterHistoryTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / 'creature-counter-history.sqlite3'
        fixtures = ROOT / 'tests/fixtures'
        build_fixture_database([fixtures / name for name in (
            'bound-effect-program-cards.json', 'lord-jyscal-guado-cards.json',
            'turn-counter-placement-history-cards.json', 'state-qualified-zone-cards.json',
            'multikicker-entry-counter-cards.json',
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = 'Generic Bound Commander'
        cls.deck = DeckDefinition('Creature counter history', [
            DeckEntry(commander, 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], [commander])

    def start(self, game, *, active='B', mana=None):
        game.state.config.semantic_policy = 'trusted_only'
        self.prepare(game, active=active, mana=mana)
        self.checkpoint(game)

    def end_step(self, game, seat):
        self.until(game, lambda current: current.state.active_player == seat and current.state.step == 'end_step')

    def activate_placing_ability(self, game, seat, source, target):
        self.priority(game, seat)
        offer, = [row for row in game.packet(f'pilot:{seat}', full=True)['decision']['ctx']['legal']['actions']
                  if row['id'].startswith(f'activate:{source.ref}:')]
        result = game.act(f'pilot:{seat}', {'action_id': offer['id'], 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)

    @staticmethod
    def placements(game, seat):
        return matching_public_history_events(game.state, seat, current_creature_counter_placement_query())

    @staticmethod
    def clues(game):
        return [card for card in game.state.cards.values() if card.is_token and card.zone == 'battlefield'
                and card.controller == 'A' and 'clue' in card.printed_name.casefold()]

    def assert_investigates(self, game, lord, *, active):
        ability, = [item for item in game.state.stack if item.source_object_id == lord.object_id]
        self.assertEqual(('A', active, 'end_step'), (ability.controller, ability.context['player'], ability.context['step']))
        self.resolve_top(game)
        self.assertEqual(1, len(self.clues(game)))

    def test_exact_investigate_requires_the_new_history_authority_and_closed_query(self):
        card = self.db.lookup(CARD, fuzzy=False)
        options = {'capability_registry': self.registry, 'capability_profile': PROFILE}
        ir = compile_oracle_card(card, **options)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = [node for node in ir.faces[0].nodes if node.event == 'step.begin']
        self.assertEqual(card.oracle_text.splitlines()[1], card.oracle_text[node.span.start:node.span.end])
        self.assertIn(CREATURE_COUNTER_PLACEMENT_HISTORY_CAPABILITY, node.capability_dependencies)
        game = self.session(12260301)
        lord = self.add(game.engine, CARD)
        self.assert_closed(game, lord)
        program, = (item for item in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if item.event == "step.begin")
        deps = [key for key in program.capability_dependencies if key != CREATURE_COUNTER_PLACEMENT_HISTORY_CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + CREATURE_COUNTER_PLACEMENT_HISTORY_CAPABILITY,
                      bound['blockers'])
        shape = current_creature_counter_placement_query().to_dict()
        for change in ({'relation': 'opponents'}, {'schema_version': True}, {'types_all': []}):
            with self.assertRaises(ValueError):
                PublicHistoryQuerySpec.from_dict({**shape, **change})
        with patch('quorune.compiler.public_history_conditions._counter_placement_condition', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(card, **options).status)

    def test_other_seat_creatures_and_nonplus_counters_use_the_actual_placing_player(self):
        for mode in ('positive', 'other-player', 'prevented', 'after-beginning'):
            with self.subTest(mode=mode):
                game = self.session(12260310 + len(mode))
                lord = self.add(game.engine, CARD)
                actor = 'B' if mode == 'other-player' else 'A'
                spitter = self.add(game.engine, 'Fume Spitter', seat=actor)
                target = self.add(game.engine, 'Llanowar Elves', seat='C')
                if mode == 'prevented':
                    self.add(game.engine, 'Solemnity', seat='D')
                self.assert_closed(game, lord)
                self.assert_closed(game, spitter)
                self.start(game)
                if mode == 'after-beginning':
                    self.end_step(game, 'B')
                    self.assertEqual([], game.state.stack)
                incarnation = target.logical_object_id
                self.activate_placing_ability(game, actor, spitter, target)
                events = self.placements(game, actor)
                expected = [] if mode == 'prevented' else [(actor, incarnation, 1)]
                self.assertEqual(expected, [(event.actor, event.object_incarnation, event.amount) for event in events])
                self.assertEqual('battlefield' if mode == 'prevented' else 'graveyard',
                                 game.state.cards[target.object_id].zone)
                self.assertEqual('graveyard', game.state.cards[spitter.object_id].zone)
                self.end_step(game, 'B')
                if mode == 'positive':
                    game = self.replay(game, load=True)
                    self.assert_investigates(game, lord, active='B')
                else:
                    self.assertFalse(any(item.source_object_id == lord.object_id for item in game.state.stack))
                    self.assertEqual([], self.clues(game))
                self.replay(game)

    def test_creature_entry_counters_count_and_noncreature_entry_counters_do_not(self):
        from test_multikicker_entry_counters import MultikickerRuntimeTests

        for creature in (False, True):
            with self.subTest(creature=creature):
                game = self.session(12260330 + creature)
                lord = self.add(game.engine, CARD)
                name = 'Mossborn Hydra' if creature else 'Everflowing Chalice'
                source = self.add(game.engine, name, zone='hand')
                self.assert_closed(game, lord)
                self.assert_closed(game, source)
                self.start(game, active='A', mana={'C': 2, 'G': 1} if creature else {'C': 2})
                if creature:
                    self.cast_for(game, 'A', source)
                else:
                    MultikickerRuntimeTests.cast_repeated(self, game, source, 1)
                self.resolve_top(game)
                current = game.state.cards[source.object_id]
                self.assertEqual({'+1/+1' if creature else 'charge': 1}, current.counters)
                self.assertEqual([('A', current.logical_object_id)] if creature else [],
                                 [(event.actor, event.object_incarnation) for event in self.placements(game, 'A')])
                self.end_step(game, 'A')
                if creature:
                    self.assert_investigates(game, lord, active='A')
                else:
                    self.assertEqual([], game.state.stack)
                    self.assertEqual([], self.clues(game))
                turn = game.state.turn_sequence
                self.until(game, lambda current: current.state.turn_sequence > turn)
                self.assertEqual((), self.placements(game, 'A'))
                self.replay(game)

    def test_legacy_marker_absence_and_malformed_placement_fail_closed(self):
        game = self.session(12260340)
        raw = game.state.turn_history.to_dict()
        del raw[MODE]
        legacy = TurnHistory.from_dict(raw)
        self.assertEqual(raw, legacy.to_dict())
        game.state.turn_history = legacy
        self.assertIsNone(self.placements(game, 'A'))
        with self.assertRaises(ValueError):
            TurnHistory.from_dict({**raw, MODE: True})
        with self.assertRaises(ValueError):
            TurnHistoryEvent(CREATURE_COUNTER_PLACEMENT_EVENT, actor='A',
                             object_incarnation='target@1', types=('artifact',), amount=1)
