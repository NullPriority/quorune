from __future__ import annotations

import unittest

from common import DB_PATH
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.token_templates import (
    static_additional_token_replacement_handler,
)
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import (
    GenericAdditionalTokenReplacementHandler,
    TokenCreationReplacementContext,
)
from quorune.semantics import SemanticRegistry


def additional_token_record(
    name: str,
    oracle_text: str,
    suffix: int,
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=name,
        mana_cost="{2}",
        mana_value=2.0,
        type_line="Artifact",
        oracle_text=oracle_text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class AdditionalTokenCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_closed_family_lowers_exact_filters_and_definitions(self):
        cases = (
            (
                "If you would create one or more Treasure tokens, instead "
                "create those tokens plus an additional Treasure token.",
                [],
                ["treasure"],
                "Treasure",
            ),
            (
                "If one or more tokens would be created under your control, "
                "those tokens plus an additional Food token are created "
                "instead.",
                [],
                [],
                "Food",
            ),
            (
                "If one or more artifact tokens would be created under your "
                "control, those tokens plus an additional 1/1 colorless "
                "Thopter artifact creature token with flying are created "
                "instead.",
                ["artifact"],
                [],
                "Thopter",
            ),
            (
                "If you would create one or more artifact tokens, instead "
                "create those tokens plus an additional Map token.",
                ["artifact"],
                [],
                "Map",
            ),
        )
        for text, types, subtypes, token_name in cases:
            with self.subTest(text=text):
                compiled = static_additional_token_replacement_handler(text)
                self.assertIsNotNone(compiled)
                _, descriptor, capability = compiled
                self.assertEqual(
                    "token.creation.additional_replacement", capability
                )
                self.assertEqual(
                    "replacement.token.additional.v2",
                    descriptor["handler_id"],
                )
                self.assertEqual(2, descriptor["schema_version"])
                self.assertEqual(types, descriptor["condition"]["created_types_all"])
                self.assertEqual(
                    subtypes,
                    descriptor["condition"]["created_subtypes_all"],
                )
                self.assertEqual(token_name, descriptor["token"]["name"])
                self.assertNotIn("oracle_text", descriptor["token"])
                if token_name in {"Treasure", "Food", "Map"}:
                    self.assertIn("display_text", descriptor["token"])

    def test_nearby_unsupported_wording_remains_residual(self):
        unsupported = (
            "If you would create one or more tokens, you may create those "
            "tokens plus an additional Food token instead.",
            "If you would create one or more tokens, create twice that many "
            "of those tokens instead.",
            "If you would create one or more modified tokens, instead create "
            "those tokens plus an additional Food token.",
            "If an opponent would create one or more tokens, instead create "
            "those tokens plus an additional Food token.",
            "If you would create one or more tokens, instead create those "
            "tokens plus an additional tapped Food token.",
            "If you would create one or more tokens, instead create those "
            "tokens plus X additional Food tokens.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(
                    static_additional_token_replacement_handler(text)
                )

    def test_pinned_family_emits_exact_source_spanned_runtime_nodes(self):
        fixtures = (
            additional_token_record(
                "Generic Treasure Replacement Fixture",
                "If you would create one or more Treasure tokens, instead "
                "create those tokens plus an additional Treasure token.",
                450001,
            ),
            additional_token_record(
                "Generic Food Replacement Fixture",
                "If one or more tokens would be created under your control, "
                "those tokens plus an additional Food token are created "
                "instead.",
                450002,
            ),
            additional_token_record(
                "Generic Thopter Replacement Fixture",
                "If one or more artifact tokens would be created under your "
                "control, those tokens plus an additional 1/1 colorless "
                "Thopter artifact creature token with flying are created "
                "instead.",
                450003,
            ),
            additional_token_record(
                "Generic Map Replacement Fixture",
                "If you would create one or more artifact tokens, instead "
                "create those tokens plus an additional Map token.",
                450004,
            ),
        )
        for record in fixtures:
            with self.subTest(card_name=record.name):
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                nodes = [
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.event == "token.create"
                ]
                self.assertEqual(1, len(nodes))
                node = nodes[0]
                self.assertTrue(node.exact)
                self.assertEqual(
                    record.oracle_text[node.span.start : node.span.end],
                    node.text,
                )
                self.assertEqual(
                    "replacement.token.additional.v2",
                    node.handlers[0]["handler_id"],
                )
                programs = generated_programs(
                    self.db,
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                program = next(
                    value for value in programs if value.event == "token.create"
                )
                self.assertEqual(
                    {
                        "start": node.span.start,
                        "end": node.span.end,
                        "line": node.span.line,
                    },
                    program.provenance["source_span"],
                )

    def test_generic_handler_matches_types_subtypes_and_unqualified_events(self):
        handler = GenericAdditionalTokenReplacementHandler()
        treasure = static_additional_token_replacement_handler(
            "If you would create one or more Treasure tokens, instead create "
            "those tokens plus an additional Treasure token."
        )[1]
        artifact = static_additional_token_replacement_handler(
            "If you would create one or more artifact tokens, instead create "
            "those tokens plus an additional Map token."
        )[1]
        any_token = static_additional_token_replacement_handler(
            "If you would create one or more tokens, instead create those "
            "tokens plus an additional Food token."
        )[1]
        context = TokenCreationReplacementContext(
            source_ref="A-source",
            source_controller="A",
            event_controller="A",
            created_types=("artifact",),
            created_subtypes=("treasure",),
        )
        self.assertTrue(handler.lower(treasure, context))
        self.assertTrue(handler.lower(artifact, context))
        self.assertTrue(handler.lower(any_token, context))
        creature = TokenCreationReplacementContext(
            source_ref="A-source",
            source_controller="A",
            event_controller="A",
            created_types=("creature",),
            created_subtypes=("cat",),
        )
        self.assertFalse(handler.lower(treasure, creature))
        self.assertFalse(handler.lower(artifact, creature))
        self.assertTrue(handler.lower(any_token, creature))

    def test_generated_and_reviewed_equivalent_handler_is_not_registered_twice(self):
        record = self.db.lookup("Stridehangar Automaton")
        registry = SemanticRegistry()
        result = register_generated_programs(
            self.db,
            registry,
            (record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        programs = registry.runtime_handler_programs_for_oracle(
            record.oracle_id,
            active_zone="battlefield",
            event="token.create",
        )
        self.assertEqual(1, len(programs))
        self.assertEqual(
            "replacement.token.additional.v2",
            programs[0].handlers[0]["handler_id"],
        )
        self.assertIn('token.creation.additional_replacement',programs[0].capability_dependencies)
        self.assertTrue(programs[0].capability_closure['trusted'])
        self.assertTrue(programs[0].ability_id.startswith('static:front:'))


if __name__ == "__main__":
    unittest.main()
