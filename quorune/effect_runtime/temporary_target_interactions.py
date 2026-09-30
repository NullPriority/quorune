from __future__ import annotations

"""Current public predicates and typed fragments for temporary target effects."""

from typing import Any, Mapping

from ..ability_fragments import ProtectionSpec, ability_fragment_from_dict
from ..continuous_effects import ContinuousOperation
from ..errors import GameRuleError
from ..object_query import object_query_result
from ..rules.temporary_target_interactions import TargetConditionSpec


def temporary_keyword_operations(
    host: Any, effect: Mapping[str, Any], keyword: str
) -> tuple[ContinuousOperation, ...]:
    operations = (ContinuousOperation("add_ability", keyword),)
    raw = effect.get("ability_fragment")
    if raw is None:
        return operations
    if keyword != "Protection" or not isinstance(raw, Mapping):
        raise GameRuleError("Temporary protection grant is malformed")
    try:
        fragment = ability_fragment_from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if not isinstance(fragment, ProtectionSpec):
        raise GameRuleError("Temporary protection requires a typed fragment")
    if host.state.continuous_effects is None:
        raise GameRuleError("Historical checkpoints cannot acquire temporary protection")
    return (*operations, ContinuousOperation("add_ability_fragment", dict(raw)))


def temporary_target_condition_matches(
    host: Any, effect: Mapping[str, Any], card: Any
) -> bool:
    raw = effect.get("target_condition")
    if raw is None:
        return True
    if not isinstance(raw, Mapping):
        raise GameRuleError("Temporary target condition is malformed")
    try:
        condition = TargetConditionSpec.from_dict(raw)
        effective = host._effective_card_data(card)
        parts = host._type_parts(str(effective.get("type_line") or ""))
        attached = host.state.cards.get(card.attached_to or "")
        row = object_query_result(
            card, effective, type_parts=parts, known_to_actor=True,
            attached_to_ref=(attached.ref if attached is not None else None),
            enchanted=any(
                candidate.attached_to == card.object_id
                and not candidate.phased_out
                and "aura" in host._type_parts(
                    str(host._effective_card_data(candidate).get("type_line") or "")
                )[1]
                for candidate in host.state.cards.values()
                if candidate.zone == "battlefield"
            ),
        )
        return condition.matches(row)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc


__all__ = ["temporary_keyword_operations", "temporary_target_condition_matches"]
