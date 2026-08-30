from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .activation_restriction_templates import (
    static_activation_restriction_handler,
)
from .action_permission_templates import static_action_permission_handler
from .casting_activation_metadata_templates import (
    static_loyalty_cost_modifier_handler,
    static_self_zone_cast_permission_handler,
)
from .cast_cost_modifier_templates import (
    static_fixed_spell_cost_reduction_handler,
)
from .combat_metadata_templates import static_goad_prohibition_handler
from .continuous_templates import (
    attached_fixed_characteristics_handler,
    basic_land_type_addition_handler,
    conditional_self_keyword_handler,
    dynamic_self_power_toughness_handler,
    fixed_public_state_characteristics_handler,
    fixed_query_characteristic_grant_handler,
    fixed_query_keyword_grant_handler,
    fixed_power_toughness_anthem_handler,
)
from .query_characteristic_templates import (
    query_power_toughness_definition_handler,
    query_self_characteristics_handler,
)
from .counter_replacement_templates import (
    static_counter_quantity_replacement_handler,
)
from .counter_maximum_templates import static_counter_maximum_handler
from .damage_templates import static_damage_handler
from .draw_templates import (
    static_draw_reveal_handler,
    static_draw_result_handler,
    static_draw_restriction_handler,
)
from .entry_state_templates import static_entry_state_handler
from .fixed_self_entry_counter_templates import (
    fixed_self_entry_counter_handler,
)
from ..entry_state_conditions import FIXED_ENTRY_CONDITION_HANDLER_ID
from .life_templates import static_life_handler
from .token_templates import static_additional_token_replacement_handler
from .trigger_participation_templates import static_trigger_multiplier_handler
from .untap_step_templates import static_untap_step_handler
from .zone_templates import static_zone_destination_replacement_handler


@dataclass(frozen=True, slots=True)
class StaticRuntimeTemplate:
    compiled: tuple[
        str,
        Mapping[str, Any],
        str | tuple[str, ...],
    ]
    kind: str
    event: str
    dependency_reason: str
    active_zone: str = "battlefield"
    runtime_coverage: tuple[str, ...] = ()


def _trigger_multiplier_template(text: str) -> StaticRuntimeTemplate | None:
    compiled = static_trigger_multiplier_handler(text)
    if compiled is None:
        return None
    return StaticRuntimeTemplate(
        compiled=compiled,
        kind="static_ability",
        event="continuous",
        dependency_reason=(
            "generic trigger multiplication depends on an untrusted "
            "rules capability"
        ),
    )


def _counter_maximum_template(
    text: str,
    *,
    source_name: str | None,
    source_is_class: bool,
) -> StaticRuntimeTemplate | None:
    if source_name is None or source_is_class:
        return None
    compiled = static_counter_maximum_handler(
        text,
        source_name=source_name,
    )
    if compiled is None:
        return None
    return StaticRuntimeTemplate(
        compiled=compiled,
        kind="static_ability",
        event="continuous",
        dependency_reason=(
            "fixed self counter maximums depend on an untrusted "
            "state-based-action capability"
        ),
    )


def _draw_template(text: str) -> StaticRuntimeTemplate | None:
    draw_reveal = static_draw_reveal_handler(text)
    if draw_reveal is not None:
        return StaticRuntimeTemplate(
            compiled=draw_reveal,
            kind="static_ability",
            event="draw.reveal_as_drawn",
            dependency_reason=(
                "generic draw reveal depends on an untrusted rules capability"
            ),
        )
    draw_restriction = static_draw_restriction_handler(text)
    if draw_restriction is not None:
        return StaticRuntimeTemplate(
            compiled=draw_restriction,
            kind="static_ability",
            event="draw.permission",
            dependency_reason=(
                "generic draw restriction depends on an untrusted rules "
                "capability"
            ),
        )
    draw_result = static_draw_result_handler(text)
    if draw_result is None:
        return None
    return StaticRuntimeTemplate(
        compiled=draw_result,
        kind="replacement_effect",
        event="draw",
        dependency_reason=(
            "generic result-draw replacement depends on an untrusted rules "
            "capability"
        ),
    )


