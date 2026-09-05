from __future__ import annotations

"""Dispatch keyword grammars that intrinsically emit multiple IR nodes."""

from typing import Any

from .commander_pairing_nodes import partner_with_keyword_nodes
from .madness_nodes import madness_keyword_nodes


def multi_keyword_nodes(**values: Any):
    for compiler in (partner_with_keyword_nodes, madness_keyword_nodes):
        nodes = compiler(**values)
        if nodes is not None:
            return nodes
    return None


__all__ = ["multi_keyword_nodes"]
