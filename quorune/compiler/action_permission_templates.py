from __future__ import annotations

"""Closed Oracle grammar for controller-wide static action permissions."""

import re
from typing import Any, Mapping

from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec
from ..semantic_runtime.action_permissions import (
    ACTION_PERMISSION_EVENT,
    ACTIVATE_CONTROLLED_CREATURE_AS_HASTE_HANDLER_ID,
    ADDITIONAL_LAND_PLAY_HANDLER_ID,
    LIBRARY_TOP_ACTION_HANDLER_ID,
    LIBRARY_TOP_VISIBILITY_HANDLER_ID,
    LAND_PLAY_FROM_OWN_GRAVEYARD_HANDLER_ID,
    ActionPermissionKind,
    ActionPermissionScope,
    LibraryTopVisibility,
)


ActionPermissionHandlerTemplate = tuple[str, Mapping[str, Any], str]


_PLAY_LANDS_FROM_GRAVEYARD = re.compile(
    r"^You may play lands from your graveyard\.?$",
    re.IGNORECASE,
)
_ACTIVATE_CREATURES_AS_HASTE = re.compile(
    r"^You may activate abilities of creatures you control as though those "
    r"creatures had haste\.?$",
    re.IGNORECASE,
)
_LOOK_AT_LIBRARY_TOP = re.compile(
    r"^You may look at the top card of your library any time\.?$",
    re.IGNORECASE,
)
_REVEAL_OWN_LIBRARY_TOP = re.compile(
    r"^Play with the top card of your library revealed\.?$",
    re.IGNORECASE,
)
_REVEAL_ALL_LIBRARY_TOPS = re.compile(
    r"^Players play with the top card of their libraries revealed\.?$",
    re.IGNORECASE,
)
_ADDITIONAL_LAND_PLAYS = re.compile(
    r"^You may play (?P<count>an|two) additional lands? on each of your turns\.?$",
    re.IGNORECASE,
)
_TOP_LIBRARY_CAST = re.compile(
    r"^You may cast (?P<subject>.+?) spells? from the top of your library\.?$",
    re.IGNORECASE,
)
_TOP_LIBRARY_CAST_KEYWORDS = re.compile(
    r"^You may cast spells with (?P<keywords>flash|flying|flash or flying) "
    r"from the top of your library\.?$",
    re.IGNORECASE,
)
_TOP_LIBRARY_PLAY_ALL = re.compile(
    r"^You may play lands and cast spells from the top of your library\.?$",
    re.IGNORECASE,
)
_TOP_LIBRARY_PLAY_LANDS_AND_CAST = re.compile(
    r"^You may play lands and cast (?P<subject>.+?) spells? from the top "
    r"of your library\.?$",
    re.IGNORECASE,
)
_TOP_LIBRARY_PLAY_LANDS = re.compile(
    r"^You may play lands from the top of your library\.?$",
    re.IGNORECASE,
)
_TOP_LIBRARY_PLAY_QUALIFIED = re.compile(
    r"^You may play (?P<quality>snow|historic) lands and cast "
    r"(?P=quality) spells from the top of your library\.?$",
    re.IGNORECASE,
)
_GARRUK_REMINDER = (
    " (Do this only any time you could cast that creature spell. "
    "You still pay the spell's costs.)"
)
_HISTORIC_REMINDER = " (Artifacts, legendaries, and Sagas are historic.)"


def _descriptor(
    *,
    handler_id: str,
    permission: ActionPermissionKind,
) -> dict[str, Any]:
    return {
        "handler_id": handler_id,
        "schema_version": 1,
        "event": ACTION_PERMISSION_EVENT,
        "permission": permission.value,
    }


def _query(**kwargs: Any) -> dict[str, Any]:
    return ObjectQuerySpec(**kwargs).to_dict()


def _spell_query(**kwargs: Any) -> dict[str, Any]:
    excluded_types = tuple(kwargs.pop("excluded_types", ()))
    return _query(
        **kwargs,
        excluded_types=tuple(dict.fromkeys((*excluded_types, "land"))),
    )


def _historic_queries(*, land: bool) -> list[dict[str, Any]]:
    query = _query if land else _spell_query
    required = ("land",) if land else ()
    return [
        query(types_all=(*required, "artifact")),
        query(types_all=required, supertypes_all=("legendary",)),
        query(types_all=required, subtypes_all=("saga",)),
    ]


