from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.util import stable_json
from scripts.update_architecture_audit import analyze_production


OUTPUT = ROOT / "platform" / "module-classifications.json"
POLICY = ROOT / "platform" / "architecture-policy.json"
ALLOWED_DEPENDENCIES = {
    "domain": ["domain"],
    "rules": ["domain", "rules", "semantics"],
    "semantics": ["adapter", "domain", "rules", "semantics"],
    "adapter": ["adapter", "application", "domain", "rules", "semantics"],
    "application": [
        "adapter",
        "application",
        "domain",
        "rules",
        "semantics",
    ],
    "transport": [
        "adapter",
        "application",
        "domain",
        "rules",
        "semantics",
        "transport",
    ],
}


def _hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _layer(relative: str, protected_rules_modules: set[str]) -> str:
    if relative.startswith("server/") or relative == "simctl.py":
        return "transport"
    if relative in {
        "quorune/additional_cost_vocabulary.py",
        "quorune/activated_ability_descriptor.py",
        "quorune/ability_fragment_primitives.py",
        "quorune/ability_fragments.py",
        "quorune/bloodthirst.py",
        "quorune/cast_cost_modifiers.py",
        "quorune/characteristic_fragments.py",
        "quorune/counter_maximums.py",
        "quorune/counter_names.py",
        "quorune/counter_snapshot.py",
        "quorune/damage_source.py",
        "quorune/damage_modifier_state.py",
        "quorune/death_return.py",
        "quorune/declaration_fragments.py",
        "quorune/declaration_rule_effects.py",
        "quorune/continuous_conditions.py",
        "quorune/continuous_effect_model.py",
        "quorune/creature_subtypes.py",
        "quorune/enchant_spec.py",
        "quorune/entry_counter_model.py",
        "quorune/evolve.py",
        "quorune/fixed_keyword_entry_counters.py",
        "quorune/fixed_token_production.py",
        "quorune/day_night_model.py",
        "quorune/spell_history_transform_model.py",
        "quorune/leveler_bands.py",
        "quorune/modular.py",
        "quorune/renown.py",
        "quorune/model.py",
        "quorune/object_predicate.py",
        "quorune/target_forms.py",
        "quorune/target_numeric.py",
        "quorune/prevention_triggers.py",
        "quorune/read_ahead.py",
        "quorune/replacement/immutable.py",
        "quorune/riot.py",
        "quorune/trigger_batches.py",
        "quorune/trigger_participation.py",
        "quorune/turn_priority_model.py",
        "quorune/unleash.py",
    }:
        return "domain"
    if relative in {
        "quorune/python_runtime.py",
        "quorune/util.py",
        "quorune/version.py",
    }:
        return "domain"
    if relative.startswith(
        (
            "quorune/card_programs/",
            "quorune/compiler/",
            "quorune/semantic_runtime/",
            "quorune/semantic_choices/",
            "quorune/effect_runtime/",
            "quorune/reusable_pieces/",
            "quorune/card_overrides/",
        )
    ) or relative in {
        "quorune/card_program_faces.py",
        "quorune/carddb_characteristics.py",
        "quorune/effect_contracts.py",
        "quorune/oracle_ir.py",
        "quorune/semantics.py",
        "quorune/ability_fragment_host.py",
        "quorune/compiled_ability_fragments.py",
        "quorune/compiled_activated_abilities.py",
        "quorune/compiled_cast_costs.py",
        "quorune/compiled_cast_lifecycles.py",
        "quorune/compiled_madness.py",
        "quorune/compiled_cast_timing.py",
        "quorune/compiled_morph.py",
        "quorune/compiled_kicker.py",
        "quorune/compiled_bestow.py",
        "quorune/compiled_flashback.py",
    }:
        return "semantics"
    if relative in {
        "quorune/carddb.py",
        "quorune/deck.py",
        "quorune/moxfield.py",
        "quorune/profiles.py",
    }:
        return "adapter"
    if relative in protected_rules_modules:
        return "rules"
    if relative.startswith(
        (
            "quorune/aura/",
            "quorune/drawing/",
            "quorune/replacement/",
            "quorune/rules/",
            "quorune/selection/",
        )
    ) or relative in {
        "quorune/activation_usage.py",
        "quorune/abilities.py",
        "quorune/cascade.py",
        "quorune/affected_permanents.py",
        "quorune/amass.py",
        "quorune/ability_fragments.py",
        "quorune/attachment_references.py",
        "quorune/attachments.py",
        "quorune/attack_transition_engine_adapter.py",
        "quorune/attack_transition_model.py",
        "quorune/attack_transition_resolution.py",
        "quorune/choice_forms.py",
        "quorune/combat.py",
        "quorune/block_transition_engine_adapter.py",
        "quorune/block_transitions.py",
        "quorune/combat_damage_assignment.py",
        "quorune/combat_damage_engine_adapter.py",
        "quorune/combat_damage_events.py",
        "quorune/combat_damage_projection.py",
        "quorune/combat_damage_sequence.py",
        "quorune/combat_damage_snapshot.py",
        "quorune/combat_damage_trample.py",
        "quorune/combat_damage_values.py",
        "quorune/combat_relationship_state.py",
        "quorune/combat_constraints.py",
        "quorune/combat_evasion.py",
        "quorune/combat_evasion_engine_adapter.py",
        "quorune/commander.py",
        "quorune/commander_zones.py",
        "quorune/convoke.py",
        "quorune/cast_timing.py",
        "quorune/cast_lifecycles.py",
        "quorune/continuous_effects.py",
        "quorune/zone_object_keyword_model.py",
        "quorune/zone_object_keyword_grants.py",
        "quorune/zone_object_subtype_grants.py",
        "quorune/counter_placement.py",
        "quorune/counter_placement_sets.py",
        "quorune/counter_snapshot.py",
        "quorune/keyword_counters.py",
        "quorune/counter_removal.py",
        "quorune/counter_state.py",
        "quorune/creature_subtypes.py",
        "quorune/cumulative_upkeep.py",
        "quorune/damage.py",
        "quorune/damage_prevention.py",
        "quorune/damage_transaction.py",
        "quorune/damage_values.py",
        "quorune/damage_results.py",
        "quorune/deathtouch.py",
        "quorune/defender.py",
        "quorune/declaration_costs.py",
        "quorune/declaration_requirements.py",
        "quorune/declaration_requirement_runtime.py",
        "quorune/declaration_restrictions.py",
        "quorune/delayed_triggers.py",
        "quorune/destruction.py",
        "quorune/destruction_sets.py",
        "quorune/dynamic_characteristics.py",
        "quorune/engine.py",
        "quorune/entry_counter_coordination.py",
        "quorune/entry_counters.py",
        "quorune/entry_state_conditions.py",
        "quorune/entry_state_metrics.py",
        "quorune/entry_keyword_grants.py",
        "quorune/entry_results.py",
        "quorune/errors.py",
        "quorune/enchant_spec.py",
        "quorune/life_change.py",
        "quorune/life_state.py",
        "quorune/land_entry_coordination.py",
        "quorune/landwalk.py",
        "quorune/mana.py",
        "quorune/mana_activation.py",
        "quorune/mana_restrictions.py",
        "quorune/color_set_mana_abilities.py",
        "quorune/fixed_mana_abilities.py",
        "quorune/intrinsic_basic_land_mana.py",
        "quorune/mana_ability_runtime.py",
        "quorune/mana_source_discovery.py",
        "quorune/mana_undo.py",
        "quorune/mechanic_contracts.py",
        "quorune/menace.py",
        "quorune/milling.py",
        "quorune/mentor.py",
        "quorune/morph.py",
        "quorune/kicker.py",
        "quorune/bestow.py",
        "quorune/flashback.py",
        "quorune/casting_cost_host.py",
        "quorune/permanent_exile.py",
        "quorune/public_zone_moves.py",
        "quorune/permanent_designations.py",
        "quorune/day_night.py",
        "quorune/permanent_transform.py",
        "quorune/spell_history_transform.py",
        "quorune/zone_object_state.py",
        "quorune/zone_transition_journal.py",
        "quorune/zone_transition_model.py",
        "quorune/zone_transitions.py",
        "quorune/permissions.py",
        "quorune/protection.py",
        "quorune/replacement_decisions.py",
        "quorune/replacement_effects.py",
        "quorune/relative_power_target.py",
        "quorune/return_to_hand.py",
        "quorune/rule_conformance.py",
        "quorune/rules_corpus.py",
        "quorune/rules_scheduler.py",
        "quorune/work_selection.py",
        "quorune/work_selection_bundles.py",
        "quorune/work_selection_common.py",
        "quorune/work_selection_evidence.py",
        "quorune/work_selection_measurement.py",
        "quorune/saga_lifecycle.py",
        "quorune/saga_progression.py",
        "quorune/self_zone_move.py",
        "quorune/turn_counter_coordination.py",
        "quorune/turn_priority_owner.py",
        "quorune/turn_step_owner.py",
        "quorune/untap_step.py",
        "quorune/untap_step_coordination.py",
        "quorune/unearth.py",
        "quorune/card_overrides/shortcuts.py",
        "quorune/stack_counter.py",
        "quorune/stack_resolution.py",
        "quorune/state_based_actions.py",
        "quorune/state_based_execution.py",
        "quorune/state_planner.py",
        "quorune/standard_token_abilities.py",
        "quorune/tap_state.py",
        "quorune/target_protection.py",
        "quorune/target_protection_engine_adapter.py",
        "quorune/target_characteristics.py",
        "quorune/target_history.py",
        "quorune/target_predicates.py",
        "quorune/targets.py",
        "quorune/token_creation.py",
        "quorune/turn_history.py",
        "quorune/trigger_targeting.py",
        "quorune/trigger_processing.py",
        "quorune/object_query.py",
    }:
        return "rules"
    return "application"


