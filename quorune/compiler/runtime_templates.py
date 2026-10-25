from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Any, Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .activation_restriction_templates import (
    static_activation_restriction_handler,
)
from .activation_cost_modifier_templates import static_activation_cost_modifier_handler
from .targeted_spell_costs import self_target_reduction_template
from .action_permission_templates import static_action_permission_handler,opening_hand_battlefield_permission_handler
from .opening_hand_reveal_templates import opening_hand_reveal_permission_handler
from .entry_designation_templates import entry_designation_handler, chosen_characteristics_handler
from .casting_activation_metadata_templates import (
    static_loyalty_cost_modifier_handler,
    static_self_zone_cast_permission_handler,
)
from .cast_cost_modifier_templates import (
    self_spell_cost_reduction_handler,
    static_fixed_spell_cost_reduction_handler,
)
from .static_cast_rule_templates import static_cast_rule_handler
from .combat_metadata_templates import static_goad_prohibition_handler
from .fixed_characteristic_settings import fixed_characteristic_setting_handler
from .continuous_templates import (
    attached_fixed_characteristics_handler,
    basic_land_type_addition_handler,
    conditional_self_keyword_handler,
    dynamic_self_power_toughness_handler,
    fixed_query_characteristic_grant_handler,
    fixed_query_keyword_grant_handler,
    fixed_power_toughness_anthem_handler,
)
from .otherwise_characteristic_templates import composed_public_state_characteristics_handler
from .declaration_nodes import fixed_static_declaration_grant_handler
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
    dynamic_self_entry_counter_handler,
    fixed_self_entry_counter_handler,
)
from ..entry_state_conditions import FIXED_ENTRY_CONDITION_HANDLER_ID
from .life_templates import static_life_handler
from .token_templates import static_additional_token_replacement_handler
from .trigger_participation_templates import static_trigger_multiplier_handler
from .library_search_rule_templates import fixed_library_search_rule_handler
from .untap_step_templates import static_untap_step_handler
from .zone_templates import static_zone_destination_replacement_handler


@dataclass(frozen=True, slots=True)
class StaticRuntimeTemplate:
    compiled: tuple[
        str,
        Mapping[str, Any] | tuple[Mapping[str, Any], ...],
        str | tuple[str, ...],
    ]
    kind: str
    event: str
    dependency_reason: str
    active_zone: str = "battlefield"
    runtime_coverage: tuple[str, ...] = ()
    mechanics: tuple[str, ...] = ()


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


