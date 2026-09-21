from __future__ import annotations

"""Shared current layer-6 applicability for compiler-authored abilities."""

from collections.abc import Mapping
from typing import Any

from ..ability_fragments import (
    StaticComponentSpec,
    canonical_ability_fragments,
    static_component_keys,
)
from .ability_fragments import fragments_from_descriptors


def program_has_current_ability_fragments(
    program: Any,
    characteristics: Mapping[str, Any],
) -> bool:
    """Require every typed component declared by one current ability."""

    required = (
        StaticComponentSpec(program.key),
        *fragments_from_descriptors(program.handlers),
    )
    available = list(
        canonical_ability_fragments(
            characteristics.get("ability_fragments", ())
        )
    )
    if program.key not in static_component_keys(available):
        return False
    for fragment in required:
        try:
            available.remove(fragment)
        except ValueError:
            return False
    return True


__all__ = ["program_has_current_ability_fragments"]
