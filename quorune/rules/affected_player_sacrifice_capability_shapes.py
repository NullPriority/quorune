from __future__ import annotations

"""Capability shape for one fixed affected-player sacrifice instruction."""

from typing import Any, Iterable, Mapping, Sequence

from ..apnap_object_payment import DECLINED_LIFE_LOSS, apnap_object_payment_dependencies
from ..compiler.affected_player_sacrifice_templates import (
    FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
    FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC,
    MOST_LANDS_ATTACK_SACRIFICE, MOST_LANDS_ATTACK_SACRIFICE_MECHANIC,
    SOURCE_EXCLUDED_SACRIFICE_CAPABILITY, SOURCE_EXCLUDED_SACRIFICE_MECHANIC,
    fixed_affected_player_sacrifice_predicate_is_closed,
)

from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..player_maximum_model import LAND_COUNT_METRIC, uses_land_count_maximizers
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects


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


def is_closed_affected_player_sacrifice_program(program: Any) -> bool:
    required=fixed_affected_player_sacrifice_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)


def fixed_affected_player_sacrifice_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one fixed affected-player permanent-sacrifice choice."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {
        FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC,
        "sacrifice",
    }.issubset(mechanics) or len(effects) != 1:
        return ()
    effect = effects[0]
    if MOST_LANDS_ATTACK_SACRIFICE_MECHANIC in mechanics or uses_land_count_maximizers(effect.get("players")):
        if (
            MOST_LANDS_ATTACK_SACRIFICE_MECHANIC in mechanics and target_schema is None
            and most_lands_attack_sacrifice_effect_is_closed(effect)
        ):
            return (FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY, "zone.change.destination_replacement")
        return ()
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


def source_excluded_sacrifice_runtime_dependencies(program):
    """Source-object authority is required by the payload, independent of tags."""
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if (value.get('op') == 'choose_cards_apnap' and value.get('then') == 'sacrifice'
                    and value.get('exclude_ref') == '$source.zone_object'):
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
