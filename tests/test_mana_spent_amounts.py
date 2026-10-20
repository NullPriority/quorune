from __future__ import annotations

"""Converge rulings: actual paid colors, with zero for an uncast spell copy."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.mana_spent_amounts import MANA_COLOR_AMOUNT_CAPABILITY, resolve_spent_mana_color_amount
from quorune.oracle_ir import compile_oracle_card
from quorune.preflight import card_semantic_status
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec
from scripts.build_test_database import build_fixture_database
import test_compound_damage_recipients as controls

NAMES = ('Painful Truths', 'Radiant Flames', 'Kaleidoscorch',
         'Infuse with the Elements', 'Brilliant Spectrum', "Roilmage's Trick")


class ManaSpentAmountValueTests(unittest.TestCase):
    def test_versioned_color_count_rejects_noncolors_duplicates_and_unbound_readers(self):
        spec = ScalarEffectAmountSpec(ScalarAmountOrigin.CAST_MANA_COLOR_COUNT, schema_version=6)
        self.assertEqual(spec, ScalarEffectAmountSpec.from_dict(spec.to_dict()))
        for colors, count in (((), 0), (('B',), 1), (('W', 'U', 'B', 'R', 'G'), 5)):
            self.assertEqual(count, resolve_spent_mana_color_amount(SimpleNamespace(kind='spell', mana_colors_spent=colors)))
        for colors in (None, ['C'], ['W', 'W'], [''], ['WU'], [True], [{}]):
            with self.subTest(colors=colors), self.assertRaises(ValueError):
                resolve_spent_mana_color_amount(SimpleNamespace(kind='spell', mana_colors_spent=colors))
        with self.assertRaises(ValueError):
            resolve_spent_mana_color_amount(SimpleNamespace(kind='trigger', mana_colors_spent=['W']))
        self.assertEqual(0, resolve_spent_mana_color_amount(SimpleNamespace(kind='spell_copy', mana_colors_spent=())))
        with self.assertRaises(ValueError):
            resolve_spent_mana_color_amount(SimpleNamespace(kind='spell_copy', mana_colors_spent=['W']))
        for changed in ({'schema_version': 1}, {'characteristic': 'power'}, {'unknown': 1}, {'history_fact': 'cards_drawn'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                ScalarEffectAmountSpec.from_dict({**spec.to_dict(), **changed})
        from quorune.mana_spent_amounts import mana_color_amount_runtime_dependencies, MANA_COLOR_AMOUNT_MECHANIC
        with self.assertRaises(ValueError):
            mana_color_amount_runtime_dependencies(SimpleNamespace(
                effects=({'op': 'draw', 'count': spec.to_dict()},),
                coverage=('scalar-effect-amount', MANA_COLOR_AMOUNT_MECHANIC)))


class ManaSpentAmountRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.CompoundDamageRuntimeTests.session
    add = controls.CompoundDamageRuntimeTests.add
    prepare = controls.CompoundDamageRuntimeTests.prepare
    checkpoint = controls.CompoundDamageRuntimeTests.checkpoint
    cast = controls.CompoundDamageRuntimeTests.cast
    resolve = controls.CompoundDamageRuntimeTests.resolve
    replay = controls.CompoundDamageRuntimeTests.replay
    binding = controls.CompoundDamageRuntimeTests.binding
    actions = controls.CompoundDamageRuntimeTests.actions
    action = controls.CompoundDamageRuntimeTests.action
    private_replay = controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'mana-spent.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/mana-color-result-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Spent-color witnesses', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def manual_cast(self, session, source, payment, *, targets=(), option=None):
        source = session.state.cards[source.object_id]
        action = self.action(session, source)
        command = {'action_id': action['id'], 'pay': 'manual', 'payment': dict(payment), 'targets': list(targets)}
        if option is not None:
            command['cost_option'] = option
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        return next(item for item in session.state.stack if item.card_object_id == source.object_id)

    def test_original_spell_consumers_keep_nonempty_programs_and_whole_source_admission(self):
        for index, name in enumerate(NAMES):
            session = self.session(732000 + index)
            source = self.add(session.engine, name, zone='hand')
            card = self.db.lookup(name)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, (name, ir.material_residuals))
            node = next(node for node in ir.faces[0].nodes if node.template_id == 'spent-mana-color-count-result-v1')
            self.assertEqual(node.text, card.oracle_text[node.span.start:node.span.end])
            self.assertTrue(any(program.effects and MANA_COLOR_AMOUNT_CAPABILITY in program.capability_dependencies
                for program in session.engine.semantics.programs_for_oracle(card.oracle_id)), name)
            binding = self.binding(session, source)
            self.assertTrue(binding['strict_capability_ready'], (name, binding))
            preflight = card_semantic_status(card, session.engine.semantics, db=self.db,
                capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('fully_playable', preflight['status'], (name, preflight['unresolved']))

    def test_actual_painful_truths_uses_distinct_paid_colors_and_tax_without_overpay(self):
        for payment in ({'C': 2, 'B': 1}, {'W': 1, 'U': 1, 'B': 1}, {'W': 1, 'U': 1, 'B': 1, 'R': 1}):
            count = len(set(payment) - {'C'})
            session = self.session(732020 + count)
            source = self.add(session.engine, 'Painful Truths', zone='hand')
            if count == 4:
                sphere = self.add(session.engine, 'Sphere of Resistance', seat='B')
                self.assertTrue(self.binding(session, sphere)['strict_capability_ready'])
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            self.prepare(session, mana={**payment, 'G': 1})
            self.checkpoint(session)
            action = self.action(session, source)
            before = authoritative_state_hash(session.state)
            overpaid = session.act('pilot:A', {'action_id': action['id'], 'pay': 'manual', 'payment': {**payment, 'G': 1}})
            self.assertFalse(overpaid.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
            hand = len(session.state.players['A'].zones['hand'])
            item = self.manual_cast(session, source, payment)
            self.assertEqual(count, len(item.mana_colors_spent))
            self.assertEqual(1, session.state.players['A'].mana_pool['G'])
            session = self.replay(session, load=True)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(hand - 1 + count, len(session.state.players['A'].zones['hand']))
            self.assertEqual([40 - count, 40, 40, 40], [session.state.players[seat].life for seat in 'ABCD'])
            self.private_replay(session, private)

    def test_actual_spell_copy_uses_zero_spent_colors_and_original_keeps_three(self):
        session = self.session(732040)
        source = self.add(session.engine, 'Painful Truths', zone='hand')
        copy = self.add(session.engine, 'Reverberate', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['B'].mana_pool['R'] = 2
        self.prepare(session, mana={'W': 1, 'U': 1, 'B': 1})
        self.checkpoint(session)
        hand_a = len(session.state.players['A'].zones['hand'])
        hand_b = len(session.state.players['B'].zones['hand'])
        original = self.manual_cast(session, source, {'W': 1, 'U': 1, 'B': 1})
        self.cast(session, 'B', copy, [original.ref])
        pending = self.resolve(session)
        if pending is not None:
            self.assertEqual('semantic.choice', pending.kind)
            self.assertTrue(session.act('pilot:B', {'action_id': 'choose', 'targets': []}).ok)
            self.assertIsNone(self.resolve(session))
        self.assertEqual([37, 40, 40, 40], [session.state.players[seat].life for seat in 'ABCD'])
        self.assertEqual(hand_a + 2, len(session.state.players['A'].zones['hand']))
        self.assertEqual(hand_b - 1, len(session.state.players['B'].zones['hand']))
        self.assertEqual(2, len(session.engine._current_turn_history('spell_cast')))
        self.private_replay(session, private)

    def test_fresh_native_painful_truths_kills_omitted_spent_color_amount(self):
        method = 'test_actual_painful_truths_uses_distinct_paid_colors_and_tax_without_overpay'
        with patch('quorune.mana_spent_amounts.resolve_spent_mana_color_amount', return_value=0):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_actual_kaleidoscorch_normal_and_flashback_payments_use_two_and_five_colors(self):
        from quorune.flashback import FLASHBACK_CAST_OPTION_ID
        session = self.session(732050)
        source = self.add(session.engine, 'Kaleidoscorch', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'W': 1, 'U': 2, 'B': 1, 'R': 2, 'G': 1})
        self.checkpoint(session)
        item = self.manual_cast(session, source, {'U': 1, 'R': 1}, targets=['D'])
        self.assertEqual(2, len(item.mana_colors_spent))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(38, session.state.players['D'].life)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        source = session.state.cards[source.object_id]
        action = self.action(session, source)
        option = next(row['id'] for row in action['cost_options'] if row['id'] == FLASHBACK_CAST_OPTION_ID)
        item = self.manual_cast(session, source, {'W': 1, 'U': 1, 'B': 1, 'R': 1, 'G': 1},
            targets=['D'], option=option)
        self.assertEqual(5, len(item.mana_colors_spent))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([40, 40, 40, 33], [session.state.players[seat].life for seat in 'ABCD'])
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session, private)

    def test_actual_radiant_flames_copy_zero_preserves_paid_damage_to_all_creatures_only(self):
        session = self.session(732060)
        source = self.add(session.engine, 'Radiant Flames', zone='hand')
        copy = self.add(session.engine, 'Reverberate', seat='B', zone='hand')
        bodies = [self.add(session.engine, 'Generic Bound Body', seat=seat, ref='flames-' + seat) for seat in 'ABCD']
        bodies[1].counters['hexproof'] = 1
        bodies[3].counters['flying'] = 1
        phased = self.add(session.engine, 'Generic Bound Body', seat='D', ref='flames-phased')
        phased.phased_out = True
        weak = self.add(session.engine, 'Grizzly Bears', seat='C')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['B'].mana_pool['R'] = 2
        self.prepare(session, mana={'W': 1, 'B': 1, 'R': 1})
        self.checkpoint(session)
        original = self.manual_cast(session, source, {'W': 1, 'B': 1, 'R': 1})
        self.cast(session, 'B', copy, [original.ref])
        pending = self.resolve(session)
        if pending is not None:
            self.assertEqual('semantic.choice', pending.kind)
            self.assertTrue(session.act('pilot:B', {'action_id': 'choose', 'targets': []}).ok)
            self.assertIsNone(self.resolve(session))
        for body in bodies:
            self.assertEqual('battlefield', session.state.cards[body.object_id].zone)
            self.assertEqual(3, session.state.cards[body.object_id].marked_damage)
        self.assertEqual('graveyard', session.state.cards[weak.object_id].zone)
        self.assertEqual(0, session.state.cards[phased.object_id].marked_damage)
        self.assertEqual([40] * 4, [session.state.players[seat].life for seat in 'ABCD'])
        self.private_replay(session, private)

    def test_actual_infuse_copy_zero_still_grants_trample_and_paid_counters_use_replacement(self):
        session = self.session(732070)
        source = self.add(session.engine, 'Infuse with the Elements', zone='hand')
        copy = self.add(session.engine, 'Reverberate', seat='B', zone='hand')
        target = self.add(session.engine, 'Generic Bound Body', ref='infuse-paid-target')
        other = self.add(session.engine, 'Generic Bound Body', seat='D', ref='infuse-copy-target')
        scales = self.add(session.engine, 'Hardened Scales')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assertTrue(self.binding(session, scales)['strict_capability_ready'])
        session.state.players['B'].mana_pool['R'] = 2
        self.prepare(session, mana={'W': 1, 'U': 1, 'B': 1, 'G': 1})
        self.checkpoint(session)
        original = self.manual_cast(session, source, {'W': 1, 'U': 1, 'B': 1, 'G': 1}, targets=[target.ref])
        self.cast(session, 'B', copy, [original.ref])
        pending = self.resolve(session)
        self.assertEqual('semantic.choice', pending.kind)
        session = self.replay(session, load=True)
        command = {'action_id': 'choose', 'targets': [other.ref]}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:B', command).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(5, session.state.cards[target.object_id].counters.get('+1/+1', 0))
        self.assertEqual(0, session.state.cards[other.object_id].counters.get('+1/+1', 0))
        for body in (target, other):
            self.assertIn('trample', session.engine._combat_keywords(session.state.cards[body.object_id]))
        self.private_replay(session, private)

    def test_actual_roilmage_copy_zero_keeps_each_controller_scope_and_both_fixed_draws(self):
        session = self.session(732080)
        source = self.add(session.engine, "Roilmage's Trick", zone='hand')
        copy = self.add(session.engine, 'Reverberate', seat='B', zone='hand')
        bodies = [self.add(session.engine, 'Generic Bound Body', seat=seat, ref='roilmage-' + seat) for seat in 'ABCD']
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['B'].mana_pool['R'] = 2
        self.prepare(session, mana={'W': 1, 'U': 1, 'B': 1, 'R': 1})
        self.checkpoint(session)
        hand_a = len(session.state.players['A'].zones['hand'])
        hand_b = len(session.state.players['B'].zones['hand'])
        original = self.manual_cast(session, source, {'W': 1, 'U': 1, 'B': 1, 'R': 1})
        self.cast(session, 'B', copy, [original.ref])
        pending = self.resolve(session)
        if pending is not None:
            self.assertEqual('semantic.choice', pending.kind)
            self.assertTrue(session.act('pilot:B', {'action_id': 'choose', 'targets': []}).ok)
            self.assertIsNone(self.resolve(session))
        self.assertEqual([2, -2, -2, -2], [session.engine._numeric_stat(body.object_id, 'power') for body in bodies])
        self.assertEqual([6] * 4, [session.engine._numeric_stat(body.object_id, 'toughness') for body in bodies])
        self.assertEqual(hand_a, len(session.state.players['A'].zones['hand']))
        self.assertEqual(hand_b, len(session.state.players['B'].zones['hand']))
        self.private_replay(session, private)

    def test_brilliant_spectrum_zero_free_base_seam_and_four_colors_keep_fixed_private_discard(self):
        from quorune.impulse_access import grant_temporary_cast_permission
        from quorune.impulse_access_model import TemporaryCastPermissionGrant, ImpulseAccessDuration
        for free in (False, True):
            session = self.session(732090 + free)
            source = self.add(session.engine, 'Brilliant Spectrum', zone='hand')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            if free:
                grant_temporary_cast_permission(session.engine, card=source,
                    grant=TemporaryCastPermissionGrant(player='A', duration=ImpulseAccessDuration.END_OF_TURN,
                        not_before_turn_sequence=None, without_mana_cost=True, source='existing-free-base-seam'),
                    reason='Existing typed free-cost seam; no new free-cast producer claim')
            payment = {} if free else {'W': 1, 'U': 1, 'B': 1, 'R': 1}
            self.prepare(session, mana=payment)
            self.checkpoint(session)
            before_hand = len(session.state.players['A'].zones['hand'])
            option = None
            if free:
                option = next(row['id'] for row in self.action(session, source)['cost_options'] if not any(row['requirements'].values()))
            self.manual_cast(session, source, payment, option=option)
            pending = self.resolve(session)
            self.assertEqual('choice.apnap', pending.kind)
            self.assertEqual(['pilot:A'], session.pending_principals())
            self.assertEqual(before_hand - 1 + len(payment), len(session.state.players['A'].zones['hand']))
            choices = [session.state.cards[object_id].ref for object_id in session.state.players['A'].zones['hand']][:2]
            self.assertEqual(2, len(choices))
            command = {'action_id': 'choose', 'cards': choices}
            before = authoritative_state_hash(session.state)
            for actor, bad in (('pilot:B', command), ('pilot:A', {'action_id': 'choose', 'cards': [choices[0], choices[0]]}),
                               ('pilot:A', {'action_id': 'choose', 'cards': [choices[0], private.ref]})):
                self.assertFalse(session.act(actor, bad).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
            session = self.replay(session, load=True)
            self.assertTrue(session.act('pilot:A', command).ok)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(before_hand - 3 + len(payment), len(session.state.players['A'].zones['hand']))
            self.private_replay(session, private)

    def test_source_grammar_keeps_unsupported_entry_antecedents_targets_and_siblings_residual(self):
        card = self.db.lookup('Painful Truths')
        for changed in (
            replace(card, oracle_text=card.oracle_text.replace('this spell', 'that spell')),
            replace(card, oracle_text=card.oracle_text + '\nWhenever a player sneezes, draw a card.'),
            replace(card, oracle_text=card.oracle_text + ' Then discard X cards.'),
            replace(card, mana_cost='{X}{B}'),
            replace(card, oracle_text='Converge — Destroy up to X target artifacts, where X is the number of colors of mana spent to cast this spell.'),
            replace(card, type_line='Creature', oracle_text='When this creature enters, draw X cards, where X is the number of colors of mana spent to cast this spell.'),
        ):
            self.assertNotEqual('exact', compile_oracle_card(changed, capability_registry=self.registry,
                capability_profile='commander_review').status)
        plain = replace(card, oracle_text=card.oracle_text.removeprefix('Converge — '))
        self.assertEqual('exact', compile_oracle_card(plain, capability_registry=self.registry,
            capability_profile='commander_review').status)

    def test_runtime_cannot_omit_spent_color_or_scalar_authority_or_change_to_a_trigger(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(732110)
        source = self.add(session.engine, 'Painful Truths', zone='hand')
        program = next(program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if program.event == 'resolve')
        for omitted in (MANA_COLOR_AMOUNT_CAPABILITY, 'quantity_expression.scalar_effect_amount'):
            dependencies = tuple(cap for cap in program.capability_dependencies if cap != omitted)
            changed = replace(program, capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:' + omitted, bound['blockers'])
        bound = bind_semantic_program_runtime(replace(program, event='creature.enter.self'),
            capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('resolving spell', str(bound['blockers']))

    def test_fresh_original_admission_kills_omitted_spent_color_compiler(self):
        method = 'test_original_spell_consumers_keep_nonempty_programs_and_whole_source_admission'
        with patch('quorune.compiler.mana_color_result_nodes.mana_color_result_node', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_fresh_native_copy_kills_inherited_original_spending(self):
        from quorune.mana_spent_amounts import resolve_spent_mana_color_amount
        method = 'test_actual_spell_copy_uses_zero_spent_colors_and_original_keeps_three'
        def inherited(item):
            return 3 if item.kind == 'spell_copy' else resolve_spent_mana_color_amount(item)
        with patch('quorune.mana_spent_amounts.resolve_spent_mana_color_amount', side_effect=inherited):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
