from __future__ import annotations

"""Dispatch keyword grammars that intrinsically emit multiple IR nodes."""

from typing import Any
import re

from .commander_pairing_nodes import partner_with_keyword_nodes
from .madness_nodes import madness_keyword_nodes
from .prototype_nodes import prototype_keyword_node
from .enlist_nodes import enlist_keyword_node,enlist_reflexive_node
from .cycling_nodes import typecycling_keyword_node
from ..cycling_abilities import compile_typecycling_ability
from .ir_model import SourceSpan


def multi_keyword_nodes(**values: Any):
    compile_sibling = values.pop('compile_single_keyword', None)
    if 'champion' in values['mechanics']:
        from .champion_nodes import champion_keyword_nodes
        champion = champion_keyword_nodes(**{key: values[key] for key in (
            'record', 'face_id', 'node_id', 'line', 'material_line', 'span',
            'capability_registry', 'capability_profile', 'residuals')})
        return champion if champion is not None else ()
    from .soulbond_nodes import soulbond_node_group
    bonded = soulbond_node_group(compile_sibling=compile_sibling, **values)
    if bonded is not None:return bonded
    from .paid_entry_copy_nodes import paid_entry_copy_keyword_nodes
    paid_copy = paid_entry_copy_keyword_nodes(**values)
    if paid_copy is not None:return paid_copy
    if len(values['mechanics']) > 1 and set(values['mechanics']) == {'cycling'}:
        parts = tuple(re.finditer(r'[^,]+', values['material_line']))
        if len(parts) != len(values['mechanics']):
            return None
        nodes = []
        for index, part in enumerate(parts):
            text = part.group().strip()
            start = part.start() + len(part.group()) - len(part.group().lstrip())
            end = part.end() - len(part.group()) + len(part.group().rstrip())
            spec = compile_typecycling_ability(material_line=text, oracle_line=text,
                                               line_index=values['span'].line - 1)
            if spec is None:
                return ()
            node = typecycling_keyword_node(
                node_id=values['node_id'] + f':typecycling-instance:{index + 1}',
                line=text, material_line=text,
                span=SourceSpan(values['span'].start + start, values['span'].start + end,
                                values['span'].line),
                mechanics=('cycling',), capability_registry=values['capability_registry'],
                capability_profile=values['capability_profile'], residuals=values['residuals'],
            )
            if node is None:
                return ()
            nodes.append(node)
        return tuple(nodes)
    if values['mechanics'] and set(values['mechanics'])=={'enlist'} and len(values['mechanics'])>1:
        if any(part.strip().casefold()!='enlist' for part in values['material_line'].rstrip('.').split(',')):return None
        return tuple(node for i in range(len(values['mechanics'])) for node in multi_keyword_nodes(**{**values,'node_id':values['node_id']+f':enlist-instance:{i+1}','material_line':'Enlist','mechanics':('enlist',)}))
    enlist=enlist_keyword_node(**values)
    if enlist is not None:return (enlist,enlist_reflexive_node(**values))
    prototype=prototype_keyword_node(**values)
    if prototype is not None:return (prototype,)
    for compiler in (partner_with_keyword_nodes, madness_keyword_nodes):
        nodes = compiler(**values)
        if nodes is not None:
            return nodes
    return None


__all__ = ["multi_keyword_nodes"]
