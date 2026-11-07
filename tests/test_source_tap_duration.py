from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_qualified_public_history as history
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import _reviewed_effect_template
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.source_tap_effect import SOURCE_TAP_EFFECT_CAPABILITY, SOURCE_TAP_EFFECT_FIELD, source_tap_effect_dependencies
from scripts.build_test_database import build_fixture_database


class SourceTapCompilerTests(unittest.TestCase):
    def test_source_tap_grammar_requires_the_original_source_and_a_closed_result(self):
        context = dict(card_name='Fixture', source_is_permanent=True, source_card_types=('artifact',))
        clause = 'Target creature gets +1/-1 for as long as this artifact remains tapped.'
        template, effects, schema, _ = _reviewed_effect_template(clause, **context)
        self.assertIsNotNone(template)
        self.assertIs(effects[0][SOURCE_TAP_EFFECT_FIELD], True)
        self.assertEqual(1, schema['count'])
        self.assertEqual((SOURCE_TAP_EFFECT_CAPABILITY,), source_tap_effect_dependencies(effects))
        with self.assertRaises(ValueError):
            source_tap_effect_dependencies(({**effects[0], SOURCE_TAP_EFFECT_FIELD: 1},))
        with self.assertRaises(ValueError):
            source_tap_effect_dependencies(({**effects[0], 'until_controller_next_turn': True},))
        for text in (
            clause.replace('this artifact', 'that artifact'),
            clause.replace('this artifact', 'this land'),
            clause.replace('remains tapped', 'remains tapped and has no counters on it'),
        ):
            self.assertIsNone(_reviewed_effect_template(text, **context)[0], text)
        self.assertIsNone(_reviewed_effect_template(clause, **{**context, 'source_is_permanent': False})[0])

    def test_nontoken_artifact_target_has_explicit_boolean_and_source_exclusion(self):
        spec = direct_permanent_target_spec('Another target nontoken artifact you control')
        self.assertIsNotNone(spec)
        schema = spec.to_target_schema()
        self.assertEqual(['artifact'], schema['types_any'])
        self.assertEqual('you', schema['controller_relation'])
        self.assertIs(schema['token'], False)
        self.assertIs(schema['source_exclusion'], True)
        self.assertEqual(spec, DirectPermanentTargetSpec.from_target_schema(schema))
        with self.assertRaises(ValueError):
            DirectPermanentTargetSpec.from_target_schema({**schema, 'token': 0})
        self.assertIsNone(direct_permanent_target_spec('Target nontoken artifact or creature'))


