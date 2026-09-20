from __future__ import annotations

"""Closed public-action and result predicates for typed event triggers."""

import re
from typing import Any, Mapping

from .fixed_public_event_trigger_bindings import (
    FixedPublicEventBindingSpec,
    _all_conditions,
    _spec,
)


_PUBLIC_ATTACK_BATCH_TRIGGER = re.compile(
    r"^Whenever you attack(?: with (?P<count>two|three) or more creatures)?, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_CONTROLLER_CYCLE_TRIGGER = re.compile(
    r"^Whenever you cycle a card, (?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_SACRIFICE_TRIGGER = re.compile(
    r"^Whenever (?P<actor>you|an opponent|a player) "
    r"(?P<verb>sacrifice|sacrifices) "
    r"(?P<another>another )?"
    r"(?P<object>a Clue|a Food|a Blood token|a token|a land|a creature|"
    r"an artifact|an enchantment|a permanent|an artifact or creature|"
    r"another creature or artifact), (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_SACRIFICE_TRIGGER = re.compile(
    r"^When you sacrifice this (?P<object>Aura|artifact|creature), "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_DISCARD_TRIGGER = re.compile(
    r"^Whenever (?P<actor>you|an opponent|a player) "
    r"(?P<verb>discard|discards) "
    r"(?P<object>a card|a land card|a creature card|a nonland card), "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_GRAVEYARD_DEPARTURE_TRIGGER = re.compile(
    r"^Whenever one or more (?P<cards>cards|creature cards|artifact cards|"
    r"artifact and/or creature cards) leave your graveyard"
    r"(?P<turn> during your turn)?, (?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_TOKEN_ENTRY_TRIGGER = re.compile(
    r"^Whenever one or more tokens you control enter, (?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_TOKEN_LEAVE_TRIGGER = re.compile(
    r"^Whenever a token you control leaves the battlefield, (?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_CREATURE_LEAVE_TRIGGER = re.compile(
    r"^Whenever another creature you control leaves the battlefield, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_CREATURE_OR_ARTIFACT_GRAVEYARD_TRIGGER = re.compile(
    r"^Whenever another (?P<order>creature or artifact|artifact or creature) "
    r"you control is put into a graveyard from the battlefield, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_NONTOKEN_GRAVEYARD_TRIGGER = re.compile(
    r"^Whenever a nontoken (?P<kind>creature is put into your graveyard|"
    r"artifact you control is put into a graveyard) from the battlefield, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_NONTOKEN_DRAGON_ENTRY_TRIGGER = re.compile(
    r"^Whenever another nontoken Dragon you control enters, (?P<body>.+)$",
    re.IGNORECASE,
)
_LAND_ENTRY_DURING_TURN_TRIGGER = re.compile(
    r"^Whenever a land enters during your turn, (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_LAND_UNTAPPED_ENTRY_TRIGGER = re.compile(
    r"^When this land enters untapped, (?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_DAMAGE_BATCH_TRIGGER = re.compile(
    r"^Whenever one or more (?P<kind>creatures|artifact creatures) you "
    r"control deal combat damage to a player, (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_EXTENDED_DAMAGE_TRIGGER = re.compile(
    r"^Whenever this creature deals combat damage to a player or "
    r"(?P<recipient>planeswalker|battle), (?P<body>.+)$",
    re.IGNORECASE,
)
_OPPONENT_NONCOMBAT_DAMAGE_TRIGGER = re.compile(
    r"^Whenever an opponent is dealt noncombat damage, (?P<body>.+)$",
    re.IGNORECASE,
)


def _actor_condition(
    actor: str,
    *,
    field: str,
) -> Mapping[str, Any] | None:
    relation = " ".join(actor.casefold().split())
    if relation == "you":
        return {
            "field": field,
            "op": "eq",
            "value": "$source.controller",
        }
    if relation == "an opponent":
        return {
            "field": field,
            "op": "ne",
            "value": "$source.controller",
        }
    if relation == "a player":
        return None
    raise ValueError("Public action actors require a closed controller relation")


def _public_sacrifice_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    source = _SOURCE_SACRIFICE_TRIGGER.fullmatch(material_line)
    if source is not None:
        kind = source.group("object").casefold()
        return _spec(
            "permanent.sacrificed.self",
            f"source_{kind}_sacrificed",
            source.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=(
                {"field": "types", "op": "contains_any", "value": [kind]}
                if kind != "aura"
                else {
                    "field": "subtypes",
                    "op": "contains_any",
                    "value": ["aura"],
                }
            ),
        )
    match = _PUBLIC_SACRIFICE_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    actor = " ".join(match.group("actor").casefold().split())
    raw_object = " ".join(match.group("object").casefold().split())
    exclude_source = bool(match.group("another")) or raw_object.startswith(
        "another "
    )
    subject = raw_object.removeprefix("a ").removeprefix("an ")
    subject = subject.removeprefix("another ")
    conditions: list[Mapping[str, Any] | None] = [
        _actor_condition(actor, field="previous_controller"),
        {"field": "card", "op": "ne", "value": "$source.ref"}
        if exclude_source
        else None,
    ]
    if subject in {"clue", "food"}:
        conditions.append(
            {
                "field": "subtypes",
                "op": "contains_any",
                "value": [subject],
            }
        )
    elif subject == "blood token":
        conditions.extend(
            (
                {
                    "field": "subtypes",
                    "op": "contains_any",
                    "value": ["blood"],
                },
                {"field": "token", "op": "truthy", "value": True},
            )
        )
    elif subject == "token":
        conditions.append(
            {"field": "token", "op": "truthy", "value": True}
        )
    elif subject in {"artifact or creature", "creature or artifact"}:
        conditions.append(
            {
                "field": "types",
                "op": "contains_any",
                "value": ["artifact", "creature"],
            }
        )
        subject = "artifact_or_creature"
    elif subject in {"artifact", "creature", "enchantment", "land"}:
        conditions.append(
            {
                "field": "types",
                "op": "contains_any",
                "value": [subject],
            }
        )
    elif subject != "permanent":
        return None
    actor_variant = {
        "you": "controller",
        "an opponent": "opponent",
        "a player": "player",
    }[actor]
    return _spec(
        "permanent.sacrificed",
        f"{actor_variant}_sacrifices_{subject.replace(' ', '_')}",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=_all_conditions(*conditions),
    )


def _public_discard_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = _PUBLIC_DISCARD_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    actor = " ".join(match.group("actor").casefold().split())
    subject = " ".join(match.group("object").casefold().split())
    subject = subject.removeprefix("a ").removeprefix("an ")
    conditions: list[Mapping[str, Any] | None] = [
        _actor_condition(actor, field="previous_controller"),
    ]
    if subject in {"land card", "creature card"}:
        conditions.append(
            {
                "field": "types",
                "op": "contains_any",
                "value": [subject.removesuffix(" card")],
            }
        )
    elif subject == "nonland card":
        conditions.append(
            {
                "not": {
                    "field": "types",
                    "op": "contains_any",
                    "value": ["land"],
                }
            }
        )
    elif subject != "card":
        return None
    actor_variant = {
        "you": "controller",
        "an opponent": "opponent",
        "a player": "player",
    }[actor]
    return _spec(
        "card.discarded",
        f"{actor_variant}_discards_{subject.removesuffix(' card')}",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=_all_conditions(*conditions),
    )


def _graveyard_departure_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = _PUBLIC_GRAVEYARD_DEPARTURE_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    card_kind = " ".join(match.group("cards").casefold().split())
    type_condition: Mapping[str, Any] | None = None
    if card_kind == "creature cards":
        type_condition = {
            "field": "types",
            "op": "contains_any",
            "value": ["creature"],
        }
    elif card_kind == "artifact cards":
        type_condition = {
            "field": "types",
            "op": "contains_any",
            "value": ["artifact"],
        }
    elif card_kind == "artifact and/or creature cards":
        type_condition = {
            "field": "types",
            "op": "contains_any",
            "value": ["artifact", "creature"],
        }
    during_turn = match.group("turn") is not None
    variant_kind = card_kind.replace(" and/or ", "_or_").replace(" ", "_")
    return _spec(
        "card.leave_graveyard",
        "one_or_more_controller_"
        f"{variant_kind}_leave_graveyard"
        f"{'_during_turn' if during_turn else ''}",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=_all_conditions(
            {"field": "owner", "op": "eq", "value": "$source.controller"},
            type_condition,
            {
                "field": "source_controller_is_active_player",
                "op": "eq",
                "value": True,
            }
            if during_turn
            else None,
        ),
    )


def _public_token_zone_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    token_entry = _CONTROLLED_TOKEN_ENTRY_TRIGGER.fullmatch(material_line)
    if token_entry is not None:
        return _spec(
            "permanent.enter",
            "one_or_more_controlled_tokens_enter",
            token_entry.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {
                    "field": "controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {"field": "token", "op": "truthy", "value": True},
            ),
        )
    token_leave = _CONTROLLED_TOKEN_LEAVE_TRIGGER.fullmatch(material_line)
    if token_leave is not None:
        return _spec(
            "permanent.leave",
            "controlled_token_leaves",
            token_leave.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {
                    "field": "previous_controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {"field": "token", "op": "truthy", "value": True},
            ),
        )
    return None


def _public_controlled_departure_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    creature_leave = _CONTROLLED_CREATURE_LEAVE_TRIGGER.fullmatch(material_line)
    if creature_leave is not None:
        return _spec(
            "permanent.leave",
            "another_controlled_creature_leaves",
            creature_leave.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {
                    "field": "previous_controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {
                    "field": "types",
                    "op": "contains_any",
                    "value": ["creature"],
                },
                {"field": "card", "op": "ne", "value": "$source.ref"},
            ),
        )
    graveyard = _CONTROLLED_CREATURE_OR_ARTIFACT_GRAVEYARD_TRIGGER.fullmatch(
        material_line
    )
    if graveyard is not None:
        return _spec(
            "permanent.graveyard",
            "another_controlled_creature_or_artifact_graveyard",
            graveyard.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {
                    "field": "previous_controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {
                    "field": "types",
                    "op": "contains_any",
                    "value": ["creature", "artifact"],
                },
                {"field": "card", "op": "ne", "value": "$source.ref"},
            ),
        )
    nontoken = _NONTOKEN_GRAVEYARD_TRIGGER.fullmatch(material_line)
    if nontoken is not None:
        creature = nontoken.group("kind").casefold().startswith("creature")
        return _spec(
            "creature.dies" if creature else "artifact.graveyard",
            (
                "nontoken_creature_enters_controller_graveyard"
                if creature
                else "controlled_nontoken_artifact_graveyard"
            ),
            nontoken.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {"field": "owner", "op": "eq", "value": "$source.controller"}
                if creature
                else {
                    "field": "previous_controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {"field": "token", "op": "falsy", "value": True},
            ),
        )
    return None


