from __future__ import annotations

"""Closed target-predicate evaluation outside the engine facade."""

from typing import Any, Mapping, Protocol, Set

from .attachments import attached_object_identity
from .continuous_effect_model import Layer
from .enchant_public_state_model import OTHER_AURA_ATTACHED_PREDICATE
from .model import CardInstance, StackItem
from .relative_power_target import (
    CREATURE_POWER_PREDICATE,
    RELATIVE_POWER_TARGET_PREDICATES,
    RelativePowerTargetError,
    bound_relative_power_condition,
    current_effective_creature_power,
)
from .targets import TargetGroup
from .stack_spell_targeting import STACK_SPELL_TARGET_PREDICATES, StackSpellTargetPredicate


class TargetPredicateError(ValueError):
    """A target predicate or its typed condition is unsupported or malformed."""


_EXILE_ZONE = "exile"
_VOID_COUNTER = "void"


class TargetPredicateHost(Protocol):
    state: Any

    def _numeric_stat(self, object_id: str, stat: str) -> int: ...

    def _effective_card_data(self, card: Any, *, maximum_layer: Layer | None = None) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


def _relative_power_matches(
    host: TargetPredicateHost,
    group: TargetGroup,
    row: Mapping[str, Any],
) -> bool:
    card = row.get("card")
    if not isinstance(card, CardInstance):
        return False
    try:
        condition = bound_relative_power_condition(group.predicate, group.resolution_condition)
        source = host.state.cards.get(condition.source.object_id)
        if (
            condition.kind == CREATURE_POWER_PREDICATE
            and source is not None
            and source.zone == "battlefield"
            and source.logical_object_id == condition.source.logical_object_id
            and source.phased_out
        ):
            raise TargetPredicateError(
                "Relative-power source phasing requires a typed LKI transition"
            )
        source_is_current = bool(
            source is not None
            and source.zone == "battlefield"
            and not source.phased_out
            and source.logical_object_id == condition.source.logical_object_id
        )
        current_source_power = (
            current_effective_creature_power(host, source)
            if source_is_current
            else None
        )
        if condition.kind != CREATURE_POWER_PREDICATE:
            return condition.permits_mana_value(
                mana_value=row.get("mana_value"), current_source_power=current_source_power,
                use_last_known=not source_is_current,
            )
        return condition.permits(
            target_power=host._numeric_stat(card.object_id, "power"),
            current_source_power=current_source_power,
            use_last_known=not source_is_current,
        )
    except RelativePowerTargetError as exc:
        raise TargetPredicateError(str(exc)) from exc


def _other_aura_attached(
    host: TargetPredicateHost,
    target: CardInstance,
    source_ref: str,
) -> bool:
    for object_id in target.attachments:
        attachment = host.state.cards.get(object_id)
        if attachment is None or attachment.ref == source_ref:
            continue
        identity = attached_object_identity(host.state.cards, attachment)
        if identity is None or identity.object_id != target.object_id:
            continue
        data = host._effective_card_data(attachment, maximum_layer=Layer.COLOR)
        types, subtypes, _ = host._type_parts(str(data.get("type_line") or ""))
        if "enchantment" in types and "aura" in subtypes:
            return True
    return False


