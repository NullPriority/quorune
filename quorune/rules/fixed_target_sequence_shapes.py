"""Capability shapes for ordered instructions sharing one fixed target."""

from typing import Any, Iterable, Mapping, Sequence

from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from ..keyword_counters import keyword_counter_mechanic
from ..zone_object_keyword_model import ZONE_OBJECT_KEYWORDS
from .permanent_predicate_capability_shapes import (
    direct_target_predicate_capabilities, fixed_counter_target_schema_is_closed,
)


_FIXED_TARGET_SEQUENCE_MECHANIC = "fixed-target-effect-sequence"
_FIXED_TARGET_SEQUENCE_KEYWORDS = frozenset(value.title() for value in FIXED_CHARACTERISTIC_KEYWORDS)
_FIXED_TARGET_ZONE_OBJECT_KEYWORDS = frozenset(value.title() for value in ZONE_OBJECT_KEYWORDS)


def fixed_target_effect_sequence_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership for one closed target-threaded counter sequence."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if any(
        effect.get("op") == "grant_zone_object_keyword"
        for effect in effects
    ):
        if not {
            _FIXED_TARGET_SEQUENCE_MECHANIC,
            "cr-115-targets",
            "cr-122-counters",
            "cr-611-continuous-effects",
        }.issubset(mechanics) or len(effects) != 2:
            return ()
        counter, grant = effects
        if (
            set(counter) != {"op", "card", "counter", "amount", "source"}
            or counter.get("op") != "place_counters"
            or counter.get("card") != "$target.0"
            or type(counter.get("counter")) is not str
            or not counter.get("counter")
            or type(counter.get("amount")) is not int
            or counter.get("amount", 0) <= 0
            or counter.get("source") != "$source"
            or not fixed_counter_target_schema_is_closed(target_schema)
        ):
            return ()
        keyword = grant.get("keyword")
        keyword_mechanic = keyword_counter_mechanic(keyword)
        if (
            set(grant) != {"op", "card", "keyword"}
            or grant.get("op") != "grant_zone_object_keyword"
            or grant.get("card") != "$target.0"
            or keyword not in _FIXED_TARGET_ZONE_OBJECT_KEYWORDS
            or keyword_mechanic is None
            or keyword_mechanic not in mechanics
        ):
            return ()
        counter_mechanic = keyword_counter_mechanic(counter.get("counter"))
        if counter_mechanic is not None and counter_mechanic not in mechanics:
            return ()
        return (
            "continuous.resolution.fixed_keyword_zone_object",
            *(("counter.characteristic.keyword",) if counter_mechanic else ()),
            "counter.producer.fixed_effect",
            "resolution.effect_sequence.fixed_target",
            *direct_target_predicate_capabilities(target_schema),
            "target.revalidate_resolution",
        )
    if not {
        _FIXED_TARGET_SEQUENCE_MECHANIC,
        "cr-115-targets",
        "cr-122-counters",
        "cr-611-continuous-effects",
    }.issubset(mechanics) or not 2 <= len(effects) <= 5:
        return ()
    if not fixed_counter_target_schema_is_closed(target_schema):
        return ()
    assert target_schema is not None
    if len(effects) == 5 and (
        target_schema.get("controller_relation") != "you"
        or tuple(target_schema.get("types_any", ())) != ("creature",)
        or effects[0].get("op") != "place_counters"
        or any(effect.get("op") != "grant_keyword_until_end_of_turn" for effect in effects[1:])
    ):
        return ()
    counter_count = 0
    keyword_counter = False
    characteristic_count = 0
    granted_keywords: set[str] = set()
    for effect in effects:
        operation = effect.get("op")
        if operation == "place_counters":
            if (
                set(effect) != {"op", "card", "counter", "amount", "source"}
                or effect.get("card") != "$target.0"
                or type(effect.get("counter")) is not str
                or not effect.get("counter")
                or type(effect.get("amount")) is not int
                or effect.get("amount", 0) <= 0
                or effect.get("source") != "$source"
            ):
                return ()
            counter_mechanic = keyword_counter_mechanic(effect.get("counter"))
            if counter_mechanic is not None:
                if counter_mechanic not in mechanics:
                    return ()
                keyword_counter = True
            counter_count += 1
            continue
        if operation == "modify_stats_until_end_of_turn":
            if (
                set(effect) != {"op", "card", "power", "toughness"}
                or effect.get("card") != "$target.0"
                or type(effect.get("power")) is not int
                or type(effect.get("toughness")) is not int
                or (effect.get("power") == 0 and effect.get("toughness") == 0)
            ):
                return ()
            characteristic_count += 1
            continue
        if operation == "grant_keyword_until_end_of_turn":
            keyword = effect.get("keyword")
            if (
                set(effect) != {"op", "card", "keyword"}
                or effect.get("card") != "$target.0"
                or keyword not in _FIXED_TARGET_SEQUENCE_KEYWORDS
                or keyword in granted_keywords
            ):
                return ()
            granted_keywords.add(keyword)
            characteristic_count += 1
            continue
        return ()
    if counter_count != 1 or characteristic_count < 1:
        return ()
    return (
        "continuous.resolution.fixed_characteristics_until_end_of_turn",
        *(("counter.characteristic.keyword",) if keyword_counter else ()),
        "counter.producer.fixed_effect",
        "resolution.effect_sequence.fixed_target",
        *direct_target_predicate_capabilities(target_schema),
        "target.revalidate_resolution",
    )


def extended_fixed_target_sequence_runtime_dependencies(program):
    """Revalidate the extended sequence and its direct ownership during binding."""
    if len(program.effects) <= 4 or (
        _FIXED_TARGET_SEQUENCE_MECHANIC not in program.coverage
        and program.provenance.get("template_id") != "fixed-target-counter-characteristics-sequence-v1"
    ):
        return ()
    required = fixed_target_effect_sequence_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required or not set(required).issubset(program.capability_dependencies):
        raise ValueError("The four-keyword target sequence requires its complete direct capabilities")
    return required
