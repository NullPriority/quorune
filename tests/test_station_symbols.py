"""Independent CR721 threshold, cumulative grant and characteristic boundaries."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch
from common import ROOT
from test_qualified_zone_event_queries import query_record
from quorune.station_symbol_model import StationSymbolSpec, StationSymbolsSpec, STATION_SYMBOL_CAPABILITY
from quorune.ability_fragments import ability_fragment_from_dict
from quorune.semantic_runtime.station_symbols import StationSymbolsHandler
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry

TEXT = "Station (Tap another creature you control: Put charge counters equal to its power on this Spacecraft. Station only as a sorcery. It's an artifact creature at 12+.)\n2+ | Other creatures you control get +1/+1.\n12+ | Flying, lifelink"


def source_record(text=TEXT):
    return replace(query_record(text), type_line='Artifact — Spacecraft', power='3', toughness='5',
                   keywords=('Station', 'Flying', 'Lifelink'))


class StationSymbolModelTests(unittest.TestCase):
    def test_station_symbols_roundtrip_reject_ambiguous_boxes_members_and_counter_facts(self):
        spec = StationSymbolsSpec((StationSymbolSpec(2, semantic_keys=('lower',)), StationSymbolSpec(12, 3, 5, ('flying', 'lifelink'), ('upper',))))
        self.assertEqual(spec, StationSymbolsSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        for args in ((True,), (0,), (3, True, 4), (3, 3, None), (3, -1, 2)):
            with self.assertRaises(ValueError):
                StationSymbolSpec(*args)
        for symbols in ((), (StationSymbolSpec(2), StationSymbolSpec(1)),
                        (StationSymbolSpec(2), StationSymbolSpec(2)),
                        (StationSymbolSpec(2, 1, 1), StationSymbolSpec(3)),
                        (StationSymbolSpec(2, semantic_keys=('x',)), StationSymbolSpec(3, semantic_keys=('x',)))):
            with self.assertRaises(ValueError):
                StationSymbolsSpec(symbols)
        for counters in (None, {'charge': True}, {'charge': -1}, {'charge': '12'}):
            with self.assertRaises(ValueError):
                spec.symbols[0].active(counters)
        for mutate in (lambda d: d.update(extra=None), lambda d: d.update(schema_version=True),
                       lambda d: d['symbols'][1].update(keywords=['unknown rule'])):
            descriptor = spec.descriptor()
            mutate(descriptor)
            with self.assertRaises(SemanticNodeError):
                StationSymbolsHandler().validate(descriptor)

    def test_station_symbols_emit_cumulative_members_and_separate_type_ability_base_layers(self):
        spec = StationSymbolsSpec((StationSymbolSpec(2, semantic_keys=('lower',)), StationSymbolSpec(12, 3, 5, ('flying',), ('upper',))))
        for count in (0, 1, 2, 11, 12, 13):
            context = SimpleNamespace(source_object_id='source', source_logical_object_id='original', source_timestamp=1,
                                      component_id='symbol', source_counters={'charge': count})
            effects = StationSymbolsHandler().lower(spec.descriptor(), context)
            operations = [op for effect in effects for op in effect.operations]
            removed = [ability_fragment_from_dict(op.value).semantic_key for op in operations if op.op == 'remove_ability_fragment']
            self.assertEqual(['lower', 'upper'] if count < 2 else ['upper'] if count < 12 else [], removed)
            self.assertEqual(count >= 12, any(op.op == 'add_types' and op.field == 'card_types' and list(op.value) == ['Creature'] for op in operations))
            base = [op for op in operations if op.op == 'set_power_toughness']
            self.assertEqual(1 if count >= 12 else 0, len(base))
            if base:
                self.assertEqual([3, 5], list(base[0].value))
            self.assertEqual(count >= 12, any(op.op == 'add_ability' and op.value == 'flying' for op in operations))

    def test_station_threshold_predicate_mutant_is_killed(self):
        spec = StationSymbolsSpec((StationSymbolSpec(3, 4, 3, ('flying',)),))
        context = SimpleNamespace(source_object_id='source', source_logical_object_id='original', source_timestamp=1,
                                  component_id='symbol', source_counters={})
        def witness():
            self.assertFalse(any(op.op == 'add_types' for effect in StationSymbolsHandler().lower(spec.descriptor(), context) for op in effect.operations))
        witness()
        with patch.object(StationSymbolSpec, 'active', return_value=True), self.assertRaises(AssertionError):
            witness()


class StationSymbolCompilerTests(unittest.TestCase):
    def test_spacecraft_self_zone_labels_share_canonical_vehicle_occurrences_without_type_predicates(self):
        registry = load_default_capability_registry()
        for verb, event in (('enters', 'permanent.enter.self'), ('dies', 'creature.dies.self')):
            nodes = []
            for label in ('Vehicle', 'Spacecraft'):
                text = f'When this {label} {verb}, draw a card.'
                record = replace(source_record(text), power=None, toughness=None, keywords=())
                ir = compile_oracle_card(record, capability_registry=registry)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(event, node.event)
                self.assertIn('trigger.event.normalized_zone_change', node.capability_dependencies)
                nodes.append(node)
            self.assertEqual(nodes[0].event_condition, nodes[1].event_condition)
            self.assertEqual(nodes[0].effects, nodes[1].effects)

    def test_station_symbols_preserve_spans_and_members_without_promoting_unknown_children(self):
        registry = load_default_capability_registry()
        ir = compile_oracle_card(source_record(), capability_registry=registry)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        parent = next(node for node in ir.faces[0].nodes if node.template_id == 'station-symbol-scopes-v1')
        spec = StationSymbolsHandler().validate(parent.handlers[0])
        self.assertEqual((2, 12), tuple(symbol.minimum for symbol in spec.symbols))
        self.assertEqual((None, 3), tuple(symbol.power for symbol in spec.symbols))
        self.assertTrue(all(symbol.semantic_keys for symbol in spec.symbols))
        self.assertEqual(('flying', 'lifelink'), spec.symbols[-1].keywords)
        for node in ir.faces[0].nodes:
            self.assertEqual(node.text, TEXT[node.span.start:node.span.end])
        for text in (TEXT.replace('at 12+', 'at 13+'), TEXT.replace('12+ |', '1+ |'),
                     TEXT.replace('2+ |', '2-5 |'), TEXT + '\nRewrite history.'):
            self.assertNotEqual('exact', compile_oracle_card(source_record(text), capability_registry=registry).status, text)

    def test_station_symbols_verified_owner_withdrawal_blocks_whole_source(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text())
        next(row for row in raw['capabilities'] if row['id'] == STATION_SYMBOL_CAPABILITY).update(status='blocked', blockers=['Station symbol authority withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'), discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual('exact', compile_oracle_card(source_record(), capability_registry=registry).status)
        self.assertEqual('exact', compile_oracle_card(replace(query_record('Draw a card.'), type_line='Sorcery'), capability_registry=registry).status)
