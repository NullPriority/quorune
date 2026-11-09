from __future__ import annotations

"""Current public conditions for source-pinned untap participation."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import partial

from ..card_programs.runtime import fixed_public_state_condition_matches
from ..characteristic_fragments import CharacteristicQuantityScope
from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
from ..dynamic_characteristics import query_characteristic_count
from ..untap_step import (
    CONDITIONAL_UNTAP_CAPABILITY, CONDITIONAL_UNTAP_HANDLER_ID, UntapInstruction,
    UntapSubjectRelation, UntapTurnRelation,
)
from .context import SemanticNodeError
from .untap_steps import StaticUntapStepParticipationHandler


def _public_condition(descriptor):
    payload = descriptor.get("public_condition")
    if not isinstance(payload, Mapping) or set(payload) != {"scope", "condition"}:
        raise SemanticNodeError("Conditional untap requires a closed public condition")
    scope = payload["scope"]
    try:
        condition = FixedPublicStateConditionSpec.from_dict(payload["condition"])
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    if scope == "subject_controller_not_monarch":
        if condition.kind is not FixedPublicStateConditionKind.CONTROLLER_IS_MONARCH:
            raise SemanticNodeError("Attached untap exception requires the subject controller's monarchy")
    else:
        quantity = condition.quantity
        if (scope not in ("source_controller", "any_opponent")
                or condition.kind is not FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST
                or not condition.amount or quantity is None or quantity.schema_version != 1
                or quantity.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
                or quantity.query.zones != ("battlefield",) or quantity.exclude_attached_object
                or scope == "any_opponent" and quantity.exclude_source):
            raise SemanticNodeError("Conditional untap requires a fixed battlefield count for one seat")
    return scope, condition


def conditional_untap_applies(host, source, descriptor):
    if descriptor.get("handler_id") != CONDITIONAL_UNTAP_HANDLER_ID:
        return None
    scope, condition = _public_condition(descriptor)
    if scope == "subject_controller_not_monarch":
        subject = host.state.cards.get(source.attached_to)
        if subject is None or subject.zone != "battlefield" or subject.phased_out:
            return False
        return not fixed_public_state_condition_matches(
            host.state, subject, condition, public_object_resolver=None, quantity_resolver=None,
        )
    anchors = (source,) if scope == "source_controller" else (
        replace(source, controller=seat) for seat in host.active_seats if seat != source.controller
    )
    count = partial(query_characteristic_count, host, _maximum_layer=None)
    return any(fixed_public_state_condition_matches(
        host.state, anchor, condition, public_object_resolver=None,
        quantity_resolver=count, require_available=True,
    ) for anchor in anchors)


@dataclass(frozen=True, slots=True)
class ConditionalUntapStepParticipationHandler(StaticUntapStepParticipationHandler):
    handler_id: str = CONDITIONAL_UNTAP_HANDLER_ID
    capability_dependencies: tuple[str, ...] = (CONDITIONAL_UNTAP_CAPABILITY,)
    rule_references: tuple[str, ...] = ("109.5", "502.3", "611.3a")

    def validate(self, descriptor):
        if type(descriptor.get("schema_version")) is not int:
            raise SemanticNodeError("Conditional untap requires its exact schema version")
        scope, _ = _public_condition(descriptor)
        node = super(ConditionalUntapStepParticipationHandler, self).validate(
            {key: value for key, value in descriptor.items() if key != "public_condition"}
        )
        if (node.instruction is not UntapInstruction.PROHIBIT
                or node.turn_relation is not UntapTurnRelation.SUBJECT_CONTROLLER or node.source_state != "any"
                or node.subject_relation not in (UntapSubjectRelation.SOURCE, UntapSubjectRelation.ATTACHED_OBJECT)
                or scope == "subject_controller_not_monarch"
                    and node.subject_relation is not UntapSubjectRelation.ATTACHED_OBJECT):
            raise SemanticNodeError("Conditional untap must prohibit its current source or attachment during that object's step")
        return node

    def lower(self, descriptor, context):
        if context.public_condition_applies is None:
            raise SemanticNodeError("Conditional untap requires an authoritative current condition result")
        if not context.public_condition_applies:
            self.validate(descriptor)
            return ()
        return super(ConditionalUntapStepParticipationHandler, self).lower(descriptor, context)
