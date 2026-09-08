from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from quorune.reusable_pieces import (
    build_reusable_piece_artifacts,
    card_piece_relations,
    execute_reusable_piece_operation,
    load_reusable_piece_policy,
    load_tracked_reusable_piece_artifacts,
    render_reusable_piece_delta_markdown,
    validate_reusable_piece_artifacts,
    validate_interaction_evidence,
    validate_reusable_piece_matrix,
    validate_reusable_piece_policy,
)
from quorune.reusable_pieces.architecture_metrics import (
    project_architecture_metrics,
)


ROOT = Path(__file__).resolve().parents[1]


def _frontier() -> dict:
    return {
        "schema_version": 1,
        "profile": "commander_review",
        "fingerprint": "frontier-fingerprint",
        "card_data_snapshot": {
            "oracle_source_sha256": "b" * 64,
            "rulings_source_sha256": "c" * 64,
            "scryfall_oracle_updated_at": "2026-08-04T00:00:00Z",
            "scryfall_rulings_updated_at": "2026-08-04T00:00:00Z",
        },
        "cards_considered": 3,
        "cards": [
            {
                "oracle_id": "oracle-exact",
                "card_name": "Exact Bird",
                "oracle_ir_status": "exact",
                "card_program_status": "trusted",
                "card_program_trust_basis": "capability_closed",
                "material_ability_count": 1,
                "exact_ability_count": 1,
                "minimum_known_blocker_set": [],
                "abilities": [
                    {
                        "ability_id": "front:n1",
                        "face_id": "front",
                        "kind": "keyword_ability",
                        "status": "exact",
                        "template_id": "printed-keyword-list-v1",
                        "blockers": {
                            "capability_ids": ["combat.block.flying"],
                            "mechanic_ids": ["flying"],
                        },
                    }
                ],
            },
            {
                "oracle_id": "oracle-residual-a",
                "card_name": "Residual Alpha",
                "oracle_ir_status": "unresolved",
                "card_program_status": "residual",
                "card_program_trust_basis": "unresolved",
                "material_ability_count": 1,
                "exact_ability_count": 0,
                "minimum_known_blocker_set": [
                    "effect_clause:unparsed-target-creature-gets"
                ],
                "abilities": [
                    {
                        "ability_id": "front:n1",
                        "face_id": "front",
                        "kind": "spell_effect",
                        "status": "unresolved",
                        "blockers": {
                            "canonical_family_ids": [
                                "effect_clause:unparsed-target-creature-gets"
                            ]
                        },
                        "residuals": [
                            {
                                "residual_id": "r1",
                                "family_ids": [
                                    "effect_clause:unparsed-target-creature-gets"
                                ],
                            }
                        ],
                    }
                ],
            },
            {
                "oracle_id": "oracle-residual-b",
                "card_name": "Residual Beta",
                "oracle_ir_status": "unresolved",
                "card_program_status": "residual",
                "card_program_trust_basis": "unresolved",
                "material_ability_count": 1,
                "exact_ability_count": 0,
                "minimum_known_blocker_set": [
                    "effect_clause:unparsed-draw-two-cards"
                ],
                "abilities": [
                    {
                        "ability_id": "front:n1",
                        "face_id": "front",
                        "kind": "spell_effect",
                        "status": "unresolved",
                        "blockers": {
                            "canonical_family_ids": [
                                "effect_clause:unparsed-draw-two-cards"
                            ]
                        },
                        "residuals": [
                            {
                                "residual_id": "r1",
                                "family_ids": [
                                    "effect_clause:unparsed-draw-two-cards"
                                ],
                            }
                        ],
                    }
                ],
            },
        ],
        "family_candidates": [
            {
                "family_id": "effect_clause:unparsed-target-creature-gets",
                "occurrences": 1,
                "expected_exact_card_gain": 1,
                "expected_exact_ability_gain": 1,
                "prerequisite_capabilities": [],
                "interaction_risk": "medium",
            },
            {
                "family_id": "effect_clause:unparsed-draw-two-cards",
                "occurrences": 1,
                "expected_exact_card_gain": 1,
                "expected_exact_ability_gain": 1,
                "prerequisite_capabilities": [],
                "interaction_risk": "medium",
            },
        ],
    }


