from __future__ import annotations

"""Capability shape for one fixed affected-player sacrifice instruction."""

from typing import Any, Iterable, Mapping, Sequence

from ..apnap_object_payment import (
    DECLINED_LIFE_LOSS, ORDER_GRAVEYARD_ARRIVALS, OTHER_PLAYER_SACRIFICE_CAPABILITY,
    OTHER_PLAYER_SACRIFICE_SCOPE,
    apnap_object_payment_dependencies, validate_other_player_sacrifice,
)
from ..compiler.affected_player_sacrifice_templates import (
    FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
    FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC,
    AffectedPlayerSacrificeSubject, FixedAffectedPlayerSacrificeTemplate,
    MOST_LANDS_ATTACK_SACRIFICE, MOST_LANDS_ATTACK_SACRIFICE_MECHANIC,
    SOURCE_EXCLUDED_SACRIFICE_CAPABILITY, SOURCE_EXCLUDED_SACRIFICE_MECHANIC,
    fixed_affected_player_sacrifice_predicate_is_closed,
)

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..power_sacrifice_model import (
    ATTACK_SACRIFICE_STAT_FIELD, ATTACK_SACRIFICE_STAT_TEMPLATE, validate_attack_sacrifice_stat_effect,
    CONTROLLER_SACRIFICE_LIFE_FIELD, CONTROLLER_SACRIFICE_LIFE_TEMPLATE,
    ENTRY_SACRIFICE_POWER_DRAW_FIELD, ENTRY_SACRIFICE_POWER_DRAW_TEMPLATE, GREATEST_POWER_SACRIFICE_FIELD,
    ENTRY_SACRIFICE_POWER_TREASURE_FIELD, ENTRY_SACRIFICE_POWER_TREASURE_TEMPLATE,
    ENTRY_SACRIFICE_POWER_TREASURE_CAPABILITY, validate_entry_sacrifice_power_treasure,
    GreatestPowerSacrificeSpec, validate_controller_sacrifice_toughness_life, validate_entry_sacrifice_power_life_draw,
)
from ..public_characteristic_aggregate_model import PUBLIC_STAT_AGGREGATE_CAPABILITY
from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_CAPABILITY
from ..player_maximum_model import LAND_COUNT_METRIC, uses_land_count_maximizers
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import contains_nested_key, nested_effects
from .graveyard_arrival_order import GRAVEYARD_ARRIVAL_ORDER_CAPABILITY
from .keep_permanents_by_type import (
    KEEP_PERMANENTS_BY_TYPE_OPERATION, keep_by_type_capabilities, keep_by_type_runtime_dependencies,
)
from .any_number_sacrifice import (
    ANY_NUMBER_SACRIFICE_FIELD, any_number_sacrifice_capabilities, any_number_sacrifice_runtime_dependencies,
)
from .counted_sacrifice_tokens import (
    COUNTED_SACRIFICE_TOKEN_FIELD, counted_sacrifice_token_capabilities, counted_sacrifice_token_runtime_dependencies,
)
from .target_sacrifice_toughness import (
    TARGET_SACRIFICE_TOUGHNESS_FIELD, target_sacrifice_toughness_capabilities,
    target_sacrifice_toughness_runtime_dependencies,
)


def most_lands_attack_sacrifice_effect_is_closed(effect, *, actor="$controller"):
    if not isinstance(effect, Mapping) or type(effect.get("count")) is not int:
        return False
    players = effect.get("players")
    if not uses_land_count_maximizers(players) or type(players.get("schema_version")) is not int:
        return False
    if thaw_value(effect) != {**MOST_LANDS_ATTACK_SACRIFICE.effects[0], "actor": actor}:
        return False
    try:
        return ObjectQuerySpec.from_dict(effect["predicate"]) == LAND_COUNT_METRIC.predicate()
    except ObjectQueryError:
        return False


