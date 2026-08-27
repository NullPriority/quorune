from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.return_to_hand_templates import (
    GraveyardCardTargetKind,
    TargetedOwnGraveyardReturnToHandEffectTemplate,
    targeted_own_graveyard_return_to_hand_effect_template,
)
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.rules.graveyard_card_targets import (
    GraveyardCardTargetError,
    OwnGraveyardCardTargetSpec,
)
from quorune.semantics import SemanticRegistry
from quorune.targets import TargetGroup
from scripts.build_test_database import build_fixture_database


def card_record(
    oracle_text: str,
    *,
    type_line: str = "Instant",
    name: str = "Graveyard Return Fixture",
) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000000231",
        name=name,
        mana_cost="{1}{G}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power="1" if "Creature" in type_line else None,
        toughness="1" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("G",),
        color_identity=("G",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class OwnGraveyardCardTargetTests(unittest.TestCase):
    def test_target_spec_is_immutable_canonical_and_copy_isolated(self):
        for kind in GraveyardCardTargetKind:
            with self.subTest(kind=kind):
                spec = OwnGraveyardCardTargetSpec(kind)
                schema = spec.to_target_schema()
                self.assertEqual(spec, OwnGraveyardCardTargetSpec.from_target_schema(schema))
                self.assertEqual(["graveyard"], schema["zones"])
                self.assertEqual(["card"], schema["categories"])
                self.assertEqual("you", schema["owner_relation"])
                schema["zones"].append("exile")
                self.assertEqual(["graveyard"], spec.to_target_schema()["zones"])
        with self.assertRaises(FrozenInstanceError):
            spec.kind = GraveyardCardTargetKind.CARD  # type: ignore[misc]
        with self.assertRaisesRegex(GraveyardCardTargetError, "typed value"):
            OwnGraveyardCardTargetSpec("card")  # type: ignore[arg-type]

    def test_target_spec_rejects_noncanonical_or_open_predicates(self):
        base = OwnGraveyardCardTargetSpec(
            GraveyardCardTargetKind.CREATURE_CARD
        ).to_target_schema()
        malformed = (
            {**base, "zones": ["exile"]},
            {**base, "categories": ["permanent"]},
            {**base, "owner_relation": "any"},
            {**base, "count": True},
            {**base, "types_any": ["creature", "goblin"]},
            {**base, "types_any": ["Creature"]},
            {**base, "subtypes_any": ["goblin"]},
        )
        for schema in malformed:
            with self.subTest(schema=schema):
                with self.assertRaises(GraveyardCardTargetError):
                    OwnGraveyardCardTargetSpec.from_target_schema(schema)

    def test_type_predicates_use_the_shared_target_matcher(self):
        permanent = TargetGroup.from_mapping(
            OwnGraveyardCardTargetSpec(
                GraveyardCardTargetKind.PERMANENT_CARD
            ).to_target_schema()
        )
        nonland = TargetGroup.from_mapping(
            OwnGraveyardCardTargetSpec(
                GraveyardCardTargetKind.NONLAND_PERMANENT_CARD
            ).to_target_schema()
        )
        self.assertTrue(
            permanent.matches_type_characteristics(
                types=("artifact", "land"), subtypes=(), supertypes=()
            )
        )
        self.assertFalse(
            nonland.matches_type_characteristics(
                types=("artifact", "land"), subtypes=(), supertypes=()
            )
        )
        self.assertTrue(
            nonland.matches_type_characteristics(
                types=("battle",), subtypes=(), supertypes=()
            )
        )


class TargetedOwnGraveyardReturnCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()

    def compile(self, oracle_text: str, *, type_line: str = "Instant"):
        return compile_oracle_card(
            card_record(oracle_text, type_line=type_line),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_parser_accepts_only_the_closed_own_graveyard_family(self):
        for kind in GraveyardCardTargetKind:
            with self.subTest(kind=kind):
                template = targeted_own_graveyard_return_to_hand_effect_template(
                    f"Return target {kind.value} from your graveyard to your hand."
                )
                self.assertIsNotNone(template)
                assert template is not None
                self.assertEqual(kind, template.target)
        for text in (
            "Return up to one target card from your graveyard to your hand.",
            "You may return target card from your graveyard to your hand.",
            "Return target Goblin card from your graveyard to your hand.",
            "Return target green card from your graveyard to your hand.",
            "Return target card from a graveyard to its owner's hand.",
            "Return target card from an opponent's graveyard to your hand.",
            "Return target creature card from your graveyard to the battlefield.",
            "Return two target cards from your graveyard to your hand.",
            "Return target card from your graveyard to your hand, then draw a card.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    targeted_own_graveyard_return_to_hand_effect_template(text)
                )

    def test_spell_trigger_and_activated_contexts_share_graveyard_return_lowering(self):
        contexts = (
            (
                "Return target card from your graveyard to your hand.",
                "Sorcery",
                "spell_ability",
                "return-target-card-from-own-graveyard-v1",
            ),
            (
                "When this creature enters, return target creature card from your graveyard to your hand.",
                "Creature — Test",
                "triggered_ability",
                "return-target-creature-card-from-own-graveyard-v1",
            ),
            (
                "{1}{W}{U}, {T}: Return target artifact or enchantment card from your graveyard to your hand.",
                "Creature — Test",
                "activated_ability",
                "return-target-artifact-or-enchantment-card-from-own-graveyard-v1",
            ),
        )
        for text, type_line, kind, template_id in contexts:
            with self.subTest(kind=kind):
                ir = self.compile(text, type_line=type_line)
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(template_id, node.template_id)
                self.assertEqual(
                    {
                        "card.return.own_graveyard_to_owner_hand",
                        "target.revalidate_resolution",
                    },
                    set(node.capability_dependencies)
                    - {
                        "trigger.event.normalized_zone_change",
                        "trigger.placement.apnap",
                    },
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_unsupported_graveyard_return_variants_remain_material_residuals(self):
        for text in (
            "Return target Goblin card from your graveyard to your hand.",
            "Return target card from a graveyard to its owner's hand.",
            "Return target card from an opponent's graveyard to your hand.",
            "Return target creature card from your graveyard to the battlefield.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_graveyard_return_shape_mutants_fail_closed(self):
        template = TargetedOwnGraveyardReturnToHandEffectTemplate(
            GraveyardCardTargetKind.CREATURE_CARD
        )
        expected = {
            "card.return.own_graveyard_to_owner_hand",
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
            ({"op": "return_graveyard_card_to_owner_hand", "card": "$target.1"},),
            ({"op": "return_graveyard_card_to_owner_hand", "card": "$source"},),
            ({"op": "bounce", "card": "$target.0"},),
            (
                {
                    "op": "return_graveyard_card_to_owner_hand",
                    "card": "$target.0",
                    "destination": "hand",
                },
            ),
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
            {**template.target_schema, "zones": ["graveyard", "exile"]},
            {**template.target_schema, "owner_relation": "any"},
            {**template.target_schema, "count": 2},
            {**template.target_schema, "types_any": ["goblin"]},
            {**template.target_schema, "controller_relation": "you"},
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

    def test_compiler_mutant_is_killed(self):
        text = "Return target card from your graveyard to your hand."
        self.assertEqual("exact", self.compile(text).status)
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "targeted_own_graveyard_return_to_hand_effect_template",
            return_value=None,
        ):
            mutated = self.compile(text)
        self.assertNotEqual("exact", mutated.status)
        self.assertTrue(mutated.material_residuals)

    def test_generated_regrowth_program_is_capability_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "graveyard-return.sqlite3"
            build_fixture_database(
                [
                    ROOT
                    / "tests"
                    / "fixtures"
                    / "targeted-graveyard-return-cards.json"
                ],
                database,
            )
            db = CardDatabase(database)
            try:
                registry = SemanticRegistry(include_builtin_packs=False)
                result = register_generated_programs(
                    db,
                    registry,
                    (db.lookup("Regrowth"),),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    promote_exact_effect_programs=True,
                )
            finally:
                db.close()
        programs = [program for program in registry.programs() if program.effects]
        self.assertEqual(1, result["exact_effect_programs_promoted"])
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertTrue(
            {
                "card.return.own_graveyard_to_owner_hand",
                "target.revalidate_resolution",
            }.issubset(programs[0].capability_dependencies)
        )


if __name__ == "__main__":
    unittest.main()
