"""Recognize one exact linked batch exile and separate return pair."""

from __future__ import annotations

from ..linked_exile_return_model import LinkedBatchDepartureScope
from . import regex_working_set as re


_ENTRY_SCOPES = {
    "exile all lands.": LinkedBatchDepartureScope.ALL_LANDS,
    "exile all other permanents you control.": LinkedBatchDepartureScope.OTHER_CONTROLLED_PERMANENTS,
    "exile two target lands.": LinkedBatchDepartureScope.TWO_TARGET_LANDS,
}


def linked_batch_departure_scope(entry_body: str, leave_body: str) -> LinkedBatchDepartureScope | None:
    scope = _ENTRY_SCOPES.get(" ".join(entry_body.split()).casefold())
    if scope is None:
        return None
    match = re.fullmatch(
        r"return the exiled cards to the battlefield(?P<tapped> tapped)? under their owners['’] control\.",
        " ".join(leave_body.split()), re.IGNORECASE,
    )
    return scope if match is not None and bool(match["tapped"]) == scope.tapped_return else None
