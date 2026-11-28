from dataclasses import replace
import unittest
from unittest.mock import Mock

from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
from quorune.card_programs.model import CardProgram
from quorune.carddb import CardDatabase, CardRecord
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry


_PAYMENT = "Starting with you, each player may pay any amount of mana."
_DRAW = "Each player draws X cards, where X is the total amount of mana paid this way."
_EFFECT = {"op": "mana_contribution_draw", "schema_version": 1, "player": "$controller"}
_CAPABILITIES = {"draw.mana_contribution", "zone.draw.library_to_hand"}


class ManaContributionDrawCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()

    def setUp(self):
        self.record = CardRecord(
            oracle_id="77777777-7777-4777-8777-777777777777",
            name="Contribution Draft", mana_cost="{U}", mana_value=1,
            type_line="Sorcery", oracle_text=f"  Join forces — {_PAYMENT}  {_DRAW}\t",
            power=None, toughness=None, loyalty=None, defense=None,
            colors=("U",), color_identity=("U",), keywords=("Join forces",),
            produced_mana=(), layout="normal", released_at="2026-01-01",
            legalities={"commander": "legal"}, faces=(), raw={},
        )
        self.db = Mock(spec=CardDatabase)
        self.db.rulings.return_value = []
        self.semantics = SemanticRegistry(include_builtin_packs=False)
        self.capability_options = {
            "capability_registry": self.capabilities, "capability_profile": "commander_review",
        }

    def test_exact_source_span_and_card_program_dependencies(self):
        ir = compile_oracle_card(self.record, **self.capability_options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        span = {"start": 2, "end": len(self.record.oracle_text) - 1, "line": 1}
        self.assertEqual(span, node.to_dict()["span"])
        self.assertEqual(self.record.oracle_text[span["start"]:span["end"]], node.text)
        self.assertEqual("mana-contribution-draw-v1", node.template_id)
        self.assertEqual((_EFFECT,), node.effects)
        self.assertEqual(_CAPABILITIES, set(node.capability_dependencies))

        register_generated_programs(
            self.db, self.semantics, (self.record,),
            promote_exact_effect_programs=True, **self.capability_options,
        )
        registered, = self.semantics.programs_for_oracle(self.record.oracle_id)
        self.assertEqual("trusted", registered.trust_level)
        program = compile_best_available_card_program(
            self.db, self.record, semantic_registry=self.semantics, **self.capability_options,
        )
        self.assertTrue(program.trust_closure["trusted"])
        serialized = program.to_dict()
        ability, = serialized["abilities"]
        self.assertEqual(span, ability["source_span"])
        self.assertEqual([_EFFECT], ability["effect_nodes"])
        self.assertEqual([{"effect_index": 0, "operation": "mana_contribution_draw"}], ability["choices"])
        self.assertIsNone(ability["targets"])
        self.assertEqual(_CAPABILITIES, set(ability["capability_dependencies"]))
        self.assertEqual(serialized, CardProgram.from_dict(serialized).to_dict())

    def test_unsupported_payment_and_result_variants_remain_material(self):
        for body in (
            _PAYMENT,
            f"{_PAYMENT.replace('Starting with you', 'Starting with the active player')} {_DRAW}",
            f"{_PAYMENT.replace('each player', 'each opponent')} {_DRAW}",
            f"{_PAYMENT} {_DRAW.replace('Each player', 'Each paying player')}",
            f"{_PAYMENT} {_DRAW.replace('total amount', 'largest amount')}",
            f"{_PAYMENT} {_DRAW.replace('draws X cards', 'mills X cards')}",
            f"{_PAYMENT} {_DRAW} You win the game.",
        ):
            with self.subTest(body=body):
                ir = compile_oracle_card(
                    replace(self.record, oracle_text=body), **self.capability_options,
                )
                self.assertTrue(ir.material_residuals)
                self.assertNotEqual("exact", ir.status)

    def test_stripped_capability_and_forged_payload_fail_closed(self):
        card_program = compile_best_available_card_program(
            self.db, self.record, semantic_registry=self.semantics, **self.capability_options,
        )
        program, = card_program.abilities
        declared = program.capability_dependencies.copy()
        declared.remove("draw.mana_contribution")
        narrowed_closure = self.capabilities.closure(declared, profile="commander_review")
        self.assertTrue(narrowed_closure.trusted)
        stripped = replace(
            program, capability_dependencies=declared, capability_closure=narrowed_closure.to_dict(),
        )
        reopened = CardProgram.create(
            compiler_version=card_program.compiler_version, oracle_id=card_program.oracle_id,
            card_name=card_program.card_name, faces=card_program.faces,
            oracle_source_hash=card_program.oracle_source_hash,
            rulings_source_hash=card_program.rulings_source_hash,
            abilities=(stripped,), residuals=(), provenance=card_program.provenance,
        )
        self.assertFalse(reopened.trust_closure["trusted"])
        self.assertIn(
            "ability:spell:front:capability:undeclared_runtime_dependency:draw.mana_contribution",
            reopened.trust_closure["blockers"],
        )
        self.assertEqual(_CAPABILITIES, set(typed_result_amount_runtime_dependencies(_EFFECT)))
        for forged in (
            {**_EFFECT, "player": "$target.0"},
            {**_EFFECT, "schema_version": True},
            {**_EFFECT, "count": 99},
        ):
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                typed_result_amount_runtime_dependencies(forged)
