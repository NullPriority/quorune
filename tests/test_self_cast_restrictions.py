from __future__ import annotations

"""CR113.6e/601.2e: intrinsic cast conditions precede payment and cast facts."""

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.compiled_cast_timing import compiled_self_cast_restriction_reason
from quorune.continuous_effect_model import ContinuousOperation, Layer
from quorune.continuous_effect_state import (
    ResolutionEffectSource, ResolutionContinuousComponent, create_resolution_continuous_effect_components,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.self_cast_restrictions import (
    SELF_CAST_RESTRICTION_CAPABILITY, SelfCastRestrictionHandler,
)
from scripts.build_test_database import build_fixture_database
import test_compound_damage_recipients as controls
import test_temporary_keyword_removal as turn_controls


CREATURES = ('Illusory Angel', 'Hewed Stone Retainers', 'Skyshroud Condor')
NAMES = (*CREATURES, 'Blizzard')


class SelfCastRestrictionTests(unittest.TestCase):
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
    private_replay = controls.CompoundDamageRuntimeTests.private_replay
    advance = turn_controls.TemporaryKeywordRemovalRuntimeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'self-cast-restrictions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/self-cast-restriction-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Intrinsic casting conditions',
            [DeckEntry('Generic Bound Commander', 1, 'commander'),
             DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def offered(self, session, source):
        return any(row['id'] == 'cast:' + source.ref for row in self.actions(session))

    def priority(self, session, seat):
        for _ in range(8):
            if session.state.priority_player == seat:
                return
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Expected ordinary priority for ' + seat)

    def test_original_creatures_cannot_count_themselves_and_cast_after_real_prior_spell(self):
        for index, name in enumerate(CREATURES):
            with self.subTest(name=name):
                self.creature_witness(name, index)

    def creature_witness(self, name, index):
        session = self.session(740100 + index)
        source = self.add(session.engine, name, zone='hand')
        bolt = self.add(session.engine, 'Lightning Bolt', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'R': 2, 'U': 4, 'C': 8})
        self.checkpoint(session)
        self.assertFalse(self.offered(session, source))
        command = {'action_id': 'cast:' + source.ref, 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', bolt, ['B'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(37, session.state.players['B'].life)
        self.assertTrue(self.offered(session, source))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.private_replay(session, private)

    def test_original_angel_restriction_is_enforced_before_payment(self):
        self.creature_witness('Illusory Angel', 8)

    def test_original_angel_has_complete_nonempty_restriction_program(self):
        session = self.session(740118)
        source = self.add(session.engine, 'Illusory Angel', zone='hand')
        ir = compile_oracle_card(self.db.lookup('Illusory Angel'), capability_registry=self.registry,
            capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        self.assertTrue(any(program.event == 'cast.restriction' and program.handlers
            for program in session.engine.semantics.programs_for_oracle(source.oracle_id)))

    def test_fresh_guard_omission_is_killed(self):
        method = 'test_original_angel_restriction_is_enforced_before_payment'
        with patch('quorune.rules.casting.proposal.compiled_self_cast_restriction_reason', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_fresh_compiler_omission_is_killed(self):
        method = 'test_original_angel_has_complete_nonempty_restriction_program'
        with patch('quorune.compiler.self_cast_restriction_nodes.self_cast_restriction_nodes', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_original_blizzard_uses_current_snow_land_control_and_stale_offers(self):
        for stale in (True, False):
            with self.subTest(stale=stale):
                session = self.session(740140 + stale)
                source = self.add(session.engine, 'Blizzard', zone='hand')
                snow = self.add(session.engine, 'Snow-Covered Plains', seat='B')
                self.add(session.engine, 'Plains')
                annex = self.add(session.engine, 'Annex', zone='hand')
                bounce = self.add(session.engine, 'Boomerang', zone='hand')
                private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
                self.prepare(session, mana={'G': 2, 'U': 8, 'C': 10})
                self.checkpoint(session)
                command = {'action_id': 'cast:' + source.ref, 'pay': 'auto'}
                self.assertFalse(self.offered(session, source))
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:A', command).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                self.cast(session, 'A', annex, [snow.ref])
                self.assertFalse(self.offered(session, source))
                self.assertIsNone(self.resolve(session))
                self.assertEqual(('B', 'A'), (snow.owner, session.state.cards[snow.object_id].controller))
                self.assertTrue(self.offered(session, source))
                if not stale:
                    self.cast(session, 'A', source)
                self.cast(session, 'A', bounce, [snow.ref])
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual('hand', session.state.cards[snow.object_id].zone)
                self.assertEqual('graveyard', session.state.cards[annex.object_id].zone)
                if stale:
                    self.assertFalse(self.offered(session, source))
                    before = authoritative_state_hash(session.state)
                    self.assertFalse(session.act('pilot:A', command).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    self.assertEqual('hand', session.state.cards[source.object_id].zone)
                else:
                    self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
                self.private_replay(session, private)

    def test_opponents_prior_spell_does_not_qualify_but_casters_pending_spell_does(self):
        session = self.session(740160)
        source = self.add(session.engine, 'Illusory Angel', zone='hand')
        foreign = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand', ref='foreign-prior-bolt')
        own = self.add(session.engine, 'Lightning Bolt', zone='hand', ref='own-prior-bolt')
        self.add(session.engine, 'Vedalken Orrery')
        self.prepare(session, mana={'U': 2, 'R': 1, 'C': 5})
        session.state.players['B'].mana_pool['R'] = 1
        self.checkpoint(session)
        self.priority(session, 'B')
        self.cast(session, 'B', foreign, ['D'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(37, session.state.players['D'].life)
        self.assertFalse(self.offered(session, source))
        self.cast(session, 'A', own, ['B'])
        self.assertTrue(self.offered(session, source))
        self.cast(session, 'A', source)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual(37, session.state.players['B'].life)
        self.replay(session, load=True)

    def test_countered_prior_spell_still_qualifies_original_retainers(self):
        session = self.session(740161)
        source = self.add(session.engine, 'Hewed Stone Retainers', zone='hand')
        bolt = self.add(session.engine, 'Lightning Bolt', zone='hand')
        counter = self.add(session.engine, 'Counterspell', seat='B', zone='hand')
        self.prepare(session, mana={'R': 1, 'C': 5})
        session.state.players['B'].mana_pool['U'] = 2
        self.checkpoint(session)
        self.cast(session, 'A', bolt, ['B'])
        target = session.state.stack[-1].ref
        self.priority(session, 'B')
        self.cast(session, 'B', counter, [target])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(40, session.state.players['B'].life)
        self.assertEqual('graveyard', session.state.cards[bolt.object_id].zone)
        self.assertTrue(self.offered(session, source))
        self.cast(session, 'A', source)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)

    def test_registered_adapter_uses_actual_caster_without_changing_card_ownership(self):
        session = self.session(740162)
        source = self.add(session.engine, 'Blizzard', zone='hand')
        self.add(session.engine, 'Snow-Covered Plains', seat='B')
        before = authoritative_state_hash(session.state)
        self.assertEqual('self_cast_condition', compiled_self_cast_restriction_reason(session.engine, 'A', source))
        self.assertIsNone(compiled_self_cast_restriction_reason(session.engine, 'B', source))
        with self.assertRaises(SemanticNodeError):
            compiled_self_cast_restriction_reason(session.engine, 'X', source)
        self.assertEqual(('A', 'A'), (source.owner, source.controller))
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_selected_face_and_face_down_spell_use_their_proposed_characteristics(self):
        session = self.session(740165)
        source = self.add(session.engine, 'Generic Restricted Front // Generic Unrestricted Back', zone='hand')
        self.prepare(session, mana={'G': 1, 'U': 1, 'C': 3})
        self.checkpoint(session)
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        rows = self.actions(session)
        self.assertFalse(any(row['id'] == 'cast:' + source.ref
            or row['id'].endswith(':face:Generic Restricted Front') for row in rows))
        back = next(row for row in rows if row['id'].endswith(':face:Generic Unrestricted Back'))
        result = session.act('pilot:A', {'action_id': back['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

        session = self.session(740166)
        source = self.add(session.engine, 'Generic Intrinsic Restriction Morph', zone='hand')
        self.prepare(session, mana={'G': 1, 'C': 3})
        self.checkpoint(session)
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        self.assertFalse(self.offered(session, source))
        self.assertTrue(any(row['id'] == 'cast-morph:' + source.ref for row in self.actions(session)))
        result = session.act('pilot:A', {'action_id': 'cast-morph:' + source.ref, 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[source.object_id].face_down)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[source.object_id].face_down)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_original_blizzard_keeps_legality_locked_when_snow_land_pays_mana_cost(self):
        session = self.session(740164)
        source = self.add(session.engine, 'Blizzard', zone='hand')
        snow = self.add(session.engine, 'Wild Cantor', ref='sole-snow-land-mana-source')
        # A legal starting characteristic state uses the existing layer-four owner.
        # The adjacent conditional-Flash witness uses the same represented seam.
        create_resolution_continuous_effect_components(session.engine,
            source=ResolutionEffectSource(stack_ref='S-snow-land-mana-boundary'), targets=(snow,),
            components=(ResolutionContinuousComponent(Layer.TYPE, '4', (
                ContinuousOperation('add_types', ('land',), field='card_types'),
                ContinuousOperation('add_types', ('snow',), field='supertypes'),)),))
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        flyer = self.add(session.engine, 'Grizzly Bears', seat='B', ref='blizzard-flying-bear')
        flyer.counters['flying'] = 1
        flyer.tapped = True
        ground = self.add(session.engine, 'Grizzly Bears', seat='B', ref='blizzard-grounded-bear')
        ground.tapped = True
        self.prepare(session, mana={'G': 1})
        self.checkpoint(session)
        self.assertIn('Snow', session.engine._effective_card_data(snow)['type_line'])
        self.assertIn('Land', session.engine._effective_card_data(snow)['type_line'])
        self.assertTrue(self.offered(session, source))
        result = session.act('pilot:A', {'action_id': 'cast:' + source.ref, 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('graveyard', session.state.cards[snow.object_id].zone)
        self.assertEqual('stack', session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.advance(session, lambda s: s.state.active_player == 'B' and s.state.step == 'upkeep')
        self.assertTrue(session.state.cards[flyer.object_id].tapped)
        self.assertFalse(session.state.cards[ground.object_id].tapped)
        self.private_replay(session, private)

    def test_minimum_capabilities_and_compiler_producer_cannot_be_omitted(self):
        session = self.session(740163)
        source = self.add(session.engine, 'Blizzard', zone='hand')
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.event == 'cast.restriction']
        self.assertTrue(bind_semantic_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict'])
        for cap in (SELF_CAST_RESTRICTION_CAPABILITY, 'continuous.characteristics.fixed_public_state'):
            with self.subTest(cap=cap):
                deps = tuple(x for x in program.capability_dependencies if x != cap)
                changed = replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:' + cap, bound['blockers'])
                raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                next(row for row in raw['capabilities'] if row['id'] == cap).update(status='blocked', blockers=['withdrawn self-cast authority'])
                ir = compile_oracle_card(self.db.lookup('Blizzard'), capability_registry=CapabilityRegistry(raw),
                    capability_profile='commander_review')
                self.assertNotEqual('exact', ir.status)
        with patch('quorune.compiler.self_cast_restriction_nodes.self_cast_restriction_nodes', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(self.db.lookup('Blizzard'),
                capability_registry=self.registry, capability_profile='commander_review').status)

    def test_closed_descriptors_and_nearest_unrepresented_conditions(self):
        ir = compile_oracle_card(self.db.lookup('Blizzard'), capability_registry=self.registry,
            capability_profile='commander_review')
        descriptor, = next(node for node in ir.faces[0].nodes if node.event == 'cast.restriction').handlers
        handler = SelfCastRestrictionHandler()
        for field, value in (('schema_version', True), ('event', 'cast.permission'), ('future', 1)):
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError)):
                handler.validate({**descriptor, field: value})
        for text in ("Cast this spell only if you've cast another green spell this turn.",
                'Cast this spell only if you control a snow creature.',
                'Cast this spell only if you reveal a card.',
                'Cast this spell only if you control a snow land and have gained life this turn.'):
            with self.subTest(text=text):
                row = replace(self.db.lookup('Hewed Stone Retainers'), oracle_text=text)
                self.assertNotEqual('exact', compile_oracle_card(row, capability_registry=self.registry,
                    capability_profile='commander_review').status)

    def test_four_originals_have_complete_admission_with_nonempty_condition_programs(self):
        for index, name in enumerate(NAMES):
            with self.subTest(name=name):
                session = self.session(740120 + index)
                source = self.add(session.engine, name, zone='hand')
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertTrue(self.binding(session, source)['strict_capability_ready'])
                programs = session.engine.semantics.programs_for_oracle(source.oracle_id)
                self.assertTrue(any(program.event == 'cast.restriction' and program.handlers
                    for program in programs))
