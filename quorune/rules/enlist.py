from __future__ import annotations
from ..entry_copy import copiable_oracle_id

"""Enlist attack cost and reflexive trigger using canonical tap and scalar owners."""

from collections.abc import Mapping
from ..ability_fragments import canonical_ability_fragments
from ..enlist_model import EnlistSpec
from ..haste import summoning_sickness_prohibits_attack
from ..model import StackItem
from ..object_predicate import ObjectQuerySpec
from ..object_query import exact_numeric_characteristic
from ..scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin,SCALAR_REFERENCE_CONTEXT
from ..errors import GameRuleError
from ..card_program_faces import program_matches_face
from .activation_costs import FixedTapActivationCost,prepare_fixed_tap_cost,commit_fixed_tap_cost


def enlist_count(host,card):
    return sum(isinstance(spec,EnlistSpec) for spec in canonical_ability_fragments(host._effective_card_data(card).get('ability_fragments',())))


def enlist_candidates(host,seat):
    return tuple(card for card in host.state.cards.values() if card.zone=='battlefield' and card.controller==seat and not card.tapped and not card.phased_out
        and 'creature' in host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))[0]
        and card.attacking is None and not summoning_sickness_prohibits_attack(host,card))


def enlist_offer_rows(host,seat,candidates):
    legal=[card.ref for card in enlist_candidates(host,seat)]
    return [{'value':str(row['id']),'label':str(row.get('name') or row['id']),
        'maximum':enlist_count(host,next(c for c in host.state.cards.values() if c.ref==row['id'])),
        'options':[{'value':ref,'label':ref} for ref in legal if ref!=row['id']]} for row in candidates
        if enlist_count(host,next(c for c in host.state.cards.values() if c.ref==row['id']))]


def prepare_enlist_costs(host,seat,chosen,raw):
    if raw is None:return ()
    if not isinstance(raw,Mapping):raise GameRuleError('Enlist must map attacking creatures to selected creatures')
    attackers={card.ref:card for card,_ in chosen};attacking_ids={card.object_id for card,_ in chosen};eligible={card.ref:card for card in enlist_candidates(host,seat)};plans=[];used=set()
    cost=FixedTapActivationCost(count=1,another=True,predicate=ObjectQuerySpec(zones=('battlefield',),controller='$actor',types_all=('creature',),tapped=False,known_to_actor=True,exclude_ref='$source'))
    class Choice:
        def fixed_tap_cost(self):return cost
    for source_ref,refs in sorted(raw.items()):
        source=attackers.get(source_ref)
        if source is None:raise GameRuleError('Only a declared attacker may enlist')
        if not any(p.provenance.get('template_id')=='printed-enlist-reflexive-growth-v1' and host.semantic_program_is_current_trusted(p) and program_matches_face(host.card_record(source),p,source) for p in host.semantics.programs_for_oracle(copiable_oracle_id(source))):
            raise GameRuleError('Enlist requires its current trusted reflexive program')
        refs=tuple(refs) if isinstance(refs,(list,tuple)) else (refs,)
        if not refs or len(refs)>enlist_count(host,source):raise GameRuleError('Enlist selection exceeds current printed instance count')
        for ref in refs:
            card=eligible.get(ref) if type(ref) is str else None
            if card is None or card.object_id in attacking_ids or card.object_id in used:raise GameRuleError('Enlist requires a distinct eligible nonattacking creature')
            used.add(card.object_id)
            plan=prepare_fixed_tap_cost(host,seat=seat,source=source,choice=Choice(),response={'cost_cards':[ref]})
            plans.append((source,card,plan,seat,source.logical_object_id))
    return tuple(plans)


def commit_enlist_costs(host,plans):
    for source,card,plan,_seat,_identity in plans:commit_fixed_tap_cost(host,plan)


def enlist_trigger_items(host,plans):
    result=[]
    ordinals={}
    for ordinal,(source,card,_plan,seat,identity) in enumerate(plans):
        programs=sorted([p for p in host.semantics.programs_for_oracle(copiable_oracle_id(source)) if p.provenance.get('template_id')=='printed-enlist-reflexive-growth-v1' and host.semantic_program_is_current_trusted(p) and program_matches_face(host.card_record(source),p,source)],key=lambda p:p.key)
        if not programs:raise GameRuleError('Enlist requires its current trusted reflexive program')
        data=host._effective_card_data(card);power=exact_numeric_characteristic(card,data,'power');toughness=exact_numeric_characteristic(card,data,'toughness');ref=host._next_ref('S')
        snapshot={'ref':card.ref,'object_id':card.object_id,'logical_object_id':card.logical_object_id,'zone':'battlefield','power':power,'toughness':toughness,'mana_value':data.get('mana_value',0)}
        result.append(StackItem(stack_id=host._stable_runtime_id('stack',ref),ref=ref,kind='triggered_ability',controller=seat,label=f'Enlist {source.ref}',source_object_id=source.object_id,
            semantic_key=programs[ordinals.get(source.object_id,0)%len(programs)].key,visibility=list(host.seats),context={'source_logical_object_id':identity,'event':'combat.enlist',SCALAR_REFERENCE_CONTEXT:{ScalarAmountOrigin.EVENT_OBJECT.value:snapshot}}))
        ordinals[source.object_id]=ordinals.get(source.object_id,0)+1
    return result
