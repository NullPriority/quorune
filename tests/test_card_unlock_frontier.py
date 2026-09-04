from __future__ import annotations

from copy import deepcopy
import gzip
import json
from math import comb
from pathlib import Path
import unittest

from common import DB_PATH
from quorune.carddb import CardDatabase
from quorune.compiler.unlock_frontier import (
    BASE_RESIDUAL_FAMILIES,
    MAX_BUNDLE_FAMILIES,
    build_card_unlock_frontier,
    canonical_card_data_snapshot,
    canonical_residual_families,
    render_card_unlock_frontier_markdown,
    validate_card_unlock_frontier,
)
from quorune.mechanic_contracts import load_mechanic_contracts
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.semantics import SemanticRegistry
from scripts.update_card_unlock_frontier import _canonical_gzip


ROOT = Path(__file__).resolve().parents[1]


def _contracts() -> list[dict]:
    manifest = json.loads(
        (ROOT / "rules" / "manifest.json").read_text(encoding="utf-8")
    )
    rules = json.loads(
        (ROOT / "rules" / "rule-index.json").read_text(encoding="utf-8")
    )
    return load_mechanic_contracts(
        ROOT,
        expected_effective_date=manifest["effective_date"],
        expected_source_sha256=manifest["source_sha256"],
        known_rule_ids={row["rule_id"] for row in rules["rules"]},
    )


class ResidualClassifierTests(unittest.TestCase):
    def test_fixed_mana_flashback_cost_literals_share_one_grammar_family(self):
        families = {
            canonical_residual_families(
                {
                    "kind": "spell_effect",
                    "reason": "spell effect has no exact generic template",
                    "blockers": [],
                    "text": (
                        f"Flashback {cost} (You may cast this card from your "
                        "graveyard for its flashback cost. Then exile it.)"
                    ),
                }
            )
            for cost in ("{U}", "{2}{R}", "{6}{G}{G}", "{R}{W}")
        }
        families.add(
            canonical_residual_families(
                {
                    "kind": "spell_effect",
                    "reason": "spell effect has no exact generic template",
                    "blockers": [],
                    "text": (
                        "Flashback—{1}{U}, Pay 3 life. (You may cast this card "
                        "from your graveyard for its flashback cost. Then exile it.)"
                    ),
                }
            )
        )
        self.assertEqual(
            {("effect_clause:unparsed-flashback-fixed-cost",)},
            families,
        )

    def test_residual_classifier_uses_dependency_sized_canonical_families(self):
        keyword = canonical_residual_families(
            {
                "kind": "dependency_contract",
                "reason": "recognized keyword lacks a trusted contract",
                "blockers": ["mechanic:flying"],
            }
        )
        capability = canonical_residual_families(
            {
                "kind": "dependency_contract",
                "reason": "capability closure failed",
                "blockers": [
                    "capability:status:damage.result.toxic:implemented"
                ],
            }
        )
        trigger = canonical_residual_families(
            {
                "kind": "trigger",
                "reason": "trigger condition/event binding is not exact",
                "blockers": ["normalized event binding"],
            }
        )

        self.assertEqual(("keyword_dependency:flying",), keyword)
        self.assertEqual(
            ("capability_dependency:damage.result.toxic",), capability
        )
        self.assertEqual(
            ("event_binding:normalized-event-binding",), trigger
        )

        compound = canonical_residual_families(
            {
                "kind": "spell_effect",
                "reason": "spell effect has no exact generic template",
                "blockers": [],
                "text": (
                    "Destroy target creature with power 4 or greater. "
                    "Its controller creates a 3/3 green Beast creature token."
                ),
            }
        )
        self.assertEqual(
            (
                "effect_clause:create-token",
                "effect_clause:destroy-target",
                "effect_clause:ordered-effect-composition",
                "reference_binding:linked-result-reference",
                "target_or_choice:target-predicate",
            ),
            compound,
        )

    def test_residual_classifier_separates_damage_grammar_families(self):
        examples = (
            (
                (
                    "Creatures you control gain first strike until end of turn. "
                    "(They deal combat damage before creatures without first "
                    "strike.)"
                ),
                (
                    "duration:until-end-of-turn",
                    "effect_clause:unparsed-creatures-you-control",
                ),
            ),
            (
                "Prevent all damage that creatures would deal this turn.",
                ("replacement:damage-prevention",),
            ),
            (
                "Fixture deals 3 damage to any target and you gain 3 life.",
                (
                    "effect_clause:deal-damage",
                    "effect_clause:life-change",
                    "effect_clause:ordered-effect-composition",
                ),
            ),
            (
                "Fixture deals 2 damage to any target. Scry 2.",
                (
                    "effect_clause:deal-damage",
                    "effect_clause:ordered-effect-composition",
                    "effect_clause:scry",
                ),
            ),
            (
                (
                    "Fixture deals 3 damage divided as you choose among one, "
                    "two, or three targets."
                ),
                (
                    "effect_clause:deal-damage",
                    "target_or_choice:divided-damage-allocation",
                    "target_or_choice:multiple-targets",
                ),
            ),
            (
                "Fixture deals 2 damage to each creature without flying.",
                (
                    "effect_clause:deal-damage",
                    "target_or_choice:target-predicate",
                ),
            ),
            (
                (
                    "Fixture deals 6 damage to target creature and 2 damage "
                    "to up to one other target creature token."
                ),
                (
                    "effect_clause:deal-damage",
                    "effect_clause:ordered-effect-composition",
                    "target_or_choice:multiple-damage-recipients",
                    "target_or_choice:multiple-targets",
                    "target_or_choice:target-predicate",
                ),
            ),
            (
                "Fixture deals 4 damage to target white or blue creature.",
                (
                    "effect_clause:deal-damage",
                    "target_or_choice:target-predicate",
                ),
            ),
            (
                "Fixture deals 4 damage to any target and 2 damage to you.",
                (
                    "effect_clause:deal-damage",
                    "effect_clause:ordered-effect-composition",
                    "target_or_choice:multiple-damage-recipients",
                ),
            ),
            (
                (
                    "Until end of turn, creatures you control gain "
                    '"{T}: This creature deals 1 damage to any target."'
                ),
                (
                    "continuous_layer:granted-ability",
                    "duration:until-end-of-turn",
                ),
            ),
        )
        for text, expected in examples:
            with self.subTest(text=text):
                self.assertEqual(
                    expected,
                    canonical_residual_families(
                        {
                            "kind": "spell_effect",
                            "reason": (
                                "spell effect has no exact generic template"
                            ),
                            "blockers": [],
                            "text": text,
                        }
                    ),
                )


class CardUnlockFrontierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.capabilities = load_default_capability_registry()
        cls.report = build_card_unlock_frontier(
            cls.db,
            registry=SemanticRegistry(),
            capabilities=cls.capabilities,
            mechanic_contracts=_contracts(),
            limit=20,
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_limited_frontier_accounts_for_every_card_and_material_ability(self):
        report = self.report
        validate_card_unlock_frontier(report)

        self.assertEqual(20, report["cards_considered"])
        self.assertEqual(20, len(report["cards"]))
        self.assertEqual(
            sorted(BASE_RESIDUAL_FAMILIES),
            report["base_residual_families"],
        )
        self.assertFalse(report["complete_snapshot_claimed"])
        self.assertEqual(
            {
                "blockers": {},
                "lowerable": False,
                "residuals": [],
                "template_id": None,
            },
            report["ability_field_defaults"],
        )
        for card in report["cards"]:
            self.assertEqual(
                sorted(card["minimum_known_blocker_set"]),
                card["minimum_known_blocker_set"],
            )
            for ability in card["abilities"]:
                self.assertIn(
                    ability["status"],
                    {"exact", "lowerable_untrusted", "unresolved"},
                )
                blockers = ability.get("blockers", {})
                self.assertEqual(
                    blockers.get("canonical_family_ids", []),
                    sorted(blockers.get("canonical_family_ids", [])),
                )
                self.assertNotIn("exact", ability)
                self.assertNotIn("source_text_sha256", ability)
                self.assertNotEqual(False, ability.get("lowerable"))
                if "template_id" in ability:
                    self.assertIsNotNone(ability["template_id"])

    def test_frontier_snapshot_excludes_environment_specific_database_provenance(self):
        metadata = {
            **self.db.metadata(),
            "bulk_manifest_url": "https://example.invalid/bulk-data",
            "oracle_source": r"C:\\local\\oracle.jsonl.gz",
            "rulings_source": "/cloud/rulings.jsonl.gz",
        }

        snapshot = canonical_card_data_snapshot(metadata)

        self.assertEqual(self.report["card_data_snapshot"], snapshot)
        self.assertNotIn("bulk_manifest_url", snapshot)
        self.assertNotIn("oracle_source", snapshot)
        self.assertNotIn("rulings_source", snapshot)

    def test_frontier_fingerprint_and_markdown_fail_closed(self):
        markdown = render_card_unlock_frontier_markdown(self.report)
        self.assertIn("not a claim of complete", markdown)
        self.assertIn("Highest-leverage bounded bundles", markdown)

        tampered = deepcopy(self.report)
        tampered["cards"][0]["card_name"] = "Tampered"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            validate_card_unlock_frontier(tampered)

        malformed = deepcopy(self.report)
        malformed["cards"][0]["abilities"][0]["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "ability fields"):
            validate_card_unlock_frontier(malformed)

        malformed = deepcopy(self.report)
        malformed["cards"][0]["minimum_known_blocker_set"] = [
            "effect_clause:not-a-real-observed-blocker"
        ]
        with self.assertRaisesRegex(ValueError, "minimum blocker"):
            validate_card_unlock_frontier(malformed)

    def test_bundle_evaluation_is_bounded_and_optimizes_full_cards(self):
        evaluation = self.report["bundle_evaluation"]
        self.assertEqual(3, evaluation["maximum_size"])
        family_count = min(
            len(self.report["family_candidates"]),
            MAX_BUNDLE_FAMILIES,
        )
        expected_evaluations = sum(
            comb(family_count, size)
            for size in range(1, min(3, family_count) + 1)
        )
        self.assertEqual(
            expected_evaluations,
            evaluation["evaluated_bundle_count"],
        )
        expected_order = sorted(
            evaluation["top_bundles"],
            key=lambda row: (
                -row["expected_exact_card_gain"],
                -row["expected_exact_ability_gain"],
                row["size"],
                row["family_ids"],
            ),
        )
        self.assertEqual(expected_order, evaluation["top_bundles"])

    def test_tracked_gzip_is_deterministic_and_round_trips(self):
        payload = b'{"frontier":"complete machine-readable rows"}\n'
        encoded = _canonical_gzip(payload)

        self.assertEqual(encoded, _canonical_gzip(payload))
        self.assertEqual(b"\x00\x00\x00\x00", encoded[4:8])
        self.assertEqual(payload, gzip.decompress(encoded))


if __name__ == "__main__":
    unittest.main()
