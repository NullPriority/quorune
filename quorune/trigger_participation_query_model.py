from __future__ import annotations

"""Closed typed source/event participation catalog; no state access."""

from dataclasses import dataclass
from .creature_subtypes import CREATURE_SUBTYPES
from .object_predicate import ObjectQuerySpec

TRIGGER_PREDICATE_CAPABILITY='trigger.participation.typed_source_event'
TRIGGER_PROHIBITION_CAPABILITY='trigger.participation.prohibition'


@dataclass(frozen=True,slots=True)
class TriggerParticipationRule:
    events: tuple[str,...]=()
    source_queries: tuple[ObjectQuerySpec,...]=()
    event_queries: tuple[ObjectQuerySpec,...]=()
    source_relation: str='you'
    event_relation: str='any'
    exclusion: str='none'
    modifier_condition: str='none'
    outcome: str='additional'
    source_zones: tuple[str,...]=('battlefield',)


def _q(**values):return ObjectQuerySpec(**values)


def trigger_participation_rule(name,subtype=None):
    if type(name) is not str:raise ValueError('Trigger participation rule must be represented')
    subtype_rules={'source_subtype','another_source_subtype','entering_subtype','controlled_entering_land_or_subtype'}
    if name in subtype_rules:
        if type(subtype) is not str or subtype not in CREATURE_SUBTYPES:
            raise ValueError('Trigger participation requires a canonical creature subtype')
    elif subtype is not None:raise ValueError('Unexpected trigger participation subtype')
    entering=('permanent.enter','artifact.enter','creature.enter','land.enter','enchantment.enter')
    leaving=('permanent.leave','creature.dies','artifact.graveyard','permanent.graveyard')
    permanent=_q(types_any=('artifact','battle','creature','enchantment','land','planeswalker'))
    creature=_q(types_any=('creature',))
    if name=='permanent_enters':return TriggerParticipationRule(entering,(permanent,))
    if name=='land_enters':return TriggerParticipationRule(entering,(permanent,),(_q(types_any=('land',)),))
    if name=='entering_subtype':return TriggerParticipationRule(entering,(permanent,),(_q(types_any=('creature',),subtypes_any=(subtype,)),),event_relation='you')
    if name=='controlled_entering_land_or_subtype':return TriggerParticipationRule(entering,(permanent,),(_q(types_any=('land',)),_q(types_any=('creature',),subtypes_any=(subtype,))),event_relation='you')
    if name=='legendary_or_artifact_enters_leaves':return TriggerParticipationRule((*entering,*leaving),(permanent,),(_q(supertypes_all=('legendary',)),_q(types_any=('artifact',))))
    if name=='creature_dies':return TriggerParticipationRule(leaving,(permanent,),(creature,))
    if name=='creature_attacks':return TriggerParticipationRule(('creature.attacks',),(permanent,))
    if name=='controlled_creature_attacks':return TriggerParticipationRule(('creature.attacks',),(permanent,),event_relation='you')
    if name=='controlled_combat_damage_player':return TriggerParticipationRule(('damage.dealt',),(permanent,),event_relation='you')
    if name=='cast_or_copy_instant_sorcery':return TriggerParticipationRule(('spell.cast','spell.cast_or_copy'),(permanent,),(_q(types_any=('instant','sorcery')),),event_relation='you')
    if name=='source_subtype':return TriggerParticipationRule(source_queries=(_q(subtypes_any=(subtype,)),))
    if name=='another_source_subtype':return TriggerParticipationRule(source_queries=(_q(subtypes_any=(subtype,)),),exclusion='modifier')
    if name=='ninja_creature_source':return TriggerParticipationRule(source_queries=(_q(types_all=('creature',),subtypes_any=('ninja',)),))
    if name=='legendary_creature_source':return TriggerParticipationRule(source_queries=(_q(types_all=('creature',),supertypes_all=('legendary',)),))
    if name=='shaman_or_another_wizard':return TriggerParticipationRule(source_queries=(_q(subtypes_any=('shaman',)),_q(subtypes_any=('wizard',))),exclusion='wizard_only')
    if name=='colorless_spell_or_another_permanent':return TriggerParticipationRule(source_queries=(_q(colorless=True),),exclusion='permanent_modifier',source_zones=('battlefield','stack'))
    if name=='equipped_source_or_attached_equipment':return TriggerParticipationRule(modifier_condition='equipped',exclusion='attached_source')
    if name=='prevent_creature_enters':return TriggerParticipationRule(entering,event_queries=(creature,),source_relation='any',outcome='prevent')
    if name=='prevent_artifact_creature_enters':return TriggerParticipationRule(entering,event_queries=(_q(types_any=('artifact','creature')),),source_relation='any',outcome='prevent')
    if name=='prevent_creature_enters_dies':return TriggerParticipationRule((*entering,*leaving),event_queries=(creature,),source_relation='any',outcome='prevent')
    if name=='prevent_opponent_permanent_enters':return TriggerParticipationRule(entering,(permanent,),source_relation='opponent',outcome='prevent')
    raise ValueError('Trigger participation rule is not represented')
