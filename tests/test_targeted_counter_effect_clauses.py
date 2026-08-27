from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.counter_templates import (
    CounterTarget,
    TargetedCounterEffectTemplate,
    is_intrinsically_uncounterable_spell,
    targeted_counter_effect_template,
)
from quorune.compiler.direct_target import (
    permanent_target_schema,
    stack_target_schema,
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
    database = Path(directory) / "targeted-counter.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "targeted-counter-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class TargetedCounterTemplateTests(unittest.TestCase):
    def test_shared_direct_target_schema_rejects_mixed_or_malformed_predicates(self):
        self.assertEqual(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "count": 1,
                "types_none": ["land"],
            },
            permanent_target_schema(types_none=("land",)),
        )
        for operation in (
            lambda: permanent_target_schema(
                types_any=("creature",),
                types_none=("land",),
            ),
            lambda: permanent_target_schema(types_any="creature"),
            lambda: stack_target_schema(categories=()),
            lambda: stack_target_schema(categories=("spell", "spell")),
            lambda: stack_target_schema(
                categories=("spell",),
                types_any=("creature",),
                colors_any=("U",),
            ),
            lambda: stack_target_schema(
                categories=("spell",),
                colorless="yes",
            ),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    operation()

    def test_targeted_counter_template_is_immutable_and_copy_isolated(self):
        template = TargetedCounterEffectTemplate(
            CounterTarget.CREATURE_SPELL
        )

        self.assertEqual(
            "counter-target-creature-spell-v2", template.template_id
        )
        self.assertEqual(
            ({"op": "counter_stack_target", "stack": "$target.0"},),
            template.effects,
        )
        schema = template.target_schema
        schema["types_any"].append("artifact")
        effects = template.effects
        effects[0]["op"] = "counter_stack"
        self.assertEqual(
            ["creature"], template.target_schema["types_any"]
        )
        self.assertEqual("counter_stack_target", template.effects[0]["op"])
        with self.assertRaisesRegex(ValueError, "target"):
            TargetedCounterEffectTemplate(  # type: ignore[arg-type]
                "creature spell"
            )

    def test_counter_whole_clause_parser_accepts_only_closed_direct_targets(self):
        for target in CounterTarget:
            with self.subTest(target=target):
                template = targeted_counter_effect_template(
                    f"Counter target {target.value}."
                )
                self.assertIsNotNone(template)
                assert template is not None
                self.assertEqual(target, template.target)
                self.assertTrue(template.target_schema["source_exclusion"])
        for text in (
            "Counter up to one target spell.",
            "You may counter target spell.",
            "Counter another target spell.",
            "Counter target spell unless its controller pays {2}.",
            "Counter target spell with mana value 2 or less.",
            "Counter all other spells.",
            "Counter target spell. Exile it instead of putting it into its owner's graveyard.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(targeted_counter_effect_template(text))

    def test_intrinsic_counter_prohibition_requires_the_complete_sentence(self):
        self.assertTrue(
            is_intrinsically_uncounterable_spell(
                "This spell can't be countered."
            )
        )
        self.assertTrue(
            is_intrinsically_uncounterable_spell(
                "This spell cannot be countered."
            )
        )
        for text in (
            "This spell can't be countered by blue spells or abilities.",
            "If {G} was spent to cast this spell, it can't be countered.",
            "Creature spells you control can't be countered.",
            "Target spell can't be countered this turn.",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_intrinsically_uncounterable_spell(text))


class TargetedCounterCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.base = cls.db.lookup("Counterspell")
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

    def test_spell_trigger_and_activated_contexts_share_targeted_counter_lowering(
        self,
    ):
        contexts = (
            (
                "Counter target spell.",
                "Instant",
                "spell_ability",
                "counter-target-spell-v2",
            ),
            (
                "When this creature enters, counter target activated ability.",
                "Creature — Test",
                "triggered_ability",
                "counter-target-activated-ability-v2",
            ),
            (
                "{U}, {T}: Counter target triggered ability.",
                "Creature — Test",
                "activated_ability",
                "counter-target-triggered-ability-v2",
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
                self.assertEqual(
                    {
                        "stack.counter.effect",
                        "target.revalidate_resolution",
                    },
                    set(node.capability_dependencies)
                    - {
                        "trigger.event.normalized_zone_change",
                        "trigger.placement.apnap",
                    },
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_all_closed_counter_target_domains_have_precise_source_spans(self):
        for target in CounterTarget:
            text = f"Counter target {target.value}."
            with self.subTest(target=target):
                ir = self.compile(text)
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertEqual(text, node.text)
                self.assertEqual(text, text[node.span.start : node.span.end])
                self.assertTrue(node.target_schema["source_exclusion"])

    def test_unsupported_counter_variants_remain_material_residuals(self):
        for text in (
            "Counter up to one target spell.",
            "Counter target spell unless its controller pays {2}.",
            "Counter target spell with mana value 2 or less.",
            "Counter all spells.",
            "Counter target spell. If that spell is countered this way, exile it instead of putting it into its owner's graveyard.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_targeted_counter_shape_mutants_fail_closed(self):
        template = TargetedCounterEffectTemplate(CounterTarget.SPELL)
        expected = {
            "stack.counter.effect",
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
            ({"op": "counter_stack_target", "stack": "$target.1"},),
            ({"op": "counter_stack_target", "stack": "$source"},),
            (
                {
                    "op": "counter_stack_target",
                    "stack": "$target.0",
                    "destination": "exile",
                },
            ),
            ({"op": "counter_stack", "stack": "$target.0"},),
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
            {**template.target_schema, "zones": ["battlefield"]},
            {**template.target_schema, "count": 2},
            {**template.target_schema, "source_exclusion": False},
            {**template.target_schema, "controller": "opponent"},
            {**template.target_schema, "types_any": ["dragon"]},
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

    def test_intrinsic_uncounterable_compiles_as_stack_capability(self):
        text = "This spell can't be countered."
        ir = self.compile(text)
        node = ir.faces[0].nodes[0]

        self.assertEqual("exact", ir.status)
        self.assertEqual("static_ability", node.kind)
        self.assertEqual("stack", node.active_zone)
        self.assertEqual("continuous", node.event)
        self.assertEqual(
            "intrinsic-spell-counter-prohibition-v1", node.template_id
        )
        self.assertEqual(
            ("stack.counter.prohibition.intrinsic",),
            node.capability_dependencies,
        )
        self.assertEqual(text, text[node.span.start : node.span.end])

    def test_conditional_counter_prohibitions_remain_residuals(self):
        for text in (
            "This spell can't be countered by blue spells or abilities.",
            "If {G} was spent to cast this spell, it can't be countered.",
            "Target spell can't be countered this turn.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_generated_targeted_counter_program_is_capability_closed(self):
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (self.db.lookup("Counterspell"),),
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
                "stack.counter.effect",
                "target.revalidate_resolution",
            }.issubset(programs[0].capability_dependencies)
        )

    def test_generated_intrinsic_prohibition_is_a_trusted_static_declaration(
        self,
    ):
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (self.db.lookup("Unanswerable Test Spell"),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_capability_declarations=True,
        )
        programs = registry.programs_for_oracle(
            self.db.lookup("Unanswerable Test Spell").oracle_id
        )

        self.assertEqual(1, result["exact_programs_promoted"])
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertEqual("static:front:n1", programs[0].ability_id)
        self.assertEqual("stack", programs[0].active_zone)
        self.assertEqual("continuous", programs[0].event)
        self.assertEqual(
            ["stack.counter.prohibition.intrinsic"],
            programs[0].capability_dependencies,
        )


if __name__ == "__main__":
    unittest.main()
