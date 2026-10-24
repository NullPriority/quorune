from __future__ import annotations

"""Canonical precedence between typed programs and reviewed compatibility views."""

from typing import Any, Iterable

from ..carddb import CardRecord
from ..rules.event_subscriptions import FixedEventSubscriptionSet
from ..semantics import SemanticProgram
from ..object_predicate import ObjectQuerySpec
from ..creature_subtypes import canonical_creature_subtype
from ..library_search_model import (
    FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
    FixedCountedLibrarySearchTemplate,
)


_SOURCE_REF_EVENT_CONDITION = {
    "field": "card",
    "op": "eq",
    "value": "$source.ref",
}


def _canonical_effects(program: SemanticProgram) -> list[dict[str, Any]]:
    effects = [dict(effect) for effect in program.effects]
    for effect in effects:
        if effect.get("op") == "draw" and "private" not in effect:
            effect["private"] = True
    return effects


def _same_trigger_body(
    generated: SemanticProgram,
    reviewed: SemanticProgram,
) -> bool:
    return bool(
        generated.active_zone == reviewed.active_zone
        and _canonical_effects(generated) == _canonical_effects(reviewed)
        and generated.handlers == reviewed.handlers
        and generated.target_schema == reviewed.target_schema
        and generated.cost_schema == reviewed.cost_schema
        and generated.destination == reviewed.destination
        and generated.requires_arbiter == reviewed.requires_arbiter
        and reviewed.event_condition is None
    )


def _reviewed_self_event_matches_subscription(
    record: CardRecord,
    reviewed_event: str,
    subscription_event: str,
) -> bool:
    if not reviewed_event.endswith(".self"):
        return False
    normalized = reviewed_event.removesuffix(".self")
    if normalized == subscription_event:
        return True
    printed_types = {
        value.casefold()
        for value in record.type_line.partition("—")[0].split()
    }
    return bool(
        "artifact" in printed_types
        and normalized == "artifact.graveyard"
        and subscription_event == "permanent.graveyard"
    )


def shadowed_reviewed_multi_event_keys(
    record: CardRecord,
    generated_programs: Iterable[SemanticProgram],
    reviewed_programs: Iterable[SemanticProgram],
) -> set[str]:
    """Identify complete split compatibility views owned by one typed ability."""

    reviewed_values = tuple(reviewed_programs)
    shadowed: set[str] = set()
    for generated in generated_programs:
        subscriptions = FixedEventSubscriptionSet.from_condition(
            generated.event_condition
        )
        if subscriptions is None or generated.trust_level != "trusted":
            continue
        if any(
            subscription.condition is None
            or dict(subscription.condition) != _SOURCE_REF_EVENT_CONDITION
            for subscription in subscriptions.subscriptions
        ):
            continue
        body_matches = tuple(
            reviewed
            for reviewed in reviewed_values
            if reviewed.trust_level == "trusted"
            and _same_trigger_body(generated, reviewed)
        )
        matched: list[SemanticProgram] = []
        for subscription in subscriptions.subscriptions:
            candidates = tuple(
                reviewed
                for reviewed in body_matches
                if _reviewed_self_event_matches_subscription(
                    record,
                    reviewed.event,
                    subscription.event,
                )
            )
            if len(candidates) != 1:
                matched = []
                break
            matched.append(candidates[0])
        if len({program.key for program in matched}) == len(
            subscriptions.subscriptions
        ):
            shadowed.update(program.key for program in matched)
    return shadowed