class SourceTapNativeTests(InheritedNativeFixture, unittest.TestCase):
    until = history.QualifiedPublicHistoryNativeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        fixtures = ROOT / 'tests/fixtures'
        path = Path(cls.temporary.name) / 'source-tap-duration.sqlite3'
        build_fixture_database([fixtures / 'bound-effect-program-cards.json',
                                fixtures / 'source-tap-duration-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Tapped source witnesses', [
            DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Generic Bound Commander'])

    def activation(self, session, seat, card):
        self.priority(session, seat)
        packet = session.packet('pilot:' + seat, full=True)
        return next(row for row in packet['decision']['legal_actions'] if row['id'].startswith(f'activate:{card.ref}:'))

    def activate(self, session, seat, card, targets=()):
        action = self.activation(session, seat, card)
        command = {'action_id': action['id'], 'pay': 'auto'}
        if targets:
            command['targets'] = [target.ref for target in targets]
        result = session.act('pilot:' + seat, command)
        self.assertTrue(result.ok, result.summary)

    def stats(self, session, card):
        current = session.state.cards[card.object_id]
        effective = session.engine._effective_card_data(current)
        return int(effective['power']), int(effective['toughness'])

    def test_actual_flowstone_duration_survives_cleanup_and_never_restarts_after_untap(self):
        s = self.session(61177001)
        source = self.add(s.engine, 'Flowstone Armor')
        target = self.add(s.engine, 'Generic Bound Body', seat='D')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        key = self.add(s.engine, 'Voltaic Key', seat='B')
        tapper = self.add(s.engine, 'Icy Manipulator', seat='B')
        for index in range(2):
            self.add(s.engine, 'Generic Bound Plains', seat='B', ref=f'timing-plains-{index}')
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 3})
        self.checkpoint(s)
        initial = self.stats(s, target)
        action = self.activation(s, 'A', source)
        refs = action['target_schema']['legal_refs']
        self.assertIn(target.ref, refs)
        self.assertNotIn(key.ref, refs)
        before = authoritative_state_hash(s.state)
        for actor, ref in (('B', target.ref), ('A', key.ref)):
            rejected = s.act('pilot:' + actor, {'action_id': action['id'], 'targets': [ref], 'pay': 'auto'})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
        self.activate(s, 'A', source, (target,))
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual(0, s.state.players['A'].mana_pool['C'])
        self.resolve_top(s)
        bonus = initial[0] + 1, initial[1] - 1
        self.assertEqual(bonus, self.stats(s, target))
        self.until(s, lambda game: game.state.active_player == 'B' and game.state.pending_decision.kind == 'priority')
        self.assertEqual(bonus, self.stats(s, target))
        s = self.replay(s, load=True)
        for viewer in 'ABD':
            packet = s.packet('pilot:' + viewer, full=True)
            self.assertNotIn(private.ref, str(packet))
        self.activate(s, 'B', key, (source,))
        self.resolve_top(s)
        self.assertFalse(s.state.cards[source.object_id].tapped)
        self.assertEqual(initial, self.stats(s, target))
        self.activate(s, 'B', tapper, (source,))
        self.resolve_top(s)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual(initial, self.stats(s, target))
        self.replay(s, load=True)

    def test_actual_flowstone_pending_activation_cannot_restart_before_resolution(self):
        s = self.session(61177002)
        source = self.add(s.engine, 'Flowstone Armor')
        target = self.add(s.engine, 'Generic Bound Body', seat='D')
        key = self.add(s.engine, 'Voltaic Key', seat='B')
        tapper = self.add(s.engine, 'Icy Manipulator', seat='B')
        s.state.players['B'].mana_pool['C'] = 2
        self.prepare(s, mana={'C': 3})
        self.checkpoint(s)
        initial = self.stats(s, target)
        self.activate(s, 'A', source, (target,))
        for instrument in (key, tapper):
            self.activate(s, 'B', instrument, (source,))
            self.resolve_top(s)
        self.assertEqual(1, len(s.state.stack))
        self.assertTrue(s.state.cards[source.object_id].tapped)
        s = self.replay(s, load=True)
        self.resolve_top(s)
        self.assertEqual(initial, self.stats(s, target))
        self.assertFalse(s.state.continuous_effects)
        self.replay(s, load=True)

    def test_actual_everglove_bonus_keeps_its_recipient_after_subtype_change(self):
        s = self.session(61177003)
        source = self.add(s.engine, 'Everglove Courier')
        elf = self.add(s.engine, 'Llanowar Elves', seat='D')
        other = self.add(s.engine, 'Generic Bound Body', seat='D')
        frog = self.add(s.engine, 'Turn to Frog', seat='B', zone='hand')
        s.state.players['B'].mana_pool.update({'C': 1, 'U': 1})
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 2, 'G': 1})
        s.state.players['A'].turns_begun = 1
        self.checkpoint(s)
        refs = self.activation(s, 'A', source)['target_schema']['legal_refs']
        self.assertIn(elf.ref, refs)
        self.assertNotIn(other.ref, refs)
        self.activate(s, 'A', source, (elf,))
        self.resolve_top(s)
        self.assertEqual((3, 3), self.stats(s, elf))
        self.assertIn('trample', s.engine._combat_keywords(s.state.cards[elf.object_id]))
        self.cast_for(s, 'B', frog, [elf.ref])
        self.resolve_top(s)
        self.assertIn('Frog', s.engine._effective_card_data(s.state.cards[elf.object_id])['type_line'])
        self.assertEqual((3, 3), self.stats(s, elf))
        self.assertNotIn('trample', s.engine._combat_keywords(s.state.cards[elf.object_id]))
        self.replay(s, load=True)

    def test_actual_thran_weaponry_locks_only_the_creatures_present_at_resolution(self):
        s = self.session(61177004)
        source = self.add(s.engine, 'Thran Weaponry')
        present = self.add(s.engine, 'Generic Bound Body', seat='D')
        late = self.add(s.engine, 'Llanowar Elves', zone='hand')
        self.assert_closed(s, source)
        self.prepare(s, mana={'G': 3})
        self.checkpoint(s)
        original = self.stats(s, present)
        self.activate(s, 'A', source)
        self.resolve_top(s)
        expected = original[0] + 2, original[1] + 2
        self.assertEqual(expected, self.stats(s, present))
        self.cast_for(s, 'A', late)
        self.resolve_top(s)
        self.assertEqual((1, 1), self.stats(s, late))
        self.assertEqual(expected, self.stats(s, present))
        self.replay(s, load=True)

    def test_actual_blackstaff_animates_another_nontoken_artifact_until_source_untaps(self):
        s = self.session(61177006)
        source = self.add(s.engine, 'The Blackstaff of Waterdeep')
        target = self.add(s.engine, 'Voltaic Key', ref='original-controlled-key')
        rival = self.add(s.engine, 'Voltaic Key', seat='B', ref='rival-key')
        token = s.engine.create_token('A', name='Calibration Artifact',
                                      characteristics={'type_line': 'Token Artifact'})[0]
        s.state.players['B'].mana_pool['C'] = 1
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 1, 'U': 1})
        self.checkpoint(s)
        action = self.activation(s, 'A', source)
        refs = action['target_schema']['legal_refs']
        self.assertIn(target.ref, refs)
        self.assertTrue({source.ref, rival.ref, token}.isdisjoint(refs))
        before = authoritative_state_hash(s.state)
        rejected = s.act('pilot:A', {'action_id': action['id'], 'targets': [token], 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.activate(s, 'A', source, (target,))
        self.resolve_top(s)
        self.assertIn('Artifact Creature', s.engine._effective_card_data(s.state.cards[target.object_id])['type_line'])
        self.assertEqual((4, 4), self.stats(s, target))
        s = self.replay(s, load=True)
        self.activate(s, 'B', rival, (source,))
        self.resolve_top(s)
        self.assertEqual('Artifact', s.engine._effective_card_data(s.state.cards[target.object_id])['type_line'])
        self.replay(s, load=True)

    def test_source_tap_runtime_binding_requires_the_declared_duration(self):
        s = self.session(61177005)
        source = self.add(s.engine, 'Flowstone Armor')
        program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
                       if SOURCE_TAP_EFFECT_CAPABILITY in p.capability_dependencies)
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != SOURCE_TAP_EFFECT_CAPABILITY)
        changed = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
        binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(binding['strict'])
        self.assertIn(f'capability:undeclared_runtime_dependency:{SOURCE_TAP_EFFECT_CAPABILITY}', binding['blockers'])

    def test_tapped_only_check_mutation_cannot_preserve_an_expired_activation(self):
        witness = type(self)('test_actual_flowstone_pending_activation_cannot_restart_before_resolution')
        with patch('quorune.control_effects._duration_condition', side_effect=lambda card, *_args, **_kwargs: card.tapped):
            with self.assertRaises(AssertionError):
                witness.test_actual_flowstone_pending_activation_cannot_restart_before_resolution()
