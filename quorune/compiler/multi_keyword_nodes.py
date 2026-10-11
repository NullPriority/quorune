from __future__ import annotations

"""Dispatch keyword grammars that intrinsically emit multiple IR nodes."""

from typing import Any

from .commander_pairing_nodes import partner_with_keyword_nodes
from .madness_nodes import madness_keyword_nodes
from .prototype_nodes import prototype_keyword_node
from .enlist_nodes import enlist_keyword_node,enlist_reflexive_node


def multi_keyword_nodes(**values: Any):
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