def _public_entry_zone_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    dragon = _CONTROLLED_NONTOKEN_DRAGON_ENTRY_TRIGGER.fullmatch(material_line)
    if dragon is not None:
        return _spec(
            "permanent.enter",
            "another_controlled_nontoken_dragon_enters",
            dragon.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {
                    "field": "controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {
                    "field": "subtypes",
                    "op": "contains_any",
                    "value": ["dragon"],
                },
                {"field": "token", "op": "falsy", "value": True},
                {"field": "card", "op": "ne", "value": "$source.ref"},
            ),
        )
    during_turn = _LAND_ENTRY_DURING_TURN_TRIGGER.fullmatch(material_line)
    if during_turn is not None:
        return _spec(
            "land.enter",
            "land_enters_during_controller_turn",
            during_turn.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition={
                "field": "source_controller_is_active_player",
                "op": "eq",
                "value": True,
            },
        )
    source_land = _SOURCE_LAND_UNTAPPED_ENTRY_TRIGGER.fullmatch(material_line)
    if source_land is not None:
        return _spec(
            "permanent.enter.self",
            "source_land_enters_untapped",
            source_land.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition={"field": "tapped", "op": "falsy", "value": True},
        )
    return None


def _public_zone_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    return (
        _public_token_zone_spec(material_line)
        or _public_controlled_departure_spec(material_line)
        or _public_entry_zone_spec(material_line)
        or _graveyard_departure_spec(material_line)
        or _public_sacrifice_spec(material_line)
        or _public_discard_spec(material_line)
    )