def _inputs() -> dict:
    policy = copy.deepcopy(load_reusable_piece_policy(ROOT))
    policy["ambient_high_risk_piece_pairs"] = [
        [
            "capability.combat.block.flying",
            "residual.effect_clause.unparsed-clause-grammar",
        ]
    ]
    return {
        "frontier": _frontier(),
        "capability_registry": {
            "registry_version": 30,
            "capabilities": [
                {
                    "id": "combat.block.flying",
                    "status": "trusted",
                    "implementation_components": ["aerial_block_verdict"],
                    "official_rules": ["702.9"],
                    "positive_tests": ["test_flying_blocks"],
                    "negative_tests": ["test_ground_cannot_block"],
                    "multiplayer_tests": [],
                    "privacy_tests": [],
                    "replay_tests": ["test_flying_replay"],
                    "interaction_tests": ["test_flying_reach"],
                    "blockers": [],
                }
            ],
        },
        "mechanics_registry": {
            "schema_version": 1,
            "mechanics": [
                {
                    "mechanic_id": "flying",
                    "official_name": "Flying",
                    "coverage_status": "trusted",
                    "rule_references": ["702.9"],
                    "implementation_component": "aerial_block_verdict",
                    "test_ids": ["test_flying_reach"],
                }
            ],
        },
        "runtime_status": {
            "capability_registry_fingerprint": "d" * 64,
            "capability_evidence_fingerprint": "e" * 64,
            "semantic_handler_registry_fingerprint": "f" * 64,
            "runtime_component_registry_fingerprint": "1" * 64,
            "semantic_handlers": [],
            "runtime_components": [],
        },
        "rules_index": {
            "rules": [
                {"rule_id": "702.9"},
                {"rule_id": "702.10"},
            ]
        },
        "oracle_coverage": {
            "compiler_version": "oracle-ir-test",
            "status_counts": {"exact": 1},
        },
        "program_coverage": {
            "card_program_schema_version": 2,
            "trust_basis_counts": {"capability_closed": 1},
            "material_residuals": 2,
            "failures": [],
        },
        "architecture_audit": {
            "architecture": {"debt_trend": {"dimensions": {}}}
        },
        "platform_status": {
            "generated": {
                "evaluated_source_tree_hash": "2" * 64,
                "source_tree_fingerprint_algorithm": "test-v1",
            },
            "snapshots": {
                "comprehensive_rules": {
                    "effective_date": "2026-06-19",
                    "sha256": "a" * 64,
                    "status": "pinned",
                },
                "oracle": {
                    "updated_at": "2026-08-04T00:00:00Z",
                    "sha256": "b" * 64,
                    "status": "pinned",
                },
                "rulings": {
                    "updated_at": "2026-08-04T00:00:00Z",
                    "sha256": "c" * 64,
                    "status": "pinned",
                },
            },
        },
        "policy": policy,
        "interaction_evidence": {
            "schema_version": 2,
            "declarations": [
                {
                    "evidence_class": "interaction",
                    "assurance_kind": "runtime_composition",
                    "test_id": "test_flying_reach",
                    "piece_ids": [
                        "capability.combat.block.flying",
                        "mechanic.flying",
                    ],
                    "capability_ids": ["combat.block.flying"],
                    "interaction_order": 2,
                    "assertion": (
                        "The test exercises compiled Flying through the "
                        "capability-owned block restriction."
                    ),
                }
            ],
        },
        "known_test_ids": {"test_flying_reach"},
        "ruling_counts": {"oracle-exact": 2},
    }


def _artifacts() -> dict:
    return build_reusable_piece_artifacts(**_inputs())


