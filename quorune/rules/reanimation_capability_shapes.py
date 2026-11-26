from __future__ import annotations

"""Capability closure for exact graveyard-to-battlefield effects."""

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
    REANIMATION_FOLLOWUP_VERSIONS, DelayedReanimationSacrifice,
    battlefield_entry_followup_capabilities, validate_reanimation_followup,
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
        covered = (*covered, SOURCE_GRAVEYARD_ATTACKING_ENTRY_MECHANIC)
    from .death_battlefield_return import DEATH_BATTLEFIELD_RETURN_CAPABILITY, DEATH_BATTLEFIELD_RETURN_MECHANIC

    if DEATH_BATTLEFIELD_RETURN_CAPABILITY in supplied:
        covered = (*covered, DEATH_BATTLEFIELD_RETURN_MECHANIC)
    from .ordered_graveyard_reanimation import (
        ORDERED_GRAVEYARD_REANIMATION_CAPABILITY, ORDERED_GRAVEYARD_REANIMATION_MECHANIC,
    )

    if ORDERED_GRAVEYARD_REANIMATION_CAPABILITY in supplied:
        covered = (*covered, ORDERED_GRAVEYARD_REANIMATION_MECHANIC)
    from .enchanted_departure_return import ENCHANTED_DEPARTURE_RETURN_CAPABILITY, ENCHANTED_DEPARTURE_RETURN_MECHANIC

    if ENCHANTED_DEPARTURE_RETURN_CAPABILITY in supplied:
        covered = (*covered, ENCHANTED_DEPARTURE_RETURN_MECHANIC)
    from .opposing_death_delayed_return import (
        OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY, OPPOSING_DEATH_DELAYED_RETURN_MECHANIC,
    )

    if OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY in supplied:
        covered = (*covered, OPPOSING_DEATH_DELAYED_RETURN_MECHANIC)
    return covered


def fixed_target_reanimation_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one closed graveyard-to-battlefield move."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    from .ordered_graveyard_reanimation import ORDERED_GRAVEYARD_REANIMATION_MECHANIC

    if ORDERED_GRAVEYARD_REANIMATION_MECHANIC in mechanics:
        return ordered_graveyard_reanimation_node_capabilities(effects, target_schema)
    from .death_battlefield_return import (
        DEATH_BATTLEFIELD_RETURN_DEPENDENCIES, DEATH_BATTLEFIELD_RETURN_MECHANIC, death_battlefield_return_effect,
    )

    from .enchanted_departure_return import ENCHANTED_DEPARTURE_RETURN_MECHANIC
    from .enchanted_departure_events import enchanted_departure_return_node_capabilities

    if ENCHANTED_DEPARTURE_RETURN_MECHANIC in mechanics:
        return enchanted_departure_return_node_capabilities(effects, target_schema)
    from .opposing_death_delayed_return import (
        OPPOSING_DEATH_DELAYED_RETURN_MECHANIC, opposing_death_delayed_return_node_capabilities,
    )

    if OPPOSING_DEATH_DELAYED_RETURN_MECHANIC in mechanics:
        return opposing_death_delayed_return_node_capabilities(effects, target_schema)
    if DEATH_BATTLEFIELD_RETURN_MECHANIC in mechanics:
        if target_schema is None and tuple(effects) == (death_battlefield_return_effect(),):
            return DEATH_BATTLEFIELD_RETURN_DEPENDENCIES
        return ()
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
            expected = event_battlefield_return_effect(tapped=effect['tapped'],
                entry_counter=effect['entry_counter'], controller=effect['controller'])
        except (ValueError, TypeError, KeyError):
            return ()
        if target_schema is not None or EVENT_BATTLEFIELD_RETURN_MECHANIC not in mechanics or dict(effect) != expected:
            return ()
        return (EVENT_BATTLEFIELD_RETURN_CAPABILITY, 'zone.change.destination_replacement',
            *(('attachment.reference.current_or_lki',) if effect['controller'] == '$controller' else ()),
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


def attached_event_battlefield_return_runtime_dependencies(program):
    from ..event_card_return import EVENT_BATTLEFIELD_RETURN_MECHANIC
    from .attachment_source_return import ATTACHMENT_DEATH_TRANSFORM_MECHANIC, attachment_source_return_runtime_dependencies
    from .effect_shape_walkers import nested_effects

    if not any(effect.get('op') == REANIMATE_OPERATION and effect.get('schema_version') == 2
               and effect.get('controller') == '$controller' for effect in nested_effects(program.effects)):
        return ()
    if ATTACHMENT_DEATH_TRANSFORM_MECHANIC in getattr(program, "coverage", ()):
        return attachment_source_return_runtime_dependencies(program)
    required = fixed_target_reanimation_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=(EVENT_BATTLEFIELD_RETURN_MECHANIC,))
    if (not required or program.active_zone != 'battlefield' or program.event != 'creature.dies'
            or program.event_condition != {'field': 'attachments', 'op': 'contains_any', 'value': ['$source.ref']}):
        raise ValueError('Controller return requires the source-bound attached creature death')
    return required


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


def ordered_graveyard_reanimation_node_capabilities(effects, target_schema):
    from .ordered_graveyard_reanimation import (
        ORDERED_GRAVEYARD_REANIMATION_CAPABILITY, validate_ordered_graveyard_reanimation,
    )

    if target_schema is not None or len(effects) != 1:
        return ()
    effect, = effects
    try:
        followup = validate_ordered_graveyard_reanimation(effect)
    except (KeyError, TypeError, ValueError):
        return ()
    return (
        ORDERED_GRAVEYARD_REANIMATION_CAPABILITY,
        "zone.graveyard.ordered_simultaneous_arrival",
        *battlefield_entry_followup_capabilities(followup),
    )


def ordered_graveyard_reanimation_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects
    from .ordered_graveyard_reanimation import (
        ORDERED_GRAVEYARD_REANIMATION_MECHANIC, is_ordered_graveyard_reanimation,
    )

    if (ORDERED_GRAVEYARD_REANIMATION_MECHANIC not in program.coverage
            and not any(is_ordered_graveyard_reanimation(effect) for effect in nested_effects(program.effects))):
        return ()
    required = ordered_graveyard_reanimation_node_capabilities(program.effects, program.target_schema)
    if (not required or program.event != "resolve" or program.active_zone != "stack"
            or program.event_condition is not None or program.cost_schema is not None or program.handlers):
        raise ValueError("Ordered graveyard return requires its exact untargeted resolving spell")
    return required


def _reanimation_followup_capabilities(effect, *, target_schema, mechanics):
    if REANIMATION_FOLLOWUP_MECHANIC not in mechanics:
        return ()
    try:
        followup = validate_reanimation_followup(effect)
    except (ValueError, TypeError):
        return ()
    if isinstance(followup, DelayedReanimationSacrifice):
        from ..replacement.immutable import FrozenMap, thaw_value

        if (thaw_value(FrozenMap(target_schema or {})) != followup.target_schema()
                or type(target_schema.get("count")) is not int):
            return ()
    elif followup.exile_on_leave and (target_schema is None or "min" in target_schema):
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
