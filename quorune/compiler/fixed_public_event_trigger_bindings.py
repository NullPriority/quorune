from __future__ import annotations

"""Closed public occurrence grammar for fixed typed event-effect triggers."""

from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping

from ..creature_subtypes import canonical_creature_subtype


_PUBLIC_DAMAGE_TRIGGER = re.compile(
    r"^Whenever (?P<subject>"
    r"a creature you control|an artifact creature you control|"
    r"a [A-Z][A-Za-z'-]* you control|"
    r"[A-Z][A-Za-z', -]+) "
    r"(?P<event>deals combat damage to a player|"
    r"deals combat damage to an opponent|deals damage to a player|"
    r"deals damage to an opponent), (?P<body>.+)$",
)
_PUBLIC_ATTACK_TRIGGER = re.compile(
    r"^Whenever (?P<subject>this Vehicle|a creature|"
    r"a creature you control|a creature with flying) "
    r"attacks(?P<alone> alone)?(?: (?P<recipient>you or a planeswalker "
    r"you control))?, (?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_BLOCK_TRIGGER = re.compile(
    r"^Whenever (?P<subject>this creature|a creature you control with defender) "
    r"(?P<event>blocks|becomes blocked)"
    r"(?P<blocked> a creature with flying)?, (?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_CYCLE_TRIGGER = re.compile(
    r"^(?:When you cycle this card|Whenever a player cycles a card), "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_FACE_UP_TRIGGER = re.compile(
    r"^Whenever a permanent is turned face up, (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_FACE_UP_TRIGGER = re.compile(
    r"^(?:When|Whenever) this "
    r"(?:artifact|creature|enchantment|Equipment|land|permanent) "
    r"is turned face up, (?P<body>.+)$",
    re.IGNORECASE,
)
_OPPONENT_CARD_DRAW_TRIGGER = re.compile(
    r"^Whenever an opponent draws a card, (?P<body>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FixedPublicEventBindingSpec:
    """One parser-owned public occurrence subscription."""

    event: str
    variant: str
    body: str
    template_id: str
    mechanic: str
    condition: Mapping[str, Any] | None = None
    active_zone: str = "battlefield"

    def __post_init__(self) -> None:
        for field in ("event", "variant", "body", "template_id", "mechanic"):
            if not isinstance(getattr(self, field), str) or not getattr(
                self, field
            ):
                raise ValueError("Public event binding identities must be nonempty")
        if self.active_zone not in {"battlefield", "hand"}:
            raise ValueError("Public event bindings require a closed active zone")


def _all_conditions(
    *conditions: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    values = [dict(value) for value in conditions if value is not None]
    if not values:
        return None
    return values[0] if len(values) == 1 else {"all": values}


def _spec(
    event: str,
    variant: str,
    body: str,
    template_id: str,
    mechanic: str,
    *,
    condition: Mapping[str, Any] | None = None,
    active_zone: str = "battlefield",
) -> FixedPublicEventBindingSpec:
    return FixedPublicEventBindingSpec(
        event=event,
        variant=variant,
        body=body,
        template_id=template_id,
        mechanic=mechanic,
        condition=condition,
        active_zone=active_zone,
    )


def _artifact_graveyard_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = re.fullmatch(
        r"Whenever an artifact is put into "
        r"(?P<owner>an opponent's|a) graveyard from the battlefield, "
        r"(?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    opponent = match.group("owner").casefold().startswith("an opponent")
    return _spec(
        "artifact.graveyard",
        "artifact_opponent_graveyard" if opponent else "artifact_any_graveyard",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=(
            {"field": "owner", "op": "ne", "value": "$source.controller"}
            if opponent
            else None
        ),
    )


def _land_entry_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    match = re.fullmatch(
        r"Whenever a land(?: (?P<relation>an opponent controls))? enters, "
        r"(?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    opponent = match.group("relation") is not None
    return _spec(
        "land.enter",
        "opponent_land" if opponent else "any_land",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=(
            {"field": "controller", "op": "ne", "value": "$source.controller"}
            if opponent
            else {"field": "types", "op": "contains_any", "value": ["land"]}
        ),
    )


def _power_entry_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    match = re.fullmatch(
        r"Whenever (?P<include>this creature or )?"
        r"(?P<article>another|a) creature you control with power "
        r"(?P<power>[1-9][0-9]*) or greater (?P<event>enters|dies), "
        r"(?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    event = match.group("event").casefold()
    exclude_source = (
        match.group("article").casefold() == "another"
        and match.group("include") is None
    )
    return _spec(
        "creature.dies" if event == "dies" else "creature.enter",
        f"controlled_creature_power_gte_{match.group('power')}_{event}",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=_all_conditions(
            {"field": "controller", "op": "eq", "value": "$source.controller"},
            {"field": "power", "op": "gte", "value": int(match.group("power"))},
            {"field": "card", "op": "ne", "value": "$source.ref"}
            if exclude_source
            else None,
        ),
    )


def _source_or_artifact_death_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = re.fullmatch(
        r"Whenever this creature or another artifact creature dies, "
        r"(?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return _spec(
        "creature.dies",
        "source_or_artifact_creature_dies",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition={
            "any": [
                {"field": "card", "op": "eq", "value": "$source.ref"},
                {"field": "types", "op": "contains_any", "value": ["artifact"]},
            ]
        },
    )


def _subtype_graveyard_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = re.fullmatch(
        r"Whenever (?P<article>another|a) (?P<token>nontoken )?"
        r"(?P<subtype>[A-Z][A-Za-z'-]*) "
        r"(?:(?P<control>you control) )?"
        r"(?P<verb>dies|is put into (?:your|a) graveyard from the battlefield), "
        r"(?P<body>.+)",
        material_line,
    )
    if match is None:
        return None
    subtype = canonical_creature_subtype(match.group("subtype"))
    if subtype is None:
        return None
    dies = match.group("verb").casefold() == "dies"
    return _spec(
        "creature.dies" if dies else "permanent.graveyard",
        f"subtype_{subtype}_{'dies' if dies else 'graveyard'}",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=_all_conditions(
            {"field": "subtypes", "op": "contains_any", "value": [subtype]},
            {
                "field": "previous_controller",
                "op": "eq",
                "value": "$source.controller",
            }
            if match.group("control") is not None
            else None,
            {"field": "owner", "op": "eq", "value": "$source.controller"}
            if "your graveyard" in match.group("verb").casefold()
            else None,
            {"field": "token", "op": "falsy", "value": True}
            if match.group("token") is not None
            else None,
            {"field": "card", "op": "ne", "value": "$source.ref"}
            if match.group("article").casefold() == "another"
            else None,
        ),
    )


def _named_source_graveyard_spec(
    material_line: str,
    *,
    card_name: str | None,
) -> FixedPublicEventBindingSpec | None:
    if not card_name:
        return None
    match = re.fullmatch(
        rf"When {re.escape(card_name)} is put into a graveyard from the "
        r"battlefield, (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return _spec(
        "permanent.graveyard.self",
        "named_source_graveyard",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
    )


def _public_zone_spec(
    material_line: str,
    *,
    card_name: str | None,
) -> FixedPublicEventBindingSpec | None:
    parsers: tuple[Callable[[str], FixedPublicEventBindingSpec | None], ...] = (
        _artifact_graveyard_spec,
        _land_entry_spec,
        _power_entry_spec,
        _source_or_artifact_death_spec,
        _subtype_graveyard_spec,
    )
    for parser in parsers:
        spec = parser(material_line)
        if spec is not None:
            return spec
    return _named_source_graveyard_spec(material_line, card_name=card_name)


def _damage_subject_condition(
    subject: str,
    *,
    card_name: str | None,
) -> Mapping[str, Any] | None:
    if subject in {"a creature you control", "an artifact creature you control"}:
        return _all_conditions(
            {
                "field": "source_controller",
                "op": "eq",
                "value": "$source.controller",
            },
            {"field": "source_types", "op": "contains_any", "value": ["artifact"]}
            if subject.startswith("an artifact")
            else None,
        )
    if subject.startswith("a ") and subject.endswith(" you control"):
        subtype = canonical_creature_subtype(
            subject.removeprefix("a ").removesuffix(" you control")
        )
        if subtype is None:
            return None
        return _all_conditions(
            {
                "field": "source_controller",
                "op": "eq",
                "value": "$source.controller",
            },
            {
                "field": "source_subtypes",
                "op": "contains_any",
                "value": [subtype],
            },
        )
    if card_name and subject == " ".join(
        card_name.split(",", 1)[0].casefold().split()
    ):
        return {"field": "source", "op": "eq", "value": "$source.ref"}
    return None


def _public_damage_spec(
    material_line: str,
    *,
    card_name: str | None,
) -> FixedPublicEventBindingSpec | None:
    match = _PUBLIC_DAMAGE_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    subject = " ".join(match.group("subject").casefold().split())
    subject_condition = _damage_subject_condition(subject, card_name=card_name)
    if subject_condition is None:
        return None
    event = match.group("event").casefold()
    return _spec(
        "damage.dealt",
        "public_" + event.replace(" ", "_"),
        match.group("body"),
        "fixed-counter-public-damage-trigger-v1",
        "trigger-event-normalized-damage",
        condition=_all_conditions(
            subject_condition,
            {"field": "target_kind", "op": "eq", "value": "player"},
            {"field": "combat", "op": "truthy", "value": True}
            if "combat damage" in event
            else None,
            {"field": "target", "op": "ne", "value": "$source.controller"}
            if event.endswith("opponent")
            else None,
        ),
    )


def _public_attack_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    match = _PUBLIC_ATTACK_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    subject = " ".join(match.group("subject").casefold().split())
    return _spec(
        "creature.attacks",
        "public_attack_" + subject.replace(" ", "_"),
        match.group("body"),
        "fixed-counter-public-attack-trigger-v1",
        "trigger-event-normalized-public-action",
        condition=_all_conditions(
            {"field": "card", "op": "eq", "value": "$source.ref"}
            if subject == "this vehicle"
            else None,
            {"field": "controller", "op": "eq", "value": "$source.controller"}
            if subject == "a creature you control"
            else None,
            {"field": "keywords", "op": "contains_any", "value": ["flying"]}
            if subject == "a creature with flying"
            else None,
            {"field": "attacking_alone", "op": "truthy", "value": True}
            if match.group("alone") is not None
            else None,
            {
                "field": "defending_player",
                "op": "eq",
                "value": "$source.controller",
            }
            if match.group("recipient") is not None
            else None,
        ),
    )


def _public_block_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    match = _PUBLIC_BLOCK_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    subject = " ".join(match.group("subject").casefold().split())
    event = match.group("event").casefold()
    blocked_flying = match.group("blocked") is not None
    if blocked_flying and (subject != "this creature" or event != "blocks"):
        return None
    return _spec(
        "creature.blocks" if event == "blocks" else "creature.becomes_blocked",
        (
            "this_creature_blocks_flying"
            if blocked_flying
            else f"{subject.replace(' ', '_')}_{event.replace(' ', '_')}"
        ),
        match.group("body"),
        "fixed-counter-public-block-trigger-v1",
        "trigger-event-normalized-public-action",
        condition=_all_conditions(
            {"field": "card", "op": "eq", "value": "$source.ref"}
            if subject == "this creature"
            else None,
            {"field": "controller", "op": "eq", "value": "$source.controller"}
            if subject != "this creature"
            else None,
            {"field": "keywords", "op": "contains_any", "value": ["defender"]}
            if "defender" in subject
            else None,
            {
                "field": "blocked_attacker_keywords",
                "op": "contains_any",
                "value": ["flying"],
            }
            if blocked_flying
            else None,
        ),
    )


def _public_misc_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    cycle = _PUBLIC_CYCLE_TRIGGER.fullmatch(material_line)
    if cycle is not None:
        source_self = material_line.casefold().startswith("when you cycle this")
        return _spec(
            "card.cycled.self" if source_self else "card.cycled",
            "source_cycles" if source_self else "player_cycles",
            cycle.group("body"),
            "fixed-counter-public-cycle-trigger-v1",
            "trigger-event-normalized-public-action",
            active_zone="hand" if source_self else "battlefield",
        )
    source_face_up = _SOURCE_FACE_UP_TRIGGER.fullmatch(material_line)
    if source_face_up is not None:
        return _spec(
            "permanent.turned_face_up",
            "source_turned_face_up",
            source_face_up.group("body"),
            "fixed-counter-public-face-up-trigger-v1",
            "trigger-event-normalized-public-action",
            condition={
                "field": "card",
                "op": "eq",
                "value": "$source.ref",
            },
        )
    face_up = _PUBLIC_FACE_UP_TRIGGER.fullmatch(material_line)
    if face_up is not None:
        return _spec(
            "permanent.turned_face_up",
            "permanent_turned_face_up",
            face_up.group("body"),
            "fixed-counter-public-face-up-trigger-v1",
            "trigger-event-normalized-public-action",
        )
    opponent_draw = _OPPONENT_CARD_DRAW_TRIGGER.fullmatch(material_line)
    if opponent_draw is not None:
        return _spec(
            "card.drawn",
            "opponent_card_draw",
            opponent_draw.group("body"),
            "fixed-counter-opponent-card-draw-trigger-v1",
            "trigger-event-normalized-card-draw",
            condition={
                "field": "player",
                "op": "ne",
                "value": "$source.controller",
            },
        )
    return None


def fixed_public_event_binding_spec(
    material_line: str,
    *,
    card_name: str | None = None,
) -> FixedPublicEventBindingSpec | None:
    """Parse one bounded public occurrence without compiling its effect body."""

    return (
        _public_zone_spec(material_line, card_name=card_name)
        or _public_damage_spec(material_line, card_name=card_name)
        or _public_attack_spec(material_line)
        or _public_block_spec(material_line)
        or _public_misc_spec(material_line)
    )


__all__ = ["FixedPublicEventBindingSpec", "fixed_public_event_binding_spec"]
