from __future__ import annotations

"""Exact same-face entry choice and basic land type setting."""

from dataclasses import replace

from ..entry_designations import (
    BASIC_LAND_ENTRY_CAPABILITIES, BASIC_LAND_ENTRY_HANDLER_ID, CHOSEN_BASIC_LAND_HANDLER_ID,
)
from . import regex_working_set as re
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .ir_model import append_residual


_ENTRY = re.compile(
    r"As this land enters, choose a basic land type\. "
    rf"Then you may pay (?P<life>{FIXED_COUNT_PATTERN}|\d+) life\. "
    r"If you don['’]t, it enters tapped\.",
    re.IGNORECASE,
)
_HANDLERS = {BASIC_LAND_ENTRY_HANDLER_ID, CHOSEN_BASIC_LAND_HANDLER_ID}


def basic_land_entry_choice_handler(text):
    match = _ENTRY.fullmatch(text.strip())
    if match is None:
        return None
    printed = match.group("life")
    life = int(printed) if printed.isdigit() else fixed_number(printed)
    if life <= 0:
        return None
    return ("intrinsic-entry-basic-land-type-and-life-v1", {
        "handler_id": BASIC_LAND_ENTRY_HANDLER_ID, "schema_version": 1, "event": "zone.change",
        "designation": "basic_land_type", "life": life, "requires_complete_card_program": True,
    }, BASIC_LAND_ENTRY_CAPABILITIES)


def chosen_basic_land_handler(text):
    if text.strip().casefold() != "this land is the chosen type.":
        return None
    return ("static-chosen-basic-land-type-v1", {
        "handler_id": CHOSEN_BASIC_LAND_HANDLER_ID, "schema_version": 1,
        "event": "characteristics.evaluate", "requires_complete_card_program": True,
    }, BASIC_LAND_ENTRY_CAPABILITIES)


def scope_basic_land_entry_pair(face):
    components = [
        (node, descriptor["handler_id"]) for node in face.nodes for descriptor in node.handlers
        if descriptor.get("handler_id") in _HANDLERS
    ]
    if not components or (len(components) == len(_HANDLERS)
                          and {handler for _, handler in components} == _HANDLERS):
        return face
    residuals = list(face.residuals)
    rejected = {}
    for node, _ in components:
        if node.node_id not in rejected:
            residual = append_residual(
                residuals, kind="entry_designation", text=node.text, span=node.span,
                reason="Basic land type selection and setting require their exact same-face pair",
                blockers=("linked basic land entry designation",),
            )
            rejected[node.node_id] = replace(
                node, exact=False, lowerable=False, event="unresolved", handlers=(),
                residual_ids=(*node.residual_ids, residual), capability_dependencies=(),
                capability_closure=(), capability_profile=None, capability_fingerprint=None,
            )
    return replace(face, residuals=tuple(residuals), nodes=tuple(
        rejected.get(node.node_id, node) for node in face.nodes
    ))
