"""Use paid creature facts only in already-owned nonmana result slots."""
from collections.abc import Mapping
from dataclasses import replace

from ..activation_condition_model import PublicActivationTiming
from ..activation_paid_characteristic_model import (
    ACTIVATION_PAID_STAT_MECHANIC,
)
from ..rules.activation.paid_characteristic_price import (
    activation_creature_sacrifice_choice, source_sacrifice_power_price_is_closed,
)
from ..rules.activation.paid_characteristic_results import (
    SOURCE_SACRIFICE_POWER_LIFE_MECHANIC, SOURCE_SACRIFICE_POWER_LIFE_TEMPLATE,
    source_sacrifice_power_life_capabilities, source_sacrifice_power_life_effect,
)
from ..scalar_effect_amount_model import (
    PAID_SACRIFICE_STAT_MECHANIC, ScalarAmountOrigin, ScalarEffectAmountSpec, SCALAR_AMOUNT_KIND, SCALAR_AMOUNT_MECHANIC,
)
from . import regex_working_set as re
from .activated_costs import activated_ability_cost
from .dependency_gate import DependencyGate, explicit_capabilities_gate
from ..rules.commander_return_choice import (
    COMMANDER_RETURN_CAPABILITY, COMMANDER_RETURN_FIELD, COMMANDER_RETURN_MECHANIC,
    commander_return_effect, commander_return_node_capabilities, commander_return_price_is_closed,
)
from .paid_sacrifice_amount_templates import _paid_sacrifice_stat_template
from .loot_transform_graveyard_templates import loot_transform_and_graveyard_cast_gate, loot_transform_or_graveyard_cast
from .void_counter_play_templates import void_counter_play_activation_gate, void_counter_play_activation_result
from .controller_top_exile_activation import controller_top_exile_activation, controller_top_exile_activation_gate
from .loyalty_spell_results import fixed_loyalty_spell_result, fixed_loyalty_spell_result_gate