def most_lands_attack_sacrifice_runtime_dependencies(program):
    effects = (*program.effects, *getattr(program, "handlers", ()))
    if MOST_LANDS_ATTACK_SACRIFICE_MECHANIC not in program.coverage and not any(
        uses_land_count_maximizers(effect.get("players")) for effect in nested_effects(effects)
    ):
        return ()
    if (
        (program.event, program.active_zone) != ("creature.attacks", "battlefield")
        or program.event_condition != {"field": "card", "op": "eq", "value": "$source.ref"}
        or program.target_schema is not None or getattr(program, "cost_schema", None) is not None
        or len(program.effects) != 1 or getattr(program, "handlers", ())
        or not most_lands_attack_sacrifice_effect_is_closed(program.effects[0])
    ):
        raise ValueError("Most-lands sacrifice requires the complete untargeted source attack")
    return (
        FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY, "trigger.event.normalized_self_attack",
        "trigger.placement.apnap", "zone.change.destination_replacement",
    )


_CONTROLLER_SACRIFICE_LIFE_DEPENDENCIES = (
    FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY, PUBLIC_STAT_AGGREGATE_CAPABILITY,
    "life.change.effect", "trigger.effect.fixed_event", "trigger.placement.apnap",
    "zone.change.destination_replacement",
)


def upkeep_sacrifice_life_condition():
    return {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "upkeep"},
    ]}


def _controller_sacrifice_life_runtime_dependencies(program):
    template = CONTROLLER_SACRIFICE_LIFE_TEMPLATE.replace("fixed-counter-", "fixed-typed-effect-", 1)
    if not (
        template in str(program.provenance.get("template_id") or "").split("+")
        or contains_nested_key((program.effects, program.handlers), CONTROLLER_SACRIFICE_LIFE_FIELD)
    ):
        return ()
    if (
        (program.event, program.active_zone) != ("step.begin", "battlefield")
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or len(program.effects) != 1 or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or thaw_value(program.event_condition) != upkeep_sacrifice_life_condition()
    ):
        raise ValueError("Controller sacrifice life requires the complete original upkeep ability")
    validate_controller_sacrifice_toughness_life(program.effects[0])
    return _CONTROLLER_SACRIFICE_LIFE_DEPENDENCIES


_ENTRY_SACRIFICE_POWER_DRAW_DEPENDENCIES = (
    *_CONTROLLER_SACRIFICE_LIFE_DEPENDENCIES, SOURCE_EXCLUDED_SACRIFICE_CAPABILITY,
    "trigger.event.normalized_zone_change", "zone.draw.library_to_hand",
)


_ENTRY_SACRIFICE_TREASURE_DEPENDENCIES = (
    "choice.affected_player.fixed_sacrifice", SOURCE_EXCLUDED_SACRIFICE_CAPABILITY,
    "zone.change.destination_replacement", "trigger.event.normalized_zone_change",
    "token.creation.fixed_definition", ENTRY_SACRIFICE_POWER_TREASURE_CAPABILITY,
)


def entry_sacrifice_power_condition():
    return {"field": "card", "op": "eq", "value": "$source.ref"}


def _entry_sacrifice_power_draw_runtime_dependencies(program):
    templates = str(program.provenance.get("template_id") or "").split("+")
    treasure_template = ENTRY_SACRIFICE_POWER_TREASURE_TEMPLATE.replace("fixed-counter-", "fixed-typed-effect-", 1)
    treasure = treasure_template in templates or contains_nested_key(
        (program.effects, program.handlers), ENTRY_SACRIFICE_POWER_TREASURE_FIELD,
    )
    draw_template = ENTRY_SACRIFICE_POWER_DRAW_TEMPLATE.replace("fixed-counter-", "fixed-typed-effect-", 1)
    if not (treasure or draw_template in templates
            or contains_nested_key((program.effects, program.handlers), ENTRY_SACRIFICE_POWER_DRAW_FIELD)):
        return ()
    if (
        (program.event, program.active_zone) != ("permanent.enter", "battlefield")
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or len(program.effects) != 1 or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or thaw_value(program.event_condition) != entry_sacrifice_power_condition()
    ):
        raise ValueError("Entry sacrifice and power result require the exact original source entry")
    if treasure:
        validate_entry_sacrifice_power_treasure(program.effects[0])
        return _ENTRY_SACRIFICE_TREASURE_DEPENDENCIES
    validate_entry_sacrifice_power_life_draw(program.effects[0])
    return _ENTRY_SACRIFICE_POWER_DRAW_DEPENDENCIES


