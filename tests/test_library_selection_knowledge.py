"""Library reordering preserves identities without exposing hidden positions."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_selection_knowledge_model import remainder_knowledge_viewers
from quorune.model import GameConfig, GameState
from quorune.record import authoritative_state_hash
from quorune.record_state_provenance import format_state_versions, validate_state_versions
from quorune.rules.capabilities import load_default_capability_registry
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as bound_witnesses


class LibrarySelectionKnowledgeOwnerTests(unittest.TestCase):
    def test_hidden_permutations_and_legitimate_singletons_have_distinct_viewers(self):
        for version in (None, 1):
            for public in (False, True):
                for order in ('chosen', 'random'):
                    for count in (0, 1, 3):
                        with self.subTest(version=version, public=public, order=order, count=count):
                            expected = None if public else ('A',)
                            if version == 1 and count > 1:
                                expected = () if order == 'random' else ('A',)
                            self.assertEqual(expected, remainder_knowledge_viewers(
                                version=version, player='A', looked_are_public=public,
                                order=order, count=count))
        good = dict(version=1, player='A', looked_are_public=True, order='random', count=2)
        for field, value in (('version', True), ('version', 0), ('version', 2),
                             ('player', ''), ('looked_are_public', 1), ('order', 'shuffle'),
                             ('count', True), ('count', -1)):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):remainder_knowledge_viewers(**{**good, field:value})

    def test_current_and_absent_state_versions_require_matching_record_provenance(self):
        state = GameState(game_id='library-knowledge-version', config=GameConfig(), players={}, cards={},
            deck_names={}, commander_oracle_ids={}, turn_order=[], current_turn=None, last_normal_turn_player=None)
        wire = state.to_dict()
        self.assertEqual(1, wire['library_selection_knowledge_version'])
        self.assertEqual(wire, GameState.from_dict(wire).to_dict())
        modern = {'format':format_state_versions(state)}
        validate_state_versions(modern, state)
        old = {key:value for key,value in wire.items() if key != 'library_selection_knowledge_version'}
        legacy = GameState.from_dict(old)
        self.assertIsNone(legacy.library_selection_knowledge_version)
        self.assertEqual(old, legacy.to_dict())
        historical = {'format':format_state_versions(legacy)}
        self.assertNotIn('library_selection_knowledge_version', historical['format'])
        validate_state_versions(historical, legacy)
        for manifest, checkpoint in ((modern, legacy), (historical, state)):
            with self.assertRaises(ValueError):validate_state_versions(manifest, checkpoint)
        for bad in (True, False, 0, 2, '1', {}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):GameState.from_dict({**wire, 'library_selection_knowledge_version':bad})
                with self.assertRaises(ValueError):validate_state_versions(
                    {'format':{**modern['format'], 'library_selection_knowledge_version':bad}}, state)


class LibrarySelectionKnowledgeNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = bound_witnesses.BoundEffectProgramRuntimeTests.session
    add = bound_witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint = bound_witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve = bound_witnesses.BoundEffectProgramRuntimeTests.resolve
    replay = bound_witnesses.BoundEffectProgramRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'library-knowledge.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/library-selection-knowledge-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Library knowledge', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def prepare_cast(self, name, library, *, current=True):
        session = self.session(679001)
        engine = session.engine
        if not current:engine.state.library_selection_knowledge_version = None
        for oid in tuple(session.state.players['A'].zones['library']):
            engine.move_card(oid, 'exile', reason='Prepare short library')
        cards = [self.add(engine, card_name, zone='library', ref='LIBRARY_KNOWLEDGE_CARD_'+str(index))
            for index,card_name in enumerate(library)]
        spell = self.add(engine, name, zone='hand', ref='LIBRARY_KNOWLEDGE_SPELL')
        private = self.add(engine, 'Anticipate', seat='B', zone='hand', ref='UNRELATED_PRIVATE_HAND')
        program = compile_best_available_card_program(self.db, self.db.by_oracle_id(spell.oracle_id),
            semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
        self.assertEqual('capability_closed', binding['trust_basis'])
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = 'A'
        engine.state.started = True
        engine.state.phase = 'precombat_main'
        engine.state.step = 'main'
        engine.state.phase_index = TURN_STEPS.index(('precombat_main', 'main'))
        engine.state.players['A'].mana_pool.update(G=4, U=4, C=4)
        engine._grant_priority('A')
        engine.pump()
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row.get('card') == spell.ref)
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id':action['id'], 'targets':[], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        return session, cards, private

    def known_top(self, session, viewer):
        return [row['id'] for row in session.packet('pilot:'+viewer, full=True)['state']['players']['A'].get('known_top', [])]

    def test_original_random_remainder_hides_positions_from_every_player_after_pending_load(self):
        self.random_remainder(current=True)

    def test_historical_absence_retains_old_knowledge_and_replays_without_rewriting_provenance(self):
        self.random_remainder(current=False)

    def random_remainder(self, *, current):
        session, cards, private = self.prepare_cast('Commune with Spirits',
            ['Grizzly Bears', 'Ornithopter', 'Elvish Mystic'], current=current)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        for viewer in 'BCD':
            packet = str(session.packet('pilot:'+viewer, full=True))
            for card in cards:self.assertNotIn(card.ref, packet)
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        for principal, selected in (('pilot:B', []), ('pilot:A', [cards[0].ref]),
                                     ('pilot:A', [cards[0].ref, cards[0].ref])):
            self.assertFalse(session.act(principal, {'action_id':'choose', 'cards':selected}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        shuffle_count = session.state.players['A'].stats.get('shuffle_count', 0)
        if current:
            from quorune.errors import GameRuleError
            with patch('quorune.library_selection_knowledge_model.remainder_knowledge_viewers',
                       side_effect=GameRuleError('Injected failure after partition commit')):
                failed = session.act('pilot:A', {'action_id':'choose', 'cards':[]})
            self.assertFalse(failed.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {'action_id':'choose', 'cards':[]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        actual = [session.state.cards[oid].ref for oid in reversed(session.state.players['A'].zones['library'])]
        self.assertCountEqual([card.ref for card in cards], actual)
        self.assertEqual([] if current else actual, self.known_top(session, 'A'))
        for viewer in 'BCD':self.assertEqual([], self.known_top(session, viewer))
        for card in cards:
            live = session.state.cards[card.object_id]
            self.assertEqual([] if current else ['A'], live.known_to)
            self.assertEqual([], live.revealed_to)
        self.assertEqual(shuffle_count, session.state.players['A'].stats.get('shuffle_count', 0))
        self.assertEqual(['B'], session.state.cards[private.object_id].known_to)
        self.assertNotIn(private.ref, str(session.packet('pilot:A', full=True)))
        self.assertEqual(current, 'library_selection_knowledge_version' in session.state.to_dict())
        self.replay(session, load=True)

    def test_original_public_reveal_keeps_chosen_bottom_order_private(self):
        session, cards, _ = self.prepare_cast('Lair Delve', ['Anticipate', 'Commune with Spirits'])
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        for viewer in 'ABCD':self.assertEqual([cards[1].ref, cards[0].ref], self.known_top(session, viewer))
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        bottom = [cards[1].ref, cards[0].ref]
        for principal, ordering in (('pilot:C', bottom), ('pilot:A', [cards[0].ref, cards[0].ref])):
            self.assertFalse(session.act(principal, {'action_id':'choose', 'cards':{'hand':[], 'bottom':ordering}}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A', {'action_id':'choose', 'cards':{'hand':[], 'bottom':bottom}}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(list(reversed(bottom)), self.known_top(session, 'A'))
        for viewer in 'BCD':self.assertEqual([], self.known_top(session, viewer))
        for card in cards:
            self.assertEqual(['A'], session.state.cards[card.object_id].known_to)
            self.assertEqual([], session.state.cards[card.object_id].revealed_to)
        self.replay(session, load=True)

    def test_original_private_chosen_order_retains_unaffected_and_selected_knowledge(self):
        session, cards, _ = self.prepare_cast('Anticipate',
            ['Commune with Spirits', 'Grizzly Bears', 'Ornithopter', 'Elvish Mystic'])
        outside, selected, first, second = cards
        outside.known_to = list('ABCD')
        outside.revealed_to = list('ABCD')
        # Re-checkpoint before resolving: the outside card's prior knowledge is an input fact.
        self.checkpoint(session)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        bottom = [first.ref, second.ref]
        self.assertTrue(session.act('pilot:A', {'action_id':'choose',
            'cards':{'hand':[selected.ref], 'bottom':bottom}}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([outside.ref, *reversed(bottom)], self.known_top(session, 'A'))
        for viewer in 'BCD':self.assertEqual([outside.ref], self.known_top(session, viewer))
        self.assertEqual('hand', session.state.cards[selected.object_id].zone)
        self.assertEqual(['A'], session.state.cards[selected.object_id].known_to)
        self.assertEqual(list('ABCD'), session.state.cards[outside.object_id].known_to)
        self.assertEqual(list('ABCD'), session.state.cards[outside.object_id].revealed_to)
        self.replay(session, load=True)

    def test_original_public_singleton_retains_revealed_identity_and_selected_hand_card(self):
        session, cards, _ = self.prepare_cast('Lair Delve', ['Anticipate', 'Grizzly Bears'])
        self.assertIsNone(self.resolve(session))
        for viewer in 'ABCD':self.assertEqual([cards[0].ref], self.known_top(session, viewer))
        self.assertEqual('hand', session.state.cards[cards[1].object_id].zone)
        self.assertEqual(list('ABCD'), session.state.cards[cards[1].object_id].known_to)
        self.replay(session, load=True)
