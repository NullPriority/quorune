from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest

from common import load_assets
from quorune.compiler.continuous_templates import (
    controlled_creature_fixed_modifier,
    fixed_power_toughness_anthem_handler,
)
from quorune.compiler.program_generation import (
    register_generated_programs,
    runtime_handler_footprint,
)
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.semantics import SemanticProgram, SemanticRegistry


def _anthem_program(
    text: str,
    *,
    key: str,
    oracle_id: str = "fixture-anthem-identity",
    trust_level: str = "provisional",
) -> SemanticProgram:
    compiled = fixed_power_toughness_anthem_handler(text)
    if compiled is None:
        raise AssertionError(f"fixture did not compile: {text}")
    _, descriptor, _ = compiled
    return SemanticProgram(
        key=key,
        label=key,
        oracle_id=oracle_id,
        ability_id=f"static:{key}",
        active_zone="battlefield",
        event="characteristics.evaluate",
        trust_level=trust_level,
        handlers=[dict(descriptor)],
        provenance=(
            {
                "source_oracle_hash": "fixture-oracle",
                "source_rulings_hash": "fixture-rulings",
                "authored_by": "fixture-review",
                "review_status": "reviewed",
            }
            if trust_level == "trusted"
            else {}
        ),
        tests=["fixture"] if trust_level == "trusted" else [],
    )


def _anthem_program_with_query(
    query_updates: dict[str, object], *, key: str
) -> SemanticProgram:
    compiled = fixed_power_toughness_anthem_handler(
        "Creatures you control get +1/+1."
    )
    if compiled is None:
        raise AssertionError("base anthem fixture did not compile")
    _, raw_descriptor, _ = compiled
    descriptor = {
        **raw_descriptor,
        "condition": {
            **raw_descriptor["condition"],
            "predicate": {
                **raw_descriptor["condition"]["predicate"],
                **query_updates,
            },
        },
    }
    return SemanticProgram(
        key=key,
        label=key,
        active_zone="battlefield",
        event="characteristics.evaluate",
        handlers=[descriptor],
    )


