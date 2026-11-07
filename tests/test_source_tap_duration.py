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
from quorune.semantic_runtime.untap_steps import current_untap_step_plan
from quorune.source_tap_effect import (
    SOURCE_TAP_EFFECT_CAPABILITY, SOURCE_TAP_EFFECT_FIELD, SOURCE_TAP_UNTAP_CAPABILITY,
    source_tap_effect_dependencies,
)
from quorune.source_continuity import SourceContinuitySnapshot
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.declaration_rule_effects import continuous_journal_effect_from_dict
from quorune.source_untap_rule_model import ResolvedSourceUntapRestraint
from scripts.build_test_database import build_fixture_database


class SourceTapCompilerTests(unittest.TestCase):
    def test_source_untap_restraint_descriptor_requires_closed_tap_shapes(self):
        context = dict(card_name='Fixture', source_is_permanent=True, source_card_types=('artifact',))
        direct = "Tap target artifact. It doesn't untap during its controller's untap step for as long as this artifact remains tapped."
        mass = "Tap all other artifacts. They don't untap during their controllers' untap steps for as long as Fixture remains tapped."
        for clause, operation in ((direct, 'tap'), (mass, 'set_public_tap_state')):
            with self.subTest(operation=operation):
                template, effects, schema, _ = _reviewed_effect_template(clause, **context)
                self.assertIsNotNone(template)
                self.assertEqual(1, len(effects))
                self.assertEqual(operation, effects[0]['op'])
                self.assertIs(effects[0][SOURCE_TAP_EFFECT_FIELD], True)
                self.assertEqual((SOURCE_TAP_UNTAP_CAPABILITY,), source_tap_effect_dependencies(effects))
                self.assertEqual(schema is None, operation == 'set_public_tap_state')
                with self.assertRaises(ValueError):
                    source_tap_effect_dependencies(({**effects[0], SOURCE_TAP_EFFECT_FIELD: False},))
        for wrong in (
            direct.replace('this artifact', 'that artifact'),
            direct.replace('Tap target', 'Untap target'),
            direct.replace('its controller\'s untap step', 'any untap step'),
            mass.replace('other artifacts', 'other artifacts and lands'),
        ):
            self.assertIsNone(_reviewed_effect_template(wrong, **context)[0], wrong)
        self.assertIsNone(_reviewed_effect_template(direct, **{**context, 'source_is_permanent': False})[0])

    def test_source_untap_restraint_journal_rejects_duplicate_or_malformed_incarnations(self):
        effect = ResolvedSourceUntapRestraint(
            'CE1', 3, ContinuousObjectIdentity('source', 'source-1'), SourceContinuitySnapshot(0, 1, 0),
            (ContinuousObjectIdentity('artifact', 'artifact-1'),),
        )
        raw = effect.to_dict()
        self.assertEqual(effect, continuous_journal_effect_from_dict(raw))
        for patch_fields in (
            {'schema_version': True}, {'timestamp': False}, {'duration_history': {}}, {'locked_objects': []},
            {'locked_objects': raw['locked_objects'] * 2},
        ):
            with self.assertRaises(ValueError):
                continuous_journal_effect_from_dict({**raw, **patch_fields})

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

    def test_actual_rust_tick_snapshots_untap_step_before_its_source_untaps(self):
        s = self.session(50277201)
        source = self.add(s.engine, 'Rust Tick')
        held = self.add(s.engine, 'Icy Manipulator')
        wrong = self.add(s.engine, 'Generic Bound Body', seat='D')
        private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand')
        next_turn = self.add(s.engine, 'Time Warp', zone='hand')
        held.tapped = True
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 4, 'U': 3})
        s.state.players['A'].turns_begun = 1
        self.checkpoint(s)
        action = self.activation(s, 'A', source)
        self.assertIn(held.ref, action['target_schema']['legal_refs'])
        self.assertNotIn(wrong.ref, action['target_schema']['legal_refs'])
        before = authoritative_state_hash(s.state)
        for seat, ref in (('B', held.ref), ('A', wrong.ref)):
            result = s.act(f'pilot:{seat}', {'action_id': action['id'], 'targets': [ref], 'pay': 'auto'})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
        self.activate(s, 'A', source, (held,))
        self.resolve_top(s)
        self.cast_for(s, 'A', next_turn, ['A'])
        self.resolve_top(s)
        self.until(s, lambda game: game.state.pending_decision.kind == 'untap.optional_source')
        s = self.replay(s, load=True)
        self.assertEqual('A', s.state.active_player)
        for seat in 'ABD':
            self.assertNotIn(private.ref, str(s.packet(f'pilot:{seat}', full=True)))
        for seat in 'BCD':
            self.assertIsNone(s.packet(f'pilot:{seat}', full=True)['decision'])
        result = s.act('pilot:A', {'action_id': 'choose', 'refs': [source.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(s.state.cards[source.object_id].tapped)
        self.assertTrue(s.state.cards[held.object_id].tapped)
        self.assertFalse(any(isinstance(effect, ResolvedSourceUntapRestraint) for effect in s.state.continuous_effects))
        self.replay(s, load=True)

    def test_actual_rust_tick_survives_other_untaps_and_follows_new_controller(self):
        s = self.session(50277202)
        source = self.add(s.engine, 'Rust Tick')
        held = self.add(s.engine, 'Icy Manipulator', seat='B', ref='held-artifact')
        key = self.add(s.engine, 'Voltaic Key', seat='D')
        tapper = self.add(s.engine, 'Icy Manipulator', seat='D', ref='retap-artifact')
        theft = self.add(s.engine, 'Steal Artifact', zone='hand')
        self.assert_closed(s, theft)
        self.prepare(s, mana={'C': 3, 'U': 3})
        s.state.players['A'].turns_begun = 1
        s.state.players['D'].mana_pool['C'] = 2
        self.checkpoint(s)
        self.activate(s, 'A', source, (held,))
        self.resolve_top(s)
        self.activate(s, 'D', key, (held,))
        self.resolve_top(s)
        self.assertFalse(s.state.cards[held.object_id].tapped)
        self.activate(s, 'D', tapper, (held,))
        self.resolve_top(s)
        self.cast_for(s, 'A', theft, [held.ref])
        self.resolve_top(s)
        self.assertEqual('A', s.state.cards[held.object_id].controller)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertTrue(s.state.cards[held.object_id].tapped)
        self.assertIn(held.object_id, current_untap_step_plan(s.engine, 'A').prohibited_object_ids)
        self.assertNotIn(held.object_id, current_untap_step_plan(s.engine, 'B').prohibited_object_ids)
        self.replay(s, load=True)

    def test_actual_rust_tick_can_begin_a_new_interval_during_resolution(self):
        s = self.session(50277208)
        source = self.add(s.engine, 'Rust Tick')
        key = self.add(s.engine, 'Voltaic Key', seat='B')
        self.prepare(s, mana={'C': 1})
        s.state.players['A'].turns_begun = 1
        s.state.players['B'].mana_pool['C'] = 1
        self.checkpoint(s)
        self.activate(s, 'A', source, (source,))
        self.activate(s, 'B', key, (source,))
        self.resolve_top(s)
        self.assertFalse(s.state.cards[source.object_id].tapped)
        self.resolve_top(s)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        plan = current_untap_step_plan(s.engine, 'A')
        self.assertIn(source.object_id, plan.prohibited_object_ids)
        self.assertNotIn(source.object_id, plan.optional_object_ids)
        self.replay(s, load=True)

    def test_actual_kill_switch_locks_the_original_other_artifact_set(self):
        s = self.session(50277203)
        source = self.add(s.engine, 'Kill Switch')
        held = self.add(s.engine, 'Icy Manipulator')
        rival = self.add(s.engine, 'Voltaic Key', seat='B')
        late = self.add(s.engine, 'Sky Diamond', zone='hand')
        wrong = self.add(s.engine, 'Generic Bound Body', seat='D')
        self.assert_closed(s, source)
        self.assert_closed(s, late)
        self.prepare(s, mana={'C': 4})
        self.checkpoint(s)
        self.activate(s, 'A', source)
        self.resolve_top(s)
        effect = next(effect for effect in s.state.continuous_effects if isinstance(effect, ResolvedSourceUntapRestraint))
        self.assertEqual({held.logical_object_id, rival.logical_object_id}, {o.logical_object_id for o in effect.locked_objects})
        self.assertFalse(s.state.cards[wrong.object_id].tapped)
        self.cast_for(s, 'A', late)
        self.resolve_top(s)
        self.assertTrue(s.state.cards[late.object_id].tapped)
        s = self.replay(s, load=True)
        self.until(s, lambda game: game.state.active_player == 'B' and game.state.pending_decision.kind == 'priority')
        self.assertTrue(s.state.cards[rival.object_id].tapped)
        plan = current_untap_step_plan(s.engine, 'A')
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertIn(held.object_id, plan.prohibited_object_ids)
        self.assertNotIn(late.object_id, plan.prohibited_object_ids)
        self.replay(s, load=True)

    def test_actual_kill_switch_still_taps_after_source_departure(self):
        s = self.session(50277204)
        source = self.add(s.engine, 'Kill Switch')
        held = self.add(s.engine, 'Voltaic Key', seat='B')
        answer = self.add(s.engine, 'Planar Incision', seat='B', zone='hand')
        original = source.logical_object_id
        self.prepare(s, mana={'C': 2})
        s.state.players['B'].mana_pool.update({'C': 1, 'U': 1})
        self.checkpoint(s)
        self.activate(s, 'A', source)
        self.cast_for(s, 'B', answer, [source.ref])
        self.resolve_top(s)
        returned = s.state.cards[source.object_id]
        self.assertEqual('battlefield', returned.zone)
        self.assertNotEqual(original, returned.logical_object_id)
        self.assertFalse(returned.tapped)
        self.assertFalse(s.state.cards[held.object_id].tapped)
        self.resolve_top(s)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertTrue(s.state.cards[held.object_id].tapped)
        self.assertFalse(any(isinstance(effect, ResolvedSourceUntapRestraint) for effect in s.state.continuous_effects))
        self.until(s, lambda game: game.state.active_player == 'B' and game.state.pending_decision.kind == 'priority')
        self.assertFalse(s.state.cards[held.object_id].tapped)
        self.replay(s, load=True)

    def test_actual_deluge_uses_the_trusted_plain_public_tap_set(self):
        s = self.session(50277209)
        spell = self.add(s.engine, 'Deluge', zone='hand')
        ground = self.add(s.engine, 'Generic Bound Body', seat='B')
        flying = self.add(s.engine, 'Wind Drake', seat='D')
        self.assert_closed(s, spell)
        self.prepare(s, mana={'C': 2, 'U': 1})
        self.checkpoint(s)
        self.cast_for(s, 'A', spell)
        self.resolve_top(s)
        self.assertTrue(s.state.cards[ground.object_id].tapped)
        self.assertFalse(s.state.cards[flying.object_id].tapped)
        self.replay(s, load=True)

    def test_actual_ice_floe_requires_a_ground_attacker_against_its_controller(self):
        s = self.session(50277206)
        source = self.add(s.engine, 'Ice Floe')
        ground = self.add(s.engine, 'Generic Bound Body', seat='B', ref='attacks-controller')
        other = self.add(s.engine, 'Generic Bound Body', seat='B', ref='attacks-other-player')
        flying = self.add(s.engine, 'Wind Drake', seat='B')
        self.assert_closed(s, source)
        self.prepare(s, mana={})
        self.checkpoint(s)
        self.until(s, lambda game: game.state.active_player == 'B' and game.state.pending_decision.kind == 'combat.attackers')
        result = s.act('pilot:B', {'action_id': 'attack', 'attackers': {ground.ref: 'A', other.ref: 'C', flying.ref: 'A'}})
        self.assertTrue(result.ok, result.summary)
        action = self.activation(s, 'A', source)
        self.assertEqual({ground.ref}, set(action['target_schema']['legal_refs']))
        before = authoritative_state_hash(s.state)
        rejected = s.act('pilot:A', {'action_id': action['id'], 'targets': [flying.ref], 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.activate(s, 'A', source, (ground,))
        self.resolve_top(s)
        self.assertIn(ground.object_id, s.state.combat.attackers)
        effect = next(effect for effect in s.state.continuous_effects if isinstance(effect, ResolvedSourceUntapRestraint))
        self.assertEqual((ContinuousObjectIdentity(ground.object_id, ground.logical_object_id),), effect.locked_objects)
        self.replay(s, load=True)

    def test_actual_thalakos_dreamsower_locks_a_target_after_its_damage_trigger(self):
        s = self.session(50277207)
        source = self.add(s.engine, 'Thalakos Dreamsower')
        held = self.add(s.engine, 'Generic Bound Body', seat='B')
        self.assert_closed(s, source)
        self.prepare(s, mana={})
        s.state.players['A'].turns_begun = 1
        self.checkpoint(s)
        self.attack(s, [(source, 'B')])
        self.until(s, lambda game: game.state.pending_decision.kind == 'semantic.target')
        s = self.replay(s, load=True)
        result = s.act('pilot:A', {'action_id': 'choose', 'targets': [held.ref]})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(s)
        self.assertTrue(s.state.cards[held.object_id].tapped)
        self.until(s, lambda game: game.state.active_player == 'B' and game.state.pending_decision.kind == 'priority')
        self.assertTrue(s.state.cards[held.object_id].tapped)
        self.replay(s, load=True)

    def test_omitting_resolved_restraint_from_untap_plan_is_detected(self):
        witness = type(self)('test_actual_rust_tick_snapshots_untap_step_before_its_source_untaps')
        with patch('quorune.rules.untap_permissions.resolved_restraint_step_participations', return_value=()):
            with self.assertRaises(AssertionError):
                witness.test_actual_rust_tick_snapshots_untap_step_before_its_source_untaps()

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

    def test_source_untap_restraint_requires_its_runtime_capability(self):
        s = self.session(50277205)
        for name in ('Rust Tick', 'Kill Switch'):
            source = self.add(s.engine, name)
            program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
                           if SOURCE_TAP_UNTAP_CAPABILITY in p.capability_dependencies)
            dependencies = tuple(cap for cap in program.capability_dependencies if cap != SOURCE_TAP_UNTAP_CAPABILITY)
            changed = replace(program, capability_dependencies=dependencies,
                              capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(binding['strict'])
            self.assertIn(f'capability:undeclared_runtime_dependency:{SOURCE_TAP_UNTAP_CAPABILITY}', binding['blockers'])

    def test_tapped_only_check_mutation_cannot_preserve_an_expired_activation(self):
        witness = type(self)('test_actual_flowstone_pending_activation_cannot_restart_before_resolution')
        with patch('quorune.control_effects._duration_condition', side_effect=lambda card, *_args, **_kwargs: card.tapped):
            with self.assertRaises(AssertionError):
                witness.test_actual_flowstone_pending_activation_cannot_restart_before_resolution()
