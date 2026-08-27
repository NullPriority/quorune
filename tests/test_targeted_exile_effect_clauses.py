from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.direct_target import DirectPermanentTargetSpec
from quorune.compiler.exile_templates import (
    TargetedExileEffectTemplate,
    targeted_exile_effect_template,
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
from scripts.build_test_database import build_fixture_database


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "targeted-exile.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "targeted-exile-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class TargetedExileTemplateTests(unittest.TestCase):
    def test_targeted_exile_template_is_immutable_and_copy_isolated(self):
        template = TargetedExileEffectTemplate(
            DirectPermanentTargetSpec(types_any=("creature",))
        )

        self.assertEqual("exile-target-creature-v2", template.template_id)
        self.assertEqual(
            ({"op": "exile_permanent", "card": "$target.0"},),
            template.effects,
        )
        schema = template.target_schema
        schema["types_any"].append("artifact")
        effects = template.effects
        effects[0]["op"] = "destroy"
        self.assertEqual(["creature"], template.target_schema["types_any"])
        self.assertEqual("exile_permanent", template.effects[0]["op"])
        self.assertEqual(
            ["land"],
            TargetedExileEffectTemplate(
                DirectPermanentTargetSpec(types_none=("land",))
            ).target_schema["types_none"],
        )
        with self.assertRaisesRegex(ValueError, "target"):
            TargetedExileEffectTemplate(  # type: ignore[arg-type]
                "creature"
            )

    def test_exile_whole_clause_parser_accepts_only_closed_direct_targets(self):
        cases = (
            (
                "Exile target artifact, creature, or enchantment.",
                {"types_any": ["artifact", "creature", "enchantment"]},
            ),
            (
                "Exile target tapped creature.",
                {
                    "types_any": ["creature"],
                    "state_predicate": {
                        "entered_this_turn": False,
                        "tapped": True,
                        "counter_name": None,
                        "minimum_counter_count": None,
                    },
                },
            ),
            (
                "Exile target Spirit.",
                {"subtypes_any": ["spirit"]},
            ),
            (
                "Exile another target creature.",
                {"types_any": ["creature"], "source_exclusion": True},
            ),
            (
                "Exile target creature you don't control.",
                {
                    "types_any": ["creature"],
                    "controller_relation": "opponent",
                },
            ),
            (
                "Exile target nonland permanent.",
                {"types_none": ["land"]},
            ),
            (
                "Exile target attacking creature.",
                {
                    "types_any": ["creature"],
                    "combat_state": "attacking",
                },
            ),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                template = targeted_exile_effect_template(text)
                self.assertIsNotNone(template)
                assert template is not None
                self.assertTrue(expected.items() <= template.target_schema.items())
        for text in (
            "Exile up to one target creature.",
            "You may exile target creature.",
            "Exile target creature or Spacecraft.",
            "Exile target creature card from a graveyard.",
            "Exile all creatures.",
            "Exile target creature, then return it to the battlefield.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(targeted_exile_effect_template(text))


class TargetedExileCompilerTests(unittest.TestCase):
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

    def test_spell_trigger_and_activated_contexts_share_targeted_exile_lowering(
        self,
    ):
        contexts = (
            (
                "Exile target artifact, creature, or enchantment.",
                "Instant",
                "spell_ability",
                "exile-target-artifact-or-creature-or-enchantment-v2",
                {"target.permanent.characteristic_predicate"},
            ),
            (
                "When this creature enters, exile target tapped creature.",
                "Creature — Test",
                "triggered_ability",
                "exile-target-creature-tapped-v2",
                {"state_query.permanent.public_state_predicate"},
            ),
            (
                "{3}, {T}: Exile target nonland permanent.",
                "Creature — Test",
                "activated_ability",
                "exile-target-nonland-permanent-v2",
                {"target.permanent.characteristic_predicate"},
            ),
        )
        for (
            text,
            type_line,
            kind,
            template_id,
            predicate_capabilities,
        ) in contexts:
            with self.subTest(kind=kind, text=text):
                ir = self.compile(text, type_line=type_line)
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(template_id, node.template_id)
                self.assertEqual(
                    {
                        "permanent.exile.effect",
                        *predicate_capabilities,
                        "target.revalidate_resolution",
                    },
                    set(node.capability_dependencies)
                    - {
                        "trigger.event.normalized_zone_change",
                        "trigger.placement.apnap",
                    },
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_unsupported_exile_variants_remain_material_residuals(self):
        for text in (
            "Exile target creature or Spacecraft.",
            "Exile target creature, then return it to the battlefield.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_targeted_exile_shape_mutants_fail_closed(self):
        template = TargetedExileEffectTemplate(
            DirectPermanentTargetSpec(
                types_any=("artifact", "creature", "enchantment")
            )
        )
        expected = {
            "permanent.exile.effect",
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
            ({"op": "exile_permanent", "card": "$target.1"},),
            ({"op": "exile_permanent", "card": "$source"},),
            (
                {
                    "op": "exile_permanent",
                    "card": "$target.0",
                    "reason": "open grammar",
                },
            ),
            ({"op": "exile", "card": "$target.0"},),
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
            {**template.target_schema, "types_any": ["noncreature"]},
            {
                **template.target_schema,
                "subtypes_any": ["vehicle"],
            },
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

    def test_pinned_direct_target_exile_family_harvests_seven_cards(self):
        expected = {
            "Angelic Edict",
            "Angelic Purge",
            "Blessed Light",
            "Excoriate",
            "Expel",
            "Iona's Judgment",
            "Undead Slayer",
        }
        for card_name in sorted(expected):
            with self.subTest(card=card_name):
                ir = compile_oracle_card(
                    self.db.lookup(card_name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                self.assertTrue(
                    any(
                        "permanent.exile.effect"
                        in node.capability_dependencies
                        for face in ir.faces
                        for node in face.nodes
                    )
                )

    def test_generated_targeted_exile_program_is_capability_closed(self):
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (self.db.lookup("Scour from Existence"),),
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
                "permanent.exile.effect",
                "target.revalidate_resolution",
            }.issubset(programs[0].capability_dependencies)
        )


if __name__ == "__main__":
    unittest.main()
