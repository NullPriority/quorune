from __future__ import annotations

"""Closed compiler owner for one optional already-typed effect clause."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import re
from typing import Any, Callable, Mapping

from ..util import stable_json


FIXED_OPTIONAL_EFFECT_MECHANIC = "fixed-optional-effect-choice"
FIXED_OPTIONAL_EFFECT_CAPABILITY = "effect.choice.optional_fixed"
OPTIONAL_EFFECT_OPERATION = "offer_optional_effect"

CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
EffectCompiler = Callable[[str], CompiledEffectTemplate]

_OPTIONAL_EFFECT = re.compile(r"you may (?P<body>.+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FixedOptionalEffectTemplate:
    """One resolution-time apply-or-decline choice around one closed clause."""

    component_template_id: str
    _component_effects: tuple[Mapping[str, Any], ...]
    _target_schema: Mapping[str, Any] | None
    mechanic_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.component_template_id
            or len(self._component_effects) != 1
            or not self.mechanic_ids
            or self.mechanic_ids[0] != FIXED_OPTIONAL_EFFECT_MECHANIC
            or any(
                effect.get("op") == OPTIONAL_EFFECT_OPERATION
                for effect in self._component_effects
            )
        ):
            raise ValueError("Fixed optional effect template is malformed")
        object.__setattr__(
            self,
            "_component_effects",
            deepcopy(tuple(self._component_effects)),
        )
        object.__setattr__(
            self,
            "_target_schema",
            deepcopy(self._target_schema),
        )

    @property
    def template_id(self) -> str:
        digest = hashlib.sha256(
            stable_json(
                {
                    "component": self.component_template_id,
                    "effects": self._component_effects,
                    "target_schema": self._target_schema,
                }
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"fixed-optional-effect-{digest}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": OPTIONAL_EFFECT_OPERATION,
                "player": "$controller",
                "effects": deepcopy(list(self._component_effects)),
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        return deepcopy(self._target_schema)

    @property
    def mechanics(self) -> tuple[str, ...]:
        return self.mechanic_ids

    def compiled(self) -> CompiledEffectTemplate:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def fixed_optional_effect_template(
    text: str,
    *,
    compile_effect: EffectCompiler,
) -> FixedOptionalEffectTemplate | None:
    """Wrap one independently exact atomic body without widening its grammar."""

    match = _OPTIONAL_EFFECT.fullmatch(text.strip())
    if match is None:
        return None
    body = match.group("body").strip()
    if body.casefold().startswith("you may "):
        return None
    template_id, effects, target_schema, mechanics = compile_effect(body)
    if (
        template_id is None
        or len(effects) != 1
        or not mechanics
        or FIXED_OPTIONAL_EFFECT_MECHANIC in mechanics
        or any(effect.get("op") == OPTIONAL_EFFECT_OPERATION for effect in effects)
    ):
        return None
    return FixedOptionalEffectTemplate(
        component_template_id=template_id,
        _component_effects=effects,
        _target_schema=target_schema,
        mechanic_ids=tuple(
            dict.fromkeys((FIXED_OPTIONAL_EFFECT_MECHANIC, *mechanics))
        ),
    )


__all__ = [
    "FIXED_OPTIONAL_EFFECT_CAPABILITY",
    "FIXED_OPTIONAL_EFFECT_MECHANIC",
    "OPTIONAL_EFFECT_OPERATION",
    "FixedOptionalEffectTemplate",
    "fixed_optional_effect_template",
]