_ATTACK_SACRIFICE_STAT_DEPENDENCIES = (
    *_CONTROLLER_SACRIFICE_LIFE_DEPENDENCIES, SOURCE_EXCLUDED_SACRIFICE_CAPABILITY,
    "trigger.event.normalized_self_attack", "zone.draw.library_to_hand",
)


def _attack_sacrifice_stat_runtime_dependencies(program):
    template = ATTACK_SACRIFICE_STAT_TEMPLATE.replace("fixed-counter-", "fixed-typed-effect-", 1)
    if not (
        template in str(program.provenance.get("template_id") or "").split("+")
        or contains_nested_key((program.effects, program.handlers), ATTACK_SACRIFICE_STAT_FIELD)
    ):
        return ()
    if (
        (program.event, program.active_zone) != ("creature.attacks", "battlefield")
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or len(program.effects) != 1 or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or thaw_value(program.event_condition) != {"field": "card", "op": "eq", "value": "$source.ref"}
    ):
        raise ValueError("Attack sacrifice and both stat results require the exact original source attack")
    validate_attack_sacrifice_stat_effect(program.effects[0])
    return _ATTACK_SACRIFICE_STAT_DEPENDENCIES


def ordered_creature_sacrifice_effect_is_closed(effect, *, actor="$controller"):
    if not isinstance(effect, Mapping) or effect.get(ORDER_GRAVEYARD_ARRIVALS) is not True:
        return False
    if type(effect.get("count")) is not int:
        return False
    template = FixedAffectedPlayerSacrificeTemplate(
        AffectedPlayerSacrificeSubject.EACH_PLAYER, 13,
        ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
        order_graveyard_arrivals=True,
    )
    return thaw_value(effect) == {**template.effects[0], "actor": actor}


def _ordered_creature_sacrifice_runtime_dependencies(program):
    marked = tuple(effect for effect in nested_effects((*program.effects, *program.handlers))
                   if ORDER_GRAVEYARD_ARRIVALS in effect)
    if not marked:
        return ()
    required = fixed_affected_player_sacrifice_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if tuple(program.effects) != marked or not required:
        raise ValueError("Ordered creature sacrifice requires the complete fixed instruction")
    return required


def greatest_power_sacrifice_runtime_dependencies(program):
    if required := (
        _ordered_creature_sacrifice_runtime_dependencies(program)
        or _controller_sacrifice_life_runtime_dependencies(program)
        or _entry_sacrifice_power_draw_runtime_dependencies(program)
        or _attack_sacrifice_stat_runtime_dependencies(program)
        or target_sacrifice_toughness_runtime_dependencies(program)
        or counted_sacrifice_token_runtime_dependencies(program)
        or any_number_sacrifice_runtime_dependencies(program)
        or keep_by_type_runtime_dependencies(program)
    ):
        return required
    effects = (*program.effects, *getattr(program, "handlers", ()))
    marked = [effect for effect in nested_effects(effects) if GREATEST_POWER_SACRIFICE_FIELD in effect]
    if not marked:
        return ()
    if len(marked) != 1 or not program.effects or marked[0] != program.effects[-1]:
        raise ValueError("Greatest-power sacrifice requires its closed result")
    spec = GreatestPowerSacrificeSpec.from_effect(marked[0])
    if spec.opponent_only:
        from .gift_graveyard_spells import greed_dependencies

        return greed_dependencies(program)
    if not _greatest_power_sacrifice_target_is_closed(program.target_schema, spec):
        raise ValueError("Greatest-power sacrifice has the wrong affected target scope")
    event_zone = (program.event, program.active_zone)
    if spec.mana_value:
        if event_zone != ("resolve", "stack") or len(program.effects) != 1:
            raise ValueError("Greatest mana-value sacrifice requires one closed untargeted spell result")
    elif spec.target_player is not None:
        if event_zone != ("resolve", "stack") or len(program.effects) != 1:
            raise ValueError("Targeted greatest-power sacrifice and life require one closed spell result")
    elif spec.lost_life_only:
        if event_zone != ("activate", "battlefield") or len(program.effects) != 1:
            raise ValueError("Life-qualified greatest-power sacrifice requires its fixed battlefield activation")
    elif event_zone == ("activate", "battlefield"):
        from .repeated_apnap_payment import is_plain_loyalty_removal_program

        if len(program.effects) != 1 or not is_plain_loyalty_removal_program(program):
            raise ValueError("Greatest-power sacrifice requires its closed loyalty result")
    elif event_zone != ("resolve", "stack") or len(program.effects) > 2:
        raise ValueError("Greatest-power sacrifice requires its closed spell resolution")
    return _greatest_power_sacrifice_dependencies(spec)


