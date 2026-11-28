from __future__ import annotations

"""Immutable progress and casting authority for public library exile scans."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib

from .mana_value_limit import ManaValueLimitSpec
from .stack_spell_name_scan_model import (
    NAME_SCAN_CAST_OPERATION, NAME_SCAN_FINISH_OPERATION, NAME_SCAN_STEP_OPERATION, StackSpellNameScan,
)
from .rules.bargain_search import BARGAIN_SEARCH_CAST_OPERATION
from .replacement.immutable import FrozenMap,thaw_value
from .rules.library_selection import LibrarySelectionObjectIdentity
from .next_cast_library_model import (
    NEXT_CAST_LIBRARY_PROGRESS_VERSION, NEXT_CAST_LIBRARY_SCAN_VERSION, valid_exclusive_mana_value,
)
from .util import stable_json

DISCOVER_CAPABILITY='library.discover.controller'
DISCOVER_OPERATION='discover'
DISCOVER_TREASURE_DIFFERENCE_VERSION = 2
DISCOVER_RECORDED_RESULT_PROGRESS_VERSION = 8
DISCOVER_TREASURE_DIFFERENCE_CAPABILITY = 'library.discover.tapped_treasure_difference'
DISCOVER_STEP_OPERATION='discover_step'
DISCOVER_CAST_OPERATION='discover_cast_choice'
DISCOVER_FINISH_OPERATION='discover_finish'
NONLAND_SCAN_CAPABILITY = 'library.exile_until_nonland.cast_without_mana'
NONLAND_SCAN_MECHANIC = 'exile-until-nonland-cast'
NONLAND_SCAN_OPERATION = 'exile_until_nonland_then_cast'
NONLAND_SCAN_STEP_OPERATION = 'exile_until_nonland_step'
NONLAND_SCAN_CAST_OPERATION = 'exile_until_nonland_cast_choice'
QUALIFIED_SCAN_CAPABILITY = 'library.exile_until_instant_or_sorcery.cast_and_return'
QUALIFIED_SCAN_MECHANIC = 'exile-until-instant-or-sorcery-cast'
QUALIFIED_SCAN_OPERATION = 'exile_until_instant_or_sorcery'
QUALIFIED_SCAN_STEP_OPERATION = 'exile_until_instant_or_sorcery_step'
QUALIFIED_SCAN_CAST_OPERATION = 'exile_until_instant_or_sorcery_cast_choice'
QUALIFIED_SCAN_FINISH_OPERATION = 'exile_until_instant_or_sorcery_finish'
QUALIFIED_SCAN_TARGET = FrozenMap({'zones': ['player'], 'categories': ['player'],
                                  'player_relation': 'opponent', 'count': 1})
EXILE_CAST_CHOICE_OPERATIONS = (
    DISCOVER_CAST_OPERATION, NONLAND_SCAN_CAST_OPERATION, QUALIFIED_SCAN_CAST_OPERATION,
    BARGAIN_SEARCH_CAST_OPERATION, NAME_SCAN_CAST_OPERATION,
)
EXILE_CAST_FINISH_OPERATIONS = (
    DISCOVER_FINISH_OPERATION, QUALIFIED_SCAN_FINISH_OPERATION, NAME_SCAN_FINISH_OPERATION,
)


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


@dataclass(frozen=True, slots=True)
class RecordedDiscoverCard:
    """Mana value of the last card actually exiled, before its fate changes."""

    mana_value: int | None = None

    def __post_init__(self):
        if self.mana_value is not None and (type(self.mana_value) is not int or self.mana_value < 0):
            raise ValueError('Discover requires an exact nonnegative recorded mana value')

    @classmethod
    def from_public_value(cls, value):
        if type(value) is float and value.is_integer():
            value = int(value)
        if type(value) is not int:
            raise ValueError('Discover requires a whole mana value from the current exile card')
        return cls(value)


@dataclass(frozen=True,slots=True)
class ExileCastProgress:
    amount: int | None
    exiled: tuple[LibrarySelectionObjectIdentity,...]
    candidate_ref: str | None
    supplier: str | None = None
    fixed_top_payment: str | None = None
    copy_parent_ref: str | None = None
    private_search_parent: str | None = None
    name_scan: StackSpellNameScan | None = None
    recorded_card: RecordedDiscoverCard | None = None
    exclusive_mana_value: int | float | None = None

    def __post_init__(self):
        if self.exclusive_mana_value is not None and (
            not valid_exclusive_mana_value(self.exclusive_mana_value) or not self.supplier
            or any(value is not None for value in (
                self.amount, self.fixed_top_payment, self.copy_parent_ref,
                self.private_search_parent, self.name_scan,
            ))
        ):
            raise ValueError("Next-cast library scans require their caster's library and sealed mana value")
        if self.name_scan is not None and (
            not isinstance(self.name_scan, StackSpellNameScan)
            or any(value is not None for value in (
                self.amount, self.supplier, self.fixed_top_payment, self.copy_parent_ref, self.private_search_parent,
            ))
        ):
            raise ValueError("A different-name scan must retain its own bound spell and library rule")
        if self.amount is not None:
            ManaValueLimitSpec(self.amount)
        if self.supplier is not None and (
            self.amount is not None or type(self.supplier) is not str or not self.supplier
        ):
            raise ValueError('A qualified exile scan requires one supplying library owner')
        if not isinstance(self.exiled,(tuple,list)):
            raise ValueError('An exile scan requires an ordered public exile history')
        exiled=tuple(self.exiled)
        if self.fixed_top_payment is not None and (
            self.fixed_top_payment not in ("free", "paid") or self.supplier is None
            or len(exiled) != 1 or self.candidate_ref is None
        ):
            raise ValueError("Fixed top casting requires one supplied exile incarnation and its payment mode")
        if self.copy_parent_ref is not None and (
            type(self.copy_parent_ref) is not str or not self.copy_parent_ref or self.fixed_top_payment != "free"
        ):
            raise ValueError("An exiled copy cast requires its resolving parent and free price")
        if self.private_search_parent is not None and (
            type(self.private_search_parent) is not str or not self.private_search_parent
            or self.amount is None or len(exiled) != 1 or self.candidate_ref is None
            or self.supplier is not None or self.fixed_top_payment is not None or self.copy_parent_ref is not None
        ):
            raise ValueError("A private search requires its single exile result and enclosing resolution")
        if (any(not isinstance(card,LibrarySelectionObjectIdentity) for card in exiled)
                or any(len({getattr(card,key) for card in exiled})!=len(exiled)
                       for key in ('object_id','logical_object_id','ref'))
                or self.candidate_ref is not None and (
                    type(self.candidate_ref) is not str or not exiled
                    or self.candidate_ref!=exiled[-1].ref)):
            raise ValueError('An exile scan requires distinct exile incarnations and its final candidate')
        if self.recorded_card is not None and (
            not isinstance(self.recorded_card, RecordedDiscoverCard) or not self.amount
            or self.private_search_parent is not None or self.supplier is not None
            or bool(exiled) is not (self.recorded_card.mana_value is not None)
            or (self.candidate_ref is not None and self.recorded_card.mana_value > self.amount)
        ):
            raise ValueError('The Discover result must retain the mana value of its last actual exile')
        for card in exiled:discover_zone_counter(card)
        object.__setattr__(self,'exiled',exiled)

    @property
    def candidate(self):
        return self.exiled[-1] if self.candidate_ref is not None else None

    @property
    def discovered_card(self):
        if self.recorded_card is None:
            return self.candidate
        value = self.recorded_card.mana_value
        return self.exiled[-1] if value is not None and value <= self.amount else None

    @property
    def cleanup_cards(self):
        return self.exiled[:-1] if self.candidate_ref is not None else self.exiled

    @property
    def step_operation(self):
        if self.name_scan is not None:
            return NAME_SCAN_STEP_OPERATION
        if self.fixed_top_payment is not None or self.private_search_parent is not None:
            return None
        if self.supplier is not None:
            return QUALIFIED_SCAN_STEP_OPERATION
        return NONLAND_SCAN_STEP_OPERATION if self.amount is None else DISCOVER_STEP_OPERATION

    @property
    def cast_operation(self):
        if self.exclusive_mana_value is not None:
            return None
        if self.name_scan is not None:
            return NAME_SCAN_CAST_OPERATION
        if self.private_search_parent is not None:
            return BARGAIN_SEARCH_CAST_OPERATION
        if self.fixed_top_payment is not None:
            from .fixed_top_exile import FIXED_TOP_EXILE_OFFER_OPERATION
            return FIXED_TOP_EXILE_OFFER_OPERATION
        if self.supplier is not None:
            return QUALIFIED_SCAN_CAST_OPERATION
        return NONLAND_SCAN_CAST_OPERATION if self.amount is None else DISCOVER_CAST_OPERATION

    @property
    def finish_operation(self):
        if self.name_scan is not None:
            return NAME_SCAN_FINISH_OPERATION
        if self.fixed_top_payment is not None or self.private_search_parent is not None:
            return None
        if self.supplier is not None:
            return QUALIFIED_SCAN_FINISH_OPERATION
        return DISCOVER_FINISH_OPERATION if self.amount is not None else None

    def to_dict(self):
        search = {'schema_version': 2, 'scan': 'first_nonland'} if self.amount is None else {
            'schema_version': 1, 'amount': self.amount,
        }
        if self.supplier is not None:
            search = {'schema_version': 3, 'scan': 'first_instant_or_sorcery', 'supplier': self.supplier}
        if self.fixed_top_payment is not None:
            search = {"schema_version": 4, "scan": "fixed_top_result", "supplier": self.supplier,
                      "payment": self.fixed_top_payment}
        if self.copy_parent_ref is not None:
            search = {"schema_version": 5, "scan": "graveyard_copy", "supplier": self.supplier,
                      "copy_parent_ref": self.copy_parent_ref}
        if self.private_search_parent is not None:
            search = {"schema_version": 6, "amount": self.amount,
                      "private_search_parent": self.private_search_parent}
        if self.name_scan is not None:
            search = {"schema_version": 7, "name_scan": self.name_scan.to_dict()}
        if self.recorded_card is not None:
            search = {'schema_version': DISCOVER_RECORDED_RESULT_PROGRESS_VERSION, 'amount': self.amount,
                      'last_exile_mana_value': self.recorded_card.mana_value}
        if self.exclusive_mana_value is not None:
            search = {
                "schema_version": NEXT_CAST_LIBRARY_PROGRESS_VERSION,
                "supplier": self.supplier,
                "exclusive_mana_value": self.exclusive_mana_value,
            }
        return {**search, 'exiled': [card.to_dict() for card in self.exiled], 'candidate_ref': self.candidate_ref}

    @classmethod
    def from_dict(cls,value):
        common = {'schema_version', 'exiled', 'candidate_ref'}
        if not isinstance(value, Mapping) or type(value.get('schema_version')) is not int:
            raise ValueError('Exile scan progress has a closed versioned shape')
        supplier = None
        payment = None
        copy_parent_ref = None
        private_search_parent = None
        name_scan = None
        recorded_card = None
        exclusive_mana_value = None
        if value['schema_version'] == 1 and set(value) == common | {'amount'}:
            amount = value['amount']
            ManaValueLimitSpec(amount)
        elif (value['schema_version'] == 2 and set(value) == common | {'scan'}
              and value['scan'] == 'first_nonland'):
            amount = None
        elif (value['schema_version'] == 3 and set(value) == common | {'scan', 'supplier'}
              and value['scan'] == 'first_instant_or_sorcery'
              and type(value['supplier']) is str and value['supplier']):
            amount, supplier = None, value['supplier']
        elif (value["schema_version"] == 4 and set(value) == common | {"scan", "supplier", "payment"}
              and value["scan"] == "fixed_top_result" and value["payment"] in ("free", "paid")):
            amount, supplier, payment = None, value["supplier"], value["payment"]
        elif (value["schema_version"] == 5 and set(value) == common | {"scan", "supplier", "copy_parent_ref"}
              and value["scan"] == "graveyard_copy" and type(value["copy_parent_ref"]) is str):
            amount, supplier, payment = None, value["supplier"], "free"
            copy_parent_ref = value["copy_parent_ref"]
        elif value["schema_version"] == 6 and set(value) == common | {"amount", "private_search_parent"}:
            amount, private_search_parent = value["amount"], value["private_search_parent"]
            ManaValueLimitSpec(amount)
            if type(private_search_parent) is not str or not private_search_parent:
                raise ValueError("A private search requires its enclosing resolution")
        elif value["schema_version"] == 7 and set(value) == common | {"name_scan"}:
            amount, name_scan = None, StackSpellNameScan.from_dict(value["name_scan"])
        elif (value['schema_version'] == DISCOVER_RECORDED_RESULT_PROGRESS_VERSION
                and set(value) == common | {'amount', 'last_exile_mana_value'}):
            amount = value['amount']
            recorded_card = RecordedDiscoverCard(value['last_exile_mana_value'])
        elif (
            value["schema_version"] == NEXT_CAST_LIBRARY_PROGRESS_VERSION
            and set(value) == common | {"supplier", "exclusive_mana_value"}
            and valid_exclusive_mana_value(value["exclusive_mana_value"])
        ):
            amount, supplier = None, value["supplier"]
            exclusive_mana_value = value["exclusive_mana_value"]
        else:
            raise ValueError('Exile scan progress has a closed versioned shape')
        if not isinstance(value['exiled'], (list, tuple)):
            raise ValueError('Exile scan progress requires an ordered history')
        return cls(amount, tuple(LibrarySelectionObjectIdentity.from_dict(card) for card in value['exiled']),
                   value['candidate_ref'], supplier, payment, copy_parent_ref, private_search_parent, name_scan,
                   recorded_card, exclusive_mana_value)


DiscoverProgress = ExileCastProgress


def require_current_discover_cards(progress,query,*,actor,include_candidate=True):
    owner = progress.supplier or actor
    for card in progress.exiled if include_candidate else progress.cleanup_cards:
        row=query.object(card.ref,zones=('exile',))
        if (row is None or row.owner!=owner or row.object_id!=card.object_id
                or row.logical_object_id!=card.logical_object_id):
            raise ValueError('A Discover exile incarnation changed')


def validate_discover_effect(effect,*,resolved=False):
    if (not isinstance(effect,Mapping) or set(effect)!={'op','schema_version','player','amount'}
            or effect['op']!=DISCOVER_OPERATION or type(effect['schema_version']) is not int
            or effect['schema_version'] not in {1, DISCOVER_TREASURE_DIFFERENCE_VERSION}
            or (effect['player']!='$controller' and not resolved)
            or type(effect['player']) is not str or not effect['player']):
        raise ValueError('Discover requires one controller-bound amount')
    amount=effect['amount']
    if effect['schema_version'] == DISCOVER_TREASURE_DIFFERENCE_VERSION and (type(amount) is not int or amount <= 0):
        raise ValueError('Discover with a Treasure difference requires the printed fixed amount')
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
    from .fixed_top_exile import FIXED_TOP_EXILE_OPERATION, fixed_top_exile_dependencies
    if value.get("op") == FIXED_TOP_EXILE_OPERATION:
        return fixed_top_exile_dependencies(value)
    if value.get('op') == DISCOVER_OPERATION:
        validate_discover_effect(value)
        if value['schema_version'] == DISCOVER_TREASURE_DIFFERENCE_VERSION:
            return (DISCOVER_CAPABILITY, DISCOVER_TREASURE_DIFFERENCE_CAPABILITY)
        return (DISCOVER_CAPABILITY,)
    if value.get('op') == NONLAND_SCAN_OPERATION:
        validate_nonland_scan_effect(value)
        return (NONLAND_SCAN_CAPABILITY,)
    if value.get('op') == QUALIFIED_SCAN_OPERATION:
        validate_qualified_scan_effect(value)
        return (QUALIFIED_SCAN_CAPABILITY,)
    return ()


def validate_nonland_scan_effect(effect, *, resolved=False):
    if (not isinstance(effect, Mapping) or set(effect) != {'op', 'schema_version', 'player'}
            or effect['op'] != NONLAND_SCAN_OPERATION or type(effect['schema_version']) is not int
            or effect['schema_version'] != 1 or type(effect['player']) is not str or not effect['player']
            or not resolved and effect['player'] != '$controller'):
        raise ValueError('The nonland scan requires its exact controller-bound instruction')


def validate_qualified_scan_effect(effect, *, resolved=False):
    if isinstance(effect, Mapping) and effect.get("schema_version") == NEXT_CAST_LIBRARY_SCAN_VERSION:
        if (
            not resolved or type(effect["schema_version"]) is not int
            or set(effect) != {"op", "schema_version", "player", "supplier", "exclusive_mana_value"}
            or effect["op"] != QUALIFIED_SCAN_OPERATION or type(effect["player"]) is not str
            or not effect["player"] or effect["player"].startswith("$") or effect["supplier"] != effect["player"]
            or not valid_exclusive_mana_value(effect["exclusive_mana_value"])
        ):
            raise ValueError("Next-cast library access requires the sealed caster and exclusive mana value")
        return
    if (
        not isinstance(effect, Mapping)
        or set(effect) != {'op', 'schema_version', 'player', 'supplier'}
        or effect['op'] != QUALIFIED_SCAN_OPERATION
        or type(effect['schema_version']) is not int or effect['schema_version'] != 1
        or type(effect['player']) is not str or not effect['player']
        or type(effect['supplier']) is not str or not effect['supplier']
        or not resolved and (effect['player'] != '$controller' or effect['supplier'] not in {'$target.0', '$context.target'})
    ):
        raise ValueError('The qualified exile scan requires its bound controller and library supplier')


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
                or not isinstance(self.progress,ExileCastProgress) or self.progress.finish_operation is None):
            raise ValueError('Discover cleanup requires its current controller and resolution')
        object.__setattr__(self,'replacement_selections',_freeze_replacement_selections(
            self.replacement_selections,family='Discover cleanup'))

    def identity(self):
        return {'actor':self.actor,'stack_ref':self.stack_ref,'progress':self.progress.to_dict()}


def decode_discover_cleanup_identity(value):
    if not isinstance(value,Mapping) or set(value)!={'actor','stack_ref','progress'}:
        raise ValueError('Discover cleanup has a closed continuation identity')
    return DiscoverCleanupIntent(value['actor'],value['stack_ref'],ExileCastProgress.from_dict(value['progress'])).identity()
