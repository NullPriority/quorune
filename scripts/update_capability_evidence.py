from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.rules.capabilities import CapabilityRegistry
from quorune.rules.evidence import (
    CAPABILITY_EVIDENCE_SCHEMA_VERSION,
    EVIDENCE_CLASSES,
    capability_evidence_fingerprint,
    validate_capability_evidence_index,
)
from quorune.util import stable_json


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
DECLARATIONS_PATH = ROOT / "platform" / "capability-evidence-declarations.json"
OUTPUT_PATH = (
    ROOT / "quorune" / "rules" / "capability-evidence.json"
)
RULE_INDEX_PATH = ROOT / "rules" / "rule-index.json"
LEGACY_EVIDENCE_FIELDS = {
    "positive": "positive_tests",
    "negative": "negative_tests",
    "interaction": "interaction_tests",
    "multiplayer": "multiplayer_tests",
    "privacy": "privacy_tests",
    "replay": "replay_tests",
}
MUTATION_TESTS = {
    "choice.affected_player.fixed_sacrifice": (
        "tests.test_fixed_affected_player_sacrifices."
        "FixedAffectedPlayerSacrificeCompilerTests."
        "test_affected_player_sacrifice_compiler_mutant_is_killed"
    ),
    "choice.modal.fixed_one": (
        "tests.test_fixed_choose_one_modal_spells."
        "FixedChooseOneModalCompilerTests."
        "test_modal_compiler_mutation_is_killed"
    ),
    "choice.modal.fixed_nonrepeating": (
        "tests.test_fixed_nonrepeating_modal_programs."
        "FixedNonrepeatingModalCompilerTests."
        "test_modal_dependency_and_compiler_mutations_fail_closed"
    ),
    "counter.producer.bloodthirst": (
        "tests.test_bloodthirst_rules."
        "BloodthirstRuntimeTests."
        "test_bloodthirst_runtime_mutation_is_killed"
    ),
    "counter.producer.sunburst": (
        "tests.test_sunburst_rules."
        "SunburstRuntimeTests."
        "test_sunburst_runtime_mutation_is_killed"
    ),
    "counter.producer.effect_entry": (
        "tests.test_persist_undying_rules."
        "PersistUndyingRuntimeTests."
        "test_effect_entry_counter_generation_mutant_is_killed"
    ),
    "counter.producer.persist": (
        "tests.test_persist_undying_rules."
        "PersistUndyingCompilerTests."
        "test_death_return_dependencies_and_compiler_mutation_fail_closed"
    ),
    "counter.producer.undying": (
        "tests.test_persist_undying_rules."
        "PersistUndyingCompilerTests."
        "test_death_return_dependencies_and_compiler_mutation_fail_closed"
    ),
    "counter.producer.cumulative_upkeep_fixed_mana": (
        "tests.test_cumulative_upkeep_counter_placement."
        "CumulativeUpkeepCompilerTests."
        "test_dependency_and_compiler_mutations_fail_closed"
    ),
    "counter.producer.evolve": (
        "tests.test_evolve_counter_placement."
        "EvolveCompilerTests."
        "test_evolve_compiler_mutant_is_killed"
    ),
    "counter.producer.mentor": (
        "tests.test_mentor_rules."
        "MentorModelAndCompilerTests."
        "test_mentor_compiler_mutant_is_killed"
    ),
    "counter.producer.intrinsic_entry": (
        "tests.test_intrinsic_entry_counters."
        "IntrinsicEntryCounterTests."
        "test_intrinsic_entry_counter_generation_mutant_is_killed"
    ),
    "counter.producer.proliferate": (
        "tests.test_proliferate_compiler."
        "ProliferateCompilerTests."
        "test_proliferate_compiler_mutant_is_killed"
    ),
    "token.creation.additional_replacement": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_additional_token_replacement_mutant_is_killed"
    ),
    "token.creation.fixed_definition": (
        "tests.test_fixed_token_creation_effects."
        "FixedTokenCreationCompilerTests."
        "test_fixed_token_compiler_mutation_is_killed"
    ),
    "resolution.effect_sequence.fixed_clauses": (
        "tests.test_fixed_effect_clause_sequences."
        "FixedEffectClauseSequenceCompilerTests."
        "test_sequence_compiler_mutation_is_killed"
    ),
    "combat.block.landwalk.basic_type": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_basic_landwalk_keyword_mapping_mutant_is_killed"
    ),
    "timing.cast.printed_flash": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_flash_cast_timing_mutant_is_killed"
    ),
    "activation.tap_untap_cost.haste": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_haste_attack_and_activation_mutant_is_killed"
    ),
    "combat.attack.haste": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_haste_attack_and_activation_mutant_is_killed"
    ),
    "combat.block.flying": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_aerial_blocking_flying_and_reach_mutants_are_killed"
    ),
    "combat.block.reach": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_aerial_blocking_flying_and_reach_mutants_are_killed"
    ),
    "combat.trigger.flanking": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_flanking_qualifying_blocker_mutant_is_killed"
    ),
    "combat.trigger.bushido": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_bushido_instance_quantity_mutant_is_killed"
    ),
    "combat.damage.assignment.trample": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_trample_lethal_assignment_mutant_is_killed"
    ),
    "combat.attack.vigilance": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_combat_vigilance_mutant_is_killed"
    ),
    "protection.typed.debt": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_typed_protection_verdict_mutant_is_killed"
    ),
    "attachment.aura.simple_object": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_aura_cast_targeting_mutant_is_killed"
    ),
    "attachment.equip.fixed_mana": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_generic_equip_resolution_mutant_is_killed"
    ),
    "continuous.attached.fixed_characteristics": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_attached_characteristic_relation_mutant_is_killed"
    ),
    "life.change.effect": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_life_effect_commit_mutant_is_killed"
    ),
    "zone.draw.library_to_hand": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_fixed_activated_draw_capability_gate_mutant_is_killed"
    ),
    "trigger.event.normalized_zone_change": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_zone_trigger_detection_mutant_is_killed"
    ),
    "trigger.event.normalized_spell_cast": (
        "tests.test_prowess_rules."
        "ProwessRuntimeTests."
        "test_spell_cast_event_dispatch_mutant_is_killed"
    ),
    "trigger.keyword.prowess": (
        "tests.test_prowess_rules."
        "ProwessCompilerTests."
        "test_prowess_dependency_and_compiler_mutations_fail_closed"
    ),
    "continuous.basic_land_type.add_all_lands": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_basic_land_type_intrinsic_mana_mutant_is_killed"
    ),
    "continuous.power_toughness.fixed_anthem": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_fixed_anthem_applicability_mutant_is_killed"
    ),
    "continuous.resolution.fixed_characteristics_until_end_of_turn": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_resolution_continuous_effect_commit_mutant_is_killed"
    ),
    "target.revalidate_resolution": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_target_validation_mutant_is_killed"
    ),
    "target.public.player_or_damageable_permanent": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_target_validation_mutant_is_killed"
    ),
    "damage.amount.positive": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_damage_amount_guard_mutant_is_killed"
    ),
    "damage.batch.fixed_set": (
        "tests.test_fixed_mass_damage."
        "FixedDamageSetModelTests."
        "test_fixed_set_snapshot_order_and_predicate_mutants_are_killed"
    ),
    "damage.replacement.static_quantity": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_damage_replacement_prevention_mutants_are_killed"
    ),
    "damage.prevention.static_fixed": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_damage_replacement_prevention_mutants_are_killed"
    ),
    "damage.result.player_life": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_damage_result_dispatch_mutant_is_killed"
    ),
    "damage.result.creature_mark": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_damage_result_dispatch_mutant_is_killed"
    ),
    "damage.result.planeswalker_loyalty": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_damage_result_dispatch_mutant_is_killed"
    ),
    "damage.result.battle_defense": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_damage_result_dispatch_mutant_is_killed"
    ),
    "damage.result.multitype_permanent": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_damage_result_dispatch_mutant_is_killed"
    ),
    "damage.result.infect": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_keyword_damage_result_mutants_are_killed"
    ),
    "damage.result.wither": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_keyword_damage_result_mutants_are_killed"
    ),
    "damage.result.lifelink": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_keyword_damage_result_mutants_are_killed"
    ),
    "damage.result.toxic": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_keyword_damage_result_mutants_are_killed"
    ),
    "damage.result.replacement_order": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_replacement_nested_order_mutant_is_killed"
    ),
    "format.commander.damage.physical_identity": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_commander_identity_mutant_is_killed"
    ),
    "life.gain.replacement.static_multiplier": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_damage_result_replacement_component_mutants_are_killed"
    ),
    "permanent.destroy.effect": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_destruction_disposition_mutants_are_killed"
    ),
    "permanent.tap.effect": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_semantic_tap_state_mutants_are_killed"
    ),
    "permanent.untap.effect": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_semantic_tap_state_mutants_are_killed"
    ),
    "permanent.untap.all_creatures": (
        "tests.test_capability_implementation_mutations."
        "CapabilityImplementationMutationTests."
        "test_semantic_tap_state_mutants_are_killed"
    ),
    "casting.payment.convoke": (
        "tests.test_convoke_rules."
        "ConvokeCompilerTests."
        "test_convoke_compiler_mutant_is_killed"
    ),
}
EXTRA_EVIDENCE_TESTS = {
    capability_id: (
        (
            "rollback",
            "tests.test_semantic_handlers.TypedSemanticHandlerTests."
            "test_tap_state_resolution_rolls_back_atomically",
        ),
    )
    for capability_id in (
        "permanent.tap.effect",
        "permanent.untap.effect",
        "permanent.untap.all_creatures",
    )
}
EXTRA_EVIDENCE_TESTS["token.creation.additional_replacement"] = (
    (
        "rollback",
        "tests.test_replacement_model_hardening."
        "ReplacementImmutabilityTests."
        "test_additional_token_operation_rejects_wrong_event_without_mutation",
    ),
)
EXTRA_EVIDENCE_TESTS["token.creation.fixed_definition"] = (
    (
        "rollback",
        "tests.test_fixed_token_creation_effects."
        "FixedTokenCreationRuntimeTests."
        "test_compiled_fixed_token_effect_suspends_for_replacement_order",
    ),
)
EXTRA_EVIDENCE_TESTS["resolution.effect_sequence.fixed_clauses"] = (
    (
        "rollback",
        "tests.test_fixed_effect_clause_sequences."
        "FixedEffectClauseSequenceRuntimeTests."
        "test_private_scry_follows_token_creation_in_four_players",
    ),
)
EXTRA_EVIDENCE_TESTS["casting.payment.convoke"] = (
    (
        "mutation",
        "tests.test_convoke_rules.ConvokeModelTests."
        "test_convoke_planner_mutant_is_killed",
    ),
)


