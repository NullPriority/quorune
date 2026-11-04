"""CR508.5/509: the defending player is an occurrence participant, not a target."""
import unittest
from types import SimpleNamespace
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record


class CombatDefendingPlayerCompilerTests(unittest.TestCase):
    def test_defending_player_discard_withdrawal_and_binder_omission_fail_closed(self):
        import json
        from unittest.mock import patch
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        card=record('Whenever this creature becomes blocked, defending player discards a card.','Creature — Human')
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id']=='trigger.result.fixed_context_player_discard').update(
            status='blocked',blockers=['Affected-player result withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(record('Whenever this creature becomes blocked, defending player loses 4 life.',
            'Creature — Human'),capability_registry=registry).status)
        with patch('quorune.compiler.affected_player_discard_templates.defending_player_discard_result',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=load_default_capability_registry()).status)

    def test_existing_afflict_owner_retains_its_exact_instruction_and_rejects_changed_shapes(self):
        from quorune.compiler.keyword_event_effect_nodes import FixedKeywordEventEffectSpec
        from quorune.rules.combat_defending_players import combat_defending_player_runtime_dependencies
        spec=FixedKeywordEventEffectSpec('afflict',2)
        program=SimpleNamespace(event=spec.event,event_condition=spec.event_condition,active_zone='battlefield',
            target_schema=spec.target_schema,coverage=('afflict','fixed-keyword-event-effect'),effects=spec.effects)
        self.assertEqual(('trigger.keyword.afflict.fixed',),combat_defending_player_runtime_dependencies(program))
        for change in ({'coverage':()}, {'event_condition':None}, {'target_schema':{}},
                {'effects':({'op':'lose_life','player':'$context.defending_player','amount':True},)},
                {'effects':({**spec.effects[0],'unowned':True},)}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                combat_defending_player_runtime_dependencies(SimpleNamespace(**{**vars(program),**change}))

    def test_original_eternal_of_harsh_truths_remains_normally_capability_closed(self):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        from scripts.build_test_database import build_fixture_database
        registry=load_default_capability_registry();semantics=SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'afflict-retention.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/unblocked-attacker-cards.json'],path)
            with CardDatabase(path) as db:
                card=db.by_oracle_id('5c09d646-afea-4fd0-9752-04820b81cc5b')
                register_generated_programs(db,semantics,[card],trust_level='provisional',capability_registry=registry,
                    capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                program=compile_best_available_card_program(db,card,semantic_registry=semantics,
                    capability_registry=registry,capability_profile='commander_review')
                bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
                self.assertEqual('capability_closed',bound['trust_basis'])

    def test_defending_player_scope_rejects_wrong_events_and_unproved_attacker_identity(self):
        from quorune.rules.combat_defending_players import combat_defending_player_runtime_dependencies
        from quorune.rules.event_player_result_shapes import _event_player_reference_is_closed
        condition={'field':'card','op':'eq','value':'$source.ref'}
        program=SimpleNamespace(event='creature.becomes_blocked',event_condition=condition,active_zone='battlefield',
            target_schema=None,coverage=('fixed-event-player-result',),effects=({'op':'lose_life','player':'$context.defending_player','amount':4},))
        self.assertIn('resolution.event_player.fixed_result',combat_defending_player_runtime_dependencies(program))
        for change in ({'event_condition':None},{'event_condition':{'any':[condition,{'field':'card','op':'eq','value':'other'}]}},
                {'active_zone':'hand'},{'coverage':()}):
            with self.subTest(change=change),self.assertRaises(ValueError):combat_defending_player_runtime_dependencies(SimpleNamespace(**{**vars(program),**change}))
        self.assertFalse(_event_player_reference_is_closed(SimpleNamespace(**{**vars(program),'event':'creature.blocks'}),'$context.defending_player'))

    def test_defending_player_occurrence_binder_preserves_blocked_and_unblocked_and_rejects_blocker_role(self):
        from quorune.compiler.event_player_results import _occurrence_player
        for event,variant in (('creature.becomes_blocked','this_creature_becomes_blocked'),('creature.unblocked','source_attacks_unblocked')):
            self.assertEqual('$context.defending_player',_occurrence_player(event,{},variant,'defending player'))
        self.assertIsNone(_occurrence_player('creature.blocks',{},'this_creature_blocks','defending player'))

    def test_blocked_and_unblocked_fixed_results_bind_the_actual_defending_player(self):
        registry=load_default_capability_registry()
        for head,body in (('becomes blocked','defending player loses 4 life.'),
                ('becomes blocked','defending player mills three cards.'),
                ('becomes blocked','defending player discards a card.'),
                ('becomes blocked','defending player sacrifices a land of their choice.'),
                ("attacks and isn't blocked",'defending player discards a card.')):
            with self.subTest(head=head,body=body):
                ir=compile_oracle_card(record('Whenever this creature '+head+', '+body,'Creature — Human'),capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals);self.assertIsNone(ir.faces[0].nodes[0].target_schema)
