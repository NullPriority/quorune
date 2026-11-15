"""Original reciprocal damage abilities preserve ordered reads and source identity."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_fixed_creature_power_damage as compiler_controls
import test_recipient_control as recipients
from inherited_composition_fixture import InheritedNativeFixture
from quorune.compiler.creature_power_damage_templates import fixed_creature_power_damage_effect_template
from quorune.oracle_ir import compile_oracle_card
from quorune.reciprocal_power_damage import (
    RECIPROCAL_POWER_CAPABILITY as CAP, reciprocal_power_damage_capabilities,
    reciprocal_power_damage_runtime_dependencies, original_source_recipient_matches,
)
from quorune.rules.capabilities import load_default_capability_registry

BODY = 'This creature deals damage equal to its power to target creature. That creature deals damage equal to its power to this creature.'


class ReciprocalPowerModelTests(unittest.TestCase):
    def test_complete_pair_preserves_order_and_requires_both_closed_instructions(self):
        template = fixed_creature_power_damage_effect_template(BODY, card_name='Witness')
        self.assertEqual([('$source', '$target.0'), ('$target.0', '$source')],
            [(e['source'], e['target']) for e in template.effects])
        self.assertTrue(all(e['kind'] == 'bite' for e in template.effects))
        args = dict(effects=template.effects, target_schema=template.target_schema, mechanic_ids=template.mechanics)
        self.assertIn(CAP, reciprocal_power_damage_capabilities(**args))
        self.assertIn(CAP, reciprocal_power_damage_runtime_dependencies(SimpleNamespace(**args)))
        for field, value in (('source', '$target.1'), ('target', '$target.0'), ('kind', 'fight'),
                ('source_lki', '$context.creature_power_damage_source_lki'), ('target_must_be_creature', 1),
                ('recipient_is_original_source', 1), ('schema_version', 3), ('unknown', True)):
            effects = deepcopy(template.effects);effects[1][field] = value
            with self.subTest(field=field):
                self.assertEqual((), reciprocal_power_damage_capabilities(**{**args, 'effects': effects}))
        for count in (True, 2):
            schema = deepcopy(template.target_schema);schema['groups'][0]['count'] = count
            self.assertEqual((), reciprocal_power_damage_capabilities(**{**args, 'target_schema': schema}))
        for effects, schema in (((template.effects[0], None), template.target_schema),
                (template.effects, {'groups': 1}), (template.effects, {'groups': [None]})):
            self.assertEqual((), reciprocal_power_damage_capabilities(**{**args, 'effects': effects, 'target_schema': schema}))
        with self.assertRaises(ValueError):
            reciprocal_power_damage_runtime_dependencies(SimpleNamespace(effects=(template.effects[1],), target_schema=template.target_schema))
        card = SimpleNamespace(logical_object_id='source@1')
        host = SimpleNamespace(state=SimpleNamespace(cards={'source': card}))
        raw = {'stack_ref': 'stack', 'object_id': 'source', 'logical_object_id': 'source@1', 'card_ref': 'source-ref'}
        effect = {'target': 'source-ref', '_runtime_source': raw}
        self.assertTrue(original_source_recipient_matches(host, effect))
        card.logical_object_id = 'source@2'
        self.assertFalse(original_source_recipient_matches(host, effect))
        for changed in ({'target': 'other'}, {'_runtime_source': None}, {'_runtime_source': {**raw, 'unknown': True}}):
            with self.assertRaises(ValueError):original_source_recipient_matches(host, {**effect, **changed})

    def test_normal_compiler_retains_source_names_and_rejects_unconsumed_neighbors(self):
        registry = load_default_capability_registry()
        for body in (BODY, BODY.replace('This creature', 'Power Damage Fixture').replace('this creature.', 'Power Damage Fixture.')):
            ir = compile_oracle_card(compiler_controls.card_record('{T}: ' + body, type_line='Creature — Beast'),
                capability_registry=registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertIn(CAP, ir.faces[0].nodes[0].capability_dependencies)
        for text in (BODY + ' Draw a card.', BODY.replace('That creature deals', 'Another creature deals'),
                BODY.replace('to this creature.', 'to another creature.'), BODY.replace('its power to this creature.', 'its toughness to this creature.'),
                'You may have ' + BODY[0].lower() + BODY[1:], BODY.replace('. That creature', ' and that creature')):
            self.assertIsNone(fixed_creature_power_damage_effect_template(text, card_name='Witness'), text)


class ReciprocalPowerNativeTests(InheritedNativeFixture, unittest.TestCase):
    setUpClass = classmethod(recipients.RecipientControlNativeTests.setUpClass.__func__)
    scene = recipients.RecipientControlNativeTests.scene
    reject = recipients.RecipientControlNativeTests.reject
    finish = recipients.RecipientControlNativeTests.finish

    def board(self, name='Karplusan Yeti', *, temporal=False, wall=False):
        extra = {}
        def before(s, source, gift):
            if temporal:s.state.players['A'].life = 29
            extra['target'] = self.add(s.engine, 'Serra Ascendant' if temporal else 'Wall of Stone' if wall else 'Grizzly Bears',
                seat='A' if temporal else 'B', ref='reciprocal-target')
            for key, card, seat in (('lifelink', 'Lifelink', 'A'), ('blink', 'Cloudshift', 'A'), ('growth', 'Giant Growth', 'A'),
                    ('theft', 'Turn Against', 'B'), ('opponent_blink', 'Cloudshift', 'B')):
                extra[key] = self.add(s.engine, card, seat=seat, zone='hand', ref='reciprocal-' + key)
            s.state.players['B'].mana_pool.update({'C': 10, 'R': 2, 'W': 2})
            self.assert_closed(s, extra['target'])
        s, source, gift, private = self.scene(name, zone='hand' if name == 'Predatory Urge' else 'battlefield', before=before)
        return s, source, gift, private, extra

    def activate_pair(self, s, source, target, *, seat='A'):
        self.priority(s, seat)
        row = next(r for r in s.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']
            if r['id'].startswith('activate:' + source.ref + ':') and not r.get('mana_ability'))
        command = {'action_id': row['id'], 'targets': [target.ref], 'pay': 'auto'}
        self.reject(s, 'B' if seat == 'A' else 'A', command)
        self.reject(s, seat, {**command, 'targets': ['C']})
        result = s.act('pilot:' + seat, command);self.assertTrue(result.ok, result.summary)

    def amounts(self, s):
        return [amount for event in s.state.events if event.code == 'effect.creature_power_damage'
            for amount in event.details['dealt_amounts']]

    def test_original_activated_cards_keep_both_damage_instructions_and_vein_followup(self):
        for name in ('Tracker', 'Karplusan Yeti', 'Tahngarth, Talruum Hero', 'Vein Drinker'):
            with self.subTest(card=name):
                s, source, gift, private, extra = self.board(name)
                power = s.engine._numeric_stat(source.object_id, 'power')
                self.activate_pair(s, source, extra['target']);s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual([power, 2], self.amounts(s))
                self.assertEqual('graveyard', s.state.cards[extra['target'].object_id].zone)
                self.assertEqual('graveyard' if name == 'Tracker' else 'battlefield', s.state.cards[source.object_id].zone)
                self.assertEqual(1 if name == 'Vein Drinker' else 0, s.state.cards[source.object_id].counters.get('+1/+1', 0))
                self.finish(s, private)

    def temporal(self):
        s, source, gift, private, extra = self.board(temporal=True)
        self.cast(s, 'A', extra['lifelink'], [source.ref]);self.assertIsNone(self.resolve(s))
        self.activate_pair(s, source, extra['target']);s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(38, s.state.players['A'].life, 'reciprocal-current-power')
        self.assertEqual([3, 6], self.amounts(s))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual(('battlefield', 3), (s.state.cards[extra['target'].object_id].zone, s.state.cards[extra['target'].object_id].marked_damage))
        self.finish(s, private)

    def test_original_lifelink_threshold_changes_second_power_after_first_damage(self):
        self.temporal()

    def test_self_target_deals_two_events_before_lethal_state_based_actions(self):
        s, source, gift, private, extra = self.board()
        self.activate_pair(s, source, source);s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s));self.assertEqual([3, 3], self.amounts(s))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone);self.finish(s, private)

    def test_source_blink_uses_original_last_power_without_hitting_returned_copy(self):
        s, source, gift, private, extra = self.board()
        self.cast(s, 'A', extra['growth'], [source.ref]);self.assertIsNone(self.resolve(s))
        original = source.logical_object_id;self.activate_pair(s, source, extra['target'])
        self.cast(s, 'A', extra['blink'], [source.ref]);self.resolve_top(s)
        self.assertNotEqual(original, s.state.cards[source.object_id].logical_object_id)
        s = self.replay(s, load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual([6], self.amounts(s))
        current = s.state.cards[source.object_id]
        self.assertEqual(('battlefield', 0, 3), (current.zone, current.marked_damage, s.engine._numeric_stat(current.object_id, 'power')))
        self.finish(s, private)

    def test_illegal_target_new_incarnation_prevents_both_instructions(self):
        s, source, gift, private, extra = self.board()
        self.activate_pair(s, source, extra['target'])
        self.cast(s, 'B', extra['opponent_blink'], [extra['target'].ref]);self.resolve_top(s)
        s = self.replay(s, load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual([], self.amounts(s));self.assertEqual(0, s.state.cards[source.object_id].marked_damage)
        self.assertEqual(('battlefield', 0), (s.state.cards[extra['target'].object_id].zone, s.state.cards[extra['target'].object_id].marked_damage))
        self.finish(s, private)

    def test_original_predatory_urge_grants_the_creature_controller_its_own_source(self):
        s, aura, gift, private, extra = self.board('Predatory Urge')
        # The scene starts it in hand so the attachment is an actual cast.
        self.assertEqual('hand', aura.zone)
        self.cast(s, 'A', aura, [gift.ref]);self.assertIsNone(self.resolve(s))
        self.cast(s, 'B', extra['theft'], [gift.ref]);self.assertIsNone(self.resolve(s))
        self.activate_pair(s, gift, extra['target'], seat='B');s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s));self.assertEqual([2, 2], self.amounts(s))
        self.assertEqual('graveyard', s.state.cards[gift.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[extra['target'].object_id].zone);self.finish(s, private)

    def test_payload_minimum_retains_sequence_authority_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s, source, gift, private, extra = self.board()
        program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
        dependencies = tuple(c for c in program.capability_dependencies if c != CAP)
        changed = replace(program, coverage=(), capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:' + CAP, result['blockers'])

    def test_fresh_temporal_witness_kills_simultaneous_fight_substitution(self):
        from quorune.creature_power_damage import resolve_creature_power_damage
        self.temporal();skipped = set()
        def simultaneous(host, effect, **kwargs):
            key = (id(host), effect.get('source'), effect.get('target'))
            if key in skipped:return ()
            skipped.add((id(host), effect.get('target'), effect.get('source')))
            return resolve_creature_power_damage(host, {**effect, 'kind': 'fight'}, **kwargs)
        with patch('quorune.effect_runtime.damage_life_and_turns.resolve_creature_power_damage', simultaneous):
            with self.assertRaisesRegex(AssertionError, 'reciprocal-current-power'):self.temporal()
