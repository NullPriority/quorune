from __future__ import annotations

"""Compiler compatibility facade for the shared cast-lifecycle model."""

from ..cast_lifecycles import (
    compile_fixed_cast_lifecycle,
    FixedCastLifecycleKind,
    FixedCastLifecycleSpec,
)


def fixed_cast_lifecycle_spec(text: str) -> FixedCastLifecycleSpec | None:
    """Parse a probe-only lifecycle without inventing a runtime source span."""

    return compile_fixed_cast_lifecycle(
        material_line=text,
        oracle_line=text,
        line_index=0,
    )


__all__ = [
    "FixedCastLifecycleKind",
    "FixedCastLifecycleSpec",
    "fixed_cast_lifecycle_spec",
]
