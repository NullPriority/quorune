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

    def test_overload_overlay_requires_the_complete_current_strict_pair(self):
        from quorune.card_programs.overload_view_equivalence import shadowed_reviewed_overload_keys
        card = self.db.lookup('Vandalblast', fuzzy=False)
        generated = self.program('Vandalblast').abilities
        reviewed = SemanticRegistry().programs_for_oracle(card.oracle_id)
        def shadow(values, previous=reviewed, record=card):
            return shadowed_reviewed_overload_keys(record, values, previous,
                capability_registry=self.registry, capability_profile='commander_review')
        self.assertTrue(shadow(generated))
        self.assertFalse(shadow(generated[:1]))
        self.assertFalse(shadow(generated, record=replace(card, oracle_text=card.oracle_text + '\nUnknown sibling.')))
        for field in ('source_oracle_hash', 'source_rulings_hash'):
            with self.assertRaises(ValueError):
                replace(generated[0], provenance={**generated[0].provenance, field: ''})
            changed = (generated[0], replace(generated[1],
                provenance={**generated[1].provenance, field: 'f' * 64}))
            self.assertFalse(shadow(changed))
        paid = generated[1]
        self.assertFalse(shadow((generated[0], replace(paid, effects=()))))
        old = next(a for a in reviewed if a.key == generated[0].key)
        cost = deepcopy(old.cost_schema)
        cost['alternate_costs'][0]['requirements']['GENERIC'] += 1
        self.assertFalse(shadow(generated, (replace(old, cost_schema=cost),)))

    def test_composition_metadata_cannot_hide_a_changed_child_target_or_source_span(self):
        for name in ('How to Start a Riot', 'Sunset Revelry', 'Reality Anchor'):
            with self.subTest(name=name):
                ability = self.program(name).abilities[0]
                self.assertTrue(self.binding(ability)['strict'])
                broken = deepcopy(ability.provenance)
                broken['components'][0]['source_span']['end'] += 1
                self.assertFalse(self.binding(replace(ability, provenance=broken))['strict'])

    def test_known_empty_scalar_role_requires_optional_singleton_target_authority(self):
        from types import SimpleNamespace
        from quorune.semantic_runtime.values import resolve_semantic_value
        from quorune.semantic_runtime.context import SemanticNodeError
        schema = {'groups': [{'id': 'clause_1', 'zones': ['battlefield'],
            'categories': ['permanent'], 'types_any': ['creature'], 'min': 0, 'max': 1}]}
        current = {'schema': schema}
        host = SimpleNamespace(semantics=SimpleNamespace(get=lambda key: None),
            _stack_target_schema=lambda item, program: current['schema'])
        item = SimpleNamespace(context={'target_groups': {'clause_1': []},
            'target_groups_current': {'clause_1': []}}, semantic_key='fixture', modes=[])
        for value in ('$target_group.clause_1.0', '$target_group.clause_1.current_controller.0'):
            self.assertIsNone(resolve_semantic_value(host, value, item))
        for bounds in ({'min': 1, 'max': 1}, {'min': 0, 'max': 2},
                {'min': 0, 'max': True}, {'id': 'clause_0', 'min': 0, 'max': 1}):
            current['schema'] = {'groups': [{**schema['groups'][0], **bounds}]}
            with self.subTest(bounds=bounds), self.assertRaises(SemanticNodeError):
                resolve_semantic_value(host, '$target_group.clause_1.0', item)
        current['schema'] = schema
        item.context['target_groups_current']['clause_1'] = ['injected']
        with self.assertRaises(SemanticNodeError):
            resolve_semantic_value(host, '$target_group.clause_1.0', item)

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

    def test_actual_token_spells_retain_their_distinct_declaration_rules(self):
        for name in ('Sleep with the Fishes', 'Into the Fae Court'):
            with self.subTest(name=name):
                def before(s, source):
                    return {'ground': self.add(s.engine, 'Darksteel Myr', seat='B'),
                        'flyer': self.add(s.engine, 'Ornithopter', seat='B')}
                s, source, private, extra = self.scene(name, before)
                hand = len(s.state.players['A'].zones['hand'])
                self.cast(s, 'A', source, [extra['ground'].ref] if name == 'Sleep with the Fishes' else [])
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                token, = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
                self.assertEqual((1, 1), tuple(s.engine._numeric_stat(token.object_id, k) for k in ('power', 'toughness')))
                if name == 'Sleep with the Fishes':
                    self.assertTrue(s.state.cards[extra['ground'].object_id].tapped)
                    self.assertFalse(s.engine._can_block(token, s.state.cards[extra['flyer'].object_id])[0])
                    self.until(s, lambda session: session.state.active_player == 'B' and session.state.step == 'upkeep')
                    self.assertTrue(s.state.cards[extra['ground'].object_id].tapped)
                else:
                    self.assertEqual(hand + 2, len(s.state.players['A'].zones['hand']))
                    self.assertIn('flying', s.engine._combat_keywords(token))
                    self.assertFalse(s.engine._can_block(s.state.cards[extra['ground'].object_id], token)[0])
                    self.assertTrue(s.engine._can_block(s.state.cards[extra['flyer'].object_id], token)[0])
                self.finish(s, private)

    def test_actual_removal_keeps_selected_shadow_and_attacking_flying_scopes_distinct(self):
        def before(s, source):
            body = self.add(s.engine, 'Darksteel Myr', seat='B')
            body.counters['shadow'] = 1
            return {'body': body, 'other': self.add(s.engine, 'Ornithopter', seat='D')}
        s, source, private, extra = self.scene('Reality Anchor', before)
        hand = len(s.state.players['A'].zones['hand'])
        self.cast(s, 'A', source, [extra['body'].ref])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertNotIn('shadow', s.engine._combat_keywords(s.state.cards[extra['body'].object_id]))
        self.assertEqual(hand, len(s.state.players['A'].zones['hand']))
        self.finish(s, private)
        def flying_before(s, source):
            first = self.add(s.engine, 'Ornithopter')
            first.acquired_control_turn_count = -1
            return {'attacker': first, 'other': self.add(s.engine, 'Ornithopter', ref='nonattacking-flyer')}
        s, source, private, extra = self.scene('Gravity Well', flying_before, zone='battlefield')
        self.attack(s, [(extra['attacker'], 'B')])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertNotIn('flying', s.engine._combat_keywords(s.state.cards[extra['attacker'].object_id]))
        self.assertIn('flying', s.engine._combat_keywords(s.state.cards[extra['other'].object_id]))
        self.finish(s, private)

    def test_actual_palm_chooses_without_targeting_and_returns_only_prevented_damage(self):
        def before(s, source):
            s.state.players['B'].mana_pool['R'] = 1
            return {'bolt': self.add(s.engine, 'Lightning Bolt', seat='B', zone='hand')}
        s, source, private, extra = self.scene('Deflecting Palm', before)
        self.cast(s, 'B', extra['bolt'], ['A'])
        bolt_ref = next(item.ref for item in s.state.stack if item.card_object_id == extra['bolt'].object_id)
        self.cast(s, 'A', source)
        decision = self.resolve(s)
        self.assertIsNotNone(decision)
        self.reject(s, 'A', {'action_id': 'choose', 'source': private.ref})
        s = self.replay(s, load=True)
        self.choose(s, {'action_id': 'choose', 'source': bolt_ref})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(40, s.state.players['A'].life)
        self.assertEqual(37, s.state.players['B'].life)
        self.assertFalse(s.state.damage_prevention_shields)
        self.finish(s, private)

    def test_actual_ninja_modes_read_the_board_after_the_blink_instruction(self):
        for blink in (False, True):
            with self.subTest(blink=blink):
                def before(s, source):
                    body = self.add(s.engine, 'Llanowar Elves')
                    body.counters['+1/+1'] = 5
                    self.add(s.engine, 'Grizzly Bears')
                    return {'body': body, 'victim': self.add(s.engine, 'Darksteel Myr', seat='B')}
                s, source, private, extra = self.scene('Go Ninja Go', before)
                row = self.action(s, 'A', source)
                modes = ['mode_1', 'mode_2'] if blink else ['mode_2']
                targets = [extra['body'].ref, extra['victim'].ref] if blink else [extra['victim'].ref]
                self.choose(s, {'action_id': row['id'], 'modes': modes, 'targets': targets, 'pay': 'auto'})
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual(2 if blink else 6, s.state.cards[extra['victim'].object_id].marked_damage)
                self.assertEqual(0 if blink else 5, s.state.cards[extra['body'].object_id].counters.get('+1/+1', 0))
                self.finish(s, private)

    def test_actual_role_spells_keep_optional_attachment_separate_from_the_first_result(self):
        for name in ('Cut In', 'Twisted Fealty'):
            for role in (False, True):
                with self.subTest(name=name, role=role):
                    def before(s, source):
                        body = self.add(s.engine, 'Grizzly Bears')
                        body.acquired_control_turn_count = -1
                        return {'body': body, 'first': self.add(s.engine, 'Darksteel Myr', seat='B'),
                            'remove': self.add(s.engine, 'Disenchant', zone='hand')}
                    s, source, private, extra = self.scene(name, before)
                    targets = [{'group': 'clause_0', 'ref': extra['first'].ref}]
                    if role:
                        targets.append({'group': 'clause_1', 'ref': extra['body'].ref})
                    self.cast(s, 'A', source, targets)
                    s = self.replay(s, load=True)
                    self.assertIsNone(self.resolve(s))
                    tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
                    self.assertEqual(int(role), len(tokens))
                    if role:
                        self.assertEqual(extra['body'].object_id, tokens[0].attached_to)
                    if name == 'Cut In':
                        self.assertEqual(4, s.state.cards[extra['first'].object_id].marked_damage)
                        if role:
                            self.attack(s, [(extra['body'], 'B')])
                            self.assertIsNone(self.resolve(s))
                            self.assertEqual(1, s.state.cards[extra['body'].object_id].counters.get('+1/+1', 0))
                    else:
                        self.assertEqual('A', s.state.cards[extra['first'].object_id].controller)
                        self.assertIn('haste', s.engine._combat_keywords(s.state.cards[extra['first'].object_id]))
                        if role:
                            self.assertEqual(3, s.engine._numeric_stat(extra['body'].object_id, 'power'))
                            self.cast(s, 'A', extra['remove'], [tokens[0].ref])
                            self.assertIsNone(self.resolve(s))
                            self.assertEqual([40, 39, 39, 39], [s.state.players[p].life for p in 'ABCD'])
                    self.finish(s, private)

    def test_actual_brigand_requires_its_controllers_committed_crime_and_draws_after_damage(self):
        for crime in (False, True):
            with self.subTest(crime=crime):
                def before(s, source):
                    source.acquired_control_turn_count = -1
                    return {'blocker': self.add(s.engine, 'Darksteel Myr', seat='B'),
                        'bolt': self.add(s.engine, 'Lightning Bolt', zone='hand')}
                s, source, private, extra = self.scene('Nimble Brigand', before, zone='battlefield')
                self.assertTrue(s.engine._can_block(s.state.cards[source.object_id], s.state.cards[extra['blocker'].object_id])[0])
                self.cast(s, 'A', extra['bolt'], ['B' if crime else 'A'])
                self.assertEqual(not crime, s.engine._can_block(s.state.cards[source.object_id], s.state.cards[extra['blocker'].object_id])[0])
                self.assertIsNone(self.resolve(s))
                hand = len(s.state.players['A'].zones['hand'])
                self.attack(s, [(source, 'B')])
                if crime:
                    # There is no legal blocker, so this empty declaration
                    # window may be skipped by the canonical combat owner.
                    s = self.replay(s, load=True)
                else:
                    self.until(s, lambda session: session.state.pending_decision.kind == 'combat.blockers')
                    s = self.replay(s, load=True)
                    self.choose(s, {'action_id': 'block', 'blocks': {extra['blocker'].ref: source.ref}}, actor='B')
                self.until(s, lambda session: session.state.phase_index >= 9 and not session.state.stack)
                self.assertEqual(hand + int(crime), len(s.state.players['A'].zones['hand']))
                self.assertEqual(36 if crime else 40, s.state.players['B'].life)
                self.finish(s, private)

    def test_actual_swordtooth_requires_the_blessing_at_combat_declaration(self):
        for blessed in (False, True):
            with self.subTest(blessed=blessed):
                def before(s, source):
                    source.acquired_control_turn_count = -1
                    other = self.add(s.engine, 'Grizzly Bears')
                    other.acquired_control_turn_count = -1
                    if blessed:
                        for n in range(8):
                            self.add(s.engine, 'Forest', ref='swordtooth-permanent-' + str(n))
                    return {}
                s, source, private, extra = self.scene('Wayward Swordtooth', before, zone='battlefield')
                self.assertEqual(blessed, s.state.players['A'].stats.get('citys_blessing', False))
                self.until(s, lambda session: session.state.pending_decision.kind == 'combat.attackers')
                command = {'action_id': 'attack', 'attackers': {source.ref: 'B'}}
                if blessed:
                    self.choose(s, command)
                else:
                    self.reject(s, 'A', command)
                    self.choose(s, {'action_id': 'attack', 'attackers': {}})
                self.finish(s, private)

    def test_actual_bat_upkeep_payment_transforms_each_face_only_when_paid(self):
        from quorune.model import TurnEntry
        for face in ('Screeching Bat', 'Stalking Vampire'):
            for paid in (False, True):
                with self.subTest(face=face, paid=paid):
                    s = self.session(702001 + int(paid))
                    source = self.add(s.engine, 'Screeching Bat // Stalking Vampire')
                    source.active_face = face
                    private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
                    for n in range(2):
                        self.add(s.engine, 'Swamp', ref='bat-black-' + str(n))
                        self.add(s.engine, 'Wastes', ref='bat-generic-' + str(n))
                    self.assert_closed(s, source)
                    self.prepare(s)
                    s.state.config.semantic_policy = 'trusted_only'
                    s.engine._begin_turn(TurnEntry(turn_id='bat-upkeep-' + str(s.state.turn_sequence + 1),
                        player='A', created_sequence=s.state.turn_sequence))
                    s.engine.pump()
                    self.checkpoint(s)
                    self.assertIsNotNone(self.resolve(s))
                    s = self.replay(s, load=True)
                    self.choose(s, {'action_id': 'choose', 'pay': paid})
                    self.assertIsNone(self.resolve(s))
                    expected = ('Stalking Vampire' if face == 'Screeching Bat' else 'Screeching Bat') if paid else face
                    self.assertEqual(expected, s.state.cards[source.object_id].active_face)
                    if paid:
                        self.assertTrue(all(s.state.cards[c].tapped for c in s.state.players['A'].zones['battlefield'] if c != source.object_id))
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
