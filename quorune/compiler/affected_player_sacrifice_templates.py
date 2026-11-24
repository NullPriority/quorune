from __future__ import annotations

"""Closed affected-player choices for fixed permanent sacrifices."""

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import hashlib
from . import regex_working_set as re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..power_sacrifice_model import (
    ATTACK_SACRIFICE_STAT_TEMPLATE, ENTRY_SACRIFICE_POWER_DRAW_TEMPLATE, GreatestPowerSacrificeSpec,
    attack_sacrifice_stat_effect, entry_sacrifice_power_life_draw_effect,
)
from ..player_maximum_model import LAND_COUNT_METRIC, land_count_maximizers_reference
from ..basic_land_types import BASIC_LAND_MANA
from .creature_subtypes import canonical_creature_subtype_surface
from ..util import stable_json
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC = (
    "fixed-affected-player-sacrifice"
)
FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY = (
    "choice.affected_player.fixed_sacrifice"
)
SOURCE_EXCLUDED_SACRIFICE_CAPABILITY = 'choice.affected_player.source_exclusion'
SOURCE_EXCLUDED_SACRIFICE_MECHANIC = 'source-excluded-affected-player-sacrifice'
MOST_LANDS_ATTACK_SACRIFICE_MECHANIC = 'most-lands-attack-sacrifice'
_SACRIFICE_MECHANIC = "sacrifice"
_TARGETS_MECHANIC = "cr-115-targets"
_INSTRUCTION = re.compile(
    r"^(?:(?P<subject>target player|target opponent|each player|each opponent|you) )?"
    rf"(?P<verb>sacrifices?) (?P<count>another|an|{FIXED_COUNT_PATTERN}) (?P<object>.+?)\.?$",
    re.IGNORECASE,
)


_GREATEST_INSTRUCTION = re.compile(
    r"Each opponent(?P<lost> who lost life this turn)? sacrifices a creature with the greatest power "
    r"among creatures (?:that player controls|they control)\.?",
    re.I,
)


class AffectedPlayerSacrificeSubject(str, Enum):
    CONTROLLER = "controller"
    TARGET_PLAYER = "target_player"
    TARGET_OPPONENT = "target_opponent"
    EACH_PLAYER = "each_player"
    EACH_OPPONENT = "each_opponent"
    MOST_LANDS = "most_lands"

    @property
    def targeted(self) -> bool:
        return self in {
            AffectedPlayerSacrificeSubject.TARGET_PLAYER,
            AffectedPlayerSacrificeSubject.TARGET_OPPONENT,
        }

    @property
    def opponent_only(self) -> bool:
        return self in {
            AffectedPlayerSacrificeSubject.TARGET_OPPONENT,
            AffectedPlayerSacrificeSubject.EACH_OPPONENT,
        }


_SUBJECTS = {
    "you": AffectedPlayerSacrificeSubject.CONTROLLER,
    "target player": AffectedPlayerSacrificeSubject.TARGET_PLAYER,
    "target opponent": AffectedPlayerSacrificeSubject.TARGET_OPPONENT,
    "each player": AffectedPlayerSacrificeSubject.EACH_PLAYER,
    "each opponent": AffectedPlayerSacrificeSubject.EACH_OPPONENT,
}


