from __future__ import annotations

"""Closed ordinary and named-relative counter placement capability shapes."""

from typing import Any, Iterable, Mapping, Sequence
from ..attachment_references import (
    ATTACHMENT_INFORMATION_CAPABILITY, AttachmentReferenceError, AttachmentReferenceSpec,
)
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
        _NAMED_COUNTER_CAPABILITY,'counter.producer.fixed_event_object','counter.producer.all_kind_doubling'}
    covered={"cr-122-counters"} if placements.intersection(supplied) else set()
    if _NAMED_COUNTER_CAPABILITY in supplied:
        covered.add(_NAMED_COUNTER_MECHANIC)
    if 'counter.producer.fixed_event_object' in supplied:covered.add('fixed-entry-event-object-counter')
    if 'counter.producer.fixed_public_source_event' in supplied:covered.add('fixed-public-event-source-counter')
    if 'counter.producer.fixed_public_target_event' in supplied:covered.add('fixed-public-event-target-counter')
    from .aggregated_arrival_counters import MATCHED_ARRIVAL_CAPABILITY, MATCHED_ARRIVAL_MECHANIC
    if MATCHED_ARRIVAL_CAPABILITY in supplied:
        covered.add(MATCHED_ARRIVAL_MECHANIC)
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
    from ..reflexive_trigger_model import SOURCE_COUNTER_REFLEXIVE_FIELD, THIRD_SOURCE_TRIGGER_FIELD
    from .target_opponent_counter_choice import (
        is_opponent_counter_choice, target_opponent_counter_choice_node_capabilities,
    )

    if len(effects) == 1 and effects[0].get("amount") == "$event.matched_arrival_count":
        from .aggregated_arrival_counters import matching_arrival_counter_capabilities

        return matching_arrival_counter_capabilities(effects, target_schema, mechanic_ids)
    if len(effects) == 1 and is_opponent_counter_choice(effects[0]):
        return target_opponent_counter_choice_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
    if len(effects) == 1 and THIRD_SOURCE_TRIGGER_FIELD in effects[0]:
        from .third_resolved_source_trigger import third_source_trigger_node_capabilities

        return third_source_trigger_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
    if len(effects) == 1 and SOURCE_COUNTER_REFLEXIVE_FIELD in effects[0]:
        from .source_counter_reflexive import source_counter_reflexive_node_capabilities

        return source_counter_reflexive_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
    return _placement_capabilities(_base_fixed_counter_placement_node_capabilities,
        effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)


def fixed_counter_placement_set_node_capabilities(*, effects, target_schema, mechanic_ids):
    from .attack_batches import DECLARED_ATTACK_COUNTER_FIELD, declared_attack_counter_effect_capabilities

    if len(effects) == 1 and DECLARED_ATTACK_COUNTER_FIELD in effects[0]:
        if target_schema is not None or "cr-122-counters" not in mechanic_ids:
            return ()
        return declared_attack_counter_effect_capabilities(effects[0])
    from ..counter_placement_sets import (
        PAIRED_PLANESWALKER_LOYALTY_FIELD, SOURCE_AND_CONTROLLED_SUBTYPE_FIELD,
        controlled_nonflying_counter_payload_dependencies, paired_planeswalker_loyalty_dependencies,
        source_and_controlled_subtype_dependencies,
    )

    if len(effects) == 1:
        try:
            nonflying = controlled_nonflying_counter_payload_dependencies(effects[0])
        except ValueError:
            return ()
        if nonflying:
            return nonflying if target_schema is None and {"cr-122-counters", "flying"} <= set(mechanic_ids) else ()

    if len(effects) == 1 and SOURCE_AND_CONTROLLED_SUBTYPE_FIELD in effects[0]:
        if target_schema is not None or "cr-122-counters" not in mechanic_ids:
            return ()
        try:
            return source_and_controlled_subtype_dependencies(effects[0])
        except (TypeError, ValueError):
            return ()
    if len(effects) == 1 and PAIRED_PLANESWALKER_LOYALTY_FIELD in effects[0]:
        if target_schema is not None or "cr-122-counters" not in mechanic_ids:
            return ()
        try:
            return paired_planeswalker_loyalty_dependencies(effects[0])
        except (TypeError, ValueError):
            return ()
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
    if target_schema is None and type(effect.get('card')) is str and effect['card'] in {'$event.entry_object','$event.counter_object'} and 'fixed-entry-event-object-counter' in mechanics:
        return ('counter.producer.fixed_event_object','counter.producer.fixed_effect',*characteristic_capabilities)
    if target_schema is None and effect.get("card") in ("$source", SOURCE_ZONE_OBJECT):
        public_source = (('counter.producer.fixed_public_source_event',)
                         if 'fixed-public-event-source-counter' in mechanics else ())
        return (*public_source, "counter.producer.fixed_effect", *characteristic_capabilities)
    if target_schema is None and isinstance(effect.get("card"), Mapping):
        try:
            reference = AttachmentReferenceSpec.from_dict(effect["card"])
        except (AttachmentReferenceError, TypeError):
            return ()
        if reference.required_color is not None and (effect["counter"] != "+1/+1" or effect["amount"] != 1):
            return ()
        return (
            "counter.producer.fixed_attached_effect",
            'counter.producer.fixed_effect',
            *characteristic_capabilities,
            *((ATTACHMENT_INFORMATION_CAPABILITY,) if reference.required_color is not None else ()),
        )
    if (
        "cr-115-targets" in mechanics
        and effect.get("card") == "$target.0"
        and fixed_counter_target_schema_is_closed(target_schema)
    ):
        assert target_schema is not None
        target_capabilities = direct_target_predicate_capabilities(target_schema)
        return (
            *(('counter.producer.fixed_public_target_event',)
              if 'fixed-public-event-target-counter' in mechanics else ()),
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
