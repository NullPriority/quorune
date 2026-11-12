from __future__ import annotations

"""Bind fixed current public conditions to ordinary untap-step prohibitions."""

import re

from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
from ..object_predicate import ObjectQuerySpec
from ..rules.source_references import SourceReferenceSpec
from ..untap_step import CONDITIONAL_UNTAP_CAPABILITY, CONDITIONAL_UNTAP_HANDLER_ID
from .public_state_queries import fixed_public_state_condition
from .untap_step_templates import static_untap_step_handler


_PREFIX = re.compile(r"As long as (?P<test>[^,]+), (?P<body>.+)", re.I)
_SUFFIX = re.compile(r"(?P<body>.+ untap step) (?P<join>if|unless) (?P<test>.+)", re.I)
_SOURCE = re.compile(
    r"(?P<source>.+) doesn['’]t untap during (?:your|its controller['’]s) untap step", re.I,
)


def _controller_count_condition(text: str, *, source_name: str) -> FixedPublicStateConditionSpec | None:
    if text.casefold() == "you control another snow permanent":
        return FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST, amount=1, schema_version=2,
            quantity=CharacteristicQuantitySpec(
                scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                query=ObjectQuerySpec(zones=("battlefield",), supertypes_all=("snow",)),
                exclude_source=True,
            ),
        )
    condition = fixed_public_state_condition(text, source_name=source_name)
    if (condition is None or condition.kind is not FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST
            or condition.quantity is None or condition.quantity.schema_version != 1
            or condition.quantity.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
            or condition.quantity.query.zones != ("battlefield",)
            or condition.quantity.exclude_attached_object):
        return None
    return condition


def conditional_untap_step_handler(text: str, *, source_name: str):
    material = " ".join(text.strip().removesuffix(".").split())
    match = _PREFIX.fullmatch(material) or _SUFFIX.fullmatch(material)
    if match is None:
        return None
    body, test = match["body"], match["test"]
    source_match = _SOURCE.fullmatch(body)
    if source_match is not None and SourceReferenceSpec(source_name).matches(source_match["source"]):
        body = "This permanent doesn't untap during your untap step"
    base = static_untap_step_handler(body, source_name=source_name)
    if base is None:
        return None
    descriptor = base[1]
    relation = descriptor["subject"]["relation"]
    if descriptor["instruction"]["kind"] != "prohibition" or relation not in {"source", "attached_object"}:
        return None

    scope = "source_controller"
    if match.groupdict().get("join", "if").casefold() == "unless":
        if relation != "attached_object" or test.casefold() != "that player is the monarch":
            return None
        scope = "subject_controller_not_monarch"
        condition = FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_IS_MONARCH)
    else:
        opponent = re.fullmatch(r"an opponent controls (?P<subject>.+)", test, re.I)
        if opponent is not None:
            scope = "any_opponent"
            test = "you control " + opponent["subject"]
        condition = _controller_count_condition(test, source_name=source_name)
        if condition is None or scope == "any_opponent" and condition.quantity.exclude_source:
            return None
    required = (CONDITIONAL_UNTAP_CAPABILITY,)
    if scope == "subject_controller_not_monarch":
        required += ("variant.monarch.designate",)
    return (
        "untap-step-conditional-prohibition-v1",
        {
            **descriptor, "handler_id": CONDITIONAL_UNTAP_HANDLER_ID,
            "public_condition": {"scope": scope, "condition": condition.to_dict()},
        },
        required,
    )
