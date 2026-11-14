"""Current predicates gate preparation while the following fight remains unconditional."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_recipient_control as recipients
import test_fixed_creature_power_damage as compiler_controls
from inherited_composition_fixture import InheritedNativeFixture
from quorune.conditional_target_preparation import (
    CONDITIONAL_PREPARATION_CAPABILITY as CAP, PreparationCondition, validate_preparation_instruction,
)
from quorune.compiler.conditional_target_preparation import conditional_prepared_power_template
from quorune.oracle_ir import compile_oracle_card, _reviewed_atomic_effect_template
from quorune.rules.conditional_target_preparation import conditional_prepared_power_capabilities, conditional_preparation_runtime_dependencies
from quorune.rules.capabilities import load_default_capability_registry
from quorune.replacement.immutable import freeze_value

BODY = "Put a +1/+1 counter on target creature you control if it's legendary. Then it fights target creature an opponent controls."


class ConditionalPreparationModelTests(unittest.TestCase):
    def template(self, text=BODY):
        return conditional_prepared_power_template(text, source_name='Witness',
            compile_atomic=lambda text:_reviewed_atomic_effect_template(text, card_name='Witness'))

    def test_complete_conditional_pair_and_frozen_payload_keep_target_roles(self):
        template = self.template();wrapper, damage = template[1]
        self.assertEqual(('$target.0', '$target.0', '$target.1'), (wrapper['condition_target'], damage['source'], damage['target']))
        self.assertEqual('fight', damage['kind']);self.assertEqual('target', wrapper['condition']['scope'])
        self.assertEqual(validate_preparation_instruction(wrapper)[0], validate_preparation_instruction(freeze_value(wrapper))[0])
        self.assertIn(CAP, conditional_prepared_power_capabilities(effects=template[1], target_schema=template[2], mechanic_ids=template[3]))
        self.assertIn(CAP, conditional_preparation_runtime_dependencies(SimpleNamespace(effects=template[1], target_schema=template[2])))
        for field, value in (('schema_version', 16.0), ('condition_target', '$target.1'), ('player', '$active'),
                ('otherwise_effects', [{'op': 'draw'}]), ('unknown', True)):
            effects = deepcopy(template[1]);effects[0][field] = value
            self.assertEqual((), conditional_prepared_power_capabilities(effects=effects, target_schema=template[2], mechanic_ids=template[3]), field)
        for change in ({'source': '$target.1'}, {'target': '$target.0'}, {'kind': 'unknown'}):
            effects = (wrapper, {**damage, **change})
            self.assertEqual((), conditional_prepared_power_capabilities(effects=effects, target_schema=template[2], mechanic_ids=template[3]))
        changed = deepcopy(wrapper);changed['effects'][0]['card'] = '$target.1'
        with self.assertRaises(ValueError):validate_preparation_instruction(changed)

    def test_bonus_conditions_do_not_become_target_requirements_or_consume_unknown_siblings(self):
        registry = load_default_capability_registry()
        ir = compile_oracle_card(compiler_controls.card_record(BODY), capability_registry=registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        first = ir.faces[0].nodes[0].target_schema['groups'][0]
        self.assertNotIn('supertypes_any', first);self.assertNotIn('supertypes_all', first)
        for text in (BODY + ' Draw a card.', BODY.replace('legendary', 'equipped with a named object'),
                BODY.replace('it fights', 'another creature fights'), BODY.replace('a +1/+1 counter', 'a charge counter'),
                BODY.replace("it's legendary", 'you cast this spell during your main phase')):
            self.assertIsNone(self.template(text), text)
        for change in ({'scope': 'unknown'}, {'schema_version': True}, {'condition': None}):
            value = {**self.template()[1][0]['condition'], **change}
            with self.assertRaises(ValueError):PreparationCondition.from_dict(value)


class ConditionalPreparationNativeTests(InheritedNativeFixture, unittest.TestCase):
    setUpClass = classmethod(recipients.RecipientControlNativeTests.setUpClass.__func__)
    scene = recipients.RecipientControlNativeTests.scene
    reject = recipients.RecipientControlNativeTests.reject
    finish = recipients.RecipientControlNativeTests.finish
    finish_turn = recipients.RecipientControlNativeTests.finish_turn

    def board(self, name, *, applies=True, prohibit=False):
        extra = {}
        fighters = {'Ancient Animus': ('Isamaru, Hound of Konda', 'Grizzly Bears'),
            'Hog-Monkey Rampage': ('Rumbling Baloth', 'Hill Giant'), 'Joust': ('Knight of the White Orchid', 'Grizzly Bears'),
            'Savage Swipe': ('Grizzly Bears', 'Hill Giant'), 'Throw from the Saddle': ('Gloryheath Lynx', 'Grizzly Bears')}
        def before(s, spell, gift):
            fighter = fighters.get(name, ('Grizzly Bears', 'Grizzly Bears'))[0 if applies else 1]
            extra['first'] = self.add(s.engine, fighter, ref='conditional-preparation-first')
            extra['second'] = self.add(s.engine, 'Hill Giant' if name == 'Blizzard Brawl' else 'Wall of Stone', seat='B', ref='conditional-preparation-second')
            if name == 'Blizzard Brawl':
                extra['snow'] = []
                for seat, count in (('A', 3 if applies else 2), ('B', 3)):
                    for i in range(count):
                        land = self.add(s.engine, 'Snow-Covered Plains', seat=seat, ref=f'condition-snow-{seat}-{i}')
                        if seat == 'A':extra['snow'].append(land)
            if name == 'Duel for Dominance':
                self.add(s.engine, 'Fugitive Wizard', ref='coven-one')
                self.add(s.engine, 'Hill Giant' if applies else 'Grizzly Bears', ref='coven-third')
            if name == 'Wolf Strike':s.state.day_night = 'night' if applies else 'day'
            if prohibit:self.add(s.engine, 'Solemnity', seat='B')
            extra['bounce'] = self.add(s.engine, 'Boomerang', seat='B', zone='hand', ref='conditional-preparation-bounce')
            extra['blink'] = self.add(s.engine, 'Cloudshift', zone='hand', ref='conditional-preparation-blink')
            extra['growth'] = self.add(s.engine, 'Giant Growth', zone='hand', ref='conditional-preparation-growth')
            s.state.players['B'].mana_pool['U'] = 10
            for c in (extra['first'], extra['second']):self.assert_closed(s, c)
        s, spell, gift, private = self.scene(name, before=before)
        return s, spell, private, extra

    def cast_preparation(self, s, spell, extra):
        self.priority(s, 'A');row = self.action(s, 'A', spell)
        command = {'action_id': row['id'], 'targets': [extra['first'].ref, extra['second'].ref], 'pay': 'auto'}
        if row.get('cost_options'):command['cost_option'] = row['cost_options'][0]['id']
        self.reject(s, 'B', command)
        self.reject(s, 'A', {**command, 'targets': [extra['second'].ref, extra['first'].ref]})
        self.reject(s, 'A', {**command, 'targets': [extra['first'].ref]})
        result = s.act('pilot:A', command);self.assertTrue(result.ok, result.summary)

    def damage(self, s):
        return [n for e in s.state.events if e.code == 'effect.creature_power_damage' for n in e.details['dealt_amounts']]

    def original_pair(self, name, applies):
        s, spell, private, extra = self.board(name, applies=applies)
        first = extra['first'];base = s.engine._numeric_stat(first.object_id, 'power')
        bonus = (2 if name in {'Joust', 'Savage Swipe', 'Wolf Strike'} else 1) if applies else (1 if name == 'Throw from the Saddle' else 0)
        counter = applies and name in {'Ancient Animus', 'Duel for Dominance', 'Hog-Monkey Rampage', 'Throw from the Saddle'}
        self.cast_preparation(s, spell, extra);s = self.replay(s, load=True);self.assertIsNone(self.resolve(s))
        own = s.state.cards[first.object_id];other = s.state.cards[extra['second'].object_id]
        self.assertEqual([base + bonus, 3] if name == 'Blizzard Brawl' else [base + bonus], self.damage(s), name)
        if name == 'Blizzard Brawl':
            self.assertEqual(('battlefield', 'graveyard') if applies else ('graveyard', 'battlefield'), (own.zone, other.zone))
            if applies:self.assertIn('indestructible', s.engine._combat_keywords(own))
        else:
            self.assertEqual(base + bonus, other.marked_damage)
            self.assertEqual(0, own.marked_damage)
            self.assertEqual(int(counter), own.counters.get('+1/+1', 0))
        if name == 'Throw from the Saddle':
            self.finish_turn(s)
            self.assertEqual(base + int(counter), s.engine._numeric_stat(first.object_id, 'power'))
        self.finish(s, private)

    def test_original_legendary_snow_coven_and_power_threshold_preserve_both_outcomes(self):
        for name in ('Ancient Animus', 'Blizzard Brawl', 'Duel for Dominance', 'Hog-Monkey Rampage'):
            for applies in (False, True):
                with self.subTest(card=name, applies=applies):self.original_pair(name, applies)

    def test_original_subtype_exact_power_mount_alternative_and_night_preserve_both_outcomes(self):
        for name in ('Joust', 'Savage Swipe', 'Throw from the Saddle', 'Wolf Strike'):
            for applies in (False, True):
                with self.subTest(card=name, applies=applies):self.original_pair(name, applies)

    def changed_power(self):
        s, spell, private, extra = self.board('Savage Swipe')
        self.cast_preparation(s, spell, extra)
        self.cast(s, 'A', extra['growth'], [extra['first'].ref]);self.resolve_top(s)
        s = self.replay(s, load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual([5], self.damage(s), 'current-preparation-predicate')
        self.assertEqual(5, s.state.cards[extra['second'].object_id].marked_damage);self.finish(s, private)

    def test_response_changes_current_power_and_snow_condition_before_preparation(self):
        self.changed_power()
        s, spell, private, extra = self.board('Blizzard Brawl')
        self.cast_preparation(s, spell, extra)
        self.cast(s, 'B', extra['bounce'], [extra['snow'][0].ref]);self.resolve_top(s)
        s = self.replay(s, load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual([2, 3], self.damage(s));self.assertEqual('graveyard', s.state.cards[extra['first'].object_id].zone)
        self.finish(s, private)

    def test_partial_target_legality_keeps_bonus_and_does_not_touch_new_source_incarnation(self):
        for first_invalid in (False, True):
            with self.subTest(first_invalid=first_invalid):
                s, spell, private, extra = self.board('Ancient Animus')
                self.cast_preparation(s, spell, extra)
                if first_invalid:
                    old = extra['first'].logical_object_id
                    self.cast(s, 'A', extra['blink'], [extra['first'].ref]);self.resolve_top(s)
                    self.assertNotEqual(old, s.state.cards[extra['first'].object_id].logical_object_id)
                else:
                    self.cast(s, 'B', extra['bounce'], [extra['second'].ref]);self.resolve_top(s)
                s = self.replay(s, load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual([], self.damage(s))
                self.assertEqual(0 if first_invalid else 1, s.state.cards[extra['first'].object_id].counters.get('+1/+1', 0))
                self.finish(s, private)

    def test_prohibited_counter_does_not_inflate_following_fight_power(self):
        s, spell, private, extra = self.board('Hog-Monkey Rampage', prohibit=True)
        self.cast_preparation(s, spell, extra);s = self.replay(s, load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(0, s.state.cards[extra['first'].object_id].counters.get('+1/+1', 0))
        self.assertEqual([4], self.damage(s));self.finish(s, private)

    def test_payload_minimum_requires_preparation_and_distinct_power_authority_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s, spell, private, extra = self.board('Duel for Dominance')
        p = next(p for p in s.engine.semantics.programs_for_oracle(spell.oracle_id) if CAP in p.capability_dependencies)
        for missing in (CAP, 'condition.characteristics.public_power_aggregate'):
            deps = tuple(c for c in p.capability_dependencies if c != missing)
            changed = replace(p, coverage=(), capability_dependencies=deps, capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:' + missing, result['blockers'])

    def test_fresh_changed_power_witness_kills_forcing_the_initial_true_branch(self):
        self.changed_power()
        with patch('quorune.conditional_target_preparation.preparation_condition_matches', return_value=True):
            with self.assertRaisesRegex(AssertionError, 'current-preparation-predicate'):self.changed_power()
