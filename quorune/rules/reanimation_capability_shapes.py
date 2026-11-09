from __future__ import annotations

"""Capability closure for fixed single-target reanimation."""

from collections.abc import Iterable, Mapping, Sequence

from ..compiler.reanimation_templates import (
    FIXED_TARGET_REANIMATION_CAPABILITY,
    FIXED_TARGET_REANIMATION_MECHANIC,
    RELATIVE_TARGET_REANIMATION_MECHANIC,
    relative_reanimation_scope,
)
from ..relative_power_target import MANA_VALUE_POWER_CAPABILITY, MANA_VALUE_POWER_PREDICATES
from ..effect_contracts import REANIMATE_OPERATION
from .reanimation_followup import (
    REANIMATION_FOLLOWUP_CAPABILITY, REANIMATION_FOLLOWUP_MECHANIC,
    REANIMATION_FOLLOWUP_VERSIONS, battlefield_entry_followup_capabilities, validate_reanimation_followup,
)
from ..targets import TargetGroup
from ..target_numeric import (
    TargetNumericCharacteristic,
    TargetNumericComparison,
)


_PERMANENT_TYPES = frozenset(
    {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
)


def fixed_target_reanimation_covered_mechanics(
    capability_ids: Iterable[str],
) -> tuple[str, ...]:
    from .attacking_card_entry import ATTACKING_CARD_ENTRY_CAPABILITY, SOURCE_GRAVEYARD_ATTACKING_ENTRY_MECHANIC

    supplied = set(capability_ids)
    covered = (FIXED_TARGET_REANIMATION_MECHANIC, "cr-115-targets") if FIXED_TARGET_REANIMATION_CAPABILITY in supplied else ()
    if covered and MANA_VALUE_POWER_CAPABILITY in supplied:
        covered = (*covered, RELATIVE_TARGET_REANIMATION_MECHANIC)
    if ATTACKING_CARD_ENTRY_CAPABILITY in supplied:
        return (*covered, SOURCE_GRAVEYARD_ATTACKING_ENTRY_MECHANIC)
    return covered


def fixed_target_reanimation_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize exactly one closed graveyard-to-battlefield target move."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if len(effects) == 1 and "attacking_card_entry" in effects[0]:
        from .attacking_card_entry import (
            ATTACKING_CARD_ENTRY_CAPABILITY,
            SOURCE_GRAVEYARD_ATTACKING_ENTRY_MECHANIC,
            validate_attacking_entry_choice,
        )

        try:
            move = validate_attacking_entry_choice(effects[0], actor="$controller", compiled=True)
            target = TargetGroup.from_mapping(target_schema) if target_schema is not None else None
        except (ValueError, TypeError):
            return ()
        if move["card"] == "$source":
            if target_schema is not None or SOURCE_GRAVEYARD_ATTACKING_ENTRY_MECHANIC not in mechanics:
                return ()
            counters = ("counter.producer.effect_entry",) if "entry_counter" in move else ()
            return (ATTACKING_CARD_ENTRY_CAPABILITY, "zone.change.destination_replacement", *counters)
        if target is None or target.types_any != ("creature",) and "creature" not in target.types_all:
            return ()
        base = {"op": REANIMATE_OPERATION, "card": move["card"], "controller": move["controller"], "tapped": True}
        required = fixed_target_reanimation_node_capabilities(effects=(base,), target_schema=target_schema, mechanic_ids=mechanics)
        return (*required, ATTACKING_CARD_ENTRY_CAPABILITY, "target.revalidate_resolution") if required else ()
    if len(effects) == 1 and effects[0].get('op') == REANIMATE_OPERATION and effects[0].get('schema_version') in REANIMATION_FOLLOWUP_VERSIONS:
        return _reanimation_followup_capabilities(effects[0], target_schema=target_schema, mechanics=mechanics)
    if len(effects) == 1 and effects[0].get('schema_version') == 2:
        from ..event_card_return import (EVENT_BATTLEFIELD_RETURN_MECHANIC,
            EVENT_BATTLEFIELD_RETURN_CAPABILITY, event_battlefield_return_effect)
        effect = effects[0]
        try:
            expected = event_battlefield_return_effect(tapped=effect['tapped'], entry_counter=effect['entry_counter'])
        except (ValueError, TypeError, KeyError):
            return ()
        if target_schema is not None or EVENT_BATTLEFIELD_RETURN_MECHANIC not in mechanics or dict(effect) != expected:
            return ()
        return (EVENT_BATTLEFIELD_RETURN_CAPABILITY, 'zone.change.destination_replacement',
            *(('counter.producer.effect_entry',) if effect['entry_counter'] else ()))
    if (
        FIXED_TARGET_REANIMATION_MECHANIC not in mechanics
        or "cr-115-targets" not in mechanics
        or len(effects) != 1
        or not isinstance(target_schema, Mapping)
    ):
        return ()
    if target_schema.get("predicate") in MANA_VALUE_POWER_PREDICATES:
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card", "controller", "tapped"}
        or effect.get("op") != REANIMATE_OPERATION
        or effect.get("card") != "$target.0"
        or effect.get("controller") not in {"$controller", "$target.owner.0"}
        or type(effect.get("tapped")) is not bool
    ):
        return ()
    try:
        target = TargetGroup.from_mapping(target_schema)
    except (TypeError, ValueError):
        return ()
    if (
        target.zones != ("graveyard",)
        or target.categories != ("card",)
        or target.owner_relation not in {"any", "you", "opponent"}
        or target.controller_relation != "any"
        or target.min_targets not in {0, 1}
        or target.max_targets != 1
        or not set((*target.types_any, *target.types_all)).intersection(
            _PERMANENT_TYPES
        )
        or set(target.types_none) - {"creature", "land"}
        or target.keywords_all
        or target.keywords_none
        or target.colors_any
        or target.colors_all
        or target.colors_none
        or target.colorless is not None
        or target.color_count_min is not None
        or target.color_count_equal is not None
        or target.damage_history is not None
        or target.state_predicate is not None
    ):
        return ()
    numeric = target.numeric_characteristic
    if numeric is not None and (
        numeric.characteristic is not TargetNumericCharacteristic.POWER
        or numeric.comparison is not TargetNumericComparison.AT_MOST
        or set(target.types_any) != {"creature"}
    ):
        return ()
    forms = tuple(form.to_dict() for form in target.characteristic_forms_any)
    if forms not in (
        (),
        (
            {
                "types_all": ["creature"],
                "subtypes_any": [],
                "supertypes_any": [],
            },
            {
                "types_all": [],
                "subtypes_any": ["vehicle"],
                "supertypes_any": [],
            },
        ),
        (
            {
                "types_all": ["creature"],
                "subtypes_any": [],
                "supertypes_any": [],
            },
            {
                "types_all": [],
                "subtypes_any": ["spacecraft"],
                "supertypes_any": [],
            },
        ),
    ):
        return ()
    return (FIXED_TARGET_REANIMATION_CAPABILITY,)