def _spell_queries(subject: str) -> list[dict[str, Any]] | None:
    normalized = " ".join(subject.casefold().split())
    if normalized in {"spell", "spells"}:
        return [_spell_query()]
    if normalized.startswith("spells with "):
        keywords = tuple(
            part.strip()
            for part in re.split(
                r"\s+or\s+", normalized.removeprefix("spells with ")
            )
            if part.strip()
        )
        if not keywords or any(
            value not in {"flash", "flying"} for value in keywords
        ):
            return None
        return [
            _spell_query(keywords_all=(keyword,)) for keyword in keywords
        ]
    normalized = " ".join(
        re.sub(r"\bspells?\b", " ", normalized).split()
    )
    if normalized == "instant and sorcery":
        return [_spell_query(types_any=("instant", "sorcery"))]
    if normalized == "artifact and colorless":
        return [
            _spell_query(types_all=("artifact",)),
            _spell_query(colorless=True),
        ]
    if normalized == "historic":
        return _historic_queries(land=False)
    if normalized == "snow":
        return [_spell_query(supertypes_all=("snow",))]
    terms = tuple(
        part.strip()
        for part in re.sub(r",?\s+(?:and|or)\s+", ",", normalized).split(",")
        if part.strip()
    )
    if not terms or len(terms) > 4:
        return None
    queries: list[dict[str, Any]] = []
    for term in terms:
        if term == "noncreature":
            queries.append(_spell_query(excluded_types=("creature",)))
        elif term in {
            "artifact",
            "battle",
            "creature",
            "enchantment",
            "instant",
            "kindred",
            "planeswalker",
            "sorcery",
        }:
            queries.append(_spell_query(types_all=(term,)))
        elif term in {"aura", "equipment"}:
            queries.append(_spell_query(subtypes_all=(term,)))
        else:
            subtype = canonical_creature_subtype(term)
            if subtype is None:
                return None
            queries.append(_spell_query(subtypes_all=(subtype,)))
    return queries


def public_spell_queries(subject: str) -> tuple[dict[str, Any], ...] | None:
    """Return one closed public nonland spell-query union."""

    queries = _spell_queries(subject)
    return None if queries is None else tuple(queries)


def _library_visibility_descriptor(
    visibility: LibraryTopVisibility,
    scope: ActionPermissionScope,
) -> dict[str, Any]:
    return {
        "handler_id": LIBRARY_TOP_VISIBILITY_HANDLER_ID,
        "schema_version": 1,
        "event": ACTION_PERMISSION_EVENT,
        "visibility": visibility.value,
        "scope": scope.value,
    }