class EvidenceGenerationError(ValueError):
    pass


def _hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def discover_tests(root: Path) -> dict[str, str]:
    """Return unique bare-name to fully qualified unittest IDs."""

    discovered: dict[str, list[str]] = {}
    for path in sorted((root / "tests").glob("test_*.py")):
        module = f"tests.{path.stem}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    discovered.setdefault(node.name, []).append(
                        f"{module}.{node.name}"
                    )
            elif isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(
                        child, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ) and child.name.startswith("test_"):
                        discovered.setdefault(child.name, []).append(
                            f"{module}.{node.name}.{child.name}"
                        )
    ambiguous = {
        name: ids for name, ids in discovered.items() if len(ids) != 1
    }
    if ambiguous:
        details = "; ".join(
            f"{name}: {', '.join(ids)}"
            for name, ids in sorted(ambiguous.items())
        )
        raise EvidenceGenerationError(
            "Capability evidence requires unique test names: " + details
        )
    return {name: ids[0] for name, ids in discovered.items()}


def _declaration(
    capability: Mapping[str, Any],
    evidence_class: str,
    test_id: str,
) -> dict[str, Any]:
    return {
        "capability_id": capability["id"],
        "evidence_class": evidence_class,
        "test_id": test_id,
        "official_rule_ids": list(capability["official_rules"]),
        "supported_profiles": list(capability["supported_profiles"]),
        "applicability_note": capability["applicability"]["summary"],
    }


