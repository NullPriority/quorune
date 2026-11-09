from __future__ import annotations

"""Closed printed bounds for a controller's permanent return."""

from dataclasses import replace
import re

from ..rules.controlled_return import CONTROLLED_RETURN_MECHANIC, ControlledReturn
from .fixed_entry_return_requirements import _controlled_quality_query

_CONTROLLED_RETURN = re.compile(
    r"(?P<optional>You may )?return (?P<count>a|an|another|up to one|up to two|up to three) "
    r"(?P<quality>.+?) you control to (?:its|their) owner['’]s hand\.?",
    re.IGNORECASE,
)
_UP_TO = {"up to one": 1, "up to two": 2, "up to three": 3}


def controlled_return_scope(text: str, *, source_is_permanent: bool | None) -> ControlledReturn | None:
    if not text.lstrip().casefold().startswith(("return ", "you may return ")):
        return None
    match = _CONTROLLED_RETURN.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    count = match["count"].casefold()
    another = count == "another"
    if another and source_is_permanent is not True:
        return None
    query = _controlled_quality_query(match["quality"], excludes_source=another)
    if query is None:
        return None
    query = replace(query, controller="$controller", exclude_ref="$source.zone_object" if another else None)
    maximum = _UP_TO.get(count, 1)
    minimum = 0 if match["optional"] or count in _UP_TO else 1
    return ControlledReturn(query, minimum, maximum)


def controlled_return_template(text: str, *, source_is_permanent: bool | None):
    scope = controlled_return_scope(text, source_is_permanent=source_is_permanent)
    if scope is None:
        return None
    return "bounded-controlled-return-v1", (scope.effect(),), None, (CONTROLLED_RETURN_MECHANIC,)
