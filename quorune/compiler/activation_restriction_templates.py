from __future__ import annotations

"""Closed Oracle grammar for static activation restrictions."""

import re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..semantic_runtime.activation_restrictions import (
    ACTIVATION_PERMISSION_EVENT,
    CHOSEN_NAME_NONMANA_PROHIBITION_HANDLER_ID,
    FIXED_PUBLIC_ACTIVATION_PROHIBITION_HANDLER_ID,
)


ActivationRestrictionHandlerTemplate = tuple[str, Mapping[str, Any], str]


_CHOSEN_NAME_NONMANA_PROHIBITION = re.compile(
    r"^Activated abilities of sources with the chosen name can['’]t be "
    r"activated unless they['’]re mana abilities\.?$",
    re.IGNORECASE,
)
_CHOSEN_NAME_ALL_PROHIBITION = re.compile(
    r"^Activated abilities of sources with the chosen name can['’]t be "
    r"activated\.?$",
    re.IGNORECASE,
)
_PUBLIC_QUERY_PROHIBITION = re.compile(
    r"^Activated abilities of (?P<subject>artifacts|creatures|lands|"
    r"artifacts and creatures|artifacts, creatures, and planeswalkers)"
    r"(?P<opponents> your opponents control)? can['’]t be activated"
    r"(?P<unless> unless they['’]re mana abilities)?\.?$",
    re.IGNORECASE,
)
_LOYALTY_PROHIBITION = re.compile(
    r"^Players can['’]t activate planeswalkers['’] loyalty abilities\.?$",
    re.IGNORECASE,
)
_ATTACHED_OBJECT_PROHIBITION = re.compile(
    r"^Enchanted (?P<subject>creature|permanent)['’]s activated abilities "
    r"can['’]t be activated\.?$",
    re.IGNORECASE,
)
_ATTACHED_PLAYER_PROHIBITION = re.compile(
    r"^Enchanted player can['’]t activate abilities that aren['’]t mana "
    r"abilities or loyalty abilities\.?$",
    re.IGNORECASE,
)
_ATTACHED_OBJECT_UNTAP_PROHIBITION = re.compile(
    r"^Enchanted permanent doesn['’]t untap during its controller['’]s "
    r"untap step and its activated abilities can['’]t be activated\.?$",
    re.IGNORECASE,
)


def _fixed_descriptor(
    *,
    relation: str,
    controller_relation: str = "any",
    ability_scope: str = "all",
    predicate: ObjectQuerySpec | None = None,
    prevents_untap: bool = False,
) -> dict[str, Any]:
    return {
        "handler_id": FIXED_PUBLIC_ACTIVATION_PROHIBITION_HANDLER_ID,
        "schema_version": 1,
        "event": ACTIVATION_PERMISSION_EVENT,
        "subject_relation": relation,
        "controller_relation": controller_relation,
        "ability_scope": ability_scope,
        "predicate": (predicate or ObjectQuerySpec()).to_dict(),
        "prevents_untap": prevents_untap,
    }


def static_activation_restriction_handler(
    text: str,
) -> ActivationRestrictionHandlerTemplate | None:
    """Lower only the exact chosen-name nonmana prohibition sentence."""

    normalized = text.strip()
    if _CHOSEN_NAME_NONMANA_PROHIBITION.fullmatch(normalized) is not None:
        return (
            "chosen-name-nonmana-activation-prohibition-v1",
            {
                "handler_id": CHOSEN_NAME_NONMANA_PROHIBITION_HANDLER_ID,
                "schema_version": 1,
                "event": ACTIVATION_PERMISSION_EVENT,
                "source_name_relation": "chosen_name",
                "ability_scope": "nonmana",
            },
            "activation.restriction.chosen_name_nonmana",
        )
    if _CHOSEN_NAME_ALL_PROHIBITION.fullmatch(normalized) is not None:
        return (
            "chosen-name-all-activation-prohibition-v1",
            _fixed_descriptor(relation="chosen_name"),
            "activation.restriction.fixed_public_query",
        )
    query = _PUBLIC_QUERY_PROHIBITION.fullmatch(normalized)
    if query is not None:
        terms = tuple(
            {
                "artifacts": "artifact",
                "creatures": "creature",
                "lands": "land",
                "planeswalkers": "planeswalker",
            }.get(part.strip(), part.strip())
            for part in re.split(
                r",\s*(?:and\s+)?|\s+and\s+",
                query.group("subject").casefold(),
            )
            if part.strip()
        )
        return (
            "fixed-public-query-activation-prohibition-v1",
            _fixed_descriptor(
                relation="query",
                controller_relation=(
                    "source_opponents" if query.group("opponents") else "any"
                ),
                ability_scope=("nonmana" if query.group("unless") else "all"),
                predicate=ObjectQuerySpec(
                    zones=("battlefield",),
                    types_any=terms,
                ),
            ),
            "activation.restriction.fixed_public_query",
        )
    if _LOYALTY_PROHIBITION.fullmatch(normalized) is not None:
        return (
            "fixed-loyalty-activation-prohibition-v1",
            _fixed_descriptor(
                relation="query",
                ability_scope="loyalty",
                predicate=ObjectQuerySpec(
                    zones=("battlefield",),
                    types_all=("planeswalker",),
                ),
            ),
            "activation.restriction.fixed_public_query",
        )
    if _ATTACHED_OBJECT_PROHIBITION.fullmatch(normalized) is not None:
        return (
            "attached-object-activation-prohibition-v1",
            _fixed_descriptor(relation="enchanted_object"),
            "activation.restriction.fixed_public_query",
        )
    if _ATTACHED_OBJECT_UNTAP_PROHIBITION.fullmatch(normalized) is not None:
        return (
            "attached-object-untap-activation-prohibition-v1",
            _fixed_descriptor(
                relation="enchanted_object",
                prevents_untap=True,
            ),
            (
                "activation.restriction.fixed_public_query",
                "untap.step.static_participation",
            ),
        )
    if _ATTACHED_PLAYER_PROHIBITION.fullmatch(normalized) is not None:
        return (
            "attached-player-activation-prohibition-v1",
            _fixed_descriptor(
                relation="enchanted_player",
                ability_scope="nonmana_nonloyalty",
            ),
            "activation.restriction.fixed_public_query",
        )
    return None


__all__ = [
    "ActivationRestrictionHandlerTemplate",
    "static_activation_restriction_handler",
]