class ContinuousHandlerIdentityTests(unittest.TestCase):
    def test_distinct_anthem_queries_have_distinct_footprints(self):
        programs = tuple(
            _anthem_program(text, key=f"fixture:{index}")
            for index, text in enumerate(
                (
                    "Creatures you control get +1/+1.",
                    "White creatures you control get +1/+1.",
                    "Legendary creatures you control get +1/+1.",
                    "Artifact creatures you control get +1/+1.",
                    "Dragon creatures you control get +1/+1.",
                )
            )
        )
        footprints = tuple(runtime_handler_footprint(value) for value in programs)
        self.assertEqual(len(programs), len(set(footprints)))

    def test_complete_query_is_normalized_without_losing_predicates(self):
        land_or_artifact = _anthem_program_with_query(
            {"types_any": ["Land", "ARTIFACT"]},
            key="fixture:types-any-one",
        )
        same_query_different_order = _anthem_program_with_query(
            {"types_any": ["artifact", "land"]},
            key="fixture:types-any-two",
        )
        creature_only = _anthem_program_with_query(
            {"types_any": ["creature"]},
            key="fixture:types-any-three",
        )
        tapped = _anthem_program_with_query(
            {"tapped": True}, key="fixture:tapped"
        )
        untapped = _anthem_program_with_query(
            {"tapped": False}, key="fixture:untapped"
        )
        self.assertEqual(
            runtime_handler_footprint(land_or_artifact),
            runtime_handler_footprint(same_query_different_order),
        )
        self.assertNotEqual(
            runtime_handler_footprint(land_or_artifact),
            runtime_handler_footprint(creature_only),
        )
        self.assertNotEqual(
            runtime_handler_footprint(tapped),
            runtime_handler_footprint(untapped),
        )

    def test_generic_handler_footprint_preserves_every_query_relation(self):
        def footprint(**updates: object):
            query = {
                **_anthem_program_with_query(
                    {}, key="fixture:query-source"
                ).handlers[0]["condition"]["predicate"],
                **updates,
            }
            program = SimpleNamespace(
                active_zone="battlefield",
                event="fixture.evaluate",
                handlers=[
                    {
                        "handler_id": "fixture.object-query.v1",
                        "schema_version": 1,
                        "event": "fixture.evaluate",
                        "condition": {"predicate": query},
                        "modifier": {
                            "power": 1,
                            "toughness": 2,
                            "abilities": ["Flying"],
                        },
                    }
                ],
            )
            return runtime_handler_footprint(program)

        baseline = footprint()
        variants = (
            footprint(colors_all=["U"]),
            footprint(colors_any=["R"]),
            footprint(supertypes_all=["legendary"]),
            footprint(types_any=["artifact"]),
            footprint(subtypes_all=["dragon"]),
            footprint(keywords_all=["flying"]),
            footprint(token=True),
            footprint(tapped=True),
            footprint(include_phased_out=True),
            footprint(owner="A"),
            footprint(controller="B"),
            footprint(exclude_ref="C01"),
        )
        self.assertTrue(all(value != baseline for value in variants))
        self.assertEqual(len(variants), len(set(variants)))
        self.assertEqual(
            footprint(colors_any=["u", "R"]),
            footprint(colors_any=["r", "U"]),
        )

    def test_legacy_and_typed_equivalent_anthem_share_one_footprint(self):
        legacy = SemanticProgram(
            key="fixture:legacy",
            label="legacy",
            active_zone="battlefield",
            event="characteristics.evaluate",
            handlers=[
                {
                    "handler_id": "continuous.anthem.power_toughness.v1",
                    "schema_version": 1,
                    "event": "characteristics.evaluate",
                    "condition": {
                        "target_controller": "source_controller",
                        "target_subtypes_all": ["thopter"],
                    },
                    "modifier": {"power": 1, "toughness": 1},
                }
            ],
        )
        typed = _anthem_program(
            "Thopter creatures you control get +1/+1.",
            key="fixture:typed",
        )
        self.assertEqual(
            runtime_handler_footprint(legacy),
            runtime_handler_footprint(typed),
        )

    def test_reviewed_unqualified_anthem_does_not_hide_color_qualified_program(self):
        db, _, _ = load_assets()
        try:
            # Abrade is part of the repository's compact exact-list fixture.
            # Do not make compiler unit tests depend on a developer's full
            # local Scryfall database.
            base = db.lookup("Abrade")
            record = replace(
                base,
                oracle_id="fixture-distinct-reviewed-anthem",
                name="Fixture Red Anthem",
                type_line="Enchantment",
                oracle_text="Red creatures you control get +1/+1.",
            )
            registry = SemanticRegistry(include_builtin_packs=False)
            registry.put(
                _anthem_program(
                    "Creatures you control get +1/+1.",
                    key="fixture:reviewed-unqualified",
                    oracle_id=record.oracle_id,
                    trust_level="trusted",
                )
            )
            result = register_generated_programs(
                db,
                registry,
                (record,),
                capability_registry=load_default_capability_registry(),
                capability_profile="commander_review",
            )
            self.assertEqual(1, result["programs_generated"])
            self.assertEqual(0, result["programs_skipped_existing"])
            self.assertEqual(
                2,
                len(registry.programs_for_oracle(record.oracle_id)),
            )
        finally:
            db.close()


