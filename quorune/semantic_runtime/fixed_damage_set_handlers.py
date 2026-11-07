from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..announced_damage import ANNOUNCED_DAMAGE_EFFECT_FIELD, AllocatedDamageRecipient
from ..fixed_damage_set_model import (
    FixedDamageSetError,
    FixedDamageSetSpec,
    PermanentControllerRelation,
    PermanentDamageGroup,
)
from ..damage_death_exile_model import DAMAGE_DEATH_EXILE_FIELD, damage_death_exile_scope
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import DealFixedDamageSetIntent, IntentPlan


_REASON_FIELD = "".join(("rea", "son"))
_FIELDS = frozenset(
    {
        "op",
        "source",
        "amount",
        "groups",
        ANNOUNCED_DAMAGE_EFFECT_FIELD,
        _REASON_FIELD,
        "_replacement_selections",
        "_replacement_event_ids",
        "source_snapshot",
        DAMAGE_DEATH_EXILE_FIELD,
    }
)


def _sequence(value: Any, *, field: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise SemanticNodeError(f"Fixed damage-set {field} must be an array")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class FixedDamageSetHandler:
    handler_id: str = "generic.damage-fixed-set.v1"
    schema_version: int = 1
    family: str = "effect.damage-fixed-set"
    operation: str = "damage_fixed_set"
    rule_references: tuple[str, ...] = (
        "120.1",
        "120.2",
        "120.4",
        "608.2c",
        "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "damage.batch.fixed_set",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        unknown = sorted(set(effect) - _FIELDS)
        if unknown:
            raise SemanticNodeError(
                "Fixed damage-set effect has unknown fields: "
                + ", ".join(unknown)
            )
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "Fixed damage-set effects require one represented source"
            )
        amount = effect.get("amount")
        if type(amount) is not int or amount < 0:
            raise SemanticNodeError(
                "Fixed damage-set amount must be a nonnegative integer"
            )
        spec = None
        allocated_recipients = None
        if ANNOUNCED_DAMAGE_EFFECT_FIELD in effect:
            recipients = effect[ANNOUNCED_DAMAGE_EFFECT_FIELD]
            if "groups" in effect or not isinstance(recipients, (list, tuple)):
                raise SemanticNodeError("Announced damage requires bound allocated recipients")
            try:
                allocated_recipients = tuple(AllocatedDamageRecipient.from_dict(row) for row in recipients)
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
        else:
            try:
                spec = FixedDamageSetSpec.from_dict({"groups": effect.get("groups")})
            except FixedDamageSetError as exc:
                raise SemanticNodeError(str(exc)) from exc
        for group in spec.groups if spec is not None else ():
            if (
                isinstance(group, PermanentDamageGroup)
                and group.controller_relation
                is PermanentControllerRelation.TARGET_PLAYER
            ):
                context.query.require_active_seat(
                    str(group.target_controller or "")
                )
                if group.target_controller == context.actor:
                    raise SemanticNodeError(
                        "Target-opponent damage cannot select the effect controller"
                    )
        raw_reason = effect.get(_REASON_FIELD)
        if raw_reason is not None and (
            type(raw_reason) is not str or not raw_reason
        ):
            raise SemanticNodeError(
                "Fixed damage-set reason must be a nonempty string"
            )
        selections = _sequence(
            effect.get("_replacement_selections"),
            field="replacement selections",
        )
        event_ids = _sequence(
            effect.get("_replacement_event_ids"),
            field="replacement event identities",
        )
        try:
            intent = DealFixedDamageSetIntent(
                actor=context.actor,
                source_ref=source_ref,
                amount=amount,
                spec=spec,
                reason=raw_reason or context.default_reason,
                replacement_selections=selections,
                replacement_event_ids=event_ids,
                source_snapshot=effect.get('source_snapshot'),
                death_exile_scope=damage_death_exile_scope(effect),
                allocated_recipients=allocated_recipients,
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


FIXED_DAMAGE_SET_HANDLERS = (FixedDamageSetHandler(),)


__all__ = ["FIXED_DAMAGE_SET_HANDLERS", "FixedDamageSetHandler"]
