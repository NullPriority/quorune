from __future__ import annotations

"""Closed Miracle declarations and distinct draw/hand identities.

These immutable values grant no casting permission. Coordination must verify
the complete source program and current game state; a resolving trigger's
controller is independent from the original drawer stored here.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import re

from ..model import CardInstance
from ..replacement.immutable import FrozenMap, thaw_value
from ..util import mana_cost_to_vector


MIRACLE_CAPABILITY_ID = "casting.miracle.fixed_mana"
MIRACLE_REVEAL_HANDLER_ID = "action.draw.miracle-reveal.v1"
MIRACLE_REVEAL_EVENT = "draw.miracle_reveal"
MIRACLE_TRIGGER_HANDLER_ID = "casting.miracle.trigger.v1"
MIRACLE_TRIGGER_EVENT = "card.draw.miracle_revealed.self"
MIRACLE_CHOICE_OPERATION = "miracle_cast_choice"


class MiracleModelError(ValueError):
    """A Miracle declaration or event identity is malformed or stale."""


def _fields(value, fields, role):
    if not isinstance(value, Mapping) or set(value) != fields:
        raise MiracleModelError(f'{role} has a closed schema')


def _strings(value, fields):
    if any(type(getattr(value, field)) is not str or not getattr(value, field)
           or getattr(value, field).strip() != getattr(value, field) for field in fields):
        raise MiracleModelError('Miracle identities require nonempty canonical strings')


def _nonnegative(value):
    return type(value) is int and value >= 0


def _ordinary_mana(cost_text):
    if type(cost_text) is not str or re.fullmatch(r'(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+', cost_text) is None:
        raise MiracleModelError('Miracle requires represented fixed ordinary mana')
    mana, complex_symbols = mana_cost_to_vector(cost_text)
    if complex_symbols:
        raise MiracleModelError('Miracle mana contains an unrepresented symbol')
    return mana


@dataclass(frozen=True, slots=True)
class FixedMiracleCost:
    cost_text: str
    mana_cost: FrozenMap
    schema_version: int = 1

    def __post_init__(self):
        expected = _ordinary_mana(self.cost_text)
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise MiracleModelError('Unknown Miracle cost version')
        if (not isinstance(self.mana_cost, Mapping) or set(self.mana_cost) != set(expected)
                or any(not _nonnegative(v) for v in self.mana_cost.values())
                or dict(self.mana_cost) != expected):
            raise MiracleModelError('Miracle mana must match its declared cost')
        object.__setattr__(self, 'mana_cost', FrozenMap(self.mana_cost))

    @classmethod
    def from_cost_text(cls, cost_text):
        return cls(cost_text, FrozenMap(_ordinary_mana(cost_text)))

    def to_dict(self):
        return {'schema_version':1, 'cost_text':self.cost_text, 'mana_cost':thaw_value(self.mana_cost)}

    @classmethod
    def from_dict(cls, value):
        _fields(value, {'schema_version','cost_text','mana_cost'}, 'Miracle cost')
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class MiracleDrawOpportunity:
    event_id: str
    drawer: str
    program_key: str
    cost: FixedMiracleCost
    card_object_id: str
    library_ref: str
    library_logical_object_id: str
    library_zone_change_counter: int
    turn_sequence: int
    turn_anchor: str
    library_size: int
    draw_ordinal: int = 1
    schema_version: int = 1

    def __post_init__(self):
        _strings(self, ('event_id','drawer','program_key','card_object_id','library_ref',
                        'library_logical_object_id','turn_anchor'))
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.cost, FixedMiracleCost)
                or not _nonnegative(self.library_zone_change_counter)
                or not _nonnegative(self.turn_sequence)
                or type(self.library_size) is not int or self.library_size < 1
                or type(self.draw_ordinal) is not int or self.draw_ordinal != 1):
            raise MiracleModelError('Miracle opportunity requires one first ordinary draw')

    @classmethod
    def capture(cls, card, *, event_id, drawer, program_key, cost, turn_sequence, turn_anchor,
                library_size, draw_ordinal):
        if not isinstance(card, CardInstance) or not card.is_card_object or card.zone != 'library' or card.owner != drawer:
            raise MiracleModelError('Miracle prospective card must be in the drawer library')
        return cls(event_id, drawer, program_key, cost, card.object_id, card.ref,
                   card.logical_object_id, card.zone_change_counter, turn_sequence,
                   turn_anchor, library_size, draw_ordinal)

    def require_current_library(self, card, *, drawer, turn_sequence, turn_anchor,
                                library_size, draw_ordinal, top_object_id):
        if (not isinstance(card, CardInstance) or not card.is_card_object or card.zone != 'library' or card.owner != self.drawer
                or type(card.zone_change_counter) is not int
                or (card.object_id,card.ref,card.logical_object_id,card.zone_change_counter)
                != (self.card_object_id,self.library_ref,self.library_logical_object_id,self.library_zone_change_counter)
                or not _nonnegative(turn_sequence) or type(library_size) is not int
                or type(draw_ordinal) is not int
                or (drawer,turn_sequence,turn_anchor,library_size,draw_ordinal,top_object_id)
                != (self.drawer,self.turn_sequence,self.turn_anchor,self.library_size,1,self.card_object_id)):
            raise MiracleModelError('Miracle prospective draw changed')

    def bind_revealed_hand(self, card, *, reveal_trigger_ref):
        if (not isinstance(card, CardInstance) or not card.is_card_object or card.zone != 'hand' or card.owner != self.drawer
                or card.object_id != self.card_object_id or type(card.zone_change_counter) is not int
                or card.zone_change_counter != self.library_zone_change_counter + 1):
            raise MiracleModelError('Miracle hand reference requires the actual committed draw')
        return MiracleRevealedHand(self.event_id,self.drawer,self.program_key,self.cost,
            card.object_id,card.ref,card.logical_object_id,card.zone_change_counter,reveal_trigger_ref)

    def to_dict(self):
        return dict(schema_version=1,kind='prospective_draw',event_id=self.event_id,drawer=self.drawer,
            program_key=self.program_key,cost=self.cost.to_dict(),card_object_id=self.card_object_id,
            library_ref=self.library_ref,library_logical_object_id=self.library_logical_object_id,
            library_zone_change_counter=self.library_zone_change_counter,turn_sequence=self.turn_sequence,
            turn_anchor=self.turn_anchor,library_size=self.library_size,draw_ordinal=self.draw_ordinal)

    @classmethod
    def from_dict(cls, value):
        _fields(value, {'schema_version','kind','event_id','drawer','program_key','cost','card_object_id',
            'library_ref','library_logical_object_id','library_zone_change_counter','turn_sequence',
            'turn_anchor','library_size','draw_ordinal'}, 'Miracle prospective draw')
        if value['kind'] != 'prospective_draw':
            raise MiracleModelError('Miracle prospective draw has the wrong role')
        return cls(**{k:FixedMiracleCost.from_dict(v) if k=='cost' else v for k,v in value.items() if k!='kind'})


@dataclass(frozen=True, slots=True)
class MiracleRevealedHand:
    event_id: str
    drawer: str
    program_key: str
    cost: FixedMiracleCost
    card_object_id: str
    hand_ref: str
    hand_logical_object_id: str
    hand_zone_change_counter: int
    reveal_trigger_ref: str
    schema_version: int = 1

    def __post_init__(self):
        _strings(self, ('event_id','drawer','program_key','card_object_id','hand_ref',
                        'hand_logical_object_id','reveal_trigger_ref'))
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.cost, FixedMiracleCost)
                or not _nonnegative(self.hand_zone_change_counter)):
            raise MiracleModelError('Miracle revealed hand identity is malformed')

    def require_current_hand(self, card):
        # No current-trigger-controller constraint belongs in this card identity.
        if (not isinstance(card, CardInstance) or not card.is_card_object or card.zone != 'hand' or card.owner != self.drawer
                or type(card.zone_change_counter) is not int
                or (card.object_id,card.ref,card.logical_object_id,card.zone_change_counter)
                != (self.card_object_id,self.hand_ref,self.hand_logical_object_id,self.hand_zone_change_counter)):
            raise MiracleModelError('Miracle revealed hand object changed')

    def to_dict(self):
        return dict(schema_version=1,kind='revealed_hand',event_id=self.event_id,drawer=self.drawer,
            program_key=self.program_key,cost=self.cost.to_dict(),card_object_id=self.card_object_id,
            hand_ref=self.hand_ref,hand_logical_object_id=self.hand_logical_object_id,
            hand_zone_change_counter=self.hand_zone_change_counter,reveal_trigger_ref=self.reveal_trigger_ref)

    @classmethod
    def from_dict(cls, value):
        _fields(value, {'schema_version','kind','event_id','drawer','program_key','cost','card_object_id',
            'hand_ref','hand_logical_object_id','hand_zone_change_counter','reveal_trigger_ref'}, 'Miracle revealed hand')
        if value['kind'] != 'revealed_hand':
            raise MiracleModelError('Miracle revealed hand has the wrong role')
        return cls(**{k:FixedMiracleCost.from_dict(v) if k=='cost' else v for k,v in value.items() if k!='kind'})


