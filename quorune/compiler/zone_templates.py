from __future__ import annotations

from . import regex_working_set as re
from typing import Any, Mapping


_OPPONENT_CARD_TO_EXILE_WITH_COUNTER = re.compile(
    r"^If a card would be put into an opponent['’]s graveyard from "
    r"anywhere, instead exile it with a "
    r"(?P<counter>[A-Za-z][A-Za-z0-9-]*(?: [A-Za-z][A-Za-z0-9-]*)*) "
    r"counter on it\.?$",
    re.IGNORECASE,
)
_COUNTERS_FIELD = "counters"
_EXILE_ZONE = "exile"


def static_zone_destination_replacement_handler(
    text: str,
) -> tuple[str, Mapping[str, Any], str | tuple[str,...]] | None:
    """Lower one closed opponent-card destination replacement family."""

    from .graveyard_exile_templates import graveyard_exile_template
    qualified=graveyard_exile_template(text)
    if qualified is not None:return qualified
    match = _OPPONENT_CARD_TO_EXILE_WITH_COUNTER.fullmatch(text.strip())
    if match is None:
        return None
    counter_name = " ".join(match.group("counter").casefold().split())
    return (
        "zone-opponent-card-graveyard-to-exile-with-counter-v1",
        {
            "handler_id": "replacement.zone.destination.v1",
            "schema_version": 1,
            "event": "zone.change",
            "condition": {
                "destination": "graveyard",
                "object_kind": "card",
                "owner_relation": "opponent",
            },
            "destination": _EXILE_ZONE,
            _COUNTERS_FIELD: {counter_name: 1},
        },
        "zone.change.destination_replacement",
    )


__all__ = ["static_zone_destination_replacement_handler"]