def compile_activation_result(ability, *, source_name, text, compile_fixed, public_timing=None):
    """Choose an owned activation quantity before the ordinary fixed result."""
    from .source_mutation_damage import source_mutation_damage_activation

    if mutation_damage := source_mutation_damage_activation(ability, text, public_timing=public_timing):
        return mutation_damage

    if loyalty := fixed_loyalty_spell_result(ability, source_name=source_name, text=text, public_timing=public_timing):
        return loyalty
    from ..rules.chosen_color_draw_price import chosen_color_draw_text, draw_effect
    from ..rules.commander_lifelink import commander_lifelink_activation_matches, commander_lifelink_instruction
    from ..rules.temporary_ability_grant import TEMPORARY_QUOTED_GRANT_MECHANIC
    from ..paid_activation_card_types import (
        DEPENDENCY_MECHANICS, MECHANIC, RESULT_TEXT, TEMPLATE, exact_paid_type_price, paid_type_instruction,
    )
    from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC

    if text.strip() == RESULT_TEXT and exact_paid_type_price(activated_ability_cost(ability, public_timing=public_timing)):
        return TEMPLATE, (paid_type_instruction(),), None, (
            MECHANIC, RESOLUTION_CONDITION_MECHANIC, *DEPENDENCY_MECHANICS,
        )

    from .loyalty_damage_templates import loyalty_damage_activation_template

    if damage := loyalty_damage_activation_template(ability, text, source_name=source_name, public_timing=public_timing):
        return damage

    if public_timing is None and ability.zones == ("battlefield",) and not ability.mana_ability:
        from ..rules.next_personal_turn_protection import (
            ACTIVATED_PROTECTION_MECHANIC, ACTIVATED_PROTECTION_TEMPLATE, activated_protection_effect,
        )

        if text.strip().casefold() == "you gain protection from everything until your next turn.":
            return ACTIVATED_PROTECTION_TEMPLATE, (activated_protection_effect(),), None, (ACTIVATED_PROTECTION_MECHANIC,)
    if "artifact's mana value" in text.casefold().replace("’", "'"):
        from ..rules.paid_artifact_library_search import paid_artifact_library_search_template

        if paid_search := paid_artifact_library_search_template(
            ability, text, public_timing=public_timing, cost=activated_ability_cost(ability, public_timing=public_timing),
        ):
            return paid_search
    if commander_lifelink_activation_matches(ability, text):
        effect, schema = commander_lifelink_instruction()
        return ("paid-commander-lifelink-v1", (effect,), schema,
                (TEMPORARY_QUOTED_GRANT_MECHANIC, "lifelink", "cr-115-targets", "cr-611-continuous-effects"))
    from .impulse_access_templates import shuffled_top_free_play_activation
    from .opponent_library_exile_templates import draw_then_opponent_top_exile_template

    if shuffled_play := shuffled_top_free_play_activation(ability, text, public_timing=public_timing):
        return shuffled_play

    from ..rules.noted_mana import is_noted_mana_charge_ability, noted_mana_charge_effect

    if is_noted_mana_charge_ability(ability):
        return "noted-mana-charge-v1", (noted_mana_charge_effect(),), None, ("cr-122-counters",)
    if ability.zones == ("battlefield",) and not ability.mana_ability:
        if ordered_exile := draw_then_opponent_top_exile_template(text):
            return ordered_exile
    if chosen_color_draw_text(ability.cost_text, text):
        return "chosen-color-paid-draw-v1", (draw_effect(),), None, ("cr-121-drawing-a-card",)
    if void_play := void_counter_play_activation_result(ability, text, public_timing=public_timing):
        return void_play
    if commander_return := commander_return_activation_result(ability, text, public_timing=public_timing):
        return commander_return
    if source_top := (draw_source_owner_top_activation(ability, text, public_timing=public_timing)
                      or loot_transform_or_graveyard_cast(ability, source_name=source_name, text=text, public_timing=public_timing)):
        return source_top
    from .counter_movement_templates import fixed_counter_movement_activation_result
    from .proliferate_templates import controlled_arrival_proliferate_activation_result
    from .counter_resolution_effect_templates import chosen_counter_placement_activation_result
    from .controller_library_top_delayed_hand_templates import controller_library_top_delayed_hand_activation
    from .delayed_object_templates import target_death_return_activation_template
    from .opponent_top_exile_templates import opponent_top_exile_activation_result

    if death_return := target_death_return_activation_template(ability, text):
        return death_return
    if placement := chosen_counter_placement_activation_result(ability, text):
        return placement

    if opponent_top := opponent_top_exile_activation_result(ability, text):
        return opponent_top
    if artifact_return := artifact_sacrifice_return_activation(ability, text):
        return artifact_return
    if top_exile := controller_top_exile_activation(ability, text, public_timing=public_timing):
        return top_exile
    if private_top := controller_library_top_delayed_hand_activation(ability, text, public_timing=public_timing):
        return private_top
    if movement := fixed_counter_movement_activation_result(ability, text):
        return movement
    if text.casefold().startswith("monstrosity x,"):
        from .self_counter_keyword_actions import controlled_counter_monstrosity_activation

        if monstrosity := controlled_counter_monstrosity_activation(ability, text, public_timing=public_timing):
            return monstrosity
    if proliferate := controlled_arrival_proliferate_activation_result(ability, text, public_timing=public_timing):
        return proliferate
    if (public_timing is PublicActivationTiming.ANY_UPKEEP
            and text.casefold().endswith(" counter from this artifact.")):
        from .counter_removal_templates import fixed_source_counter_removal_effect_template
        result = fixed_source_counter_removal_effect_template(text, card_name=source_name)
        if result is not None:
            return result
    random_target = random_target_activation_result(ability, source_name=source_name, text=text, public_timing=public_timing)
    if random_target is not None:
        return random_target
    from .activation_x_costs import announced_x_activation_effect
    from .temporary_keyword_removal_templates import chosen_temporary_keyword_removal_template
    if ("third time this ability has resolved this turn" in text.casefold()
            and not ability.mana_ability and ability.zones == ("battlefield",)
            and not ability.sorcery_speed and not ability.activation_conditions):
        from .resolution_condition_templates import third_ability_result_template
        if ordinal := third_ability_result_template(
            text, compile_component=compile_fixed,
            cost_schema=activated_ability_cost(ability, public_timing=public_timing),
        ):
            return ordinal
    from .graveyard_exile_copy import graveyard_exile_self_copy_template
    from .persistent_public_copy import controlled_artifact_copy_activation_template
    from .temporary_self_copy_templates import (
        announced_artifact_self_copy_template, announced_graveyard_self_copy_template, linked_exile_self_copy_template,
        temporary_graveyard_self_copy_template,
    )

    return (announced_artifact_self_copy_template(ability, source_name=source_name, text=text, public_timing=public_timing)
        or announced_graveyard_self_copy_template(ability, source_name=source_name, text=text, public_timing=public_timing)
        or temporary_graveyard_self_copy_template(ability, text=text, public_timing=public_timing)
        or graveyard_exile_self_copy_template(ability, source_name=source_name, text=text, public_timing=public_timing)
        or linked_exile_self_copy_template(ability, source_name=source_name, text=text, public_timing=public_timing)
        or controlled_artifact_copy_activation_template(ability, text, public_timing=public_timing)
        or chosen_temporary_keyword_removal_template(ability, text=text, source_name=source_name)
        or source_sacrifice_power_life_result(ability, text, public_timing=public_timing)
        or activation_paid_sacrifice_result(ability, source_name=source_name,
                text=text, compile_fixed=compile_fixed)
        or announced_x_activation_effect(ability, source_name=source_name,
                text=text, compile_fixed=compile_fixed)
        or compile_fixed(text))


