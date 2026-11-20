from __future__ import annotations

"""Exact hand-library return/redraw clauses without card-specific dispatch."""

from dataclasses import dataclass
from ..hand_library_redraw_model import (
    HandLibraryRedrawSpec, HandLibraryPlacement as Placement, HandLibrarySelection as Selection,
    HandLibraryScope as Scope, HAND_LIBRARY_REDRAW_MECHANIC,
)

_CLAUSES = {
    'shuffle the cards from your hand into your library, then draw that many cards.':
        (Placement.SHUFFLE, Selection.ALL, Scope.CONTROLLER, 0),
    'each player shuffles the cards from their hand into their library, then draws that many cards.':
        (Placement.SHUFFLE, Selection.ALL, Scope.ALL_PLAYERS, 0),
    'shuffle any number of cards from your hand into your library, then draw that many cards.':
        (Placement.SHUFFLE, Selection.ANY, Scope.CONTROLLER, 0),
    'put the cards in your hand on the bottom of your library in any order, then draw that many cards.':
        (Placement.BOTTOM, Selection.ALL, Scope.CONTROLLER, 0),
    'that player puts the cards in their hand on the bottom of their library in any order, then draws that many cards.':
        (Placement.BOTTOM, Selection.ALL, Scope.EVENT_PLAYER, 0),
    'put any number of cards from your hand on the bottom of your library, then draw that many cards plus one.':
        (Placement.BOTTOM, Selection.ANY, Scope.CONTROLLER, 1),
}


@dataclass(frozen=True, slots=True)
class HandLibraryRedrawTemplate:
    redraw: HandLibraryRedrawSpec

    def compiled(self):
        return ('hand-library-redraw-'+self.redraw.placement.value+'-'+self.redraw.selection.value+'-'+self.redraw.scope.value+'-v1',
                (self.redraw.effect(),), None, (HAND_LIBRARY_REDRAW_MECHANIC,))


def hand_library_redraw_template(text):
    parsed = _CLAUSES.get(' '.join(text.casefold().split()))
    return HandLibraryRedrawTemplate(HandLibraryRedrawSpec(*parsed)) if parsed is not None else None
