from __future__ import annotations

import hashlib
from typing import Any, Mapping

from ..semantic_runtime import (
    default_semantic_handler_registry,
    runtime_component_inventory,
    runtime_component_registry_fingerprint,
)
from ..util import stable_json
from .runtime_requirements import program_dynamic_runtime_requirements
from ..semantic_runtime.effect_handlers import effect_descriptor_capability_dependencies
from ..semantic_runtime.activated_abilities import (
    is_structural_activated_ability_catalog_program,
    activated_abilities_from_descriptors,
)


def _hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _semantic_program_requirements(program):
    from ..rules.announced_mana_value_target import (
        DISTINCT_GRAVEYARD_MANA_VALUE_CAPABILITY, announced_mana_value_capability,
        has_announced_mana_value_limit, has_distinct_own_graveyard_mana_value_limit,
        has_energy_reanimation_target, project_announced_target_program, uses_distinct_graveyard_reanimation,
    )
    from ..rules.effect_shape_walkers import nested_effects

    if (
        any(uses_distinct_graveyard_reanimation(effect) for effect in nested_effects((program.effects, program.handlers, program.target_schema)))
        and not has_distinct_own_graveyard_mana_value_limit(program.target_schema or {})
    ):
        return {DISTINCT_GRAVEYARD_MANA_VALUE_CAPABILITY}, {"runtime_distinct_reanimation:missing_announced_target_group"}

    from ..rules.entry_x_graveyard_target import (
        ENTRY_X_GRAVEYARD_REQUIREMENTS, has_entry_x_target_limit, project_entry_x_graveyard_target_program,
    )

    from ..rules.kicked_artifact_entry import (
        KICKED_ARTIFACT_ENTRY_REQUIREMENTS, has_kicked_artifact_entry_limit, project_kicked_artifact_entry_program,
    )

    from ..rules.entry_x_support import (
        ENTRY_X_SUPPORT_REQUIREMENTS, has_entry_x_support, project_entry_x_support_program,
    )

    from ..rules.announced_energy_reanimation import CAPABILITY as ENERGY_REANIMATION_CAPABILITY, has_energy_reanimation_price

    if has_energy_reanimation_price(program.cost_schema) and not has_energy_reanimation_target(program.target_schema):
        return {ENERGY_REANIMATION_CAPABILITY}, {"runtime_announced_energy_reanimation:invalid:missing exact target"}
    if has_kicked_artifact_entry_limit(program.target_schema):
        try:
            projected, capabilities = project_kicked_artifact_entry_program(program)
        except (TypeError, ValueError, KeyError) as exc:
            return set(KICKED_ARTIFACT_ENTRY_REQUIREMENTS), {f"runtime_kicked_artifact_entry:invalid:{exc}"}
        required, blockers = program_dynamic_runtime_requirements(projected)
        return required.union(capabilities), blockers
    if has_entry_x_support(program.target_schema):
        capabilities = ENTRY_X_SUPPORT_REQUIREMENTS
        project = project_entry_x_support_program
        label = "entry_x_support_target"
    elif has_entry_x_target_limit(program.target_schema):
        capabilities = ENTRY_X_GRAVEYARD_REQUIREMENTS
        project = project_entry_x_graveyard_target_program
        label = "entry_x_mana_value_target"
    elif has_announced_mana_value_limit(program.target_schema):
        capabilities = (announced_mana_value_capability(program.target_schema),)
        project = project_announced_target_program
        label = "announced_mana_value_target"
    else:
        return program_dynamic_runtime_requirements(program)
    try:
        projected = project(program)
    except (TypeError, ValueError, KeyError) as exc:
        return set(capabilities), {f"runtime_{label}:invalid:{exc}"}
    required, blockers = program_dynamic_runtime_requirements(projected)
    required.update(capabilities)
    return required, blockers


def _add_program_specific_dependencies(program, required, blockers):
    from ..activation_x import source_discard_activation_x_program_dependencies
    try:required.update(source_discard_activation_x_program_dependencies(program))
    except (TypeError,ValueError) as exc:blockers.add(f'runtime_hand_activation_x:invalid:{exc}')
    from ..colored_hand_reveal_model import variable_colored_reveal_program_dependencies
    try:
        required.update(variable_colored_reveal_program_dependencies(program))
    except (TypeError, ValueError) as exc:
        blockers.add(f"runtime_variable_colored_reveal:invalid:{exc}")
    from ..rules.cast_x_zone_searches import cast_x_zone_search_program_dependencies
    try:
        required.update(cast_x_zone_search_program_dependencies(program))
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_cast_x_zone_search:invalid:{exc}")
    if getattr(program, "trigger_limit", None) is not None:
        from ..rules.trigger_limits import validate_program_trigger_limit,trigger_limit_capabilities
        try:
            required.update(trigger_limit_capabilities(program.trigger_limit))
            validate_program_trigger_limit(program)
        except (TypeError, ValueError) as exc:
            blockers.add(f"runtime_trigger_limit:invalid:{exc}")


