from __future__ import annotations

"""Closed affected-player choices for fixed permanent sacrifices."""

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import hashlib
from . import regex_working_set as re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..apnap_object_payment import (
    ORDER_GRAVEYARD_ARRIVALS, OTHER_PLAYER_SACRIFICE_SCOPE, OTHER_PLAYER_SACRIFICE_TYPES,
)
from ..power_sacrifice_model import (
    ATTACK_SACRIFICE_STAT_TEMPLATE, ENTRY_SACRIFICE_POWER_DRAW_TEMPLATE, GreatestPowerSacrificeSpec,
    attack_sacrifice_stat_effect, entry_sacrifice_power_life_draw_effect,
    ENTRY_SACRIFICE_POWER_TREASURE_TEMPLATE, entry_sacrifice_power_treasure_effect,
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
    r"^(?:(?P<subject>target player|target opponent|each other player|each player|each opponent|you) )?"
    rf"(?P<verb>sacrifices?) (?P<count>another|an|thirteen|{FIXED_COUNT_PATTERN}) (?P<object>.+?)\.?$",
    re.IGNORECASE,
)


_GREATEST_MANA_VALUE_INSTRUCTION = re.compile(
    r"Each opponent sacrifices a creature or planeswalker with the greatest mana value "
    r"among creatures and planeswalkers they control\.?",
    re.I,
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
    EACH_OTHER_PLAYER = "each_other_player"
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
    "each other player": AffectedPlayerSacrificeSubject.EACH_OTHER_PLAYER,
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
    order_graveyard_arrivals: bool = False

    def __post_init__(self) -> None:
        if type(self.order_graveyard_arrivals) is not bool or self.order_graveyard_arrivals and (
            self.subject is not AffectedPlayerSacrificeSubject.EACH_PLAYER
            or self.count != 13 or self.exclude_source or self.greatest_power is not None
            or self.predicate != ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))
        ):
            raise ValueError("The ordered sacrifice requires the closed fixed creature choice")
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
            or self.exclude_source or self.predicate != self.greatest_power.predicate
        ):
            raise ValueError('Greatest-power sacrifice keeps its exact affected creature selection')
        if self.subject is AffectedPlayerSacrificeSubject.EACH_OTHER_PLAYER and (
            self.count != 1 or self.exclude_source
            or self.predicate not in tuple(ObjectQuerySpec(zones=("battlefield",), types_all=(category,))
                                           for category in OTHER_PLAYER_SACRIFICE_TYPES)
        ):
            raise ValueError("Other-player sacrifice requires one fixed type and no exclusions")
        if self.subject is AffectedPlayerSacrificeSubject.MOST_LANDS and (
            self.count != 2 or self.predicate != LAND_COUNT_METRIC.predicate() or self.exclude_source
        ):
            raise ValueError("Most-lands sacrifice requires exactly two controlled lands")

    @property
    def template_id(self) -> str:
        if self.greatest_power is not None:
            if self.greatest_power.mana_value:
                return "fixed-opponent-greatest-mana-value-sacrifice-v3"
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
            AffectedPlayerSacrificeSubject.EACH_OTHER_PLAYER: OTHER_PLAYER_SACRIFICE_SCOPE,
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
        if self.order_graveyard_arrivals:
            effect[ORDER_GRAVEYARD_ARRIVALS] = True
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


def keep_by_type_template(text):
    from ..rules.keep_permanents_by_type import KEEP_PERMANENTS_BY_TYPE_TEMPLATE, KeepPermanentsByType

    material = " ".join(text.split()).casefold()
    variants = {
        ("each player chooses an artifact, a creature, an enchantment, and a planeswalker from among the "
         "nonland permanents they control, then sacrifices the rest."): "nonland_self_choice",
        ("for each player, you choose from among the permanents that player controls an artifact, a creature, "
         "an enchantment, and a planeswalker. then each player sacrifices all other nonland permanents they control."):
            "nonland_controller_choice",
        ("each player chooses from among the permanents they control an artifact, a creature, an enchantment, "
         "and a land, then sacrifices the rest."): "land_self_choice",
    }
    mode = variants.get(material)
    if mode is None:
        return None
    return (
        KEEP_PERMANENTS_BY_TYPE_TEMPLATE, (KeepPermanentsByType(mode).effect(),), None,
        (FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, "sacrifice"),
    )