def _source_permanent_participation_template(
    text: str,
    *,
    source_name: str | None,
) -> StaticRuntimeTemplate | None:
    goad_prohibition = static_goad_prohibition_handler(text)
    if goad_prohibition is not None:
        return StaticRuntimeTemplate(
            compiled=goad_prohibition,
            kind="static_ability",
            event="combat.goad.prohibition",
            dependency_reason=(
                "controller-creature goad prohibition requires its closed "
                "typed runtime capability"
            ),
        )
    self_zone_cast = static_self_zone_cast_permission_handler(text)
    if self_zone_cast is not None:
        return StaticRuntimeTemplate(
            compiled=self_zone_cast,
            kind="static_ability",
            event="cast.zone.permission",
            active_zone="graveyard",
            dependency_reason=(
                "self-zone casting permission requires its closed typed "
                "runtime capability"
            ),
        )
    loyalty_cost_modifier = static_loyalty_cost_modifier_handler(text)
    if loyalty_cost_modifier is not None:
        return StaticRuntimeTemplate(
            compiled=loyalty_cost_modifier,
            kind="static_ability",
            event="activation.cost.modify",
            dependency_reason=(
                "loyalty-cost modification detection requires its closed "
                "typed runtime capability"
            ),
        )
    spell_cost_reduction = static_fixed_spell_cost_reduction_handler(text)
    if spell_cost_reduction is not None:
        return StaticRuntimeTemplate(
            compiled=spell_cost_reduction,
            kind="static_ability",
            event="cast.cost.modify",
            dependency_reason=(
                "fixed spell-cost reductions require their closed typed "
                "runtime capability"
            ),
            runtime_coverage=("static_ability",),
        )
    activation_restriction = static_activation_restriction_handler(text)
    if activation_restriction is not None:
        return StaticRuntimeTemplate(
            compiled=activation_restriction,
            kind="static_ability",
            event="activation.permission",
            dependency_reason=(
                "generic activation restriction requires its closed typed "
                "runtime capability"
            ),
        )
    action_permission = static_action_permission_handler(text)
    if action_permission is not None:
        return StaticRuntimeTemplate(
            compiled=action_permission,
            kind="static_ability",
            event="action.permission",
            dependency_reason=(
                "generic controller action permission requires its closed "
                "typed runtime capability"
            ),
        )
    untap_step = (
        static_untap_step_handler(text, source_name=source_name)
        if source_name is not None
        else None
    )
    if untap_step is not None:
        return StaticRuntimeTemplate(
            compiled=untap_step,
            kind="static_ability",
            event="untap.step",
            dependency_reason=(
                "generic untap-step participation requires its closed "
                "typed runtime capability"
            ),
        )
    entry_state = static_entry_state_handler(
        text,
        source_name=source_name or "",
    )
    if entry_state is None:
        return None
    relation = (
        "affected_object"
        if entry_state[1].get("handler_id")
        == FIXED_ENTRY_CONDITION_HANDLER_ID
        else str(entry_state[1]["source_relation"])
    )
    return StaticRuntimeTemplate(
        compiled=entry_state,
        kind="replacement_effect",
        event="zone.change",
        active_zone="all" if relation == "affected_object" else "battlefield",
        dependency_reason=(
            "generic battlefield-entry state depends on an untrusted "
            "rules capability"
        ),
    )


def _query_definition_static_runtime_template(
    text: str,
    *,
    source_name: str | None,
    source_is_class: bool,
) -> StaticRuntimeTemplate | None:
    if source_name is None or source_is_class:
        return None
    compiled = query_power_toughness_definition_handler(
        text,
        source_name=source_name,
    )
    if compiled is None:
        return None
    return StaticRuntimeTemplate(
        compiled=compiled,
        kind="static_ability",
        event="continuous",
        active_zone="all",
        dependency_reason=(
            "query-derived characteristic definitions require their "
            "cycle-safe all-zone capability"
        ),
    )


