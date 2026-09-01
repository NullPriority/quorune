from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from ..fixed_damage_set_model import (
    FixedDamageSetSpec,
    PermanentControllerRelation,
    PermanentDamageGroup,
    PlayerDamageGroup,
    PlayerDamageRelation,
)
from ..object_predicate import ObjectQuerySpec
from ..rules.source_references import SourceReferenceSpec
from .direct_target import (
    DirectPermanentTargetSpec,
    direct_permanent_target_spec,
)
from .fixed_all_damage_prevention import (
    fixed_all_damage_prevention_scope_descriptor,
    fixed_all_damage_prevention_specs,
)


_ABILITY_WORD = re.compile(
    r"^(?P<word>[A-Za-z][A-Za-z ']+)\s+[—-]\s+(?P<body>.+)$"
)
_DAMAGE_QUANTITY_REPLACEMENT = re.compile(
    r"^If (?P<source>.+?) would deal "
    r"(?:(?P<damage_kind>combat|noncombat) )?damage to (?P<target>.+?), "
    r"(?:it|that source) deals double that damage"
    r"(?: to (?:that permanent or player|that player|that player or permanent))? "
    r"instead\.?$",
    re.IGNORECASE,
)
_DAMAGE_ADDITIVE_REPLACEMENT = re.compile(
    r"^If (?P<source>.+?) would deal "
    r"(?:(?P<damage_kind>combat|noncombat) )?damage to (?P<target>.+?), "
    r"(?:it|that source) deals that much damage plus "
    r"(?P<additional>[1-9][0-9]*)"
    r"(?: to (?:that permanent or player|that player|that player or permanent))? "
    r"instead\.?$",
    re.IGNORECASE,
)
_FIXED_DAMAGE_PREVENTION = re.compile(
    r"^If (?P<source>.+?) would deal "
    r"(?:(?P<damage_kind>combat|noncombat) )?damage to (?P<target>.+?), "
    r"prevent (?P<amount>[1-9][0-9]*) of that damage\.?$",
    re.IGNORECASE,
)
_REDIRECT_TO_SOURCE = re.compile(
    r"^All damage that would be dealt to you"
    r"(?P<permanents> and other permanents you control)? is dealt to "
    r"this (?:creature|permanent) instead\.?$",
    re.IGNORECASE,
)


class FixedDamageRecipient(str, Enum):
    """Closed recipient vocabulary for fixed direct-damage instructions."""

    ANY_TARGET = "any_target"
    CREATURE = "creature"
    CREATURE_OR_PLANESWALKER = "creature_or_planeswalker"
    PLAYER = "player"
    OPPONENT = "opponent"
    PLAYER_OR_PLANESWALKER = "player_or_planeswalker"
    OPPONENT_OR_PLANESWALKER = "opponent_or_planeswalker"
    EACH_OPPONENT = "each_opponent"


_FIXED_DAMAGE_RECIPIENTS: tuple[tuple[str, FixedDamageRecipient], ...] = (
    ("target creature or planeswalker", FixedDamageRecipient.CREATURE_OR_PLANESWALKER),
    ("target player or planeswalker", FixedDamageRecipient.PLAYER_OR_PLANESWALKER),
    ("target opponent or planeswalker", FixedDamageRecipient.OPPONENT_OR_PLANESWALKER),
    ("target creature", FixedDamageRecipient.CREATURE),
    ("target player", FixedDamageRecipient.PLAYER),
    ("target opponent", FixedDamageRecipient.OPPONENT),
    ("any target", FixedDamageRecipient.ANY_TARGET),
    ("each opponent", FixedDamageRecipient.EACH_OPPONENT),
)
_FIXED_DAMAGE_SOURCE_KINDS = (
    "artifact",
    "battle",
    "creature",
    "enchantment",
    "land",
    "permanent",
    "planeswalker",
    "spell",
)


