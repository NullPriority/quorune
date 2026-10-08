from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from scripts.build_test_database import build_fixture_database, compact_ci_fixture_paths
import test_counted_library_searches as counted_search_tests
from test_fixed_library_searches import card_record
from quorune.carddb import CardDatabase
from quorune.compiler.library_search_templates import fixed_library_search_effect_template
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.library_search_model import FixedCountedLibrarySearchTemplate, search_selector
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.library_search_capability_shapes import (
    fixed_counted_library_search_node_capabilities,
    fixed_library_search_node_capabilities,
)
from quorune.selection.searching import HiddenSearchOwnerMixin
from quorune.session import CommanderSession


PERMANENT_TYPES = ['artifact', 'battle', 'creature', 'enchantment', 'land', 'planeswalker']


class LibrarySearchQualifierCompilerTests(unittest.TestCase):
    def test_color_permanent_predicates_preserve_meaning_at_every_destination(self):
        for color, symbol in (('white', 'W'), ('blue', 'U'), ('black', 'B'), ('red', 'R'), ('green', 'G')):
            for destination in ('into your hand', 'into your graveyard', 'onto the battlefield'):
                text = f'Search your library for a {color} permanent card, put it {destination}, then shuffle.'
                with self.subTest(text=text):
                    template = fixed_library_search_effect_template(text)
                    self.assertIsNotNone(template)
                    self.assertEqual({'types_any': PERMANENT_TYPES, 'colors_any': [symbol]}, template.compiled()[1][0]['selector'])
                    if color == 'green':
                        ir = compile_oracle_card(card_record(text), capability_registry=load_default_capability_registry(), capability_profile='commander_review')
                        self.assertEqual('exact', ir.status, ir.material_residuals)

    def test_unknown_and_unrepresented_qualifiers_remain_material_residuals(self):
        for quality in ('nonland permanent', 'legendary nonland permanent', 'imaginary permanent', 'legendary imaginary permanent', 'snow permanent', 'green Goblin permanent', 'legendary green permanent'):
            for destination in ('into your hand', 'into your graveyard', 'onto the battlefield'):
                text = f'Search your library for a {quality} card, put it {destination}, then shuffle.'
                with self.subTest(text=text):
                    self.assertIsNone(fixed_library_search_effect_template(text))
                    if destination == 'into your graveyard':
                        ir = compile_oracle_card(card_record(text), capability_registry=load_default_capability_registry(), capability_profile='commander_review')
                        self.assertNotEqual('exact', ir.status)
                        self.assertTrue(ir.material_residuals)

    def test_known_subtypes_and_artifact_controls_keep_their_fields(self):
        controls = {
            'green': {'colors_any': ['G']},
            'artifact': {'types': ['artifact']},
            'Goblin permanent': {'types_any': PERMANENT_TYPES, 'subtypes_any': ['goblin']},
            'legendary Goblin permanent': {'types_any': PERMANENT_TYPES, 'subtypes_any': ['goblin'], 'supertypes': ['legendary']},
            'permanent': {'types_any': PERMANENT_TYPES},
        }
        for quality, expected in controls.items():
            article = 'an' if quality == 'artifact' else 'a'
            template = fixed_library_search_effect_template(f'Search your library for {article} {quality} card, put it into your graveyard, then shuffle.')
            self.assertEqual(expected, template.compiled()[1][0]['selector'])

    def test_codec_shapes_and_matcher_reject_lost_exclusions_and_fake_subtypes(self):
        with self.assertRaisesRegex(ValueError, 'closed selector codec'):
            search_selector(ObjectQuerySpec(types_any=tuple(PERMANENT_TYPES), excluded_types=('land',)))
        for selector in ({'subtypes_any': ['green']}, {'subtypes_any': ['nonland']}, {'subtypes_any': ['imaginary']}, {'colors_any': ['green']}, {'excluded_types': ['land']}):
            with self.subTest(selector=selector):
                with self.assertRaises(ValueError):
                    FixedCountedLibrarySearchTemplate(count=1, optional_count=False, selector=FrozenMap(selector), destination='graveyard', reveal=False)
                base = fixed_library_search_effect_template('Search your library for a permanent card, put it into your graveyard, then shuffle.')
                effect = base.effect()
                effect['selector'] = selector
                self.assertEqual((), fixed_counted_library_search_node_capabilities(effects=(effect,), target_schema=None, mechanic_ids=base.compiled()[3]))
                legacy = fixed_library_search_effect_template('Search your library for a permanent card, put it onto the battlefield, then shuffle.')
                effect = copy.deepcopy(legacy.compiled()[1][0])
                effect['selector'] = {'types_any': PERMANENT_TYPES, **selector}
                self.assertEqual((), fixed_library_search_node_capabilities(effects=(effect,), target_schema=None, mechanic_ids=legacy.compiled()[3]))
        with self.assertRaisesRegex(GameRuleError, 'type exclusions are unsupported'):
            HiddenSearchOwnerMixin._search_candidate_matches(object(), None, {'excluded_types': ['land']})
        with self.assertRaisesRegex(GameRuleError, 'type exclusions are unsupported'):
            HiddenSearchOwnerMixin._semantic_search_options(object(), 'A', {'selector': {'excluded_types': ['land']}})
        for word in ('green', 'nonland', 'imaginary'):
            with self.assertRaisesRegex(GameRuleError, 'closed vocabulary'):
                HiddenSearchOwnerMixin._search_candidate_matches(object(), None, {'subtypes_any': [word]})
        for malformed in ('goblin', None, [[]]):
            with self.assertRaisesRegex(GameRuleError, 'closed vocabulary'):
                HiddenSearchOwnerMixin._search_candidate_matches(object(), None, {'subtypes_any': malformed})

    def test_color_permanent_codec_mutation_is_detected(self):
        from quorune import library_search_model
        serialize = library_search_model.search_selector
        text = 'Search your library for a green permanent card, put it into your graveyard, then shuffle.'
        expected = {'types_any': PERMANENT_TYPES, 'colors_any': ['G']}

        def erase_color(query):
            selector = serialize(query)
            selector.pop('colors_any', None)
            return selector

        with patch('quorune.compiler.library_search_templates.search_selector', side_effect=erase_color):
            mutant = fixed_library_search_effect_template(text)
            with self.assertRaises(AssertionError):
                self.assertEqual(expected, mutant.effect()['selector'])

    def test_color_permanent_round_trip_and_capability_shape_stay_closed(self):
        for destination in ('into your graveyard', 'then shuffle and put it on top'):
            text = (
                'Search your library for a green permanent card, then shuffle and put it on top.'
                if destination.startswith('then') else
                'Search your library for a green permanent card, put it into your graveyard, then shuffle.'
            )
            template = fixed_library_search_effect_template(text)
            self.assertEqual(template, FixedCountedLibrarySearchTemplate.from_effect(template.effect()))
            self.assertEqual(('library.search.fixed_counted',),
                fixed_counted_library_search_node_capabilities(effects=(template.effect(),),
                    target_schema=None, mechanic_ids=template.compiled()[3]))


class LibrarySearchQualifierGameplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'qualifiers.sqlite3'
        build_fixture_database(compact_ci_fixture_paths(root=ROOT), path)
        cls.db = CardDatabase(path)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(ROOT / 'examples/mishra-eminent-one.txt', commander='Mishra, Eminent One')
        cls.zimone = loader.load(ROOT / 'examples/zimone-and-dina.txt', commander='Zimone and Dina')

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def case(self):
        case = counted_search_tests.CountedLibrarySearchTests('test_counted_search_schema_dependencies_and_mutations_fail_closed')
        case.db, case.mishra, case.zimone = self.db, self.mishra, self.zimone
        return case

    def test_qualified_search_publishes_accepts_rejects_and_replays_actual_actions(self):
        # CR 110.4a/701.23b: a green permanent includes a green noncreature
        # permanent, and excludes green nonpermanent cards. Artifact alone
        # includes Artifact Land; stated quality permits failure to find.
        candidates = ('Runeclaw Bear', 'Rancor', 'Sol Ring', 'Giant Growth', 'Cultivate', 'Forest', 'Tree of Tales', 'Mogg Fanatic', 'Isamaru, Hound of Konda', 'Flagstones of Trokair')
        expected = {
            'Green Permanent Search Fixture': {'Runeclaw Bear', 'Rancor'},
            'Green Card Search Fixture': {'Runeclaw Bear', 'Rancor', 'Giant Growth', 'Cultivate'},
            'Artifact Card Search Fixture': {'Sol Ring', 'Tree of Tales'},
            'Goblin Permanent Search Fixture': {'Mogg Fanatic'},
            'Permanent Search Fixture': set(candidates) - {'Giant Growth', 'Cultivate'},
        }
        for index, (spell_name, eligible) in enumerate(expected.items()):
            with self.subTest(spell=spell_name):
                case = self.case()
                session, spell = case.setup_cast(spell_name, 701239100 + index)
                for object_id in list(session.state.players['A'].zones['library']):
                    session.engine.move_card(object_id, 'exile', semantic_events=False, log=False)
                cards = {name: case.card(session, name, 'library') for name in candidates}
                case.offered_cast(session, spell)
                decision = case.reach_search(session)
                schema = decision['legal_actions'][0]['choice_schema']
                self.assertEqual(eligible, {name for name, card in cards.items() if card.ref in schema['legal_refs']})
                self.assertTrue(schema['rules_may_fail_to_find'])
                self.assertEqual(0, schema['minimum'])
                for seat in ('B', 'C', 'D'):
                    packet = json.dumps(session.packet(f'pilot:{seat}', full=True))
                    self.assertTrue(all(card.ref not in packet for card in cards.values()))
                chosen_name = 'Tree of Tales' if spell_name == 'Artifact Card Search Fixture' else sorted(eligible)[0]
                chosen = cards[chosen_name]
                invalid = cards[sorted(set(candidates) - eligible)[0]]
                before = authoritative_state_hash(session.state)
                for principal, selected in (('pilot:A', invalid), ('pilot:B', chosen)):
                    rejected = session.act(principal, {'action_id': 'choose', 'search_cards': [selected.ref]})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                accepted = session.act('pilot:A', {'action_id': 'choose', 'search_cards': [chosen.ref]})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual('graveyard', session.state.cards[chosen.object_id].zone)
                case.assert_replay(session)

    def test_no_eligible_result_continues_and_replays(self):
        case = self.case()
        session, spell = case.setup_cast('Green Permanent Search Fixture', 701239110)
        for object_id in list(session.state.players['A'].zones['library']):
            session.engine.move_card(object_id, 'exile', semantic_events=False, log=False)
        instant = case.card(session, 'Giant Growth', 'library')
        case.offered_cast(session, spell)
        schema = case.reach_search(session)['legal_actions'][0]['choice_schema']
        self.assertEqual([], schema['legal_refs'])
        self.assertEqual(0, schema['maximum'])
        accepted = session.act('pilot:A', {'action_id': 'choose', 'search_cards': []})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('library', instant.zone)
        self.assertNotEqual('semantic.search', session.state.pending_decision.kind)
        case.assert_replay(session)

    def test_qualified_commander_replacement_survives_reload_and_replays(self):
        # A repaired green permanent predicate reaches the existing independent
        # Commander replacement path; pending state stays principal-private.
        case = self.case()
        session, spell = case.setup_cast('Green Permanent Hand Search Fixture', 903239111)
        commander = case.card(session, 'Llanowar Elves', 'library')
        for card in session.state.cards.values():
            if card.owner == 'A' and card.is_commander:
                card.is_commander = False
                card.commander_designation_id = None
        commander.is_commander = True
        commander.commander_designation_id = 'commander:' + commander.object_id
        session.state.commander_oracle_ids['A'] = [commander.oracle_id]
        case.offered_cast(session, spell)
        schema = case.reach_search(session)['legal_actions'][0]['choice_schema']
        self.assertIn(commander.ref, schema['legal_refs'])
        accepted = session.act('pilot:A', {'action_id': 'choose', 'search_cards': [commander.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('replacement.order', session.state.pending_decision.kind)
        self.assertEqual('library', commander.zone)
        for seat in ('B', 'C', 'D'):
            self.assertNotIn(commander.ref, json.dumps(session.packet(f'pilot:{seat}', full=True)))
        options = session.packet('pilot:A', full=True)['decision']['ctx']['options']
        decline = next(option['id'] for option in options if option['id'].startswith('decline:'))
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:B', {'action_id': 'choose', 'choices': {'replacement': decline}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            session = CommanderSession.load(self.db, directory)
        self.assertEqual('replacement.order', session.state.pending_decision.kind)
        accepted = session.act('pilot:A', {'action_id': 'choose', 'choices': {'replacement': decline}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('hand', session.state.cards[commander.object_id].zone)
        case.assert_replay(session)