def _spell_target_subject_matches(
    host: TargetPredicateHost,
    predicate: StackSpellTargetPredicate,
    subject: str | CardInstance,
    *,
    controller: str | None,
    source_incarnation: str | None,
) -> bool:
    if isinstance(subject, str):
        return predicate is StackSpellTargetPredicate.PLAYER or (
            predicate in {StackSpellTargetPredicate.CONTROLLER,
                          StackSpellTargetPredicate.CONTROLLER_OR_CONTROLLED_PERMANENT}
            and subject == controller
        )
    if predicate is StackSpellTargetPredicate.SOURCE:
        return subject.logical_object_id == source_incarnation
    if predicate in {StackSpellTargetPredicate.CONTROLLED_PERMANENT,
                     StackSpellTargetPredicate.CONTROLLER_OR_CONTROLLED_PERMANENT,
                     StackSpellTargetPredicate.INSTANT_OR_AURA_CONTROLLED_PERMANENT}:
        return subject.controller == controller
    if predicate in {StackSpellTargetPredicate.CONTROLLED_CREATURE,
                     StackSpellTargetPredicate.CONTROLLED_ARTIFACT_OR_CREATURE} and subject.controller != controller:
        return False
    required = _SPELL_SUBJECT_TYPES.get(predicate)
    if required is None:
        return False
    effective = host._effective_card_data(subject)
    subject_types, _, _ = host._type_parts(str(effective.get("type_line") or ""))
    return bool(required.intersection(subject_types))


_SPELL_SUBJECT_TYPES = {
    StackSpellTargetPredicate.CREATURE: frozenset({"creature"}),
    StackSpellTargetPredicate.ENCHANTMENT: frozenset({"enchantment"}),
    StackSpellTargetPredicate.CONTROLLED_CREATURE: frozenset({"creature"}),
    StackSpellTargetPredicate.CONTROLLED_ARTIFACT_OR_CREATURE: frozenset({"artifact", "creature"}),
}


def _spell_targets_matching_subject(
    host: TargetPredicateHost,
    predicate: StackSpellTargetPredicate,
    row: Mapping[str, Any],
    *,
    types: Set[str],
    controller: str | None,
    source_incarnation: str | None,
) -> bool:
    from .selection.target_query import current_spell_target_subjects

    item = row.get("stack_item")
    if (row.get("category") != "spell" or not isinstance(item, StackItem)
            or item.kind not in {"spell", "spell_copy"}):
        return False
    if predicate is StackSpellTargetPredicate.INSTANT_OR_AURA_CONTROLLED_PERMANENT and (
        "instant" not in types and not ("enchantment" in types and "aura" in row.get("subtypes", ()))
    ):
        return False
    return any(
        _spell_target_subject_matches(
            host, predicate, subject, controller=controller, source_incarnation=source_incarnation,
        )
        for subject in current_spell_target_subjects(host, item)
    )