def counted_sacrifice_damage_template(text, *, card_name):
    from ..rules.any_number_sacrifice import ANY_NUMBER_SACRIFICE_TEMPLATE, any_number_sacrifice_effect
    from ..rules.committed_sacrifice_damage import committed_sacrifice_damage_recipe, committed_sacrifice_damage_target_schema
    from ..rules.source_references import SourceReferenceSpec

    material = " ".join(text.split())
    match = re.fullmatch(
        r"Sacrifice any number of (?P<domain>mountains|creatures)\. "
        r"(?P<source>.+?) deals that much damage to (?P<target>target player or planeswalker|any target)\.",
        material, re.I,
    )
    if match is None or not (match["source"].casefold() in ("this spell", "it")
                             or card_name and SourceReferenceSpec(card_name).matches(match["source"])):
        return None
    domain = match["domain"].casefold()
    target = "target player or planeswalker" if domain == "mountains" else "any target"
    if match["target"].casefold() != target:
        return None
    recipe = committed_sacrifice_damage_recipe(domain)
    return (
        ANY_NUMBER_SACRIFICE_TEMPLATE, (any_number_sacrifice_effect(domain, recipe),),
        committed_sacrifice_damage_target_schema(recipe),
        (FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, "sacrifice", "cr-120-damage", "cr-115-targets"),
    )


def any_number_sacrifice_template(text):
    from ..rules.any_number_sacrifice import ANY_NUMBER_SACRIFICE_TEMPLATE, any_number_sacrifice_effect

    from ..replacement.immutable import thaw_value
    from ..rules.graveyard_return_choices import COMMITTED_GRAVEYARD_RETURN_RECIPE

    material = " ".join(text.split()).casefold()
    life_text = "sacrifice any number of permanents. you gain 2 life for each permanent sacrificed this way."
    bounded_search = (
        "sacrifice any number of creatures. search your library and/or graveyard for a creature card with mana "
        "value less than or equal to the number of creatures sacrificed this way and put it onto the battlefield. "
        "if you search your library this way, shuffle."
    )
    if material == bounded_search:
        from ..rules.named_searches import sacrifice_bound_creature_search_recipe

        domain, followup = "creatures", sacrifice_bound_creature_search_recipe()
    elif material == (
        "sacrifice any number of artifacts, enchantments, and/or tokens. "
        "return that many creature cards from your graveyard to the battlefield."
    ):
        domain = "artifact_enchantment_token"
        followup = thaw_value(COMMITTED_GRAVEYARD_RETURN_RECIPE)
    elif material == life_text:
        domain = "permanents"

        followup = {"op": "life", "player": "$controller", "delta": 2}
    elif material == (
        "sacrifice any number of lands. search your library for up to that many land cards, "
        "put them onto the battlefield tapped, then shuffle."
    ):
        from ..rules.committed_land_sacrifice_search import land_sacrifice_search_recipe

        domain = "lands"
        followup = land_sacrifice_search_recipe()
    elif material == "sacrifice any number of lands, then add that much {c}.":
        domain = "lands"
        followup = {"op": "mana", "player": "$controller", "color": "C", "amount": 1}
    else:
        domains = {
            "sacrifice any number of artifacts, creatures, and/or lands. draw a card for each permanent sacrificed this way.":
                "artifact_creature_land",
            "sacrifice any number of permanents you control, then draw that many cards.": "permanents",
            "sacrifice any number of other permanents, then draw that many cards.": "other_permanents",
            "sacrifice any number of artifacts, enchantments, and/or tokens, then draw that many cards.":
                "artifact_enchantment_token",
        }
        domain = domains.get(material)
        if domain is None:
            return None
        followup = {"op": "draw", "player": "$controller", "count": 1, "private": True}
    return (ANY_NUMBER_SACRIFICE_TEMPLATE, (any_number_sacrifice_effect(domain, followup),), None,
            (FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, "sacrifice"))


