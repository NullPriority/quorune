from __future__ import annotations

import unittest

from common import DB_PATH
from quorune.ability_fragments import (
    CombatKeywordTriggerKind,
    ability_fragment_from_dict,
)
from quorune.card_programs.adapters import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.ability_keyword_fragments import (
    lower_ability_keyword_fragments,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import (
    load_default_capability_registry,
)


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
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.faces[0].residuals)


if __name__ == "__main__":
    unittest.main()
