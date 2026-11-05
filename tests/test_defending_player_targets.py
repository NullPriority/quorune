"""CR508.5/603.3d/608.2b: source occurrence binds a target controller."""

import unittest
from types import SimpleNamespace

from quorune.compiler.direct_target import DirectPermanentTargetSpec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record


class DefendingPlayerTargetCompilerTests(unittest.TestCase):
    def test_target_authority_withdrawal_and_relation_adapter_omission_fail_closed(self):
        import json
        from unittest.mock import patch
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        card=record('Whenever this creature attacks, tap target creature defending player controls.','Creature — Human')
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(c for c in raw['capabilities'] if c['id']=='target.revalidate_resolution').update(
            status='blocked',blockers=['Target authority withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(record('Whenever this creature attacks, draw a card.',
            'Creature — Human'),capability_registry=registry).status)
        with patch('quorune.compiler.event_player_target_results.event_player_target_result',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=load_default_capability_registry()).status)

    def test_existing_single_target_leaves_retain_the_defending_player_relation(self):
        registry=load_default_capability_registry()
        for body in ('tap target creature defending player controls.',
                'this creature deals 1 damage to target creature defending player controls.',
                'target creature defending player controls gets -3/-0 until end of turn.',
                'you may destroy target artifact or enchantment defending player controls.'):
            with self.subTest(body=body):
                ir=compile_oracle_card(record('Whenever this creature attacks, '+body,'Creature — Human'),capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                schema=ir.faces[0].nodes[0].target_schema
                self.assertEqual('$context.defending_player',schema['controller_seat'])
                self.assertNotIn('controller_relation',schema)
                self.assertEqual(schema,DirectPermanentTargetSpec.from_target_schema(schema).to_target_schema())

    def test_target_scope_is_proved_without_a_compiler_marker_and_wrong_contexts_fail_closed(self):
        from quorune.rules.event_player_targets import event_player_target_runtime_dependencies
        schema=DirectPermanentTargetSpec(types_any=('creature',),controller_seat='$context.defending_player').to_target_schema()
        program=SimpleNamespace(event='creature.attacks',event_condition={'field':'card','op':'eq','value':'$source.ref'},
            active_zone='battlefield',target_schema=schema,coverage=('trigger-event-normalized-self-attack',),effects=())
        self.assertEqual(('target.revalidate_resolution',),event_player_target_runtime_dependencies(program))
        for change in ({'event':'creature.blocks'},{'active_zone':'hand'},{'coverage':()},
                {'target_schema':{**schema,'controller_relation':'you'}},
                {'target_schema':{**schema,'count':2}}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                event_player_target_runtime_dependencies(SimpleNamespace(**{**vars(program),**change}))
        for player in ('B','$context.unknown',True):
            with self.assertRaises(ValueError):DirectPermanentTargetSpec(types_any=('creature',),controller_seat=player)

    def test_ambiguous_heads_multiple_target_relations_and_unknown_siblings_remain_residual(self):
        registry=load_default_capability_registry()
        for text in ('Whenever this creature blocks, tap target creature defending player controls.',
                'Whenever this creature attacks, tap target creature defending player controls. Rewrite history.',
                'Whenever this creature attacks, tap target creature defending player controls and target artifact defending player controls.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature — Human'),capability_registry=registry).status)
