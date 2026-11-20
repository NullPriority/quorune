from __future__ import annotations

"""Versioned private hand returns with committed library-arrival draw counts."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .discard_result_model import (
    DiscardWholeHandsResultIntent, IntentResultPlayerDraws, PlayerDiscardCounts,
)
from .library_order_model import LibraryArrivalOrder, DepartedLibraryArrivalOrder
from .replacement.immutable import FrozenMap, thaw_value

HAND_LIBRARY_REDRAW_OPERATION = 'hand_library_redraw'
HAND_LIBRARY_REDRAW_CAPABILITY = 'zone.hand.library_redraw'
HAND_LIBRARY_REDRAW_MECHANIC = 'hand-library-redraw'
HAND_LIBRARY_REDRAW_DEPENDENCIES = (
    HAND_LIBRARY_REDRAW_CAPABILITY, 'zone.draw.library_to_hand',
    'zone.change.destination_replacement',
)


class HandLibraryPlacement(StrEnum):
    SHUFFLE = 'shuffle'
    BOTTOM = 'bottom'


class HandLibrarySelection(StrEnum):
    ALL = 'all'
    ANY = 'any'


class HandLibraryScope(StrEnum):
    CONTROLLER = 'controller'
    ALL_PLAYERS = 'all_players'
    EVENT_PLAYER = 'event_player'


_REPRESENTED = frozenset({
    ('shuffle', 'all', 'controller', 0),
    ('shuffle', 'all', 'all_players', 0),
    ('shuffle', 'any', 'controller', 0),
    ('bottom', 'all', 'controller', 0),
    ('bottom', 'all', 'event_player', 0),
    ('bottom', 'any', 'controller', 1),
})


@dataclass(frozen=True, slots=True)
class HandLibraryRedrawSpec:
    placement: HandLibraryPlacement
    selection: HandLibrarySelection
    scope: HandLibraryScope
    draw_offset: int = 0
    schema_version: int = 1

    def __post_init__(self):
        if (not isinstance(self.placement, HandLibraryPlacement)
                or not isinstance(self.selection, HandLibrarySelection)
                or not isinstance(self.scope, HandLibraryScope)
                or type(self.schema_version) is not int or self.schema_version != 1
                or type(self.draw_offset) is not int
                or (self.placement.value, self.selection.value, self.scope.value, self.draw_offset)
                not in _REPRESENTED):
            raise ValueError('Hand redraw requires a represented version1 placement, selection and scope')

    def to_dict(self):
        return {'schema_version': 1, 'placement': self.placement.value,
                'selection': self.selection.value, 'scope': self.scope.value,
                'draw_offset': self.draw_offset}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
                'schema_version', 'placement', 'selection', 'scope', 'draw_offset'}:
            raise ValueError('Hand redraw fields are incomplete or unknown')
        return cls(HandLibraryPlacement(value['placement']), HandLibrarySelection(value['selection']),
                   HandLibraryScope(value['scope']), value['draw_offset'], value['schema_version'])

    def effect(self):
        return {'op': HAND_LIBRARY_REDRAW_OPERATION, 'actor': '$controller',
                'player': '$context.player' if self.scope is HandLibraryScope.EVENT_PLAYER else '$controller',
                'redraw': self.to_dict()}


def decode_hand_library_redraw(effect, *, actor=None, player=None):
    if (not isinstance(effect, Mapping) or set(effect) != {'op', 'actor', 'player', 'redraw'}
            or effect['op'] != HAND_LIBRARY_REDRAW_OPERATION):
        raise ValueError('Hand redraw requires its complete owned instruction')
    spec = HandLibraryRedrawSpec.from_dict(effect['redraw'])
    if actor is None:
        expected_player = '$context.player' if spec.scope is HandLibraryScope.EVENT_PLAYER else '$controller'
        if effect['actor'] != '$controller' or effect['player'] != expected_player:
            raise ValueError('Compiled hand redraw changed its controller or recipient scope')
    elif (type(actor) is not str or not actor or type(player) is not str or not player
            or effect['actor'] != actor or effect['player'] != player
            or spec.scope is not HandLibraryScope.EVENT_PLAYER and player != actor):
        raise ValueError('Resolved hand redraw changed its controller or recipient')
    return spec


@dataclass(frozen=True, slots=True)
class HandLibraryRedrawIntent:
    actor: str
    players: tuple[str, ...]
    redraw: HandLibraryRedrawSpec
    hands: FrozenMap
    selected: FrozenMap
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self):
        if not isinstance(self.redraw, HandLibraryRedrawSpec):
            raise ValueError('Hand return requires its typed redraw instruction')
        # Reuse only the existing immutable hand-identity validation, not discard behavior.
        base = DiscardWholeHandsResultIntent(self.actor, self.players, self.hands,
                                             self.reason, self.replacement_selections)
        if (not base.players
                or self.redraw.scope is HandLibraryScope.CONTROLLER and base.players != (self.actor,)
                or self.redraw.scope is HandLibraryScope.EVENT_PLAYER and len(base.players) != 1):
            raise ValueError('Hand return recipients contradict the represented scope')
        selected = FrozenMap(self.selected)
        if set(selected) != set(base.players):
            raise ValueError('Selected hand return recipients changed')
        for seat in base.players:
            refs = selected[seat]
            legal = {member['ref'] for member in base.hands[seat]}
            if (not isinstance(refs, (list, tuple)) or any(type(ref) is not str or not ref for ref in refs)
                    or len(refs) != len(set(refs)) or not set(refs) <= legal
                    or self.redraw.selection is HandLibrarySelection.ALL and set(refs) != legal):
                raise ValueError('Selected hand returns are outside their sealed hand identities')
        object.__setattr__(self, 'players', base.players)
        object.__setattr__(self, 'hands', base.hands)
        object.__setattr__(self, 'selected', selected)
        object.__setattr__(self, 'replacement_selections', base.replacement_selections)

    def identity(self):
        return {'schema_version': 1, 'actor': self.actor, 'players': list(self.players),
                'redraw': self.redraw.to_dict(), 'hands': thaw_value(self.hands),
                'selected': thaw_value(self.selected), 'reason': self.reason}

    @classmethod
    def from_identity(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {
                'schema_version', 'actor', 'players', 'redraw', 'hands', 'selected', 'reason'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or not isinstance(value['players'], (list, tuple))
                or not isinstance(value['hands'], Mapping) or not isinstance(value['selected'], Mapping)):
            raise ValueError('Hand redraw continuation has unknown or missing fields')
        return cls(value['actor'], tuple(value['players']), HandLibraryRedrawSpec.from_dict(value['redraw']),
                   FrozenMap(value['hands']), FrozenMap(value['selected']), value['reason'])


@dataclass(frozen=True, slots=True)
class PlayerLibraryReturnCounts:
    counts: tuple[tuple[str, int], ...]

    def __post_init__(self):
        object.__setattr__(self, 'counts', PlayerDiscardCounts(self.counts).counts)


def committed_library_return_counts(intent, libraries):
    """Count selected source identities in actual owner libraries before draws."""
    if not isinstance(intent, HandLibraryRedrawIntent) or not isinstance(libraries, Mapping) or set(libraries) != set(intent.players):
        raise ValueError('Committed hand counts require their exact recipient libraries')
    counts = []
    for seat in intent.players:
        actual = libraries[seat]
        if (not isinstance(actual, (list, tuple)) or any(type(oid) is not str or not oid for oid in actual)
                or len(actual) != len(set(actual))):
            raise ValueError('Committed library membership is malformed')
        selected = set(intent.selected[seat])
        intended = {member['object_id'] for member in intent.hands[seat] if member['ref'] in selected}
        counts.append((seat, len(intended.intersection(actual))))
    return PlayerLibraryReturnCounts(tuple(counts))


@dataclass(frozen=True, slots=True)
class HandLibraryResultDraws(IntentResultPlayerDraws):
    reduction: str = 'own'

    def __post_init__(self):
        IntentResultPlayerDraws.__post_init__(self)
        if self.reduction != 'own':
            raise ValueError('Hand returns draw only their own committed arrivals')

    def resolved_effects(self, result):
        if not isinstance(result, PlayerLibraryReturnCounts):
            raise ValueError('Hand redraw requires committed library-arrival counts')
        return tuple(FrozenMap({'op': 'draw', 'player': seat, 'count': count + self.offset, 'private': True})
                     for seat, count in result.counts)


@dataclass(frozen=True, slots=True)
class HandLibraryArrivalOrder(DepartedLibraryArrivalOrder):
    """New wire tag for the owner's bottom-first private hand-arrival order."""

    def __post_init__(self):
        LibraryArrivalOrder.__post_init__(self)
        if self.position != 'bottom':
            raise ValueError('Hand redraw ordering supports only the library bottom')

    @property
    def origin_zones(self):
        return ('hand',)

    def to_dict(self):
        return {**LibraryArrivalOrder.to_dict(self), 'scope': 'hand_redraw_v1'}