def committed_sacrifice_hand_entry_template(text):
    from .hand_entry_collection_templates import private_hand_collection_template
    from ..private_hand_entry_model import decode_private_hand_collection
    from ..rules.any_number_sacrifice import (
        COMMITTED_SACRIFICE_HAND_ENTRY_TEMPLATE, any_number_sacrifice_effect,
    )

    normalized = " ".join(text.strip().split())
    prefix = re.match(r"(?:You )?sacrifice a creature\. If you do, draw a card, then ",
                      normalized, re.IGNORECASE)
    if prefix is None:
        return None
    collection = private_hand_collection_template(normalized[prefix.end():])
    if collection is None:
        return None
    _template, (effect,), _targets, _mechanics = collection
    if not decode_private_hand_collection(effect).one_of_each:
        return None
    return (COMMITTED_SACRIFICE_HAND_ENTRY_TEMPLATE, (any_number_sacrifice_effect("creatures", effect),),
            None, ("fixed-affected-player-sacrifice", "sacrifice"))


def counted_sacrifice_token_template(text):
    from ..rules.counted_sacrifice_tokens import COUNTED_SACRIFICE_TOKEN_TEMPLATE, counted_sacrifice_tokens_effect
    from .oracle_source_text import without_parenthetical_reminder
    from .token_templates import fixed_token_creation_effect_template

    material = " ".join(without_parenthetical_reminder(text).split()).casefold()
    other_player_followup = ". you create a 2/2 black zombie creature token for each creature sacrificed this way."
    if material in (
        "each other player sacrifices a creature" + other_player_followup,
        "each other player sacrifices a creature of their choice" + other_player_followup,
    ):
        scope, definition = OTHER_PLAYER_SACRIFICE_SCOPE, "Create a 2/2 black Zombie creature token."
    elif material == ("each opponent sacrifices a creature of their choice. create a food token "
                      "for each creature sacrificed this way."):
        scope, definition = "opponents", "Create a Food token."
    else:
        return None
    token = fixed_token_creation_effect_template(definition)
    if token is None:
        return None
    _template, (result,), _schema, mechanics = token.compiled()
    return (COUNTED_SACRIFICE_TOKEN_TEMPLATE, (counted_sacrifice_tokens_effect(scope, result),), None,
            (FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, "sacrifice", *mechanics))


def target_sacrifice_toughness_template(text):
    from ..rules.target_sacrifice_toughness import (
        TARGET_SACRIFICE_TOUGHNESS_TEMPLATE, target_sacrifice_toughness_effect, target_sacrifice_toughness_schema,
    )

    material = " ".join(text.split())
    target_result = re.fullmatch(
        r"Target player sacrifices a creature(?: of their choice)?, then gains life equal to that creature['’]s toughness\.",
        material, re.I,
    )
    controller_result = re.fullmatch(
        r"Target (?P<relation>player|opponent) sacrifices a creature(?: of their choice)?\. "
        r"You gain life equal to that creature['’]s toughness\.",
        material, re.I,
    )
    if target_result is not None:
        relation, recipient = "any", "target"
    elif controller_result is not None:
        relation = "opponent" if controller_result["relation"].casefold() == "opponent" else "any"
        recipient = "controller"
    else:
        return None
    return (
        TARGET_SACRIFICE_TOUGHNESS_TEMPLATE, (target_sacrifice_toughness_effect(relation, recipient),),
        target_sacrifice_toughness_schema(relation),
        (FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, "sacrifice", "cr-115-targets", "cr-119-life"),
    )


def sacrifice_discard_fallback_template(text):
    expected = (
        "each player sacrifices a creature or planeswalker of their choice. "
        "each player who can't discards a card."
    )
    if " ".join(text.split()).casefold().replace("\u2019", "'") != expected:
        return None
    from ..rules.sacrifice_discard_fallback import sacrifice_discard_fallback_effect

    return ("sacrifice-creature-planeswalker-or-private-discard-v1", (sacrifice_discard_fallback_effect(),), None,
            ("fixed-affected-player-sacrifice", "fixed-affected-player-discard", "sacrifice"))


def ordered_type_sacrifice_template(text):
    expected = (
        "choose an order for artifacts, creatures, and lands. each player sacrifices one permanent "
        "of their choice of the first type, sacrifices two of their choice of the second type, "
        "then sacrifices three of their choice of the third type."
    )
    if " ".join(text.split()).casefold() != expected:
        return None
    from ..rules.ordered_type_sacrifice import ordered_type_sacrifice_effect

    return ("fixed-ordered-type-sacrifices-v1", (ordered_type_sacrifice_effect(),), None,
            ("fixed-affected-player-sacrifice", "sacrifice"))


