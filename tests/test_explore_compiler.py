from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from pathlib import Path
import os

from quorune.carddb import CardDatabase
from quorune.compiler.explore_templates import (
    single_explore_effect_template,
)
from quorune.oracle_ir import (
    ORACLE_COMPILER_VERSION,
    compile_oracle_card,
    generated_programs,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(
    os.environ.get(
        "MTG_CARD_DB",
        ROOT / "data" / "scryfall-20260728-compact.sqlite3",
    )
)
REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
from quorune.rules.node_capability_shapes import (
    single_explore_node_capabilities,
)


class ExploreCompilerTests(unittest.TestCase):
    def test_single_source_and_controlled_target_shapes_are_typed(self):
        source = single_explore_effect_template("This creature explores.")
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(
            (
                {
                    "op": "explore",
                    "player": "$source.controller",
                    "card": "$source",
                },
            ),
            source.effects,
        )
        self.assertEqual(
            ("keyword_action.explore.single",),
            single_explore_node_capabilities(
                effects=source.effects,
                target_schema=source.target_schema,
                mechanic_ids=source.mechanics,
            ),
        )

        target = single_explore_effect_template(
            "Target creature you control explores."
        )
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual("you", target.target_schema["controller_relation"])
        self.assertEqual(
            (
                "keyword_action.explore.single",
                "target.revalidate_resolution",
            ),
            single_explore_node_capabilities(
                effects=target.effects,
                target_schema=target.target_schema,
                mechanic_ids=target.mechanics,
            ),
        )

    def test_trigger_pronoun_requires_explicit_source_binding(self):
        self.assertIsNone(single_explore_effect_template("It explores."))
        bound = single_explore_effect_template(
            "It explores.",
            allow_source_pronoun=True,
        )
        self.assertIsNotNone(bound)
        assert bound is not None
        self.assertEqual("$source", bound.effects[0]["card"])

    def test_unsupported_explore_variants_remain_outside_closed_grammar(self):
        for text in (
            "Explore.",
            "This creature explores twice.",
            "Each creature you control explores.",
            "Target creature explores.",
            "Up to two target creatures you control explore.",
            "This creature explores, then it explores again.",
            "If this creature would explore, it explores twice instead.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(single_explore_effect_template(text))

    def test_capability_shape_rejects_malformed_effects(self):
        valid = {
            "op": "explore",
            "player": "$source.controller",
            "card": "$source",
        }
        for mutation in (
            {**valid, "times": 2},
            {**valid, "card": "$target.0"},
            {**valid, "player": "$controller"},
            {**valid, "op": "explore_many"},
        ):
            with self.subTest(mutation=mutation):
                self.assertEqual(
                    (),
                    single_explore_node_capabilities(
                        effects=(mutation,),
                        target_schema=None,
                        mechanic_ids=("explore",),
                    ),
                )

    def test_oracle_nodes_preserve_trigger_and_activation_source_spans(self):
        with CardDatabase(DB_PATH) as database:
            trigger = compile_oracle_card(
                database.lookup("Cenote Scout"),
                trusted_mechanics={
                    "explore",
                    "cr-603-handling-triggered-abilities",
                },
            ).faces[0].nodes[0]
            activation = compile_oracle_card(
                database.lookup("Seeker of Sunlight"),
                trusted_mechanics={"explore"},
            ).faces[0].nodes[0]
        self.assertEqual("explore-source-permanent-once-v1", trigger.template_id)
        self.assertEqual("permanent.enter.self", trigger.event)
        self.assertEqual(1, trigger.span.line)
        self.assertEqual("$source", trigger.effects[0]["card"])
        self.assertEqual("activated_ability", activation.kind)
        self.assertEqual("explore-source-permanent-once-v1", activation.template_id)
        self.assertEqual(1, activation.span.line)

    def test_explore_program_is_capability_closed_and_dependencies_fail_closed(self):
        capabilities = load_default_capability_registry()
        with CardDatabase(DB_PATH) as database:
            record = database.lookup("Cenote Scout")
            program = next(
                value
                for value in generated_programs(
                    database,
                    record,
                    trust_level="trusted",
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                if value.provenance.get("template_id")
                == "explore-source-permanent-once-v1"
            )
            self.assertEqual("trusted", program.trust_level)
            self.assertTrue(program.capability_closure["trusted"])
            self.assertEqual(
                ORACLE_COMPILER_VERSION, program.provenance["authored_by"]
            )
            self.assertIn(
                "keyword_action.explore.single",
                program.capability_dependencies,
            )

            registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            dependency = next(
                row
                for row in registry_value["capabilities"]
                if row["id"] == "counter.placement.quantity_replacement"
            )
            dependency["status"] = "blocked"
            dependency["blockers"] = ["test mutation"]
            blocked = compile_oracle_card(
                record,
                capability_registry=CapabilityRegistry(registry_value),
                capability_profile="commander_review",
            )
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(
            any(
                "counter.placement.quantity_replacement" in blocker
                for residual in blocked.material_residuals
                for blocker in residual.blockers
            )
        )

    def test_explore_compiler_template_mutant_is_killed(self):
        capabilities = load_default_capability_registry()
        with CardDatabase(DB_PATH) as database:
            record = database.lookup("Cenote Scout")

            def assert_exact() -> None:
                result = compile_oracle_card(
                    record,
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", result.status)
                self.assertTrue(
                    any(
                        node.template_id == "explore-source-permanent-once-v1"
                        for face in result.faces
                        for node in face.nodes
                    )
                )

            assert_exact()
            with patch(
                "quorune.compiler.source_self_effect_templates."
                "single_explore_effect_template",
                return_value=None,
            ):
                with self.assertRaises(AssertionError):
                    assert_exact()


if __name__ == "__main__":
    unittest.main()
