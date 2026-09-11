from __future__ import annotations

"""Closed Fight and one-way creature-power damage grammar."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..attachment_references import AttachmentReferenceKind, AttachmentReferenceSpec
from ..creature_subtypes import canonical_creature_subtype
from ..creature_power_damage_model import (
    CREATURE_POWER_DAMAGE_LKI_CONTEXT,
    CREATURE_POWER_DAMAGE_MECHANIC,
    CREATURE_POWER_DAMAGE_OPERATION,
)
from ..rules.source_references import SourceReferenceSpec


_QUALITY = r"(?:green |[A-Z][A-Za-z'’-]* )?creature"
_DIRECT_FIGHT = re.compile(
    rf"^(?P<first>Target {_QUALITY}(?: you control)?"
    r"(?: with a \+1/\+1 counter on it)?) fights "
    rf"(?P<article>another target|up to one target|target) (?P<second>{_QUALITY})"
    r"(?P<relation> you don't control| an opponent controls)?\.?$",
    re.IGNORECASE,
)
_DIRECT_COMBAT_FIGHT = re.compile(
    r"^Target (?P<combat>attacking|blocking) creature fights another target "
    r"(?P=combat) creature\.?$",
    re.IGNORECASE,
)
_SOURCE_FIGHT = re.compile(
    rf"^(?P<optional>You may have )?(?P<source>this creature|it|enchanted creature|.+?) fight(?:s)? "
    rf"(?P<article>another target|up to one target|target) (?P<second>{_QUALITY})"
    r"(?P<relation> you don't control| an opponent controls)?\.?$",
    re.IGNORECASE,
)
_DIRECT_BITE = re.compile(
    rf"^(?P<first>Target {_QUALITY} you control) deals damage equal to its "
    r"power to (?P<second>any other target|any target|"
    rf"another target {_QUALITY}|target {_QUALITY}|target creature or planeswalker)"
    r"(?P<relation> you don't control| an opponent controls)?\.?$",
    re.IGNORECASE,
)
_SOURCE_BITE = re.compile(
    r"^(?P<optional>You may have )?(?P<source>this creature|it|enchanted creature|.+?) deal(?:s)? damage equal "
    r"to its power to (?P<second>any other target|any target|"
    rf"another target {_QUALITY}|target {_QUALITY}|target creature or planeswalker)"
    r"(?P<relation> you don't control| an opponent controls)?\.?$",
    re.IGNORECASE,
)
_STAT_PREP_FIGHT = re.compile(
    rf"^Target (?P<first>{_QUALITY}) you control gets "
    r"(?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) until end of turn\. "
    r"(?:Then )?(?:it|that creature) fights "
    rf"(?P<article>another target|up to one target|target) (?P<second>{_QUALITY})"
    r"(?P<relation> you don't control| an opponent controls)?\.?$",
    re.IGNORECASE,
)
_COUNTER_PREP_FIGHT = re.compile(
    r"^Put (?P<count>a|one|two|three|[1-9][0-9]*) \+1/\+1 counter(?:s)? on "
    rf"target (?P<first>{_QUALITY}) you control\. "
    r"(?:Then )?(?:it|that creature) fights "
    rf"(?P<article>another target|up to one target|target) (?P<second>{_QUALITY})"
    r"(?P<relation> you don't control| an opponent controls)?\.?$",
    re.IGNORECASE,
)
_REMINDER = re.compile(
    r"\s*\((?:Each|Those) (?:deals|creatures deal) damage equal to "
    r"(?:its|their) power to the other\.\)\s*$",
    re.IGNORECASE,
)

_OPTIONAL_EFFECT_MECHANIC = "fixed-optional-effect-choice"
_OPTIONAL_EFFECT_OPERATION = "offer_optional_effect"


@dataclass(frozen=True, slots=True)
class FixedCreaturePowerDamageTemplate:
    template_id: str
    effects: tuple[Mapping[str, Any], ...]
    target_schema: Mapping[str, Any]
    mechanics: tuple[str, ...]

    def compiled(self):
        return self.template_id, self.effects, self.target_schema, self.mechanics


def _quality_fields(text: str) -> dict[str, Any] | None:
    normalized = " ".join(text.casefold().split())
    if not normalized.endswith("creature"):
        return None
    prefix = normalized.removesuffix("creature").strip()
    fields: dict[str, Any] = {"types_any": ["creature"]}
    if prefix == "green":
        fields["colors_any"] = ["G"]
    elif prefix:
        subtype = canonical_creature_subtype(prefix)
        if subtype is None:
            return None
        fields["subtypes_any"] = [subtype]
    return fields


def _creature_group(
    *,
    group_id: str,
    quality: str,
    relation: str,
    optional: bool = False,
    state_counter: bool = False,
    source_exclusion: bool = False,
) -> dict[str, Any] | None:
    fields = _quality_fields(quality)
    if fields is None:
        return None
    result: dict[str, Any] = {
        "id": group_id,
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "controller_relation": relation,
        **fields,
        **({"min": 0, "max": 1} if optional else {"count": 1}),
    }
    if state_counter:
        result["state_predicate"] = {
            "kind": "counter_minimum",
            "counter_name": "+1/+1",
            "minimum": 1,
        }
    if source_exclusion:
        result["source_exclusion"] = True
    return result


def _source_reference(
    value: str,
    *,
    card_name: str,
    allow_source_pronoun: bool,
    source_attachment_relation: AttachmentReferenceKind | None,
) -> Any | None:
    normalized = " ".join(value.casefold().split())
    if normalized == "this creature" or (
        normalized == "it" and allow_source_pronoun
    ):
        return "$source"
    if normalized == "enchanted creature":
        if source_attachment_relation is not AttachmentReferenceKind.ENCHANTED:
            return None
        return AttachmentReferenceSpec(
            relation=AttachmentReferenceKind.ENCHANTED,
            required_card_type="creature",
        ).to_dict()
    if SourceReferenceSpec(card_name).matches(value):
        return "$source"
    return None


def _relation(value: str | None) -> str:
    return "opponent" if value else "any"


def _damageable_group(*, different_from: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": "recipient",
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "damageable",
        "count": 1,
    }
    if different_from is not None:
        result["different_from_groups"] = [different_from]
    return result


def _optional(
    template: FixedCreaturePowerDamageTemplate,
) -> FixedCreaturePowerDamageTemplate:
    return FixedCreaturePowerDamageTemplate(
        template_id=f"fixed-optional-{template.template_id}",
        effects=(
            {
                "op": _OPTIONAL_EFFECT_OPERATION,
                "player": "$controller",
                "effects": list(template.effects),
            },
        ),
        target_schema=template.target_schema,
        mechanics=tuple(
            dict.fromkeys((_OPTIONAL_EFFECT_MECHANIC, *template.mechanics))
        ),
    )


def _direct_fight(
    match: re.Match[str],
) -> FixedCreaturePowerDamageTemplate | None:
    first_text = match.group("first")
    controlled = " you control" in first_text.casefold()
    first_quality = re.sub(r"^target ", "", first_text, flags=re.IGNORECASE)
    first_quality = re.sub(
        r" you control(?: with a \+1/\+1 counter on it)?$",
        "",
        first_quality,
        flags=re.IGNORECASE,
    ).strip()
    first = _creature_group(
        group_id="fighter",
        quality=first_quality,
        relation="you" if controlled else "any",
        state_counter="+1/+1 counter" in first_text.casefold(),
    )
    second = _creature_group(
        group_id="opponent",
        quality=match.group("second"),
        relation=_relation(match.group("relation")),
        optional=match.group("article").casefold().startswith("up to one"),
    )
    if first is None or second is None:
        return None
    template = FixedCreaturePowerDamageTemplate(
        template_id="fixed-target-creature-fight-v1",
        effects=(
            {
                "op": CREATURE_POWER_DAMAGE_OPERATION,
                "kind": "fight",
                "source": "$target.0",
                "target": "$target.1",
                "target_must_be_creature": True,
                "source_lki": None,
            },
        ),
        target_schema={"groups": [first, second], "globally_distinct": True},
        mechanics=(
            CREATURE_POWER_DAMAGE_MECHANIC,
            "fight",
            "cr-115-targets",
            "cr-120-damage",
        ),
    )
    return template


def _direct_combat_fight(
    match: re.Match[str],
) -> FixedCreaturePowerDamageTemplate:
    combat = match.group("combat").casefold()
    groups = []
    for group_id in ("fighter", "opponent"):
        group = _creature_group(
            group_id=group_id,
            quality="creature",
            relation="any",
        )
        assert group is not None
        group["combat_state"] = combat
        groups.append(group)
    return FixedCreaturePowerDamageTemplate(
        template_id=f"fixed-target-{combat}-creature-fight-v1",
        effects=(
            {
                "op": CREATURE_POWER_DAMAGE_OPERATION,
                "kind": "fight",
                "source": "$target.0",
                "target": "$target.1",
                "target_must_be_creature": True,
                "source_lki": None,
            },
        ),
        target_schema={"groups": groups, "globally_distinct": True},
        mechanics=(
            CREATURE_POWER_DAMAGE_MECHANIC,
            "fight",
            "cr-115-targets",
            "cr-120-damage",
        ),
    )


def _source_fight(
    match: re.Match[str],
    *,
    card_name: str,
    allow_source_pronoun: bool,
    source_attachment_relation: AttachmentReferenceKind | None,
) -> FixedCreaturePowerDamageTemplate | None:
    source = _source_reference(
        match.group("source"),
        card_name=card_name,
        allow_source_pronoun=allow_source_pronoun,
        source_attachment_relation=source_attachment_relation,
    )
    target = _creature_group(
        group_id="opponent",
        quality=match.group("second"),
        relation=_relation(match.group("relation")),
        optional=match.group("article").casefold().startswith("up to one"),
        source_exclusion=match.group("article").casefold().startswith("another"),
    )
    if source is None or target is None:
        return None
    source_lki = (
        "$context." + CREATURE_POWER_DAMAGE_LKI_CONTEXT
        if source == "$source"
        else None
    )
    template = FixedCreaturePowerDamageTemplate(
        template_id="fixed-source-creature-fight-v1",
        effects=(
            {
                "op": CREATURE_POWER_DAMAGE_OPERATION,
                "kind": "fight",
                "source": source,
                "target": "$target.0",
                "target_must_be_creature": True,
                "source_lki": source_lki,
            },
        ),
        target_schema={"groups": [target]},
        mechanics=(
            CREATURE_POWER_DAMAGE_MECHANIC,
            "fight",
            "cr-115-targets",
            "cr-120-damage",
        ),
    )
    return _optional(template) if match.group("optional") else template


def _recipient_group(
    text: str,
    *,
    relation: str | None,
    different_from: str | None,
    source_exclusion: bool = False,
) -> tuple[dict[str, Any], bool] | None:
    normalized = " ".join(text.casefold().split())
    if normalized in {"any target", "any other target"}:
        group = _damageable_group(different_from=different_from)
        if source_exclusion:
            group["source_exclusion"] = True
        return group, False
    quality = re.sub(
        r"^(?:another target|target) ",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if quality.casefold() == "creature or planeswalker":
        return (
            {
                "id": "recipient",
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "types_any": ["creature", "planeswalker"],
                "controller_relation": _relation(relation),
                "count": 1,
            },
            False,
        )
    group = _creature_group(
        group_id="recipient",
        quality=quality,
        relation=_relation(relation),
        source_exclusion=source_exclusion,
    )
    if group is not None and different_from is not None:
        group["different_from_groups"] = [different_from]
    return (group, True) if group is not None else None


def _direct_bite(
    match: re.Match[str],
) -> FixedCreaturePowerDamageTemplate | None:
    first_quality = re.sub(
        r"^target | you control$",
        "",
        match.group("first"),
        flags=re.IGNORECASE,
    ).strip()
    fighter = _creature_group(
        group_id="fighter",
        quality=first_quality,
        relation="you",
    )
    other = "another target" in match.group("second").casefold() or (
        match.group("second").casefold() == "any other target"
    )
    recipient = _recipient_group(
        match.group("second"),
        relation=match.group("relation"),
        different_from="fighter" if other else None,
    )
    if fighter is None or recipient is None:
        return None
    target, must_be_creature = recipient
    return FixedCreaturePowerDamageTemplate(
        template_id="fixed-target-creature-power-damage-v1",
        effects=(
            {
                "op": CREATURE_POWER_DAMAGE_OPERATION,
                "kind": "bite",
                "source": "$target.0",
                "target": "$target.1",
                "target_must_be_creature": must_be_creature,
                "source_lki": None,
            },
        ),
        target_schema={"groups": [fighter, target]},
        mechanics=(
            CREATURE_POWER_DAMAGE_MECHANIC,
            "cr-115-targets",
            "cr-120-damage",
        ),
    )


def _source_bite(
    match: re.Match[str],
    *,
    card_name: str,
    allow_source_pronoun: bool,
    source_attachment_relation: AttachmentReferenceKind | None,
) -> FixedCreaturePowerDamageTemplate | None:
    source = _source_reference(
        match.group("source"),
        card_name=card_name,
        allow_source_pronoun=allow_source_pronoun,
        source_attachment_relation=source_attachment_relation,
    )
    recipient = _recipient_group(
        match.group("second"),
        relation=match.group("relation"),
        different_from=None,
        source_exclusion=(
            "another target" in match.group("second").casefold()
            or match.group("second").casefold() == "any other target"
        ),
    )
    if source is None or recipient is None:
        return None
    target, must_be_creature = recipient
    source_lki = (
        "$context." + CREATURE_POWER_DAMAGE_LKI_CONTEXT
        if source == "$source"
        else None
    )
    template = FixedCreaturePowerDamageTemplate(
        template_id="fixed-source-creature-power-damage-v1",
        effects=(
            {
                "op": CREATURE_POWER_DAMAGE_OPERATION,
                "kind": "bite",
                "source": source,
                "target": "$target.0",
                "target_must_be_creature": must_be_creature,
                "source_lki": source_lki,
            },
        ),
        target_schema={"groups": [target]},
        mechanics=(
            CREATURE_POWER_DAMAGE_MECHANIC,
            "cr-115-targets",
            "cr-120-damage",
        ),
    )
    return _optional(template) if match.group("optional") else template


def _prepared_fight(
    match: re.Match[str],
    *,
    prep_effect: Mapping[str, Any],
    prep_mechanic: str,
    template_id: str,
) -> FixedCreaturePowerDamageTemplate | None:
    fighter = _creature_group(
        group_id="fighter",
        quality=match.group("first"),
        relation="you",
    )
    opponent = _creature_group(
        group_id="opponent",
        quality=match.group("second"),
        relation=_relation(match.group("relation")),
        optional=match.group("article").casefold().startswith("up to one"),
    )
    if fighter is None or opponent is None:
        return None
    fight = {
        "op": CREATURE_POWER_DAMAGE_OPERATION,
        "kind": "fight",
        "source": "$target.0",
        "target": "$target.1",
        "target_must_be_creature": True,
        "source_lki": None,
    }
    return FixedCreaturePowerDamageTemplate(
        template_id=template_id,
        effects=(dict(prep_effect), fight),
        target_schema={
            "groups": [fighter, opponent],
            "globally_distinct": True,
        },
        mechanics=(
            CREATURE_POWER_DAMAGE_MECHANIC,
            "fight",
            "cr-115-targets",
            "cr-120-damage",
            prep_mechanic,
        ),
    )


def _stat_prepared_fight(
    match: re.Match[str],
) -> FixedCreaturePowerDamageTemplate | None:
    power = int(match.group("power"))
    toughness = int(match.group("toughness"))
    if power == 0 and toughness == 0:
        return None
    return _prepared_fight(
        match,
        prep_effect={
            "op": "modify_stats_until_end_of_turn",
            "card": "$target.0",
            "power": power,
            "toughness": toughness,
        },
        prep_mechanic="cr-611-continuous-effects",
        template_id="fixed-target-stat-prep-fight-v1",
    )


def _counter_prepared_fight(
    match: re.Match[str],
) -> FixedCreaturePowerDamageTemplate | None:
    words = {"a": 1, "one": 1, "two": 2, "three": 3}
    raw_count = match.group("count").casefold()
    count = words.get(raw_count, int(raw_count) if raw_count.isdigit() else 0)
    if count <= 0:
        return None
    return _prepared_fight(
        match,
        prep_effect={
            "op": "place_counters",
            "card": "$target.0",
            "counter": "+1/+1",
            "amount": count,
            "source": "$source",
        },
        prep_mechanic="cr-122-counters",
        template_id="fixed-target-counter-prep-fight-v1",
    )


def fixed_creature_power_damage_effect_template(
    text: str,
    *,
    card_name: str,
    allow_source_pronoun: bool = False,
    source_attachment_relation: AttachmentReferenceKind | None = None,
) -> FixedCreaturePowerDamageTemplate | None:
    """Lower one closed Fight or creature-power damage instruction."""

    normalized = " ".join(_REMINDER.sub("", text).split())
    stat_prep = _STAT_PREP_FIGHT.fullmatch(normalized)
    if stat_prep is not None:
        return _stat_prepared_fight(stat_prep)
    counter_prep = _COUNTER_PREP_FIGHT.fullmatch(normalized)
    if counter_prep is not None:
        return _counter_prepared_fight(counter_prep)
    combat_fight = _DIRECT_COMBAT_FIGHT.fullmatch(normalized)
    if combat_fight is not None:
        return _direct_combat_fight(combat_fight)
    direct_fight = _DIRECT_FIGHT.fullmatch(normalized)
    if direct_fight is not None:
        return _direct_fight(direct_fight)
    source_fight = _SOURCE_FIGHT.fullmatch(normalized)
    if source_fight is not None:
        return _source_fight(
            source_fight,
            card_name=card_name,
            allow_source_pronoun=allow_source_pronoun,
            source_attachment_relation=source_attachment_relation,
        )
    direct_bite = _DIRECT_BITE.fullmatch(normalized)
    if direct_bite is not None:
        return _direct_bite(direct_bite)
    source_bite = _SOURCE_BITE.fullmatch(normalized)
    if source_bite is not None:
        return _source_bite(
            source_bite,
            card_name=card_name,
            allow_source_pronoun=allow_source_pronoun,
            source_attachment_relation=source_attachment_relation,
        )
    return None


__all__ = [
    "fixed_creature_power_damage_effect_template",
    "FixedCreaturePowerDamageTemplate",
]