def relative_target_reanimation_node_capabilities(*, effects, target_schema, mechanics):
    if not {RELATIVE_TARGET_REANIMATION_MECHANIC, FIXED_TARGET_REANIMATION_MECHANIC, "cr-115-targets"} <= set(mechanics):
        return ()
    if len(effects) != 1 or relative_reanimation_scope(target_schema) is None:
        return ()
    effect = effects[0]
    if dict(effect) != {"op": REANIMATE_OPERATION, "card": "$target.0", "controller": "$controller", "tapped": False}:
        return ()
    if effect["tapped"] is not False:
        return ()
    return FIXED_TARGET_REANIMATION_CAPABILITY, MANA_VALUE_POWER_CAPABILITY, "target.revalidate_resolution"


def source_power_reanimation_runtime_dependencies(program):
    schema = program.target_schema
    claimed = RELATIVE_TARGET_REANIMATION_MECHANIC in program.coverage
    if not claimed and not (
        isinstance(schema, Mapping) and schema.get("predicate") in MANA_VALUE_POWER_PREDICATES
        and any(effect.get("op") == REANIMATE_OPERATION for effect in program.effects)
    ):
        return ()
    from ..reanimation_entry_counters import CONDITIONAL_REANIMATION_COUNTER_VERSION

    conditional = (len(program.effects) == 1
        and program.effects[0].get("schema_version") == CONDITIONAL_REANIMATION_COUNTER_VERSION)
    if conditional:
        from .reanimation_entry_counters import reanimation_entry_counter_node_capabilities
        required = reanimation_entry_counter_node_capabilities(
            effects=program.effects, target_schema=schema, mechanic_ids=program.coverage,
        )
    else:
        required = relative_target_reanimation_node_capabilities(
            effects=program.effects, target_schema=schema, mechanics=program.coverage,
        )
    scope = relative_reanimation_scope(schema)
    expected_event = {
        "optional_permanent": "creature.attacks",
        "mandatory_creature": "creature.attacks" if conditional else "step.begin",
        "another_creature": "creature.dies.self",
    }.get(scope)
    if not required or program.active_zone != "battlefield" or program.event != expected_event:
        raise ValueError("Relative reanimation requires its exact triggered source and graveyard target")
    if program.event == "creature.attacks" and program.event_condition != {"field": "card", "op": "eq", "value": "$source.ref"}:
        raise ValueError("Relative attack reanimation requires its own attack")
    return required


def _reanimation_followup_capabilities(effect, *, target_schema, mechanics):
    if REANIMATION_FOLLOWUP_MECHANIC not in mechanics:
        return ()
    try:
        followup = validate_reanimation_followup(effect)
    except (ValueError, TypeError):
        return ()
    if followup.exile_on_leave and (target_schema is None or "min" in target_schema):
        return ()
    base = {key: value for key, value in effect.items() if key not in {"schema_version", "followup"}}
    required = fixed_target_reanimation_node_capabilities(effects=(base,),
        target_schema=target_schema, mechanic_ids=mechanics)
    if not required:
        return ()
    return (*required, REANIMATION_FOLLOWUP_CAPABILITY, "target.revalidate_resolution",
        *battlefield_entry_followup_capabilities(followup))


__all__ = [
    "fixed_target_reanimation_covered_mechanics",
    "fixed_target_reanimation_node_capabilities",
]