def _public_damage_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    batch = _PUBLIC_DAMAGE_BATCH_TRIGGER.fullmatch(material_line)
    if batch is not None:
        kind = batch.group("kind").casefold()
        return _spec(
            "damage.dealt",
            (
                "one_or_more_controlled_artifact_creatures_combat_damage_player"
                if kind.startswith("artifact")
                else "one_or_more_controlled_creatures_combat_damage_player"
            ),
            batch.group("body"),
            "fixed-counter-public-damage-trigger-v1",
            "trigger-event-normalized-damage",
            condition=_all_conditions(
                {
                    "field": "source_controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {
                    "field": "source_types",
                    "op": "contains_any",
                    "value": ["creature"],
                },
                {
                    "field": "source_types",
                    "op": "contains_any",
                    "value": ["artifact"],
                }
                if kind.startswith("artifact")
                else None,
                {"field": "target_kind", "op": "eq", "value": "player"},
                {"field": "combat", "op": "truthy", "value": True},
            ),
        )
    extended = _SOURCE_EXTENDED_DAMAGE_TRIGGER.fullmatch(material_line)
    if extended is not None:
        recipient = extended.group("recipient").casefold()
        return _spec(
            "damage.dealt",
            f"source_combat_damage_player_or_{recipient}",
            extended.group("body"),
            "fixed-counter-public-damage-trigger-v1",
            "trigger-event-normalized-damage",
            condition=_all_conditions(
                {"field": "source", "op": "eq", "value": "$source.ref"},
                {"field": "combat", "op": "truthy", "value": True},
                {
                    "any": [
                        {
                            "field": "target_kind",
                            "op": "eq",
                            "value": "player",
                        },
                        {
                            "all": [
                                {
                                    "field": "target_kind",
                                    "op": "eq",
                                    "value": "permanent",
                                },
                                {
                                    "field": "target_types",
                                    "op": "contains_any",
                                    "value": [recipient],
                                },
                            ]
                        },
                    ]
                },
            ),
        )
    opponent = _OPPONENT_NONCOMBAT_DAMAGE_TRIGGER.fullmatch(material_line)
    if opponent is None:
        return None
    return _spec(
        "damage.dealt",
        "opponent_dealt_noncombat_damage",
        opponent.group("body"),
        "fixed-counter-public-damage-trigger-v1",
        "trigger-event-normalized-damage",
        condition=_all_conditions(
            {"field": "target_kind", "op": "eq", "value": "player"},
            {
                "field": "target",
                "op": "ne",
                "value": "$source.controller",
            },
            {"field": "combat", "op": "falsy", "value": True},
        ),
    )