class ClosedContinuousGrammarTests(unittest.TestCase):
    def test_pinned_creature_subtypes_and_explicit_qualities_compile(self):
        for text in (
            "Dragon creatures you control get +1/+1.",
            "Time Lord creatures you control get +1/+1.",
            "Elves you control get +1/+1.",
            "White creatures you control get +1/+1.",
            "Legendary creatures you control get +1/+1.",
            "Artifact creatures you control get +1/+1.",
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(
                    controlled_creature_fixed_modifier(
                        text, until_end_of_turn=False
                    )
                )

    def test_unsupported_qualities_remain_residual(self):
        unsupported = (
            "Modified creatures you control get +1/+1.",
            "Token creatures you control get +1/+1.",
            "Nontoken creatures you control get +1/+1.",
            "Snow creatures you control get +1/+1.",
            "Commander creatures you control get +1/+1.",
            "Attacking creatures you control get +1/+1.",
            "Blocking creatures you control get +1/+1.",
            "Equipped creatures you control get +1/+1.",
            "Enchanted creatures you control get +1/+1.",
            "Tapped creatures you control get +1/+1.",
            "Untapped creatures you control get +1/+1.",
            "Nonwhite creatures you control get +1/+1.",
            "Artifact token creatures you control get +1/+1.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(
                    controlled_creature_fixed_modifier(
                        text, until_end_of_turn=False
                    )
                )
                self.assertIsNone(fixed_power_toughness_anthem_handler(text))

    def test_unsupported_qualities_preserve_the_exact_oracle_residual(self):
        db, _, _ = load_assets()
        try:
            base = db.lookup("Abrade")
            capabilities = load_default_capability_registry()
            for index, text in enumerate(
                (
                    "Modified creatures you control get +1/+1.",
                    "Token creatures you control get +1/+1.",
                    "Snow creatures you control get +1/+1.",
                    "Commander creatures you control get +1/+1.",
                    "Attacking creatures you control get +1/+1.",
                    "Nonwhite creatures you control get +1/+1.",
                    "Artifact token creatures you control get +1/+1.",
                )
            ):
                with self.subTest(text=text):
                    record = replace(
                        base,
                        oracle_id=f"fixture-residual-anthem-{index}",
                        name=f"Fixture Residual Anthem {index}",
                        type_line="Enchantment",
                        oracle_text=text,
                    )
                    ir = compile_oracle_card(
                        record,
                        capability_registry=capabilities,
                        capability_profile="commander_review",
                    )
                    self.assertNotEqual("exact", ir.status)
                    self.assertTrue(
                        any(
                            residual.text == text and residual.material
                            for residual in ir.faces[0].residuals
                        )
                    )
        finally:
            db.close()

    def test_previously_false_anthem_promotions_are_demoted(self):
        db, _, _ = load_assets()
        try:
            base = db.lookup("Abrade")
            capabilities = load_default_capability_registry()
            cases = {
                "Battle Frenzy": (
                    "Nongreen creatures you control get +1/+0 until end of turn."
                ),
                "Broodwarden": (
                    "Eldrazi Spawn creatures you control get +2/+1."
                ),
                "Flowering of the White Tree": (
                    "Nonlegendary creatures you control get +1/+1."
                ),
                "Blossoming Tortoise": (
                    "Land creatures you control get +1/+1."
                ),
                "Forsaken Monument": (
                    "Colorless creatures you control get +2/+2."
                ),
                "Secret Plans": (
                    "Face-down creatures you control get +0/+1."
                ),
                "Guardian Augmenter": (
                    "Commander creatures you control get +2/+2."
                ),
            }
            for index, (name, unsupported_line) in enumerate(cases.items()):
                with self.subTest(name=name):
                    record = replace(
                        base,
                        oracle_id=f"fixture-false-anthem-{index}",
                        name=name,
                        type_line="Enchantment",
                        oracle_text=unsupported_line,
                    )
                    ir = compile_oracle_card(
                        record,
                        capability_registry=capabilities,
                        capability_profile="commander_review",
                    )
                    self.assertFalse(
                        any(
                            node.text == unsupported_line and node.exact
                            for face in ir.faces
                            for node in face.nodes
                        )
                    )
                    self.assertTrue(
                        any(
                            residual.text == unsupported_line
                            and residual.material
                            for face in ir.faces
                            for residual in face.residuals
                        )
                    )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