def shadowed_reviewed_counted_search_keys(
    record: CardRecord,
    generated_programs: Iterable[SemanticProgram],
    reviewed_programs: Iterable[SemanticProgram],
) -> set[str]:
    """Prefer a current bound search only over the same reviewed instruction.

    Persisted reviewed programs remain compatibility payloads. This precedence
    applies while assembling current programs, with matching source hashes,
    events, targets, costs and effect semantics rather than a card identity.
    """
    reviewed = {program.key: program for program in reviewed_programs}
    shadowed = set()
    for generated in generated_programs:
        old = reviewed.get(generated.key)
        if (
            old is None or generated.trust_level != "trusted"
            or old.trust_level != "trusted"
            or FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID not in generated.capability_dependencies
        ):
            continue
        if not generated.capability_closure or generated.capability_closure.get("trusted") is not True:
            continue
        if any(
            getattr(generated, field) != getattr(old, field)
            for field in (
                "active_zone", "event", "event_condition", "target_schema",
                "cost_schema", "handlers", "destination", "requires_arbiter",
            )
        ):
            continue
        if any(
            not generated.provenance.get(field)
            or generated.provenance[field] != old.provenance.get(field)
            for field in ("source_oracle_hash", "source_rulings_hash")
        ):
            continue
        if len(generated.effects) != len(old.effects):
            continue
        equal = True
        for current, previous in zip(generated.effects, old.effects):
            current = dict(current)
            previous = dict(previous)
            if current.get("op") != "search" or current.get("schema_version") != 2:
                equal = False
                break
            try:
                FixedCountedLibrarySearchTemplate.from_effect(current)
            except (ValueError, TypeError, KeyError):
                equal = False
                break
            current.pop("schema_version")
            if current.get("shuffle_before_placement") is False:
                current.pop("shuffle_before_placement")
            if previous.get("searching_player") == "$controller":
                previous.pop("searching_player")
            if current != previous:
                equal = False
                break
        if equal:
            shadowed.add(old.key)
    return shadowed


def shadowed_reviewed_event_return_keys(
    record: CardRecord,
    generated_programs: Iterable[SemanticProgram],
    reviewed_programs: Iterable[SemanticProgram],
) -> set[str]:
    """Prefer an incarnation-bound return over its same-source legacy body."""
    from ..event_card_return import EVENT_CARD_RETURN_CAPABILITY, event_card_return_effect
    reviewed_values = tuple(reviewed_programs)
    shadowed: set[str] = set()
    for generated in generated_programs:
        if (
            generated.trust_level != "trusted"
            or EVENT_CARD_RETURN_CAPABILITY not in generated.capability_dependencies
            or not generated.capability_closure
            or generated.capability_closure.get("trusted") is not True
            or tuple(dict(effect) for effect in generated.effects) != (event_card_return_effect(),)
            or generated.event not in {"permanent.graveyard.self", "creature.dies.self"}
            or generated.event_condition is not None
        ):
            continue
        for old in reviewed_values:
            if old.trust_level != "trusted" or len(old.effects) != 1:
                continue
            effect = dict(old.effects[0])
            effect.pop("reason", None)
            if effect != {"op": "move", "card": "$source", "destination": "hand"}:
                continue
            if any(getattr(old, field) != getattr(generated, field) for field in (
                "active_zone", "event_condition", "target_schema", "cost_schema",
                "handlers", "destination", "requires_arbiter",
            )):
                continue
            if not _reviewed_self_event_matches_subscription(record, old.event, generated.event.removesuffix(".self")):
                continue
            if any(not generated.provenance.get(field) or generated.provenance[field] != old.provenance.get(field)
                   for field in ("source_oracle_hash", "source_rulings_hash")):
                continue
            shadowed.add(old.key)
    return shadowed


