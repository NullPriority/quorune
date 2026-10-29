"""Pinned CR614/616 and Thalia/Archelos rulings, independently scoped."""

from copy import deepcopy
from dataclasses import replace
from itertools import combinations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_job_select as actions
import test_ring_native_actions as traversal
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_type_scopes import EntryTypeScope, entry_type_scopes
from quorune.external_entry_state_model import ExternalEntryStateSpec
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.qualified_entry_state_model import (
    QUALIFIED_ENTRY_STATE_CAPABILITY, QUALIFIED_ENTRY_STATE_HANDLER,
    QualifiedEntryStateSpec, EntryStateSourceSnapshot,
)
from quorune.compiler.qualified_entry_state_templates import qualified_entry_state_handler
from quorune.record import authoritative_state_hash
from quorune.replacement import ReplaceableEvent, ReplacementEffect, SetField
from quorune.replacement.applicability import condition_matches, replacement_choice
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.qualified_entry_state import QualifiedEntryStateHandler
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


THALIA = 'Creatures and nonbasic lands your opponents control enter tapped.'
ARCH = 'As long as Archelos is untapped, other permanents enter untapped.'


class QualifiedEntryStateModelTests(unittest.TestCase):
    def descriptor(self, text=THALIA):
        return qualified_entry_state_handler(text, source_name='Archelos, Lagoon Mystic')[1]

    def effects(self, descriptor=None, *, tapped=False):
        return QualifiedEntryStateHandler().source_replacement_effects(descriptor or self.descriptor(),
            source_ref='source:1', source_controller='A', component_id='entry',
            source_context=EntryStateSourceSnapshot('source:1', 'A', tapped))

    def test_qualified_codec_rejects_unrepresented_state_and_retains_old_payload(self):
        old = {'source_relation': 'opponents', 'types_any': ['artifact', 'land']}
        self.assertEqual(json.dumps(old), json.dumps(ExternalEntryStateSpec.from_dict(old).to_dict()))
        spec = QualifiedEntryStateHandler().validate(self.descriptor())
        self.assertEqual(spec, QualifiedEntryStateSpec.from_dict(spec.to_dict()))
        for update in ({'tapped': 1}, {'exclude_source': None}, {'source_tapped': 1},
                       {'source_relation': 'caster'}, {'unknown': 1}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                QualifiedEntryStateSpec.from_dict({**spec.to_dict(), **update})
        for query in (ObjectQuerySpec(zones=('hand',)), ObjectQuerySpec(zones=('battlefield',), token=True),
                      ObjectQuerySpec(zones=('battlefield',), colors_all=('U',)),
                      ObjectQuerySpec(zones=('battlefield',), tapped=True)):
            with self.assertRaises(ValueError): replace(spec, subject_queries=(query,))
        for context in (None, EntryStateSourceSnapshot('source:2', 'A', False),
                        EntryStateSourceSnapshot('source:1', 'B', False)):
            with self.assertRaises(SemanticNodeError):
                QualifiedEntryStateHandler().source_replacement_effects(self.descriptor(), source_ref='source:1',
                    source_controller='A', component_id='entry', source_context=context)

    def test_type_union_is_one_occurrence_with_prospective_controller_and_basic_exclusion(self):
        effects = self.effects()
        tags = ('artifact', 'basic', 'creature', 'dryad', 'forest', 'land')
        for size in range(len(tags) + 1):
            for selected in combinations(tags, size):
                for controller in 'ABCD':
                    event = ReplaceableEvent('entry', 'zone.change', controller, {
                        'destination': 'battlefield', 'destination_controller': controller,
                        'owner': 'C', 'object_ref': 'entrant:1', 'object_types': list(selected)})
                    expected = controller != 'A' and ('creature' in selected or
                        ('land' in selected and 'basic' not in selected))
                    matching = [effect for effect in effects if condition_matches(effect.conditions, event)]
                    self.assertEqual(int(expected), len(matching), (controller, selected))
                    choice = replacement_choice(event, effects)
                    self.assertEqual(expected, choice is not None)
                    if matching:
                        used = replace(event, applied_effects=(matching[0].effect_id,))
                        self.assertIsNone(replacement_choice(used, effects))
                        changed = replace(used, payload={**dict(used.payload),
                            'object_types': ['creature', 'land', 'forest']})
                        self.assertIsNone(replacement_choice(changed, effects))

    def test_type_scope_partition_matches_independent_three_subject_union(self):
        scopes = entry_type_scopes((
            ObjectQuerySpec(zones=('battlefield',), types_all=('creature',), excluded_subtypes=('phyrexian',)),
            ObjectQuerySpec(zones=('battlefield',), types_all=('land',), excluded_supertypes=('basic',)),
            ObjectQuerySpec(zones=('battlefield',), types_all=('artifact',)),
        ))
        tags = ('artifact', 'basic', 'creature', 'land', 'phyrexian')
        for size in range(len(tags) + 1):
            for selected in combinations(tags, size):
                actual = sum(scope.required <= set(selected) and not scope.excluded & set(selected)
                             for scope in scopes)
                expected = ('artifact' in selected or ('creature' in selected and 'phyrexian' not in selected)
                            or ('land' in selected and 'basic' not in selected))
                self.assertEqual(int(expected), actual, selected)

    def test_plural_source_port_preserves_legacy_effect_identity_and_codec(self):
        from quorune.semantic_runtime.zone_replacements import default_zone_change_replacement_registry
        from quorune.compiler.external_entry_state_templates import external_entry_state_handler
        registry = default_zone_change_replacement_registry()
        descriptor = external_entry_state_handler('Creatures your opponents control enter tapped.')[1]
        identities = dict(source_ref='legacy:1', source_controller='B', component_id='legacy-component')
        single = registry.source_replacement_effect(descriptor, **identities)
        plural, = registry.source_replacement_effects(descriptor, **identities)
        self.assertEqual(single.to_dict(), plural.to_dict())
        self.assertNotIn('occurrence_id', plural.to_dict())

    def test_source_tap_condition_and_other_incarnation_are_exact(self):
        descriptor = self.descriptor(ARCH)
        self.assertEqual((), self.effects(descriptor, tapped=True))
        effect, = self.effects(descriptor)
        self.assertEqual({'op': 'set', 'field': 'tapped', 'value': False}, effect.operations[0].to_dict())
        for ref in ('source:1', 'source:2', 'other:1'):
            event = ReplaceableEvent('entry', 'zone.change', 'D', {'destination': 'battlefield',
                'destination_controller': 'D', 'object_ref': ref, 'object_types': ['creature']})
            self.assertEqual(ref != 'source:1', condition_matches(effect.conditions, event))
        with self.assertRaises(ValueError): entry_type_scopes((ObjectQuerySpec(zones=('battlefield',),
            types_all=('creature',), excluded_types=('creature',)),))
        with self.assertRaises(ValueError): entry_type_scopes((ObjectQuerySpec(zones=('battlefield',),
            subtypes_any=tuple('type'+str(index) for index in range(17))),))

    def test_shared_entry_occurrence_codec_rejects_other_mutations(self):
        effect, = self.effects(self.descriptor(ARCH))
        self.assertEqual(effect.to_dict(), ReplacementEffect.from_dict(effect.to_dict()).to_dict())
        for change in ({'event_kind': 'damage'}, {'optional': True}, {'replacement_class': []},
                       {'operations': (SetField('tapped', 1),)},
                       {'operations': (SetField('destination', 'exile'),)},
                       {'conditions': {}}, {'decline_operations': (SetField('tapped', True),)}):
            with self.subTest(change=change), self.assertRaises(ValueError): replace(effect, **change)

    def test_lost_type_scope_mutation_is_killed(self):
        with patch('quorune.semantic_runtime.qualified_entry_state.entry_type_scopes',
                   return_value=(EntryTypeScope(),)), self.assertRaises(AssertionError):
            self.test_type_union_is_one_occurrence_with_prospective_controller_and_basic_exclusion()


class QualifiedEntryStateCompilerTests(unittest.TestCase):
    def test_exact_source_spans_and_closed_qualified_entry_grammar(self):
        registry = load_default_capability_registry()
        for text in (THALIA, ARCH, 'Nonbasic lands enter tapped.',
                     'Non-Phyrexian creatures enter tapped.',
                     'As long as Archelos is tapped, other permanents enter tapped.'):
            record = replace(query_record(text), name='Archelos, Lagoon Mystic')
            ir = compile_oracle_card(record, capability_registry=registry)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(text, record.oracle_text[node.span.start:node.span.end])
            self.assertEqual('battlefield', node.active_zone)
            self.assertEqual(QUALIFIED_ENTRY_STATE_HANDLER, node.handlers[0]['handler_id'])
        for text in ('Creatures played by your opponents enter tapped.',
                     'Creatures enter tapped if it is your turn.',
                     'Blue creatures enter tapped.', 'Token creatures enter tapped.',
                     'As long as Another Source is tapped, other permanents enter tapped.',
                     'Nonbasic lands enter tapped. Draw a card.'):
            self.assertIsNone(qualified_entry_state_handler(text, source_name='Archelos, Lagoon Mystic'), text)

    def test_missing_qualified_entry_dependency_fails_closed_with_unrelated_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        record = query_record(THALIA)
        for omitted in (QUALIFIED_ENTRY_STATE_CAPABILITY, 'zone.entry.tapped_state',
                        'zone.entry.tapped_state.external_source', 'variant.monarch.designate'):
            value = deepcopy(raw)
            next(row for row in value['capabilities'] if row['id'] == omitted).update(
                status='blocked', blockers=['qualified entry owner omitted'])
            registry = CapabilityRegistry(value)
            index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertEqual(omitted == 'variant.monarch.designate',
                compile_oracle_card(record, capability_registry=registry).status == 'exact', omitted)


class QualifiedEntryStateNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    add = actions.JobSelectRuntimeTests.add
    prepare = actions.JobSelectRuntimeTests.prepare
    cast = actions.JobSelectRuntimeTests.cast
    checkpoint = actions.JobSelectRuntimeTests.checkpoint
    resolve = actions.JobSelectRuntimeTests.resolve
    replay = actions.JobSelectRuntimeTests.replay
    until = traversal.RingNativeActionTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'qualified-entry.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                                ROOT/'tests/fixtures/qualified-entry-state-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Qualified entry', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 24)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def session(self, seed):
        session = actions.JobSelectRuntimeTests.session(self, seed)
        # Bound initial hands before the checkpoint; these tests traverse turns.
        for player in session.state.players.values():
            for object_id in tuple(player.zones['hand'])[2:]:
                session.engine.move_card(object_id, 'library', log=False)
        return session

    def turn(self, session, seat):
        order = 'ABCD'
        start = order.index(session.state.active_player)
        for offset in range(1, 5):
            next_seat = order[(start + offset) % 4]
            self.until(session, lambda current: current.state.active_player == next_seat
                and current.state.phase == 'precombat_main' and current.state.pending_decision is not None
                and current.state.pending_decision.kind == 'priority' and current.state.pending_decision.actors == [next_seat])
            if next_seat == seat:
                return

    def play(self, session, seat, card):
        offered = session.packet('pilot:'+seat, full=True)['decision']['ctx']['legal']['actions']
        action = next(row for row in offered if row['id'] == 'play-land:'+card.ref)
        result = session.act('pilot:'+seat, {'action_id': action['id']})
        self.assertTrue(result.ok, result.summary)

    def test_actual_thalia_distinguishes_creature_land_union_basic_land_and_source_departure(self):
        session = self.session(614_120_101)
        source = self.add(session.engine, 'Thalia, Heretic Cathar', zone='hand')
        own = self.add(session.engine, 'Memnite', zone='hand', ref='entry-own')
        basic = self.add(session.engine, 'Forest', seat='B', zone='hand')
        arbor = self.add(session.engine, 'Dryad Arbor', seat='D', zone='hand')
        first = self.add(session.engine, 'Memnite', seat='D', zone='hand', ref='entry-before')
        later = self.add(session.engine, 'Memnite', seat='D', zone='hand', ref='entry-after')
        bounce = self.add(session.engine, 'Unsummon', seat='D', zone='hand')
        self.add(session.engine, 'Island', seat='D')
        secret = self.add(session.engine, 'Twiddle', seat='C', zone='hand', ref='entry-private')
        self.prepare(session, mana={'C': 2, 'W': 1}); self.checkpoint(session)
        self.cast(session, 'A', source); self.assertIsNone(self.resolve(session))
        self.assertFalse(source.tapped)
        self.cast(session, 'A', own); self.assertIsNone(self.resolve(session)); self.assertFalse(own.tapped)
        self.turn(session, 'B'); self.play(session, 'B', basic); self.assertFalse(basic.tapped)
        self.turn(session, 'D'); self.play(session, 'D', arbor)
        self.assertTrue(arbor.tapped); self.assertNotEqual('replacement.order', session.state.pending_decision.kind)
        self.cast(session, 'D', first); self.assertIsNone(self.resolve(session)); self.assertTrue(first.tapped)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {'action_id': 'cast:'+bounce.ref, 'targets': [source.ref], 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        for seat in 'ABD': self.assertNotIn(secret.ref, json.dumps(session.packet('pilot:'+seat, full=True)))
        self.cast(session, 'D', bounce, [source.ref]); self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', source.zone)
        self.cast(session, 'D', later); self.assertIsNone(self.resolve(session)); self.assertFalse(later.tapped)
        self.replay(session, load=True)

    def test_actual_archelos_affected_controller_orders_entry_and_later_tap_changes_state(self):
        for external_first in (False, True):
            with self.subTest(external_first=external_first):
                session = self.session(614_120_102 + int(external_first))
                source = self.add(session.engine, 'Archelos, Lagoon Mystic', zone='hand')
                icy = self.add(session.engine, 'Icy Manipulator')
                self.add(session.engine, 'Sol Ring')
                land = self.add(session.engine, 'Azorius Guildgate', seat='D', zone='hand')
                entrant = self.add(session.engine, 'Memnite', seat='D', zone='hand')
                self.prepare(session, mana={'C': 1, 'B': 1, 'G': 1, 'U': 1}); self.checkpoint(session)
                self.cast(session, 'A', source); self.assertIsNone(self.resolve(session)); self.assertFalse(source.tapped)
                self.turn(session, 'D'); self.play(session, 'D', land)
                self.assertEqual('replacement.order', session.state.pending_decision.kind)
                session = self.replay(session, load=True)
                for seat in 'ABC': self.assertIsNone(session.packet('pilot:'+seat, full=True)['decision'])
                decision = session.packet('pilot:D', full=True)['decision']
                option = next(row for row in decision['ctx']['options']
                    if (QUALIFIED_ENTRY_STATE_HANDLER in row['id']) is external_first)
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:A', {'action_id': 'choose', 'replacement': option['id']}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                accepted = session.act('pilot:D', {'action_id': 'choose', 'replacement': option['id']})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(external_first, session.state.cards[land.object_id].tapped)
                self.cast(session, 'D', entrant)
                self.until(session, lambda current: current.state.pending_decision is not None
                    and current.state.pending_decision.kind == 'priority' and current.state.pending_decision.actors == ['A'])
                options = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                activation = next(row for row in options if row['id'].startswith('activate:'+icy.ref+':'))
                accepted = session.act('pilot:A', {'action_id': activation['id'], 'targets': [source.ref], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary); self.assertIsNone(self.resolve(session))
                self.assertTrue(session.state.cards[source.object_id].tapped)
                self.assertTrue(session.state.cards[entrant.object_id].tapped)
                self.assertEqual(external_first, session.state.cards[land.object_id].tapped)
                self.replay(session, load=True)

    def test_simultaneous_qualified_source_entry_has_no_retroactive_replacement(self):
        session = self.session(614_120_104)
        source = self.add(session.engine, 'Archelos, Lagoon Mystic', zone='hand')
        other = self.add(session.engine, 'Memnite', zone='hand')
        session.engine._move_cards_simultaneously([(source.object_id, 'battlefield'),
            (other.object_id, 'battlefield')], reason='qualified entry source boundary', log=False)
        self.assertFalse(source.tapped); self.assertFalse(other.tapped)


if __name__ == '__main__': unittest.main()