def bind_semantic_program_runtime(
    program: Any,
    *,
    capability_registry: Any,
    profile: str,
) -> dict[str, Any]:
    """Bind one ability to exact registered handlers and capability closure."""

    semantic_handlers = default_semantic_handler_registry()
    component_handlers = {
        row["handler_id"]: row for row in runtime_component_inventory()
    }
    semantic_identities: list[dict[str, Any]] = []
    component_identities: list[dict[str, Any]] = []
    required, blockers = _semantic_program_requirements(program)
    _add_program_specific_dependencies(program, required, blockers)
    unregistered_operations: set[str] = set()
    for effect in program.effects:
        operation = str(effect.get("op") or "")
        if operation == 'inspect_target_library':
            from ..rules.target_library_inspection import target_library_inspection_instruction_capabilities
            try:required.update(target_library_inspection_instruction_capabilities(effect))
            except (ValueError, TypeError, KeyError) as exc:blockers.add(f'runtime_target_library_inspection:invalid:{exc}')
        if operation == 'search_named_library_or_graveyard' or operation == 'search' and effect.get('schema_version') == 6:
            from ..rules.named_searches import named_zone_search_instruction_capabilities
            try:
                required.update(named_zone_search_instruction_capabilities(effect))
            except (ValueError, TypeError, KeyError) as exc:
                blockers.add(f'runtime_named_zone_search:invalid:{exc}')
        if operation == 'linked_exile_return' and effect.get('spec',{}).get('timing') == 'source_departure':
            from ..linked_exile_return_model import CONDITIONAL_EXILE_CAPABILITY
            required.add(CONDITIONAL_EXILE_CAPABILITY)
        descriptor = semantic_handlers.describe(operation)
        if operation == "fixed_self_counter_keyword_action" and effect.get("action") in {"fading", "vanishing_upkeep", "vanishing_sacrifice"}:
            from ..rules.node_capability_shapes import fixed_self_counter_keyword_action_node_capabilities
            variant = fixed_self_counter_keyword_action_node_capabilities(
                effects=(effect,), target_schema=program.target_schema,
                mechanic_ids=program.coverage,
            )
            if not variant:
                blockers.add("runtime_effect:invalid_fading_action" if effect.get("action") == "fading" else "runtime_effect:invalid_vanishing_action")
            required.update(variant)
        if descriptor is None:
            unregistered_operations.add(operation)
            continue
        semantic_identities.append(descriptor)
        required.update(effect_descriptor_capability_dependencies(effect, descriptor))
    for descriptor in program.handlers:
        handler_id = str(descriptor.get("handler_id") or "")
        registered = component_handlers.get(handler_id)
        if registered is None:
            # SemanticProgram construction already rejects this. Retain the
            # fail-closed blocker for callers binding untrusted objects.
            blockers.add(f"runtime_handler:unregistered:{handler_id}")
            continue
        component_identities.append(registered)
        from ..rules.keyword_runtime_requirements import registered_component_capabilities
        try:required.update(registered_component_capabilities(descriptor,registered))
        except (ValueError,TypeError) as exc:blockers.add(f'runtime_handler:invalid:{handler_id}:{exc}')
        if handler_id == 'activation.catalog.pinned.v1' and not is_structural_activated_ability_catalog_program(program) and descriptor.get('ability',{}).get('power_up') is not None:
            from ..power_up_model import POWER_UP_CAPABILITY
            required.add(POWER_UP_CAPABILITY)
        if handler_id == 'activation.catalog.pinned.v1' and not is_structural_activated_ability_catalog_program(program) and descriptor.get('ability',{}).get('announced_x_symbols') is not None:
            from ..activation_x import CONTROLLED_LAND_RETURN_X_COST, activation_x_capabilities
            from ..rules.cycling_activation_x import variable_cycling_payment_program_dependencies

            ability = descriptor["ability"]
            try:
                scoped = variable_cycling_payment_program_dependencies(program)
            except (ValueError, TypeError, KeyError) as exc:
                blockers.add(f"runtime_variable_cycling:invalid:{exc}")
                scoped = ()
            required.update(scoped or activation_x_capabilities(
                symbols=ability["announced_x_symbols"],
                sacrifice_source=ability.get("sacrifice_source", False),
                discard_cards=bool(ability.get("choices")) and ability.get("cost_text") != CONTROLLED_LAND_RETURN_X_COST,
                discard_source=ability.get("discard_source", False),
            ))
        if descriptor.get("schema_version") != registered["schema_version"]:
            blockers.add(f"runtime_handler:schema_mismatch:{handler_id}")
        if descriptor.get("event") != registered["event"]:
            blockers.add(f"runtime_handler:event_mismatch:{handler_id}")
    declared = set(program.capability_dependencies)
    for dependency in sorted(required - declared):
        blockers.add(f"capability:undeclared_runtime_dependency:{dependency}")
    structural_catalog = is_structural_activated_ability_catalog_program(program)
    if structural_catalog:
        try:
            activated_abilities_from_descriptors(program.handlers)
        except (TypeError, ValueError) as exc:
            blockers.add(f"runtime_handler:invalid_catalog:{exc}")
    closure = program.capability_closure
    current_closure: Mapping[str, Any] | None = None
    if declared:
        current_closure = capability_registry.closure(
            declared, profile=profile
        ).to_dict()
        if closure != current_closure:
            blockers.add("capability:closure_binding_mismatch")
        if current_closure.get("trusted") is not True:
            blockers.add("capability:closure_untrusted")
    elif closure is not None:
        blockers.add("capability:unexpected_closure")
    compatibility = (
        closure is None
        and not declared
        and program.trust_level in {"trusted", "intentionally_ignored"}
        and program.provenance.get("review_status") == "reviewed"
        and bool(program.tests)
    )
    if required and compatibility:
        blockers.add("capability:legacy_runtime_dependencies_unbound")
    result = {
        "ability_id": program.ability_id,
        "semantic_key": program.key,
        "profile": profile,
        "capability_registry_fingerprint": capability_registry.fingerprint,
        "capability_evidence_fingerprint": (
            capability_registry.evidence_fingerprint
        ),
        "semantic_handler_registry_fingerprint": semantic_handlers.fingerprint,
        "runtime_component_registry_fingerprint": (
            runtime_component_registry_fingerprint()
        ),
        "declared_capabilities": sorted(declared),
        "required_registered_capabilities": sorted(required),
        "semantic_handlers": sorted(
            semantic_identities, key=lambda row: row["handler_id"]
        ),
        "runtime_components": sorted(
            component_identities, key=lambda row: row["handler_id"]
        ),
        "unregistered_legacy_operations": sorted(
            value for value in unregistered_operations if value
        ),
        "capability_closure": (
            dict(current_closure) if current_closure is not None else None
        ),
        "compatibility_path": compatibility,
        "blockers": sorted(blockers),
        "strict": not blockers and (closure is not None or structural_catalog),
    }
    result["fingerprint"] = _hash(result)
    return result