@dataclass(frozen=True, slots=True)
class FixedDamageEffectTemplate:
    """Typed lowering result for one positive fixed-damage instruction."""

    amount: int
    recipient: FixedDamageRecipient
    source_kind: str | None = None
    target_spec: DirectPermanentTargetSpec | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise ValueError("Fixed damage amount must be an integer")
        if self.amount <= 0:
            raise ValueError("Fixed damage amount must be positive")
        if not isinstance(self.recipient, FixedDamageRecipient):
            raise ValueError("Fixed damage recipient is unsupported")
        if self.source_kind is not None and self.source_kind not in (
            *_FIXED_DAMAGE_SOURCE_KINDS,
            "named",
        ):
            raise ValueError("Fixed damage source kind is unsupported")
        if self.target_spec is not None and (
            not isinstance(self.target_spec, DirectPermanentTargetSpec)
            or self.target_spec.combat_state is None
            or self.recipient is not FixedDamageRecipient.CREATURE
        ):
            raise ValueError(
                "Fixed damage direct target requires one combat-state creature"
            )

    @property
    def template_id(self) -> str:
        if self.target_spec is not None:
            return f"damage-{self.target_spec.slug}-v1"
        if self.recipient is FixedDamageRecipient.ANY_TARGET:
            if self.source_kind not in {None, "named", "spell"}:
                return f"damage-any-target-self-{self.source_kind}-v1"
            return "damage-any-target-v1"
        return f"damage-{self.recipient.value.replace('_', '-')}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        if self.recipient is FixedDamageRecipient.EACH_OPPONENT:
            return (
                {
                    "op": "damage_each_opponent",
                    "source": "$source",
                    "amount": self.amount,
                },
            )
        return (
            {
                "op": "damage",
                "source": "$source",
                "target": "$target.0",
                "amount": self.amount,
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        if self.target_spec is not None:
            return self.target_spec.to_target_schema()
        if self.recipient is FixedDamageRecipient.EACH_OPPONENT:
            return None
        if self.recipient is FixedDamageRecipient.ANY_TARGET:
            return {
                "zones": ["player", "battlefield"],
                "categories": ["player", "permanent"],
                "predicate": "damageable",
                "count": 1,
            }
        if self.recipient is FixedDamageRecipient.CREATURE:
            return {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "types_any": ["creature"],
                "count": 1,
            }
        if self.recipient is FixedDamageRecipient.CREATURE_OR_PLANESWALKER:
            return {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "types_any": ["creature", "planeswalker"],
                "count": 1,
            }
        if self.recipient in {
            FixedDamageRecipient.PLAYER_OR_PLANESWALKER,
            FixedDamageRecipient.OPPONENT_OR_PLANESWALKER,
        }:
            schema: dict[str, Any] = {
                "zones": ["player", "battlefield"],
                "categories": ["player", "permanent"],
                "predicate": "player_or_planeswalker",
                "count": 1,
            }
            if self.recipient is FixedDamageRecipient.OPPONENT_OR_PLANESWALKER:
                schema["player_relation"] = "opponent"
            return schema
        schema = {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
        }
        if self.recipient is FixedDamageRecipient.OPPONENT:
            schema["player_relation"] = "opponent"
        return schema

    @property
    def mechanics(self) -> tuple[str, ...]:
        if self.recipient is FixedDamageRecipient.EACH_OPPONENT:
            return ("cr-120-damage",)
        return ("cr-120-damage", "cr-115-targets")

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


@dataclass(frozen=True, slots=True)
class FixedMassDamageEffectTemplate:
    """Typed lowering for one fixed simultaneous affected-set instruction."""

    amount: int
    spec: FixedDamageSetSpec
    source_kind: str | None = None
    target_opponent: bool = False

    def __post_init__(self) -> None:
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError("Fixed mass damage amount must be positive")
        if not isinstance(self.spec, FixedDamageSetSpec):
            raise ValueError("Fixed mass damage requires a typed recipient set")
        if self.source_kind is not None and self.source_kind not in (
            *_FIXED_DAMAGE_SOURCE_KINDS,
            "named",
        ):
            raise ValueError("Fixed mass damage source kind is unsupported")
        if type(self.target_opponent) is not bool:
            raise ValueError("Fixed mass target marker must be boolean")

    @property
    def template_id(self) -> str:
        return (
            "damage-fixed-target-opponent-controlled-set-v1"
            if self.target_opponent
            else "damage-fixed-simultaneous-set-v1"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "damage_fixed_set",
                "source": "$source",
                "amount": self.amount,
                "groups": self.spec.to_dict()["groups"],
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        if not self.target_opponent:
            return None
        return {
            "zones": ["player"],
            "categories": ["player"],
            "player_relation": "opponent",
            "count": 1,
        }

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (
            ("cr-120-damage", "cr-115-targets")
            if self.target_opponent
            else ("cr-120-damage",)
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


def _permanent_group(
    *,
    types_all: tuple[str, ...] = (),
    types_any: tuple[str, ...] = (),
    excluded_types: tuple[str, ...] = (),
    colors_any: tuple[str, ...] = (),
    keywords_all: tuple[str, ...] = (),
    token: bool | None = None,
    controller_relation: PermanentControllerRelation = (
        PermanentControllerRelation.ANY
    ),
    target_controller: str | None = None,
) -> PermanentDamageGroup:
    return PermanentDamageGroup(
        query=ObjectQuerySpec(
            zones=("battlefield",),
            types_all=types_all,
            types_any=types_any,
            excluded_types=excluded_types,
            colors_any=colors_any,
            keywords_all=keywords_all,
            token=token,
        ),
        controller_relation=controller_relation,
        target_controller=target_controller,
    )


_COLOR_WORDS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}


def _fixed_mass_damage_spec(
    recipient_text: str,
) -> tuple[FixedDamageSetSpec, bool] | None:
    normalized = " ".join(recipient_text.casefold().split())
    exact: dict[str, tuple[object, ...]] = {
        "each creature": (_permanent_group(types_all=("creature",)),),
        "each player": (PlayerDamageGroup(PlayerDamageRelation.ALL),),
        "each creature and each player": (
            _permanent_group(types_all=("creature",)),
            PlayerDamageGroup(PlayerDamageRelation.ALL),
        ),
        "each creature and each planeswalker": (
            _permanent_group(types_any=("creature", "planeswalker")),
        ),
        "each opponent and each creature they control": (
            PlayerDamageGroup(PlayerDamageRelation.OPPONENTS),
            _permanent_group(
                types_all=("creature",),
                controller_relation=PermanentControllerRelation.OPPONENTS,
            ),
        ),
        "each opponent and each creature and planeswalker they control": (
            PlayerDamageGroup(PlayerDamageRelation.OPPONENTS),
            _permanent_group(
                types_any=("creature", "planeswalker"),
                controller_relation=PermanentControllerRelation.OPPONENTS,
            ),
        ),
        "each opponent and each creature and each planeswalker they control": (
            PlayerDamageGroup(PlayerDamageRelation.OPPONENTS),
            _permanent_group(
                types_any=("creature", "planeswalker"),
                controller_relation=PermanentControllerRelation.OPPONENTS,
            ),
        ),
        "each creature opponents control": (
            _permanent_group(
                types_all=("creature",),
                controller_relation=PermanentControllerRelation.OPPONENTS,
            ),
        ),
        "each creature your opponents control": (
            _permanent_group(
                types_all=("creature",),
                controller_relation=PermanentControllerRelation.OPPONENTS,
            ),
        ),
        "each creature and planeswalker your opponents control": (
            _permanent_group(
                types_any=("creature", "planeswalker"),
                controller_relation=PermanentControllerRelation.OPPONENTS,
            ),
        ),
        "each creature with flying": (
            _permanent_group(
                types_all=("creature",), keywords_all=("flying",)
            ),
        ),
        "each nonartifact creature": (
            _permanent_group(
                types_all=("creature",), excluded_types=("artifact",)
            ),
        ),
        "each nontoken creature": (
            _permanent_group(types_all=("creature",), token=False),
        ),
        "each creature with shadow": (
            _permanent_group(
                types_all=("creature",), keywords_all=("shadow",)
            ),
        ),
    }
    if normalized in exact:
        return FixedDamageSetSpec(exact[normalized]), False
    if normalized == "each creature target opponent controls":
        return (
            FixedDamageSetSpec(
                (
                    _permanent_group(
                        types_all=("creature",),
                        controller_relation=(
                            PermanentControllerRelation.TARGET_PLAYER
                        ),
                        target_controller="$target.0",
                    ),
                )
            ),
            True,
        )
    color_match = re.fullmatch(
        r"each (?P<colors>white|blue|black|red|green)"
        r"(?: and/or (?P<second>white|blue|black|red|green))? creature",
        normalized,
    )
    if color_match is not None:
        colors = tuple(
            _COLOR_WORDS[value]
            for value in (
                color_match.group("colors"),
                color_match.group("second"),
            )
            if value is not None
        )
        if len(colors) != len(set(colors)):
            return None
        return (
            FixedDamageSetSpec(
                (
                    _permanent_group(
                        types_all=("creature",), colors_any=colors
                    ),
                )
            ),
            False,
        )
    return None


def _fixed_damage_instruction_template(
    *,
    amount: int,
    recipient_text: str,
    source_kind: str | None,
) -> FixedDamageEffectTemplate | FixedMassDamageEffectTemplate | None:
    """Build one fixed-damage instruction from already-bounded grammar."""

    recipient_text = recipient_text.casefold()
    fixed_set = _fixed_mass_damage_spec(recipient_text)
    if fixed_set is not None:
        spec, target_opponent = fixed_set
        return FixedMassDamageEffectTemplate(
            amount=amount,
            spec=spec,
            source_kind=source_kind,
            target_opponent=target_opponent,
        )
    target_spec = direct_permanent_target_spec(recipient_text)
    if target_spec is not None and target_spec.combat_state is not None:
        return FixedDamageEffectTemplate(
            amount=amount,
            recipient=FixedDamageRecipient.CREATURE,
            source_kind=source_kind,
            target_spec=target_spec,
        )
    recipient = next(
        (
            value
            for phrase, value in _FIXED_DAMAGE_RECIPIENTS
            if recipient_text == phrase
        ),
        None,
    )
    if recipient is None:
        return None
    return FixedDamageEffectTemplate(
        amount=amount,
        recipient=recipient,
        source_kind=source_kind,
    )


def fixed_damage_effect_template(
    text: str,
    *,
    card_name: str,
) -> FixedDamageEffectTemplate | FixedMassDamageEffectTemplate | None:
    """Recognize one whole fixed-damage clause without interpreting riders."""

    source = re.fullmatch(
        r"(?P<source>.+?) deals "
        r"(?P<amount>[1-9][0-9]*) damage to (?P<recipient>.+?)\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if source is None:
        return None
    source_kind = re.fullmatch(
        rf"this (?P<kind>{'|'.join(_FIXED_DAMAGE_SOURCE_KINDS)})",
        source.group("source"),
        re.IGNORECASE,
    )
    if source_kind is None and not SourceReferenceSpec(card_name).matches(
        source.group("source")
    ):
        return None
    return _fixed_damage_instruction_template(
        amount=int(source.group("amount")),
        recipient_text=source.group("recipient"),
        source_kind=(
            source_kind.group("kind").casefold()
            if source_kind is not None
            else "named"
        ),
    )


def source_pronoun_damage_effect_template(
    text: str,
) -> FixedDamageEffectTemplate | FixedMassDamageEffectTemplate | None:
    """Recognize ``It deals`` only after a source identity is established."""

    source = re.fullmatch(
        r"it deals (?P<amount>[1-9][0-9]*) damage to "
        r"(?P<recipient>.+?)\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if source is None:
        return None
    return _fixed_damage_instruction_template(
        amount=int(source.group("amount")),
        recipient_text=source.group("recipient"),
        source_kind=None,
    )


def activated_source_damage_effect_template(
    text: str,
) -> FixedDamageEffectTemplate | FixedMassDamageEffectTemplate | None:
    """Compatibility entry point for source-bound activated abilities."""

    return source_pronoun_damage_effect_template(text)


def _source_condition(phrase: str) -> tuple[str, list[str]] | None:
    normalized = " ".join(phrase.casefold().split())
    exact = {
        "a source": ("any", []),
        "a source you control": ("source_controller", []),
        "a source an opponent controls": ("opponent", []),
        "a creature": ("any", ["creature"]),
        "an artifact": ("any", ["artifact"]),
    }
    if normalized in exact:
        return exact[normalized]
    controlled = re.fullmatch(
        r"(?:a|an) (?P<kind>[a-z][a-z0-9-]*)(?: source)? you control",
        normalized,
    )
    if controlled:
        return "source_controller", [controlled.group("kind")]
    return None


def _additive_source_condition(
    phrase: str,
) -> tuple[str, list[str], list[str], list[str], bool] | None:
    normalized = " ".join(phrase.casefold().split())
    exact = {
        "a source": ("any", [], [], [], False),
        "a source you control": ("source_controller", [], [], [], False),
        "another source you control": (
            "source_controller",
            [],
            [],
            [],
            True,
        ),
        "a red source": ("any", [], [], ["R"], False),
        "a red source you control": (
            "source_controller",
            [],
            [],
            ["R"],
            False,
        ),
        "another red source you control": (
            "source_controller",
            [],
            [],
            ["R"],
            True,
        ),
        "a spell": ("any", [], ["instant", "sorcery"], [], False),
        "a red spell": ("any", [], ["instant", "sorcery"], ["R"], False),
        "an instant or sorcery source you control": (
            "source_controller",
            [],
            ["instant", "sorcery"],
            [],
            False,
        ),
        "a red instant or sorcery spell you control or a red planeswalker you control": (
            "source_controller",
            [],
            ["instant", "planeswalker", "sorcery"],
            ["R"],
            False,
        ),
        "a lizard, mouse, otter, or raccoon you control": (
            "source_controller",
            [],
            ["lizard", "mouse", "otter", "raccoon"],
            [],
            False,
        ),
    }
    return exact.get(normalized)


def _target_condition(
    phrase: str,
) -> tuple[str, list[str], list[str]] | None:
    normalized = " ".join(phrase.casefold().split())
    exact = {
        "a permanent or player": ("any", [], []),
        "a player or permanent": ("any", [], []),
        "a player": ("any", ["player"], []),
        "an opponent": ("opponent", ["player"], []),
        "you": ("source_controller", ["player"], []),
        "an opponent or a permanent an opponent controls": (
            "opponent",
            [],
            [],
        ),
        "you or a permanent you control": (
            "source_controller",
            [],
            [],
        ),
        "a permanent an opponent controls": (
            "opponent",
            ["permanent"],
            [],
        ),
    }
    if normalized in exact:
        return exact[normalized]
    controlled = re.fullmatch(
        r"(?:a|an) (?:(?P<qualifier>[a-z][a-z0-9-]*) )?"
        r"(?P<kind>creature|planeswalker|battle|permanent) you control",
        normalized,
    )
    if controlled:
        types = [controlled.group("kind")]
        if controlled.group("qualifier"):
            types.append(controlled.group("qualifier"))
        return "source_controller", ["permanent"], types
    ordinary = re.fullmatch(
        r"(?:a|an) (?P<kind>creature|planeswalker|battle|permanent|"
        r"[a-z][a-z0-9-]*)",
        normalized,
    )
    if ordinary:
        kind = ordinary.group("kind")
        return "any", ["permanent"], [kind]
    return None


def _additive_damage_handler(
    text: str,
) -> tuple[str, dict[str, Any], str] | None:
    match = _DAMAGE_ADDITIVE_REPLACEMENT.fullmatch(text)
    if match is None:
        return None
    source = _additive_source_condition(match.group("source"))
    target = _target_condition(match.group("target"))
    if source is None or target is None:
        return None
    (
        source_relation,
        source_types_all,
        source_types_any,
        source_colors_all,
        exclude_source_ref,
    ) = source
    target_relation, target_kinds, target_types = target
    damage_kind = match.group("damage_kind")
    return (
        "damage-quantity-additive-static-v2",
        {
            "handler_id": "replacement.damage.quantity.v2",
            "schema_version": 2,
            "event": "damage",
            "condition": {
                "source_controller_relation": source_relation,
                "target_controller_relation": target_relation,
                "target_kinds": target_kinds,
                "source_types_all": source_types_all,
                "source_types_any": source_types_any,
                "source_colors_all": source_colors_all,
                "target_types_all": target_types,
                "combat": (
                    True
                    if damage_kind and damage_kind.casefold() == "combat"
                    else False
                    if damage_kind
                    else None
                ),
                "exclude_source_ref": exclude_source_ref,
            },
            "modification": {
                "multiplier": 1,
                "additional": int(match.group("additional")),
            },
        },
        "damage.replacement.static_quantity",
    )


def static_damage_handler(
    text: str,
    *,
    card_name: str = "",
) -> tuple[str, dict[str, Any], str] | None:
    """Lower a closed static damage/prevention wording family."""

    ability_word = _ABILITY_WORD.match(text)
    normalized = ability_word.group("body") if ability_word else text
    redirection = _REDIRECT_TO_SOURCE.fullmatch(normalized)
    if redirection is not None:
        target_kinds = ["player"]
        if redirection.group("permanents"):
            target_kinds.append("permanent")
        return (
            "damage-redirection-static-to-source-v1",
            {
                "handler_id": "replacement.damage.redirect-to-source.v1",
                "schema_version": 1,
                "event": "damage",
                "condition": {
                    "source_controller_relation": "any",
                    "target_controller_relation": "source_controller",
                    "target_kinds": target_kinds,
                    "source_types_all": [],
                    "target_types_all": [],
                    "combat": None,
                },
                "modification": {"destination": "source"},
            },
            "damage.redirection.static_to_source",
        )
    all_prevention = fixed_all_damage_prevention_specs(
        normalized,
        card_name=card_name,
    )
    if all_prevention is not None and all(
        spec.duration == "static"
        and spec.source.selected_target is None
        and spec.recipient.selected_target is None
        for spec in all_prevention
    ):
        return (
            "damage-prevention-fixed-all-scopes-static-v1",
            {
                "handler_id": "prevention.damage.all.v1",
                "schema_version": 1,
                "event": "damage",
                "condition": {
                    "scopes": [
                        {
                            "damage_kind": spec.damage_kind,
                            "source_controller_turn_only": (
                                spec.source_controller_turn_only
                            ),
                            "scope": (
                                fixed_all_damage_prevention_scope_descriptor(
                                    spec
                                )
                            ),
                        }
                        for spec in all_prevention
                    ]
                },
                "modification": {"amount": "all"},
            },
            "damage.prevention.persistent_amount",
        )
    match = _DAMAGE_QUANTITY_REPLACEMENT.fullmatch(normalized)
    handler_id = "replacement.damage.quantity.v1"
    capability = "damage.replacement.static_quantity"
    modification: dict[str, int] = {"multiplier": 2, "additional": 0}
    template_id = "damage-quantity-double-static-v1"
    if match is None:
        additive = _additive_damage_handler(normalized)
        if additive is not None:
            return additive
        match = _FIXED_DAMAGE_PREVENTION.fullmatch(normalized)
        handler_id = "prevention.damage.fixed.v1"
        capability = "damage.prevention.static_fixed"
        template_id = "damage-prevention-fixed-static-v1"
        if match is None:
            return None
        modification = {"amount": int(match.group("amount"))}
    source = _source_condition(match.group("source"))
    target = _target_condition(match.group("target"))
    if source is None or target is None:
        return None
    source_relation, source_types = source
    target_relation, target_kinds, target_types = target
    damage_kind = match.group("damage_kind")
    return (
        template_id,
        {
            "handler_id": handler_id,
            "schema_version": 1,
            "event": "damage",
            "condition": {
                "source_controller_relation": source_relation,
                "target_controller_relation": target_relation,
                "target_kinds": target_kinds,
                "source_types_all": source_types,
                "target_types_all": target_types,
                "combat": (
                    True
                    if damage_kind and damage_kind.casefold() == "combat"
                    else False
                    if damage_kind
                    else None
                ),
            },
            "modification": modification,
        },
        capability,
    )
