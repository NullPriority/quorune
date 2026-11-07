from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from common import DB_PATH, ROOT
from quorune.ability_fragments import (
    CombatKeywordTriggerKind,
    ability_fragment_from_dict,
)
from quorune.card_programs.adapters import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.ability_keyword_fragments import (
    lower_ability_keyword_fragments,
)
from quorune.compiler import token_templates
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.rules.token_creation_capability_shapes import token_definition_payload_dependencies


def _keyword_card(keyword: str, oracle_text: str, suffix: int) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=f"Generic {keyword} Fixture",
        mana_cost="{2}{W}",
        mana_value=3.0,
        type_line="Creature — Knight",
        oracle_text=oracle_text,
        power="2",
        toughness="2",
        loyalty=None,
        defense=None,
        colors=("W",),
        color_identity=("W",),
        keywords=(keyword,),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class AttackKeywordCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_decayed_fixed_definition_requires_both_abilities(self):
        production = token_templates.fixed_token_creation_effect_template(
            "Create a 2/2 black Zombie creature token with decayed."
        )
        self.assertIsNotNone(production)
        effect = production.effect
        capability = "combat.trigger.decayed"
        self.assertIn(capability, token_definition_payload_dependencies(effect))
        for omitted in range(2):
            incomplete = deepcopy(effect)
            del incomplete["characteristics"]["ability_fragments"][omitted]
            with self.subTest(omitted=omitted), self.assertRaisesRegex(ValueError, "Token Decayed"):
                token_definition_payload_dependencies(incomplete)

        falcon = self.db.lookup("Falcon Abomination")
        self.assertEqual("exact", compile_oracle_card(falcon, capability_registry=self.capabilities).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        row = next(row for row in raw["capabilities"] if row["id"] == capability)
        row.update(status="blocked", blockers=["withdrawn for the dependency test"])
        withdrawn = compile_oracle_card(falcon, capability_registry=CapabilityRegistry(raw))
        self.assertNotEqual("exact", withdrawn.status)
        with patch.object(
            token_templates, "_CREATURE_TOKEN_KEYWORDS",
            token_templates._CREATURE_TOKEN_KEYWORDS - {"decayed"},
        ):
            mutant = compile_oracle_card(falcon, capability_registry=self.capabilities)
        self.assertNotEqual("exact", mutant.status)

    def test_closed_keyword_lowering_preserves_every_printed_instance(self):
        lowering = lower_ability_keyword_fragments(
            "Exalted, exalted, Battle cry, Melee",
            ("exalted", "exalted", "battle cry", "melee"),
        )

        self.assertIsNone(lowering.residual_kind)
        self.assertEqual(
            [
                CombatKeywordTriggerKind.EXALTED,
                CombatKeywordTriggerKind.EXALTED,
                CombatKeywordTriggerKind.BATTLE_CRY,
                CombatKeywordTriggerKind.MELEE,
            ],
            [
                ability_fragment_from_dict(handler["fragment"]).kind
                for handler in lowering.handlers
            ],
        )

    def test_attack_keywords_lower_with_precise_closed_spans(self):
        cases = (
            ("Exalted", "combat.trigger.exalted", 702_083_001),
            ("Battle cry", "combat.trigger.battle_cry", 702_091_001),
            ("Melee", "combat.trigger.melee", 702_121_001),
            ("Decayed", "combat.trigger.decayed", 702_147_001),
        )
        for keyword, capability, suffix in cases:
            with self.subTest(keyword=keyword):
                program = compile_card_program(
                    self.db,
                    _keyword_card(keyword, keyword, suffix),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )

                self.assertEqual(
                    (capability,),
                    program.capability_dependencies,
                )
                self.assertEqual(
                    "capability_closed",
                    program.trust_closure["trust_basis"],
                )
                self.assertTrue(program.trust_closure["trusted"])
                ability = program.to_dict()["abilities"][0]
                self.assertEqual(
                    {"line": 1, "start": 0, "end": len(keyword)},
                    ability["source_span"],
                )
                self.assertEqual([], program.to_dict()["residuals"])
                self.assertEqual(1, len(ability["runtime"]["handlers"]))

    def test_conditional_and_rules_text_variants_remain_residual(self):
        cases = (
            (
                "Exalted as long as you control an artifact",
                ("exalted",),
                "unsupported_exalted_variant",
            ),
            (
                "Battle cry 2",
                ("battle cry",),
                "unsupported_battle_cry_variant",
            ),
            (
                "Melee against the monarch",
                ("melee",),
                "unsupported_melee_variant",
            ),
        )
        for text, mechanics, expected in cases:
            with self.subTest(text=text):
                lowering = lower_ability_keyword_fragments(text, mechanics)
                self.assertEqual(expected, lowering.residual_kind)
                self.assertTrue(lowering.residual_reason)

        for suffix, (keyword, text) in enumerate(
            (
                (
                    "Exalted",
                    "Whenever a creature you control attacks alone, it gets "
                    "+1/+1 until end of turn.",
                ),
                (
                    "Battle Cry",
                    "Whenever this creature attacks, another target attacker "
                    "gets +1/+0 until end of turn.",
                ),
            ),
            start=702_121_100,
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    _keyword_card(keyword, text, suffix),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                if keyword == "Exalted":
                    self.assertEqual("exact", ir.status, ir.material_residuals)
                    self.assertFalse(ir.material_residuals)
                    self.assertEqual("triggered_ability", ir.faces[0].nodes[0].kind)
                else:
                    self.assertNotEqual("exact", ir.status)
                    self.assertTrue(ir.faces[0].residuals)


if __name__ == "__main__":
    unittest.main()