def target_predicate_matches(
    host: TargetPredicateHost,
    group: TargetGroup,
    row: Mapping[str, Any],
    *,
    types: Set[str],
    supertypes: Set[str],
    colors: Set[str],
    derived: Mapping[str, bool],
    source_ref: str | None = None,
    controller: str | None = None,
    source_incarnation: str | None = None,
) -> bool:
    """Evaluate the closed predicate vocabulary for one normalized target row."""

    predicate = group.predicate
    if not predicate:
        return True
    if predicate in STACK_SPELL_TARGET_PREDICATES:
        return _spell_targets_matching_subject(
            host, StackSpellTargetPredicate(predicate), row, types=types,
            controller=controller, source_incarnation=source_incarnation,
        )
    from .stack_target_change_model import SINGLE_ANNOUNCED_STACK_TARGET, matches_single_announced_stack_target

    if predicate == SINGLE_ANNOUNCED_STACK_TARGET:
        item = row.get("stack_item")
        return row.get("zone") == "stack" and isinstance(item, StackItem) and matches_single_announced_stack_target(item)
    card = row.get("card")
    if predicate == OTHER_AURA_ATTACHED_PREDICATE:
        return bool(
            source_ref and isinstance(card, CardInstance)
            and row.get("category") == "permanent" and "creature" in types
            and _other_aura_attached(host, card, source_ref)
        )
    from .ability_source_information import ABILITY_SOURCE_FILTERS
    if predicate in ABILITY_SOURCE_FILTERS:
        item=row.get('stack_item');source_types=row.get('ability_source_types')
        if (row.get('zone')!='stack' or row.get('category')!='ability'
                or not isinstance(item,StackItem) or source_types is None):
            return False
        if predicate=='activated_artifact_source':
            return item.kind=='activated_ability' and 'artifact' in source_types
        if predicate == 'artifact_ability_source':
            return item.kind in {'activated_ability','triggered_ability'} and 'artifact' in source_types
        if predicate == 'colorless_ability_source':
            source_colors = row.get('ability_source_colors')
            return item.kind in {'activated_ability','triggered_ability'} and source_colors is not None and not source_colors
        return item.kind in {'activated_ability','triggered_ability'} and 'creature' not in source_types
    if predicate == 'activated_or_triggered_ability':
        item = row.get('stack_item')
        return row.get('zone') == 'stack' and isinstance(item, StackItem) and item.kind in {'activated_ability','triggered_ability'}
    if predicate in {'triggered_ability_or_colorless_spell','ability_or_noncreature_spell',
            'ability_or_legendary_spell','blue_instant_spell'}:
        item = row.get('stack_item')
        if row.get('zone') != 'stack' or not isinstance(item, StackItem):
            return False
        spell = row.get('category') == 'spell' and item.kind in {'spell','spell_copy'}
        triggered = row.get('category') == 'ability' and item.kind == 'triggered_ability'
        ability = row.get('category') == 'ability' and item.kind in {'activated_ability','triggered_ability'}
        if predicate == 'triggered_ability_or_colorless_spell':
            return triggered or spell and not colors
        if predicate == 'ability_or_noncreature_spell':
            return ability or spell and 'creature' not in types
        if predicate == 'ability_or_legendary_spell':
            return ability or spell and 'legendary' in supertypes
        return spell and 'U' in colors and 'instant' in types
    if predicate == "artifact_or_enchantment_or_nonbasic_land":
        return bool(
            derived["artifact"]
            or derived["enchantment"]
            or (derived["land"] and "basic" not in supertypes)
        )
    if predicate == "permanent_card":
        return bool(
            types.intersection(
                {
                    "artifact",
                    "battle",
                    "creature",
                    "enchantment",
                    "land",
                    "planeswalker",
                }
            )
        )
    if predicate == "damageable":
        return bool(
            row["category"] == "player"
            or types.intersection({"battle", "creature", "planeswalker"})
        )
    if predicate == "player_or_planeswalker":
        return row["category"] == "player" or "planeswalker" in types
    if predicate == "void_counter":
        return bool(
            isinstance(card, CardInstance)
            and row.get("zone") == _EXILE_ZONE
            and int(card.counters.get(_VOID_COUNTER, 0)) > 0
        )
    if predicate == "artifact_source":
        return bool(
            row.get("zone") == "stack"
            and "artifact" in set(row.get("stack_source_types") or ())
        )
    if predicate == "triggered_ability":
        stack_item = row.get("stack_item")
        return bool(
            isinstance(stack_item, StackItem)
            and stack_item.kind == "triggered_ability"
        )
    if predicate == "activated_ability":
        stack_item = row.get("stack_item")
        return bool(
            isinstance(stack_item, StackItem)
            and stack_item.kind == "activated_ability"
        )
    if predicate == "nonblue_spell":
        return row.get("category") == "spell" and "U" not in colors
    if predicate == "not_source_attachment":
        source = next(
            (
                candidate
                for candidate in host.state.cards.values()
                if candidate.ref == source_ref
                and candidate.zone == "battlefield"
                and not candidate.phased_out
            ),
            None,
        )
        return bool(
            source is not None
            and isinstance(card, CardInstance)
            and source.attached_to != card.object_id
        )
    if predicate in RELATIVE_POWER_TARGET_PREDICATES:
        return _relative_power_matches(host, group, row)
    raise TargetPredicateError(f"Unsupported target predicate {predicate!r}")


__all__ = [
    "TargetPredicateError",
    "TargetPredicateHost",
    "target_predicate_matches",
]