def _library_action_descriptor(
    *,
    land_queries: tuple[dict[str, Any], ...] = (),
    spell_queries: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    return {
        "handler_id": LIBRARY_TOP_ACTION_HANDLER_ID,
        "schema_version": 1,
        "event": ACTION_PERMISSION_EVENT,
        "land_queries": list(land_queries),
        "spell_queries": list(spell_queries),
    }


def static_action_permission_handler(
    text: str,
) -> ActionPermissionHandlerTemplate | None:
    """Lower one closed controller/public action-permission sentence."""

    normalized = text.strip()
    if _PLAY_LANDS_FROM_GRAVEYARD.fullmatch(normalized) is not None:
        return (
            "land-play-from-own-graveyard-permission-v1",
            _descriptor(
                handler_id=LAND_PLAY_FROM_OWN_GRAVEYARD_HANDLER_ID,
                permission=ActionPermissionKind.LAND_PLAY_FROM_OWN_GRAVEYARD,
            ),
            "land.play.from_own_graveyard",
        )
    if _ACTIVATE_CREATURES_AS_HASTE.fullmatch(normalized) is not None:
        return (
            "activate-controlled-creature-as-haste-permission-v1",
            _descriptor(
                handler_id=ACTIVATE_CONTROLLED_CREATURE_AS_HASTE_HANDLER_ID,
                permission=(
                    ActionPermissionKind.ACTIVATE_CONTROLLED_CREATURE_AS_HASTE
                ),
            ),
            "activation.permission.controlled_creature_as_haste",
        )
    if _LOOK_AT_LIBRARY_TOP.fullmatch(normalized) is not None:
        return (
            "look-at-own-library-top-v1",
            _library_visibility_descriptor(
                LibraryTopVisibility.CONTROLLER,
                ActionPermissionScope.CONTROLLER,
            ),
            "library.visibility.top.static",
        )
    if _REVEAL_OWN_LIBRARY_TOP.fullmatch(normalized) is not None:
        return (
            "reveal-own-library-top-v1",
            _library_visibility_descriptor(
                LibraryTopVisibility.PUBLIC,
                ActionPermissionScope.CONTROLLER,
            ),
            "library.visibility.top.static",
        )
    if _REVEAL_ALL_LIBRARY_TOPS.fullmatch(normalized) is not None:
        return (
            "reveal-all-library-tops-v1",
            _library_visibility_descriptor(
                LibraryTopVisibility.PUBLIC,
                ActionPermissionScope.ALL_PLAYERS,
            ),
            "library.visibility.top.static",
        )
    land_match = _ADDITIONAL_LAND_PLAYS.fullmatch(normalized)
    if land_match is not None:
        return (
            "additional-land-plays-v1",
            {
                "handler_id": ADDITIONAL_LAND_PLAY_HANDLER_ID,
                "schema_version": 1,
                "event": ACTION_PERMISSION_EVENT,
                "amount": 1 if land_match.group("count").casefold() == "an" else 2,
            },
            "land.play.additional.static",
        )
    if normalized.endswith(_GARRUK_REMINDER):
        normalized = normalized[: -len(_GARRUK_REMINDER)]
    if normalized.endswith(_HISTORIC_REMINDER):
        normalized = normalized[: -len(_HISTORIC_REMINDER)]
    if _TOP_LIBRARY_PLAY_ALL.fullmatch(normalized) is not None:
        return (
            "play-and-cast-library-top-v1",
            _library_action_descriptor(
                land_queries=(_query(types_all=("land",)),),
                spell_queries=(_query(excluded_types=("land",)),),
            ),
            "library.action.top.static",
        )
    lands_and_cast = _TOP_LIBRARY_PLAY_LANDS_AND_CAST.fullmatch(normalized)
    if lands_and_cast is not None:
        queries = _spell_queries(lands_and_cast.group("subject"))
        if queries is not None:
            return (
                "play-land-and-cast-library-top-public-query-v1",
                _library_action_descriptor(
                    land_queries=(_query(types_all=("land",)),),
                    spell_queries=tuple(queries),
                ),
                "library.action.top.static",
            )
    if _TOP_LIBRARY_PLAY_LANDS.fullmatch(normalized) is not None:
        return (
            "play-land-library-top-v1",
            _library_action_descriptor(
                land_queries=(_query(types_all=("land",)),),
            ),
            "library.action.top.static",
        )
    qualified = _TOP_LIBRARY_PLAY_QUALIFIED.fullmatch(normalized)
    if qualified is not None:
        quality = qualified.group("quality").casefold()
        land_queries = (
            _historic_queries(land=True)
            if quality == "historic"
            else (_query(types_all=("land",), supertypes_all=("snow",)),)
        )
        spell_queries = (
            _historic_queries(land=False)
            if quality == "historic"
            else (_spell_query(supertypes_all=("snow",)),)
        )
        return (
            f"play-and-cast-{quality}-library-top-v1",
            _library_action_descriptor(
                land_queries=tuple(land_queries),
                spell_queries=tuple(spell_queries),
            ),
            "library.action.top.static",
        )
    keyword_cast = _TOP_LIBRARY_CAST_KEYWORDS.fullmatch(normalized)
    if keyword_cast is not None:
        keywords = tuple(
            value.strip()
            for value in keyword_cast.group("keywords").casefold().split(" or ")
        )
        return (
            "cast-library-top-keyword-query-v1",
            _library_action_descriptor(
                spell_queries=tuple(
                    _spell_query(keywords_all=(keyword,))
                    for keyword in keywords
                )
            ),
            "library.action.top.static",
        )
    cast_match = _TOP_LIBRARY_CAST.fullmatch(normalized)
    if cast_match is not None:
        queries = _spell_queries(cast_match.group("subject"))
        if queries is not None:
            return (
                "cast-library-top-public-query-v1",
                _library_action_descriptor(spell_queries=tuple(queries)),
                "library.action.top.static",
            )
    return None


__all__ = [
    "ActionPermissionHandlerTemplate",
    "public_spell_queries",
    "static_action_permission_handler",
]