def shadowed_reviewed_subtype_anthem_keys(
    record: CardRecord, generated_programs: Iterable[SemanticProgram],
    reviewed_programs: Iterable[SemanticProgram],
) -> set[str]:
    """Replace one source-matched legacy anthem with its corrected typed query."""
    reviewed = tuple(reviewed_programs)
    shadowed: set[str] = set()
    for generated in generated_programs:
        if (generated.oracle_id != record.oracle_id or generated.trust_level != "trusted"
                or not generated.capability_closure
                or generated.capability_closure.get("trusted") is not True
                or len(generated.handlers) != 1):
            continue
        handler = generated.handlers[0]
        if handler.get("handler_id") != "continuous.anthem.fixed-query.v2":
            continue
        condition = handler.get("condition", {})
        if condition.get("exclude_source") is not False:
            continue
        query = ObjectQuerySpec.from_dict(condition.get("predicate"))
        subtypes = query.subtypes_all
        if (not subtypes or any(canonical_creature_subtype(value) != value for value in subtypes)
                or query != ObjectQuerySpec(zones=("battlefield",), subtypes_all=subtypes)):
            continue
        for old in reviewed:
            if (old.oracle_id != record.oracle_id or old.trust_level != "trusted"
                    or str(old.provenance.get("authored_by", "")).startswith("oracle-ir-v")
                    or len(old.handlers) != 1):
                continue
            if any(getattr(old, field) != getattr(generated, field) for field in (
                "active_zone", "event", "event_condition", "effects", "target_schema",
                "cost_schema", "destination", "requires_arbiter",
            )):
                continue
            if any(not generated.provenance.get(field)
                   or generated.provenance[field] != old.provenance.get(field)
                   for field in ("source_oracle_hash", "source_rulings_hash")):
                continue
            expected = {
                "handler_id": "continuous.anthem.power_toughness.v1", "schema_version": 1,
                "event": handler.get("event"),
                "condition": {"target_controller": condition.get("target_controller"),
                              "target_subtypes_all": list(subtypes)},
                "modifier": dict(handler.get("modifier", {})),
            }
            if dict(old.handlers[0]) == expected:
                shadowed.add(old.key)
    return shadowed


def shadowed_reviewed_program_keys(
    record: CardRecord,
    generated_programs: Iterable[SemanticProgram],
    reviewed_programs: Iterable[SemanticProgram],
    *, capability_registry=None, capability_profile='traditional', oracle_ir=None,
) -> set[str]:
    """Apply shared current-program precedence in both assembly paths."""
    generated = tuple(generated_programs)
    reviewed = tuple(reviewed_programs)
    from .alternative_price_overlay import shadowed_reviewed_zero_price_keys
    from .current_view_equivalence import shadowed_reviewed_fixed_search_keys,shadowed_reviewed_upkeep_type_count_keys
    from .kicker_view_equivalence import shadowed_reviewed_kicker_target_keys
    from .modal_catalog_equivalence import shadowed_reviewed_modal_catalog_keys
    from .resolution_mana_overlay import shadowed_reviewed_resolution_mana_keys
    from .additional_token_equivalence import shadowed_reviewed_additional_token_keys
    from .empty_permanent_equivalence import shadowed_reviewed_empty_permanent_keys
    from .trigger_view_equivalence import shadowed_reviewed_trigger_view_keys
    return shadowed_reviewed_multi_event_keys(
        record, generated, reviewed,
    ) | shadowed_reviewed_counted_search_keys(
        record, generated, reviewed,
    ) | shadowed_reviewed_event_return_keys(record, generated, reviewed) | shadowed_reviewed_subtype_anthem_keys(
        record, generated, reviewed,
    ) | shadowed_reviewed_zero_price_keys(record, generated, reviewed) | shadowed_reviewed_fixed_search_keys(
        record,generated,reviewed) | shadowed_reviewed_upkeep_type_count_keys(record,generated,reviewed) | shadowed_reviewed_kicker_target_keys(record,generated,reviewed) | shadowed_reviewed_modal_catalog_keys(record,generated,reviewed) | shadowed_reviewed_resolution_mana_keys(record,generated,reviewed) | shadowed_reviewed_additional_token_keys(record,generated,reviewed) | shadowed_reviewed_empty_permanent_keys(record,generated,reviewed,capability_registry=capability_registry,capability_profile=capability_profile,oracle_ir=oracle_ir) | shadowed_reviewed_trigger_view_keys(record,generated,reviewed,capability_registry=capability_registry,capability_profile=capability_profile)


def retire_reviewed_programs(record, generated_programs, registry, *,
        capability_registry=None, capability_profile='traditional') -> set[str]:
    """Commit the shared precedence decision through the registry's own port."""
    keys=shadowed_reviewed_program_keys(record,generated_programs,
        registry.programs_for_oracle(record.oracle_id),
        capability_registry=capability_registry,capability_profile=capability_profile)
    for key in sorted(keys):registry.remove(key)
    return keys


__all__ = [
    "shadowed_reviewed_multi_event_keys", "shadowed_reviewed_counted_search_keys",
    "shadowed_reviewed_program_keys",
    "retire_reviewed_programs",
]
