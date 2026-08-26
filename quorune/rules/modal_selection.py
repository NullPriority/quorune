from __future__ import annotations

"""Runtime normalization for closed modal choices."""

from typing import Any, Mapping, Sequence


def canonical_modes(
    schema: Mapping[str, Any],
    modes: Sequence[str] = (),
    *,
    require_modes: bool = True,
) -> tuple[str, ...]:
    """Validate one modal selection and return it in printed order."""

    selected_modes = tuple(str(mode) for mode in modes)
    mode_definitions = schema.get("modes")
    if not isinstance(mode_definitions, Mapping):
        return selected_modes
    minimum_modes = int(schema.get("min_modes", schema.get("mode_count", 1)))
    maximum_modes = int(schema.get("max_modes", schema.get("mode_count", 1)))
    if require_modes and not (
        minimum_modes <= len(selected_modes) <= maximum_modes
    ):
        raise ValueError(
            f"Action requires between {minimum_modes} and {maximum_modes} mode(s)"
        )
    if len(set(selected_modes)) != len(selected_modes):
        raise ValueError("The same mode cannot be selected twice")
    mode_order = {str(mode): index for index, mode in enumerate(mode_definitions)}
    if any(mode not in mode_order for mode in selected_modes):
        unknown = next(mode for mode in selected_modes if mode not in mode_order)
        raise ValueError(f"Unknown target mode {unknown!r}")
    return tuple(sorted(selected_modes, key=mode_order.__getitem__))


__all__ = ["canonical_modes"]