def _continuous_static_runtime_template(
    text: str,
    *,
    source_name: str | None,
    source_is_class: bool,
) -> StaticRuntimeTemplate | None:
    """Select one closed continuous-characteristic production."""

    attached_characteristics = attached_fixed_characteristics_handler(text)
    if attached_characteristics is not None:
        return StaticRuntimeTemplate(
            compiled=attached_characteristics,
            kind="static_ability",
            event="characteristics.evaluate",
            dependency_reason=(
                "generic attached characteristics depend on an untrusted "
                "continuous-effect capability"
            ),
        )
    basic_land_type = basic_land_type_addition_handler(text)
    if basic_land_type is not None:
        return StaticRuntimeTemplate(
            compiled=basic_land_type,
            kind="static_ability",
            event="characteristics.evaluate",
            dependency_reason=(
                "generic basic-land-type addition depends on an untrusted "
                "rules capability"
            ),
        )
    characteristic_grant = (
        None
        if source_is_class
        else fixed_query_characteristic_grant_handler(text)
    )
    if characteristic_grant is not None:
        return StaticRuntimeTemplate(
            compiled=characteristic_grant,
            kind="static_ability",
            event="characteristics.evaluate",
            dependency_reason=(
                "generic controlled characteristic grants require their "
                "closed continuous-effect capabilities"
            ),
        )
    keyword_grant = (
        None if source_is_class else fixed_query_keyword_grant_handler(text)
    )
    if keyword_grant is not None:
        return StaticRuntimeTemplate(
            compiled=keyword_grant,
            kind="static_ability",
            event="characteristics.evaluate",
            dependency_reason=(
                "generic controlled keyword grants require their closed "
                "continuous-effect capability"
            ),
        )
    query_definition = _query_definition_static_runtime_template(
        text,
        source_name=source_name,
        source_is_class=source_is_class,
    )
    if query_definition is not None:
        return query_definition
    if source_name is not None:
        query_characteristics = (
            None
            if source_is_class
            else query_self_characteristics_handler(
                text,
                source_name=source_name,
            )
        )
        if query_characteristics is not None:
            return StaticRuntimeTemplate(
                compiled=query_characteristics,
                kind="static_ability",
                event="continuous",
                dependency_reason=(
                    "typed public quantities require their cycle-safe "
                    "characteristic capability"
                ),
            )
        fixed_public_state = (
            None
            if source_is_class
            else fixed_public_state_characteristics_handler(
                text,
                source_name=source_name,
            )
        )
        if fixed_public_state is not None:
            return StaticRuntimeTemplate(
                compiled=fixed_public_state,
                kind="static_ability",
                event="characteristics.evaluate",
                dependency_reason=(
                    "fixed public-state characteristics require their closed "
                    "continuous-effect capability"
                ),
            )
        conditional_keyword = conditional_self_keyword_handler(
            text,
            source_name=source_name,
        )
        if conditional_keyword is not None:
            return StaticRuntimeTemplate(
                compiled=conditional_keyword,
                kind="static_ability",
                event="continuous",
                dependency_reason=(
                    "generic conditional characteristics require their closed "
                    "typed capability"
                ),
            )
        dynamic_power_toughness = dynamic_self_power_toughness_handler(
            text,
            source_name=source_name,
        )
        if dynamic_power_toughness is not None:
            return StaticRuntimeTemplate(
                compiled=dynamic_power_toughness,
                kind="static_ability",
                event="continuous",
                dependency_reason=(
                    "generic count-derived characteristics require their "
                    "closed typed capability"
                ),
            )
    fixed_anthem = fixed_power_toughness_anthem_handler(text)
    if fixed_anthem is None:
        return None
    return StaticRuntimeTemplate(
        compiled=fixed_anthem,
        kind="static_ability",
        event="characteristics.evaluate",
        dependency_reason=(
            "generic fixed anthem depends on an untrusted continuous-effect "
            "capability"
        ),
    )