def _global_alternative_template(text):
    from .global_alternative_cost_templates import fixed_global_alternative_cost_handler
    from .turn_cast_grants import turn_cast_grant_handler
    compiled=turn_cast_grant_handler(text) or fixed_global_alternative_cost_handler(text)
    if compiled is None:return None
    return StaticRuntimeTemplate(compiled=compiled,kind='static_ability',event='cast.cost',
        active_zone='battlefield',runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Battlefield alternative mana costs require a complete current source and existing cast payment owners')


def _source_permanent_participation_template(
    text: str,
    *,
    source_name: str | None,
) -> StaticRuntimeTemplate | None:
    if global_alternative := _global_alternative_template(text):
        return global_alternative
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
    self_zone_cast = static_self_zone_cast_permission_handler(text, source_name=source_name or 'source')
    if self_zone_cast is not None:
        return StaticRuntimeTemplate(
            compiled=self_zone_cast,
            kind="static_ability",
            event="cast.zone.permission",
            active_zone='all' if self_zone_cast[1].get('requires_complete_card_program') is True else 'graveyard',
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,) if self_zone_cast[1].get('requires_complete_card_program') is True else (),
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
    static_cast_rule = static_cast_rule_handler(
        text,
        source_name=source_name or "",
    )
    from .targeted_spell_costs import targeted_spell_cost_template
    spell_cost_reduction = (targeted_spell_cost_template(text,source_name=source_name or 'source')
        or static_fixed_spell_cost_reduction_handler(text, source_name=source_name or 'source'))
    if spell_cost_reduction is not None:
        template_id = spell_cost_reduction[0]
        return StaticRuntimeTemplate(
            compiled=spell_cost_reduction,
            kind="static_ability",
            event="cast.cost.modify",
            dependency_reason=(
                "fixed spell-cost modifiers require their closed typed "
                "runtime capability"
            ),
            runtime_coverage=(
                "static_ability",
                *(
                    (CURRENT_ABILITY_FRAGMENT_COVERAGE,)
                    if template_id in {"public-fixed-spell-cost-modifier-v1", "public-conditional-spell-cost-modifier-v1", "targeted-fixed-spell-cost-v1", "optional-life-colored-reduction-v1"}
                    else ()
                ),
            ),
        )
    if static_cast_rule is not None:
        return StaticRuntimeTemplate(
            compiled=static_cast_rule,
            kind="static_ability",
            event="cast.static.rule",
            dependency_reason=(
                "fixed public casting rules require their typed legality "
                "capability"
            ),
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        )
    activation_rule = (static_activation_restriction_handler(text)
        or static_activation_cost_modifier_handler(text))
    if activation_rule is not None:
        return StaticRuntimeTemplate(
            compiled=activation_rule,
            kind="static_ability",
            event=activation_rule[1]['event'],
            dependency_reason=(
                "generic activation rule requires its closed typed "
                "runtime capability"
            ),
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        )
    action_permission = (opening_hand_reveal_permission_handler(text,source_name=source_name)
        or opening_hand_battlefield_permission_handler(text,source_name=source_name)
        or fixed_library_search_rule_handler(text)
        or static_action_permission_handler(text))
    if action_permission is not None:
        opening_hand=action_permission[0] in {'opening-hand-battlefield-permission-v1','opening-hand-reveal-permission-v1'}
        return StaticRuntimeTemplate(
            compiled=action_permission,
            kind="static_ability",
            event="action.permission",
            dependency_reason=(
                "generic controller action permission requires its closed "
                "typed runtime capability"
            ),
            active_zone='hand' if opening_hand else 'battlefield',
            runtime_coverage=() if opening_hand else (CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        )
    from .untap_selection_templates import fixed_untap_selection_limit_handler
    untap_step = (
        (fixed_untap_selection_limit_handler(text, source_name=source_name)
         or static_untap_step_handler(text, source_name=source_name))
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
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
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
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
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


def _static_declaration_runtime_template(
    text: str,
    *,
    source_name: str | None,
) -> StaticRuntimeTemplate | None:
    declaration_grant = fixed_static_declaration_grant_handler(
        text,
        source_name=source_name or "source",
    )
    if declaration_grant is not None:
        return StaticRuntimeTemplate(
            compiled=declaration_grant,
            kind="static_ability",
            event="characteristics.evaluate",
            dependency_reason=(
                "queried declaration rules require their continuous ability "
                "grant and combat declaration capabilities"
            ),
        )
    return None


def _basic_land_type_runtime_template(text):
    from .basic_type_setting_templates import fixed_basic_type_setting_handler
    from .basic_type_addition_templates import scoped_basic_type_addition_handler
    compiled = fixed_basic_type_setting_handler(text) or scoped_basic_type_addition_handler(text)
    if compiled is not None:
        return StaticRuntimeTemplate(compiled=compiled,kind='static_ability',
            event='characteristics.evaluate',runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
            dependency_reason='Fixed basic type changes require their layer-four, ability and intrinsic-mana owners')
    legacy = basic_land_type_addition_handler(text)
    if legacy is not None:
        return StaticRuntimeTemplate(compiled=legacy,kind='static_ability',
            event='characteristics.evaluate',
            dependency_reason='Basic land type addition requires its existing continuous capability')
    return None


def _continuous_static_runtime_template(
    text: str,
    *,
    source_name: str | None,
    source_is_class: bool,
) -> StaticRuntimeTemplate | None:
    if source_name is not None and not source_is_class:
        from .all_colors_definitions import all_colors_definition_handler
        definition=all_colors_definition_handler(text,source_name=source_name)
        if definition is not None:
            return StaticRuntimeTemplate(compiled=definition,kind='static_ability',event='continuous',active_zone='all',
                runtime_coverage=('layer_5_all_colors_characteristic',),dependency_reason='All-colors source definitions require their all-zone typed color owner')
    from .keyword_prohibition_templates import opponent_keyword_prohibition_handler
    prohibition = None if source_is_class else opponent_keyword_prohibition_handler(text)
    if prohibition is not None:
        return StaticRuntimeTemplate(compiled=prohibition, kind='static_ability', event='characteristics.evaluate',
            dependency_reason='Fixed keyword denial requires its prohibition and keyword gameplay owners',
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,))
    """Select one closed continuous-characteristic production."""

    label = re.fullmatch(r"(?P<label>[A-Za-z][A-Za-z ',-]*[A-Za-z])\s+—\s+(?P<body>.+)", text)
    if label is not None and label['label'].casefold() not in {'solved', 'solve','max speed'} and not source_is_class:
        text = label['body']

    declaration_grant = _static_declaration_runtime_template(
        text,
        source_name=source_name,
    )
    if declaration_grant is not None:
        return declaration_grant

    setting = _constant_static_characteristic_template(text, source_name=source_name, source_is_class=source_is_class)
    if setting is not None:
        return setting
    basic_land_type = _basic_land_type_runtime_template(text)
    if basic_land_type is not None:
        return basic_land_type
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
            else composed_public_state_characteristics_handler(
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
        from .conditional_granted_ability_templates import conditional_typed_keyword_handler
        conditional_keyword = (
            conditional_typed_keyword_handler(text, source_name=source_name)
            or conditional_self_keyword_handler(text, source_name=source_name)
        )
        if conditional_keyword is not None:
            return StaticRuntimeTemplate(
                compiled=conditional_keyword,
                kind="static_ability",
                event=conditional_keyword[1]['event'],
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
    fixed_anthem = fixed_power_toughness_anthem_handler(text, source_name=source_name or "source")
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


def _constant_static_characteristic_template(text, *, source_name, source_is_class):
    from .soulbond_templates import paired_characteristics_handler
    paired = paired_characteristics_handler(text, source_name=source_name or 'source')
    if paired is not None:
        return StaticRuntimeTemplate(compiled=paired, kind='static_ability', event='characteristics.evaluate',
            dependency_reason='Paired grants require trusted pairing and independently closed modifier capabilities')
    from .attached_control_templates import attached_control_handler
    control = attached_control_handler(text)
    if control is not None:
        return StaticRuntimeTemplate(compiled=control, kind="static_ability", event="characteristics.evaluate",
            dependency_reason="Static attached control requires its typed layer-two capability")
    attached = attached_fixed_characteristics_handler(text, source_name=source_name or "source")
    if attached is not None:
        return StaticRuntimeTemplate(compiled=attached, kind="static_ability", event="characteristics.evaluate",
            dependency_reason="Generic attached characteristics require a trusted continuous-effect capability")
    setting = None if source_is_class else fixed_characteristic_setting_handler(text, source_name=source_name)
    if setting is not None:
        return StaticRuntimeTemplate(compiled=setting, kind="static_ability", event="characteristics.evaluate",
            dependency_reason="Fixed public characteristic settings require closed layer and subject capabilities")
    return None


def _self_entry_counter_runtime_template(
    text: str,
    *,
    source_name: str | None,
    source_is_class: bool,
) -> StaticRuntimeTemplate | None:
    if source_name is None or source_is_class:
        return None
    from .fixed_self_entry_counter_templates import fixed_keyword_counter_entry_choice_handler
    from ..keyword_counters import KeywordCounterEntryChoiceSpec
    choice = fixed_keyword_counter_entry_choice_handler(text, source_name=source_name)
    fixed = choice or fixed_self_entry_counter_handler(text, source_name=source_name)
    if fixed is not None:
        return StaticRuntimeTemplate(
            compiled=fixed,
            kind="replacement_effect",
            event="zone.change",
            active_zone="all",
            mechanics=KeywordCounterEntryChoiceSpec.from_descriptor(choice[1]).mechanics if choice else (),
            dependency_reason=(
                "generic fixed self-entry counters depend on the canonical "
                "zone and counter replacement owners"
            ),
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        )
    dynamic = dynamic_self_entry_counter_handler(text, source_name=source_name)
    if dynamic is None:
        return None
    return StaticRuntimeTemplate(
        compiled=dynamic,
        kind="replacement_effect",
        event="zone.change",
        active_zone="all",
        dependency_reason=(
            "dynamic self-entry counters depend on frozen public facts and "
            "the canonical zone and counter replacement owners"
        ),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
    )


def _graveyard_static_template(text: str, *, source_name: str | None, source_is_class: bool):
    from .conditional_granted_ability_templates import graveyard_static_characteristics_handler
    graveyard = None if source_is_class else graveyard_static_characteristics_handler(text, source_name=source_name or 'source')
    return None if graveyard is None else StaticRuntimeTemplate(
        compiled=graveyard, kind='static_ability', event='characteristics.evaluate', active_zone='graveyard',
        dependency_reason='Explicit graveyard grants require source-zone and live query capabilities')


def _chosen_static_template(text: str, *, source_name: str | None, source_is_class: bool):
    chosen = None if source_is_class else chosen_characteristics_handler(text, source_name=source_name or "source")
    return None if chosen is None else StaticRuntimeTemplate(
        compiled=chosen, kind='static_ability', event='characteristics.evaluate',
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Chosen-value characteristics require the retained public entry designation owner')


def _static_damage_runtime_template(
    text: str,
    *,
    source_name: str | None,
    source_damageable: bool | None,
    source_permanent: bool,
    source_is_class: bool,
) -> StaticRuntimeTemplate | None:
    """Assemble closed damage handlers and their current-ability contract."""
    from .counter_prevention_templates import fixed_counter_prevention_handler
    static_damage = (fixed_counter_prevention_handler(text, source_name=source_name or '')
                     if source_permanent and not source_is_class else None)
    static_damage = static_damage or static_damage_handler(text, card_name=source_name)
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
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
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

    self_spell_reduction = self_target_reduction_template(text) or self_spell_cost_reduction_handler(text)
    if self_spell_reduction is not None:
        return StaticRuntimeTemplate(
            compiled=self_spell_reduction,
            kind="static_ability",
            event="cast.cost.modify",
            active_zone="all",
            dependency_reason=(
                "self spell-cost reductions require their closed public-query "
                "capability"
            ),
            runtime_coverage=("self_cast_cost_reduction",),
        )

    if source_permanent:
        graveyard = _graveyard_static_template(text, source_name=source_name, source_is_class=source_is_class)
        if graveyard is not None:
            return graveyard
        designation = None if source_is_class else entry_designation_handler(text, source_name=source_name or "source")
        if designation is not None:
            return StaticRuntimeTemplate(compiled=designation, kind="replacement_effect", event="zone.change", active_zone="all", runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,), dependency_reason="Intrinsic entry designation requires the public replacement-choice owner")
        entry_counter = _self_entry_counter_runtime_template(
            text,
            source_name=source_name,
            source_is_class=source_is_class,
        )
        if entry_counter is not None:
            return entry_counter
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

    chosen = _chosen_static_template(text, source_name=source_name, source_is_class=source_is_class)
    if chosen is not None:
        return chosen
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
    return _static_damage_runtime_template(
        text, source_name=source_name, source_damageable=source_damageable,
        source_permanent=source_permanent, source_is_class=source_is_class,
    )