def _greatest_power_sacrifice_target_is_closed(target_schema, spec):
    if spec.target_schema is None:
        return target_schema is None
    return (isinstance(target_schema, Mapping) and type(target_schema.get("count")) is int
            and thaw_value(FrozenMap(target_schema)) == spec.target_schema)


def _greatest_power_sacrifice_dependencies(spec):
    return (
        FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
        "zone.change.destination_replacement",
        PUBLIC_QUERY_AMOUNT_CAPABILITY if spec.mana_value else PUBLIC_STAT_AGGREGATE_CAPABILITY,
        *(("condition.history.fixed_public_query",) if spec.lost_life_only else ()),
        *(("target.revalidate_resolution",) if spec.opponent_only else
          ("target.revalidate_resolution", "life.change.effect") if spec.target_player is not None else ()),
    )


def is_closed_affected_player_sacrifice_program(program: Any) -> bool:
    required=fixed_affected_player_sacrifice_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)


def _specialized_sacrifice_node_capabilities(effect, target_schema):
    from .shared_type_sacrifice import SHARED_TYPE_SACRIFICE, shared_type_sacrifice_node_capabilities

    if SHARED_TYPE_SACRIFICE in effect:
        return shared_type_sacrifice_node_capabilities(effect, target_schema)
    if effect.get("op") == KEEP_PERMANENTS_BY_TYPE_OPERATION:
        try:
            return keep_by_type_capabilities(effect, target_schema)
        except (KeyError, TypeError, ValueError):
            return ()
    if ANY_NUMBER_SACRIFICE_FIELD in effect:
        try:
            return any_number_sacrifice_capabilities(effect, target_schema)
        except (KeyError, TypeError, ValueError):
            return ()
    if COUNTED_SACRIFICE_TOKEN_FIELD in effect:
        try:
            return counted_sacrifice_token_capabilities(effect, target_schema)
        except (KeyError, TypeError, ValueError):
            return ()
    if TARGET_SACRIFICE_TOUGHNESS_FIELD in effect:
        try:
            return target_sacrifice_toughness_capabilities(effect, target_schema)
        except (KeyError, TypeError, ValueError):
            return ()
    return None