def commander_return_activation_result(ability, text, *, public_timing):
    from ..rules.owned_commander_battlefield_return import own_commander_return_activation_result

    if result := own_commander_return_activation_result(ability, text, public_timing=public_timing):
        return result
    if (text.strip().casefold() != "put your commander into your hand from the command zone."
            or ability.zones != ("battlefield",) or ability.mana_ability
            or not commander_return_price_is_closed(activated_ability_cost(ability, public_timing=public_timing))):
        return None
    return ("own-commander-command-return-v1", (commander_return_effect(),), None, (COMMANDER_RETURN_MECHANIC,))


def activation_result_dependency_gate(**scope):
    """Resolve explicit activation result gates that inspect the original price."""
    from .delayed_object_templates import target_death_return_activation_gate
    from .finite_damage_redirection_nodes import finite_damage_redirection_gate
    from .impulse_access_templates import shuffled_top_free_play_activation_gate
    from .opponent_library_exile_templates import draw_then_opponent_exile_activation_gate
    from .proliferate_templates import controlled_arrival_proliferate_activation_gate

    from ..rules.next_personal_turn_protection import activated_protection_node_capabilities
    from ..rules.loyalty_damage import loyalty_damage_capabilities

    if loyalty_damage := loyalty_damage_capabilities(**{
        key: scope[key] for key in ("effects", "target_schema", "mechanics", "active_zone", "cost_schema")
    }):
        return explicit_capabilities_gate(
            loyalty_damage, capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
        )

    required = activated_protection_node_capabilities(
        effects=scope["effects"], target_schema=scope["target_schema"], mechanics=scope["mechanics"],
        active_zone=scope["active_zone"], cost_schema=scope["cost_schema"],
    )
    if required:
        return explicit_capabilities_gate(
            required, capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
        )
    from ..rules.paid_artifact_library_search import paid_artifact_search_node_capabilities

    required = paid_artifact_search_node_capabilities(
        effects=scope["effects"], target_schema=scope["target_schema"], mechanics=scope["mechanics"],
        active_zone=scope["active_zone"], cost_schema=scope["cost_schema"],
    )
    if required:
        return explicit_capabilities_gate(
            required, capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
        )
    return (fixed_loyalty_spell_result_gate(**scope) or paid_type_activation_gate(**scope) or commander_lifelink_gate(**scope) or noted_mana_charge_gate(**scope)
            or loot_transform_and_graveyard_cast_gate(**scope)
            or controller_top_exile_activation_gate(**scope)
            or draw_then_opponent_exile_activation_gate(**scope)
            or shuffled_top_free_play_activation_gate(**scope)
            or draw_source_owner_top_activation_gate(**scope) or linked_loyalty_activation_gate(**scope)
            or finite_damage_redirection_gate(**scope) or either_commander_return_gate(**scope)
            or source_sacrifice_power_life_gate(**scope)
            or target_death_return_activation_gate(**scope) or controlled_arrival_proliferate_activation_gate(**scope)
            or void_counter_play_activation_gate(**scope) or artifact_sacrifice_return_activation_gate(**scope))


