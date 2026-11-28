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
from quorune.standard_token_abilities import TOKEN_ABILITY_PROFILE_FIELD


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


class AdditionalTokenDescriptorContractTests(unittest.TestCase):
    def test_predefined_replacement_preserves_the_executable_token_profile(self):
        compiled = static_additional_token_replacement_handler(
            "If you would create one or more artifact tokens, instead "
            "create those tokens plus a Food token."
        )
        self.assertIsNotNone(compiled)
        token = GenericAdditionalTokenReplacementHandler().validate(compiled[1]).token
        self.assertEqual("two_tap_sac_gain_three_life_v1", token.ability_profile)
        self.assertEqual(1, len(token.characteristics()["activated_abilities"]))


    def test_matching_additions_use_current_pending_recipes_and_keep_entry_associations(self):
        from quorune.replacement.operations import MATCH_PENDING_TOKEN_QUANTITY, operation_from_dict
        from quorune.semantic_runtime.token_replacements import (
            MATCH_PENDING_ADDITIONAL_TOKEN_CAPABILITY, MATCH_PENDING_ADDITIONAL_TOKEN_HANDLER_ID,
            SemanticNodeError, resolve_token_creation_replacements,
        )

        reference = (
            "If one or more tokens would be created under your control, those tokens plus that many "
            "1/1 green Squirrel creature tokens are created instead."
        )
        _, descriptor, capability = static_additional_token_replacement_handler(reference)
        self.assertEqual(MATCH_PENDING_ADDITIONAL_TOKEN_CAPABILITY, capability)
        self.assertEqual(MATCH_PENDING_TOKEN_QUANTITY, descriptor["quantity"])
        handler = GenericAdditionalTokenReplacementHandler(
            handler_id=MATCH_PENDING_ADDITIONAL_TOKEN_HANDLER_ID, schema_version=3,
            capability_dependencies=(capability,),
        )
        for changed in ({"quantity": 1}, {"condition": {**descriptor["condition"], "created_types_all": ["artifact"]}}):
            with self.assertRaises(SemanticNodeError):
                handler.validate({**descriptor, **changed})
        context = TokenCreationReplacementContext("source", "A", "A", ("artifact", "creature"))
        effect = handler.replacement_effect(descriptor, context)
        op, = effect.operations
        restored = operation_from_dict(op.to_dict())
        self.assertEqual(op, restored)
        originals = (
            {"name": "Servo Token", "quantity": 2, "tapped": True, "_attacking_group": "assigned",
             "temporary_keywords": ["Haste"], "characteristics": {"keywords": ["Deathtouch"]}},
            {"name": "Treasure Token", "quantity": 3, "characteristics": {"type_line": "Token Artifact — Treasure"}},
        )
        result = resolve_token_creation_replacements(
            event_id="create", controller="A", tokens=originals, created_types=("artifact", "creature"),
            created_subtypes=("servo", "treasure"), effects=(effect,), apnap_order=("A", "B"),
        )
        self.assertIsNone(result.pending)
        additions = tuple(result.tokens[2:])
        self.assertEqual([2, 3], [token["quantity"] for token in additions])
        self.assertEqual({"Squirrel Token"}, {token["name"] for token in additions})
        self.assertEqual(("assigned", True, ("Haste",)),
                         (additions[0]["_attacking_group"], additions[0]["tapped"], tuple(additions[0]["temporary_keywords"])))
        self.assertNotIn("_attacking_group", additions[1])
        self.assertFalse(additions[1].get("tapped"))
        self.assertFalse(any("Deathtouch" in row["characteristics"]["keywords"] for row in additions))
        self.assertIn("Deathtouch", result.tokens[0]["characteristics"]["keywords"])

    def test_matching_additional_token_grammar_withdrawal_is_detected(self):
        from unittest.mock import patch

        reference = (
            "If one or more tokens would be created under your control, those tokens plus that many "
            "1/1 green Squirrel creature tokens are created instead."
        )
        self.assertIsNotNone(static_additional_token_replacement_handler(reference))
        for text in (reference.replace("your control", "an opponent's control"),
                     reference.replace("those tokens plus that many", "instead of those tokens create that many"),
                     reference.replace("that many", "twice that many")):
            self.assertIsNone(static_additional_token_replacement_handler(text))
        with patch("quorune.compiler.token_templates._matching_pending_additional_tokens", return_value=None):
            self.assertIsNone(static_additional_token_replacement_handler(reference))


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
                "Treasure Token",
            ),
            (
                "If one or more tokens would be created under your control, "
                "those tokens plus an additional Food token are created "
                "instead.",
                [],
                [],
                "Food Token",
            ),
            (
                "If one or more artifact tokens would be created under your "
                "control, those tokens plus an additional 1/1 colorless "
                "Thopter artifact creature token with flying are created "
                "instead.",
                ["artifact"],
                [],
                "Thopter Token",
            ),
            (
                "If you would create one or more artifact tokens, instead "
                "create those tokens plus an additional Map token.",
                ["artifact"],
                [],
                "Map Token",
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
                token = GenericAdditionalTokenReplacementHandler().validate(descriptor).token
                if token_name == "Food Token":
                    self.assertEqual("two_tap_sac_gain_three_life_v1", token.ability_profile)
                    self.assertEqual(1, len(token.characteristics()["activated_abilities"]))
                    self.assertNotIn(TOKEN_ABILITY_PROFILE_FIELD, descriptor["token"])
                if token_name in {"Treasure Token", "Food Token", "Map Token"}:
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
