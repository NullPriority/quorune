from __future__ import annotations

"""Closed source-spanned Devour keyword productions."""

from . import regex_working_set as re
from ..devour import (DevourSpec, DevourQuality, DEVOUR_HANDLER, DEVOUR_CAPABILITY,
    DevourEntryAmount)
from ..zone_devour import devour_entry_result_node_capabilities
from ..rules.source_references import SourceReferenceSpec
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .dependency_gate import dependency_gate, explicit_capabilities_gate
from .ir_model import OracleNode,SourceSpan,append_residual


def _parts(material):
    if not re.match(r'^devour\b',material,re.I):return None
    result=[]
    for text in re.split(r',\s*(?=devour\b)',material.strip().rstrip('.'),flags=re.I):
        fixed=re.fullmatch(r'Devour(?: (?P<quality>artifact|Food))? (?P<count>\d+)',text,re.I)
        if fixed is not None:spec=DevourSpec(DevourQuality((fixed['quality'] or 'creature').casefold()),int(fixed['count']))
        elif re.fullmatch(r'Devour X, where X is the number of creatures devoured this way',text,re.I):spec=DevourSpec(DevourQuality.CREATURE,'devoured_count')
        else:return None
        result.append((text,spec))
    return result


def devour_keyword_nodes(*,node_id,line,material_line,span,capability_registry,capability_profile,residuals):
    parts=_parts(material_line)
    if parts is None:return None
    gate=explicit_capabilities_gate((DEVOUR_CAPABILITY,'counter.placement.quantity_replacement'),
        capability_registry=capability_registry,capability_profile=capability_profile)
    nodes=[];cursor=0
    for index,(text,spec) in enumerate(parts):
        if len(parts)==1:source,part_span=line,span
        else:
            match=re.search(r'\s+'.join(map(re.escape,text.split())),line[cursor:],re.I)
            if match is None:return None
            start=cursor+match.start();end=cursor+match.end();cursor=end
            source=line[start:end];part_span=SourceSpan(span.start+start,span.start+end,span.line)
        rid=() if not gate.blockers else (append_residual(residuals,kind='dependency_contract',text=source,span=part_span,
            reason='Devour requires its canonical combined-entry and counter capabilities',blockers=gate.blockers),)
        nodes.append(OracleNode(node_id=node_id+('' if len(parts)==1 else f':devour:{index}'),kind='static_ability',text=source,span=part_span,
            active_zone='all',event='zone.change',lowerable=True,exact=not gate.blockers,template_id='devour-entry-v1',
            handlers=({'handler_id':DEVOUR_HANDLER,'schema_version':1,'event':'zone.change','devour':spec.to_dict()},),
            runtime_coverage=('typed_devour_entry',CURRENT_ABILITY_FRAGMENT_COVERAGE),residual_ids=rid,capability_dependencies=gate.capabilities,
            capability_closure=gate.closure.reachable if gate.closure else (),capability_profile=gate.closure.profile if gate.closure else None,
            capability_fingerprint=gate.closure.fingerprint if gate.closure else None))
    return tuple(nodes)


def devour_result_trigger_compiler(record, *, fallback, ordinary_effect, material_rows):
    if record.faces or record.layout != "normal" or not record.is_creature:
        return fallback
    devour_rows = [material for _, material, _ in material_rows if re.match(r"Devour\b", material, re.I)]
    if len(devour_rows) != 1 or re.fullmatch(r"Devour [0-9]+\.?", devour_rows[0], re.I) is None:
        return fallback

    def compile_trigger(**values):
        result = _devour_result_node(ordinary_effect=ordinary_effect, **values)
        return result if result is not None else fallback(**values)

    return compile_trigger


def _devour_result_node(*, node_id, line, span, card_name, trusted_mechanics,
                        capability_registry, capability_profile, residuals, ordinary_effect,
                        material_line=None, **_ignored):
    source = SourceReferenceSpec(card_name).regex_pattern
    match = re.fullmatch(
        rf"When (?:this creature|{source}) enters, (?P<body>.+?) for each creature it devoured\.?",
        material_line or line, re.I,
    )
    if match is None:
        return None
    body = match["body"]
    if re.fullmatch(r"draw a card", body, re.I):
        field, coefficient = "count", 1
    elif re.fullmatch(r"target player discards a card", body, re.I):
        field, coefficient = "count", 1
    elif (life := re.fullmatch(r"you gain ([123]) life", body, re.I)) is not None:
        field, coefficient = "delta", int(life[1])
    else:
        return None

    template, fixed, schema, leaf_mechanics = ordinary_effect(body + ".", card_name=card_name)
    if template is None or len(fixed) != 1 or field not in fixed[0]:
        return None
    effects = ({**fixed[0], field: DevourEntryAmount(coefficient).to_dict()},)
    mechanics = ("cr-603-handling-triggered-abilities", "trigger-event-normalized-zone-change", *leaf_mechanics)
    required = devour_entry_result_node_capabilities(effects=effects, target_schema=schema, mechanic_ids=mechanics)
    gate = dependency_gate(mechanics=mechanics, effects=effects, target_schema=schema,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, explicit_capabilities=required)
    ids = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
        reason="Devour entry results require the committed original sacrifice count and fixed instruction",
        blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(node_id=node_id, kind="triggered_ability", text=line, span=span,
        active_zone="battlefield", event="permanent.enter.self", lowerable=True, exact=not ids,
        template_id="committed-devour-entry-result-v1", effects=effects, target_schema=schema,
        mechanics=mechanics, residual_ids=ids, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)
