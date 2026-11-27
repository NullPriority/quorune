from __future__ import annotations

"""Closed target-predicate evaluation outside the engine facade."""

from typing import Any, Mapping, Protocol, Set

from .attachments import attached_object_identity
from .continuous_effect_model import Layer
from .enchant_public_state_model import OTHER_AURA_ATTACHED_PREDICATE
from .model import CardInstance, StackItem
from .public_characteristic_aggregates import reduce_public_characteristic_values
from .relative_power_target import (
    CREATURE_POWER_PREDICATE,
    GREATEST_CREATURE_POWER_PREDICATE,
    RELATIVE_POWER_TARGET_PREDICATES,
    RelativePowerTargetError,
    bound_relative_power_condition,
    current_effective_creature_power,
)
from .targets import CONTROLLER_ISLAND_TARGET_PREDICATE, ISLAND_COUNT_POWER_TARGET_PREDICATE, TargetGroup
from .entry_designations import CHOSEN_COLOR_TARGET_PREDICATE, CHOSEN_CREATURE_TYPE_TARGET_PREDICATE
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
                          StackSpellTargetPredicate.CONTROLLER_OR_CONTROLLED_PERMANENT,
                          StackSpellTargetPredicate.STACK_CONTROLLER_OR_CONTROLLED_CREATURE}
            and subject == controller
        )
    if predicate is StackSpellTargetPredicate.SOURCE:
        return subject.logical_object_id == source_incarnation
    if predicate in {StackSpellTargetPredicate.CONTROLLED_PERMANENT,
                     StackSpellTargetPredicate.CONTROLLER_OR_CONTROLLED_PERMANENT,
                     StackSpellTargetPredicate.INSTANT_OR_AURA_CONTROLLED_PERMANENT}:
        return subject.controller == controller
    if predicate in {StackSpellTargetPredicate.CONTROLLED_CREATURE,
                     StackSpellTargetPredicate.STACK_CONTROLLER_OR_CONTROLLED_CREATURE,
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
    StackSpellTargetPredicate.STACK_CONTROLLER_OR_CONTROLLED_CREATURE: frozenset({"creature"}),
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
    if not isinstance(item, StackItem):
        return False
    spell = row.get("category") == "spell" and item.kind in {"spell", "spell_copy"}
    ability = (
        predicate is StackSpellTargetPredicate.STACK_CONTROLLER_OR_CONTROLLED_CREATURE
        and row.get("category") == "ability"
        and item.kind in {"activated_ability", "triggered_ability"}
    )
    if not (spell or ability):
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


def materialize_greatest_power_target_rows(host: TargetPredicateHost, controller: str, group: TargetGroup, rows):
    if group.predicate != GREATEST_CREATURE_POWER_PREDICATE:
        return rows
    if (group.zones != ("battlefield",) or group.categories != ("permanent",)
            or group.types_any != ("creature",) or group.controller_relation not in {"any", "you"}
            or group.min_targets != 1 or group.max_targets != 1):
        raise TargetPredicateError("Greatest-power targeting requires one public creature domain")
    ranked = []
    try:
        for row in rows:
            card = row.get("card")
            if (not isinstance(card, CardInstance) or card.phased_out
                    or "creature" not in row.get("types", ())
                    or group.controller_relation == "you" and card.controller != controller):
                continue
            power = current_effective_creature_power(host, card)
            if power is not None:
                ranked.append((row, power))
    except RelativePowerTargetError as exc:
        raise TargetPredicateError(str(exc)) from exc
    if not ranked:
        return []
    maximum = reduce_public_characteristic_values(tuple(power for _, power in ranked), "maximum")
    # Protection and other target legality checks follow this public ranking.
    # A higher untargetable creature must still exclude a lower creature.
    return [{**row, GREATEST_CREATURE_POWER_PREDICATE: True}
            for row, power in ranked if power == maximum]


def _designation_target_matches(predicate, row, types, colors, chosen_type, chosen_color):
    if predicate == CHOSEN_COLOR_TARGET_PREDICATE:
        return row.get("category") == "permanent" and chosen_color in colors
    return chosen_type is not None and "creature" in types and chosen_type in row.get("subtypes", ())


def _ability_source_target_matches(predicate: str, row: Mapping[str, Any], source_incarnation: str | None) -> bool:
    item=row.get('stack_item');source_types=row.get('ability_source_types')
    if (row.get('zone')!='stack' or row.get('category')!='ability'
            or not isinstance(item,StackItem) or source_types is None):
        return False
    from .ability_source_information import (
        ACTIVATED_PUBLIC_PERMANENT_SOURCE_FILTER, ACTIVATED_PUBLIC_PERMANENT_SOURCE_TYPES,
        DIFFERENT_LEGENDARY_NONCOMMANDER_SOURCE_FILTER,
    )

    if predicate == DIFFERENT_LEGENDARY_NONCOMMANDER_SOURCE_FILTER:
        from .target_source import TargetingSource

        other = row.get("ability_source_identity")
        return bool(item.kind in {"activated_ability", "triggered_ability"} and isinstance(other, TargetingSource)
                    and other.is_commander is False and other.supertypes is not None and "legendary" in other.supertypes
                    and other.logical_object_id is not None and source_incarnation is not None
                    and other.logical_object_id != source_incarnation)
    if predicate == ACTIVATED_PUBLIC_PERMANENT_SOURCE_FILTER:
        return item.kind == "activated_ability" and bool(ACTIVATED_PUBLIC_PERMANENT_SOURCE_TYPES.intersection(source_types))
    if predicate=='activated_artifact_source':
        return item.kind=='activated_ability' and 'artifact' in source_types
    if predicate == 'artifact_ability_source':
        return item.kind in {'activated_ability','triggered_ability'} and 'artifact' in source_types
    if predicate == 'colorless_ability_source':
        source_colors = row.get('ability_source_colors')
        return item.kind in {'activated_ability','triggered_ability'} and source_colors is not None and not source_colors
    return item.kind in {'activated_ability','triggered_ability'} and 'creature' not in source_types


def _controlled_island_count(host: TargetPredicateHost, controller: str) -> int:
    player = host.state.players.get(controller)
    if player is None:
        return 0
    count = 0
    for object_id in player.zones["battlefield"]:
        land = host.state.cards.get(object_id)
        if land is None or land.zone != "battlefield" or land.phased_out or land.controller != controller:
            continue
        current = host._effective_card_data(land)
        types, subtypes, _ = host._type_parts(str(current.get("type_line") or ""))
        if "land" in types and "island" in subtypes:
            count += 1
    return count


def _island_qualified_target_matches(host, predicate, row, types, controller):
    card = row.get("card")
    if not isinstance(card, CardInstance) or row.get("category") != "permanent" or row.get("zone") != "battlefield" or "creature" not in types:
        return False
    if predicate == CONTROLLER_ISLAND_TARGET_PREDICATE:
        return _controlled_island_count(host, card.controller) > 0
    if controller not in host.state.players:
        return False
    from .object_query import exact_numeric_characteristic

    power = exact_numeric_characteristic(card, host._effective_card_data(card), "power")
    return power is not None and power <= _controlled_island_count(host, controller)


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
    source_chosen_creature_type: str | None = None,
    source_chosen_color: str | None = None,
) -> bool:
    """Evaluate the closed predicate vocabulary for one normalized target row."""

    predicate = group.predicate
    if not predicate:
        return True
    from .linked_ability_exile_model import LINKED_EXILE_COPY_TARGET_PREDICATE

    if predicate == LINKED_EXILE_COPY_TARGET_PREDICATE:
        from .rules.linked_exile_copy import linked_exile_copy_target_matches

        return linked_exile_copy_target_matches(
            row.get("card"), source_incarnation=source_incarnation,
            ability_pair=group.resolution_condition["ability_pair"],
        )
    if predicate == GREATEST_CREATURE_POWER_PREDICATE:
        return row.get(GREATEST_CREATURE_POWER_PREDICATE) is True
    if predicate in (CHOSEN_COLOR_TARGET_PREDICATE, CHOSEN_CREATURE_TYPE_TARGET_PREDICATE):
        return _designation_target_matches(predicate, row, types, colors, source_chosen_creature_type, source_chosen_color)
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
    if predicate in {CONTROLLER_ISLAND_TARGET_PREDICATE, ISLAND_COUNT_POWER_TARGET_PREDICATE}:
        return _island_qualified_target_matches(host, predicate, row, types, controller)
    if predicate == OTHER_AURA_ATTACHED_PREDICATE:
        return bool(
            source_ref and isinstance(card, CardInstance)
            and row.get("category") == "permanent" and "creature" in types
            and _other_aura_attached(host, card, source_ref)
        )
    from .ability_source_information import ABILITY_SOURCE_FILTERS
    if predicate in ABILITY_SOURCE_FILTERS:
        return _ability_source_target_matches(predicate, row, source_incarnation)
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
