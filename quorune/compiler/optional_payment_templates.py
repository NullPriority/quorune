from __future__ import annotations

"""Closed compiler owner for one fixed-mana optional effect payment."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import re
from typing import Any, Callable, Mapping

from ..util import mana_cost_to_vector, stable_json


FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC = "fixed-optional-mana-payment"
FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY = (
    "effect.choice.optional_fixed_mana_payment"
)
OPTIONAL_MANA_PAYMENT_OPERATION = "offer_optional_mana_payment"

CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
EffectCompiler = Callable[..., CompiledEffectTemplate]

_FIXED_ORDINARY_PAYMENT = re.compile(
    r"you may pay (?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)\. "
    r"if you do, (?P<body>.+)",
    re.IGNORECASE,
)
_OPTIONAL_OPERATIONS = frozenset(
    {"offer_optional_effect", OPTIONAL_MANA_PAYMENT_OPERATION}
)


@dataclass(frozen=True, slots=True)
class FixedOptionalManaPaymentTemplate:
    """One positive ordinary mana payment around one exact atomic effect."""

    component_template_id: str
    _requirements: Mapping[str, int]
    _component_effects: tuple[Mapping[str, Any], ...]
    _target_schema: Mapping[str, Any] | None
    mechanic_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        expected_keys = {"GENERIC", "W", "U", "B", "R", "G", "C"}
        if (
            not self.component_template_id
            or set(self._requirements) != expected_keys
            or any(
                type(amount) is not int or amount < 0
                for amount in self._requirements.values()
            )
            or not any(self._requirements.values())
            or len(self._component_effects) != 1
            or not self.mechanic_ids
            or self.mechanic_ids[0]
            != FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC
            or any(
                effect.get("op") in _OPTIONAL_OPERATIONS
                for effect in self._component_effects
            )
        ):
            raise ValueError("Fixed optional mana payment template is malformed")
        object.__setattr__(
            self,
            "_requirements",
            deepcopy(dict(self._requirements)),
        )
        object.__setattr__(
            self,
            "_component_effects",
            deepcopy(tuple(self._component_effects)),
        )
        object.__setattr__(self, "_target_schema", deepcopy(self._target_schema))

    @property
    def template_id(self) -> str:
        digest = hashlib.sha256(
            stable_json(
                {
                    "component": self.component_template_id,
                    "cost": self._requirements,
                    "effects": self._component_effects,
                    "target_schema": self._target_schema,
                }
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"fixed-optional-mana-payment-{digest}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": OPTIONAL_MANA_PAYMENT_OPERATION,
                "player": "$controller",
                "cost": deepcopy(dict(self._requirements)),
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
        return self.template_id, self.effects, self.target_schema, self.mechanics


def fixed_optional_mana_payment_template(
    text: str,
    *,
    compile_effect: EffectCompiler,
) -> FixedOptionalManaPaymentTemplate | None:
    """Compile only a positive fixed ordinary cost and one exact body."""

    match = _FIXED_ORDINARY_PAYMENT.fullmatch(text.strip())
    if match is None:
        return None
    requirements, complex_symbols = mana_cost_to_vector(match.group("cost"))
    if complex_symbols or not any(requirements.values()):
        return None
    body = match.group("body").strip()
    if body.casefold().startswith(("you may ", "when you do,")):
        return None
    template_id, effects, target_schema, mechanics = compile_effect(body)
    if (
        template_id is None
        or len(effects) != 1
        or not mechanics
        or FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC in mechanics
        or any(effect.get("op") in _OPTIONAL_OPERATIONS for effect in effects)
    ):
        return None
    return FixedOptionalManaPaymentTemplate(
        component_template_id=template_id,
        _requirements=requirements,
        _component_effects=effects,
        _target_schema=target_schema,
        mechanic_ids=tuple(
            dict.fromkeys((FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC, *mechanics))
        ),
    )


def reviewed_fixed_optional_mana_payment_trigger_template(
    text: str,
    *,
    compile_trigger_effect: EffectCompiler,
    compile_effect: EffectCompiler,
    **context: Any,
) -> CompiledEffectTemplate:
    """Add this wrapper only at the whole triggered-effect boundary."""

    compiled = compile_trigger_effect(text, **context)
    if compiled[0] is not None:
        return compiled
    optional_payment = fixed_optional_mana_payment_template(
        text,
        compile_effect=lambda body: compile_effect(body, **context),
    )
    return optional_payment.compiled() if optional_payment is not None else compiled


__all__ = [
    "FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY",
    "FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC",
    "OPTIONAL_MANA_PAYMENT_OPERATION",
    "FixedOptionalManaPaymentTemplate",
    "fixed_optional_mana_payment_template",
    "reviewed_fixed_optional_mana_payment_trigger_template",
]
