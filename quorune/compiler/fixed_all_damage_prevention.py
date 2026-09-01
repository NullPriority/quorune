from __future__ import annotations

"""Closed compiler grammar for fixed all-damage prevention scopes."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..creature_subtypes import canonical_creature_subtype
from ..rules.source_references import SourceReferenceSpec
from .direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec


_CONTROLLER_RELATIONS = {"any", "source_controller", "opponent"}
_DAMAGE_KINDS = {"any", "combat", "noncombat"}
_DURATIONS = {"static", "until_end_of_turn"}
_TARGET_KINDS = {"player", "permanent"}


@dataclass(frozen=True, slots=True)
class FixedDamageObjectScope:
    """One public damage-source or damage-recipient predicate."""

    controller_relation: str = "any"
    kinds: tuple[str, ...] = ()
    characteristics_all: tuple[str, ...] = ()
    characteristics_any: tuple[str, ...] = ()
    characteristics_none: tuple[str, ...] = ()
    colors_any: tuple[str, ...] = ()
    colors_none: tuple[str, ...] = ()
    source_identity: bool = False
    attached_identity: bool = False
    selected_target: DirectPermanentTargetSpec | None = None
    exclude_source_identity: bool = False

    def __post_init__(self) -> None:
        if self.controller_relation not in _CONTROLLER_RELATIONS:
            raise ValueError("Damage scope controller relation is unsupported")
        for field in (
            "kinds",
            "characteristics_all",
            "characteristics_any",
            "characteristics_none",
            "colors_any",
            "colors_none",
        ):
            values = tuple(getattr(self, field))
            if any(type(value) is not str or not value for value in values):
                raise ValueError(f"Damage scope {field} must contain strings")
            if values != tuple(sorted(set(values))):
                raise ValueError(f"Damage scope {field} must be canonical")
        if set(self.kinds) - _TARGET_KINDS:
            raise ValueError("Damage scope object kind is unsupported")
        if sum(
            bool(value)
            for value in (
                self.source_identity,
                self.attached_identity,
                self.selected_target is not None,
                self.exclude_source_identity,
            )
        ) > 1:
            raise ValueError("Damage scope identity predicates conflict")
        if self.selected_target is not None and not isinstance(
            self.selected_target, DirectPermanentTargetSpec
        ):
            raise ValueError("Damage scope selected target must be typed")


@dataclass(frozen=True, slots=True)
class FixedAllDamagePreventionSpec:
    """One immutable all-damage prevention component or modifier scope."""

    damage_kind: str
    duration: str
    source: FixedDamageObjectScope
    recipient: FixedDamageObjectScope
    source_controller_turn_only: bool = False

    def __post_init__(self) -> None:
        if self.damage_kind not in _DAMAGE_KINDS:
            raise ValueError("All-damage prevention kind is unsupported")
        if self.duration not in _DURATIONS:
            raise ValueError("All-damage prevention duration is unsupported")
        if not isinstance(self.source, FixedDamageObjectScope) or not isinstance(
            self.recipient, FixedDamageObjectScope
        ):
            raise ValueError("All-damage prevention requires typed scopes")
        if type(self.source_controller_turn_only) is not bool:
            raise ValueError("All-damage prevention turn scope must be boolean")


_ANY_SCOPE = FixedDamageObjectScope()
_SELF_SCOPE = FixedDamageObjectScope(source_identity=True)
_ATTACHED_SCOPE = FixedDamageObjectScope(attached_identity=True)


_IRREGULAR_CREATURE_PLURALS = {
    "dwarves": "dwarf",
    "elves": "elf",
    "faeries": "faerie",
    "mice": "mouse",
    "wolves": "wolf",
}


def _creature_subtype_from_plural(value: str) -> str | None:
    normalized = value.casefold()
    candidate = _IRREGULAR_CREATURE_PLURALS.get(normalized)
    if candidate is None and normalized.endswith("s"):
        candidate = normalized[:-1]
    return canonical_creature_subtype(candidate or "")


def _canonical_scope(**values: object) -> FixedDamageObjectScope:
    canonical = {
        field: tuple(sorted(set(value))) if isinstance(value, (list, tuple)) else value
        for field, value in values.items()
    }
    return FixedDamageObjectScope(**canonical)


def _recipient_scopes(
    phrase: str,
    *,
    card_name: str,
) -> tuple[FixedDamageObjectScope, ...] | None:
    normalized = " ".join(phrase.casefold().split())
    exact: dict[str, tuple[FixedDamageObjectScope, ...]] = {
        "you": (
            _canonical_scope(
                controller_relation="source_controller", kinds=("player",)
            ),
        ),
        "players": (_canonical_scope(kinds=("player",)),),
        "creatures": (
            _canonical_scope(
                kinds=("permanent",), characteristics_all=("creature",)
            ),
        ),
        "permanents": (_canonical_scope(kinds=("permanent",)),),
        "creatures you control": (
            _canonical_scope(
                controller_relation="source_controller",
                kinds=("permanent",),
                characteristics_all=("creature",),
            ),
        ),
        "other creatures you control": (
            _canonical_scope(
                controller_relation="source_controller",
                kinds=("permanent",),
                characteristics_all=("creature",),
                exclude_source_identity=True,
            ),
        ),
        "permanents you control": (
            _canonical_scope(
                controller_relation="source_controller", kinds=("permanent",)
            ),
        ),
        "other permanents you control": (
            _canonical_scope(
                controller_relation="source_controller",
                kinds=("permanent",),
                exclude_source_identity=True,
            ),
        ),
        "planeswalkers you control": (
            _canonical_scope(
                controller_relation="source_controller",
                kinds=("permanent",),
                characteristics_all=("planeswalker",),
            ),
        ),
        "artifact creatures": (
            _canonical_scope(
                kinds=("permanent",),
                characteristics_all=("artifact", "creature"),
            ),
        ),
        "artifact creatures you control": (
            _canonical_scope(
                controller_relation="source_controller",
                kinds=("permanent",),
                characteristics_all=("artifact", "creature"),
            ),
        ),
        "creatures and planeswalkers you control": (
            _canonical_scope(
                controller_relation="source_controller",
                kinds=("permanent",),
                characteristics_any=("creature", "planeswalker"),
            ),
        ),
    }
    if normalized in exact:
        return exact[normalized]
    combined = {
        "you and creatures you control": "creatures you control",
        "you and permanents you control": "permanents you control",
        "you and other permanents you control": "other permanents you control",
        "you and planeswalkers you control": "planeswalkers you control",
    }
    if normalized in combined:
        return (*exact["you"], *exact[combined[normalized]])
    if normalized in {"this artifact", "this creature", "this permanent"}:
        return (_SELF_SCOPE,)
    if normalized in {"enchanted creature", "equipped creature"}:
        return (_ATTACHED_SCOPE,)
    direct_target = direct_permanent_target_spec(phrase)
    if direct_target is not None:
        return (_canonical_scope(selected_target=direct_target),)
    subtype_match = re.fullmatch(
        r"(?P<plural>[a-z][a-z'-]*) you control", normalized
    )
    if subtype_match is not None:
        subtype = _creature_subtype_from_plural(subtype_match.group("plural"))
        if subtype is not None:
            return (
                _canonical_scope(
                    controller_relation="source_controller",
                    kinds=("permanent",),
                    characteristics_all=("creature", subtype),
                ),
            )
    if card_name and SourceReferenceSpec(card_name).matches(phrase):
        return (_SELF_SCOPE,)
    return None


def _source_scope(
    phrase: str,
    *,
    card_name: str,
) -> FixedDamageObjectScope | None:
    normalized = " ".join(phrase.casefold().split())
    if normalized in {"this artifact", "this creature", "this permanent"}:
        return _SELF_SCOPE
    if normalized in {"enchanted creature", "equipped creature"}:
        return _ATTACHED_SCOPE
    direct_target = direct_permanent_target_spec(phrase)
    if direct_target is not None:
        return _canonical_scope(selected_target=direct_target)
    if card_name and SourceReferenceSpec(card_name).matches(phrase):
        return _SELF_SCOPE

    relation = "any"
    for suffix, candidate in (
        (" your opponents control", "opponent"),
        (" you don't control", "opponent"),
        (" you control", "source_controller"),
    ):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            relation = candidate
            break

    exact: dict[str, FixedDamageObjectScope] = {
        "sources": _canonical_scope(controller_relation=relation),
        "creatures": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("creature",),
        ),
        "other creatures": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("creature",),
            exclude_source_identity=True,
        ),
        "artifact sources": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("artifact",),
        ),
        "artifact creatures": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("artifact", "creature"),
        ),
        "instant and sorcery spells": _canonical_scope(
            controller_relation=relation,
            characteristics_any=("instant", "sorcery"),
        ),
        "black and/or red sources": _canonical_scope(
            controller_relation=relation,
            colors_any=("B", "R"),
        ),
        "black or red sources": _canonical_scope(
            controller_relation=relation,
            colors_any=("B", "R"),
        ),
        "blue creatures and black creatures": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("creature",),
            colors_any=("B", "U"),
        ),
        "snow creatures": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("creature", "snow"),
        ),
        "creatures with first strike": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("creature", "first strike"),
        ),
        "deserts": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("desert",),
        ),
        "red creatures": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("creature",),
            colors_any=("R",),
        ),
        "nongreen creatures": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("creature",),
            colors_none=("G",),
        ),
        "nonartifact creatures": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("creature",),
            characteristics_none=("artifact",),
        ),
        "non-human sources": _canonical_scope(
            controller_relation=relation,
            characteristics_none=("human",),
        ),
        "non-soldier creatures": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("creature",),
            characteristics_none=("soldier",),
        ),
        "creatures without trample": _canonical_scope(
            controller_relation=relation,
            characteristics_all=("creature",),
            characteristics_none=("trample",),
        ),
    }
    return exact.get(normalized)


def _split_recipient_and_source(
    text: str,
) -> tuple[str | None, str | None] | None:
    if text == "that would be dealt":
        return None, None
    for pattern in (
        r"that would be dealt to (?P<target>.+?) by (?P<source>.+)",
        r"that would be dealt by (?P<source>.+?) to (?P<target>.+)",
        r"that would be dealt to (?P<target>.+)",
        r"that would be dealt by (?P<source>.+)",
        r"that (?P<source>.+?) would deal to (?P<target>.+)",
        r"that (?P<source>.+?) would deal",
        r"(?P<source>.+?) would deal to (?P<target>.+)",
        r"(?P<source>.+?) would deal",
    ):
        match = re.fullmatch(pattern, text, re.IGNORECASE)
        if match is None:
            continue
        return match.groupdict().get("target"), match.groupdict().get("source")
    return None


def fixed_all_damage_prevention_specs(
    text: str,
    *,
    card_name: str,
) -> tuple[FixedAllDamagePreventionSpec, ...] | None:
    """Parse one complete fixed all-damage prevention instruction.

    The grammar deliberately rejects choices, targets, conditions, linked
    results, quantities, combat-role relations, attachments, and compound
    instructions.  Callers decide whether the inferred static or turn-bound
    lifetime is valid for the containing Oracle ability kind.
    """

    normalized = " ".join(text.strip().rstrip(".").split())
    ability_word = re.match(
        r"^[A-Za-z][A-Za-z ']+\s+[—-]\s+(?P<body>.+)$", normalized
    )
    if ability_word is not None:
        normalized = ability_word.group("body")
    source_controller_turn_only = False
    if normalized.casefold().startswith("during your turn, "):
        normalized = normalized[len("during your turn, ") :]
        source_controller_turn_only = True
    elif normalized.casefold().endswith(" during your turn"):
        normalized = normalized[: -len(" during your turn")]
        source_controller_turn_only = True
    if re.search(r"\bthis turn\b", normalized, re.IGNORECASE):
        normalized = re.sub(
            r"\s*\bthis turn\b", "", normalized, count=1, flags=re.IGNORECASE
        )
        duration = "until_end_of_turn"
    else:
        duration = "static"
    match = re.fullmatch(
        r"prevent all (?:(?P<kind>combat|noncombat) )?damage (?P<body>.+)",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    body = match.group("body")
    if re.fullmatch(
        r"that would be dealt to and dealt by (?P<source>.+)",
        body,
        re.IGNORECASE,
    ):
        phrase = re.fullmatch(
            r"that would be dealt to and dealt by (?P<source>.+)",
            body,
            re.IGNORECASE,
        ).group("source")
        recipients = _recipient_scopes(phrase, card_name=card_name)
        source = _source_scope(phrase, card_name=card_name)
        if recipients is None or source is None or len(recipients) != 1:
            return None
        kind = str(match.group("kind") or "any").casefold()
        return (
            FixedAllDamagePreventionSpec(
                damage_kind=kind,
                duration=duration,
                source=_ANY_SCOPE,
                recipient=recipients[0],
                source_controller_turn_only=source_controller_turn_only,
            ),
            FixedAllDamagePreventionSpec(
                damage_kind=kind,
                duration=duration,
                source=source,
                recipient=_ANY_SCOPE,
                source_controller_turn_only=source_controller_turn_only,
            ),
        )
    split = _split_recipient_and_source(body)
    if split is None:
        return None
    target_phrase, source_phrase = split
    recipients = (
        (_ANY_SCOPE,)
        if target_phrase is None
        else _recipient_scopes(target_phrase, card_name=card_name)
    )
    source = (
        _ANY_SCOPE
        if source_phrase is None
        else _source_scope(source_phrase, card_name=card_name)
    )
    if recipients is None or source is None:
        return None
    kind = str(match.group("kind") or "any").casefold()
    return tuple(
        FixedAllDamagePreventionSpec(
            damage_kind=kind,
            duration=duration,
            source=source,
            recipient=recipient,
            source_controller_turn_only=source_controller_turn_only,
        )
        for recipient in recipients
    )


def _scope_ref(
    scope: FixedDamageObjectScope,
) -> tuple[str | None, str | None]:
    if scope.source_identity:
        return "$source", None
    if scope.attached_identity:
        return "$attached", None
    if scope.selected_target is not None:
        return "$target.0", None
    if scope.exclude_source_identity:
        return None, "$source"
    return None, None


def fixed_all_damage_prevention_scope_descriptor(
    spec: FixedAllDamagePreventionSpec,
) -> dict[str, Any]:
    """Serialize one parsed scope for the typed runtime applicability owner."""

    source_ref, excluded_source_ref = _scope_ref(spec.source)
    target_ref, excluded_target_ref = _scope_ref(spec.recipient)
    return {
        "source_controller_relation": spec.source.controller_relation,
        "target_controller_relation": spec.recipient.controller_relation,
        "target_kinds": list(spec.recipient.kinds),
        "source_characteristics_all": list(
            spec.source.characteristics_all
        ),
        "source_characteristics_any": list(
            spec.source.characteristics_any
        ),
        "source_characteristics_none": list(
            spec.source.characteristics_none
        ),
        "source_colors_any": list(spec.source.colors_any),
        "source_colors_none": list(spec.source.colors_none),
        "target_characteristics_all": list(
            spec.recipient.characteristics_all
        ),
        "target_characteristics_any": list(
            spec.recipient.characteristics_any
        ),
        "target_characteristics_none": list(
            spec.recipient.characteristics_none
        ),
        "source_ref": source_ref,
        "target_ref": target_ref,
        "excluded_source_ref": excluded_source_ref,
        "excluded_target_ref": excluded_target_ref,
    }


def fixed_all_damage_prevention_target_schema(
    specs: tuple[FixedAllDamagePreventionSpec, ...],
) -> Mapping[str, Any] | None:
    """Return the one shared direct-target schema used by parsed scopes."""

    target_specs = {
        scope.selected_target
        for spec in specs
        for scope in (spec.source, spec.recipient)
        if scope.selected_target is not None
    }
    if not target_specs:
        return None
    if len(target_specs) != 1:
        raise ValueError(
            "All-damage prevention cannot use distinct target predicates"
        )
    return next(iter(target_specs)).to_target_schema()


__all__ = [
    "FixedAllDamagePreventionSpec",
    "FixedDamageObjectScope",
    "fixed_all_damage_prevention_scope_descriptor",
    "fixed_all_damage_prevention_specs",
    "fixed_all_damage_prevention_target_schema",
]