def static_runtime_template(
    text: str,
    *,
    source_name: str | None = None,
    source_damageable: bool | None = None,
    source_permanent: bool = True,
    source_is_class: bool = False,
) -> StaticRuntimeTemplate | None:
    """Select one closed static runtime production for an Oracle line."""

    if source_permanent:
        if source_name is not None and not source_is_class:
            entry_counter = fixed_self_entry_counter_handler(
                text,
                source_name=source_name,
            )
            if entry_counter is not None:
                return StaticRuntimeTemplate(
                    compiled=entry_counter,
                    kind="replacement_effect",
                    event="zone.change",
                    active_zone="all",
                    dependency_reason=(
                        "generic fixed self-entry counters depend on the "
                        "canonical zone and counter replacement owners"
                    ),
                )
        participation = _source_permanent_participation_template(
            text,
            source_name=source_name,
        )
        if participation is not None:
            return participation
        counter_maximum = _counter_maximum_template(
            text,
            source_name=source_name,
            source_is_class=source_is_class,
        )
        if counter_maximum is not None:
            return counter_maximum
        trigger_multiplier = _trigger_multiplier_template(text)
        if trigger_multiplier is not None:
            return trigger_multiplier
        counter_quantity = (
            None
            if source_is_class
            else static_counter_quantity_replacement_handler(text)
        )
        if counter_quantity is not None:
            return StaticRuntimeTemplate(
                compiled=counter_quantity,
                kind="replacement_effect",
                event="counter.place",
                dependency_reason=(
                    "generic counter-quantity replacement depends on an "
                    "untrusted rules capability"
                ),
            )
        additional_token = static_additional_token_replacement_handler(text)
        if additional_token is not None:
            return StaticRuntimeTemplate(
                compiled=additional_token,
                kind="replacement_effect",
                event="token.create",
                dependency_reason=(
                    "generic additional-token replacement depends on an "
                    "untrusted rules capability"
                ),
            )
        zone_replacement = static_zone_destination_replacement_handler(text)
        if zone_replacement is not None:
            return StaticRuntimeTemplate(
                compiled=zone_replacement,
                kind="replacement_effect",
                event="zone.change",
                dependency_reason=(
                    "generic destination replacement depends on an untrusted "
                    "rules capability"
                ),
            )
        draw = _draw_template(text)
        if draw is not None:
            return draw

    continuous = _continuous_static_runtime_template(
        text,
        source_name=source_name,
        source_is_class=source_is_class,
    )
    if continuous is not None:
        return continuous
    static_life = static_life_handler(text)
    if static_life is not None:
        return StaticRuntimeTemplate(
            compiled=static_life,
            kind="replacement_effect",
            event="life.change",
            dependency_reason=(
                "generic life-gain replacement depends on an untrusted "
                "rules capability"
            ),
        )
    static_damage = static_damage_handler(text)
    if static_damage is None:
        return None
    if (
        (source_is_class or not source_permanent)
        and static_damage[1]["handler_id"]
        == "replacement.damage.quantity.v2"
    ):
        return None
    if (
        static_damage[1]["handler_id"]
        == "replacement.damage.redirect-to-source.v1"
        and source_damageable is False
    ):
        # Damage can be redirected only to an object that can receive damage.
        # Keeping this type check at compilation prevents a future artifact or
        # enchantment with superficially similar wording from being promoted
        # to a trusted program that can only fail at runtime.
        return None
    return StaticRuntimeTemplate(
        compiled=static_damage,
        kind=(
            "prevention_effect"
            if static_damage[1]["handler_id"].startswith("prevention.")
            else "replacement_effect"
        ),
        event="damage",
        dependency_reason=(
            "generic damage replacement depends on an untrusted rules "
            "capability"
        ),
    )
