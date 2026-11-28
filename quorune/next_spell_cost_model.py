"""Closed resolving reductions and pending next-eligible-spell journal rules."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from .cast_cost_modifiers import CastCostAffectedController, PublicCastCostModifierSpec
from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError
from .object_predicate import ObjectQuerySpec
from .query_effect_amount_model import PUBLIC_QUERY_AMOUNT_KIND, PUBLIC_QUERY_AMOUNT_CAPABILITY, PublicQueryAmountSpec
from .replacement.immutable import immutable_fingerprint

OPERATION='grant_next_spell_cost_reduction'
MECHANIC='next-eligible-spell-cost-reduction'
CAPABILITY='casting.cost.next_eligible_spell_reduction'
EFFECT_KIND='resolution_next_spell_cost_reduction'
NEXT_TURN_SPELL_COST_VERSION = 3
NEXT_ELIGIBLE_FREE_VERSION = 4
NEXT_ELIGIBLE_FREE_CAPABILITY = "casting.cost.next_eligible_spell_alternative"
NEXT_ELIGIBLE_FREE_MECHANIC = "next-eligible-spell-alternative-cost"


def next_eligible_free_spell_query():
    return ObjectQuerySpec(types_any=("creature",), colors_any=("R", "G"))


def next_eligible_free_spell_effect():
    return {"op": OPERATION, "schema_version": NEXT_ELIGIBLE_FREE_VERSION,
            "spell_query": next_eligible_free_spell_query().to_dict(), "amount": 0}
RULES=('118.7a','601.2f','601.2h','601.2i','611.2a')


def validate_spell_query(value):
    query=ObjectQuerySpec.from_dict(value)
    PublicCastCostModifierSpec(CastCostAffectedController.SOURCE_CONTROLLER,(query,),-1)
    return query


def recurring_spell_cost_query(face_down_only: bool) -> ObjectQuerySpec:
    return ObjectQuerySpec(types_any=() if face_down_only else ("instant", "sorcery"))


def _validate_recurring_spell_cost_instruction(value):
    if (
        set(value) != {"op", "schema_version", "spell_query", "amount", "face_down_only"}
        or value["op"] != OPERATION or type(value["schema_version"]) is not int
        or value["schema_version"] != 2 or type(value["amount"]) is not int or value["amount"] != 1
        or type(value["face_down_only"]) is not bool
    ):
        raise ValueError("Recurring spell costs require one fixed generic reduction and its announced scope")
    expected = recurring_spell_cost_query(value["face_down_only"])
    query = validate_spell_query(value["spell_query"])
    if query != expected or value["spell_query"] != expected.to_dict():
        raise ValueError("Recurring spell cost predicates must be canonical")
    return query, (CAPABILITY,)


def next_turn_spell_cost_effect():
    return {
        "op": OPERATION, "schema_version": NEXT_TURN_SPELL_COST_VERSION,
        "spell_query": recurring_spell_cost_query(False).to_dict(), "amount": 1,
    }


def validate_next_spell_cost_instruction(value,*,resolved=False):
    if isinstance(value, Mapping) and value.get("schema_version") == NEXT_TURN_SPELL_COST_VERSION:
        query = recurring_spell_cost_query(False)
        if (type(value["schema_version"]) is not int or type(value.get("amount")) is not int
                or immutable_fingerprint(value) != immutable_fingerprint(next_turn_spell_cost_effect())):
            raise ValueError("Next-turn spell costs require the exact recurring instant and sorcery reduction")
        return query, (CAPABILITY,)
    if isinstance(value, Mapping) and value.get("schema_version") == NEXT_ELIGIBLE_FREE_VERSION:
        from .replacement.immutable import FrozenMap

        if (FrozenMap(value) != FrozenMap(next_eligible_free_spell_effect())
                or type(value["schema_version"]) is not int or type(value["amount"]) is not int):
            raise ValueError("The temporary free price requires its exact red or green creature query")
        return next_eligible_free_spell_query(), (CAPABILITY, NEXT_ELIGIBLE_FREE_CAPABILITY)
    if isinstance(value, Mapping) and value.get("schema_version") == 2:
        return _validate_recurring_spell_cost_instruction(value)
    if (not isinstance(value,Mapping) or set(value)!={'op','schema_version','spell_query','amount'}
            or value['op']!=OPERATION or type(value['schema_version']) is not int or value['schema_version']!=1):
        raise ValueError('Next-spell cost instructions have one closed schema')
    query=validate_spell_query(value['spell_query']);amount=value['amount'];dependencies={CAPABILITY}
    if type(amount) is int and amount>=0:return query,tuple(sorted(dependencies))
    if resolved or not isinstance(amount,Mapping):
        raise ValueError('A resolved next-spell reduction requires a nonnegative integer')
    from .scalar_effect_amount_model import SCALAR_AMOUNT_KIND, SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec
    if amount.get('kind')==PUBLIC_QUERY_AMOUNT_KIND:
        spec=PublicQueryAmountSpec.from_dict(amount)
        if spec.schema_version!=1 or spec.coefficient!=1:
            raise ValueError('Next-spell query amounts are independent positive public values')
        dependencies.add(PUBLIC_QUERY_AMOUNT_CAPABILITY)
    elif amount.get('kind')==SCALAR_AMOUNT_KIND:
        spec=ScalarEffectAmountSpec.from_dict(amount)
        if (spec.schema_version!=1 or spec.origin is not ScalarAmountOrigin.SOURCE
                or spec.characteristic not in {'power','toughness'} or spec.coefficient!=1 or spec.binding_id is not None):
            raise ValueError('Next-spell scalar values require current-or-last-known source stats')
        dependencies.add(SCALAR_AMOUNT_CAPABILITY)
    else:raise ValueError('Next-spell amount producer is unsupported')
    return query,tuple(sorted(dependencies))


@dataclass(frozen=True,slots=True)
class ResolvedNextSpellCostReduction:
    effect_id: str
    source_id: str
    timestamp: int
    controller: str
    spell_query: ObjectQuerySpec
    amount: int
    duration: ContinuousEffectDuration=ContinuousEffectDuration.UNTIL_END_OF_TURN
    schema_version: int=1
    face_down_only: bool=False
    expires_on_next_turn_of: str | None = None

    def __post_init__(self):
        if (any(type(v) is not str or not v for v in (self.effect_id,self.source_id,self.controller))
                or type(self.timestamp) is not int or self.timestamp<0
                or type(self.amount) is not int or self.amount<0
                or type(self.schema_version) is not int or self.schema_version not in {1, 2, NEXT_TURN_SPELL_COST_VERSION, NEXT_ELIGIBLE_FREE_VERSION}
                or type(self.face_down_only) is not bool
                or self.schema_version != 2 and self.face_down_only
                or self.duration is not (ContinuousEffectDuration.UNTIL_NEXT_TURN
                    if self.schema_version == NEXT_TURN_SPELL_COST_VERSION else ContinuousEffectDuration.UNTIL_END_OF_TURN)
                or self.expires_on_next_turn_of != (self.controller
                    if self.schema_version == NEXT_TURN_SPELL_COST_VERSION else None)
                or not isinstance(self.spell_query,ObjectQuerySpec)):
            raise ContinuousEffectError('Pending next-spell reduction has invalid identity, amount or duration')
        if self.schema_version == NEXT_ELIGIBLE_FREE_VERSION:
            if self.amount != 0 or self.spell_query != next_eligible_free_spell_query():
                raise ContinuousEffectError("The recorded free price requires its exact creature and color scope")
        else:
            validate_spell_query(self.spell_query.to_dict())
        if self.schema_version in {2, NEXT_TURN_SPELL_COST_VERSION} and (
            self.amount != 1 or self.spell_query != recurring_spell_cost_query(self.face_down_only)
        ):
            raise ContinuousEffectError("Recurring cost rule requires its exact spell scope")

    def to_dict(self):
        value = {'effect_kind':EFFECT_KIND,'schema_version':self.schema_version,'effect_id':self.effect_id,
            'source_id':self.source_id,'timestamp':self.timestamp,'controller':self.controller,
            'spell_query':self.spell_query.to_dict(),'amount':self.amount,'duration':self.duration.value}
        if self.schema_version == 2:
            value['face_down_only'] = self.face_down_only
        if self.schema_version == NEXT_TURN_SPELL_COST_VERSION:
            value['expires_on_next_turn_of'] = self.expires_on_next_turn_of
        return value

    @classmethod
    def from_dict(cls,value):
        fields = {'effect_kind','schema_version','effect_id','source_id','timestamp','controller',
                  'spell_query','amount','duration'}
        if isinstance(value, Mapping) and value.get('schema_version') == 2:
            fields.add('face_down_only')
        if isinstance(value, Mapping) and value.get('schema_version') == NEXT_TURN_SPELL_COST_VERSION:
            fields.add('expires_on_next_turn_of')
        if not isinstance(value, Mapping) or set(value) != fields or value['effect_kind'] != EFFECT_KIND:
            raise ContinuousEffectError('Pending spell reduction fields are incomplete or unknown')
        query = (ObjectQuerySpec.from_dict(value["spell_query"])
                 if value["schema_version"] == NEXT_ELIGIBLE_FREE_VERSION else validate_spell_query(value["spell_query"]))
        pending = cls(value['effect_id'],value['source_id'],value['timestamp'],value['controller'],
            query,value['amount'],ContinuousEffectDuration(value['duration']),
            value['schema_version'],value.get('face_down_only',False),value.get('expires_on_next_turn_of'))
        if pending.schema_version in {2, NEXT_TURN_SPELL_COST_VERSION, NEXT_ELIGIBLE_FREE_VERSION} and pending.to_dict() != value:
            raise ContinuousEffectError("Recurring spell cost journal must use its exact canonical fields")
        return pending

    @property
    def fingerprint(self):return immutable_fingerprint(self.to_dict())


def next_spell_cost_node_capabilities(*,effects,target_schema,mechanic_ids,**kwargs):
    if MECHANIC not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    if effects[0].get('schema_version') == NEXT_TURN_SPELL_COST_VERSION:return ()
    if (effects[0].get("schema_version") == NEXT_ELIGIBLE_FREE_VERSION) != (NEXT_ELIGIBLE_FREE_MECHANIC in mechanic_ids):
        return ()
    try:return validate_next_spell_cost_instruction(effects[0])[1]
    except (TypeError,ValueError):return ()
