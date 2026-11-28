from __future__ import annotations

"""Closed Oracle grammar for fixed public-origin zone moves."""

from dataclasses import dataclass, replace
from . import regex_working_set as re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..creature_subtypes import canonical_creature_subtype
from ..public_zone_moves import (
    FixedMixedZoneExileSpec, MixedZoneExileScope,
    PUBLIC_AURA_ENTRY_VERSION,
    public_graveyard_power_return_spec,
    own_graveyard_type_return_choice_effect,
    PublicBattlefieldController,
    PublicZoneDestination,
    PublicZoneMoveSetSpec,
    same_turn_graveyard_return_spec, targeted_same_turn_graveyard_return_schema,
    PublicZoneOrigin,
    PublicZoneRelationAxis,
    PublicZoneSeatRelation,
)
from ..rules.graveyard_card_targets import (
    PublicGraveyardCardTargetKind,
    PublicGraveyardCardTargetSpec,
)
from ..rules.same_name_zone_moves import SameNameZoneMoveScope
from .destruction_templates import fixed_affected_permanent_query
from .direct_target import compiled_direct_target, direct_target_effect
from ..graveyard_card_result_model import GraveyardCardCounterLifeSpec, GraveyardPermanentCardCounterSpec
from .graveyard_card_result_templates import parse_graveyard_card_counter_life, parse_graveyard_permanent_card_counter
from .public_tap_state_set_templates import (
    FixedPublicTapStateSetTemplate,
    fixed_public_tap_state_set_effect_template,
)


def _player_target_schema(*, opponent: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
        "player_relation": "opponent" if opponent else "any",
    }
    return result


