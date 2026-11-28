from __future__ import annotations

"""Validate the coupled non-Human anthem and granted Undying source."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, GrantedTriggeredAbilitySpec
from ..death_return import DeathReturnSpec
from ..object_predicate import ObjectQuerySpec


NONHUMAN_UNDYING_ANTHEM_TEMPLATE = "continuous-other-nonhuman-undying-anthem-v1"
NONHUMAN_UNDYING_ANTHEM_CAPABILITY = "continuous.characteristics.other_nonhuman_undying_anthem"
NONHUMAN_UNDYING_ANTHEM_DEPENDENCIES = (
    NONHUMAN_UNDYING_ANTHEM_CAPABILITY,
    "continuous.ability.fixed_keyword_event_grant",
    "continuous.ability.fixed_query_grant",
    "continuous.power_toughness.fixed_anthem",
    "counter.producer.undying",
)


def _uses_nonhuman_undying_anthem(program):
    return (program.provenance.get("template_id") == NONHUMAN_UNDYING_ANTHEM_TEMPLATE
            or NONHUMAN_UNDYING_ANTHEM_CAPABILITY in program.capability_dependencies)


def _nonhuman_undying_anthem_fragment(program):
    if (
        (program.event, program.active_zone) != ("characteristics.evaluate", "battlefield")
        or len(program.handlers) != 2 or program.effects or program.target_schema is not None
        or program.cost_schema is not None or program.event_condition is not None
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("The non-Human anthem requires both printed current-source components")
    from ..semantic_runtime.continuous_components import (
        FixedQueryAbilityGrantHandler, FixedQueryPowerToughnessAnthemHandler,
    )

    grant = FixedQueryAbilityGrantHandler().validate(program.handlers[0])
    anthem = FixedQueryPowerToughnessAnthemHandler().validate(program.handlers[1])
    expected = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), excluded_subtypes=("human",))
    if any(
        component.predicate != expected or component.target_controller != "source_controller"
        or not component.exclude_source for component in (grant, anthem)
    ):
        raise ValueError("Both non-Human effects require the same live controlled set excluding the source")
    if grant.abilities or len(grant.fragments) != 1 or (anthem.power, anthem.toughness) != (1, 1):
        raise ValueError("The non-Human anthem requires +1/+1 and exactly one granted triggered ability")
    fragment, = grant.fragments
    if (
        not isinstance(fragment, GrantedTriggeredAbilitySpec) or fragment.schema_version != 1
        or fragment.event != "permanent.graveyard.self" or fragment.label.casefold() != "undying"
        or not program.oracle_id or fragment.semantic_key != f"{program.oracle_id}:{fragment.ability_id}"
    ):
        raise ValueError("The non-Human anthem requires the original granted Undying trigger")
    return fragment


def nonhuman_undying_anthem_runtime_dependencies(program):
    if not _uses_nonhuman_undying_anthem(program):
        return ()
    _nonhuman_undying_anthem_fragment(program)
    return NONHUMAN_UNDYING_ANTHEM_DEPENDENCIES


def nonhuman_undying_anthem_linkage_blockers(abilities):
    """Require the same source-scoped executable Undying child in the CardProgram."""
    sources = tuple(program for program in abilities if _uses_nonhuman_undying_anthem(program))
    if not sources:
        return ()
    by_key = {program.key: program for program in abilities}
    expected = DeathReturnSpec.for_keyword("undying")
    blockers = []
    for source in sources:
        try:
            fragment = _nonhuman_undying_anthem_fragment(source)
        except (TypeError, ValueError):
            continue
        child = by_key.get(fragment.semantic_key)
        span = source.provenance.get("source_span")
        face = source.provenance.get("face_id")
        if (
            child is None or child.ability_id != fragment.ability_id or child.oracle_id != source.oracle_id
            or child.provenance.get("granted_only") is not True
            or not span or child.provenance.get("source_span") != span
            or not face or child.provenance.get("face_id") != face
            or (child.event, child.active_zone) != ("permanent.graveyard.self", "battlefield")
            or child.target_schema is not None or child.cost_schema is not None or child.handlers
            or child.event_condition != expected.event_condition()
            or tuple(child.effects) != (expected.effect_descriptor(),)
            or expected.capability_id not in child.capability_dependencies
        ):
            blockers.append(f"ability:{source.ability_id}:runtime_nonhuman_undying_anthem:invalid_granted_program")
    return tuple(blockers)
