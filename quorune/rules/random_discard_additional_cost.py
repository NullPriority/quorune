from __future__ import annotations

"""One mandatory random hand payment, using the ordinary discard cost owner."""

from dataclasses import dataclass
from typing import Mapping

from ..additional_cost_vocabulary import DISCARD_ONE_COST,ZONE_CHANGE_COST_KIND
from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..random_discard import RandomDiscardIntent,resolve_random_discard
from .casting_additional_costs import AdditionalCostError,FixedZoneChangeAdditionalCost

RANDOM_SPELL_COST_KIND='random_discard'
RANDOM_SPELL_COST_CAPABILITY='casting.additional_cost.fixed_random_discard'


@dataclass(frozen=True,slots=True)
class FixedRandomDiscardAdditionalCost:
    count: int = 1
    schema_version: int = 1

    def __post_init__(self):
        if type(self.count) is not int or self.count!=1 or type(self.schema_version) is not int or self.schema_version!=1:
            raise AdditionalCostError('Random spell costs require exactly one unrestricted hand card')

    def to_descriptor(self):return {'kind':RANDOM_SPELL_COST_KIND,'schema_version':self.schema_version,'count':self.count}

    @classmethod
    def from_descriptor(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'kind','schema_version','count'} or value['kind']!=RANDOM_SPELL_COST_KIND:
            raise AdditionalCostError('Random spell cost fields are closed')
        return cls(count=value['count'],schema_version=value['schema_version'])


def random_spell_cost_dependencies(cost_schema):
    if not isinstance(cost_schema,Mapping):return ()
    costs=cost_schema.get('additional_costs',())
    if not isinstance(costs,(list,tuple)):
        raise AdditionalCostError('Additional spell costs require an array')
    if not any(isinstance(cost,Mapping) and cost.get('kind')==RANDOM_SPELL_COST_KIND for cost in costs):return ()
    composed = fixed_life_random_costs(costs)
    if set(cost_schema)!={'additional_costs'} or (len(costs)!=1 and not composed):
        raise AdditionalCostError('Random spell payment cannot compose other additional prices')
    FixedRandomDiscardAdditionalCost.from_descriptor(costs[-1])
    return (RANDOM_SPELL_COST_CAPABILITY,'casting.additional_cost.zone_change.fixed_discard','zone.discard.fixed_random',
        *(('casting.additional_cost.fixed_life_random','casting.additional_cost.fixed_life_payment') if composed else ()))


def fixed_life_random_costs(costs):
    """One deterministic life price followed by the existing random hand price."""
    if not isinstance(costs,(list,tuple)) or len(costs)!=2:return False
    from .casting_additional_cost_groups import FixedLifePaymentAdditionalCost
    try:
        FixedLifePaymentAdditionalCost.from_descriptor(costs[0])
        FixedRandomDiscardAdditionalCost.from_descriptor(costs[1])
    except (ValueError,TypeError):return False
    return True


def select_random_spell_cost(host,seat,source,costs,response,*,position,choice_schema):
    FixedRandomDiscardAdditionalCost.from_descriptor(costs[position])
    if (len(costs)!=1 and not fixed_life_random_costs(costs)) or any(key in response for key in ('cost_cards','cost_objects','discard_cards')):return False,None
    if len([oid for oid in host.state.players[seat].zones['hand'] if oid!=source.object_id])<1:return False,None
    return True,{'kind':RANDOM_SPELL_COST_KIND,'cost_position':position}


def random_spell_cost_refs(host,seat,source,response):
    if any(key in response for key in ('cost_cards','cost_objects','discard_cards')):
        raise GameRuleError('Random spell payment is not a selected card cost')
    payment_id=response.get('_mana_payment_id')
    if type(payment_id) is not str or not payment_id:raise GameRuleError('Random spell payment requires its server identity')
    from ..cast_source_placement_model import cast_source_placement_version
    # One server payment identity spans placement and any rolled-back retry.
    # New casting moves the source to a fresh stack incarnation before payment.
    identity=(source.object_id if cast_source_placement_version(
        getattr(host.state,'cast_source_placement_version',None))==1 else source.logical_object_id)
    intent=RandomDiscardIntent(actor=seat,players=(seat,),count=1,
        randomization_key=f'{payment_id}|spell-random-discard|{identity}',reason='spell additional cost')
    refs=tuple(resolve_random_discard(host,intent,commit_batch=lambda values:values,exclude_object_ids=(source.object_id,)))
    if len(refs)!=1:raise GameRuleError('Random spell payment requires another current hand card')
    frozen=response.get('_random_discard_cost_refs')
    if frozen is not None and (not isinstance(frozen,(list,tuple)) or tuple(frozen)!=refs):
        raise GameRuleError('Random spell payment changed across its continuation')
    return refs


def ordinary_random_spell_cost_selection(host,proposal,response,option,selected):
    costs=option.get('additional_costs',())
    random_spell_cost_dependencies({'additional_costs':costs})
    position=len(costs)-1
    if selected!={'kind':RANDOM_SPELL_COST_KIND,'cost_position':position}:
        raise GameRuleError('Random spell payment selection changed')
    source=host.state.cards[proposal.object_id];refs=random_spell_cost_refs(host,proposal.seat,source,response)
    cost=FixedZoneChangeAdditionalCost(operation=DISCARD_ONE_COST,choice_field='discard_cards',
        predicate=ObjectQuerySpec(zones=('hand',),owner='$actor',known_to_actor=True))
    # The life component has already been paid by the canonical nonmoving owner.
    # Present only this validated moving leaf to the existing single-zone commit.
    return ({**option,'additional_costs':[cost.to_descriptor()]},
            {'kind':ZONE_CHANGE_COST_KIND,'operation':DISCARD_ONE_COST,'cost_position':0,'card':refs[0]})


def freeze_random_spell_cost_response(host,seat,response):
    ref=response.get('card') or response.get('id')
    source=next((card for card in host.state.cards.values() if card.ref==ref),None)
    if source is None:return response
    mandatory=any(random_spell_cost_dependencies(program.cost_schema)
               for program in host.semantics.programs_for_oracle(source.oracle_id))
    optional=False
    if response.get('cost_option')=='buyback':
        from ..compiled_cast_lifecycles import compiled_fixed_cast_lifecycle_specs
        optional=any(spec.schema_version==5 and any(cost.get('kind')==RANDOM_SPELL_COST_KIND for cost in spec.additional_costs)
            for spec in compiled_fixed_cast_lifecycle_specs(host,source))
    if not mandatory and not optional:return response
    return {**response,'_random_discard_cost_refs':list(random_spell_cost_refs(host,seat,source,response))}


def random_spell_cost_reference(response,*,origin,destination,object_ref):
    refs=response.get('_random_discard_cost_refs');payment_id=response.get('_mana_payment_id');source_ref=response.get('card') or response.get('id')
    return object_ref if (origin=='hand' and destination=='graveyard'
        and type(payment_id) is str and payment_id and type(source_ref) is str and source_ref
        and isinstance(refs,(list,tuple)) and len(refs)==1 and type(refs[0]) is str and refs[0]
        and refs[0]!=source_ref and object_ref==refs[0]
        and not any(key in response for key in ('cost_cards','cost_objects','discard_cards'))) else None