def paid_type_activation_gate(*, effects, target_schema, mechanics, active_zone, cost_schema,
                              capability_registry, capability_profile):
    from ..paid_activation_card_types import CAPABILITY, MECHANIC, paid_type_capabilities, uses_paid_type_condition

    if MECHANIC not in mechanics and not any(uses_paid_type_condition(effect) for effect in effects):
        return None
    required = paid_type_capabilities(effects=effects, target_schema=target_schema,
                                     mechanic_ids=mechanics, cost_schema=cost_schema)
    if not required or active_zone != "battlefield":
        return DependencyGate(blockers=(f"capability:{CAPABILITY}",))
    return explicit_capabilities_gate(
        required, capability_registry=capability_registry, capability_profile=capability_profile,
    )

def either_commander_return_gate(**scope):
    from ..rules.owned_commander_battlefield_return import own_commander_return_node_capabilities

    required = own_commander_return_node_capabilities(
        effects=scope["effects"], active_zone=scope["active_zone"], target_schema=scope["target_schema"],
        mechanics=scope["mechanics"], cost_schema=scope["cost_schema"],
    )
    if required:
        return explicit_capabilities_gate(
            required, capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
        )
    return commander_return_activation_gate(**scope)


def commander_lifelink_gate(**scope):
    from ..rules.commander_lifelink import commander_lifelink_node_capabilities

    effects = scope["effects"]
    if len(effects) != 1 or scope["active_zone"] != "battlefield":
        return None
    required = commander_lifelink_node_capabilities(
        effects[0], scope["target_schema"], scope["mechanics"], scope["cost_schema"],
    )
    if not required:
        return None
    return explicit_capabilities_gate(
        required, capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
    )


def noted_mana_charge_gate(**scope):
    from ..rules.noted_mana import NOTED_MANA_CAPABILITY, noted_mana_charge_effect, noted_mana_cost_is_closed

    if (tuple(scope["effects"]) != (noted_mana_charge_effect(),)
            or scope["active_zone"] != "battlefield" or scope["target_schema"] is not None
            or not noted_mana_cost_is_closed(scope["cost_schema"], charge=True)):
        return None
    return explicit_capabilities_gate(
        (NOTED_MANA_CAPABILITY, "counter.producer.fixed_effect"),
        capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
    )


def artifact_sacrifice_return_activation(ability, text):
    from ..rules.artifact_sacrifice_return import (
        ARTIFACT_SACRIFICE_RETURN_MECHANIC, artifact_sacrifice_return_payload, artifact_sacrifice_return_targets,
    )

    printed = " ".join(text.split()).casefold().replace("’", "'")
    expected = ("choose target artifact a player controls and target artifact card in that player's graveyard. "
                "if both targets are still legal as this ability resolves, that player simultaneously sacrifices "
                "the artifact and returns the artifact card to the battlefield.")
    if printed != expected or ability.zones != ("battlefield",) or ability.mana_ability or not ability.tap_source:
        return None
    return ("simultaneous-artifact-sacrifice-return-v1", (artifact_sacrifice_return_payload(),),
            artifact_sacrifice_return_targets(), (ARTIFACT_SACRIFICE_RETURN_MECHANIC, "cr-115-targets"))


