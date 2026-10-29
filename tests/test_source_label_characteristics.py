"""CR201.5 self labels retain source identity in characteristic clauses."""
from dataclasses import replace
import unittest
from unittest.mock import patch
from test_qualified_zone_event_queries import query_record
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.source_references import source_self_permanent_pattern, source_self_permanent_type
from quorune.compiler.fixed_target_effect_sequences import fixed_source_characteristics_effect_template


class SourceLabelCharacteristicTests(unittest.TestCase):
    def test_canonical_self_labels_bind_complete_fixed_and_live_characteristic_clauses(self):
        registry = load_default_capability_registry()
        labels = ('creature', 'artifact', 'enchantment', 'land', 'permanent', 'planeswalker', 'Aura', 'Equipment', 'Saga', 'Spacecraft', 'Vehicle')
        for label in labels:
            with self.subTest(label=label):
                value = fixed_source_characteristics_effect_template(f'This {label} gets +2/+0 until end of turn.',
                    source_is_permanent=True, source_card_types=('artifact',), source_name='Source Fixture')
                self.assertIsNotNone(value)
                self.assertEqual('$source.zone_object', value.effects[0]['card'])
                self.assertEqual((2, 0), (value.effects[0]['power'], value.effects[0]['toughness']))
                self.assertIsNotNone(source_self_permanent_type('this ' + label))
                live = f'This {label} gets +1/+0 for each artifact you control.'
                ir = compile_oracle_card(replace(query_record(live), type_line='Artifact — Vehicle'), capability_registry=registry)
                self.assertEqual('exact', ir.status, ir.material_residuals)
        self.assertIn('spacecraft', source_self_permanent_pattern())

    def test_self_label_characteristics_reject_unknown_referents_and_incomplete_bodies(self):
        for text in ('This spaceship gets +2/+0 until end of turn.', 'That Vehicle gets +2/+0 until end of turn.',
                     'Another Vehicle gets +2/+0 until end of turn.', 'This Vehicle gets +2/+0 until end of turn. Rewrite history.',
                     'This Vehicle gets +2/+0 until next turn.'):
            self.assertIsNone(fixed_source_characteristics_effect_template(text, source_is_permanent=True,
                source_card_types=('artifact',), source_name='Source Fixture'))
        self.assertIsNone(fixed_source_characteristics_effect_template('This Vehicle gets +2/+0 until end of turn.',
            source_is_permanent=False, source_card_types=(), source_name='Source Fixture'))

    def test_missing_source_label_vocabulary_mutant_is_killed(self):
        import quorune.compiler.query_characteristic_templates as owner
        def witness():
            ir = compile_oracle_card(replace(query_record('This Spacecraft gets +1/+0 for each artifact you control.'),
                type_line='Artifact — Spacecraft'), capability_registry=load_default_capability_registry())
            self.assertEqual('exact', ir.status, ir.material_residuals)
        witness()
        with patch.object(owner, 'source_self_permanent_pattern', return_value='this creature'), self.assertRaises(AssertionError):
            witness()
