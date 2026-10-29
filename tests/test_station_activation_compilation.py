"""Validated Station bodies keep costs separate from their threshold prefix."""
from dataclasses import replace
import unittest
from unittest.mock import patch
from test_qualified_zone_event_queries import query_record
from quorune.oracle_ir import compile_oracle_card
from quorune.compiler.activated_ability_catalog import compile_activated_ability_catalog
from quorune.compiler.station_context_nodes import station_activation_catalog_source
from quorune.rules.capabilities import load_default_capability_registry

TEXT = 'Station (Tap another creature you control: Put charge counters equal to its power on this Planet. Station only as a sorcery.)\n12+ | {G}, {T}: Add {G} for each creature you control.'


def record(text=TEXT):
    return replace(query_record(text), type_line='Land — Planet', power=None, toughness=None, keywords=('Station',))


class StationActivationCompilationTests(unittest.TestCase):
    def test_station_activation_body_and_catalog_agree_while_spans_retain_original_row(self):
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record(), capability_registry=registry)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = next(node for node in ir.faces[0].nodes if node.event == 'activate' and node.span.line == 2)
        self.assertEqual(TEXT.splitlines()[1], node.text)
        self.assertEqual(node.text, TEXT[node.span.start:node.span.end])
        ability = compile_activated_ability_catalog(record())['front'][0]
        self.assertEqual(1, ability.line_index)
        self.assertEqual('ab2', ability.ability_id)
        self.assertEqual('{G}, {T}', ability.cost_text)
        self.assertTrue(ability.mana_ability)
        self.assertTrue(ability.tap_source)
        self.assertEqual(1, ability.mana['G'])
        self.assertEqual((), ability.uncompiled_costs)
        self.assertNotIn('12+', ability.oracle_line)

    def test_station_activation_unknown_cost_result_and_unvalidated_prefix_remain_residual(self):
        registry = load_default_capability_registry()
        for text in (TEXT.replace('Station (', 'Unknown ('), TEXT.replace('12+ |', '12-20 |'),
                     TEXT.replace('{G}, {T}', '{G}, Rewrite history'),
                     TEXT.replace('Add {G} for each creature you control.', 'Rewrite history.')):
            self.assertNotEqual('exact', compile_oracle_card(record(text), capability_registry=registry).status, text)
        plain = replace(record('{G}, {T}: Add {G} for each creature you control.'), keywords=())
        self.assertEqual(plain.oracle_text, station_activation_catalog_source(plain))

    def test_station_activation_parse_prefix_mutant_is_killed(self):
        import quorune.compiler.activated_mana_nodes as owner
        original = owner.activated_oracle_node
        def witness():
            self.assertEqual('exact', compile_oracle_card(record(), capability_registry=load_default_capability_registry()).status)
        witness()
        def prefixed(**values):
            values.pop('parse_line', None)
            return original(**values)
        with patch('quorune.oracle_ir.activated_oracle_node', side_effect=prefixed), self.assertRaises(AssertionError):
            witness()