def artifact_sacrifice_return_activation_gate(**scope):
    from ..rules.artifact_sacrifice_return import (
        ARTIFACT_SACRIFICE_RETURN_CAPABILITY, ARTIFACT_SACRIFICE_RETURN_MECHANIC,
        artifact_sacrifice_return_dependencies,
    )

    if ARTIFACT_SACRIFICE_RETURN_MECHANIC not in scope["mechanics"]:
        return None
    required = artifact_sacrifice_return_dependencies(scope["effects"], scope["target_schema"])
    if not required or scope["active_zone"] != "battlefield" or scope["cost_schema"].get("tap_source") is not True:
        return DependencyGate(blockers=(f"capability:{ARTIFACT_SACRIFICE_RETURN_CAPABILITY}",))
    return explicit_capabilities_gate(
        required, capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
    )


def draw_source_owner_top_activation(ability, text, *, public_timing):
    from ..rules.owner_library_position_shapes import DRAW_SOURCE_OWNER_TOP_MECHANIC, draw_source_owner_top_effects, has_exact_tap_price

    if (ability.mana_ability or ability.zones != ('battlefield',)
            or not has_exact_tap_price(activated_ability_cost(ability, public_timing=public_timing))
            or re.fullmatch(r"Draw a card, then put this artifact on top of its owner['’]s library\.", text, re.I) is None):
        return None
    return 'draw-then-source-owner-top-v1', draw_source_owner_top_effects(), None, (DRAW_SOURCE_OWNER_TOP_MECHANIC,)


def draw_source_owner_top_activation_gate(
    *, effects, target_schema, mechanics, active_zone, cost_schema, capability_registry, capability_profile,
):
    from ..rules.owner_library_position_shapes import (
        DRAW_SOURCE_OWNER_TOP_CAPABILITY, DRAW_SOURCE_OWNER_TOP_FIELD, draw_source_owner_top_capabilities,
    )

    if not any(DRAW_SOURCE_OWNER_TOP_FIELD in effect for effect in effects):
        return None
    required = draw_source_owner_top_capabilities(effects, target_schema, active_zone, cost_schema, mechanics)
    if not required:
        return DependencyGate(blockers=(f'capability:{DRAW_SOURCE_OWNER_TOP_CAPABILITY}',))
    return explicit_capabilities_gate(required, capability_registry=capability_registry, capability_profile=capability_profile)


def linked_loyalty_activation_gate(
    *, effects, target_schema, mechanics, active_zone, cost_schema, capability_registry, capability_profile,
):
    from ..rules.linked_loyalty_exile_model import LOYALTY_X_CAPABILITY
    from ..rules.linked_loyalty_exile_shapes import (
        linked_loyalty_activation_capabilities, uses_linked_loyalty_activation,
    )

    if not uses_linked_loyalty_activation(effects, cost_schema):
        return None
    required = linked_loyalty_activation_capabilities(
        effects=effects, target_schema=target_schema, active_zone=active_zone, cost_schema=cost_schema,
    )
    if not required:
        return DependencyGate(blockers=(f"capability:{LOYALTY_X_CAPABILITY}",))
    return explicit_capabilities_gate(
        required, capability_registry=capability_registry, capability_profile=capability_profile,
    )


def source_sacrifice_power_life_result(ability, text, *, public_timing):
    if (
        text.casefold() != "you gain life equal to its power."
        or ability.mana_ability or ability.zones != ("battlefield",) or ability.sorcery_speed
        or ability.activation_conditions
        or not source_sacrifice_power_price_is_closed(activated_ability_cost(ability, public_timing=public_timing))
    ):
        return None
    return (
        SOURCE_SACRIFICE_POWER_LIFE_TEMPLATE, (source_sacrifice_power_life_effect(),), None,
        (SOURCE_SACRIFICE_POWER_LIFE_MECHANIC, SCALAR_AMOUNT_MECHANIC, "cr-119-life"),
    )