def _owner(relative: str, layer: str) -> str:
    if relative.startswith("server/"):
        return "server_transport"
    if relative.startswith("quorune/semantic_runtime/"):
        return "semantic_runtime"
    if relative.startswith("quorune/semantic_choices/"):
        return "semantic_choices"
    if relative.startswith("quorune/selection/"):
        return "search_target_and_choice"
    if relative.startswith("quorune/effect_runtime/"):
        return "effect_runtime"
    if relative.startswith("quorune/card_overrides/"):
        return (
            "game_record_compatibility"
            if relative.endswith(("/__init__.py", "/game_record_v3.py"))
            else "reviewed_card_overrides"
        )
    if relative == "quorune/effect_contracts.py":
        return "effect_runtime"
    if relative.startswith("quorune/card_programs/"):
        return "card_programs"
    if relative.startswith("quorune/reusable_pieces/"):
        return "reusable_piece_inventory"
    if relative.startswith("quorune/compiler/"):
        return "oracle_compiler"
    if relative == "quorune/rules/source_references.py":
        return "oracle_compiler"
    if relative in {
        "quorune/casting_cost_host.py",
        "quorune/cast_cost_modifiers.py",
        "quorune/cast_lifecycles.py",
        "quorune/compiled_cast_lifecycles.py",
        "quorune/self_cast_reductions.py",
    }:
        return "casting_activation_and_costs"
    if relative in {
        "quorune/compiled_morph.py",
        "quorune/morph.py",
        "quorune/rules/morph_actions.py",
    }:
        return "face_down_cards"
    if relative in {
        "quorune/compiled_kicker.py",
        "quorune/kicker.py",
    }:
        return "casting_kicker"
    if relative in {
        "quorune/bestow.py",
        "quorune/compiled_bestow.py",
    } or relative in {
        "quorune/compiler/bestow_nodes.py",
        "quorune/semantic_runtime/bestow.py",
    }:
        return "casting_bestow"
    if relative in {
        "quorune/flashback.py",
        "quorune/compiled_flashback.py",
        "quorune/compiler/flashback_nodes.py",
        "quorune/semantic_runtime/flashback.py",
    }:
        return "casting_flashback"
    if relative.startswith("quorune/rules/"):
        return "rules_capabilities"
    if relative.startswith("quorune/aura/"):
        return "aura_rules"
    if relative in {
        "quorune/ability_fragment_host.py",
        "quorune/ability_fragments.py",
        "quorune/characteristic_fragments.py",
        "quorune/compiled_ability_fragments.py",
    }:
        return "ability_fragments"
    if relative in {
        "quorune/activated_ability_descriptor.py",
        "quorune/compiled_activated_abilities.py",
        "quorune/counter_keyword_abilities.py",
        "quorune/crew.py",
        "quorune/cycling_abilities.py",
        "quorune/station.py",
    }:
        return "activated_abilities"
    if relative == "quorune/unearth.py":
        return "graveyard_actions"
    if relative == "quorune/self_zone_move.py":
        return "zones_and_object_identity"
    if relative in {
        "quorune/impulse_access.py",
        "quorune/impulse_access_model.py",
        "quorune/milling.py",
    }:
        return "zones_and_object_identity"
    if relative in {
        "quorune/entry_state_conditions.py",
        "quorune/entry_state_metrics.py",
    }:
        return "zones_and_object_identity"
    if relative in {
        "quorune/cast_timing.py",
        "quorune/compiled_cast_timing.py",
    }:
        return "cast_timing"
    if relative == "quorune/enchant_spec.py":
        return "aura_rules"
    if relative in {
        "quorune/bloodthirst.py",
        "quorune/day_night_model.py",
        "quorune/spell_history_transform_model.py",
        "quorune/read_ahead.py",
        "quorune/riot.py",
        "quorune/unleash.py",
    }:
        return "keyword_abilities"
    if relative == "quorune/protection.py":
        return "protection"
    if relative.startswith("quorune/drawing/"):
        return "drawing"
    if relative.startswith("quorune/replacement/"):
        return "replacement_effects"
    if relative in {
        "quorune/commander.py",
        "quorune/commander_pairing.py",
    }:
        return "commander_variant"
    if relative in {
        "quorune/attack_transition_engine_adapter.py",
        "quorune/attack_transition_model.py",
        "quorune/attack_transition_resolution.py",
        "quorune/block_transition_engine_adapter.py",
        "quorune/block_transitions.py",
        "quorune/mentor.py",
    }:
        return "combat_transitions"
    if relative in {
        "quorune/damage_modifier_state.py",
        "quorune/damage_source.py",
        "quorune/prevention_triggers.py",
        "quorune/replacement/immutable.py",
    }:
        return "damage"
    if relative in {
        "quorune/counter_names.py",
        "quorune/counter_removal.py",
        "quorune/counter_state.py",
    }:
        return "counter_state"
    if relative == "quorune/counter_maximums.py":
        return "state_based_actions"
    if relative in {
        "quorune/attachment_references.py",
        "quorune/attachments.py",
    }:
        return "attachments"
    if relative in {
        "quorune/life_change.py",
        "quorune/life_state.py",
    }:
        return "life_state"
    if relative in {
        "quorune/delayed_triggers.py",
        "quorune/cascade.py",
        "quorune/player_result_events.py",
        "quorune/trigger_batches.py",
        "quorune/trigger_discovery.py",
        "quorune/trigger_participation.py",
        "quorune/trigger_processing.py",
        "quorune/trigger_targeting.py",
        "quorune/spell_copy_engine_adapter.py",
    }:
        return "trigger_processing"
    if relative == "quorune/tap_state.py":
        return "tap_state_effects"
    if relative in {
        "quorune/declaration_rule_effects.py",
        "quorune/dynamic_characteristics.py",
        "quorune/characteristic_evaluation_host.py",
        "quorune/leveler_bands.py",
        "quorune/zone_object_keyword_model.py",
        "quorune/zone_object_keyword_grants.py",
        "quorune/zone_object_subtype_grants.py",
    }:
        return "continuous_effects"
    if relative == "quorune/creature_subtypes.py":
        return "card_characteristics"
    if relative in {
        "quorune/mana.py",
        "quorune/mana_activation.py",
        "quorune/mana_restrictions.py",
        "quorune/color_set_mana_abilities.py",
        "quorune/fixed_mana_abilities.py",
        "quorune/intrinsic_basic_land_mana.py",
        "quorune/mana_ability_runtime.py",
        "quorune/mana_source_discovery.py",
        "quorune/mana_mode_effects.py",
        "quorune/mana_payment_continuations.py",
        "quorune/mana_undo.py",
        "quorune/semantic_runtime/color_set_mana_abilities.py",
    }:
        return "mana_rules"
    if relative in {
        "quorune/affected_permanents.py",
        "quorune/object_predicate.py",
        "quorune/object_query.py",
    }:
        return "object_query"
    if relative == "quorune/state_planner.py":
        return "state_change_planning"
    if relative in {
        "quorune/saga_lifecycle.py",
        "quorune/state_based_actions.py",
    }:
        return "state_based_actions"
    if relative in {
        "quorune/amass.py",
        "quorune/counter_placement.py",
        "quorune/counter_placement_sets.py",
        "quorune/keyword_counters.py",
        "quorune/entry_counter_coordination.py",
        "quorune/entry_counters.py",
        "quorune/entry_keyword_grants.py",
        "quorune/entry_results.py",
        "quorune/entry_counter_model.py",
        "quorune/evolve.py",
        "quorune/fixed_keyword_entry_counters.py",
        "quorune/modular.py",
        "quorune/renown.py",
        "quorune/death_return.py",
        "quorune/saga_progression.py",
        "quorune/turn_counter_coordination.py",
    }:
        return "counter_placement"
    if relative == "quorune/cumulative_upkeep.py":
        return "cumulative_upkeep"
    if relative in {
        "quorune/control_history.py",
        "quorune/echo.py",
    }:
        return "echo"
    if relative in {
        "quorune/destruction.py",
        "quorune/destruction_sets.py",
        "quorune/state_based_execution.py",
    }:
        return "destruction"
    if relative == "quorune/regeneration.py":
        return "regeneration"
    if relative in {
        "quorune/damage.py",
        "quorune/damage_prevention.py",
        "quorune/damage_prevention_aftermath.py",
        "quorune/damage_prevention_creation.py",
        "quorune/damage_transaction.py",
        "quorune/damage_values.py",
        "quorune/damage_results.py",
        "quorune/deathtouch.py",
    }:
        return "damage"
    if relative.startswith("quorune/combat_damage_") or relative in {
        "quorune/combat_relationship_state.py",
    }:
        return "combat_damage"
    if relative in {
        "quorune/fixed_token_production.py",
        "quorune/standard_token_abilities.py",
        "quorune/token_creation.py",
    }:
        return "token_creation"
    if relative == "quorune/return_to_hand.py":
        return "return_to_hand"
    if relative == "quorune/permanent_exile.py":
        return "permanent_exile"
    if relative in {
        "quorune/commander_zones.py",
        "quorune/public_zone_moves.py",
    }:
        return "zones_and_object_identity"
    if relative == "quorune/permanent_designations.py":
        return "permanent_designations"
    if relative in {
        "quorune/zone_object_state.py",
        "quorune/zone_transition_journal.py",
        "quorune/zone_transition_model.py",
        "quorune/zone_transitions.py",
    }:
        return "zones_and_object_identity"
    if relative in {
        "quorune/day_night.py",
        "quorune/permanent_transform.py",
        "quorune/spell_history_transform.py",
    }:
        return "day_night_and_transform"
    if relative in {
        "quorune/turn_priority_model.py",
        "quorune/turn_priority_owner.py",
        "quorune/turn_step_owner.py",
    }:
        return "turn_priority_and_decisions"
    if relative in {
        "quorune/untap_step.py",
        "quorune/untap_step_coordination.py",
    }:
        return "untap_step"
    if relative in {
        "quorune/stack_counter.py",
        "quorune/stack_resolution.py",
    }:
        return "stack_counter"
    if relative in {
        "quorune/relative_power_target.py",
        "quorune/target_protection.py",
        "quorune/target_protection_engine_adapter.py",
        "quorune/target_characteristics.py",
        "quorune/target_forms.py",
        "quorune/target_history.py",
        "quorune/target_numeric.py",
        "quorune/target_predicates.py",
        "quorune/targets.py",
    }:
        return "targeting"
    if relative == "quorune/replacement_decisions.py":
        return "replacement_effects"
    if relative in {
        "quorune/rules_scheduler.py",
        "quorune/work_selection.py",
        "quorune/work_selection_bundles.py",
        "quorune/work_selection_common.py",
        "quorune/work_selection_evidence.py",
        "quorune/work_selection_measurement.py",
    }:
        return "rules_governance"
    if relative in {
        "quorune/record.py",
        "quorune/record_trust.py",
    }:
        return "game_record"
    return f"legacy_{layer}"


