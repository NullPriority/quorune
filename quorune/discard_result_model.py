"""Immutable whole-hand selection and committed per-player discard quantities."""
from dataclasses import dataclass
from collections.abc import Mapping
from .replacement.immutable import FrozenMap, thaw_value

DISCARD_RESULT_OPERATION = 'discard_hands_then_draw'
DISCARD_RESULT_CAPABILITY = 'zone.discard.whole_hand_result'
DISCARD_RESULT_MECHANIC = 'whole-hand-discard-result'


def validate_discard_draw(effect):
    if not isinstance(effect, Mapping) or set(effect) != {'op', 'schema_version', 'actor', 'players', 'reduction', 'offset'}:
        raise ValueError('Discard-result instruction has a closed schema')
    if effect['op'] != DISCARD_RESULT_OPERATION or type(effect['schema_version']) is not int or effect['schema_version'] != 1:
        raise ValueError('Unsupported discard-result instruction')
    if type(effect['actor']) is not str or not effect['actor']:
        raise ValueError('Discard-result actor is required')
    players = effect['players']
    if players != 'all' and (not isinstance(players, (list, tuple)) or len(players) != 1 or type(players[0]) is not str or not players[0]):
        raise ValueError('Discard results require all players or one bound player')
    if effect['reduction'] not in {'own', 'greatest'} or type(effect['offset']) is not int or effect['offset'] not in (0, 1):
        raise ValueError('Discard-result quantity is unrepresented')


@dataclass(frozen=True, slots=True)
class PlayerDiscardCounts:
    counts: tuple[tuple[str, int], ...]

    def __post_init__(self):
        rows = tuple(tuple(row) for row in self.counts)
        if any(len(row) != 2 or type(row[0]) is not str or not row[0] or type(row[1]) is not int or row[1] < 0 for row in rows):
            raise ValueError('Committed discard counts are malformed')
        if len({row[0] for row in rows}) != len(rows):
            raise ValueError('Committed discard players are duplicated')
        object.__setattr__(self, 'counts', rows)


@dataclass(frozen=True, slots=True)
class DiscardWholeHandsResultIntent:
    actor: str
    players: tuple[str, ...]
    hands: FrozenMap
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self):
        from .whole_hand_discard_model import DiscardWholeHandsIntent
        base = DiscardWholeHandsIntent(self.actor, self.players, self.reason, self.replacement_selections)
        hands = FrozenMap(self.hands)
        if set(hands) != set(base.players):
            raise ValueError('Retained discard hands differ from affected players')
        identities = []
        for members in hands.values():
            if not isinstance(members, (list, tuple)):
                raise ValueError('Retained discard hand must be a collection')
            for row in members:
                if not isinstance(row, Mapping) or set(row) != {'object_id', 'logical_object_id', 'ref'} or any(type(v) is not str or not v for v in row.values()):
                    raise ValueError('Retained discard member is malformed')
                identities.append((row['object_id'], row['logical_object_id'], row['ref']))
        if any(len({row[index] for row in identities}) != len(identities) for index in range(3)):
            raise ValueError('Retained discard members are duplicated')
        object.__setattr__(self, 'players', base.players)
        object.__setattr__(self, 'hands', hands)
        object.__setattr__(self, 'replacement_selections', base.replacement_selections)

    def identity(self):
        return {'actor': self.actor, 'players': list(self.players), 'hands': thaw_value(self.hands), 'reason': self.reason}


def decode_discard_result_identity(value):
    if not isinstance(value, Mapping) or set(value) != {'actor', 'players', 'hands', 'reason'}:
        raise ValueError('Discard-result continuation has a closed schema')
    return DiscardWholeHandsResultIntent(**dict(value)).identity()


@dataclass(frozen=True, slots=True)
class IntentResultPlayerDraws:
    intent_index: int
    reduction: str
    offset: int = 0

    def __post_init__(self):
        if type(self.intent_index) is not int or self.intent_index < 0 or self.reduction not in {'own', 'greatest'} or type(self.offset) is not int or self.offset not in (0, 1):
            raise ValueError('Committed discard draw binding is malformed')

    def resolved_effects(self, result):
        if not isinstance(result, PlayerDiscardCounts):
            raise ValueError('Discard draws require a committed typed result')
        greatest = max((count for _, count in result.counts), default=0)
        return tuple(FrozenMap({'op': 'draw', 'player': player, 'count': (greatest if self.reduction == 'greatest' else count) + self.offset, 'private': True})
            for player, count in result.counts)