@dataclass(frozen=True, slots=True)
class PublicGraveyardCardExileTemplate:
    """One target physical card in a public, optionally owner-relative graveyard."""

    target: PublicGraveyardCardTargetKind = PublicGraveyardCardTargetKind.CARD
    owner_relation: str = "any"
    result: GraveyardCardCounterLifeSpec | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, PublicGraveyardCardTargetKind):
            raise ValueError("Public graveyard exile target is unsupported")
        PublicGraveyardCardTargetSpec(self.target, owner_relation=self.owner_relation)
        if self.result is not None and (not isinstance(self.result, GraveyardCardCounterLifeSpec)
                or self.target is not PublicGraveyardCardTargetKind.CARD or self.owner_relation != 'any'):
            raise ValueError('Conditional card result requires one unrestricted public graveyard card')

    @property
    def template_id(self) -> str:
        if isinstance(self.result, GraveyardPermanentCardCounterSpec):
            return 'exile-target-graveyard-permanent-card-counter-result-v2'
        if self.result is not None:
            return 'exile-target-graveyard-card-counter-life-result-v1'
        if self.owner_relation != "any":
            return ("exile-target-" + PublicGraveyardCardTargetSpec(self.target).slug
                    + "-from-owner-" + self.owner_relation + "-graveyard-v1")
        return (
            "exile-target-"
            + PublicGraveyardCardTargetSpec(self.target).slug
            + "-from-public-graveyard-v1"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        if self.result is not None:
            return (self.result.effect(),)
        return direct_target_effect(
            "exile_public_graveyard_card",
            reference_field="card",
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return PublicGraveyardCardTargetSpec(self.target, owner_relation=self.owner_relation).to_target_schema()

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("exile", "fixed-public-zone-move", "cr-115-targets")

    def compiled(self):
        return compiled_direct_target(
            template_id=self.template_id,
            effects=self.effects,
            target_schema=self.target_schema,
            mechanics=self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class PublicZoneMoveSetTemplate:
    spec: PublicZoneMoveSetSpec
    target_schema: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.spec, PublicZoneMoveSetSpec):
            raise ValueError("Public zone move requires a typed affected set")
        schema = dict(self.target_schema) if self.target_schema is not None else None
        targeted = (
            self.spec.seat_relation is PublicZoneSeatRelation.TARGET_PLAYER
            or self.spec.same_name_target is not None or self.spec.target_refs is not None
        )
        if targeted is not (schema is not None):
            raise ValueError(
                "Public zone-move target schema contradicts its affected set"
            )
        object.__setattr__(self, "target_schema", schema)

    @property
    def template_id(self) -> str:
        return f"move-fixed-public-set-{self.spec.fingerprint[:16]}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "move_public_zone_set",
                "source": "$source",
                "set": self.spec.to_dict(),
            },
        )

    @property
    def mechanics(self) -> tuple[str, ...]:
        mechanic = (
            "exile"
            if self.spec.destination is PublicZoneDestination.EXILE
            else "return-to-owner-hand"
            if self.spec.destination is PublicZoneDestination.OWNER_HAND
            else "fixed-public-zone-move"
        )
        return (
            mechanic,
            "fixed-public-zone-move",
            "fixed-public-zone-move-set",
            *(("cr-115-targets",) if self.target_schema is not None else ()),
        )

    def compiled(self):
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def _relation(
    phrase: str,
) -> tuple[
    PublicZoneRelationAxis,
    PublicZoneSeatRelation,
    str | None,
    Mapping[str, Any] | None,
] | None:
    normalized = phrase.casefold()
    mapping = {
        "": (
            PublicZoneRelationAxis.CONTROLLER,
            PublicZoneSeatRelation.ANY,
            None,
            None,
        ),
        " you control": (
            PublicZoneRelationAxis.CONTROLLER,
            PublicZoneSeatRelation.ACTOR,
            None,
            None,
        ),
        " your opponents control": (
            PublicZoneRelationAxis.CONTROLLER,
            PublicZoneSeatRelation.OPPONENTS,
            None,
            None,
        ),
        " you don't control": (
            PublicZoneRelationAxis.CONTROLLER,
            PublicZoneSeatRelation.OPPONENTS,
            None,
            None,
        ),
        " target player controls": (
            PublicZoneRelationAxis.CONTROLLER,
            PublicZoneSeatRelation.TARGET_PLAYER,
            "$target.0",
            _player_target_schema(),
        ),
        " target opponent controls": (
            PublicZoneRelationAxis.CONTROLLER,
            PublicZoneSeatRelation.TARGET_PLAYER,
            "$target.0",
            _player_target_schema(opponent=True),
        ),
        " you own": (
            PublicZoneRelationAxis.OWNER,
            PublicZoneSeatRelation.ACTOR,
            None,
            None,
        ),
        " your opponents own": (
            PublicZoneRelationAxis.OWNER,
            PublicZoneSeatRelation.OPPONENTS,
            None,
            None,
        ),
        " target player owns": (
            PublicZoneRelationAxis.OWNER,
            PublicZoneSeatRelation.TARGET_PLAYER,
            "$target.0",
            _player_target_schema(),
        ),
        " target opponent owns": (
            PublicZoneRelationAxis.OWNER,
            PublicZoneSeatRelation.TARGET_PLAYER,
            "$target.0",
            _player_target_schema(opponent=True),
        ),
    }
    return mapping.get(normalized)


def public_graveyard_card_exile_template(
    text: str,
) -> PublicGraveyardCardExileTemplate | None:
    result = parse_graveyard_card_counter_life(text) or parse_graveyard_permanent_card_counter(text)
    if result is not None:
        return PublicGraveyardCardExileTemplate(result=result)
    targets = "|".join(
        re.escape(kind.value) for kind in PublicGraveyardCardTargetKind
    )
    match = re.fullmatch(
        rf"exile target (?P<target>{targets}) from (?P<owner>a|your|an opponent[\x27’]s) graveyard\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    return PublicGraveyardCardExileTemplate(
        PublicGraveyardCardTargetKind(match.group("target").casefold()),
        owner_relation={"a": "any", "your": "you"}.get(match.group("owner").casefold(), "opponent"),
    )


_SAME_TURN_RETURN_SEATS = {
    ("return to the battlefield all permanent cards in your graveyard that were put there "
     "from the battlefield this turn."): PublicZoneSeatRelation.ACTOR,
    ("each player returns to the battlefield all artifact, creature, enchantment, and land cards "
     "in their graveyard that were put there from the battlefield this turn."): PublicZoneSeatRelation.ANY,
}


