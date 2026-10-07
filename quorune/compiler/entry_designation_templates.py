from __future__ import annotations

"""Source-spanned entry designations and fixed chosen-value characteristics."""

import re
from collections import Counter
from dataclasses import replace
from typing import Mapping, Sequence

from ..entry_designations import (
    CHOSEN_CHARACTERISTICS_CAPABILITY,
    CHOSEN_CHARACTERISTICS_HANDLER_ID,
    ENTRY_DESIGNATION_CAPABILITY,
    ENTRY_DESIGNATION_HANDLER_ID,
)
from ..rules.source_references import SourceReferenceSpec
from .continuous_templates import (
    fixed_query_characteristic_grant_handler,
    fixed_query_keyword_grant_handler,
    fixed_power_toughness_anthem_handler,
)
from .ir_model import OracleFaceIR, append_residual


def scope_entry_designation_faces(
    faces: Sequence[OracleFaceIR],
) -> tuple[OracleFaceIR, ...]:
    """Reject duplicate same-kind entry declarations requiring linked choices."""
    result = []
    for face in faces:
        declarations = Counter(
            descriptor["designation"]
            for node in face.nodes
            for descriptor in node.handlers
            if descriptor.get("handler_id") == ENTRY_DESIGNATION_HANDLER_ID
        )
        duplicates = {kind for kind, count in declarations.items() if count > 1}
        if not duplicates:
            result.append(face)
            continue
        residuals = list(face.residuals)
        nodes = []
        for node in face.nodes:
            if any(
                descriptor.get("handler_id") == ENTRY_DESIGNATION_HANDLER_ID
                and descriptor.get("designation") in duplicates
                for descriptor in node.handlers
            ):
                residual_id = append_residual(
                    residuals,
                    kind="entry_designation",
                    text=node.text,
                    span=node.span,
                    reason=(
                        "Multiple same-kind intrinsic choices require independent "
                        "linked designation identities"
                    ),
                    blockers=("linked entry designation ownership",),
                )
                node = replace(
                    node,
                    exact=False,
                    lowerable=False,
                    event="unresolved",
                    handlers=(),
                    residual_ids=(*node.residual_ids, residual_id),
                )
            nodes.append(node)
        result.append(replace(face, nodes=tuple(nodes), residuals=tuple(residuals)))
    return tuple(result)


def entry_designation_handler(text: str, *, source_name: str):
    source = (
        rf"(?:{SourceReferenceSpec(source_name).regex_pattern}|"
        r"this (?:artifact|Aura|card|creature|enchantment|Equipment|land|permanent))"
    )
    match = re.fullmatch(
        rf"As {source} enters, choose a (?P<kind>color|creature type)\.",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return (
        "intrinsic-entry-designation-v1",
        {
            "handler_id": ENTRY_DESIGNATION_HANDLER_ID,
            "schema_version": 1,
            "event": "zone.change",
            "designation": match["kind"].casefold().replace(" ", "_"),
        },
        (ENTRY_DESIGNATION_CAPABILITY,),
    )


def chosen_characteristics_handler(text: str):
    match = re.fullmatch(
        r"(?P<other>Other )?(?P<all>All )?creatures"
        r"(?P<controlled> you control)? of the chosen (?P<kind>color|type)"
        r"(?P<opponents> your opponents control)? (?P<body>get .+|have .+)",
        text,
        re.IGNORECASE,
    )
    if match is None or (match["controlled"] and match["opponents"]):
        return None
    if match["opponents"]:
        subject = "Creatures your opponents control"
    else:
        subject = (
            ("Other " if match["other"] else "")
            + "Creatures"
            + (" you control" if match["controlled"] else "")
        )
    body = subject + " " + match["body"]
    compiled = None
    compilers = (
        fixed_query_characteristic_grant_handler,
        fixed_power_toughness_anthem_handler,
        fixed_query_keyword_grant_handler,
    )
    for compiler in compilers:
        compiled = compiler(body)
        if compiled is not None:
            break
    if compiled is None:
        return None
    _, descriptor, capabilities = compiled
    if not isinstance(descriptor, Mapping):
        return None
    body_capabilities = (
        (capabilities,) if isinstance(capabilities, str) else capabilities
    )
    return (
        "fixed-chosen-designation-characteristics-v1",
        {
            "handler_id": CHOSEN_CHARACTERISTICS_HANDLER_ID,
            "schema_version": 1,
            "event": "characteristics.evaluate",
            "designation": (
                "color" if match["kind"].casefold() == "color" else "creature_type"
            ),
            "body": dict(descriptor),
        },
        tuple(dict.fromkeys((CHOSEN_CHARACTERISTICS_CAPABILITY, *body_capabilities))),
    )