def fixed_affected_player_sacrifice_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one fixed affected-player permanent-sacrifice choice."""
    from .keep_three_sacrifice import KEEP_THREE_SACRIFICE_FIELD, keep_three_sacrifice_node_capabilities
    from .counter_marked_keep import KEEP_COUNTER_FIELD, keep_counter_node_capabilities

    if len(effects) == 1 and KEEP_COUNTER_FIELD in effects[0]:
        return keep_counter_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if len(effects) == 1 and KEEP_THREE_SACRIFICE_FIELD in effects[0]:
        return keep_three_sacrifice_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {
        FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC,
        "sacrifice",
    }.issubset(mechanics) or len(effects) != 1:
        return ()
    effect = effects[0]
    if ORDER_GRAVEYARD_ARRIVALS in effect:
        if target_schema is not None or not ordered_creature_sacrifice_effect_is_closed(effect):
            return ()
        return (FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY, "zone.change.destination_replacement",
                GRAVEYARD_ARRIVAL_ORDER_CAPABILITY)
    from .ordered_opponent_creature_sacrifice import DEPENDENCIES, ordered_opponent_sacrifice_is_closed, uses_ordered_opponent_sacrifice

    if uses_ordered_opponent_sacrifice(effect):
        return DEPENDENCIES if target_schema is None and ordered_opponent_sacrifice_is_closed(effect) else ()
    from .ordered_type_sacrifice import ORDERED_TYPE_SACRIFICE_OPERATION, ordered_type_sacrifice_node_capabilities
    from .sacrifice_discard_fallback import sacrifice_discard_fallback_node_capabilities, uses_sacrifice_discard_fallback

    if uses_sacrifice_discard_fallback(effect):
        return sacrifice_discard_fallback_node_capabilities(effects, target_schema)
    if effect.get("op") == ORDERED_TYPE_SACRIFICE_OPERATION:
        return ordered_type_sacrifice_node_capabilities(effects=effects, target_schema=target_schema)
    from .colored_permanent_sacrifice import DEPENDENCIES, all_colored_sacrifice_is_closed, uses_all_colored_sacrifice

    if uses_all_colored_sacrifice(effect):
        return DEPENDENCIES if target_schema is None and all_colored_sacrifice_is_closed(effect) else ()
    specialized = _specialized_sacrifice_node_capabilities(effect, target_schema)
    if specialized is not None:
        return specialized
    if effect.get("players") == OTHER_PLAYER_SACRIFICE_SCOPE:
        try:
            validate_other_player_sacrifice(effect, actor="$controller")
        except (KeyError, TypeError, ValueError):
            return ()
        return _OTHER_PLAYER_SACRIFICE_DEPENDENCIES if target_schema is None else ()
    if ATTACK_SACRIFICE_STAT_FIELD in effect:
        try:
            validate_attack_sacrifice_stat_effect(effect)
        except ValueError:
            return ()
        return _ATTACK_SACRIFICE_STAT_DEPENDENCIES if target_schema is None else ()
    if ENTRY_SACRIFICE_POWER_TREASURE_FIELD in effect:
        try:
            validate_entry_sacrifice_power_treasure(effect)
        except ValueError:
            return ()
        return _ENTRY_SACRIFICE_TREASURE_DEPENDENCIES if target_schema is None else ()
    if ENTRY_SACRIFICE_POWER_DRAW_FIELD in effect:
        try:
            validate_entry_sacrifice_power_life_draw(effect)
        except ValueError:
            return ()
        return _ENTRY_SACRIFICE_POWER_DRAW_DEPENDENCIES if target_schema is None else ()
    if CONTROLLER_SACRIFICE_LIFE_FIELD in effect:
        try:
            validate_controller_sacrifice_toughness_life(effect)
        except ValueError:
            return ()
        return _CONTROLLER_SACRIFICE_LIFE_DEPENDENCIES if target_schema is None else ()
    if GREATEST_POWER_SACRIFICE_FIELD in effect:
        try:
            spec = GreatestPowerSacrificeSpec.from_effect(effect)
        except (TypeError, ValueError):
            return ()
        if not _greatest_power_sacrifice_target_is_closed(target_schema, spec):
            return ()
        return _greatest_power_sacrifice_dependencies(spec)
    if MOST_LANDS_ATTACK_SACRIFICE_MECHANIC in mechanics or uses_land_count_maximizers(effect.get("players")):
        if (
            MOST_LANDS_ATTACK_SACRIFICE_MECHANIC in mechanics and target_schema is None
            and most_lands_attack_sacrifice_effect_is_closed(effect)
        ):
            return (FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY, "zone.change.destination_replacement")
        return ()
    return _ordinary_sacrifice_node_capabilities(effect, target_schema, mechanics)


def _ordinary_sacrifice_node_capabilities(effect, target_schema, mechanics):
    excluded = 'exclude_ref' in effect
    if (excluded != (SOURCE_EXCLUDED_SACRIFICE_MECHANIC in mechanics)
            or excluded and effect.get('exclude_ref') != '$source.zone_object'):
        return ()
    try:
        payment_dependencies = apnap_object_payment_dependencies(effect)
    except ValueError:
        return ()
    targeted = effect.get("players") == ["$target.0"]
    expected_fields = {
        "op",
        "actor",
        "players",
        "zone",
        "predicate",
        "count",
        "then",
        "prompt",
        *((DECLINED_LIFE_LOSS,) if payment_dependencies else ()),
        *(("target",) if targeted else ()),
        *(('exclude_ref',) if excluded else ()),
    }
    if (
        set(effect) != expected_fields
        or effect.get("op") != "choose_cards_apnap"
        or effect.get("actor") != "$controller"
        or effect.get("zone") != "battlefield"
        or type(effect.get("count")) is not int
        or effect.get("count") < 1
        or effect.get("then") != "sacrifice"
        or type(effect.get("prompt")) is not str
        or not str(effect.get("prompt")).strip()
        or not isinstance(effect.get("predicate"), Mapping)
        or not fixed_affected_player_sacrifice_predicate_is_closed(
            effect["predicate"]
        )
    ):
        return ()
    dependencies = {
        FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
        "zone.change.destination_replacement",
        *payment_dependencies,
        *((SOURCE_EXCLUDED_SACRIFICE_CAPABILITY,) if excluded else ()),
    }
    if targeted:
        if (
            effect.get("target") != "$target.0"
            or "cr-115-targets" not in mechanics
            or dict(target_schema or {})
            not in (
                {
                    "zones": ["player"],
                    "categories": ["player"],
                    "player_relation": "any",
                    "count": 1,
                },
                {
                    "zones": ["player"],
                    "categories": ["player"],
                    "player_relation": "opponent",
                    "count": 1,
                },
            )
        ):
            return ()
        dependencies.add("target.revalidate_resolution")
    elif target_schema is not None or effect.get("players") not in (
        "all",
        "opponents",
        ["$controller"],
    ):
        return ()
    return tuple(sorted(dependencies))


_OTHER_PLAYER_SACRIFICE_DEPENDENCIES = (
    FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY, OTHER_PLAYER_SACRIFICE_CAPABILITY,
    "zone.change.destination_replacement",
)


def other_player_sacrifice_runtime_dependencies(program):
    from .ordered_opponent_creature_sacrifice import ordered_opponent_sacrifice_program_dependencies

    if required := ordered_opponent_sacrifice_program_dependencies(program):
        return required
    marked = tuple(effect for effect in nested_effects((*program.effects, *program.handlers))
                   if effect.get("players") == OTHER_PLAYER_SACRIFICE_SCOPE
                   and COUNTED_SACRIFICE_TOKEN_FIELD not in effect)
    if not marked and OTHER_PLAYER_SACRIFICE_CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(marked) != 1 or tuple(program.effects) != marked or program.target_schema is not None
            or program.active_zone != "battlefield" or program.event not in {"activate", "creature.dies", "permanent.dies"}):
        raise ValueError("Other-player sacrifice requires its single untargeted battlefield result")
    validate_other_player_sacrifice(marked[0], actor="$controller")
    return _OTHER_PLAYER_SACRIFICE_DEPENDENCIES


def source_excluded_sacrifice_runtime_dependencies(program):
    """Source-object authority is required by the payload, independent of tags."""
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if (value.get('op') == 'choose_cards_apnap' and value.get('then') == 'sacrifice'
                    and value.get('exclude_ref') == '$source.zone_object' and ANY_NUMBER_SACRIFICE_FIELD not in value):
                if not isinstance(value.get('predicate'),Mapping) or not fixed_affected_player_sacrifice_predicate_is_closed(value['predicate']):
                    raise ValueError('Source-excluded sacrifice requires a closed permanent predicate')
                required.add(SOURCE_EXCLUDED_SACRIFICE_CAPABILITY)
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit(program.effects)
    if required and program.active_zone != 'battlefield':
        raise ValueError('Source-excluded sacrifices require their represented battlefield source')
    return tuple(sorted(required))


__all__ = ["fixed_affected_player_sacrifice_node_capabilities"]