def _same_turn_graveyard_return_template(text: str) -> PublicZoneMoveSetTemplate | None:
    normalized = " ".join(text.casefold().split())
    if normalized == (
        "choose up to two target permanent cards in your graveyard that were put there from the battlefield "
        "this turn. return them to the battlefield tapped."
    ):
        return PublicZoneMoveSetTemplate(
            same_turn_graveyard_return_spec(PublicZoneSeatRelation.ACTOR, targeted=True),
            target_schema=targeted_same_turn_graveyard_return_schema(),
        )
    relation = _SAME_TURN_RETURN_SEATS.get(normalized)
    return PublicZoneMoveSetTemplate(same_turn_graveyard_return_spec(relation)) if relation is not None else None


def _graveyard_set_template(text: str) -> PublicZoneMoveSetTemplate | None:
    normalized = " ".join(text.strip().split()).casefold().rstrip(".")
    relation = None
    target_schema = None
    query = ObjectQuerySpec(zones=("graveyard",))
    if normalized in {"exile all graveyards", "exile all cards from all graveyards"}:
        relation = PublicZoneSeatRelation.ANY
    elif normalized == "exile each opponent's graveyard":
        relation = PublicZoneSeatRelation.OPPONENTS
    elif normalized in ("exile target player's graveyard", "exile target opponent's graveyard"):
        relation = PublicZoneSeatRelation.TARGET_PLAYER
        target_schema = _player_target_schema(opponent=normalized == "exile target opponent's graveyard")
    elif normalized == "exile all creature cards from target player's graveyard":
        relation = PublicZoneSeatRelation.TARGET_PLAYER
        target_schema = _player_target_schema()
        query = ObjectQuerySpec(zones=("graveyard",), types_all=("creature",), token=False)
    elif normalized == "exile all creature cards from all graveyards":
        relation = PublicZoneSeatRelation.ANY
        query = ObjectQuerySpec(zones=("graveyard",), types_all=("creature",), token=False)
    if relation is None:
        return None
    return PublicZoneMoveSetTemplate(
        spec=PublicZoneMoveSetSpec(
            query=query,
            origin=PublicZoneOrigin.GRAVEYARD,
            destination=PublicZoneDestination.EXILE,
            relation_axis=PublicZoneRelationAxis.OWNER,
            seat_relation=relation,
            target_seat=(
                "$target.0"
                if relation is PublicZoneSeatRelation.TARGET_PLAYER
                else None
            ),
        ),
        target_schema=target_schema,
    )


def _battlefield_set_template(
    text: str,
    *,
    action: str,
    destination: PublicZoneDestination,
) -> PublicZoneMoveSetTemplate | None:
    match = re.fullmatch(
        rf"{action} (?P<quantifier>all|each) (?P<subject>.+?)"
        r"(?P<relation> target opponent controls| target player controls|"
        r" target opponent owns| target player owns| you control| you own|"
        r" you don't control| your opponents control| your opponents own)?"
        + (
            r" to (?:their owners['’] hands|their owner['’]s hand|their hand|its owner['’]s hand)"
            if destination is PublicZoneDestination.OWNER_HAND
            else ""
        )
        + r"\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    parsed = fixed_affected_permanent_query(match.group("subject"),
        singular=match['quantifier'].casefold() == 'each')
    relation = _relation(match.group("relation") or "")
    if parsed is None or relation is None:
        return None
    query, exclude_source = parsed
    axis, seat_relation, target_seat, target_schema = relation
    return PublicZoneMoveSetTemplate(
        spec=PublicZoneMoveSetSpec(
            query=query,
            origin=PublicZoneOrigin.BATTLEFIELD,
            destination=destination,
            relation_axis=axis,
            seat_relation=seat_relation,
            target_seat=target_seat,
            exclude_source=exclude_source,
        ),
        target_schema=target_schema,
    )