def bind_card_program_runtime(
    card_program: Any,
    *,
    capability_registry: Any,
    profile: str,
) -> dict[str, Any]:
    from ..rules.temporary_ability_grant import (
        validate_life_loss_self_death_grant_child, validate_temporary_combat_draw_unblockable_child,
    )
    from ..rules.equipped_planeswalker import validate_equipped_planeswalker_child
    from ..rules.granted_death_treasure import validate_granted_death_treasure_child
    from ..rules.grantor_damage_redirection import validate_grantor_redirect_grant_child
    from ..rules.target_graveyard_flashback import validate_attached_flashback_trigger

    ability_bindings = [
        bind_semantic_program_runtime(
            ability,
            capability_registry=capability_registry,
            profile=profile,
        )
        for ability in card_program.abilities
    ]
    blockers = {
        f"ability:{binding['ability_id']}:{blocker}"
        for binding in ability_bindings
        for blocker in binding["blockers"]
    }
    from ..rules.nonhuman_undying_anthem import nonhuman_undying_anthem_linkage_blockers
    blockers.update(nonhuman_undying_anthem_linkage_blockers(card_program.abilities))
    abilities_by_key = {ability.key: ability for ability in card_program.abilities}
    for ability in card_program.abilities:
        try:
            validate_attached_flashback_trigger(ability, abilities_by_key.get)
        except (TypeError, ValueError, KeyError) as exc:
            blockers.add(f"ability:{ability.ability_id}:runtime_attached_flashback_trigger:invalid:{exc}")
        try:
            validate_grantor_redirect_grant_child(ability, abilities_by_key.get)
        except (TypeError, ValueError, KeyError) as exc:
            blockers.add(f"ability:{ability.ability_id}:runtime_grantor_redirect_child:invalid:{exc}")
        try:
            validate_temporary_combat_draw_unblockable_child(ability, abilities_by_key.get)
        except (TypeError, ValueError, KeyError) as exc:
            blockers.add(f"ability:{ability.ability_id}:runtime_temporary_combat_draw_child:invalid:{exc}")
        try:
            validate_life_loss_self_death_grant_child(ability, abilities_by_key.get)
        except (TypeError, ValueError, KeyError) as exc:
            blockers.add(f"ability:{ability.ability_id}:runtime_temporary_self_death_child:invalid:{exc}")
        try:
            validate_granted_death_treasure_child(ability, abilities_by_key.get)
        except (TypeError, ValueError, KeyError) as exc:
            blockers.add(f"ability:{ability.ability_id}:runtime_granted_death_treasure_child:invalid:{exc}")
        try:
            validate_equipped_planeswalker_child(ability, abilities_by_key.get)
        except (TypeError, ValueError, KeyError) as exc:
            blockers.add(f"ability:{ability.ability_id}:runtime_equipped_planeswalker_child:invalid:{exc}")
    from ..rules.linked_color_cast_exile import validate_linked_color_cast_card_group
    from ..rules.hideaway_play import validate_hideaway_card_pair

    try:
        validate_hideaway_card_pair(card_program.abilities)
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_hideaway_card_pair:invalid:{exc}")

    try:
        validate_linked_color_cast_card_group(card_program.abilities)
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_linked_color_cast_group:invalid:{exc}")
    from ..rules.linked_hand_mana import validate_linked_hand_mana_group

    try:
        validate_linked_hand_mana_group(card_program.abilities)
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_linked_hand_mana_group:invalid:{exc}")
    from ..rules.linked_graveyard_mana import validate_linked_graveyard_mana_group

    try:
        validate_linked_graveyard_mana_group(card_program.abilities)
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_linked_graveyard_mana_group:invalid:{exc}")
    from ..rules.behold_additional_cost import validate_linked_behold_card_group

    try:
        validate_linked_behold_card_group(card_program.abilities)
    except (TypeError, ValueError, KeyError) as exc:
        blockers.add(f"runtime_linked_behold_group:invalid:{exc}")
    basis = str(card_program.trust_closure.get("trust_basis") or "unresolved")
    if basis in {"provisional", "unresolved"}:
        blockers.add(f"trust_basis:{basis}")
    compatibility = card_program.trust_closure.get(
        "compatibility_provenance", []
    )
    if basis in {"legacy_reviewed", "mixed"} and not compatibility:
        blockers.add("compatibility_provenance:missing")
    result = {
        "card_program_fingerprint": card_program.fingerprint,
        "oracle_id": card_program.oracle_id,
        "profile": profile,
        "trust_basis": basis,
        "ability_bindings": ability_bindings,
        "compatibility_provenance": compatibility,
        "blockers": sorted(blockers),
        "strict_capability_ready": (
            not blockers
            and basis == "capability_closed"
            and all(binding["strict"] for binding in ability_bindings)
        ),
        "compatible_ready": (
            basis
            in {
                "capability_closed",
                "legacy_reviewed",
                "mixed",
                "non_rules_governed",
            }
            and not any(
                blocker.startswith("trust_basis:")
                or blocker.startswith("runtime_linked_color_cast_group:")
                or blocker.startswith("runtime_linked_hand_mana_group:")
                or blocker.startswith("runtime_linked_graveyard_mana_group:")
                or blocker == "compatibility_provenance:missing"
                for blocker in blockers
            )
        ),
    }
    result["fingerprint"] = _hash(result)
    return result