@dataclass(frozen=True, slots=True)
class AllColoredPermanentSacrificeTemplate:
    def compiled(self):
        from ..rules.colored_permanent_sacrifice import all_colored_sacrifice_effect

        return ("all-colored-permanent-sacrifice-v1", (all_colored_sacrifice_effect(),), None,
                (FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, _SACRIFICE_MECHANIC))


def ordered_opponent_creature_sacrifice_template(text):
    if " ".join(text.split()).casefold() != "each opponent sacrifices three creatures of their choice.":
        return None
    from ..rules.ordered_opponent_creature_sacrifice import ordered_opponent_sacrifice_effect

    return ("ordered-opponent-creature-sacrifice-v1", (ordered_opponent_sacrifice_effect(),), None,
            (FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, _SACRIFICE_MECHANIC))


def fixed_affected_player_sacrifice_effect_template(
    text: str,
) -> FixedAffectedPlayerSacrificeTemplate | AllColoredPermanentSacrificeTemplate | None:
    """Lower one mandatory fixed affected-player sacrifice instruction."""

    normalized = " ".join(text.strip().split())
    if normalized.casefold() == "each player sacrifices all permanents they control that are one or more colors.":
        return AllColoredPermanentSacrificeTemplate()
    targeted = _targeted_greatest_power_sacrifice_life_template(normalized)
    if targeted is not None:
        return targeted
    if _GREATEST_MANA_VALUE_INSTRUCTION.fullmatch(normalized):
        spec = GreatestPowerSacrificeSpec(mana_value=True)
        return FixedAffectedPlayerSacrificeTemplate(
            AffectedPlayerSacrificeSubject.EACH_OPPONENT, 1, spec.predicate, greatest_power=spec,
        )
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
    try:
        return FixedAffectedPlayerSacrificeTemplate(
            subject=_SUBJECTS[subject],
            count=count,
            predicate=predicate,
            exclude_source=exclude_source,
            order_graveyard_arrivals=(
                subject == "each player" and count == 13
                and phrase.casefold() == "creatures of their choice"
            ),
        )
    except ValueError:
        return None


_ENTRY_SACRIFICE_POWER_VARIANT = "source_entry_selected_sacrifice_power"
_ENTRY_SACRIFICE_POWER = re.compile(
    r"When this creature enters, (?P<body>(?P<optional>you may )?sacrifice another creature\. "
    r"(?P<paid>If you do, )?you gain X life and draw X cards, where X is that creature['’]s power\.)",
    re.I,
)


_ENTRY_SACRIFICE_TREASURE_VARIANT = "source_entry_selected_sacrifice_treasure"
_ENTRY_SACRIFICE_TREASURE = re.compile(
    r"When this creature enters, (?P<body>you may sacrifice another creature you control\. "
    r"If you do, create a number of Treasure tokens equal to that creature['’]s power\.)",
    re.I,
)


def entry_sacrifice_power_draw_binding_spec(text):
    treasure = _ENTRY_SACRIFICE_TREASURE.fullmatch(text)
    match = treasure or _ENTRY_SACRIFICE_POWER.fullmatch(text)
    if match is None or (treasure is None and (match["optional"] is None) != (match["paid"] is None)):
        return None
    from ..rules.affected_player_sacrifice_capability_shapes import entry_sacrifice_power_condition
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec

    return FixedPublicEventBindingSpec(
        event="permanent.enter", body=match["body"],
        variant=_ENTRY_SACRIFICE_TREASURE_VARIANT if treasure else _ENTRY_SACRIFICE_POWER_VARIANT,
        template_id=ENTRY_SACRIFICE_POWER_TREASURE_TEMPLATE if treasure else ENTRY_SACRIFICE_POWER_DRAW_TEMPLATE,
        mechanic="trigger-event-normalized-zone-change",
        condition=entry_sacrifice_power_condition(),
    )


def entry_sacrifice_power_draw_result(binding, body):
    if binding.variant == _ENTRY_SACRIFICE_TREASURE_VARIANT and body == binding.body:
        return (
            (ENTRY_SACRIFICE_POWER_TREASURE_TEMPLATE, (entry_sacrifice_power_treasure_effect(),), None,
             (FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, "sacrifice", SOURCE_EXCLUDED_SACRIFICE_MECHANIC, "cr-111-tokens")),
            True,
        )
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