def _battlefield_exile_template(text: str) -> PublicZoneMoveSetTemplate | None:
    if " ".join(text.casefold().split()).rstrip(".") == "exile all nonland permanents that aren't legendary":
        return PublicZoneMoveSetTemplate(PublicZoneMoveSetSpec(
            query=ObjectQuerySpec(
                zones=("battlefield",), excluded_types=("land",), excluded_supertypes=("legendary",),
            ),
            origin=PublicZoneOrigin.BATTLEFIELD,
            destination=PublicZoneDestination.EXILE,
        ))
    return _battlefield_set_template(
        text,
        action="exile",
        destination=PublicZoneDestination.EXILE,
    )


def _battlefield_return_template(text: str) -> PublicZoneMoveSetTemplate | None:
    if re.fullmatch(
        r"Return each nonland permanent that['’]s not historic to its owner['’]s hand\."
        r"(?: \(Artifacts, legendaries, and Sagas are historic\.\))?",
        " ".join(text.split()), re.IGNORECASE,
    ):
        from ..public_zone_moves import NONHISTORIC_NONLAND_QUERY, PUBLIC_NONHISTORIC_NONLAND_RETURN_VERSION

        return PublicZoneMoveSetTemplate(PublicZoneMoveSetSpec(
            query=NONHISTORIC_NONLAND_QUERY,
            origin=PublicZoneOrigin.BATTLEFIELD,
            destination=PublicZoneDestination.OWNER_HAND,
            schema_version=PUBLIC_NONHISTORIC_NONLAND_RETURN_VERSION,
        ))
    if re.fullmatch(r"Return each creature without a \+1/\+1 counter on it to its owner['’]s hand[.]?",
                    " ".join(text.split()), re.IGNORECASE):
        from .qualified_destruction_queries import creature_counter_absence_query

        return PublicZoneMoveSetTemplate(PublicZoneMoveSetSpec(
            query=creature_counter_absence_query("+1/+1"),
            origin=PublicZoneOrigin.BATTLEFIELD,
            destination=PublicZoneDestination.OWNER_HAND,
        ))
    exception = re.fullmatch(
        r"return all creatures to their owners['’] hands except for "
        r"(?P<subtypes>.+)",
        text.strip().rstrip("."),
        re.IGNORECASE,
    )
    if exception is not None:
        parsed = fixed_affected_permanent_query(
            "creatures except for " + exception.group("subtypes")
        )
        if parsed is None:
            return None
        query, exclude_source = parsed
        return PublicZoneMoveSetTemplate(
            spec=PublicZoneMoveSetSpec(
                query=query,
                origin=PublicZoneOrigin.BATTLEFIELD,
                destination=PublicZoneDestination.OWNER_HAND,
                exclude_source=exclude_source,
            )
        )
    return _battlefield_set_template(
        text,
        action="return",
        destination=PublicZoneDestination.OWNER_HAND,
    )


def _graveyard_card_query(
    subject: str, *, permanent_qualities: bool = False,
) -> ObjectQuerySpec | None:
    normalized = " ".join(subject.casefold().split())
    fields: dict[str, Any] = {"zones": ("graveyard",)}
    if not normalized:
        return ObjectQuerySpec(**fields, token=False)
    if normalized.startswith("legendary "):
        fields["supertypes_all"] = ("legendary",)
        normalized = normalized.removeprefix("legendary ")
    elif normalized.startswith("basic "):
        fields["supertypes_all"] = ("basic",)
        normalized = normalized.removeprefix("basic ")
    permanent_types = ("artifact", "battle", "creature", "enchantment", "land", "planeswalker")
    if permanent_qualities and normalized == "nonland permanent":
        if fields.get("supertypes_all") == ("basic",):
            return None
        return ObjectQuerySpec(**fields, types_any=permanent_types, excluded_types=("land",), token=False)
    subtype = re.fullmatch(r"(?P<subtype>.+) (?P<domain>creature|permanent)", normalized)
    if permanent_qualities and subtype is not None and not fields.get("supertypes_all") == ("basic",):
        canonical = canonical_creature_subtype(subtype["subtype"])
        if canonical is not None:
            fields["subtypes_any"] = (canonical,)
            fields["types_all" if subtype["domain"] == "creature" else "types_any"] = (
                ("creature",) if subtype["domain"] == "creature" else permanent_types)
            return ObjectQuerySpec(**fields, token=False)
    type_words = {
        "artifact": "artifact",
        "battle": "battle",
        "creature": "creature",
        "enchantment": "enchantment",
        "land": "land",
        "planeswalker": "planeswalker",
        "instant": "instant",
        "sorcery": "sorcery",
    }
    if normalized == "permanent":
        if fields.get('supertypes_all') == ('basic',):
            return None
        fields['types_any'] = ('artifact', 'battle', 'creature', 'enchantment', 'land', 'planeswalker')
        return ObjectQuerySpec(**fields, token=False)
    parts = tuple(
        part.strip()
        for part in normalized.replace(", and ", ", ")
        .replace(" and ", ", ")
        .split(",")
    )
    if not parts or any(part not in type_words for part in parts):
        return None
    values = tuple(sorted(type_words[part] for part in parts))
    if set(values).intersection({'instant','sorcery'}):
        fields['token'] = False
    if len(values) == 1:
        fields["types_all"] = values
    else:
        fields["types_any"] = values
    if fields.get("supertypes_all") == ("basic",) and values != ("land",):
        return None
    return ObjectQuerySpec(**fields)


