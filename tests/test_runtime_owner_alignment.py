"""Typed owner admission retains the actual variant and each child context.

Expected gameplay comes from the pinned originals and CR 603.2d, 608.2,
611.2 and 702.96; exact compilation alone is not a behavioral witness.
"""
import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from common import ROOT
import test_delivery_composition_native as actions
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database

NAMES = ('Screeching Bat // Stalking Vampire', 'How to Start a Riot',
    'Return of the Wildspeaker', 'Gravity Well', 'Vandalblast', 'Wayward Swordtooth',
    'Go Ninja Go', 'Sleep with the Fishes', 'Sunset Revelry', 'Panharmonicon',
    'Nimble Brigand', 'Into the Fae Court', 'Cut In', 'Twisted Fealty',
    'Reality Anchor', 'Deflecting Palm')


class RuntimeOwnerAlignmentTests(InheritedNativeFixture, unittest.TestCase):
    scene = actions.DeliveryCompositionNativeTests.scene
    choose = actions.DeliveryCompositionNativeTests.choose
    reject = actions.DeliveryCompositionNativeTests.reject
    finish = actions.DeliveryCompositionNativeTests.finish
    settle_order = actions.DeliveryCompositionNativeTests.settle_order

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'owner-alignment.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/runtime-owner-alignment-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Owner alignment originals',
            [DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Sliver Hivelord'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def program(self, name):
        card = self.db.lookup(name, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry,
            capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        return _compile_best_available_card_program_from_ir(self.db, card, ir,
            semantic_registry=SemanticRegistry(), capability_registry=self.registry,
            capability_profile='commander_review')

    def binding(self, ability):
        return bind_semantic_program_runtime(ability, capability_registry=self.registry,
            profile='commander_review')

    def test_originals_have_complete_normal_runtime_admission(self):
        for name in NAMES:
            with self.subTest(name=name):
                result = bind_card_program_runtime(self.program(name),
                    capability_registry=self.registry, profile='commander_review')
                self.assertTrue(result['strict_capability_ready'], result['blockers'])
                self.assertEqual('capability_closed', result['trust_basis'])

    def test_component_variant_minimum_is_decoded_without_using_coverage_labels(self):
        from quorune.rules.keyword_runtime_requirements import registered_component_capabilities
        from quorune.semantic_runtime.ability_fragments import TriggerMultiplierAbilityFragmentHandler
        owner = TriggerMultiplierAbilityFragmentHandler()
        ability = next(a for a in self.program('Panharmonicon').abilities if a.handlers)
        descriptor = next(d for d in ability.handlers if d['handler_id'] == owner.handler_id)
        represented = owner.validate(descriptor).capability_id
        self.assertEqual((represented,), registered_component_capabilities(descriptor,
            {'capability_dependencies': owner.capability_dependencies}))
        self.assertEqual((represented,), registered_component_capabilities(descriptor,
            {'capability_dependencies': (represented,)}))
        other = tuple(c for c in owner.capability_dependencies if c != represented)
        with self.assertRaises(ValueError):
            registered_component_capabilities(descriptor, {'capability_dependencies': other})
        invalid = deepcopy(descriptor)
        invalid['unowned_variant'] = True
        with self.assertRaises(ValueError):
            registered_component_capabilities(invalid,
                {'capability_dependencies': owner.capability_dependencies})

    def test_unrelated_recipient_field_does_not_claim_the_conditional_static_namespace(self):
        from quorune.conditional_recipient_model import conditional_recipient_capabilities
        from quorune.continuous_conditions import FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
        self.assertEqual((), conditional_recipient_capabilities({'op': 'deal_damage', 'recipient': None}))
        with self.assertRaises(ValueError):
            conditional_recipient_capabilities({
                'handler_id': FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID, 'recipient': None})

    def test_optional_composed_target_bounds_are_preserved_and_mixed_encodings_reject(self):
        from quorune.compiler.program_composition import _composed_target_schema
        single = {'zones': ['battlefield'], 'categories': ['permanent'],
            'types_any': ['creature'], 'count': 1}
        optional = {k: v for k, v in single.items() if k != 'count'}
        optional.update(min=0, max=1)
        schema = _composed_target_schema((single, optional))
        self.assertEqual([(1, 1), (0, 1)], [(g['min'], g['max']) for g in schema['groups']])
        for invalid in ({**optional, 'up_to': 1}, {**optional, 'max': True},
                {**optional, 'min': 1}, {**optional, 'max': 6}):
            self.assertIsNone(_composed_target_schema((single, invalid)))

    def test_actual_riot_keeps_the_creature_target_separate_from_target_player_creatures(self):
        def before(s, source):
            return {'menace': self.add(s.engine, 'Darksteel Myr', seat='B'),
                'boost': self.add(s.engine, 'Grizzly Bears', seat='C'),
                'other': self.add(s.engine, 'Llanowar Elves', seat='D')}
        s, source, private, extra = self.scene('How to Start a Riot', before)
        row = self.action(s, 'A', source)
        self.reject(s, 'A', {'action_id': row['id'], 'targets': ['C', extra['menace'].ref], 'pay': 'auto'})
        self.cast(s, 'A', source, [extra['menace'].ref, 'C'])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertIn('menace', s.engine._combat_keywords(s.state.cards[extra['menace'].object_id]))
        self.assertEqual(0, s.engine._numeric_stat(extra['menace'].object_id, 'power'))
        self.assertEqual(4, s.engine._numeric_stat(extra['boost'].object_id, 'power'))
        self.assertEqual(1, s.engine._numeric_stat(extra['other'].object_id, 'power'))
        self.finish(s, private)

    def test_actual_wildspeaker_aggregate_uses_current_nonhuman_power_and_empty_zero(self):
        for eligible in (False, True):
            with self.subTest(eligible=eligible):
                def before(s, source):
                    human = self.add(s.engine, 'Nimble Brigand')
                    human.counters['+1/+1'] = 10
                    if eligible:
                        body = self.add(s.engine, 'Llanowar Elves')
                        body.counters['+1/+1'] = 3
                    return {}
                s, source, private, extra = self.scene('Return of the Wildspeaker', before)
                hand = len(s.state.players['A'].zones['hand'])
                row = self.action(s, 'A', source)
                self.choose(s, {'action_id': row['id'], 'modes': ['mode_1'], 'targets': [], 'pay': 'auto'})
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual(hand - 1 + (4 if eligible else 0), len(s.state.players['A'].zones['hand']))
                self.finish(s, private)

    def test_actual_revelry_checks_independent_opponents_and_current_hand(self):
        for behind in (False, True):
            with self.subTest(behind=behind):
                def before(s, source):
                    if behind:
                        s.state.players['B'].life = 41
                        for n in range(2):
                            self.add(s.engine, 'Grizzly Bears', seat='C', ref='revelry-body-' + str(n))
                        for n in range(5):
                            self.add(s.engine, 'Counterspell', seat='D', zone='hand', ref='revelry-private-' + str(n))
                    else:
                        s.state.players['A'].life = 41
                        self.add(s.engine, 'Grizzly Bears')
                        for n in range(5):
                            self.add(s.engine, 'Counterspell', zone='hand', ref='revelry-own-' + str(n))
                    return {}
                s, source, private, extra = self.scene('Sunset Revelry', before)
                hand = len(s.state.players['A'].zones['hand'])
                self.cast(s, 'A', source)
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual(44 if behind else 41, s.state.players['A'].life)
                tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
                self.assertEqual(2 if behind else 0, len(tokens))
                self.assertEqual(hand - 1 + int(behind), len(s.state.players['A'].zones['hand']))
                self.finish(s, private)

    def test_actual_panharmonicon_doubles_only_its_controllers_trigger_once(self):
        def before(s, source):
            self.add(s.engine, 'Soul Warden')
            self.add(s.engine, 'Soul Warden', seat='B', ref='foreign-warden')
            return {'entry': self.add(s.engine, 'Ornithopter', zone='hand')}
        s, source, private, extra = self.scene('Panharmonicon', before, zone='battlefield')
        self.cast(s, 'A', extra['entry'])
        s = self.replay(s, load=True)
        self.settle_order(s)
        self.assertEqual(42, s.state.players['A'].life)
        self.assertEqual(41, s.state.players['B'].life)
        self.finish(s, private)

    def test_actual_vandalblast_preserves_normal_targeting_and_paid_opponent_set(self):
        for overloaded in (False, True):
            with self.subTest(overloaded=overloaded):
                def before(s, source):
                    return {'own': self.add(s.engine, 'Sol Ring'),
                        'first': self.add(s.engine, 'Ornithopter', seat='B'),
                        'second': self.add(s.engine, "Wayfarer's Bauble", seat='C'),
                        'durable': self.add(s.engine, 'Darksteel Myr', seat='D')}
                s, source, private, extra = self.scene('Vandalblast', before)
                row = self.action(s, 'A', source)
                self.reject(s, 'A', {'action_id': row['id'], 'targets': [extra['own'].ref], 'pay': 'auto'})
                command = {'action_id': row['id'], 'targets': [] if overloaded else [extra['first'].ref], 'pay': 'auto'}
                if overloaded:
                    command['cost_option'] = 'overload'
                self.choose(s, command)
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard', s.state.cards[extra['first'].object_id].zone)
                self.assertEqual('graveyard' if overloaded else 'battlefield', s.state.cards[extra['second'].object_id].zone)
                for key in ('own', 'durable'):
                    self.assertEqual('battlefield', s.state.cards[extra[key].object_id].zone)
                self.finish(s, private)


if __name__ == '__main__':
    unittest.main()
