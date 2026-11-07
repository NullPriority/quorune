from __future__ import annotations

"""Immutable progress and casting authority for public library exile scans."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib

from .mana_value_limit import ManaValueLimitSpec
from .replacement.immutable import FrozenMap,thaw_value
from .rules.library_selection import LibrarySelectionObjectIdentity
from .util import stable_json

DISCOVER_CAPABILITY='library.discover.controller'
DISCOVER_OPERATION='discover'
DISCOVER_STEP_OPERATION='discover_step'
DISCOVER_CAST_OPERATION='discover_cast_choice'
DISCOVER_FINISH_OPERATION='discover_finish'
NONLAND_SCAN_CAPABILITY = 'library.exile_until_nonland.cast_without_mana'
NONLAND_SCAN_MECHANIC = 'exile-until-nonland-cast'
NONLAND_SCAN_OPERATION = 'exile_until_nonland_then_cast'
NONLAND_SCAN_STEP_OPERATION = 'exile_until_nonland_step'
NONLAND_SCAN_CAST_OPERATION = 'exile_until_nonland_cast_choice'


def discover_options_fingerprint(options):
    return hashlib.sha256(stable_json([thaw_value(option) for option in options]).encode()).hexdigest()


def discover_zone_counter(card):
    try:counter=int(card.logical_object_id.rsplit('@',1)[1])
    except (IndexError,ValueError,AttributeError) as exc:
        raise ValueError('Discover requires a canonical logical incarnation') from exc
    if counter<0 or card.logical_object_id!=f'{card.object_id}@{counter}':
        raise ValueError('Discover requires a canonical logical incarnation')
    return counter


def discover_spell_qualifies(amount,*,mana_value):
    return ManaValueLimitSpec(amount).permits(mana_value)


def discover_card_qualifies(amount,*,types,mana_value):
    return 'land' not in types and discover_spell_qualifies(amount,mana_value=mana_value)


@dataclass(frozen=True,slots=True)
class ExileCastProgress:
    amount: int | None
    exiled: tuple[LibrarySelectionObjectIdentity,...]
    candidate_ref: str | None

    def __post_init__(self):
        if self.amount is not None:
            ManaValueLimitSpec(self.amount)
        if not isinstance(self.exiled,(tuple,list)):
            raise ValueError('An exile scan requires an ordered public exile history')
        exiled=tuple(self.exiled)
        if (any(not isinstance(card,LibrarySelectionObjectIdentity) for card in exiled)
                or any(len({getattr(card,key) for card in exiled})!=len(exiled)
                       for key in ('object_id','logical_object_id','ref'))
                or self.candidate_ref is not None and (
                    type(self.candidate_ref) is not str or not exiled
                    or self.candidate_ref!=exiled[-1].ref)):
            raise ValueError('An exile scan requires distinct exile incarnations and its final candidate')
        for card in exiled:discover_zone_counter(card)
        object.__setattr__(self,'exiled',exiled)

    @property
    def candidate(self):
        return self.exiled[-1] if self.candidate_ref is not None else None

    @property
    def cleanup_cards(self):
        return self.exiled[:-1] if self.candidate_ref is not None else self.exiled

    def to_dict(self):
        search = {'schema_version': 2, 'scan': 'first_nonland'} if self.amount is None else {
            'schema_version': 1, 'amount': self.amount,
        }
        return {**search, 'exiled': [card.to_dict() for card in self.exiled], 'candidate_ref': self.candidate_ref}

    @classmethod
    def from_dict(cls,value):
        common = {'schema_version', 'exiled', 'candidate_ref'}
        if not isinstance(value, Mapping) or type(value.get('schema_version')) is not int:
            raise ValueError('Exile scan progress has a closed versioned shape')
        if value['schema_version'] == 1 and set(value) == common | {'amount'}:
            amount = value['amount']
            ManaValueLimitSpec(amount)
        elif (value['schema_version'] == 2 and set(value) == common | {'scan'}
              and value['scan'] == 'first_nonland'):
            amount = None
        else:
            raise ValueError('Exile scan progress has a closed versioned shape')
        if not isinstance(value['exiled'], (list, tuple)):
            raise ValueError('Exile scan progress requires an ordered history')
        return cls(amount, tuple(LibrarySelectionObjectIdentity.from_dict(card) for card in value['exiled']),
                   value['candidate_ref'])


DiscoverProgress = ExileCastProgress


def require_current_discover_cards(progress,query,*,actor,include_candidate=True):
    for card in progress.exiled if include_candidate else progress.cleanup_cards:
        row=query.object(card.ref,zones=('exile',))
        if (row is None or row.owner!=actor or row.object_id!=card.object_id
                or row.logical_object_id!=card.logical_object_id):
            raise ValueError('A Discover exile incarnation changed')


def validate_discover_effect(effect,*,resolved=False):
    if (not isinstance(effect,Mapping) or set(effect)!={'op','schema_version','player','amount'}
            or effect['op']!=DISCOVER_OPERATION or type(effect['schema_version']) is not int
            or effect['schema_version']!=1 or (effect['player']!='$controller' and not resolved)
            or type(effect['player']) is not str or not effect['player']):
        raise ValueError('Discover requires one controller-bound amount')
    amount=effect['amount']
    if type(amount) is int:
        ManaValueLimitSpec(amount)
    elif not resolved and isinstance(amount,Mapping):
        from .query_effect_amount_model import (PublicQueryAmountSpec,CastXAmountSpec,
            PUBLIC_QUERY_AMOUNT_KIND,CAST_X_AMOUNT_KIND)
        from .scalar_effect_amount_model import ScalarEffectAmountSpec,SCALAR_AMOUNT_KIND
        constructor={PUBLIC_QUERY_AMOUNT_KIND:PublicQueryAmountSpec,CAST_X_AMOUNT_KIND:CastXAmountSpec,
            SCALAR_AMOUNT_KIND:ScalarEffectAmountSpec}.get(amount.get('kind'))
        if constructor is None or constructor.from_dict(amount).coefficient<0:
            raise ValueError('Discover amount producer is unrepresented or negative')
    else:raise ValueError('Discover amount must be resolved before exiling')


def discover_payload_dependencies(value):
    if not isinstance(value, Mapping):
        return ()
    if value.get('op') == DISCOVER_OPERATION:
        validate_discover_effect(value)
        return (DISCOVER_CAPABILITY,)
    if value.get('op') == NONLAND_SCAN_OPERATION:
        validate_nonland_scan_effect(value)
        return (NONLAND_SCAN_CAPABILITY,)
    return ()


def validate_nonland_scan_effect(effect, *, resolved=False):
    if (not isinstance(effect, Mapping) or set(effect) != {'op', 'schema_version', 'player'}
            or effect['op'] != NONLAND_SCAN_OPERATION or type(effect['schema_version']) is not int
            or effect['schema_version'] != 1 or type(effect['player']) is not str or not effect['player']
            or not resolved and effect['player'] != '$controller'):
        raise ValueError('The nonland scan requires its exact controller-bound instruction')


@dataclass(frozen=True,slots=True)
class DiscoverCastIntent:
    """The shared resolving exile cast carried by the historical intent wire."""

    actor: str
    stack_ref: str
    progress: ExileCastProgress
    options_fingerprint: str
    response: FrozenMap
    replacement_selections: tuple[str|FrozenMap,...]=()

    def __post_init__(self):
        from .semantic_runtime.zone_move_intent import _freeze_replacement_selections
        if (any(type(value) is not str or not value for value in
                (self.actor,self.stack_ref,self.options_fingerprint))
                or not isinstance(self.progress,ExileCastProgress) or self.progress.candidate is None
                or not isinstance(self.response,FrozenMap)):
            raise ValueError('Discover cast requires its current controller, resolution, candidate and options')
        if any(type(key) is not str or key.startswith('_') for key in self.response):
            raise ValueError('Discover accepts no client-supplied internal casting-payment fields')
        object.__setattr__(self,'replacement_selections',_freeze_replacement_selections(
            self.replacement_selections,family='Discover casting'))

    def identity(self):
        return {'actor':self.actor,'stack_ref':self.stack_ref,'progress':self.progress.to_dict(),
                'options_fingerprint':self.options_fingerprint,'response':thaw_value(self.response)}


def decode_discover_cast_identity(value):
    if not isinstance(value,Mapping) or set(value)!={'actor','stack_ref','progress','options_fingerprint','response'}:
        raise ValueError('Discover casting has a closed continuation identity')
    if not isinstance(value['response'],Mapping):raise ValueError('Discover casting response must be an object')
    return DiscoverCastIntent(value['actor'],value['stack_ref'],ExileCastProgress.from_dict(value['progress']),
        value['options_fingerprint'],FrozenMap(value['response'])).identity()


@dataclass(frozen=True,slots=True)
class DiscoverCleanupIntent:
    actor: str
    stack_ref: str
    progress: ExileCastProgress
    replacement_selections: tuple[str|FrozenMap,...]=()

    def __post_init__(self):
        from .semantic_runtime.zone_move_intent import _freeze_replacement_selections
        if (any(type(value) is not str or not value for value in (self.actor,self.stack_ref))
                or not isinstance(self.progress,ExileCastProgress) or self.progress.amount is None):
            raise ValueError('Discover cleanup requires its current controller and resolution')
        object.__setattr__(self,'replacement_selections',_freeze_replacement_selections(
            self.replacement_selections,family='Discover cleanup'))

    def identity(self):
        return {'actor':self.actor,'stack_ref':self.stack_ref,'progress':self.progress.to_dict()}


def decode_discover_cleanup_identity(value):
    if not isinstance(value,Mapping) or set(value)!={'actor','stack_ref','progress'}:
        raise ValueError('Discover cleanup has a closed continuation identity')
    return DiscoverCleanupIntent(value['actor'],value['stack_ref'],ExileCastProgress.from_dict(value['progress'])).identity()
