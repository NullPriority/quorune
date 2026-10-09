from __future__ import annotations

"""Closed ordinary and named-relative counter placement capability shapes."""

from typing import Any, Iterable, Mapping, Sequence
from ..attachment_references import AttachmentReferenceError, AttachmentReferenceSpec
from ..affected_permanents import AffectedPermanentSetError, AffectedPermanentSetSpec, PermanentControllerRelation as AffectedControllerRelation
from ..compiler.counter_placement_templates import fixed_counter_set_spec_is_closed
from ..compiler.fixed_source_effect_sequences import SOURCE_ZONE_OBJECT
from ..counter_names import is_existing_counter_amount
from ..keyword_counters import keyword_counter_mechanic
from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities, fixed_counter_target_schema_is_closed, public_state_query_capabilities

_NAMED_COUNTER_CAPABILITY = "counter.producer.named_doubling"
_NAMED_COUNTER_MECHANIC = "named-counter-doubling"


def counter_placement_covered_mechanics(supplied):
    placements={"counter.producer.fixed_effect", "counter.producer.fixed_permanent_group_effect",
        "counter.producer.fixed_multikind_effect", "counter.producer.fixed_attached_effect",
        "counter.producer.fixed_permanent_set_effect", "counter.producer.fixed_permanent_target_set_effect",
        _NAMED_COUNTER_CAPABILITY}
    covered={"cr-122-counters"} if placements.intersection(supplied) else set()
    if _NAMED_COUNTER_CAPABILITY in supplied:
        covered.add(_NAMED_COUNTER_MECHANIC)
    return covered


def _placement_capabilities(base, *, effects, target_schema, mechanic_ids):
    mechanics=tuple(mechanic_ids)
    relative=len(effects)==1 and is_existing_counter_amount(effects[0].get("amount"))
    named=_NAMED_COUNTER_MECHANIC in mechanics
    shared=bool({"bound-effect-program","closed-effect-program"}.intersection(mechanics))
    if (relative and not named) or (named and not relative and not shared):
        return ()
    projected=({**effects[0],"amount":1},) if relative else effects
    caps=base(effects=projected,target_schema=target_schema,mechanic_ids=mechanics)
    return (_NAMED_COUNTER_CAPABILITY,*caps) if relative and caps else caps


def fixed_counter_placement_node_capabilities(*, effects, target_schema, mechanic_ids):
    return _placement_capabilities(_base_fixed_counter_placement_node_capabilities,
        effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)


def fixed_counter_placement_set_node_capabilities(*, effects, target_schema, mechanic_ids):
    return _placement_capabilities(_base_fixed_counter_placement_set_node_capabilities,
        effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)


def _base_fixed_counter_placement_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for one closed fixed counter placement."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-122-counters" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card", "counter", "amount", "source"}
        or effect.get("op") != "place_counters"
        or type(effect.get("counter")) is not str
        or not effect.get("counter")
        or type(effect.get("amount")) is not int
        or effect.get("amount", 0) <= 0
        or effect.get("source") != "$source"
    ):
        return ()
    counter_mechanic = keyword_counter_mechanic(effect.get("counter"))
    if counter_mechanic is not None and counter_mechanic not in mechanics:
        return ()
    characteristic_capabilities = (
        ("counter.characteristic.keyword",)
        if counter_mechanic is not None
        else ()
    )
    if target_schema is None and effect.get("card") in ("$source", SOURCE_ZONE_OBJECT):
        return ("counter.producer.fixed_effect", *characteristic_capabilities)
    if target_schema is None and isinstance(effect.get("card"), Mapping):
        try:
            AttachmentReferenceSpec.from_dict(effect["card"])
        except (AttachmentReferenceError, TypeError):
            return ()
        return (
            "counter.producer.fixed_attached_effect",
            'counter.producer.fixed_effect',
            *characteristic_capabilities,
        )
    if (
        "cr-115-targets" in mechanics
        and effect.get("card") == "$target.0"
        and fixed_counter_target_schema_is_closed(target_schema)
    ):
        assert target_schema is not None
        target_capabilities = direct_target_predicate_capabilities(target_schema)
        return (
            "counter.producer.fixed_effect",
            *characteristic_capabilities,
            *target_capabilities,
            "target.revalidate_resolution",
        )
    return ()


def _base_fixed_counter_placement_set_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for one closed affected-set placement."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-122-counters" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "source", "set", "counter", "amount"}
        or effect.get("op") != "place_counters_on_set"
        or effect.get("source") != "$source"
        or type(effect.get("counter")) is not str
        or not str(effect.get("counter") or "").strip()
        or type(effect.get("amount")) is not int
        or effect.get("amount", 0) <= 0
    ):
        return ()
    counter_mechanic = keyword_counter_mechanic(effect.get("counter"))
    if counter_mechanic is not None and counter_mechanic not in mechanics:
        return ()
    characteristic_capabilities = (
        ("counter.characteristic.keyword",)
        if counter_mechanic is not None
        else ()
    )
    try:
        spec = AffectedPermanentSetSpec.from_dict(effect.get("set"))
    except (AffectedPermanentSetError, TypeError):
        return ()
    if not fixed_counter_set_spec_is_closed(spec):
        return ()
    state_capabilities = public_state_query_capabilities(
        spec.query.state_predicate
    )
    if spec.controller_relation is AffectedControllerRelation.TARGET_PLAYER:
        if (
            "cr-115-targets" not in mechanics
            or dict(target_schema or {})
            not in {
                "any": {
                    "zones": ["player"],
                    "categories": ["player"],
                    "count": 1,
                    "player_relation": "any",
                },
                "opponent": {
                    "zones": ["player"],
                    "categories": ["player"],
                    "count": 1,
                    "player_relation": "opponent",
                },
            }.values()
        ):
            return ()
        return (
            "counter.producer.fixed_permanent_set_effect",
            *characteristic_capabilities,
            *state_capabilities,
            "target.revalidate_resolution",
        )
    if target_schema is not None:
        return ()
    return (
        "counter.producer.fixed_permanent_set_effect",
        *characteristic_capabilities,
        *state_capabilities,
    )
