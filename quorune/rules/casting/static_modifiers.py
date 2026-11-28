from __future__ import annotations

"""Read and apply current public casting costs."""

from typing import Any, Sequence

from ...compiled_cast_costs import compiled_self_spell_cost_reduction_specs
from ...cast_cost_modifiers import PublicCastCostModifierV2Spec
from ...semantic_runtime.cast_costs import FixedSpellCostReductionSpec, active_fixed_spell_cost_reductions
from ...self_cast_reductions import evaluated_self_reduction
from ...targeted_spell_costs import validate_targeted_spell_cost_fact
from .colored_hand_credit import HAND_CREDIT_AMOUNT
from .optional_creature_payments import SACRIFICE_CREDIT_AMOUNT


def fixed_generic_reduction(
    host: Any,
    seat: str,
    card: Any,
    *,
    cast_type_line: str | None = None,
    face_down_method: str | None = None,
    _public_modifiers: Sequence[FixedSpellCostReductionSpec] | None = None,
) -> int:
    if _public_modifiers is None:
        _public_modifiers = active_fixed_spell_cost_reductions(
            host, seat, card, cast_type_line=cast_type_line, face_down_method=face_down_method,
        )
    return sum(modifier.generic_reduction for modifier in _public_modifiers)


def apply_fixed_generic_increases(
    host: Any,
    seat: str,
    card: Any,
    option: dict[str, Any],
    *,
    cast_type_line: str | None = None,
    face_down_method: str | None = None,
) -> None:
    modifiers = active_fixed_spell_cost_reductions(
        host, seat, card, cast_type_line=cast_type_line, face_down_method=face_down_method,
    )
    _apply_fixed_generic_increases(option, modifiers)


def _apply_fixed_generic_increases(option, modifiers) -> None:
    increase = sum(max(0, modifier.generic_adjustment) for modifier in modifiers)
    if not increase:
        return
    option["requirements"]["GENERIC"] += increase
    option.setdefault("cost_increases", []).append(
        {
            "kind": "fixed_query",
            "count": increase,
        }
    )


def apply_static_reductions(
    host: Any,
    seat: str,
    card: Any,
    option: dict[str, Any],
    *,
    program: Any,
    fixed_reduction: int | None = None,
    cast_type_line: str | None = None,
    face_down_method: str | None = None,
) -> None:
    public_modifiers = active_fixed_spell_cost_reductions(
        host,
        seat,
        card,
        cast_type_line=cast_type_line,
        face_down_method=face_down_method,
    )
    if fixed_reduction is None:
        from ...harmonize import harmonize_generic_reduction

        fixed_reduction = fixed_generic_reduction(
            host, seat, card, cast_type_line=cast_type_line, face_down_method=face_down_method,
            _public_modifiers=public_modifiers,
        ) + option.get(HAND_CREDIT_AMOUNT, 0) + harmonize_generic_reduction(option)
    fixed_reduction += option.pop(SACRIFICE_CREDIT_AMOUNT, 0)
    _apply_fixed_generic_increases(option, public_modifiers)
    colored_increases: dict[str, int] = {}
    colored_reductions: dict[str, int] = {}
    for modifier in public_modifiers:
        if not isinstance(modifier, PublicCastCostModifierV2Spec):
            continue
        for key, amount in modifier.mana_adjustment:
            if key == "GENERIC":
                continue
            target = colored_increases if amount > 0 else colored_reductions
            target[key] = target.get(key, 0) + abs(amount)
    applied_increases: dict[str, int] = {}
    for key, amount in sorted(colored_increases.items()):
        option["requirements"][key] = int(
            option["requirements"].get(key, 0)
        ) + amount
        applied_increases[key] = amount
    if applied_increases:
        option.setdefault("cost_increases", []).append(
            {"kind": "fixed_query", "increase": applied_increases}
        )
    target_cost=option.get('targeted_cost')
    if target_cost is not None:
        validate_targeted_spell_cost_fact(target_cost)
        reduction=target_cost.get('generic_reduction',0)
        if reduction:
            applied=min(option['requirements']['GENERIC'],reduction)
            option['requirements']['GENERIC']-=applied
            option.setdefault('cost_reductions',[]).append({'kind':'self_target','count':applied})
    if fixed_reduction:
        applied = min(
            int(option["requirements"]["GENERIC"]),
            fixed_reduction,
        )
        option["requirements"]["GENERIC"] -= applied
        option.setdefault("cost_reductions", []).append(
            {"kind": "fixed_query", "count": applied}
        )
    applied_colored: dict[str, int] = {}
    for key, amount in sorted(colored_reductions.items()):
        applied = min(int(option["requirements"].get(key, 0)), amount)
        if not applied:
            continue
        option["requirements"][key] -= applied
        applied_colored[key] = applied
    if applied_colored:
        option.setdefault("cost_reductions", []).append(
            {"kind": "fixed_query", "reduction": applied_colored}
        )
    self_reduction: dict[str, int] = {}
    specifications = () if face_down_method is not None else compiled_self_spell_cost_reduction_specs(
        host,
        card.oracle_id,
        spell_program=program,
    )
    for specification in specifications:
        for key, amount in evaluated_self_reduction(
            host,
            seat,
            specification,
            source=card,
        ).items():
            self_reduction[key] = self_reduction.get(key, 0) + amount
    applied_self: dict[str, int] = {}
    for key in sorted(self_reduction):
        applied = min(
            int(option["requirements"].get(key, 0)),
            self_reduction[key],
        )
        if not applied:
            continue
        option["requirements"][key] -= applied
        applied_self[key] = applied
    if applied_self:
        option.setdefault("cost_reductions", []).append(
            {"kind": "self_public", "reduction": applied_self}
        )
    from .optional_life_costs import apply_optional_life_reduction
    apply_optional_life_reduction(option)
    from .declared_cost_reductions import apply_declared_cost_reductions
    apply_declared_cost_reductions(host,card,option,face_down_method=face_down_method)


__all__ = [
    "apply_fixed_generic_increases",
    "apply_static_reductions",
    "fixed_generic_reduction",
]


def apply_cast_static_reductions(
    host: Any, seat: str, card: Any, option: dict[str, Any], *, program: Any,
    cast_type_line: str | None = None,
) -> None:
    from ...morph import FACE_DOWN_CAST_METHODS

    face_down_method = option.get("id") if option.get("id") in FACE_DOWN_CAST_METHODS else None
    apply_static_reductions(
        host, seat, card, option, program=program,
        cast_type_line=cast_type_line, face_down_method=face_down_method,
    )