def source_sacrifice_power_life_gate(*, capability_registry, capability_profile, **scope):
    if SOURCE_SACRIFICE_POWER_LIFE_MECHANIC not in scope["mechanics"]:
        return None
    required = source_sacrifice_power_life_capabilities(**scope)
    if not required:
        return DependencyGate(blockers=(f"mechanic:{SOURCE_SACRIFICE_POWER_LIFE_MECHANIC}",))
    return explicit_capabilities_gate(
        required, capability_registry=capability_registry, capability_profile=capability_profile,
    )


def commander_return_activation_gate(
    *, effects, target_schema, mechanics, active_zone, cost_schema, capability_registry, capability_profile,
):
    if not any(COMMANDER_RETURN_FIELD in effect for effect in effects):
        return None
    required = commander_return_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
    )
    if not required or active_zone != "battlefield" or not commander_return_price_is_closed(cost_schema):
        return DependencyGate(blockers=(f"capability:{COMMANDER_RETURN_CAPABILITY}",))
    return explicit_capabilities_gate(
        required, capability_registry=capability_registry, capability_profile=capability_profile,
    )


_RANDOM_ACTIVATION_DAMAGE = re.compile(
    r"(?P<ordinary>.+ deals [1-9][0-9]* damage to any target) chosen at random\.?",
    re.IGNORECASE,
)


def random_target_activation_result(ability, *, source_name, text, public_timing):
    match = _RANDOM_ACTIVATION_DAMAGE.fullmatch(text.strip())
    if match is None or ability.mana_ability or ability.zones != ("battlefield",):
        return None
    from ..rules.random_activation_target import RANDOM_ACTIVATION_TARGET_FIELD, random_activation_has_tap_price
    from .damage_templates import fixed_damage_effect_template

    if not random_activation_has_tap_price(activated_ability_cost(ability, public_timing=public_timing)):
        return None
    damage = fixed_damage_effect_template(match["ordinary"] + ".", card_name=source_name)
    if damage is None:
        return None
    _, effects, schema, mechanics = damage.compiled()
    effect, = effects
    return (
        "damage-random-activation-target-v1", ({**effect, RANDOM_ACTIVATION_TARGET_FIELD: 1},), schema, mechanics,
    )


def activation_paid_sacrifice_result(ability, *, source_name, text, compile_fixed):
    cost = activated_ability_cost(ability)
    if ability.mana_ability or activation_creature_sacrifice_choice(cost) is None:
        return None
    from .paid_sacrifice_condition_templates import paid_sacrifice_condition_template

    if conditioned := paid_sacrifice_condition_template(
        text, cost_schema=cost, compile_fixed=compile_fixed, activation=True, source_name=source_name,
    ):
        return conditioned
    compiled = _paid_sacrifice_stat_template(text, source_name=source_name, compile_fixed=compile_fixed)
    if compiled is None:
        return None
    template, effects, schema, mechanics = compiled
    if not effects or any(effect.get('op') == 'mana' for effect in effects):
        return None
    def convert(value):
        if isinstance(value, Mapping):
            if value.get('kind') == SCALAR_AMOUNT_KIND:
                spec = ScalarEffectAmountSpec.from_dict(value)
                if spec.origin is not ScalarAmountOrigin.PAID_SACRIFICE_CREATURE:
                    raise ValueError('Activation paid result requires its isolated paid creature stat')
                return replace(spec, origin=ScalarAmountOrigin.PAID_ACTIVATION_CREATURE, schema_version=13).to_dict()
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(convert(v) for v in value)
        return value
    return ('activation-' + template, tuple(convert(e) for e in effects), schema,
        tuple(m for m in mechanics if m != PAID_SACRIFICE_STAT_MECHANIC) + (ACTIVATION_PAID_STAT_MECHANIC,))
