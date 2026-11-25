"""Immutable whole-hand selection and committed per-player discard quantities."""
from dataclasses import dataclass
from .discard_effect_origin import DiscardEffectOrigin
from collections.abc import Mapping
from .replacement.immutable import FrozenMap, thaw_value
from .event_player_modifier_model import DEFENDING_PLAYER_REFERENCE
from .graveyard_arrival_order_model import GraveyardArrivalOrders

DISCARD_RESULT_OPERATION = 'discard_hands_then_draw'
DISCARD_RESULT_CAPABILITY = 'zone.discard.whole_hand_result'
DISCARD_RESULT_MECHANIC = 'whole-hand-discard-result'
EVENT_PLAYER_DISCARD_RESULT_CAPABILITY = 'zone.discard.event_player_whole_hand_result'
EVENT_PLAYER_DISCARD_RESULT_VERSION = 3
SELECTED_LAND_DISCARD_RESULT_VERSION = 4
SELECTED_LAND_DISCARD_FIELD = '_selected_lands'
DISCARD_GRAVEYARD_ORDER_FIELD = "_graveyard_arrival_order"
DAMAGE_RECIPIENT_REFERENCE = '$context.target'
DISCARD_RESULT_EVENT_PLAYER_REFERENCES = frozenset({DEFENDING_PLAYER_REFERENCE, DAMAGE_RECIPIENT_REFERENCE})
FIXED_SUBSET_DISCARD_DRAW_CAPABILITY = 'zone.discard.fixed_subset_draw'
FIXED_SUBSET_DISCARD_DRAW_MECHANIC = 'fixed-subset-discard-draw'
VARIABLE_SUBSET_DISCARD_DRAW_CAPABILITY = 'zone.discard.variable_subset_draw'
VARIABLE_SUBSET_DISCARD_DRAW_MECHANIC = 'variable-subset-discard-draw'


def validate_fixed_subset_discard_draw(effect):
    if (not isinstance(effect, Mapping) or set(effect)!={'op','schema_version','maximum'}
            or effect['op']!='discard_draw_up_to' or type(effect['schema_version']) is not int
            or effect['schema_version']!=2 or type(effect['maximum']) is not int or effect['maximum']<1):
        raise ValueError('Fixed-subset discard/draw requires a closed schema-two positive bound')


def validate_variable_subset_discard_draw(effect):
    if (not isinstance(effect, Mapping) or set(effect)!={'op','schema_version','maximum','draw_offset'}
            or effect['op']!='discard_draw_up_to' or type(effect['schema_version']) is not int
            or effect['schema_version']!=3 or effect['maximum']!='hand_size'
            or type(effect['draw_offset']) is not int or effect['draw_offset'] not in (0,1)):
        raise ValueError('Variable-subset discard/draw requires a closed schema-three hand bound and offset')


def validate_discard_draw(effect):
    fields = {'op', 'schema_version', 'actor', 'players', 'reduction', 'offset'}
    if isinstance(effect, Mapping) and effect.get('schema_version') == SELECTED_LAND_DISCARD_RESULT_VERSION:
        if SELECTED_LAND_DISCARD_FIELD in effect:
            fields.add(SELECTED_LAND_DISCARD_FIELD)
    if not isinstance(effect, Mapping) or set(effect) != fields:
        raise ValueError('Discard-result instruction has a closed schema')
    if effect['op'] != DISCARD_RESULT_OPERATION or type(effect['schema_version']) is not int or effect['schema_version'] not in (1, 2, EVENT_PLAYER_DISCARD_RESULT_VERSION, SELECTED_LAND_DISCARD_RESULT_VERSION):
        raise ValueError('Unsupported discard-result instruction')
    if type(effect['actor']) is not str or not effect['actor']:
        raise ValueError('Discard-result actor is required')
    players = effect['players']
    if players != 'all' and (not isinstance(players, (list, tuple)) or len(players) != 1 or type(players[0]) is not str or not players[0]):
        raise ValueError('Discard results require all players or one bound player')
    if effect['schema_version'] == EVENT_PLAYER_DISCARD_RESULT_VERSION:
        if players == 'all' or effect['reduction'] != 'own' or type(effect['offset']) is not int or effect['offset'] != 0:
            raise ValueError('Event-player discard requires one bound player and their own committed count')
    elif effect['schema_version'] == SELECTED_LAND_DISCARD_RESULT_VERSION:
        if (players == 'all' or tuple(players) != (effect['actor'],) or effect['reduction'] != 'own'
                or type(effect['offset']) is not int or effect['offset'] != 0):
            raise ValueError('Selected land discards require their controller and one committed count')
        if SELECTED_LAND_DISCARD_FIELD in effect:
            members = effect[SELECTED_LAND_DISCARD_FIELD]
            if (not isinstance(members, (list, tuple)) or not members
                    or any(not isinstance(row, Mapping) or set(row) != {'object_id', 'logical_object_id', 'ref'}
                           or any(type(value) is not str or not value for value in row.values()) for row in members)):
                raise ValueError('Selected land discard members are incomplete')
            for field in ('object_id', 'logical_object_id', 'ref'):
                if len({row[field] for row in members}) != len(members):
                    raise ValueError('A selected land discard repeats a member')
    elif effect['schema_version'] == 2:
        if effect['reduction'] != 'own' or type(effect['offset']) is not int or effect['offset'] != -1:
            raise ValueError('Schema-two discard results require own count minus one')
    elif effect['reduction'] not in {'own', 'greatest'} or type(effect['offset']) is not int or effect['offset'] not in (0, 1):
        raise ValueError('Discard-result quantity is unrepresented')


