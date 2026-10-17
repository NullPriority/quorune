from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorune.abilities import parse_activated_abilities
from quorune.card_programs import CardProgram, compile_card_program
from quorune.carddb import CardRecord
from quorune.compiler import activated_mana_nodes
from quorune.fixed_mana_abilities import FixedManaMode
from quorune.intrinsic_basic_land_mana import (
    INTRINSIC_BASIC_LAND_MANA_CAPABILITY,
    IntrinsicBasicLandManaError,
    IntrinsicBasicLandManaSpec,
    expected_intrinsic_basic_land_mana_reminder,
    intrinsic_basic_land_mana_specs,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.activation import query as activation_query
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)


ROOT = Path(__file__).resolve().parents[1]


class _RulingsDatabase:
    @staticmethod
    def rulings(_record):
        return ()


def _record(
    type_line: str,
    oracle_text: str,
    *,
    name: str = "Typed Land",
) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000003056",
        name=name,
        mana_cost="",
        mana_value=0.0,
        type_line=type_line,
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
        released_at="2026-08-14",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class _IntrinsicQueryHost:
    def __init__(self, type_line: str, abilities=()):
        self.type_line = type_line
        self.abilities = abilities
        self.semantics = SimpleNamespace(
            runtime_handler_compatibility_enabled=False
        )

    def _effective_card_data(self, _card):
        from quorune.characteristic_evaluation import type_parts
        from quorune.continuous_effects import CharacteristicState,evaluate_continuous_effects
        kinds,subtypes,supertypes=type_parts(self.type_line)
        effective=evaluate_continuous_effects(CharacteristicState(name='Intrinsic query',
            card_types=kinds,subtypes=subtypes,supertypes=supertypes,
            activated_abilities=list(self.abilities)),()).characteristics
        return {
            "type_line": self.type_line,
            "activated_abilities": effective['activated_abilities'],
        }


