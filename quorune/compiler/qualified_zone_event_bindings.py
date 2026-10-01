from __future__ import annotations

"""Closed public characteristic predicates over normalized zone occurrences."""

import re
from typing import Any, Mapping

from ..rules.source_references import SourceReferenceSpec
from ..target_numeric import TargetNumericCharacteristic, TargetNumericComparison
from .direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec
from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec


QUALIFIED_ZONE_CAPABILITY = "trigger.event.qualified_zone_change"
QUALIFIED_ZONE_VARIANT = "qualified_public_zone_query"
_ZONE_EVENT = re.compile(
    r"^(?:When|Whenever) (?P<subject>.+?) "
    r"(?P<verb>enters|dies|leaves the battlefield|"
    r"is put into (?:a|your) graveyard from the battlefield), (?P<body>.+)$",
    re.IGNORECASE,
)


def _all(conditions: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return conditions[0] if len(conditions) == 1 else {"all": conditions} if conditions else None


def _field(field: str, op: str, value: Any) -> Mapping[str, Any]:
    return {"field": field, "op": op, "value": value}


def _subject_query(target: str) -> tuple[DirectPermanentTargetSpec, bool | None] | None:
    """Normalize closed Oracle subject order into the shared query grammar."""

    token = re.fullmatch(r"(?P<head>(?:another )?target) (?P<token>nontoken|token) (?P<body>.+)", target, re.IGNORECASE)
    if token is not None:
        target = token.group("head") + " " + token.group("body")
    spec = direct_permanent_target_spec(target)
    if spec is None:
        qualified = re.fullmatch(r"(?P<head>.+?) (?P<relation>you control|you don't control|an opponent controls)(?P<tail> with .+| without .+)", target, re.IGNORECASE)
        if qualified is not None:
            spec = direct_permanent_target_spec(qualified.group("head") + qualified.group("tail") + " " + qualified.group("relation"))
    if spec is not None and token is not None:
        if spec.token is not None:
            return None
    # Nontoken is already a closed normalized-event fact, but is intentionally
    # outside the narrower direct-target schema. Do not widen targeting here.
    return (spec, token.group("token").casefold() == "token" if token is not None else None) if spec is not None else None


def _subject_condition(spec: DirectPermanentTargetSpec) -> Mapping[str, Any] | None:
    conditions: list[Mapping[str, Any]] = []
    for prefix in ("types", "subtypes", "supertypes", "colors", "keywords"):
        any_values = getattr(spec, prefix + "_any", ())
        all_values = getattr(spec, prefix + "_all", ())
        none_values = getattr(spec, prefix + "_none", ())
        if any_values:
            conditions.append(_field(prefix, "contains_any", list(any_values)))
        conditions.extend(_field(prefix, "contains_any", [value]) for value in all_values)
        if none_values:
            conditions.append({"not": _field(prefix, "contains_any", list(none_values))})
    if spec.colorless is not None:
        empty = _field("colors", "eq", [])
        conditions.append(empty if spec.colorless else {"not": empty})
    if spec.token is not None:
        conditions.append(_field("token", "eq", spec.token))
    if spec.controller_relation != "any":
        conditions.append(_field("controller", "eq" if spec.controller_relation == "you" else "ne", "$source.controller"))
    if spec.source_exclusion:
        conditions.append(_field("card", "ne", "$source.ref"))
    for value, op in ((spec.mana_value_min, "gte"), (spec.mana_value_max, "lte"), (spec.mana_value_equal, "eq")):
        if value is not None:
            conditions.append(_field("mana_value", op, value))
    numeric = spec.numeric_characteristic
    if numeric is not None:
        fields = ("power", "toughness") if numeric.characteristic is TargetNumericCharacteristic.POWER_OR_TOUGHNESS else (numeric.characteristic.value,)
        comparisons = [_field(field, "gte" if numeric.comparison is TargetNumericComparison.AT_LEAST else "lte", numeric.value) for field in fields]
        conditions.append({"any": comparisons} if len(comparisons) > 1 else comparisons[0])
    return _all(conditions)


def qualified_public_zone_event_binding_spec(
    material_line: str, *, card_name: str | None = None,
) -> FixedPublicEventBindingSpec | None:
    """Compile only single-object public entry/departure subscriptions.

    This is not targeting: the shared characteristic parser supplies predicates,
    while the existing event-condition owner evaluates sealed occurrence facts.
    Existing productions retain precedence and serialized descriptors.
    """

    match = _ZONE_EVENT.fullmatch(material_line)
    if match is None:
        return None
    subject = match.group("subject")
    source_union = False
    union = re.fullmatch(r"(?P<self>.+?) or another (?P<other>.+)", subject, re.IGNORECASE)
    if union is not None:
        self_subject = union.group("self")
        if not (re.fullmatch(r"this (?:artifact|creature|enchantment|land|permanent)", self_subject, re.IGNORECASE)
                or (card_name and SourceReferenceSpec(card_name).matches(self_subject))):
            return None
        subject = "another " + union.group("other")
        source_union = True
    article = re.fullmatch(r"(?P<article>a|an|another) (?P<query>.+)", subject, re.IGNORECASE)
    if article is None:
        return None
    target = ("another target " if article.group("article").casefold() == "another" else "target ") + article.group("query")
    query = _subject_query(target)
    if query is None:
        return None
    spec, token = query
    if any((spec.state_predicate, spec.commander, spec.combat_state,
                           spec.damage_history, spec.color_count_min, spec.color_count_equal)):
        return None
    if spec.numeric_characteristic and spec.numeric_characteristic.characteristic is TargetNumericCharacteristic.TOTAL_POWER_AND_TOUGHNESS:
        return None
    condition = _subject_condition(spec)
    if token is not None:
        condition = _all([value for value in (condition, _field("token", "eq", token)) if value is not None])
    if source_union:
        condition = {"any": [_field("card", "eq", "$source.ref"), condition]}
    verb = match.group("verb").casefold()
    if "your graveyard" in verb:
        condition = _all([value for value in (condition, _field("owner", "eq", "$source.controller")) if value is not None])
    # Retain the established subtype-only dies domain. Explicit noncreature
    # permanent types (for example creature-or-planeswalker) use graveyard facts.
    creature_only = "creature" in spec.types_all or spec.types_any == ("creature",) or (bool(spec.subtypes_any) and not (spec.types_any or spec.types_all))
    event = ("permanent.enter" if verb == "enters" else "permanent.leave" if verb == "leaves the battlefield"
             else "creature.dies" if verb == "dies" and creature_only else "permanent.graveyard")
    return FixedPublicEventBindingSpec(
        event=event, variant=QUALIFIED_ZONE_VARIANT, body=match.group("body"),
        template_id="fixed-counter-public-zone-trigger-v1",
        mechanic="trigger-event-normalized-zone-change", condition=condition,
        capabilities=(QUALIFIED_ZONE_CAPABILITY,),
    )


def public_binding_from_spec(spec, *, binding_type, event_type):
    """Adapt the one public subscription value to the existing trigger model."""

    if spec is None:
        return None
    return binding_type(
        event=event_type(spec.event), variant=spec.variant, body=spec.body,
        public_condition=spec.condition, public_active_zone=spec.active_zone,
        public_mechanic=spec.mechanic, public_template_id=spec.template_id,
        public_capabilities=spec.capabilities,
    )
