from __future__ import annotations

"""Dispatch keyword grammars that intrinsically emit multiple IR nodes."""

from typing import Any

from .commander_pairing_nodes import partner_with_keyword_nodes
from .madness_nodes import madness_keyword_nodes
from .prototype_nodes import prototype_keyword_node


def multi_keyword_nodes(**values: Any):
    prototype=prototype_keyword_node(**values)
    if prototype is not None:return (prototype,)
    for compiler in (partner_with_keyword_nodes, madness_keyword_nodes):
        nodes = compiler(**values)
        if nodes is not None:
            return nodes
    return None


__all__ = ["multi_keyword_nodes"]