def _predicate(phrase: str, *, count: int) -> ObjectQuerySpec | None:
    normalized = " ".join(phrase.casefold().split())
    if normalized.endswith(" of their choice with flying"):
        normalized = normalized.removesuffix(
            " of their choice with flying"
        ) + " with flying"
    elif normalized.endswith(" of their choice"):
        normalized = normalized.removesuffix(" of their choice")
    if count > 1:
        normalized = {
            "artifacts": "artifact",
            "creatures": "creature",
            "enchantments": "enchantment",
            "lands": "land",
            "permanents": "permanent",
            "planeswalkers": "planeswalker",
            "plains": "plains",
            "islands": "island",
            "swamps": "swamp",
            "mountains": "mountain",
            "forests": "forest",
            "powerstones": "powerstone",
            "permanents that are one or more colors": "permanent that's one or more colors",
        }.get(normalized, normalized if canonical_creature_subtype_surface(normalized) is not None else "")
    if not normalized:
        return None
    if normalized in BASIC_LAND_MANA:
        return ObjectQuerySpec(zones=("battlefield",), types_all=("land",),
                               subtypes_all=(normalized,))
    if normalized == "powerstone":
        return ObjectQuerySpec(zones=("battlefield",), types_all=("artifact",),
                               subtypes_all=("powerstone",))
    if normalized == "permanent that's one or more colors":
        return ObjectQuerySpec(zones=("battlefield",), colors_any=tuple('WUBRG'))
    if normalized == "permanent":
        return ObjectQuerySpec(zones=("battlefield",))
    if normalized == "nonland permanent":
        return ObjectQuerySpec(zones=("battlefield",), excluded_types=("land",))
    if normalized.startswith("non-") and normalized.endswith(" creature"):
        excluded = canonical_creature_subtype_surface(normalized.removeprefix("non-").removesuffix(" creature"))
        if excluded is not None:
            return ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), excluded_subtypes=(excluded,))
    if normalized in {
        "artifact",
        "creature",
        "enchantment",
        "land",
        "planeswalker",
    }:
        return ObjectQuerySpec(
            zones=("battlefield",),
            types_all=(normalized,),
        )
    if normalized in {
        "artifact or enchantment",
        "creature or enchantment",
        "creature or planeswalker",
        "creature or land",
    }:
        return ObjectQuerySpec(
            zones=("battlefield",),
            types_any=tuple(normalized.split(" or ")),
        )
    if normalized == "creature, artifact, or land":
        return ObjectQuerySpec(zones=("battlefield",), types_any=("artifact", "creature", "land"))
    if normalized == "creature token":
        return ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("creature",),
            token=True,
        )
    if normalized in {"nontoken artifact", "nontoken creature"}:
        return ObjectQuerySpec(
            zones=("battlefield",),
            types_all=(normalized.removeprefix("nontoken "),),
            token=False,
        )
    if normalized == "nonartifact creature":
        return ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("creature",),
            excluded_types=("artifact",),
        )
    if normalized == "creature with flying":
        return ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("creature",),
            keywords_all=("flying",),
        )
    subtype = canonical_creature_subtype_surface(normalized)
    if subtype is not None:
        return ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), subtypes_all=(subtype,))
    return None


_CLOSED_PREDICATES = frozenset(
    stable_json(value.to_dict())
    for phrase in (
        "artifact",
        "creature",
        "enchantment",
        "land",
        "permanent",
        "planeswalker",
        "artifact or enchantment",
        "creature or enchantment",
        "creature or planeswalker",
        "creature or land",
        "creature, artifact, or land",
        "creature token",
        "nontoken artifact",
        "nontoken creature",
        "nonartifact creature",
        "nonland permanent",
        "creature with flying",
        *BASIC_LAND_MANA,
        "powerstone",
        "permanent that's one or more colors",
    )
    for value in (_predicate(phrase, count=1),)
    if value is not None
)


def fixed_affected_player_sacrifice_predicate_is_closed(
    value: Mapping[str, Any],
) -> bool:
    try:
        predicate = ObjectQuerySpec.from_dict(value)
    except (TypeError, ValueError):
        return False
    if stable_json(predicate.to_dict()) in _CLOSED_PREDICATES:
        return True
    if len(predicate.excluded_subtypes) == 1:
        subtype = canonical_creature_subtype_surface(predicate.excluded_subtypes[0])
        return subtype is not None and predicate == ObjectQuerySpec(
            zones=("battlefield",), types_all=("creature",), excluded_subtypes=(subtype,))
    if len(predicate.subtypes_all) != 1:
        return False
    subtype = canonical_creature_subtype_surface(predicate.subtypes_all[0])
    return subtype is not None and predicate == ObjectQuerySpec(
        zones=("battlefield",), types_all=("creature",), subtypes_all=(subtype,))


