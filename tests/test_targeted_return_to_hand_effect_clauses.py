from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.direct_target import DirectPermanentTargetSpec
from quorune.compiler.return_to_hand_templates import (
    ReturnToHandTarget,
    TargetedReturnToHandEffectTemplate,
    targeted_return_to_hand_effect_template,
)
from quorune.compiler.self_return_templates import (
    FIXED_SELF_RETURN_MECHANIC,
    fixed_self_return_effect_template,
)
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantics import SemanticRegistry
from quorune.targets import TargetGroup
from scripts.build_test_database import build_fixture_database


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "targeted-return-to-hand.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT
            / "tests"
            / "fixtures"
            / "targeted-return-to-hand-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class TargetedReturnToHandTemplateTests(unittest.TestCase):
    def test_targeted_return_template_is_immutable_and_copy_isolated(self):
        template = TargetedReturnToHandEffectTemplate(
            ReturnToHandTarget.CREATURE
        )

        self.assertEqual("return-target-creature-v2", template.template_id)
        self.assertEqual(
            ({"op": "bounce", "card": "$target.0"},),
            template.effects,
        )
        schema = template.target_schema
        schema["types_any"].append("artifact")
        effects = template.effects
        effects[0]["op"] = "exile"
        self.assertEqual(["creature"], template.target_schema["types_any"])
        self.assertEqual("bounce", template.effects[0]["op"])
        self.assertEqual(
            ["land"],
            TargetedReturnToHandEffectTemplate(
                ReturnToHandTarget.NONLAND_PERMANENT
            ).target_schema["types_none"],
        )
        with self.assertRaisesRegex(ValueError, "target"):
            TargetedReturnToHandEffectTemplate(  # type: ignore[arg-type]
                "creature"
            )

    def test_return_whole_clause_parser_accepts_only_closed_direct_targets(self):
        for target in ReturnToHandTarget:
            with self.subTest(target=target):
                template = targeted_return_to_hand_effect_template(
                    f"Return target {target.value} to its owner's hand."
                )
                self.assertIsNotNone(template)
                assert template is not None
                self.assertEqual(target, template.target)
        for text in (
            "Return up to one target creature to its owner's hand.",
            "You may return target creature to its owner's hand.",
            "Return another target creature to its owner's hand.",
            "Return target tapped creature to its owner's hand.",
            "Return target creature to its controller's hand.",
            "Return target creature to its owners hand.",
            "Return target creature card from a graveyard to its owner's hand.",
            "Return all creatures to their owners' hands.",
            "Return target creature to its owner's hand. Draw a card.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(targeted_return_to_hand_effect_template(text))

    def test_combat_state_return_reuses_shared_direct_target_schema(self):
        template = targeted_return_to_hand_effect_template(
            "Return target blocking creature to its owner's hand."
        )

        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(
            DirectPermanentTargetSpec(
                types_any=("creature",), combat_state="blocking"
            ),
            template.target_spec,
        )
        self.assertEqual(
            "return-target-creature-blocking-v2", template.template_id
        )
        self.assertEqual("blocking", template.target_schema["combat_state"])

    def test_nonland_target_uses_the_shared_exact_type_predicate(self):
        group = TargetGroup.from_mapping(
            TargetedReturnToHandEffectTemplate(
                ReturnToHandTarget.NONLAND_PERMANENT
            ).target_schema
        )

        self.assertTrue(
            group.matches_type_characteristics(
                types=("artifact",),
                subtypes=(),
                supertypes=(),
            )
        )
        self.assertFalse(
            group.matches_type_characteristics(
                types=("artifact", "land"),
                subtypes=(),
                supertypes=(),
            )
        )


class TargetedReturnToHandCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.base = cls.db.lookup("Lightning Greaves")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, oracle_text: str, *, type_line: str = "Instant"):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=oracle_text,
                type_line=type_line,
                keywords=(),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_spell_trigger_and_activated_contexts_share_targeted_return_lowering(self):
        contexts = (
            (
                "Return target creature to its owner's hand.",
                "Instant",
                "spell_ability",
                "return-target-creature-v2",
            ),
            (
                "When this creature enters, return target artifact to its owner's hand.",
                "Creature — Test",
                "triggered_ability",
                "return-target-artifact-v2",
            ),
            (
                "{2}{U}, {T}: Return target nonland permanent to its owner's hand.",
                "Creature — Test",
                "activated_ability",
                "return-target-nonland-permanent-v2",
            ),
        )
        for text, type_line, kind, template_id in contexts:
            with self.subTest(kind=kind, text=text):
                ir = self.compile(text, type_line=type_line)
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(template_id, node.template_id)
                expected_capabilities = {
                    "permanent.return.owner_hand",
                    "target.revalidate_resolution",
                }
                if template_id == "return-target-nonland-permanent-v2":
                    expected_capabilities.add(
                        "target.permanent.characteristic_predicate"
                    )
                self.assertEqual(
                    expected_capabilities,
                    set(node.capability_dependencies)
                    - {
                        "trigger.event.normalized_zone_change",
                        "trigger.placement.apnap",
                    },
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_fixed_self_return_activation_is_capability_closed(self):
        text = "{2}{U}: Return this creature to its owner's hand."
        ir = self.compile(text, type_line="Creature — Test")
        node = ir.faces[0].nodes[0]

        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertTrue(node.exact)
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual("bounce-self-creature-v1", node.template_id)
        self.assertEqual((FIXED_SELF_RETURN_MECHANIC,), node.mechanics)
        self.assertEqual(
            ({"op": "bounce", "card": "$source"},),
            node.effects,
        )
        self.assertIn(
            "permanent.return.owner_hand",
            node.capability_dependencies,
        )

        template = fixed_self_return_effect_template(
            "Return this enchantment to its owner's hand."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual("bounce-self-enchantment-v1", template.template_id)
        for effects, schema, mechanics in (
            (template.compiled()[1], {"count": 1}, template.compiled()[3]),
            (({"op": "bounce", "card": "$target.0"},), None, template.compiled()[3]),
            (template.compiled()[1], None, ("cr-400-general",)),
        ):
            with self.subTest(effects=effects, schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=schema,
                        mechanic_ids=mechanics,
                    )
                )

    def test_unsupported_return_variants_remain_material_residuals(self):
        for text in (
            "Return up to one target creature to its owner's hand.",
            "You may return target creature to its owner's hand.",
            "Return another target creature to its owner's hand.",
            "Return target tapped creature to its owner's hand.",
            "Return target creature card from a graveyard to its owner's hand.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_targeted_return_shape_mutants_fail_closed(self):
        template = TargetedReturnToHandEffectTemplate(
            ReturnToHandTarget.NONLAND_PERMANENT
        )
        expected = {
            "permanent.return.owner_hand",
            "target.permanent.characteristic_predicate",
            "target.revalidate_resolution",
        }
        self.assertEqual(
            expected,
            set(
                capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=template.target_schema,
                    mechanic_ids=template.mechanics,
                )
            ),
        )
        malformed_effects = (
            ({"op": "bounce", "card": "$target.1"},),
            ({"op": "bounce", "card": "$source"},),
            (
                {
                    "op": "bounce",
                    "card": "$target.0",
                    "reason": "open grammar",
                },
            ),
            ({"op": "move", "card": "$target.0"},),
        )
        for effects in malformed_effects:
            with self.subTest(effects=effects):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        malformed_schemas = (
            {**template.target_schema, "zones": ["graveyard"]},
            {**template.target_schema, "count": 2},
            {**template.target_schema, "types_none": []},
            {**template.target_schema, "types_any": ["creature"]},
            {**template.target_schema, "controller": "opponent"},
        )
        for schema in malformed_schemas:
            with self.subTest(schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=template.effects,
                        target_schema=schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        self.assertFalse(
            capability_dependencies_for_node(
                effects=template.effects,
                target_schema=template.target_schema,
                mechanic_ids=("cr-115-targets",),
            )
        )

    def test_generated_return_program_is_capability_closed(self):
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (self.db.lookup("Unsummon"),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_effect_programs=True,
        )
        programs = [program for program in registry.programs() if program.effects]
        self.assertEqual(1, result["exact_effect_programs_promoted"])
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertTrue(
            {
                "permanent.return.owner_hand",
                "target.revalidate_resolution",
            }.issubset(programs[0].capability_dependencies)
        )


if __name__ == "__main__":
    unittest.main()