def _graveyard_power_return_template(text: str) -> PublicZoneMoveSetTemplate | None:
    match = re.fullmatch(
        r"Return all creature cards with power (?P<maximum>0|[1-9]\d*) or less "
        r"from your graveyard to your hand\.",
        " ".join(text.split()), re.IGNORECASE,
    )
    if match is None:
        return None
    return PublicZoneMoveSetTemplate(spec=public_graveyard_power_return_spec(int(match["maximum"])))


def _graveyard_public_move_template(
    text: str,
) -> PublicZoneMoveSetTemplate | None:
    normalized = " ".join(text.strip().split())
    colors = re.fullmatch(
        r"Each player returns all (?P<first>white|blue|black|red|green) and all "
        r"(?P<second>white|blue|black|red|green) creature cards from their graveyard to the battlefield\.",
        normalized, re.IGNORECASE,
    )
    if colors is not None:
        symbols = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
        color_union = tuple(sorted({symbols[colors[key].casefold()] for key in ("first", "second")}))
        if len(color_union) != 2:
            return None
        return PublicZoneMoveSetTemplate(spec=PublicZoneMoveSetSpec(
            query=ObjectQuerySpec(zones=("graveyard",), types_all=("creature",), colors_any=color_union, token=False),
            origin=PublicZoneOrigin.GRAVEYARD,
            destination=PublicZoneDestination.BATTLEFIELD,
            relation_axis=PublicZoneRelationAxis.OWNER,
            seat_relation=PublicZoneSeatRelation.ANY,
            battlefield_controller=PublicBattlefieldController.OWNER,
            schema_version=PUBLIC_AURA_ENTRY_VERSION,
        ))
    ordered_hand = re.fullmatch(
        r"Return to your hand all (?:(?P<subject>.+?) )?cards in your graveyard\.?",
        normalized,
        re.IGNORECASE,
    )
    if ordered_hand is not None:
        query = _graveyard_card_query(ordered_hand.group("subject") or '')
        return (
            PublicZoneMoveSetTemplate(
                spec=PublicZoneMoveSetSpec(
                    query=query,
                    origin=PublicZoneOrigin.GRAVEYARD,
                    destination=PublicZoneDestination.OWNER_HAND,
                    relation_axis=PublicZoneRelationAxis.OWNER,
                    seat_relation=PublicZoneSeatRelation.ACTOR,
                    schema_version=2,
                )
            )
            if query is not None
            else None
        )
    match = re.fullmatch(
        r"(?P<verb>Return|Put) all (?:(?P<subject>.+?) )?cards "
        r"(?:with mana value (?P<mana_value>0|[1-9]\d*) or less )?from "
        r"(?P<scope>your graveyard|all graveyards) "
        r"(?P<direction>to|onto) (?P<destination>your hand|the battlefield)"
        r"(?P<tapped> tapped)?"
        r"(?: under (?P<controller>your|their owners['’]) control)?\.?",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    verb = match.group("verb").casefold()
    direction = match.group("direction").casefold()
    destination_text = match.group("destination").casefold()
    if (verb == "return" and direction != "to") or (
        verb == "put" and direction != "onto"
    ):
        return None
    battlefield_entry = destination_text == "the battlefield"
    subject = match.group("subject") or ''
    query = (_graveyard_card_query(subject, permanent_qualities=True)
        if battlefield_entry else _graveyard_card_query(subject))
    if query is None:
        return None
    if match["mana_value"] is not None:
        maximum = int(match["mana_value"])
        if not battlefield_entry or maximum > 20:
            return None
        query = replace(query, mana_value_max=maximum, token=False)
    destination = (
        PublicZoneDestination.OWNER_HAND
        if destination_text == "your hand"
        else PublicZoneDestination.BATTLEFIELD
    )
    types = set((*query.types_all, *query.types_any))
    if destination is PublicZoneDestination.BATTLEFIELD and (
        not types or types.intersection({"instant", "sorcery"})
    ):
        return None
    scope = match.group("scope").casefold()
    controller = (match.group("controller") or "").casefold().replace("’", "'")
    battlefield_controller = None
    if destination is PublicZoneDestination.BATTLEFIELD:
        battlefield_controller = (
            PublicBattlefieldController.ACTOR
            if controller == "your"
            else PublicBattlefieldController.OWNER
        )
        if scope == "all graveyards" and not controller:
            return None
    elif match.group("tapped") or match.group("controller"):
        return None
    return PublicZoneMoveSetTemplate(
        spec=PublicZoneMoveSetSpec(
            query=query,
            origin=PublicZoneOrigin.GRAVEYARD,
            destination=destination,
            relation_axis=PublicZoneRelationAxis.OWNER,
            seat_relation=(
                PublicZoneSeatRelation.ACTOR
                if scope == "your graveyard"
                else PublicZoneSeatRelation.ANY
            ),
            battlefield_controller=battlefield_controller,
            battlefield_tapped=bool(match.group("tapped")),
            schema_version=PUBLIC_AURA_ENTRY_VERSION if battlefield_entry else 2,
        )
    )


@dataclass(frozen=True, slots=True)
class OwnGraveyardTypeReturnTemplate:
    def compiled(self):
        return (
            "own-graveyard-permanent-type-return-v1", (own_graveyard_type_return_choice_effect(),), None,
            ("return-to-owner-hand", "fixed-public-zone-move", "fixed-public-zone-move-set"),
        )


def _own_graveyard_type_return_template(text: str) -> OwnGraveyardTypeReturnTemplate | None:
    normalized = " ".join(text.casefold().split())
    if normalized == (
        "choose a permanent type. return all cards of the chosen type from your graveyard to your hand."
    ):
        return OwnGraveyardTypeReturnTemplate()
    return None


_SAME_NAME_ZONE_GRAMMAR = {
    "return target nonland permanent and all other permanents with the same name as that permanent to their owners' hands.":
        SameNameZoneMoveScope.RETURN_BATTLEFIELD_PERMANENTS,
    "exile target creature and all other creatures with the same name as that creature.":
        SameNameZoneMoveScope.EXILE_BATTLEFIELD_CREATURES,
    "return target creature card and all other cards with the same name as that card from your graveyard to your hand.":
        SameNameZoneMoveScope.RETURN_OWN_GRAVEYARD_CARDS,
}


def _same_name_zone_move_template(text):
    scope = _SAME_NAME_ZONE_GRAMMAR.get(" ".join(text.casefold().strip().split()))
    if scope is None:
        return None
    return PublicZoneMoveSetTemplate(scope.spec(), target_schema=scope.target_schema())


class ChosenColorPermanentReturnTemplate:
    """One fixed color choice over the existing public return sets."""

    def compiled(self):
        from ..rules.public_zone_move_capability_shapes import (
            CHOSEN_COLOR_PERMANENT_RETURN_TEMPLATE, chosen_color_permanent_return_effect,
        )

        return (
            CHOSEN_COLOR_PERMANENT_RETURN_TEMPLATE, (chosen_color_permanent_return_effect(),), None,
            ("return-to-owner-hand", "fixed-public-zone-move", "fixed-public-zone-move-set"),
        )


def chosen_color_permanent_return_template(text: str) -> ChosenColorPermanentReturnTemplate | None:
    if re.fullmatch(r"Return all permanents of the color of your choice to their owners['’] hands[.]?",
                    text.strip(), re.IGNORECASE) is None:
        return None
    return ChosenColorPermanentReturnTemplate()


class ChosenTypeTapStateTemplate:
    """One original target followed by an exact type and tap-state choice."""

    def compiled(self):
        from ..rules.public_zone_move_capability_shapes import (
            CHOSEN_TYPE_TAP_STATE_MECHANICS, CHOSEN_TYPE_TAP_STATE_TARGET,
            CHOSEN_TYPE_TAP_STATE_TEMPLATE, chosen_type_tap_state_effect,
        )

        return (
            CHOSEN_TYPE_TAP_STATE_TEMPLATE, (chosen_type_tap_state_effect(),),
            dict(CHOSEN_TYPE_TAP_STATE_TARGET), CHOSEN_TYPE_TAP_STATE_MECHANICS,
        )


def chosen_type_tap_state_template(text: str) -> ChosenTypeTapStateTemplate | None:
    if " ".join(text.casefold().split()) != (
        "choose artifact, creature, or land. tap all untapped permanents of the chosen type target player "
        "controls, or untap all tapped permanents of that type that player controls."
    ):
        return None
    return ChosenTypeTapStateTemplate()


@dataclass(frozen=True, slots=True)
class FixedMixedZoneExileTemplate:
    scope: MixedZoneExileScope

    def compiled(self):
        spec = FixedMixedZoneExileSpec(self.scope)
        version = spec.to_dict()["schema_version"]
        return f"fixed-mixed-zone-exile-v{version}", (spec.effect(),), None, ("exile",)


def _fixed_mixed_zone_exile_template(text: str) -> FixedMixedZoneExileTemplate | None:
    normalized = " ".join(text.casefold().split())
    if normalized == (
        "exile all the cards from your hand, then draw that many cards. "
        "until the end of your next turn, you may play cards exiled this way."
    ):
        return FixedMixedZoneExileTemplate(MixedZoneExileScope.CONTROLLER_HAND_DRAW_AND_PLAY)
    if normalized == "exile all cards from all hands and graveyards.":
        return FixedMixedZoneExileTemplate(MixedZoneExileScope.HANDS_AND_GRAVEYARDS)
    if normalized == ("exile all artifacts, creatures, and lands from the battlefield, "
                      "all cards from all graveyards, and all cards from all hands."):
        return FixedMixedZoneExileTemplate(MixedZoneExileScope.ARTIFACTS_CREATURES_LANDS_HANDS_AND_GRAVEYARDS)
    return None


def public_zone_move_effect_template(
    text: str,
) -> (
    PublicGraveyardCardExileTemplate
    | PublicZoneMoveSetTemplate
    | FixedMixedZoneExileTemplate
    | OwnGraveyardTypeReturnTemplate
    | FixedPublicTapStateSetTemplate
    | ChosenColorPermanentReturnTemplate
    | ChosenTypeTapStateTemplate
    | None
):
    return (
        _fixed_mixed_zone_exile_template(text)
        or chosen_type_tap_state_template(text)
        or chosen_color_permanent_return_template(text)
        or _same_name_zone_move_template(text)
        or _own_graveyard_type_return_template(text)
        or public_graveyard_card_exile_template(text)
        or _same_turn_graveyard_return_template(text)
        or _graveyard_set_template(text)
        or _graveyard_power_return_template(text)
        or _graveyard_public_move_template(text)
        or _battlefield_exile_template(text)
        or _battlefield_return_template(text)
        or fixed_public_tap_state_set_effect_template(text)
    )


__all__ = [
    "PublicGraveyardCardExileTemplate",
    "PublicZoneMoveSetTemplate",
    "public_graveyard_card_exile_template",
    "public_zone_move_effect_template",
]