@dataclass(frozen=True, slots=True)
class FixedAffectedPlayerSacrificeTemplate:
    subject: AffectedPlayerSacrificeSubject
    count: int
    predicate: ObjectQuerySpec
    exclude_source: bool = False
    greatest_power: GreatestPowerSacrificeSpec | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.subject, AffectedPlayerSacrificeSubject):
            raise ValueError("Affected-player sacrifice subject is malformed")
        if type(self.count) is not int or self.count < 1:
            raise ValueError("Affected-player sacrifice count is unsupported")
        if type(self.exclude_source) is not bool:
            raise ValueError('Affected-player source exclusion must be boolean')
        if not fixed_affected_player_sacrifice_predicate_is_closed(
            self.predicate.to_dict()
        ):
            raise ValueError("Affected-player sacrifice predicate is unsupported")
        if self.greatest_power is not None and (
            not isinstance(self.greatest_power, GreatestPowerSacrificeSpec)
            or self.subject is not (
                AffectedPlayerSacrificeSubject.TARGET_PLAYER if self.greatest_power.target_player is not None
                else AffectedPlayerSacrificeSubject.EACH_OPPONENT
            )
            or self.greatest_power.target_player not in (None, "$target.0") or self.count != 1
            or self.exclude_source or self.predicate != ObjectQuerySpec(zones=('battlefield',), types_all=('creature',))
        ):
            raise ValueError('Greatest-power sacrifice keeps its exact affected creature selection')
        if self.subject is AffectedPlayerSacrificeSubject.MOST_LANDS and (
            self.count != 2 or self.predicate != LAND_COUNT_METRIC.predicate() or self.exclude_source
        ):
            raise ValueError("Most-lands sacrifice requires exactly two controlled lands")

    @property
    def template_id(self) -> str:
        if self.greatest_power is not None:
            if self.greatest_power.target_player is not None:
                return "fixed-target-greatest-power-sacrifice-life-v2"
            return ('fixed-opponent-life-loss-greatest-power-sacrifice-v1' if self.greatest_power.lost_life_only
                    else 'fixed-opponent-greatest-power-sacrifice-v1')
        digest = hashlib.sha256(
            stable_json(self.predicate.to_dict()).encode("utf-8")
        ).hexdigest()[:12]
        return (
            f"fixed-affected-player-sacrifice-{self.subject.value}-"
            f"{self.count}-{digest}" + ('-source-excluded-v1' if self.exclude_source else '-v1')
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        if self.greatest_power is not None:
            return (self.greatest_power.effect(),)
        players: str | list[str] | dict[str, Any] = {
            AffectedPlayerSacrificeSubject.CONTROLLER: ["$controller"],
            AffectedPlayerSacrificeSubject.EACH_PLAYER: "all",
            AffectedPlayerSacrificeSubject.EACH_OPPONENT: "opponents",
            AffectedPlayerSacrificeSubject.MOST_LANDS: land_count_maximizers_reference(),
        }.get(self.subject, ["$target.0"])
        effect: dict[str, Any] = {
            "op": "choose_cards_apnap",
            "actor": "$controller",
            "players": players,
            "zone": "battlefield",
            "predicate": self.predicate.to_dict(),
            "count": self.count,
            "then": "sacrifice",
            "prompt": "Choose the required permanent(s) to sacrifice.",
        }
        if self.subject.targeted:
            effect["target"] = "$target.0"
        if self.exclude_source:
            effect['exclude_ref'] = '$source.zone_object'
        return (deepcopy(effect),)

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        if not self.subject.targeted:
            return None
        return {
            "zones": ["player"],
            "categories": ["player"],
            "player_relation": (
                "opponent" if self.subject.opponent_only else "any"
            ),
            "count": 1,
        }

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (
            FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC,
            _SACRIFICE_MECHANIC,
            *((MOST_LANDS_ATTACK_SACRIFICE_MECHANIC,) if self.subject is AffectedPlayerSacrificeSubject.MOST_LANDS else ()),
            *((SOURCE_EXCLUDED_SACRIFICE_MECHANIC,) if self.exclude_source else ()),
            *((_TARGETS_MECHANIC,) if self.subject.targeted else ()),
        )

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


MOST_LANDS_ATTACK_SACRIFICE = FixedAffectedPlayerSacrificeTemplate(
    AffectedPlayerSacrificeSubject.MOST_LANDS, 2, LAND_COUNT_METRIC.predicate(),
)


def most_lands_attack_sacrifice_result(binding, body):
    if (
        (binding.event.value, binding.variant, binding.active_zone)
        == ("creature.attacks", "source_attacks", "battlefield")
        and binding.event_condition == {"field": "card", "op": "eq", "value": "$source.ref"}
        and body == binding.body
        and re.fullmatch(r"Each player who controls the most lands sacrifices two lands of their choice[.]?", body, re.I)
    ):
        return MOST_LANDS_ATTACK_SACRIFICE.compiled()
    return None


def _targeted_greatest_power_sacrifice_life_template(normalized: str) -> FixedAffectedPlayerSacrificeTemplate | None:
    if normalized.casefold() == (
        "target player sacrifices a creature with the greatest power among creatures they control. "
        "you gain life equal to its power."
    ):
        return FixedAffectedPlayerSacrificeTemplate(
            AffectedPlayerSacrificeSubject.TARGET_PLAYER, 1,
            ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
            greatest_power=GreatestPowerSacrificeSpec(target_player="$target.0"),
        )
    return None


def fixed_affected_player_sacrifice_effect_template(
    text: str,
) -> FixedAffectedPlayerSacrificeTemplate | None:
    """Lower one mandatory fixed affected-player sacrifice instruction."""

    normalized = " ".join(text.strip().split())
    targeted = _targeted_greatest_power_sacrifice_life_template(normalized)
    if targeted is not None:
        return targeted
    greatest = _GREATEST_INSTRUCTION.fullmatch(normalized)
    if greatest is not None:
        return FixedAffectedPlayerSacrificeTemplate(
            AffectedPlayerSacrificeSubject.EACH_OPPONENT, 1, ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
            greatest_power=GreatestPowerSacrificeSpec(lost_life_only=greatest["lost"] is not None),
        )
    match = _INSTRUCTION.fullmatch(normalized)
    if match is None:
        return None
    subject = (match.group("subject") or "you").casefold()
    if (subject == "you") != (match.group("verb").casefold() == "sacrifice"):
        return None
    count_word = match.group("count").casefold()
    exclude_source = count_word == 'another'
    count = 1 if exclude_source else fixed_number(count_word)
    if count < 1:
        return None
    phrase = match.group('object')
    other = re.fullmatch(r'(?P<objects>.+?) other than this (?:artifact|creature|enchantment|land|permanent|planeswalker)',phrase,re.I)
    if other is not None:
        if exclude_source:
            return None
        exclude_source = True
        phrase = other['objects']
    predicate = _predicate(phrase, count=count)
    if predicate is None:
        return None
    return FixedAffectedPlayerSacrificeTemplate(
        subject=_SUBJECTS[subject],
        count=count,
        predicate=predicate,
        exclude_source=exclude_source,
    )


_ENTRY_SACRIFICE_POWER_VARIANT = "source_entry_selected_sacrifice_power"
_ENTRY_SACRIFICE_POWER = re.compile(
    r"When this creature enters, (?P<body>(?P<optional>you may )?sacrifice another creature\. "
    r"(?P<paid>If you do, )?you gain X life and draw X cards, where X is that creature['’]s power\.)",
    re.I,
)


def entry_sacrifice_power_draw_binding_spec(text):
    match = _ENTRY_SACRIFICE_POWER.fullmatch(text)
    if match is None or (match["optional"] is None) != (match["paid"] is None):
        return None
    from ..rules.affected_player_sacrifice_capability_shapes import entry_sacrifice_power_condition
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec

    return FixedPublicEventBindingSpec(
        event="permanent.enter", variant=_ENTRY_SACRIFICE_POWER_VARIANT, body=match["body"],
        template_id=ENTRY_SACRIFICE_POWER_DRAW_TEMPLATE, mechanic="trigger-event-normalized-zone-change",
        condition=entry_sacrifice_power_condition(),
    )


def entry_sacrifice_power_draw_result(binding, body):
    if binding.variant != _ENTRY_SACRIFICE_POWER_VARIANT or body != binding.body:
        return None
    return (
        (ENTRY_SACRIFICE_POWER_DRAW_TEMPLATE,
         (entry_sacrifice_power_life_draw_effect(body.casefold().startswith("you may ")),), None,
         (FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, "sacrifice", SOURCE_EXCLUDED_SACRIFICE_MECHANIC, "cr-119-life")),
        True,
    )


_ATTACK_SACRIFICE_STAT_VARIANT = "source_attack_selected_sacrifice_stats"
_ATTACK_SACRIFICE_STATS = re.compile(
    r"(?:When|Whenever) (?P<source>.+?) attacks, (?P<body>you may sacrifice another creature\. "
    r"If you do, you draw cards equal to that creature['’]s power and you lose life equal to that creature['’]s toughness\.)",
    re.I,
)


def attack_sacrifice_stat_binding_spec(text, *, card_name):
    match = _ATTACK_SACRIFICE_STATS.fullmatch(text)
    if match is None:
        return None
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec, _source_subject

    if not _source_subject(match["source"], card_name=card_name):
        return None
    return FixedPublicEventBindingSpec(
        event="creature.attacks", variant=_ATTACK_SACRIFICE_STAT_VARIANT, body=match["body"],
        template_id=ATTACK_SACRIFICE_STAT_TEMPLATE, mechanic="trigger-event-normalized-public-action",
        condition={"field": "card", "op": "eq", "value": "$source.ref"},
    )


def attack_sacrifice_stat_result(binding, body):
    if binding.variant != _ATTACK_SACRIFICE_STAT_VARIANT or body != binding.body:
        return None
    return (
        (ATTACK_SACRIFICE_STAT_TEMPLATE, (attack_sacrifice_stat_effect(),), None,
         (FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, "sacrifice", SOURCE_EXCLUDED_SACRIFICE_MECHANIC, "cr-119-life")),
        True,
    )


__all__ = [
    "AffectedPlayerSacrificeSubject",
    "FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY",
    "FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC",
    "FixedAffectedPlayerSacrificeTemplate",
    "fixed_affected_player_sacrifice_effect_template",
    "fixed_affected_player_sacrifice_predicate_is_closed",
]