def event_player_discard_result_effect(player):
    if player not in DISCARD_RESULT_EVENT_PLAYER_REFERENCES:
        raise ValueError('Event-player discard result requires a represented occurrence player')
    return {
        'op': DISCARD_RESULT_OPERATION, 'schema_version': EVENT_PLAYER_DISCARD_RESULT_VERSION,
        'actor': '$controller', 'players': [player], 'reduction': 'own', 'offset': 0,
    }


def event_discard_player_departed(effect, *, seats, active_seats):
    if effect['schema_version'] != EVENT_PLAYER_DISCARD_RESULT_VERSION:
        return False
    player, = effect['players']
    if player not in seats:
        raise ValueError('Discard-result occurrence player is unknown')
    return player not in active_seats


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
    graveyard_orders: GraveyardArrivalOrders | None = None
    require_graveyard_order: bool = False
    discard_effect_origin: DiscardEffectOrigin | None = None
    selected_lands: bool = False

    def __post_init__(self):
        if type(self.require_graveyard_order) is not bool or type(self.selected_lands) is not bool:
            raise ValueError("Discard result order requirements must be typed")
        from .whole_hand_discard_model import DiscardWholeHandsIntent
        base = DiscardWholeHandsIntent(self.actor, self.players, self.reason, self.replacement_selections,
                                       effect_origin=self.discard_effect_origin)
        hands = FrozenMap(self.hands)
        if self.selected_lands and (
            base.players != (self.actor,) or not self.require_graveyard_order or not hands.get(self.actor)
        ):
            raise ValueError('Selected land discards require one ordered, nonempty actor selection')
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
        if self.graveyard_orders is not None:
            orders = self.graveyard_orders
            if not isinstance(orders, GraveyardArrivalOrders) or orders.current_group is not None:
                raise ValueError("Discard results require completed graveyard orders")
            for group in orders.groups:
                if group.owner not in hands:
                    raise ValueError("A graveyard order belongs to an unrelated player")
                retained = {m["ref"]: (m["object_id"], m["logical_object_id"]) for m in hands[group.owner]}
                if any(retained.get(ref) != (identity.object_id, identity.logical_object_id)
                       for ref, identity in group.members):
                    raise ValueError("A graveyard order member was outside the retained hand")
        object.__setattr__(self, 'players', base.players)
        object.__setattr__(self, 'hands', hands)
        object.__setattr__(self, 'replacement_selections', base.replacement_selections)

    def identity(self):
        value = {'actor': self.actor, 'players': list(self.players), 'hands': thaw_value(self.hands), 'reason': self.reason}
        if self.graveyard_orders is not None:
            value["graveyard_orders"] = self.graveyard_orders.to_dict()
        if self.require_graveyard_order:
            value["require_graveyard_order"] = True
        if self.selected_lands:
            value["selected_lands"] = True
        if self.discard_effect_origin is not None:
            value["discard_effect_origin"] = self.discard_effect_origin.to_dict()
        return value


def decode_discard_result_identity(value):
    required = {'actor', 'players', 'hands', 'reason'}
    if not isinstance(value, Mapping) or not required <= set(value) <= required | {'graveyard_orders', 'require_graveyard_order', 'discard_effect_origin', 'selected_lands'}:
        raise ValueError('Discard-result continuation has a closed schema')
    fields = dict(value)
    if "graveyard_orders" in fields:
        fields["graveyard_orders"] = GraveyardArrivalOrders.from_dict(fields["graveyard_orders"])
    if "require_graveyard_order" in fields and fields["require_graveyard_order"] is not True:
        raise ValueError("Discard continuation order authority must be explicit")
    if "selected_lands" in fields and fields["selected_lands"] is not True:
        raise ValueError("Selected land authority must be explicit")
    if "discard_effect_origin" in fields:
        fields["discard_effect_origin"] = DiscardEffectOrigin.from_dict(fields["discard_effect_origin"])
    return DiscardWholeHandsResultIntent(**fields).identity()


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


@dataclass(frozen=True, slots=True)
class ReducedDiscardResultDraws(IntentResultPlayerDraws):
    """Explicit schema-two binding; historical result bindings stay closed."""

    def __post_init__(self):
        if (type(self.intent_index) is not int or self.intent_index < 0
                or self.reduction != 'own' or type(self.offset) is not int or self.offset != -1):
            raise ValueError('Reduced discard draws require own committed count minus one')

    def resolved_effects(self, result):
        if not isinstance(result, PlayerDiscardCounts):
            raise ValueError('Reduced discard draws require committed discard counts')
        return tuple(FrozenMap({'op':'draw','player':player,'count':max(0,count-1),'private':True})
            for player,count in result.counts)


@dataclass(frozen=True, slots=True)
class SelectedLandDiscardCounterDraws(IntentResultPlayerDraws):
    """Reuse the committed count for each independent instruction."""

    def resolved_effects(self, result):
        if not isinstance(result, PlayerDiscardCounts) or len(result.counts) != 1:
            raise ValueError('Selected land results require one committed player count')
        if self.reduction != 'own' or self.offset != 0:
            raise ValueError('Selected land results use the same unmodified count')
        _, amount = result.counts[0]
        if not amount:
            return ()
        counters = FrozenMap({'op': 'place_counters', 'card': '$source.zone_object',
                              'counter': '+1/+1', 'amount': amount, 'source': '$source'})
        return (counters, *IntentResultPlayerDraws.resolved_effects(self, result))
