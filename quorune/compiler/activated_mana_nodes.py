from __future__ import annotations

"""Closed Oracle-IR lowering for fixed-output activated mana abilities."""

from dataclasses import replace
import re
from typing import Any, Callable, Mapping, Sequence

from ..activation_usage import ActivationLimit
from ..abilities import (
    ActivatedAbility,
    ActivationCondition,
    ActivationConditionKind,
    parse_activated_abilities,
)
from ..color_set_mana_abilities import (
    color_set_mana_handler_descriptor,
    compile_color_set_activated_mana_ability,
)
from ..fixed_mana_abilities import (
    compile_fixed_activated_mana_ability,
    fixed_mana_handler_descriptor,
)
from ..intrinsic_basic_land_mana import (
    INTRINSIC_BASIC_LAND_MANA_CAPABILITY,
    expected_intrinsic_basic_land_mana_reminder,
)
from ..rules.capabilities import CapabilityRegistry
from ..self_zone_move import (
    compile_self_zone_move,
    self_zone_move_handler_descriptor,
)
from .activated_costs import activated_ability_cost
from .activated_zone_change_costs import fixed_activated_zone_change_cost
from .dependency_gate import (
    DependencyGate,
    dependency_gate,
    explicit_capabilities_gate,
)
from .damage_templates import activated_source_damage_effect_template
from .ir_model import (
    append_residual,
    OracleNode,
    OracleResidual,
    SourceSpan,
)
from .modal_templates import FIXED_NONREPEATING_MODAL_MECHANIC
from .regeneration_templates import self_regeneration_effect_template
from .temporary_declaration_templates import (
    activated_temporary_declaration_restriction_effect_template,
)


