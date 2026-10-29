"""Source-led counter quantities bind without borrowing a target's identity."""
from dataclasses import replace
import unittest
from unittest.mock import patch
from test_qualified_zone_event_queries import query_record
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import scalar_amount_specs, ScalarAmountOrigin


class SourceCounterPronounCompilerTests(unittest.TestCase):
    def compile(self, text, *, type_line='Artifact'):
        return compile_oracle_card(replace(query_record(text), type_line=type_line), capability_registry=load_default_capability_registry())

    def test_explicit_source_and_activated_pronoun_counter_amounts_keep_original_span_and_roles(self):
        for head in ('This artifact', 'It'):
            text = '{2}, Sacrifice this artifact: ' + head + ' deals damage equal to the number of charge counters on it to any target.'
            with self.subTest(head=head):
                ir = self.compile(text);self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual((0, len(text)), (node.span.start, node.span.end))
                amount, = scalar_amount_specs(node.effects)
                self.assertEqual((ScalarAmountOrigin.SOURCE, 'charge', 2), (amount.origin, amount.counter_name, amount.schema_version))
                self.assertEqual('$source', node.effects[0]['source'])
                self.assertEqual('$target.0', node.effects[0]['target'])
                self.assertIn('quantity_expression.scalar_effect_amount', node.capability_dependencies)

    def test_source_counter_pronouns_do_not_bind_unowned_target_or_event_subjects(self):
        for text in (
            'It deals damage equal to the number of charge counters on it to any target.',
            'Target creature gets +X/+X until end of turn, where X is the number of +1/+1 counters on it.',
            'Whenever another creature enters, it deals damage equal to the number of charge counters on it to any target.',
            '{T}: Target creature deals damage equal to the number of charge counters on it to any target.',
            '{T}: It deals damage equal to the number of charge counters on that creature to any target.',
            '{T}: It deals damage equal to the number of charge counters on it to any target. Invent another result.',
        ):
            with self.subTest(text=text):self.assertNotEqual('exact', self.compile(text).status)

    def test_explicit_counter_spelling_keeps_its_existing_descriptor(self):
        explicit = self.compile('{T}: This artifact deals damage equal to the number of charge counters on this artifact to any target.')
        pronoun = self.compile('{T}: This artifact deals damage equal to the number of charge counters on it to any target.')
        self.assertEqual('exact', explicit.status);self.assertEqual('exact', pronoun.status)
        self.assertEqual(explicit.faces[0].nodes[0].effects, pronoun.faces[0].nodes[0].effects)
        self.assertEqual(explicit.faces[0].nodes[0].target_schema, pronoun.faces[0].nodes[0].target_schema)

    def test_omitting_source_subject_proof_mutant_loses_the_new_explicit_form(self):
        import quorune.compiler.scalar_effect_amounts as owner
        original = owner.re.match
        text = '{T}: This artifact deals damage equal to the number of time counters on it to each creature and each player.'
        def witness():self.assertEqual('exact', self.compile(text).status)
        witness()
        def missing(pattern, string, *args, **kwargs):
            if pattern.endswith(r' (?:deals|gets|gains)\b'):return None
            return original(pattern, string, *args, **kwargs)
        with patch.object(owner.re, 'match', side_effect=missing), self.assertRaises(AssertionError):witness()
