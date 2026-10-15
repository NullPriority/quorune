from __future__ import annotations

import unittest

from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry


def role_creator(text: str) -> CardRecord:
    return CardRecord(oracle_id='00000000-0000-4000-8000-000000311060', name='Generic Role Creator',
        mana_cost='{G}', mana_value=1, type_line='Sorcery', oracle_text=text,
        power=None, toughness=None, loyalty=None, defense=None, colors=('G',), color_identity=('G',),
        keywords=(), produced_mana=(), layout='normal', released_at='2026-01-01',
        legalities={'commander': 'legal'}, faces=(), raw={})


class PredefinedRoleCompilerTests(unittest.TestCase):
    def test_complete_role_definitions_keep_source_spans_and_child_authority(self):
        from quorune.compiler.role_token_nodes import role_definition_children
        from quorune.compiler.ir_model import SourceSpan
        registry = load_default_capability_registry()
        for role in ('Cursed', 'Monster', 'Royal', 'Sorcerer', 'Virtuous', 'Wicked', 'Young Hero'):
            text = 'Create a ' + role + ' Role token attached to target creature you control.'
            with self.subTest(role=role):
                definition = role_definition_children(role=role, record=role_creator(text), face_id='front',
                    node_id='n1', line=text, span=SourceSpan(0, len(text), 1), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertIsNotNone(definition)
                fragments, nodes = definition
                self.assertTrue(all(n.text == text and n.span.start == 0 and n.span.end == len(text) for n in nodes))
                self.assertTrue(fragments)
                self.assertTrue(all(n.kind.startswith('granted_') for n in nodes))
                from quorune.compiler.generated_program_identity import generated_ability_id
                from quorune.semantic_runtime.ability_fragments import fragments_from_descriptors
                from quorune.ability_fragments import granted_triggered_specs
                child_keys = {'00000000-0000-4000-8000-000000311060:' + generated_ability_id(
                    kind=n.kind, face_id='front', line=1, static_declaration=bool(n.handlers), node_id=n.node_id)
                    for n in nodes}
                for node in nodes:
                    for descriptor in node.handlers:
                        raw = descriptor.get('modifier', {}).get('add_ability_fragments', [])
                        for fragment in granted_triggered_specs(raw):
                            self.assertIn(fragment.semantic_key, child_keys)
                            self.assertTrue(fragment.semantic_key.endswith(':' + fragment.ability_id))

    def test_role_definition_withdrawal_and_creator_fragment_isolation(self):
        from unittest.mock import patch
        from quorune.compiler.role_token_nodes import role_definition_children, role_creation_parts
        from quorune.compiler.ir_model import SourceSpan
        text = 'Create a Monster Role token attached to target creature you control.'
        args = dict(role='Monster', record=role_creator(text), face_id='front', node_id='n1', line=text,
            span=SourceSpan(0, len(text), 1), capability_registry=load_default_capability_registry(),
            capability_profile='commander_review')
        with patch('quorune.oracle_ir.compile_oracle_card') as compiler:
            compiler.return_value.status = 'unresolved'
            self.assertIsNone(role_definition_children(**args))
        for text in ('Create a Unknown Role token attached to target creature you control.',
            'Create a Monster Role token attached to that creature.',
            'Create two Monster Role tokens attached to target creature you control.'):
            self.assertIsNone(role_creation_parts(text))
        from quorune.compiled_ability_fragments import compiled_static_ability_fragments
        from types import SimpleNamespace
        program = SimpleNamespace(key='fixture:static:front:n1:role:1', provenance={'granted_only': True})
        registry = SimpleNamespace(runtime_handler_programs_for_oracle=lambda *_args, **_kwargs: (),
            programs_for_oracle=lambda *_args, **_kwargs: (program,))
        host = SimpleNamespace(semantics=registry, card_record=lambda _card: role_creator('Draw a card.'),
            semantic_program_is_current_trusted=lambda _program: self.fail('Granted-only creator child was consulted'))
        program.handlers = ({'handler_id': 'continuous.attached.fixed-characteristics.v1'},)
        card = SimpleNamespace(active_face=None)
        self.assertEqual((), compiled_static_ability_fragments(host, card))

    def test_role_static_children_lower_through_existing_program_identity(self):
        from quorune.compiler.role_token_nodes import role_definition_children
        from quorune.compiler.ir_model import SourceSpan, OracleFaceIR, OracleCardIR
        from quorune.compiler.program_generation import _generated_programs_from_ir
        from types import SimpleNamespace
        text = 'Create a Sorcerer Role token attached to target creature you control.'
        registry = load_default_capability_registry()
        record = role_creator(text)
        fragments, nodes = role_definition_children(role='Sorcerer', record=record, face_id='front', node_id='n1',
            line=text, span=SourceSpan(0, len(text), 1), capability_registry=registry, capability_profile='commander_review')
        ir = OracleCardIR(oracle_id=record.oracle_id, card_name=record.name, schema_version=1,
            compiler_version='oracle-ir-v365', oracle_hash='0' * 64, semantic_hash='0' * 64,
            faces=(OracleFaceIR(face_id='front', face_name=record.name, oracle_text=text, nodes=nodes, residuals=()),))
        db = SimpleNamespace(rulings=lambda _card: ())
        programs = _generated_programs_from_ir(db, record, ir, trust_level='provisional',
            capability_registry=registry, capability_profile='commander_review', has_rules_derived_trust_carrier=False)
        self.assertEqual(2, len(programs))
        self.assertTrue(all(program.provenance['granted_only'] for program in programs))
        self.assertTrue(any(program.ability_id == 'static:front:n1:role:1' for program in programs))
        self.assertTrue(any(program.ability_id == 'trigger:front:n1:granted:token:2' for program in programs))


if __name__ == '__main__':
    unittest.main()
