from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .direct_target import (
    DirectPermanentTargetSpec,
    compiled_direct_target,
    direct_permanent_target_spec,
    direct_target_effect,
)

_EXILE_MECHANIC = "exile"


@dataclass(frozen=True, slots=True)
class TargetedExileEffectTemplate:
    """Closed lowering for one mandatory direct battlefield exile."""

    target_spec: DirectPermanentTargetSpec

    def __post_init__(self) -> None:
        if not isinstance(self.target_spec, DirectPermanentTargetSpec):
            raise ValueError("Exile target predicate is unsupported")

    @property
    def template_id(self) -> str:
        return f"exile-target-{self.target_spec.slug}-v2"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return direct_target_effect("exile_permanent", reference_field="card")

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return self.target_spec.to_target_schema()

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (_EXILE_MECHANIC, "cr-115-targets")

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any],
        tuple[str, ...],
    ]:
        return compiled_direct_target(
            template_id=self.template_id,
            effects=self.effects,
            target_schema=self.target_schema,
            mechanics=self.mechanics,
        )


def targeted_exile_effect_template(
    text: str,
) -> TargetedExileEffectTemplate | None:
    match = re.fullmatch(
        r"exile (?P<subject>(?:another )?target .+?)\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    target_spec = direct_permanent_target_spec(match.group("subject"))
    return (
        TargetedExileEffectTemplate(target_spec)
        if target_spec is not None
        else None
    )


__all__ = [
    "TargetedExileEffectTemplate",
    "targeted_exile_effect_template",
]