def semantic_program_execution_provenance(
    program: Any,
    card_program: Any | None,
    *,
    capability_registry: Any,
    profile: str,
) -> dict[str, Any]:
    """Return the compact, replay-verifiable runtime binding for one ability."""

    binding = bind_semantic_program_runtime(
        program,
        capability_registry=capability_registry,
        profile=profile,
    )
    closure = binding.get("capability_closure")
    return {
        "card_program_fingerprint": (
            card_program.fingerprint if card_program is not None else None
        ),
        "card_program_trust_basis": (
            card_program.trust_closure["trust_basis"]
            if card_program is not None
            else None
        ),
        "card_program_trust_closure_fingerprint": (
            card_program.trust_closure["fingerprint"]
            if card_program is not None
            else None
        ),
        "legacy_compatibility": (
            card_program is not None
            and card_program.trust_closure["trust_basis"]
            in {"legacy_reviewed", "mixed"}
        ),
        "runtime_binding_fingerprint": binding["fingerprint"],
        "semantic_handler_ids": sorted(
            row["handler_id"] for row in binding["semantic_handlers"]
        ),
        "runtime_component_ids": sorted(
            row["handler_id"] for row in binding["runtime_components"]
        ),
        "runtime_capability_closure_fingerprint": (
            closure.get("fingerprint")
            if isinstance(closure, Mapping)
            else None
        ),
    }