class ReusablePieceInventoryTests(unittest.TestCase):
    def test_architecture_projection_accepts_baseline_and_audit_schemas(self) -> None:
        expected = {
            "card_named_helpers": 1,
            "direct_game_state_writes": 5,
            "engine_logical_lines": 10,
            "legacy_card_specific_operations": 2,
            "oracle_id_literals": 1,
            "oversized_functions_and_methods": 1,
            "oversized_modules": 1,
            "prohibited_identity_dispatch_count": 0,
        }
        baseline = {
            "card_named_helpers": [{}],
            "direct_game_state_writes_by_file": {"a.py": 2, "b.py": 3},
            "engine": {"logical_lines": 10},
            "legacy_card_specific_operations": ["a", "b"],
            "oracle_id_literals": [{}],
            "oversized_functions_and_methods": [{}],
            "oversized_modules": [{}],
        }
        audit = {
            "architecture": {
                "debt_trend": {
                    "dimensions": {
                        key: {"current": value}
                        for key, value in expected.items()
                    }
                }
            }
        }

        self.assertEqual(expected, project_architecture_metrics(baseline))
        self.assertEqual(expected, project_architecture_metrics(audit))

    def test_interaction_evidence_has_closed_explicit_semantics(self) -> None:
        evidence = _inputs()["interaction_evidence"]
        validate_interaction_evidence(evidence)

        for mutation in (
            {"evidence_class": "positive"},
            {"assurance_kind": "fail_closed_runtime_admission"},
            {"piece_ids": ["mechanic.flying"]},
            {"interaction_order": True},
            {"capability_ids": ["combat.block.reach"]},
        ):
            invalid = copy.deepcopy(evidence)
            invalid["declarations"][0].update(mutation)
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    validate_interaction_evidence(invalid)

    def test_tracked_interaction_evidence_has_closed_explicit_semantics(
        self,
    ) -> None:
        evidence = json.loads(
            (ROOT / "platform" / "reusable-piece-interaction-evidence.json")
            .read_text(encoding="utf-8")
        )
        validate_interaction_evidence(evidence)

    def test_reusable_piece_inventory_classifies_every_material_ability(self) -> None:
        artifacts = _artifacts()
        validate_reusable_piece_artifacts(
            artifacts, policy=load_reusable_piece_policy(ROOT)
        )
        matrix = artifacts["matrix"]
        self.assertEqual(matrix["summary"]["material_abilities_classified"], 3)
        self.assertEqual(matrix["summary"]["unclassified_material_spans"], 0)
        exact = card_piece_relations(artifacts["card_index"], "Exact Bird")
        self.assertGreaterEqual(
            {row["piece_id"] for row in exact["pieces"]},
            {
                "capability.combat.block.flying",
                "mechanic.flying",
                "compiler.node.keyword_ability",
                "compiler.template.printed-keyword-list-v1",
            },
        )

    def test_oracle_clause_clusters_share_one_reusable_grammar_piece(self) -> None:
        artifacts = _artifacts()
        piece_id = "residual.effect_clause.unparsed-clause-grammar"
        piece = next(
            row
            for row in artifacts["matrix"]["pieces"]
            if row["piece_id"] == piece_id
        )
        self.assertEqual(piece["counts"]["distinct_oracle_ids"], 2)
        self.assertEqual(piece["frontier"]["sole_blocker_cards"], 2)
        self.assertEqual(piece["frontier"]["expected_exact_card_gain"], 2)
        self.assertEqual(
            piece["source_ids"]["frontier_family"],
            [
                "effect_clause:unparsed-draw-two-cards",
                "effect_clause:unparsed-target-creature-gets",
            ],
        )

    def test_explicit_declaration_is_pairwise_interaction_evidence(self) -> None:
        interactions = _artifacts()["interactions"]["pairs"]
        pair = next(
            row
            for row in interactions
            if set(row["piece_ids"])
            == {"capability.combat.block.flying", "mechanic.flying"}
        )

        self.assertTrue(pair["covered"])
        self.assertEqual(["test_flying_reach"], pair["evidence_test_ids"])
        self.assertEqual(
            ["runtime_composition"], pair["evidence_assurance_kinds"]
        )

        incidental = _inputs()
        incidental["interaction_evidence"] = {
            "schema_version": 2,
            "declarations": [],
        }
        uncovered = build_reusable_piece_artifacts(**incidental)[
            "interactions"
        ]["pairs"]
        pair = next(
            row
            for row in uncovered
            if set(row["piece_ids"])
            == {"capability.combat.block.flying", "mechanic.flying"}
        )
        self.assertFalse(pair["covered"])
        self.assertEqual([], pair["evidence_test_ids"])
        self.assertEqual([], pair["evidence_assurance_kinds"])

    def test_ambient_cross_card_pair_is_visible_before_and_after_evidence(self) -> None:
        pair_ids = {
            "capability.combat.block.flying",
            "residual.effect_clause.unparsed-clause-grammar",
        }
        inputs = _inputs()
        rows = build_reusable_piece_artifacts(**inputs)["interactions"][
            "pairs"
        ]
        uncovered = next(
            row for row in rows if set(row["piece_ids"]) == pair_ids
        )
        self.assertEqual(0, uncovered["card_count"])
        self.assertEqual(0, uncovered["ability_count"])
        self.assertTrue(uncovered["high_risk"])
        self.assertFalse(uncovered["covered"])
        self.assertEqual(
            ["declared_ambient_high_risk"],
            uncovered["applicability_bases"],
        )

        inputs["interaction_evidence"]["declarations"].append(
            {
                "evidence_class": "interaction",
                "assurance_kind": "fail_closed_runtime_admission",
                "test_id": "test_flying_reach",
                "piece_ids": sorted(pair_ids),
                "capability_ids": ["combat.block.flying"],
                "interaction_order": 2,
                "assertion": (
                    "A synthetic cross-card fixture proves declared ambient "
                    "pairs become covered only through exact evidence."
                ),
            }
        )
        covered_rows = build_reusable_piece_artifacts(**inputs)[
            "interactions"
        ]["pairs"]
        covered = next(
            row for row in covered_rows if set(row["piece_ids"]) == pair_ids
        )
        self.assertTrue(covered["covered"])
        self.assertEqual(["test_flying_reach"], covered["evidence_test_ids"])
        self.assertEqual(
            ["fail_closed_runtime_admission"],
            covered["evidence_assurance_kinds"],
        )
        self.assertEqual(
            [
                "declared_ambient_high_risk",
                "explicit_interaction_evidence",
            ],
            covered["applicability_bases"],
        )

    def test_ambient_pair_policy_rejects_unknown_and_noncanonical_pieces(self) -> None:
        policy = copy.deepcopy(load_reusable_piece_policy(ROOT))
        policy["ambient_high_risk_piece_pairs"] = [
            ["mechanic.flying", "capability.combat.block.flying"]
        ]
        with self.assertRaisesRegex(ValueError, "ambient high-risk"):
            validate_reusable_piece_policy(policy)

        inputs = _inputs()
        inputs["policy"]["ambient_high_risk_piece_pairs"] = [
            ["capability.combat.block.flying", "mechanic.unknown"]
        ]
        with self.assertRaisesRegex(ValueError, "unknown pieces"):
            build_reusable_piece_artifacts(**inputs)

    def test_additional_cost_replacement_boundaries_are_ambient_high_risk(
        self,
    ) -> None:
        policy = load_reusable_piece_policy(ROOT)
        ambient_pairs = {
            tuple(pair) for pair in policy["ambient_high_risk_piece_pairs"]
        }
        expected = {
            (
                "capability.casting.additional_cost.fixed_counter_placement",
                "capability.counter.placement.quantity_replacement",
            ): "test_counter_additional_cost_replacement_suspends_before_mutation_and_replays",
            (
                "capability.casting.additional_cost.fixed_sacrifice",
                "capability.trigger.event.normalized_zone_change",
            ): "test_zone_replacement_suspends_cost_before_mutation_and_replays",
            (
                "capability.casting.additional_cost.fixed_sacrifice",
                "capability.zone.change.destination_replacement",
            ): "test_zone_replacement_suspends_cost_before_mutation_and_replays",
            (
                "capability.casting.additional_cost.zone_change.fixed_discard",
                "capability.zone.change.destination_replacement",
            ): "test_discard_replacement_suspends_before_mutation_and_replays",
            (
                "capability.trigger.event.normalized_zone_change",
                "capability.zone.change.destination_replacement",
            ): "test_zone_replacement_suspends_cost_before_mutation_and_replays",
        }

        self.assertLessEqual(set(expected), ambient_pairs)
        interactions = load_tracked_reusable_piece_artifacts(ROOT)[
            "interactions"
        ]
        rows_by_pair = {
            tuple(row["piece_ids"]): row for row in interactions["pairs"]
        }
        for pair_ids, test_id in expected.items():
            with self.subTest(pair_ids=pair_ids):
                row = rows_by_pair[pair_ids]
                self.assertTrue(row["high_risk"])
                self.assertTrue(row["covered"])
                self.assertIn(
                    "declared_ambient_high_risk",
                    row["applicability_bases"],
                )
                self.assertIn(
                    "explicit_interaction_evidence",
                    row["applicability_bases"],
                )
                self.assertIn(test_id, row["evidence_test_ids"])

    def test_baseline_is_snapshot_pinned_and_delta_starts_at_zero(self) -> None:
        artifacts = _artifacts()
        self.assertEqual(set(artifacts["delta"]["deltas"].values()), {0})
        changed = _inputs()
        changed["platform_status"] = copy.deepcopy(changed["platform_status"])
        changed["platform_status"]["snapshots"]["oracle"]["sha256"] = "9" * 64
        with self.assertRaisesRegex(ValueError, "Pinned snapshot changed"):
            build_reusable_piece_artifacts(
                **changed, baseline=artifacts["baseline"]
            )

    def test_policy_and_artifact_mutations_fail_closed(self) -> None:
        policy = load_reusable_piece_policy(ROOT)
        malformed_policy = copy.deepcopy(policy)
        malformed_policy["relation_types"].append(
            malformed_policy["relation_types"][0]
        )
        with self.assertRaisesRegex(ValueError, "relation types"):
            validate_reusable_piece_policy(malformed_policy)

        artifacts = _artifacts()
        malformed_matrix = copy.deepcopy(artifacts["matrix"])
        malformed_matrix["pieces"][0]["status"]["runtime"] = "complete"
        with self.assertRaisesRegex(ValueError, "invalid runtime status"):
            validate_reusable_piece_matrix(malformed_matrix, policy=policy)

    def test_rendering_is_independent_of_json_mapping_order(self) -> None:
        delta = _artifacts()["delta"]
        reloaded = json.loads(json.dumps(delta, sort_keys=True))
        self.assertEqual(
            render_reusable_piece_delta_markdown(delta),
            render_reusable_piece_delta_markdown(reloaded),
        )

    def test_tracked_matrix_is_valid_and_exposes_honest_boundaries(self) -> None:
        artifacts = load_tracked_reusable_piece_artifacts(ROOT)
        matrix = artifacts["matrix"]
        self.assertEqual(matrix["summary"]["cards_indexed"], 31_623)
        self.assertEqual(matrix["summary"]["unclassified_material_spans"], 0)
        self.assertFalse(matrix["complete_snapshot_claimed"])
        self.assertIn(
            "not yet behaviorally classified",
            matrix["ruling_evidence_boundary"],
        )
        self.assertGreater(
            artifacts["interactions"]["summary"]["applicable_high_risk_pairs"],
            0,
        )
        interaction_by_pair = {
            frozenset(row["piece_ids"]): row
            for row in artifacts["interactions"]["pairs"]
        }
        for mechanic_id, test_id in (
            (
                "mechanic.indestructible",
                "test_trample_assigns_lethal_over_indestructible_blocker",
            ),
            (
                "mechanic.protection",
                "test_lifelink_counts_only_damage_not_prevented_by_protection",
            ),
            (
                "mechanic.double-strike",
                "test_double_strike_trample_recomputes_after_blocker_leaves",
            ),
        ):
            pair = interaction_by_pair[
                frozenset(
                    {
                        "capability.combat.damage.assignment.trample",
                        mechanic_id,
                    }
                )
            ]
            self.assertTrue(pair["covered"])
            self.assertIn(test_id, pair["evidence_test_ids"])
        matrix_schema = json.loads(
            (ROOT / "schemas" / "reusable-piece-matrix.schema.json").read_text(
                encoding="utf-8"
            )
        )
        policy_schema = json.loads(
            (ROOT / "schemas" / "reusable-piece-policy.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator(matrix_schema).validate(matrix)
        Draft202012Validator(policy_schema).validate(
            load_reusable_piece_policy(ROOT)
        )

    def test_piece_commands_query_the_tracked_indexes(self) -> None:
        inventory = execute_reusable_piece_operation("inventory", root=ROOT)
        self.assertGreater(inventory["summary"]["piece_count"], 0)
        storm_crow = execute_reusable_piece_operation(
            "card", root=ROOT, card="Storm Crow"
        )
        self.assertEqual(storm_crow["card_name"], "Storm Crow")
        flying = execute_reusable_piece_operation(
            "show", root=ROOT, piece_id="mechanic.flying"
        )
        self.assertEqual(flying["label"], "Flying")
        cards = execute_reusable_piece_operation(
            "cards", root=ROOT, piece_id="mechanic.flying", limit=2
        )
        self.assertGreaterEqual(cards["card_count"], len(cards["cards"]))
        self.assertEqual(len(cards["cards"]), 2)
