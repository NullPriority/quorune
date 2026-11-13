"""Compile a fixed Awaken alternative and its separately bound land result."""
import re
from collections.abc import Mapping
from ..awaken_model import FixedManaAwakenSpec,AWAKEN_PAID_MECHANIC
from ..rules.awaken_shapes import awaken_land_target_schema,awaken_result_effects
from ..resolution_characteristic_model import INDEFINITE_TARGET_LAND_ANIMATION_MECHANIC
from ..spell_text_variants import SpellTextVariantKind,spell_body_fingerprint
from ..target_clause_binding import scope_clause_effect,clause_target_roles_are_exact
from ..util import mana_cost_to_vector
from .program_composition import _composed_target_schema
from .paired_spell_faces import paired_spell_face
from .ir_model import OracleNode,append_residual


AWAKEN_COST_PATTERN=re.compile(r'Awaken (?P<count>[1-9][0-9]*)[—-](?P<cost>(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+)\.?',re.I)


def residual_awaken_keyword_node(*,node_id,line,span,residuals,**unused):
    rid=append_residual(residuals,kind='keyword_grammar',text=line,span=span,
        reason='Awaken requires a fixed price, complete ordinary body and correlated paid land result',
        blockers=('unsupported price, ordinary body, source form or added target composition',))
    return OracleNode(node_id=node_id,kind='keyword_ability',text=line,span=span,active_zone='all',event='unresolved',
        lowerable=False,exact=False,residual_ids=(rid,))


def compile_fixed_awaken_face(*,record,face_id,face_name,oracle_text,material_rows,effect_template,
        trusted_mechanics,capability_registry,capability_profile,residuals):
    if record.layout!='normal' or record.faces or len(material_rows)<2:return None
    costs=[row for row in material_rows if AWAKEN_COST_PATTERN.fullmatch(row[1])]
    if len(costs)!=1 or re.search(r'\{[XYZ]\}',record.mana_cost or '',re.I):return None
    match=AWAKEN_COST_PATTERN.fullmatch(costs[0][1]);price,complex_symbols=mana_cost_to_vector(match['cost'].upper())
    if complex_symbols:return None
    body=' '.join(row[1] for row in material_rows if row is not costs[0])
    ordinary=effect_template(body,card_name=face_name or record.name)
    template,effects,schema,mechanics=ordinary
    if not template or not effects or not mechanics or schema is not None and 'modes' in schema:return None
    land=awaken_land_target_schema();paid_schema=_composed_target_schema([schema,land]) if schema is not None else land
    if paid_schema is None:return None
    paid=tuple(scope_clause_effect(effect,0) for effect in effects) if schema is not None else tuple(dict(effect) for effect in effects)
    if schema is not None and not all(clause_target_roles_are_exact(effect,0) for effect in paid):return None
    count=int(match['count']);paid+=awaken_result_effects(count,'$target_group.clause_1.0' if schema is not None else '$target.0')
    spec=FixedManaAwakenSpec(f'{face_id}:awaken:line:{costs[0][2].line}',price,count,schema is not None,
        spell_body_fingerprint(effects,schema),spell_body_fingerprint(paid,paid_schema),tuple(dict.fromkeys(mechanics)))
    added=tuple(dict.fromkeys((*mechanics,'cr-122-counters','cr-611-continuous-effects','cr-115-targets',
                              INDEFINITE_TARGET_LAND_ANIMATION_MECHANIC,AWAKEN_PAID_MECHANIC)))
    variants=[ordinary,(template+'+fixed-awaken-result-v1',paid,paid_schema,added)]
    return paired_spell_face(kind=SpellTextVariantKind.AWAKEN,face_id=face_id,face_name=face_name,oracle_text=oracle_text,
        material_rows=material_rows,variants=variants,spec=spec,trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)


def awaken_resolution_body(record,node):
    """Audit the printed keyword independently of the producer's derived text."""
    from .oracle_source_text import without_parenthetical_reminder
    from ..rules.awaken_shapes import ordinary_awaken_body
    kind=SpellTextVariantKind.AWAKEN;roles=set(node.runtime_coverage)&{kind.normal_role,kind.paid_role}
    if (kind.boundary not in node.runtime_coverage or roles not in ({kind.normal_role},{kind.paid_role})
            or not isinstance(node.cost,Mapping) or set(node.cost)!={'awaken'}):
        raise ValueError('Awaken audit requires one complete declared source role')
    paid=roles=={kind.paid_role}
    if node.kind!=('spell_variant' if paid else 'spell_ability'):
        raise ValueError('Awaken audit lost the ordinary or paid node role')
    rows=[(index,without_parenthetical_reminder(line).strip()) for index,line in enumerate(record.oracle_text.splitlines(),1)]
    rows=[(index,line) for index,line in rows if line]
    costs=[(index,AWAKEN_COST_PATTERN.fullmatch(line)) for index,line in rows if AWAKEN_COST_PATTERN.fullmatch(line)]
    if len(costs)!=1:raise ValueError('Awaken audit requires one printed fixed price and count')
    line,match=costs[0];spec=FixedManaAwakenSpec.from_dict(node.cost['awaken'])
    price,complex_symbols=mana_cost_to_vector(match['cost'].upper())
    body=' '.join(text for index,text in rows if index!=line)
    if (complex_symbols or price!=dict(spec.requirements) or spec.count!=int(match['count'])
            or spec.instance_id!=f'front:awaken:line:{line}'
            or spec.normal_targeted!=bool(re.search(r'\btarget\b',body,re.I))):
        raise ValueError('Awaken audit differs from its printed source')
    expected=spec.awakened_fingerprint if paid else spec.normal_fingerprint
    if spell_body_fingerprint(node.effects,node.target_schema)!=expected:
        raise ValueError('Awaken audit lost its body correlation')
    if paid:
        ordinary_awaken_body(spec,node.effects,node.target_schema)
        body+=f" Put {spec.count} +1/+1 counters on target land you control. That land becomes a 0/0 Elemental creature with haste. It's still a land."
    return body