def bootstrap_declarations(
    registry_value: Mapping[str, Any],
    tests: Mapping[str, str],
) -> dict[str, Any]:
    """Build the one-time explicit declaration source from legacy citations."""

    declarations: list[dict[str, Any]] = []
    for capability in registry_value["capabilities"]:
        for evidence_class, field in LEGACY_EVIDENCE_FIELDS.items():
            for test_name in capability[field]:
                test_id = tests.get(test_name)
                if test_id is None:
                    raise EvidenceGenerationError(
                        f"Missing cited test: {test_name}"
                    )
                declarations.append(
                    _declaration(capability, evidence_class, test_id)
                )
        mutation_test = MUTATION_TESTS.get(capability["id"])
        if mutation_test is not None:
            declarations.append(
                _declaration(capability, "mutation", mutation_test)
            )
        for evidence_class, test_id in EXTRA_EVIDENCE_TESTS.get(
            capability["id"], ()
        ):
            declarations.append(
                _declaration(capability, evidence_class, test_id)
            )
    declarations.sort(
        key=lambda row: (
            row["capability_id"],
            row["evidence_class"],
            row["test_id"],
        )
    )
    return {"schema_version": 1, "declarations": declarations}


def build_index(
    *,
    registry_value: Mapping[str, Any],
    declaration_source: Mapping[str, Any],
    discovered_test_ids: set[str],
) -> dict[str, Any]:
    if set(declaration_source) != {"schema_version", "declarations"}:
        raise EvidenceGenerationError(
            "Capability evidence declaration source fields are invalid"
        )
    if declaration_source.get("schema_version") != 1:
        raise EvidenceGenerationError(
            "Unsupported capability evidence declaration schema_version"
        )
    declarations = declaration_source.get("declarations")
    if not isinstance(declarations, list):
        raise EvidenceGenerationError("declarations must be a list")
    known_rules = {
        row["rule_id"]
        for row in json.loads(RULE_INDEX_PATH.read_text(encoding="utf-8"))[
            "rules"
        ]
    }
    for index, row in enumerate(declarations):
        if not isinstance(row, Mapping):
            raise EvidenceGenerationError(
                f"declarations[{index}] must be an object"
            )
        if row.get("evidence_class") not in EVIDENCE_CLASSES:
            raise EvidenceGenerationError(
                f"declarations[{index}] has an unknown evidence class"
            )
        if row.get("test_id") not in discovered_test_ids:
            raise EvidenceGenerationError(
                f"Removed or renamed evidence test: {row.get('test_id')}"
            )
        if set(row.get("official_rule_ids", [])) - known_rules:
            raise EvidenceGenerationError(
                f"declarations[{index}] cites an unknown official rule"
            )
        if str(row.get("test_id")).endswith(
            "test_registry_matches_schema_rules_snapshot_and_test_evidence"
        ):
            raise EvidenceGenerationError(
                "Registry-validation tests cannot be behavioral evidence"
            )
    registry = CapabilityRegistry(registry_value)
    payload = {
        "schema_version": CAPABILITY_EVIDENCE_SCHEMA_VERSION,
        "registry_fingerprint": registry.fingerprint,
        "declaration_source_fingerprint": _hash(declaration_source),
        "declarations": declarations,
    }
    payload["fingerprint"] = capability_evidence_fingerprint(payload)
    validate_capability_evidence_index(payload, registry=registry)
    return payload


def _json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument(
        "--bootstrap-declarations",
        action="store_true",
        help="One-time migration from legacy registry test citations.",
    )
    args = parser.parse_args()
    registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    discovered = discover_tests(ROOT)
    if args.bootstrap_declarations:
        source = bootstrap_declarations(registry_value, discovered)
        DECLARATIONS_PATH.write_text(_json_text(source), encoding="utf-8")
        return 0
    source = json.loads(DECLARATIONS_PATH.read_text(encoding="utf-8"))
    index = build_index(
        registry_value=registry_value,
        declaration_source=source,
        discovered_test_ids=set(discovered.values()),
    )
    expected = _json_text(index)
    if args.write:
        OUTPUT_PATH.write_text(expected, encoding="utf-8")
        return 0
    actual = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""
    if actual != expected:
        print(
            "quorune/rules/capability-evidence.json is stale; run "
            "python scripts/update_capability_evidence.py --write",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
