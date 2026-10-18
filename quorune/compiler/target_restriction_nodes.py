from __future__ import annotations

"""Source-spanned fixed spell/ability targeting prohibitions."""

import re
from ..target_restrictions import TargetRestrictionSpec,TARGET_RESTRICTION_HANDLER,TARGET_RESTRICTION_CAPABILITY
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.source_references import SourceReferenceSpec
from .static_runtime_nodes import runtime_handler_node


def target_restriction_specs(text,*,source_name):
    source=SourceReferenceSpec(source_name).regex_pattern
    match=re.fullmatch(rf'(?P<subject>this creature|{source}|enchanted creature|creatures you control|creatures|cards in graveyards) '
        r"can't be the targets? of (?P<quality>.+?)\.?",text.strip(),re.I)
    if match is None:return None
    subject=match['subject'].casefold();quality=match['quality'].casefold()
    affected={'enchanted creature':'attached_creature','creatures you control':'controlled_creatures',
        'creatures':'creatures','cards in graveyards':'graveyard_cards'}.get(subject,'source')
    if quality=='spells or abilities your opponents control':
        return (TargetRestrictionSpec(affected,('ability','spell'),'acting_opponent'),)
    specs=[]
    for index,clause in enumerate(quality.split(' or abilities ')):
        if index:clause='abilities '+clause
        mode='any'
        if clause.endswith(' your opponents control'):
            clause=clause.removesuffix(' your opponents control')
            mode='source_opponent' if 'from ' in clause else 'acting_opponent'
        if clause in {'spells','abilities','spells or abilities'}:
            kinds=('ability','spell') if clause=='spells or abilities' else ('spell',) if clause=='spells' else ('ability',)
            specs.append(TargetRestrictionSpec(affected,kinds,mode));continue
        spell=re.fullmatch(r'(?P<quality>nongreen|blue or black|black or red|blue|red|aura) spells',clause)
        ability=re.fullmatch(r'abilities from (?P<quality>nongreen|blue|red|artifact) sources',clause)
        parsed=spell or ability
        if parsed is None:return None
        term=parsed['quality'];values={}
        if term=='nongreen':values['colors_none']=('G',)
        elif term=='artifact':values['types_all']=('artifact',)
        elif term=='aura':values['subtypes_all']=('aura',)
        else:values['colors_any']=tuple(sorted({'blue or black':('U','B'),'black or red':('B','R'),'blue':('U',),'red':('R',)}[term]))
        specs.append(TargetRestrictionSpec(affected,('spell',) if spell else ('ability',),mode,**values))
    return tuple(specs)


def target_restriction_node(*,node_id,line,material_line,span,source_name,card_types,permanent_card_types,capability_registry,capability_profile,residuals):
    if not set(card_types).intersection(permanent_card_types):return None
    material=material_line;extra=();capabilities=(TARGET_RESTRICTION_CAPABILITY,)
    match=re.fullmatch(r"Enchanted creature gets (?P<p>[+-][0-9]+)/(?P<t>[+-][0-9]+) and can't be the target of spells\.?",material,re.I)
    if match is not None:
        from .continuous_templates import attached_fixed_characteristics_handler
        compiled=attached_fixed_characteristics_handler(f"Enchanted creature gets {match['p']}/{match['t']}.")
        if compiled is None:return None
        extra=(compiled[1],);capabilities+=tuple(compiled[2])
        material="Enchanted creature can't be the target of spells."
    specs=target_restriction_specs(material,source_name=source_name)
    if specs is None:return None
    descriptor={'handler_id':TARGET_RESTRICTION_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
        'restrictions':[spec.to_dict() for spec in specs]}
    return runtime_handler_node(node_id=node_id,line=line,span=span,
        compiled=('static-fixed-target-prohibition-v1',(*extra,descriptor),capabilities),
        kind='static_ability',event='characteristics.evaluate',runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Target prohibitions require typed acting objects, current/LKI source qualities and shared target revalidation.',
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
