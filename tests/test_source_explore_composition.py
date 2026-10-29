"""Independent source-binding, Explore action-mode and provenance contracts."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from common import ROOT
import source_explore_fixture as fixtures
import test_explore_rules as explore_fixtures
from test_qualified_zone_event_queries import query_record
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_choices.library_and_hand import ExploreChoiceHandler
from quorune.semantic_runtime.intents import PlaceCountersIntent, ExploreCompletedIntent


class SourceExploreCompositionTests(fixtures.SourceExploreFixture, unittest.TestCase):
    def test_source_explore_named_and_self_heads_keep_identity_and_ambiguous_receivers_residual(self):
        from quorune.compiler.source_self_effect_templates import normalized_source_event_line
        vehicle = 'Whenever this Vehicle attacks, it gets +1/+1 and gains trample until end of turn.'
        self.assertIsNone(normalized_source_event_line(vehicle, source_name='Generic Query Observer', named_only=True))
        self.assertIsNotNone(normalized_source_event_line(vehicle, source_name='Generic Query Observer'))
        vehicle_ir = compile_oracle_card(replace(query_record(vehicle), type_line='Artifact — Vehicle'), capability_registry=self.registry)
        self.assertEqual('exact', vehicle_ir.status, vehicle_ir.material_residuals)
        for subject, name in (('this creature', 'Generic Query Observer'), ('Jenny', 'Jenny, Generated Anomaly'), ('Jenny, Generated Anomaly', 'Jenny, Generated Anomaly')):
            record = replace(query_record('Whenever ' + subject + ' deals combat damage to a player, it explores.'), name=name, type_line='Creature — Human')
            ir = compile_oracle_card(record, capability_registry=self.registry)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node = ir.faces[0].nodes[0]
            self.assertEqual(({'op': 'explore', 'player': '$source.controller', 'card': '$source'},), tuple(node.effects))
            self.assertIsNone(node.target_schema)
            self.assertIn('keyword_action.explore.single', node.capability_dependencies)
        for text in ('Whenever a creature you control enters, it explores.',
                     'Whenever another creature attacks, it explores.',
                     'Whenever this creature attacks, it explores twice.',
                     'Whenever this creature attacks, it explores. Rewrite history.'):
            self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=self.registry).status)

    def test_current_empty_explore_counters_only_current_incarnation_and_legacy_mode_completes_without_it(self):
        row = explore_fixtures._row('source', zone='battlefield', logical='logical:explorer', types=('creature',))
        handler = ExploreChoiceHandler()
        effect = {'op': 'explore', 'player': 'A', 'card': row.ref}
        current = explore_fixtures._context(row, None)
        prepared = handler.prepare(effect, current)
        self.assertEqual((PlaceCountersIntent, ExploreCompletedIntent), tuple(type(i) for i in prepared.preparation_intents))
        self.assertEqual((row.ref,), prepared.preparation_intents[0].object_refs)
        self.assertEqual(1, prepared.preparation_intents[0].amount)
        legacy = replace(current, query=replace(current.query, explore_action_version=None))
        self.assertEqual((ExploreCompletedIntent,), tuple(type(i) for i in handler.prepare(effect, legacy).preparation_intents))
        for changed in (replace(row, logical_object_id='returned@2'), replace(row, phased_out=True), replace(row, zone='graveyard')):
            context = explore_fixtures._context(changed, None)
            self.assertEqual((ExploreCompletedIntent,), tuple(type(i) for i in handler.prepare(effect, context).preparation_intents))

    def test_explore_mode_checkpoint_absence_and_record_provenance_never_silently_upgrade(self):
        from quorune.explore_action_model import explore_action_version
        from quorune.model import GameState
        from quorune.record import authoritative_state_hash
        from quorune.record_state_provenance import format_state_versions, validate_state_versions
        s = self.session(613658010)
        self.assertEqual(1, s.state.explore_action_version)
        raw = s.state.to_dict()
        self.assertEqual(1, raw.pop('explore_action_version'))
        legacy = GameState.from_dict(raw)
        self.assertIsNone(legacy.explore_action_version)
        self.assertNotIn('explore_action_version', legacy.to_dict())
        self.assertEqual(authoritative_state_hash(legacy), authoritative_state_hash(GameState.from_dict(legacy.to_dict())))
        current = format_state_versions(s.state)
        self.assertEqual(1, current['explore_action_version'])
        validate_state_versions({'format': current}, s.state)
        validate_state_versions({'format': format_state_versions(legacy)}, legacy)
        for bad in (True, False, 0, 2, '1'):
            with self.assertRaises(ValueError):
                explore_action_version(bad)
            with self.assertRaises(ValueError):
                GameState.from_dict({**raw, 'explore_action_version': bad})
        for declared in (None, True, 0, 2):
            with self.assertRaises(ValueError):
                validate_state_versions({'format': {**current, 'explore_action_version': declared}}, s.state)

    def test_owned_nested_explore_departure_capture_uses_current_controller_and_sealed_incarnation(self):
        from quorune.semantic_runtime.explore import capture_explore_source_departure, explore_source_controller
        from quorune.model import CardInstance, StackItem
        from quorune.semantics import SemanticProgram, SemanticRegistry
        for mode in (None, 1):
            program = SemanticProgram(key='fixture:bound-explore', label='Explore', effects=[{'op': 'offer_optional_effect', 'player': '$controller',
                'effects': [{'op': 'explore', 'player': '$source.controller', 'card': '$source'}]}])
            registry = SemanticRegistry()
            registry.put(program)
            card = CardInstance(object_id='id', ref='source', oracle_id='fixture:source', printed_name='Source', owner='A', controller='B', zone='battlefield')
            item = StackItem(stack_id='stack', ref='stack', kind='triggered_ability', controller='A', label='Explore', source_object_id=card.object_id,
                             semantic_key=program.key, context={'source_logical_object_id': card.logical_object_id})
            host = SimpleNamespace(semantics=registry, state=SimpleNamespace(stack=[item], pending_trigger_batches=[], explore_action_version=mode))
            self.assertEqual(1 if mode == 1 else 0, capture_explore_source_departure(host, card))
            card.zone = 'hand'
            card.controller = 'A'
            self.assertEqual('B' if mode == 1 else 'A', explore_source_controller(item, {'id': card}))
            if mode == 1:
                card.zone = 'battlefield'
                card.zone_change_counter += 1
                self.assertEqual('B', explore_source_controller(item, {'id': card}))

    def test_source_explore_and_empty_counter_omission_mutants_are_killed(self):
        record = query_record('Whenever this creature deals combat damage to a player, it explores.')
        def source_witness():
            self.assertEqual('exact', compile_oracle_card(record, capability_registry=self.registry).status)
        source_witness()
        with patch('quorune.compiler.explore_templates.single_explore_effect_template', return_value=None), self.assertRaises(AssertionError):
            source_witness()
        row = explore_fixtures._row('source', zone='battlefield', logical='logical:explorer', types=('creature',))
        def counter_witness():
            prepared = ExploreChoiceHandler().prepare({'op': 'explore', 'player': 'A', 'card': 'source'}, explore_fixtures._context(row, None))
            self.assertTrue(any(isinstance(i, PlaceCountersIntent) for i in prepared.preparation_intents))
        counter_witness()
        with patch('quorune.semantic_choices.library_and_hand._explore_counter_intents', return_value=()), self.assertRaises(AssertionError):
            counter_witness()

    def test_verified_source_explore_withdrawal_keeps_unrelated_source_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text())
        changed = deepcopy(raw)
        next(r for r in changed['capabilities'] if r['id'] == 'keyword_action.explore.single').update(status='blocked', blockers=['Explore owner withdrawn'])
        registry = CapabilityRegistry(changed)
        index = build_index(registry_value=changed, declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'),
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual('exact', compile_oracle_card(query_record('Whenever this creature deals combat damage to a player, it explores.'), capability_registry=registry).status)
        self.assertEqual('exact', compile_oracle_card(query_record('Whenever this creature deals combat damage to a player, draw a card.'), capability_registry=registry).status)