class IntrinsicBasicLandManaTests(unittest.TestCase):
    def test_intrinsic_basic_land_mana_compiler_binds_type_line_and_reminder(
        self,
    ):
        capabilities = load_default_capability_registry()
        cases = (
            ("Basic Land — Plains", "({T}: Add {W}.)", ("plains",)),
            (
                "Land — Forest Island",
                "({T}: Add {G} or {U}.)",
                ("island", "forest"),
            ),
            (
                "Land — Mountain Plains Swamp",
                "({T}: Add {R}, {W}, or {B}.)",
                ("plains", "swamp", "mountain"),
            ),
        )
        for type_line, reminder, expected_types in cases:
            with self.subTest(type_line=type_line):
                card = _record(type_line, reminder)
                ir = compile_oracle_card(
                    card,
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual(
                    "intrinsic-basic-land-mana-reminder-v1",
                    node.template_id,
                )
                self.assertEqual((0, len(reminder), 1), (
                    node.span.start,
                    node.span.end,
                    node.span.line,
                ))
                self.assertEqual(
                    (INTRINSIC_BASIC_LAND_MANA_CAPABILITY,),
                    node.capability_dependencies,
                )

                program = compile_card_program(
                    _RulingsDatabase(),
                    card,
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )
                self.assertTrue(program.trust_closure["trusted"])
                self.assertIn(
                    INTRINSIC_BASIC_LAND_MANA_CAPABILITY,
                    program.capability_dependencies,
                )
                declarations = [
                    ability
                    for ability in program.to_dict()["abilities"]
                    if ability["runtime"]["provenance"].get("template_id")
                    == "intrinsic_basic_land_mana"
                ]
                self.assertEqual(
                    set(expected_types),
                    {
                        ability["runtime"]["provenance"]
                        ["card_form_descriptor"]["basic_land_type"]
                        for ability in declarations
                    },
                )
                for ability in declarations:
                    self.assertEqual(
                        {"line": 1, "start": 0, "end": len(type_line)},
                        ability["source_span"],
                    )
                    self.assertEqual(
                        "type_line",
                        ability["runtime"]["provenance"]["source_kind"],
                    )
                    self.assertEqual("front", ability["face_id"])
                self.assertEqual(
                    program.to_dict(),
                    CardProgram.from_dict(program.to_dict()).to_dict(),
                )

    def test_intrinsic_basic_land_mana_rejects_malformed_descriptors_and_near_misses(
        self,
    ):
        spec = IntrinsicBasicLandManaSpec("island", "U")
        self.assertEqual(spec, IntrinsicBasicLandManaSpec.from_dict(spec.to_dict()))
        for mutation in (
            {**spec.to_dict(), "unknown": True},
            {**spec.to_dict(), "schema_version": 2},
            {**spec.to_dict(), "schema_version": True},
            {**spec.to_dict(), "rule_id": "305.7"},
            {**spec.to_dict(), "mana_symbol": "B"},
            {**spec.to_dict(), "basic_land_type": "Island"},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(IntrinsicBasicLandManaError):
                    IntrinsicBasicLandManaSpec.from_dict(mutation)

        capabilities = load_default_capability_registry()
        near_misses = (
            ("Land — Plains Island", "({T}: Add {U} or {W}.)"),
            ("Land — Plains Island", "({T}: Add {W}.)"),
            ("Land — Plains Island", "({T}: Add {W}, {U}.)"),
            ("Creature — Island", "({T}: Add {U}.)"),
            ("Land", "({T}: Add {U}.)"),
        )
        for type_line, reminder in near_misses:
            with self.subTest(type_line=type_line, reminder=reminder):
                ir = compile_oracle_card(
                    _record(type_line, reminder),
                    capability_registry=capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertIn(
                    "does not exactly match",
                    ir.material_residuals[0].reason,
                )
        partial = compile_card_program(
            _RulingsDatabase(),
            _record("Land — Plains Island", "({T}: Add {U} or {W}.)"),
            capability_registry=capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertFalse(partial.trust_closure["trusted"])
        self.assertTrue(partial.residuals)
        with self.assertRaisesRegex(
            ValueError,
            "cannot be promoted to trusted generated semantics",
        ):
            compile_card_program(
                _RulingsDatabase(),
                _record("Creature — Island", "({T}: Add {U}.)"),
                capability_registry=capabilities,
                capability_profile="commander_review",
                trust_level="trusted",
            )

        registry_value = json.loads(
            (ROOT / "quorune" / "rules" / "capability-registry.json")
            .read_text(encoding="utf-8")
        )
        capability = next(
            value
            for value in registry_value["capabilities"]
            if value["id"] == INTRINSIC_BASIC_LAND_MANA_CAPABILITY
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["test mutation"]
        blocked = CapabilityRegistry(copy.deepcopy(registry_value))
        blocked.mark_evidence_verified("0" * 64)
        with self.assertRaisesRegex(
            ValueError, "intrinsic basic-land mana capability is blocked"
        ):
            compile_card_program(
                _RulingsDatabase(),
                _record("Land — Island", "({T}: Add {U}.)"),
                capability_registry=blocked,
                capability_profile="commander_review",
                trust_level="trusted",
            )

    def test_intrinsic_basic_land_mana_compiler_and_runtime_mutants_are_killed(
        self,
    ):
        card = _record("Land — Forest Island", "({T}: Add {G} or {U}.)")
        capabilities = load_default_capability_registry()

        def assert_compiler_binding() -> None:
            ir = compile_oracle_card(
                card,
                capability_registry=capabilities,
                capability_profile="commander_review",
            )
            self.assertEqual("exact", ir.status)
            self.assertEqual(
                "intrinsic-basic-land-mana-reminder-v1",
                ir.faces[0].nodes[0].template_id,
            )

        with patch.object(
            activated_mana_nodes,
            "expected_intrinsic_basic_land_mana_reminder",
            return_value="({T}: Add {G}.)",
        ):
            with self.assertRaises(AssertionError):
                assert_compiler_binding()

        host = _IntrinsicQueryHost(card.type_line)

        def assert_runtime_binding() -> None:
            self.assertEqual(
                {"intrinsic_island", "intrinsic_forest"},
                {
                    ability.ability_id
                    for ability in activation_query.activated_abilities(
                        host, SimpleNamespace()
                    )
                },
            )

        assert_runtime_binding()
        import quorune.continuous_effects as continuous_owner
        original=continuous_owner.intrinsic_land_mana_abilities
        with patch.object(
            continuous_owner,
            "intrinsic_land_mana_abilities",
            side_effect=lambda *args,**kwargs: original(*args,**kwargs)[:-1],
        ):
            with self.assertRaises(AssertionError):
                assert_runtime_binding()

    def test_reminder_derivation_is_type_line_ordered_and_land_gated(self):
        self.assertEqual(
            "({T}: Add {R}, {W}, or {B}.)",
            expected_intrinsic_basic_land_mana_reminder(
                "Land — Mountain Plains Swamp"
            ),
        )
        self.assertIsNone(
            expected_intrinsic_basic_land_mana_reminder(
                "Creature — Mountain Plains Swamp"
            )
        )
        printed = replace(
            parse_activated_abilities(
                card_name="Printed Forest Land",
                oracle_text="{T}: Add {G}.",
            )[0],
            fixed_mana_outputs=(FixedManaMode.from_bundle({"G": 1}),),
        )
        self.assertEqual(
            {"ab1", "intrinsic_forest"},
            {
                ability.ability_id
                for ability in activation_query.activated_abilities(
                    _IntrinsicQueryHost("Land — Forest", (printed,)),
                    SimpleNamespace(),
                )
            },
        )
        with self.assertRaisesRegex(ValueError, "reserved for CR 305.6"):
            activation_query.activated_abilities(
                _IntrinsicQueryHost(
                    "Land — Forest",
                    (replace(printed, ability_id="intrinsic_forest"),),
                ),
                SimpleNamespace(),
            )


if __name__ == "__main__":
    unittest.main()
