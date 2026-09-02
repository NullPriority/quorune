from __future__ import annotations

import unittest

from common import DB_PATH
from quorune.ability_fragments import (
    CombatKeywordTriggerKind,
    CombatKeywordTriggerSpec,
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


def _keyword_card(
    keyword: str,
    oracle_text: str,
    suffix: int,
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=f"Generic {keyword.title()} Fixture",
        mana_cost="{2}{W}",
        mana_value=3.0,
        type_line="Creature — Samurai Knight",
        oracle_text=oracle_text,
        power="2",
        toughness="2",
        loyalty=None,
        defense=None,
        colors=("W",),
        color_identity=("W",),
        keywords=(keyword.title(),),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class BlockKeywordCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_closed_keyword_lowering_preserves_instances_and_values(self):
        lowering = lower_ability_keyword_fragments(
            "Flanking, flanking, Bushido 2",
            ("flanking", "flanking", "bushido"),
        )

        self.assertIsNone(lowering.residual_kind)
        fragments = [
            ability_fragment_from_dict(handler["fragment"])
            for handler in lowering.handlers
        ]
        self.assertEqual(
            [
                CombatKeywordTriggerSpec(
                    CombatKeywordTriggerKind.FLANKING,
                    1,
                ),
                CombatKeywordTriggerSpec(
                    CombatKeywordTriggerKind.FLANKING,
                    1,
                ),
                CombatKeywordTriggerSpec(
                    CombatKeywordTriggerKind.BUSHIDO,
                    2,
                ),
            ],
            fragments,
        )

    def test_flanking_and_bushido_lower_with_precise_closed_spans(self):
        cases = (
            (
                "flanking",
                "Flanking",
                702_025_001,
                "combat.trigger.flanking",
            ),
            (
                "bushido",
                "Bushido 2",
                702_045_001,
                "combat.trigger.bushido",
            ),
        )
        for keyword, text, suffix, capability in cases:
            with self.subTest(keyword=keyword):
                program = compile_card_program(
                    self.db,
                    _keyword_card(keyword, text, suffix),
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
                    {"line": 1, "start": 0, "end": len(text)},
                    ability["source_span"],
                )
                self.assertEqual([], program.to_dict()["residuals"])
                self.assertEqual(
                    1,
                    len(ability["runtime"]["handlers"]),
                )

    def test_unsupported_bushido_values_and_flanking_variants_stay_residual(self):
        cases = (
            ("Bushido X", ("bushido",), "unsupported_bushido_value"),
            ("Bushido 0", ("bushido",), "unsupported_bushido_value"),
            (
                "Flanking against green creatures",
                ("flanking",),
                "unsupported_flanking_variant",
            ),
        )
        for text, mechanics, expected in cases:
            with self.subTest(text=text):
                lowering = lower_ability_keyword_fragments(text, mechanics)
                self.assertEqual(expected, lowering.residual_kind)
                self.assertTrue(lowering.residual_reason)

    def test_conditional_keyword_wording_is_not_promoted_as_printed_keyword(self):
        text = "As long as this creature is attacking, it has flanking."
        ir = compile_oracle_card(
            _keyword_card("bushido", text, 702_045_100),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.faces[0].residuals)


if __name__ == "__main__":
    unittest.main()