def _public_attack_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = _PUBLIC_ATTACK_BATCH_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    minimum = {"two": 2, "three": 3}.get(
        str(match.group("count") or "").casefold()
    )
    return _spec(
        "creature.attacks",
        (
            f"controller_attack_batch_at_least_{minimum}"
            if minimum is not None
            else "controller_attack_batch"
        ),
        match.group("body"),
        "fixed-counter-public-attack-trigger-v1",
        "trigger-event-normalized-public-action",
        condition=_all_conditions(
            {
                "field": "controller",
                "op": "eq",
                "value": "$source.controller",
            },
            {
                "field": "attacker_count",
                "op": "gte",
                "value": minimum,
            }
            if minimum is not None
            else None,
        ),
    )


def _public_cycle_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = _PUBLIC_CONTROLLER_CYCLE_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    return _spec(
        "card.cycled",
        "controller_cycles",
        match.group("body"),
        "fixed-counter-public-cycle-trigger-v1",
        "trigger-event-normalized-public-action",
        condition={
            "field": "player",
            "op": "eq",
            "value": "$source.controller",
        },
    )


def fixed_public_action_event_binding_spec(
    material_line: str,
    *,
    card_name: str | None = None,
) -> FixedPublicEventBindingSpec | None:
    """Parse one bounded action/result occurrence without compiling its body."""

    del card_name
    return (
        _public_attack_spec(material_line)
        or _public_damage_spec(material_line)
        or _public_zone_spec(material_line)
        or _public_cycle_spec(material_line)
    )


__all__ = ["fixed_public_action_event_binding_spec"]