def build_classifications() -> dict[str, Any]:
    source, _paths, analyses = analyze_production()
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    mutable = set(policy["game_state_access"]["mutable_owners"])
    readers = set(policy["game_state_access"]["read_only_consumers"])
    protected_rules_modules = set(policy["protected_rules_modules"])
    model = policy["game_state_access"]["model_definition"]
    modules = []
    for relative in sorted(analyses):
        layer = _layer(relative, protected_rules_modules)
        allowed_dependencies = list(ALLOWED_DEPENDENCIES[layer])
        if relative in {
            "quorune/commander.py",
            "quorune/commander_pairing.py",
            "quorune/engine.py",
            "quorune/mana.py",
            "quorune/rules_corpus.py",
        } and "adapter" not in allowed_dependencies:
            allowed_dependencies.append("adapter")
            allowed_dependencies.sort()
        access = (
            "mutable_owner"
            if relative in mutable
            else "read_only"
            if relative in readers
            else "model_definition"
            if relative == model
            else "none"
        )
        modules.append(
            {
                "file": relative,
                "layer": layer,
                "owning_subsystem": _owner(relative, layer),
                "allowed_dependency_layers": allowed_dependencies,
                "game_state_access": access,
                "card_specificity_policy": (
                    "explicit_card_override"
                    if relative.startswith("quorune/card_overrides/")
                    else "generic_no_growth"
                ),
                "visibility_sensitivity": (
                    "principal_scoped"
                    if any(
                        marker in relative
                        for marker in (
                            "projection",
                            "pilot",
                            "session",
                            "action_explanations",
                            "server/",
                        )
                    )
                    else "authoritative_internal"
                ),
                "replay_participation": (
                    "authoritative"
                    if any(
                        marker in relative
                        for marker in (
                            "attachment_references.py",
                            "attachments.py",
                            "attack_transition",
                            "block_transition",
                            "spell_copy_engine_adapter.py",
                            "ability_fragment_host.py",
                            "ability_fragments.py",
                            "characteristic_fragments.py",
                            "characteristic_evaluation_host.py",
                            "aura/",
                            "engine.py",
                            "enchant_spec.py",
                            "target_forms.py",
                            "session.py",
                            "semantics.py",
                            "self_cast_reductions.py",
                            "cast_cost_modifiers.py",
                            "cast_lifecycles.py",
                            "compiled_cast_lifecycles.py",
                            "card_programs/",
                            "semantic_runtime/",
                            "semantic_choices/",
                            "effect_runtime/",
                            "card_overrides/",
                            "effect_contracts.py",
                            "counter_placement.py",
                            "counter_placement_sets.py",
                            "counter_maximums.py",
                            "counter_names.py",
                            "counter_removal.py",
                            "counter_state.py",
                            "cumulative_upkeep.py",
                            "entry_counter",
                            "commander.py",
                            "combat_damage_",
                            "combat_relationship_state.py",
                            "damage.py",
                            "damage_modifier_state.py",
                            "damage_prevention",
                            "damage_transaction.py",
                            "damage_results.py",
                            "death_return.py",
                            "day_night.py",
                            "declaration_rule_effects.py",
                            "delayed_triggers.py",
                            "drawing/",
                            "dynamic_characteristics.py",
                            "life_change.py",
                            "life_state.py",
                            "leveler_bands.py",
                            "permanent_transform.py",
                            "mana_activation.py",
                            "mana_mode_effects.py",
                            "mana_payment_continuations.py",
                            "mana_undo.py",
                            "object_predicate.py",
                            "object_query.py",
                            "permanent_exile.py",
                            "replacement/",
                            "return_to_hand.py",
                            "stack_counter.py",
                            "stack_resolution.py",
                            "state_planner.py",
                            "tap_state.py",
                            "token_creation.py",
                            "replacement_decisions.py",
                            "prevention_triggers.py",
                            "protection.py",
                            "compiled_ability_fragments.py",
                            "compiled_activated_abilities.py",
                            "crew.py",
                            "cycling_abilities.py",
                            "fixed_mana_abilities.py",
                            "intrinsic_basic_land_mana.py",
                            "impulse_access_model.py",
                            "mana_ability_runtime.py",
                            "trigger_targeting.py",
                            "untap_step",
                            "spell_history_transform.py",
                        )
                    )
                    or relative in {
                        "quorune/record.py",
                        "quorune/record_trust.py",
                    }
                    else "none"
                ),
            }
        )
    payload = {
        "schema_version": 1,
        "classification_policy": "default_deny_exact_production_python_v1",
        "modules": modules,
    }
    payload["fingerprint"] = _hash(payload)
    return payload


def _text(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = _text(build_classifications())
    if args.write:
        OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
        return 0
    actual = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
    if actual != expected:
        print(
            "platform/module-classifications.json is stale; run "
            "python scripts/update_module_classifications.py --write",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
