"""Fixed card-type receiver lists retain live, overlapping public membership."""
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as seat_actions
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.compiler.public_state_queries import fixed_characteristic_battlefield_query_subject
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_query import ObjectQueryResult, query_objects
from quorune.object_query_union import RECEIVER_UNION_CAPABILITY
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


TEXT = 'Artifacts, creatures, enchantments, and lands you control'
FIXTURE = ROOT / 'tests/fixtures/card-type-receiver-union-cards.json'


class CardTypeReceiverUnionModelTests(unittest.TestCase):
    def test_three_and_four_card_types_keep_scope_and_one_overlap_member(self):
        relation, query, other = fixed_characteristic_battlefield_query_subject(TEXT)
        self.assertEqual(('source_controller', False), (relation, other))
        self.assertEqual(('artifact', 'creature', 'enchantment', 'land'),
                         tuple(part.types_all[0] for part in query.any_of))
        def row(ref, types, controller='A', owner='A'):
            return ObjectQueryResult(object_id=ref, ref=ref, printed_name='Public receiver',
                owner=owner, controller=controller, zone='battlefield', types=types)
        overlap = row('overlap', ('artifact', 'creature'), owner='B')
        samples = (overlap, row('enchantment', ('enchantment',)), row('land', ('land',)),
                   row('foreign', ('creature',), 'B'), row('walker', ('planeswalker',)),
                   row('battle', ('battle',)))
        controlled = replace(query, controller='A')
        self.assertEqual(('overlap', 'enchantment', 'land'),
                         tuple(value.ref for value in query_objects(samples, controlled)))
        self.assertEqual((), query_objects((replace(overlap, controller='B'),), controlled))
        self.assertEqual((), query_objects((replace(overlap, types=('planeswalker',)),), controlled))
        self.assertEqual((overlap,), query_objects((overlap,), controlled))
        relation, triple, other = fixed_characteristic_battlefield_query_subject(
            'Other artifacts, battles, and planeswalkers')
        self.assertEqual(('any', True, 3), (relation, other, len(triple.any_of)))

    def test_lists_reject_duplicate_unowned_hidden_and_mixed_scope_branches(self):
        for text in ('Artifacts, creatures, and artifacts you control',
                'Artifacts, creatures, enchantments, lands, and planeswalkers you control',
                'Artifacts, creatures, and objects you control',
                'Artifacts, creatures with flying, and lands you control',
                'Artifacts, creatures in your hand, and lands you control',
                'Artifacts you control, creatures, and lands your opponents control',
                'Artifacts, creatures, or lands you control'):
            with self.subTest(text=text):
                self.assertIsNone(fixed_characteristic_battlefield_query_subject(text))


class CardTypeReceiverUnionNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject
    finish = seat_actions.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'type-receivers.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Type receiver witnesses', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_elspeth_original_is_closed_and_unrepresented_original_siblings_remain_material(self):
        semantics = SemanticRegistry()
        cards = [self.db.lookup(name) for name in ('Elspeth, Knight-Errant', 'Fountain Watch',
                                                   'Callaphe, Beloved of the Sea')]
        register_generated_programs(self.db, semantics, cards, trust_level='provisional',
            capability_registry=self.registry, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(name=card.name):
                program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry,
                                                     profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
                self.assertEqual('capability_closed', binding['trust_basis'])
        ir = compile_oracle_card(cards[0], capability_registry=self.registry,
                                 capability_profile='commander_review')
        child, = (n for n in ir.faces[0].nodes if n.active_zone == 'command')
        self.assertIn(RECEIVER_UNION_CAPABILITY, child.capability_dependencies)
        asylum = compile_oracle_card(self.db.lookup('Spiritual Asylum'),
            capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', asylum.status, asylum.material_residuals)
        grant, attack = asylum.faces[0].nodes
        self.assertEqual(('characteristics.evaluate', 'creature.attacks'), (grant.event, attack.event))
        self.assertEqual('self_zone_move', attack.effects[0]['op'])
        sorin = compile_oracle_card(self.db.lookup('Sorin, Vengeful Bloodlord'),
            capability_registry=self.registry, capability_profile='commander_review')
        self.assertNotEqual('exact', sorin.status)
        self.assertTrue(sorin.material_residuals)

    def test_original_emblem_survives_creator_and_tracks_controller_new_entries_and_destruction(self):
        s = self.session(114613)
        creator = self.add(s.engine, 'Elspeth, Knight-Errant')
        creator.counters['loyalty'] = 8
        overlap = self.add(s.engine, 'Ornithopter'); overlap.owner = 'B'
        artifact = self.add(s.engine, 'Fountain of Youth')
        enchantment = self.add(s.engine, 'Glorious Anthem')
        land = self.add(s.engine, 'Generic Bound Plains', ref='type-union-land')
        excluded = self.add(s.engine, 'Gideon, Ally of Zendikar'); excluded.counters['loyalty'] = 4
        foreign = self.add(s.engine, 'Grizzly Bears', seat='B'); foreign.owner = 'A'
        entrant = self.add(s.engine, 'Memnite', zone='hand')
        donation = self.add(s.engine, 'Donate', zone='hand')
        destruction = self.add(s.engine, 'Planar Cleansing', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        for card in (creator, entrant, donation, destruction):
            self.assert_closed(s, card)
        self.prepare(s, mana={'C': 8, 'U': 1, 'W': 3}); self.checkpoint(s)
        action = next(row for row in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if row['id'] == 'activate:' + creator.ref + ':ab3')
        command = {'action_id': action['id'], 'pay': 'auto'}
        self.reject(s, 'B', command)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', s.state.cards[creator.object_id].zone)
        self.assertIsNone(self.resolve(s))
        emblems = [card for card in s.state.cards.values() if card.object_kind == 'emblem']
        emblem, = emblems
        self.assertEqual(('A', 'A', 'command'), (emblem.owner, emblem.controller, emblem.zone))
        def protected(card):
            return 'indestructible' in s.engine._combat_keywords(s.state.cards[card.object_id])
        for card in (overlap, artifact, enchantment, land):
            self.assertTrue(protected(card), card.printed_name)
        for card in (foreign, excluded):
            self.assertFalse(protected(card), card.printed_name)
        s = self.replay(s, load=True)
        self.cast(s, 'A', entrant); self.assertIsNone(self.resolve(s))
        self.assertTrue(protected(entrant))
        self.cast(s, 'A', donation, ['B', overlap.ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual('B', s.state.cards[overlap.object_id].controller)
        self.assertFalse(protected(overlap))
        self.cast(s, 'A', destruction); self.assertIsNone(self.resolve(s))
        for card in (artifact, enchantment, land, entrant):
            self.assertEqual('battlefield', s.state.cards[card.object_id].zone)
        for card in (excluded, foreign, overlap):
            self.assertEqual('graveyard', s.state.cards[card.object_id].zone)
        self.assertEqual('command', s.state.cards[emblem.object_id].zone)
        self.finish(s, private)

    def test_original_action_witness_detects_missing_receiver_matching(self):
        from quorune import object_query
        original = object_query.object_matches_query
        def omit(row, spec):
            return False if spec.any_of else original(row, spec)
        with patch('quorune.continuous_effects.object_matches_query', side_effect=omit):
            with self.assertRaises(AssertionError):
                self.test_original_emblem_survives_creator_and_tracks_controller_new_entries_and_destruction()
