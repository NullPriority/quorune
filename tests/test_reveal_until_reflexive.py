"""Normal command, targeting, and replay witnesses for a revealed creature result."""

from copy import deepcopy
from pathlib import Path
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
from quorune.reveal_until_library_selection_model import (
    REVEAL_UNTIL_CAPABILITY, REVEAL_UNTIL_REFLEXIVE_CAPABILITY, validate_reveal_until_selection,
)
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.semantics import SemanticRegistry
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests
import test_bound_effect_programs as bound_fixture


WHISTLE = "Yuna's Whistle"
FIXTURE = ROOT / 'tests/fixtures/reveal-until-reflexive-cards.json'


class RevealUntilReflexiveTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    native = bound_fixture.BoundEffectProgramRuntimeTests

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        path = Path(cls.temp.name) / 'whistle.sqlite3'
        build_fixture_database((FIXTURE, ROOT / 'tests/fixtures/bound-effect-program-cards.json',
                                ROOT / 'tests/fixtures/reflexive-payment-cards.json',
                                ROOT / 'tests/fixtures/state-qualified-zone-cards.json'), path)
        cls.db = CardDatabase(path)
        cls.addClassCleanup(cls.db.close)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Reflexive reveal', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30),
        ], ['Generic Bound Commander'])

    def prepare(self, names, *, own_target=True, response=False, seed=603120751):
        game = self.native.session(self, seed)
        engine = game.engine
        for oid in tuple(game.state.players['A'].zones['library']):
            engine.move_card(oid, 'exile', reason='Prepare the known fixture order')
        cards = [self.native.add(self, engine, name, zone='library', ref=f'whistle-library-{i}')
                 for i, name in enumerate(names)]
        spell = self.native.add(self, engine, WHISTLE, zone='hand', ref='whistle-source')
        enemy = self.native.add(self, engine, 'Generic Bound Body', seat='B', ref='whistle-enemy')
        target = self.native.add(self, engine, 'Generic Bound Body', ref='whistle-target') if own_target else None
        if response:
            self.native.add(self, engine, 'Stifle', seat='B', zone='hand', ref='whistle-response')
            self.native.add(self, engine, 'Island', seat='B', ref='whistle-island')
        engine.state.phase_index = TURN_STEPS.index(('precombat_main', 'main'))
        offer = self.native.ready(self, game, spell, {'G': 2, 'C': 1})
        self.native.checkpoint(self, game)
        return game, cards, spell, enemy, target, offer

    def rejected(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        revision = len(game.commands)
        self.assertFalse(game.act('pilot:' + actor, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(revision, len(game.commands))

    def cast(self, game, offer):
        command = {'action_id': offer['id'], 'pay': 'auto'}
        self.assertFalse(offer.get('target_schema'))
        self.rejected(game, 'B', command)
        result = game.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        parent, = game.state.stack
        self.assertEqual([], parent.targets)
        return parent.ref

    def choose(self, game, target):
        result = game.act('pilot:A', {'action_id': 'choose', 'targets': [target.ref]})
        self.assertTrue(result.ok, result.summary)

    def test_original_counter_uses_the_stopping_card_after_public_partition_and_private_target_choice(self):
        game, cards, spell, enemy, target, offer = self.prepare([
            'Walking Ballista', 'Grizzly Bears', 'Generic Bound Growth', 'Generic Bound Plains',
        ])
        hidden, selected, remainder_one, remainder_two = cards
        parent_ref = self.cast(game, offer)
        self.assertEqual('semantic.target', self.native.resolve(self, game).kind)
        self.assertEqual('graveyard', game.state.cards[spell.object_id].zone)
        child, = game.state.stack
        self.assertEqual(('triggered_ability', 'A', parent_ref),
                         (child.kind, child.controller, child.context['reflexive_parent']))
        self.assertEqual(2, child.context['dynamic_effects'][0]['amount'])
        self.assertEqual('hand', game.state.cards[selected.object_id].zone)
        self.assertEqual(list('ABCD'), game.state.cards[selected.object_id].known_to)
        library = game.state.players['A'].zones['library']
        self.assertEqual({remainder_one.object_id, remainder_two.object_id}, set(library[:2]))
        self.assertEqual(hidden.object_id, library[-1])
        self.assertFalse(set('BCD') & set(game.state.cards[hidden.object_id].revealed_to))
        self.assertEqual([], game.state.cards[remainder_one.object_id].known_to)
        self.assertEqual([], game.state.cards[remainder_two.object_id].known_to)
        self.assertEqual(0, game.state.players['A'].stats.get('shuffle_count', 0))
        choices = game.packet('pilot:A', full=True)['decision']['ctx']['target_schema']['legal_refs']
        self.assertEqual([target.ref], choices)
        for seat in 'BCD':
            packet = game.packet('pilot:' + seat, full=True)
            self.assertIsNone(packet.get('decision'))
            self.assertFalse(packet['state']['players']['A'].get('known_top'))
        game = self.native.replay(self, game, load=True)
        self.rejected(game, 'A', {'action_id': 'choose', 'targets': [enemy.ref]})
        self.rejected(game, 'B', {'action_id': 'choose', 'targets': [target.ref]})
        self.choose(game, target)
        self.assertIsNone(self.native.resolve(self, game))
        self.assertEqual(2, game.state.cards[target.object_id].counters.get('+1/+1', 0))
        self.native.replay(self, game, load=True)

    def test_creature_with_x_has_a_zero_counter_target_and_nonmatch_has_no_child(self):
        cases = (
            (['Walking Ballista'], True, True),
            (['Generic Bound Plains', 'Generic Bound Growth'], True, False),
            ([], True, False),
            (['Grizzly Bears'], False, False),
        )
        for case, (names, has_target, child_expected) in enumerate(cases):
            with self.subTest(names=names, has_target=has_target):
                game, cards, spell, _, target, offer = self.prepare(
                    names, own_target=has_target, seed=603120752 + case,
                )
                parent_ref = self.cast(game, offer)
                decision = self.native.resolve(self, game)
                if child_expected:
                    self.assertEqual('semantic.target', decision.kind)
                    child, = game.state.stack
                    self.assertEqual(parent_ref, child.context['reflexive_parent'])
                    self.assertEqual(0, child.context['dynamic_effects'][0]['amount'])
                    self.choose(game, target)
                    self.assertIsNone(self.native.resolve(self, game))
                    self.assertEqual(0, game.state.cards[target.object_id].counters.get('+1/+1', 0))
                else:
                    self.assertIsNone(decision)
                    self.assertFalse(game.state.stack)
                    self.assertFalse(game.state.pending_trigger_batches)
                self.assertEqual('graveyard', game.state.cards[spell.object_id].zone)
                if names and names[-1] in {'Walking Ballista', 'Grizzly Bears'}:
                    self.assertEqual('hand', game.state.cards[cards[-1].object_id].zone)
                else:
                    self.assertEqual(set(card.object_id for card in cards),
                                     set(game.state.players['A'].zones['library']))
                self.native.replay(self, game, load=True)

    def test_opponent_can_counter_the_child_without_undoing_the_library_result(self):
        game, cards, spell, _, target, starting_offer = self.prepare(
            ['Grizzly Bears'], response=True, seed=603120759,
        )
        self.cast(game, starting_offer)
        self.assertEqual('semantic.target', self.native.resolve(self, game).kind)
        self.choose(game, target)
        child, = game.state.stack
        result = game.act('pilot:A', {'action_id': 'pass'})
        self.assertTrue(result.ok, result.summary)
        offers = game.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        response = next(offer for offer in offers if offer.get('card') == 'whistle-response')
        result = game.act('pilot:B', {'action_id': response['id'], 'pay': 'auto', 'targets': [child.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.native.resolve(self, game))
        self.assertEqual('graveyard', game.state.cards[spell.object_id].zone)
        self.assertEqual('hand', game.state.cards[cards[0].object_id].zone)
        self.assertEqual(0, game.state.cards[target.object_id].counters.get('+1/+1', 0))
        self.native.replay(self, game, load=True)

    def test_strict_original_admission_withdraws_the_leaf_and_keeps_legacy_partition(self):
        record = self.db.lookup(WHISTLE)
        for profile in ('traditional', 'commander_duel', 'commander_review'):
            with self.subTest(profile=profile):
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile=profile)
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile=profile)
                self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        effect, = ir.faces[0].nodes[0].effects
        for change in ({'player': ' B'}, {'stop_count': 2}, {'remainder_order': 'chosen'}, {'schema_version': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_reveal_until_selection({**effect, **change})
        from quorune.compiler import reveal_until_library_selection_templates as owner
        with patch.object(owner, 'reveal_until_library_selection_effect_template', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=self.registry,
                capability_profile='commander_review').status)

        files = ROOT / 'quorune/rules'
        registry_source = read_evidence_document(files / 'capability-registry.json')
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        test_ids = set(discover_tests(ROOT).values())
        for capability in (REVEAL_UNTIL_REFLEXIVE_CAPABILITY, 'target.revalidate_resolution'):
            source = deepcopy(registry_source)
            next(row for row in source['capabilities'] if row['id'] == capability).update(
                status='blocked', blockers=['Genuine focused authority withdrawal'],
            )
            registry = CapabilityRegistry(source)
            index = build_index(registry_value=source, declaration_source=declarations, discovered_test_ids=test_ids)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            withheld = compile_oracle_card(record, capability_registry=registry, capability_profile='commander_review')
            self.assertTrue(withheld.material_residuals)
        prefix = record.oracle_text.split(' When ', 1)[0]
        simple = owner.reveal_until_library_selection_effect_template(prefix, allow_reflexive=True)
        self.assertEqual(1, simple[1][0]['schema_version'])
        self.assertNotIn(REVEAL_UNTIL_REFLEXIVE_CAPABILITY, validate_reveal_until_selection(simple[1][0])[1])
        self.assertIn(REVEAL_UNTIL_CAPABILITY, ir.faces[0].nodes[0].capability_dependencies)