def fixed_activated_mana_node(
    ability: Any,
    node_id: str,
    line: str,
    span: SourceSpan,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> tuple[Any, OracleNode | None]:
    ability = replace(
        ability,
        ability_id=f"ab{span.line}",
        line_index=span.line - 1,
    )
    reminder_line = line.strip()
    reminder_only = (
        reminder_line.startswith("(") and reminder_line.endswith(")")
    )
    spec = (
        None
        if reminder_only
        else compile_fixed_activated_mana_ability(ability)
    )
    if spec is None:
        return ability, None
    capabilities = ["mana.activated.fixed_output"]
    if ability.activation_limit is ActivationLimit.EXHAUST_ONCE:
        capabilities.append("activation.exhaust.once_per_object")
    gate = explicit_capabilities_gate(
        capabilities,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=(
                    "fixed-output activated mana ability lacks a trusted "
                    "capability closure"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return ability, OracleNode(
        node_id=node_id,
        kind="mana_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="activate",
        lowerable=True,
        exact=not gate.blockers,
        template_id="activated-mana-fixed-output-v1",
        cost=activated_ability_cost(ability),
        handlers=(fixed_mana_handler_descriptor(spec),),
        mechanics=(
            ("exhaust",)
            if ability.activation_limit is ActivationLimit.EXHAUST_ONCE
            else ()
        ),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def color_set_activated_mana_node(
    ability: Any,
    node_id: str,
    line: str,
    span: SourceSpan,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    spec = compile_color_set_activated_mana_ability(ability)
    if spec is None:
        return None
    capabilities = ["mana.activated.color_set"]
    if ability.activation_limit is ActivationLimit.EXHAUST_ONCE:
        capabilities.append("activation.exhaust.once_per_object")
    gate = explicit_capabilities_gate(
        capabilities,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=(
                    "color-set activated mana ability lacks a trusted "
                    "capability closure"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="mana_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="activate",
        lowerable=True,
        exact=not gate.blockers,
        template_id="activated-mana-color-set-v1",
        cost=activated_ability_cost(ability),
        handlers=(color_set_mana_handler_descriptor(spec),),
        mechanics=(
            ("exhaust",)
            if ability.activation_limit is ActivationLimit.EXHAUST_ONCE
            else ()
        ),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def unresolved_activated_mana_residual(
    ability: Any,
    span: SourceSpan,
    residuals: list[OracleResidual],
    *,
    source_line: str | None = None,
) -> str:
    reminder_text = str(source_line or ability.oracle_line).strip()
    reminder_only = (
        reminder_text.startswith("(") and reminder_text.endswith(")")
    )
    return append_residual(
        residuals,
        kind="mana_ability",
        text=ability.effect_text,
        span=span,
        reason=(
            "parenthesized mana reminder text requires the separate intrinsic "
            "basic-land-type ability owner"
            if reminder_only
            else "activated mana ability is outside the typed fixed-output grammar"
        ),
        blockers=(
            *(
                ("intrinsic basic-land-type mana capability",)
                if reminder_only
                else ()
            ),
            "dynamic or conditional mana output",
            "restricted mana or effect-clause side effects",
            "unrepresented activation-cost variant",
        ),
    )


def _activated_effect_residuals(
    *,
    ability: Any,
    template: str | None,
    line: str,
    span: SourceSpan,
    residuals: list[OracleResidual],
) -> list[str]:
    residual_ids: list[str] = []
    if not ability.compiled_cost:
        residual_ids.append(
            append_residual(
                residuals,
                kind="cost",
                text=ability.cost_text,
                span=span,
                reason="mandatory activated cost is not compiled",
                blockers=(
                    "complete alternate/additional-cost grammar",
                    "restricted payment predicates",
                ),
            )
        )
    if template is None and not ability.mana_ability:
        residual_ids.append(
            append_residual(
                residuals,
                kind="effect",
                text=ability.effect_text,
                span=span,
                reason="activated effect has no exact generic template",
            )
        )
    if ability.mana_ability:
        residual_ids.append(
            unresolved_activated_mana_residual(
                ability, span, residuals, source_line=line
            )
        )
    return residual_ids


def _activated_effect_dependency_gate(
    *,
    effects: tuple[Mapping[str, Any], ...],
    target_schema: Mapping[str, Any] | None,
    mechanics: tuple[str, ...],
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
) -> DependencyGate:
    prevention_operations = {
        "choose_damage_source",
        "create_damage_prevention_shield",
        "life",
    }
    capability_shaped_effect = (
        len(effects) == 1
        and str(effects[0].get("op") or "")
        in {
            "amass",
            "become_monarch",
            "bounce",
            "counter_stack_target",
            "create_damage_prevention_shield",
            "create_token",
            "choose_damage_source",
            "choose_cards_apnap",
            "choose_option",
            "damage",
            "damage_fixed_set",
            "damage_each_opponent",
            "drain_each_opponent",
            "drain_opponent",
            "draw",
            "draw_each_player",
            "draw_with_actions",
            "destroy",
            "destroy_all",
            "exile_permanent",
            "exile_public_graveyard_card",
            "explore",
            "grant_keyword_until_end_of_turn",
            "grant_declaration_restriction_until_end_of_turn",
            "offer_draw",
            "proliferate",
            "regenerate",
            "place_counters",
            "place_counter_batch",
            "place_counters_on_set",
            "place_counters_on_targets",
            "place_player_counters",
            "remove_counters",
            "remove_all_counters",
            "return_graveyard_card_to_owner_hand",
            "fixed_self_counter_keyword_action",
            "fixed_bolster",
            "life",
            "lose_life",
            "lose_life_each_opponent",
            "mill",
            "move_public_zone_set",
            "modify_all_matching_permanents_until_end_of_turn",
            "modify_stats_until_end_of_turn",
            "scry",
            "search",
            "surveil",
            "self_zone_move",
            "tap",
            "untap",
        }
    )
    closed_prevention_effect = (
        "cr-615-prevention-effects" in mechanics
        and bool(effects)
        and all(
            str(effect.get("op") or "") in prevention_operations
            for effect in effects
        )
    )
    closed_target_sequence = (
        bool(
            {
                "fixed-target-effect-sequence",
                "fixed-source-effect-sequence",
                "fixed-controller-effect-sequence",
                "fixed-counter-controller-effect-sequence",
                "fixed-effect-clause-sequence",
                "closed-effect-program",
            }.intersection(mechanics)
        )
        or (
            len(effects) >= 1
            and all(
                str(effect.get("op") or "")
                in {
                    "grant_keyword_until_end_of_turn",
                    "modify_stats_until_end_of_turn",
                }
                for effect in effects
            )
            and {
                "cr-115-targets",
                "cr-611-continuous-effects",
            }.issubset(mechanics)
        )
    )
    if (
        capability_shaped_effect
        or closed_prevention_effect
        or closed_target_sequence
        or FIXED_NONREPEATING_MODAL_MECHANIC in mechanics
    ) and capability_registry is not None:
        return dependency_gate(
            mechanics=mechanics,
            effects=effects,
            target_schema=target_schema,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
    return DependencyGate(
        blockers=tuple(
            f"mechanic:{mechanic}"
            for mechanic in sorted(set(mechanics) - trusted_mechanics)
        )
    )


def _activated_cost_dependency_gate(
    ability: Any,
    gate: DependencyGate,
    *,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
) -> DependencyGate:
    """Add closed typed cost ownership without weakening effect blockers."""

    additional: list[str] = []
    if ability.loyalty_delta is not None and ability.loyalty_delta > 0:
        additional.append("activation.loyalty.positive_counter_cost")
    if ability.activation_limit is ActivationLimit.EXHAUST_ONCE:
        additional.append("activation.exhaust.once_per_object")
    if not ability.mana_ability and (
        ability.discard_source
        or ability.sacrifice_source
        or ability.exile_source
    ):
        additional.append("activation.source_zone_change.fixed")
    if not ability.mana_ability and any(
        choice.predicate is not None for choice in ability.choices
    ):
        additional.append("activation.selected_zone_change.fixed")
    if not additional:
        return gate
    cost_gate = explicit_capabilities_gate(
        (*gate.capabilities, *additional),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    return DependencyGate(
        blockers=tuple(sorted(set((*gate.blockers, *cost_gate.blockers)))),
        capabilities=cost_gate.capabilities,
        closure=cost_gate.closure,
    )


def _dependency_metadata(
    gate: DependencyGate,
) -> tuple[tuple[str, ...], str | None, str | None]:
    if gate.closure is None:
        return (), None, None
    return gate.closure.reachable, gate.closure.profile, gate.closure.fingerprint


_ACTIVATION_RESTRICTION_PREFIX = re.compile(
    r"^(?P<effect>.+?)\.?\s+activate only ",
    re.IGNORECASE,
)
_CONTROLS_TYPE_RESTRICTION = re.compile(
    r"if you control "
    r"(?:(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten) "
    r"or more |an? )?(?P<kind>artifacts?|creatures?|lands?)\.?$",
    re.IGNORECASE,
)
_ACTIVATION_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _represented_activation_restriction(
    restriction: str,
) -> tuple[
    bool,
    ActivationLimit | None,
    tuple[ActivationCondition, ...],
] | None:
    normalized = " ".join(restriction.casefold().rstrip(".").split())
    once = " and only once each turn"
    if normalized == "as a sorcery":
        return True, None, ()
    if normalized == f"as a sorcery{once}":
        return True, ActivationLimit.ONCE_PER_TURN, ()
    if normalized == "during your turn":
        return False, None, (
            ActivationCondition(ActivationConditionKind.CONTROLLERS_TURN),
        )
    if normalized == f"during your turn{once}":
        return False, ActivationLimit.ONCE_PER_TURN, (
            ActivationCondition(ActivationConditionKind.CONTROLLERS_TURN),
        )
    if normalized == "once each turn":
        return False, ActivationLimit.ONCE_PER_TURN, ()
    condition_kinds = {
        "if it's not your turn": ActivationConditionKind.NOT_CONTROLLERS_TURN,
        "if you created a token this turn": (
            ActivationConditionKind.TOKEN_CREATED_THIS_TURN
        ),
    }
    if normalized in condition_kinds:
        return False, None, (ActivationCondition(condition_kinds[normalized]),)
    if normalized == (
        "if there are four or more card types among cards in your graveyard"
    ):
        return False, None, (
            ActivationCondition(
                ActivationConditionKind.GRAVEYARD_DISTINCT_TYPES,
                minimum=4,
            ),
        )
    controlled = _CONTROLS_TYPE_RESTRICTION.fullmatch(normalized)
    if controlled is None:
        return None
    raw_count = controlled.group("count") or "one"
    count = (
        int(raw_count)
        if raw_count.isdigit()
        else _ACTIVATION_NUMBER_WORDS[raw_count]
    )
    return False, None, (
        ActivationCondition(
            ActivationConditionKind.CONTROLS_TYPE,
            minimum=count,
            card_type=controlled.group("kind").casefold().removesuffix("s"),
        ),
    )


def _activated_effect_material(ability: ActivatedAbility) -> str:
    """Remove only a complete restriction already present in typed metadata."""

    material = ability.effect_text.strip()
    prefix = _ACTIVATION_RESTRICTION_PREFIX.match(material)
    if prefix is None:
        return material
    represented = _represented_activation_restriction(
        material[prefix.end() :]
    )
    if represented is None:
        return material
    sorcery_speed, activation_limit, activation_conditions = represented
    if (
        ability.sorcery_speed is not sorcery_speed
        or ability.activation_limit is not activation_limit
        or ability.activation_conditions != activation_conditions
    ):
        return material
    return prefix.group("effect").strip()


def _intrinsic_basic_land_mana_reminder_node(
    *,
    node_id: str,
    line: str,
    reminder_line: str,
    type_line: str,
    span: SourceSpan,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode:
    expected_reminder = expected_intrinsic_basic_land_mana_reminder(type_line)
    if reminder_line != expected_reminder:
        residual_id = append_residual(
            residuals,
            kind="mana_ability",
            text=line,
            span=span,
            reason=(
                "parenthesized mana reminder text is nonexecuting and "
                "does not exactly match the intrinsic abilities derived "
                "from the printed land types"
            ),
            blockers=("intrinsic basic-land-type mana capability",),
        )
        return OracleNode(
            node_id=node_id,
            kind="reminder_text",
            text=line,
            span=span,
            active_zone="all",
            event="none",
            lowerable=False,
            exact=False,
            template_id="basic-land-mana-reminder-residual-v1",
            residual_ids=(residual_id,),
        )
    gate = explicit_capabilities_gate(
        (INTRINSIC_BASIC_LAND_MANA_CAPABILITY,),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=(
                    "intrinsic basic-land-type mana reminder lacks "
                    "a trusted capability closure"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    closure, profile, fingerprint = _dependency_metadata(gate)
    return OracleNode(
        node_id=node_id,
        kind="reminder_text",
        text=line,
        span=span,
        active_zone="all",
        event="none",
        lowerable=True,
        exact=not gate.blockers,
        template_id="intrinsic-basic-land-mana-reminder-v1",
        runtime_coverage=("intrinsic_basic_land_mana",),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=closure,
        capability_profile=profile,
        capability_fingerprint=fingerprint,
    )


def activated_oracle_node(
    *,
    node_id: str,
    line: str,
    span: SourceSpan,
    card_name: str,
    type_line: str,
    keywords: Sequence[str],
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    effect_template: Callable[..., tuple[
        str | None,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]],
) -> OracleNode | None:
    """Compile one complete colon-form activated-ability Oracle line."""

    reminder_line = line.strip()
    if (
        reminder_line.casefold().startswith("({t}: add ")
        and reminder_line.endswith(")")
    ):
        return _intrinsic_basic_land_mana_reminder_node(
            node_id=node_id,
            line=line,
            reminder_line=reminder_line,
            type_line=type_line,
            span=span,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=residuals,
        )
    abilities = parse_activated_abilities(
        card_name=card_name,
        oracle_text=line,
        keywords=keywords,
    )
    if not abilities:
        return None
    ability, fixed_mana = fixed_activated_mana_node(
        fixed_activated_zone_change_cost(abilities[0]),
        node_id,
        line,
        span,
        capability_registry,
        capability_profile,
        residuals,
    )
    if fixed_mana is not None:
        return fixed_mana
    color_set_mana = color_set_activated_mana_node(
        ability, node_id, line, span, capability_registry,
        capability_profile, residuals,
    )
    if color_set_mana is not None:
        return color_set_mana
    effect_material = _activated_effect_material(ability)
    self_zone_move = compile_self_zone_move(ability)
    handlers: tuple[Mapping[str, Any], ...] = ()
    regeneration = self_regeneration_effect_template(effect_material)
    activated_damage = activated_source_damage_effect_template(
        effect_material
    )
    declaration_restriction = (
        activated_temporary_declaration_restriction_effect_template(
            effect_material
        )
    )
    if self_zone_move is not None:
        ability = self_zone_move.ability
        template = "activated-self-zone-move-v1"
        effects = (self_zone_move.effect(),)
        target_schema = None
        mechanics = ("self-zone-move",)
        handlers = (self_zone_move_handler_descriptor(self_zone_move),)
    elif declaration_restriction is not None:
        template, effects, target_schema, mechanics = (
            declaration_restriction.compiled()
        )
    elif regeneration is not None:
        template, effects, target_schema, mechanics = regeneration.compiled()
    elif activated_damage is not None:
        template, effects, target_schema, mechanics = activated_damage.compiled()
    else:
        template, effects, target_schema, mechanics = effect_template(
            effect_material,
            card_name=card_name,
        )
    if ability.activation_limit is ActivationLimit.EXHAUST_ONCE:
        mechanics = tuple(dict.fromkeys((*mechanics, "exhaust")))
    residual_ids = _activated_effect_residuals(
        ability=ability,
        template=template,
        line=line,
        span=span,
        residuals=residuals,
    )
    lowerable = not residual_ids and (
        template is not None or ability.mana_ability
    )
    dependencies = mechanics if template is not None else ()
    gate = _activated_effect_dependency_gate(
        effects=effects,
        target_schema=target_schema,
        mechanics=dependencies,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    gate = _activated_cost_dependency_gate(
        ability,
        gate,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    if lowerable and gate.blockers:
        residual_ids.append(
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=(
                    "lowerable ability depends on untrusted mechanic contracts"
                ),
                blockers=gate.blockers,
            )
        )
    closure, profile, fingerprint = _dependency_metadata(gate)
    return OracleNode(
        node_id=node_id,
        kind=(
            "mana_ability" if ability.mana_ability else "activated_ability"
        ),
        text=line,
        span=span,
        active_zone=ability.zones[0],
        event="activate",
        lowerable=lowerable,
        exact=lowerable and not gate.blockers,
        template_id=(
            "intrinsic-mana-ability-v1"
            if ability.mana_ability and template is None
            else template
        ),
        cost=activated_ability_cost(ability),
        effects=effects,
        target_schema=target_schema,
        handlers=handlers,
        mechanics=mechanics,
        residual_ids=tuple(residual_ids),
        capability_dependencies=gate.capabilities,
        capability_closure=closure,
        capability_profile=profile,
        capability_fingerprint=fingerprint,
    )


__all__ = [
    "activated_oracle_node",
    "fixed_activated_mana_node",
    "unresolved_activated_mana_residual",
]
