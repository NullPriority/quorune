from __future__ import annotations

"""CR 614/616: finite entry choices compose with unconditional tapped entry."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.entry_designation_templates import entry_designation_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.zone_replacements import default_zone_change_replacement_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_chosen_color_mana as mana_actions
import test_self_public_zone_casting as zone_actions


OWNER = 'zone.entry.color_designation_composition'


class EntryColorCompositionCompilerTests(unittest.TestCase):
    def test_excluded_color_and_complete_tapped_paragraph_keep_typed_leaf_owners(self):
        registry = load_default_capability_registry()
        for text, count, excluded in (
            ('As this land enters, choose a color other than white.', 1, 'W'),
            ('As this artifact enters, choose a color other than red.', 1, 'R'),
            ('This land enters tapped. As it enters, choose a color.', 2, None),
            ('This land enters tapped. As it enters, choose a color other than blue.', 2, 'U'),
        ):
            with self.subTest(text=text):
                compiled = entry_designation_handler(text, source_name='Entry source')
                self.assertIsNotNone(compiled)
                descriptors = (compiled[1],) if isinstance(compiled[1], dict) else compiled[1]
                self.assertEqual(count, len(descriptors))
                for descriptor in descriptors:
                    default_zone_change_replacement_registry().validate(descriptor)
                self.assertEqual(excluded, descriptors[-1].get('excluded_color'))
                self.assertEqual(1 if excluded is None else 2, descriptors[-1]['schema_version'])
                if count == 2:
                    self.assertEqual('replacement.zone.entry-state.v1', descriptors[0]['handler_id'])
                    self.assertTrue(descriptors[0]['instruction']['tapped'])
                ir = compile_oracle_card(record(text, 'Land' if 'land' in text else 'Artifact'),
                    capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertIn(OWNER, ir.faces[0].nodes[0].capability_dependencies)
                self.assertEqual(text, ir.faces[0].nodes[0].text)

    def test_open_exclusions_prefixes_tails_and_duplicate_linking_stay_residual(self):
        registry = load_default_capability_registry()
        for text in (
            'As this land enters, choose a color other than colorless.',
            'As this land enters, choose a color other than the chosen color.',
            'As it enters, choose a color other than red.',
            'This land enters tapped unless you control a Dragon. As it enters, choose a color.',
            'This land enters tapped. As it enters, choose a color. Draw a card.',
            'As this land enters, choose a color.\nAs this land enters, choose a color other than red.',
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, 'Land'), capability_registry=registry,
                                         capability_profile='commander_review')
                self.assertNotEqual('exact', ir.status)

    def test_excluded_descriptor_dependency_and_value_validation_fail_closed(self):
        text = 'As this land enters, choose a color other than white.'
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (OWNER, 'zone.entry.public_designation'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(
                status='blocked', blockers=['entry composition owner withdrawn'])
            self.assertNotEqual('exact', compile_oracle_card(record(text, 'Land'),
                capability_registry=CapabilityRegistry(changed), capability_profile='commander_review').status)
        changed = deepcopy(raw)
        next(row for row in changed['capabilities'] if row['id'] == 'zone.entry.tapped_state').update(
            status='blocked', blockers=['tapped entry owner withdrawn'])
        paragraph = 'This land enters tapped. As it enters, choose a color.'
        self.assertNotEqual('exact', compile_oracle_card(record(paragraph, 'Land'),
            capability_registry=CapabilityRegistry(changed), capability_profile='commander_review').status)
        descriptor = entry_designation_handler(text, source_name='Entry source')[1]
        for change in ({'schema_version': True}, {'excluded_color': 'C'}, {'excluded_color': ['W']},
                       {'designation': 'creature_type'}, {'unknown': True}):
            with self.assertRaises(SemanticNodeError):
                default_zone_change_replacement_registry().validate({**descriptor, **change})
        plain = entry_designation_handler('As this land enters, choose a color.', source_name='Entry source')[1]
        self.assertEqual(1, plain['schema_version'])
        self.assertNotIn('excluded_color', plain)

    def test_excluded_choice_uses_destination_controller_and_only_one_fresh_entry_value(self):
        from quorune.replacement_effects import apply_replacement, replacement_choice
        from quorune.semantic_runtime.entry_color_exclusion import ExcludedColorEntryDesignationHandler
        from quorune.semantic_runtime.zone_replacement_inputs import zone_change_snapshot_event
        from quorune.semantic_runtime.zone_replacement_model import ZoneChangeSubjectSnapshot, ZoneChangeReplacementSnapshot

        descriptor = entry_designation_handler('As this land enters, choose a color other than white.', source_name='Entry source')[1]
        subject = ZoneChangeSubjectSnapshot(object_id='source', object_ref='P-source', logical_object_id='source@0',
            owner='A', controller=None, origin='hand', destination='battlefield', destination_controller='C',
            entry_face_id='front', object_types=('land',), is_card_object=True)
        effects = ExcludedColorEntryDesignationHandler().subject_replacement_effects(descriptor, subject=subject, component_id='entry')
        self.assertEqual(set('UBRG'), {effect.effect_id.rsplit(':', 1)[-1] for effect in effects})
        snapshot = ZoneChangeReplacementSnapshot(revision=0, event_sequence=0, apnap_order=('A', 'B', 'C', 'D'),
            source_refs=(subject.object_ref,), subjects=(subject,), effects=effects)
        event = zone_change_snapshot_event(snapshot, subject)
        choice = replacement_choice(event, effects)
        self.assertEqual('C', choice.chooser)
        selected = next(effect.effect_id for effect in effects if effect.effect_id.endswith(':R'))
        result = apply_replacement(choice, effects, selected)
        self.assertEqual('R', result.payload['entry_chosen_color'])
        self.assertIsNone(replacement_choice(result, effects))
        departed = replace(subject, destination='exile')
        self.assertIsNone(replacement_choice(zone_change_snapshot_event(replace(snapshot, subjects=(departed,)), departed), effects))


class EntryColorCompositionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed
    action = mana_actions.ChosenColorManaRuntimeTests.action
    reject = mana_actions.ChosenColorManaRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'entry-color-composition.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/chosen-color-mana-cards.json',
            ROOT / 'tests/fixtures/entry-color-composition-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Entry color witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def play(self, session, source):
        offered = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        action = next(row['id'] for row in offered if row['id'] == 'play-land:' + source.ref)
        result = session.act('pilot:A', {'action_id': action})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('replacement.order', session.state.pending_decision.kind)

    def options(self, session):
        return session.packet('pilot:A', full=True)['decision']['ctx']['options']

    def choose(self, session, selected):
        result = session.act('pilot:A', {'action_id': 'choose', 'choices': {'replacement': selected}})
        self.assertTrue(result.ok, result.summary)

    def finish_color(self, session, color):
        selected = next(row['id'] for row in self.options(session) if row['id'].endswith(':' + color))
        self.choose(session, selected)
        self.resolve(session)

    def test_actual_gate_excludes_forbidden_choice_and_fixed_or_chosen_modes_match_commands(self):
        session = self.session(415000)
        source = self.add(session.engine, 'Citadel Gate', zone='hand', ref='entry-original-gate')
        untap = [self.add(session.engine, 'Generic Chosen Mana Untap', zone='hand', ref='entry-gate-untap-' + str(i)) for i in range(2)]
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='entry-gate-private')
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.play(session, source)
        options = self.options(session)
        colors = {row['id'].rsplit(':', 1)[-1] for row in options} & set('WUBRG')
        self.assertEqual(set('UBRG'), colors)
        selected = next(row['id'] for row in options if row['id'].endswith(':R'))
        base = selected.rsplit(':', 1)[0]
        for actor, choice in (('D', selected), ('A', base + ':W'), ('A', base + ':C')):
            self.reject(session, 'choose', actor, choices={'replacement': choice})
        session = self.replay(session, load=True)
        self.finish_color(session, 'R')
        if session.state.pending_decision.kind == 'replacement.order':
            self.choose(session, next(row['id'] for row in self.options(session)
                if row['id'].startswith('replacement.zone.entry-state.v1:')))
        source = session.state.cards[source.object_id]
        self.assertEqual(('battlefield', True, 'R'), (source.zone, source.tapped, source.annotations['chosen_color']))
        for index, color in enumerate(('W', 'R')):
            self.cast(session, 'A', untap[index], [source.ref])
            self.resolve(session)
            action = self.action(session, source, 'ab3')
            public = next(row for row in session.packet('pilot:A', full=True)['decision']['legal_actions'] if row['id'] == action['id'])
            options = public['choice_schema']['mana_output']['options']
            self.assertEqual({('W',), ('R',)}, {tuple(row['value']) for row in options})
            self.reject(session, action['id'], mana_output={'G': 1})
            result = session.act('pilot:A', {'action_id': action['id'], 'mana_output': {color: 1}})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(1, session.state.players['A'].mana_pool[color])
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_thriving_tapped_color_replacements_work_in_both_offered_orders(self):
        for tap_first in (False, True):
            with self.subTest(tap_first=tap_first):
                session = self.session(415010 + tap_first)
                source = self.add(session.engine, 'Thriving Isle', zone='hand', ref='entry-original-thriving')
                self.assert_closed(session, source)
                self.prepare(session)
                self.checkpoint(session)
                self.play(session, source)
                options = self.options(session)
                self.assertFalse(any(row['id'].endswith(':U') for row in options))
                tap = next(row['id'] for row in options if row['id'].startswith('replacement.zone.entry-state.v1:'))
                if tap_first:
                    self.choose(session, tap)
                    self.assertEqual('hand', session.state.cards[source.object_id].zone)
                session = self.replay(session, load=True)
                self.finish_color(session, 'R')
                if session.state.pending_decision.kind == 'replacement.order':
                    self.choose(session, next(row['id'] for row in self.options(session)
                        if row['id'].startswith('replacement.zone.entry-state.v1:')))
                source = session.state.cards[source.object_id]
                self.assertEqual(('battlefield', True, 'R'), (source.zone, source.tapped, source.annotations['chosen_color']))
                self.assertEqual(0, session.state.players['A'].land_plays_remaining)
                self.replay(session, load=True)

    def test_actual_sunken_plain_compound_entry_preserves_restricted_mana_spending(self):
        session = self.session(415020)
        source = self.add(session.engine, 'Sunken Citadel', zone='hand', ref='entry-original-sunken')
        sink = self.add(session.engine, 'Generic Entry Land Sink', ref='entry-land-spending-sink')
        untap = self.add(session.engine, 'Generic Chosen Mana Untap', zone='hand', ref='entry-sunken-untap')
        spell = self.add(session.engine, 'Generic Chosen Red Spell', zone='hand', ref='entry-restricted-spell')
        self.assert_closed(session, source)
        self.assert_closed(session, sink)
        self.prepare(session)
        self.checkpoint(session)
        self.play(session, source)
        self.finish_color(session, 'R')
        if session.state.pending_decision.kind == 'replacement.order':
            self.choose(session, next(row['id'] for row in self.options(session)
                if row['id'].startswith('replacement.zone.entry-state.v1:')))
        source = session.state.cards[source.object_id]
        self.assertTrue(source.tapped)
        self.cast(session, 'A', untap, [source.ref])
        self.resolve(session)
        action = self.action(session, source, 'ab3')
        result = session.act('pilot:A', {'action_id': action['id'], 'mana_output': {'R': 2}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, session.state.players['A'].mana_pool['R'])
        offered = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertNotIn('cast:' + spell.ref, {row['id'] for row in offered})
        self.reject(session, 'cast:' + spell.ref, pay='auto')
        action = self.action(session, sink, 'ab1')
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.resolve(session)
        self.assertEqual(41, session.state.players['A'].life)
        self.replay(session, load=True)
