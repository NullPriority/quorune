from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ...additional_cost_vocabulary import ZONE_CHANGE_COST_KIND
from ...casting_payment_keywords import (
    AffinitySpec,
    CastingPaymentKeywordError,
    DelveCandidate,
    DelvePaymentPlan,
)
from ...compiled_cast_costs import (
    compiled_affinity_specs,
    compiled_convoke_specs,
    compiled_delve_specs,
    compiled_evoke_specs,
    compiled_improvise_specs,
)
from ...compiled_flashback import (
    compiled_fixed_mana_flashback_spec,
    compiled_ordinary_zone_cast_permission,
)
from ...compiled_kicker import compiled_fixed_mana_kicker_spec
from ...compiled_bestow import compiled_fixed_mana_bestow_spec
from ...convoke import (
    CONVOKE_PAYMENT_SYMBOLS,
    ConvokeCandidate,
    ConvokeError,
    ConvokePaymentPlan,
    canonical_mana_requirements,
    find_convoke_plan,
    select_convoke_plan,
)
from ...evoke import EVOKE_PAYMENT_FIELD, validate_evoke_payment_marker
from ...object_query import object_matches_query, object_query_result
from ...semantic_runtime.cast_costs import (
    active_fixed_spell_cost_reductions,
)
from ..action_proposals import CastCostOption
from ..casting_additional_costs import (
    AdditionalCostError,
    FixedZoneChangeAdditionalCost,
    fixed_counter_additional_cost,
    fixed_counter_cost_candidates,
    fixed_zone_change_additional_cost,
    fixed_zone_change_cost_candidates,
    legacy_additional_cost_candidates,
)
from ..casting_additional_cost_groups import (
    FixedManaPaymentAdditionalCost,
    fixed_additional_cost_option_label,
    fixed_alternative_additional_cost,
    fixed_life_payment_additional_cost,
)


class CastCostHost(Protocol):
    state: Any
    semantics: Any

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def card_record(self, card: Any) -> Any: ...

    def _temporary_play_permission(
        self, seat: str, card: Any
    ) -> Mapping[str, Any] | None: ...

    def _compiled_printed_cost_options(
        self,
        seat: str,
        card: Any,
        *,
        x_value: int | None,
        hint: bool,
    ) -> tuple[list[dict[str, Any]], bool]: ...

    def _mana_vector(self, value: Any) -> dict[str, int]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _spell_mana_spend_context(self, type_line: str) -> Any: ...

    def _cost_payment_mechanics(
        self, record: Any, schema: Mapping[str, Any]
    ) -> list[dict[str, Any]]: ...

    def _alternate_cost_condition_met(
        self, seat: str, condition: Mapping[str, Any]
    ) -> bool: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _exile_cost_candidates(
        self, seat: str, card: Any, specification: Mapping[str, Any]
    ) -> list[str]: ...

    def _cost_is_affordable(
        self,
        seat: str,
        requirements: Mapping[str, int],
        *,
        exclude_sources: set[str] | None = None,
        spend_context: Any = None,
    ) -> bool: ...

    def _maximum_affordable_x(self, seat: str, card: Any) -> int: ...


def _expand_fixed_alternative_options(
    host: CastCostHost,
    expanded: list[dict[str, Any]],
    mandatory: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Expand one closed binary additional cost into explicit cost options."""

    if len(mandatory) != 1:
        return expanded, mandatory
    try:
        alternative = fixed_alternative_additional_cost(mandatory[0])
    except AdditionalCostError:
        return None
    if alternative is None:
        return expanded, mandatory

    alternatives: list[dict[str, Any]] = []
    for base_option in expanded:
        for index, cost in enumerate(alternative.options, start=1):
            option = copy.deepcopy(base_option)
            option["base_cost_option"] = str(base_option["id"])
            option["id"] = (
                f"{base_option['id']}+additional-alternative-{index}"
            )
            option["kind"] = "additional_alternative"
            option["label"] = fixed_additional_cost_option_label(cost)
            if isinstance(cost, FixedManaPaymentAdditionalCost):
                requirements = host._mana_vector(option["requirements"])
                for symbol, amount in cost.requirements:
                    requirements[symbol] += amount
                option["requirements"] = requirements
                option["_selected_additional_costs"] = []
            else:
                option["_selected_additional_costs"] = [cost.to_descriptor()]
            alternatives.append(option)
    return alternatives, []


def _compiled_payment_mechanics(
    host: CastCostHost,
    seat: str,
    record: Any,
    schema: Mapping[str, Any],
    program: Any,
    *,
    suppress_source_costs: bool = False,
) -> list[dict[str, Any]] | None:
    """Resolve trusted printed payment mechanics at the selected face."""

    if suppress_source_costs:
        mechanics: list[dict[str, Any]] = []
        if host.state.players[seat].stats.get("next_spell_improvise"):
            mechanics.append({"kind": "improvise", "schema_version": 1})
        return mechanics
    mechanics = host._cost_payment_mechanics(record, schema)
    printed_keywords = {str(value).casefold() for value in record.keywords}
    compiled_by_kind: dict[str, tuple[Any, ...]] = {
        "affinity": compiled_affinity_specs(
            host, record.oracle_id, spell_program=program
        ),
        "convoke": compiled_convoke_specs(
            host, record.oracle_id, spell_program=program
        ),
        "delve": compiled_delve_specs(
            host, record.oracle_id, spell_program=program
        ),
        "improvise": compiled_improvise_specs(
            host, record.oracle_id, spell_program=program
        ),
    }
    declared_kinds = {
        str(value.get("kind") or "").casefold() for value in mechanics
    }
    mechanics = [
        value
        for value in mechanics
        if str(value.get("kind") or "").casefold()
        not in compiled_by_kind
    ]
    compiled_affinity = compiled_by_kind["affinity"]
    mechanics.extend(
        specification.to_payment_mechanic()
        for specification in compiled_affinity
    )
    compiled_convoke = compiled_by_kind["convoke"]
    if compiled_convoke:
        mechanics.append(
            {
                "kind": "convoke",
                "schema_version": compiled_convoke[0].schema_version,
            }
        )
    compiled_delve = compiled_by_kind["delve"]
    if compiled_delve:
        mechanics.append(compiled_delve[0].to_dict())
    compiled_improvise = compiled_by_kind["improvise"]
    if compiled_improvise:
        mechanics.append(compiled_improvise[0].to_dict())
    for kind in compiled_by_kind:
        declared = kind in declared_kinds
        printed = kind in printed_keywords
        if (declared or printed) and not compiled_by_kind[kind]:
            return None
    if host.state.players[seat].stats.get("next_spell_improvise") and not any(
        str(value.get("kind") or "").casefold() == "improvise"
        for value in mechanics
    ):
        mechanics.append({"kind": "improvise", "schema_version": 1})
    return mechanics


def _with_kicker_cost(
    host: CastCostHost,
    card: Any,
    schema: Mapping[str, Any],
    *,
    suppress_source_costs: bool,
) -> dict[str, Any] | None:
    result = copy.deepcopy(dict(schema))
    kicker = (
        compiled_fixed_mana_kicker_spec(host, card)
        if not suppress_source_costs
        else None
    )
    if kicker is None:
        return result
    optional_costs = list(result.get("optional_costs", ()))
    if any(
        str(value.get("id") or "") == "kicked"
        or str(value.get("kind") or "").casefold() == "kicker"
        for value in optional_costs
    ):
        return None
    optional_costs.append(kicker.cast_cost_option())
    result["optional_costs"] = optional_costs
    return result


def _with_bestow_cost(
    host: CastCostHost,
    card: Any,
    schema: Mapping[str, Any],
    *,
    suppress_source_costs: bool,
) -> dict[str, Any] | None:
    result = copy.deepcopy(dict(schema))
    bestow = (
        compiled_fixed_mana_bestow_spec(host, card)
        if not suppress_source_costs
        else None
    )
    if bestow is None:
        return result
    alternate_costs = list(result.get("alternate_costs", ()))
    if any(str(value.get("id") or "") == "bestow" for value in alternate_costs):
        return None
    alternate_costs.append(bestow.cast_cost_option())
    result["alternate_costs"] = alternate_costs
    return result


def _with_evoke_cost(
    host: CastCostHost,
    record: Any,
    program: Any,
    schema: Mapping[str, Any],
    *,
    suppress_source_costs: bool,
) -> dict[str, Any] | None:
    """Add one trusted fixed-mana Evoke branch or reviewed override."""

    result = copy.deepcopy(dict(schema))
    alternate_costs = [dict(value) for value in result.get("alternate_costs", ())]
    reviewed = [
        value
        for value in alternate_costs
        if value.get(EVOKE_PAYMENT_FIELD) is not None
    ]
    if any(
        not validate_evoke_payment_marker(value.get(EVOKE_PAYMENT_FIELD))
        for value in reviewed
    ):
        return None
    compiled = (
        compiled_evoke_specs(
            host,
            record.oracle_id,
            spell_program=program,
        )
        if not suppress_source_costs
        else ()
    )
    if len(compiled) > 1 or (compiled and reviewed):
        return None
    if compiled:
        alternate_costs.extend(compiled[0].cast_cost_options())
    elif (
        not suppress_source_costs
        and "evoke" in {str(value).casefold() for value in record.keywords}
        and len(reviewed) != 1
    ):
        return None
    result["alternate_costs"] = alternate_costs
    return result


def _flashback_base_options(
    host: CastCostHost,
    seat: str,
    card: Any,
    printed: Sequence[Mapping[str, Any]],
    *,
    cast_without_mana: bool,
    force_without_mana_cost: bool,
    suppress_source_costs: bool,
) -> list[dict[str, Any]]:
    """Add only the currently authorized Flashback casting-cost branch."""

    result = [copy.deepcopy(dict(value)) for value in printed]
    flashback = (
        compiled_fixed_mana_flashback_spec(host, card)
        if not suppress_source_costs and card.zone == "graveyard"
        else None
    )
    if flashback is None:
        return result
    if not cast_without_mana and not compiled_ordinary_zone_cast_permission(
        host, seat, card
    ):
        result.clear()
    if not force_without_mana_cost:
        result.append(flashback.cast_cost_option())
    return result


def _cast_schema_and_mechanics(
    host: CastCostHost,
    seat: str,
    card: Any,
    program: Any,
    *,
    suppress_source_costs: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Resolve the trusted cost schema and typed payment-mechanic set."""

    schema = (
        {}
        if suppress_source_costs
        else dict(program.cost_schema or {}) if program else {}
    )
    for augment in (_with_kicker_cost, _with_bestow_cost):
        schema = augment(
            host,
            card,
            schema,
            suppress_source_costs=suppress_source_costs,
        )
        if schema is None:
            return None
    record = host.card_record(card)
    if record is None:
        return None
    schema = _with_evoke_cost(
        host,
        record,
        program,
        schema,
        suppress_source_costs=suppress_source_costs,
    )
    if schema is None:
        return None
    mechanics = _compiled_payment_mechanics(
        host,
        seat,
        record,
        schema,
        program,
        suppress_source_costs=suppress_source_costs,
    )
    if mechanics is None:
        return None
    return schema, mechanics


def _initial_options(
    host: CastCostHost,
    seat: str,
    card: Any,
    program: Any,
    response: Mapping[str, Any],
    *,
    hint: bool,
    force_without_mana_cost: bool,
    alternative_base: Mapping[str, Any] | None,
    suppress_source_costs: bool,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    bool,
] | None:
    x_value = response.get("x")
    temporary_permission = host._temporary_play_permission(seat, card)
    cast_without_mana = force_without_mana_cost or bool(
        temporary_permission and temporary_permission.get("without_mana_cost")
    )
    if alternative_base is not None:
        if cast_without_mana or (x_value is not None and int(x_value) != 0):
            return None
        printed = [copy.deepcopy(dict(alternative_base))]
        has_x = False
    elif cast_without_mana:
        if x_value is not None and int(x_value) != 0:
            return None
        printed = [
            {
                "id": "without_mana_cost",
                "kind": "alternate",
                "label": "Cast without paying its mana cost",
                "requirements": host._mana_vector({}),
            }
        ]
        has_x = False
    else:
        printed, has_x = host._compiled_printed_cost_options(
            seat,
            card,
            x_value=int(x_value) if x_value is not None else None,
            hint=hint,
        )
    resolved = _cast_schema_and_mechanics(
        host,
        seat,
        card,
        program,
        suppress_source_costs=suppress_source_costs,
    )
    if resolved is None:
        return None
    schema, mechanics = resolved
    commander_tax = (
        2 * host.state.players[seat].commander_casts.get(card.oracle_id, 0)
        if card.zone == "command" and card.is_commander
        else 0
    )
    if alternative_base is not None:
        for option in printed:
            requirements = host._mana_vector(option.get("requirements"))
            requirements["GENERIC"] += commander_tax
            option["requirements"] = requirements
    base = _flashback_base_options(
        host,
        seat,
        card,
        printed,
        cast_without_mana=cast_without_mana,
        force_without_mana_cost=force_without_mana_cost,
        suppress_source_costs=suppress_source_costs,
    )
    for raw in [] if cast_without_mana else schema.get("alternate_costs", []):
        alternative = dict(raw)
        if not host._alternate_cost_condition_met(
            seat, dict(alternative.get("condition") or {})
        ):
            continue
        requirements = host._mana_vector(alternative.get("requirements"))
        requirements["GENERIC"] += commander_tax
        base.append(
            {
                **alternative,
                "id": str(alternative["id"]),
                "kind": str(alternative.get("kind") or "alternate"),
                "requirements": requirements,
            }
        )
    expanded = list(base)
    for raw in schema.get("optional_costs", []):
        additional = dict(raw)
        additional_vector = host._mana_vector(additional.get("requirements"))
        for base_option in base:
            combined = host._mana_vector(base_option["requirements"])
            for symbol, amount in additional_vector.items():
                combined[symbol] += amount
            expanded.append(
                {
                    **base_option,
                    **{
                        key: copy.deepcopy(value)
                        for key, value in additional.items()
                        if key not in {"requirements", "id"}
                    },
                    "id": str(additional["id"]),
                    "kind": str(
                        additional.get("kind") or "optional_additional"
                    ),
                    "requirements": combined,
                    "base_cost_option": base_option["id"],
                }
            )
    mandatory = [dict(value) for value in schema.get("additional_costs", [])]
    alternative_expansion = _expand_fixed_alternative_options(
        host,
        expanded,
        mandatory,
    )
    if alternative_expansion is None:
        return None
    expanded, mandatory = alternative_expansion
    return expanded, mandatory, mechanics, has_x


def _static_generic_reduction(
    host: CastCostHost,
    seat: str,
    card: Any,
    *,
    cast_type_line: str | None = None,
) -> int:
    return sum(
        reduction.generic_reduction
        for reduction in active_fixed_spell_cost_reductions(
            host,
            seat,
            card,
            cast_type_line=cast_type_line,
        )
    )


def _apply_static_reductions(
    host: CastCostHost,
    seat: str,
    card: Any,
    option: dict[str, Any],
    *,
    cast_type_line: str | None = None,
) -> None:
    reduction = _static_generic_reduction(
        host,
        seat,
        card,
        cast_type_line=cast_type_line,
    )
    if not reduction:
        return
    applied = min(int(option["requirements"]["GENERIC"]), reduction)
    option["requirements"]["GENERIC"] -= applied
    option.setdefault("cost_reductions", []).append(
        {
            "kind": "fixed_query",
            "count": applied,
        }
    )


def _apply_affinity(
    host: CastCostHost,
    seat: str,
    option: dict[str, Any],
    mechanic: Mapping[str, Any],
) -> None:
    try:
        spec = AffinitySpec.from_dict(
            {
                key: mechanic[key]
                for key in ("schema_version", "quality", "queries_any")
            }
        )
    except (CastingPaymentKeywordError, KeyError, TypeError) as exc:
        raise ValueError("Typed Affinity payment is malformed") from exc
    count = 0
    for object_id in host.state.players[seat].zones["battlefield"]:
        card = host.state.cards[object_id]
        if card.controller != seat or card.phased_out:
            continue
        effective = host._effective_card_data(card)
        row = object_query_result(
            card,
            effective,
            type_parts=host._type_parts(str(effective.get("type_line") or "")),
            known_to_actor=True,
            attached_to_ref=None,
        )
        if any(object_matches_query(row, query) for query in spec.queries_any):
            count += 1
    option["requirements"]["GENERIC"] = max(
        0, int(option["requirements"]["GENERIC"]) - count
    )
    option.setdefault("cost_reductions", []).append(
        {
            "kind": "affinity",
            "count": count,
            "quality": spec.quality,
        }
    )


def _convoke_candidates(host: CastCostHost, seat: str) -> tuple[ConvokeCandidate, ...]:
    result: list[ConvokeCandidate] = []
    for object_id in host.state.players[seat].zones["battlefield"]:
        card = host.state.cards[object_id]
        if card.controller != seat or card.phased_out or card.tapped:
            continue
        data = host._effective_card_data(card)
        types, _, _ = host._type_parts(str(data.get("type_line") or ""))
        if "creature" not in types:
            continue
        result.append(
            ConvokeCandidate(
                ref=card.ref,
                object_id=card.object_id,
                logical_object_id=card.logical_object_id,
                colors=tuple(str(color) for color in data.get("colors", ())),
            )
        )
    return tuple(result)


def _convoke_plan_is_affordable(
    host: CastCostHost,
    seat: str,
    plan: ConvokePaymentPlan,
    *,
    spend_context: Any,
) -> bool:
    return host._cost_is_affordable(
        seat,
        plan.remaining_dict,
        exclude_sources=set(plan.selected_object_ids),
        spend_context=spend_context,
    )


def revalidate_convoke_payment(
    host: CastCostHost,
    seat: str,
    option: Mapping[str, Any],
) -> ConvokePaymentPlan | None:
    raw = option.get("convoke_payment")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ConvokeError("Convoke payment snapshot must be an object")
    plan = ConvokePaymentPlan.from_dict(raw)
    if plan.remaining_dict != canonical_mana_requirements(
        option.get("requirements") or {}
    ):
        raise ConvokeError("Convoke payment remainder does not match the cast cost")
    current_by_id = {
        candidate.object_id: candidate
        for candidate in _convoke_candidates(host, seat)
    }
    for contribution in plan.contributions:
        current = current_by_id.get(contribution.candidate.object_id)
        if current != contribution.candidate:
            raise ConvokeError(
                "A selected Convoke creature changed identity or characteristics"
            )
    selected_tap_refs = option.get("selected_tap_cost_cards") or []
    if not isinstance(selected_tap_refs, list) or not set(plan.selected_refs).issubset(
        set(selected_tap_refs)
    ):
        raise ConvokeError("Convoke payment refs do not match the tap-cost plan")
    return plan


def _apply_convoke(
    host: CastCostHost,
    seat: str,
    option: dict[str, Any],
    response: Mapping[str, Any],
    *,
    hint: bool,
    spend_context: Any,
    selected_cards: list[Any],
    choice_schema: dict[str, Any],
) -> bool:
    selected_object_ids = {card.object_id for card in selected_cards}
    candidates = tuple(
        candidate
        for candidate in _convoke_candidates(host, seat)
        if candidate.object_id not in selected_object_ids
    )
    requirements = canonical_mana_requirements(option["requirements"])
    choice_schema["convoke_cards"] = {
        "type": "object_ref_array",
        "minimum": 0,
        "maximum": min(
            len(candidates),
            sum(requirements[symbol] for symbol in CONVOKE_PAYMENT_SYMBOLS),
        ),
        "legal_refs": [candidate.ref for candidate in candidates],
        "payment": "convoke",
    }
    affordable = lambda plan: _convoke_plan_is_affordable(
        host,
        seat,
        plan,
        spend_context=spend_context,
    )
    if hint:
        plan = find_convoke_plan(requirements, candidates, affordable=affordable)
    else:
        raw_values = response.get("convoke_cards") or []
        if not isinstance(raw_values, (list, tuple)) or any(
            type(value) is not str for value in raw_values
        ):
            return False
        values = tuple(raw_values)
        by_ref = {candidate.ref: candidate for candidate in candidates}
        if len(values) != len(set(values)) or any(
            value not in by_ref for value in values
        ):
            return False
        plan = select_convoke_plan(
            requirements,
            tuple(by_ref[value] for value in values),
            affordable=affordable,
        )
    if plan is None:
        return False
    option["requirements"] = plan.remaining_dict
    option["convoke_payment"] = plan.to_dict()
    if hint:
        option.setdefault("recommended_payment_refs", {})["convoke_cards"] = list(
            plan.selected_refs
        )
    cards_by_id = {
        host.state.cards[object_id].object_id: host.state.cards[object_id]
        for object_id in host.state.players[seat].zones["battlefield"]
    }
    selected_cards.extend(
        cards_by_id[contribution.candidate.object_id]
        for contribution in plan.contributions
    )
    return True


def _apply_improvise(
    host: CastCostHost,
    seat: str,
    option: dict[str, Any],
    mechanic: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    hint: bool,
    spend_context: Any,
    selected_cards: list[Any],
    choice_schema: dict[str, Any],
) -> bool:
    candidates = [
        card
        for card in _improvise_candidates(host, seat)
        if card not in selected_cards
    ]
    field = "improvise_cards"
    choice_schema[field] = {
        "type": "object_ref_array",
        "minimum": 0,
        "maximum": min(
            len(candidates), int(option["requirements"]["GENERIC"])
        ),
        "legal_refs": [card.ref for card in candidates],
        "payment": "improvise",
    }
    if hint:
        plan = _find_improvise_plan(
            host,
            seat,
            option["requirements"],
            candidates,
            spend_context=spend_context,
        )
        if plan is None:
            return False
        option["requirements"] = plan[0]
        option.setdefault("recommended_payment_refs", {})[field] = [
            card.ref for card in plan[1]
        ]
        selected_cards.extend(plan[1])
        return True
    raw_values = response.get(field) or []
    if isinstance(raw_values, (str, bytes)):
        return False
    if not isinstance(raw_values, (list, tuple)) or any(
        type(value) is not str for value in raw_values
    ):
        return False
    values = list(raw_values)
    by_ref = {candidate.ref: candidate for candidate in candidates}
    if len(values) != len(set(values)) or any(
        value not in by_ref for value in values
    ):
        return False
    selected = [by_ref[value] for value in values]
    if len(selected) > int(option["requirements"]["GENERIC"]):
        return False
    option["requirements"]["GENERIC"] -= len(selected)
    selected_cards.extend(selected)
    return True


def _improvise_candidates(host: CastCostHost, seat: str) -> tuple[Any, ...]:
    candidates: list[Any] = []
    for object_id in host.state.players[seat].zones["battlefield"]:
        card = host.state.cards[object_id]
        if card.controller != seat or card.phased_out or card.tapped:
            continue
        data = host._effective_card_data(card)
        types, _, _ = host._type_parts(str(data.get("type_line") or ""))
        if "artifact" in types:
            candidates.append(card)
    return tuple(candidates)


def _find_improvise_plan(
    host: CastCostHost,
    seat: str,
    requirements: Mapping[str, int],
    candidates: Sequence[Any],
    *,
    spend_context: Any,
) -> tuple[dict[str, int], list[Any]] | None:
    """Find a deterministic minimum-card Improvise payment."""

    base = host._mana_vector(requirements)
    best: tuple[dict[str, int], list[Any]] | None = None

    def search(index: int, selected: list[Any]) -> None:
        nonlocal best
        if best is not None and len(selected) >= len(best[1]):
            return
        reduced = host._mana_vector(base)
        if len(selected) > reduced["GENERIC"]:
            return
        reduced["GENERIC"] -= len(selected)
        if host._cost_is_affordable(
            seat,
            reduced,
            exclude_sources={card.object_id for card in selected},
            spend_context=spend_context,
        ):
            best = (reduced, list(selected))
            return
        if index >= len(candidates):
            return
        search(index + 1, selected)
        selected.append(candidates[index])
        search(index + 1, selected)
        selected.pop()

    search(0, [])
    return best


def _delve_candidates(
    host: CastCostHost,
    seat: str,
) -> tuple[DelveCandidate, ...]:
    result: list[DelveCandidate] = []
    for object_id in host.state.players[seat].zones["graveyard"]:
        card = host.state.cards[object_id]
        if card.zone != "graveyard" or card.owner != seat:
            continue
        result.append(
            DelveCandidate(
                ref=card.ref,
                object_id=card.object_id,
                logical_object_id=card.logical_object_id,
            )
        )
    return tuple(result)


def revalidate_delve_payment(
    host: CastCostHost,
    seat: str,
    option: Mapping[str, Any],
) -> DelvePaymentPlan | None:
    raw = option.get("delve_payment")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise CastingPaymentKeywordError("Delve payment snapshot must be an object")
    plan = DelvePaymentPlan.from_dict(raw)
    post_delve = plan.remaining_dict
    final_requirements = canonical_mana_requirements(
        option.get("requirements") or {}
    )
    raw_convoke = option.get("convoke_payment")
    if raw_convoke is not None:
        try:
            convoke_plan = ConvokePaymentPlan.from_dict(raw_convoke)
        except ConvokeError as exc:
            raise CastingPaymentKeywordError(
                "The payment following Delve has a malformed Convoke plan"
            ) from exc
        remainder_matches = (
            convoke_plan.requirement_dict == post_delve
            and convoke_plan.remaining_dict == final_requirements
        )
    else:
        tap_refs = tuple(option.get("selected_tap_cost_cards", ()))
        expected_final = dict(post_delve)
        expected_final["GENERIC"] -= len(tap_refs)
        remainder_matches = (
            expected_final["GENERIC"] >= 0
            and expected_final == final_requirements
        )
    if not remainder_matches:
        raise CastingPaymentKeywordError(
            "Delve payment remainder does not match the cast cost"
        )
    current_by_id = {
        candidate.object_id: candidate
        for candidate in _delve_candidates(host, seat)
    }
    for selected in plan.selected:
        if current_by_id.get(selected.object_id) != selected:
            raise CastingPaymentKeywordError(
                "A selected Delve card changed zone or identity"
            )
    recorded = tuple(
        str(ref)
        for selection in option.get("selected_additional_costs", ())
        if str(selection.get("kind") or "") == "delve"
        for ref in selection.get("cards", ())
    )
    if recorded != plan.selected_refs:
        raise CastingPaymentKeywordError(
            "Delve payment refs do not match the cast-cost plan"
        )
    return plan


def _apply_delve(
    host: CastCostHost,
    seat: str,
    option: dict[str, Any],
    response: Mapping[str, Any],
    *,
    hint: bool,
    spend_context: Any,
    followed_by_tap_payment: bool,
    choice_schema: dict[str, Any],
) -> bool:
    candidates = _delve_candidates(host, seat)
    original = canonical_mana_requirements(option["requirements"])
    maximum = min(len(candidates), original["GENERIC"])
    choice_schema["delve_cards"] = {
        "type": "object_ref_array",
        "minimum": 0,
        "maximum": maximum,
        "legal_refs": [candidate.ref for candidate in candidates],
        "zone": "graveyard",
        "destination": "exile",
        "payment": "delve",
    }
    selected: tuple[DelveCandidate, ...]
    if hint:
        count = maximum if followed_by_tap_payment else -1
        if not followed_by_tap_payment:
            for candidate_count in range(maximum + 1):
                remaining = dict(original)
                remaining["GENERIC"] -= candidate_count
                if host._cost_is_affordable(
                    seat,
                    remaining,
                    spend_context=spend_context,
                ):
                    count = candidate_count
                    break
        if count < 0:
            return False
        selected = candidates[:count]
        option.setdefault("recommended_payment_refs", {})["delve_cards"] = [
            candidate.ref for candidate in selected
        ]
    else:
        raw_values = response.get("delve_cards") or []
        if not isinstance(raw_values, (list, tuple)) or any(
            type(value) is not str for value in raw_values
        ):
            return False
        values = tuple(raw_values)
        by_ref = {candidate.ref: candidate for candidate in candidates}
        if (
            len(values) != len(set(values))
            or len(values) > maximum
            or any(value not in by_ref for value in values)
        ):
            return False
        selected = tuple(by_ref[value] for value in values)
    remaining = dict(original)
    remaining["GENERIC"] -= len(selected)
    plan = DelvePaymentPlan(
        original_requirements=tuple(original.items()),
        remaining_requirements=tuple(remaining.items()),
        selected=selected,
    )
    option["requirements"] = remaining
    option["delve_payment"] = plan.to_dict()
    if selected:
        option.setdefault("selected_additional_costs", []).append(
            {"kind": "delve", "cards": list(plan.selected_refs)}
        )
    return True


def _apply_payment_mechanics(
    host: CastCostHost,
    seat: str,
    option: dict[str, Any],
    mechanics: Sequence[Mapping[str, Any]],
    response: Mapping[str, Any],
    *,
    hint: bool,
    spend_context: Any,
) -> tuple[dict[str, Any], list[Any]] | None:
    choice_schema: dict[str, Any] = {}
    selected_cards: list[Any] = []
    kinds = tuple(
        str(mechanic.get("kind") or "").casefold()
        for mechanic in mechanics
    )
    if any(kind not in {"affinity", "convoke", "delve", "improvise"} for kind in kinds):
        return None
    tap_mechanics = tuple(
        mechanic
        for mechanic in mechanics
        if str(mechanic.get("kind") or "").casefold()
        in {"convoke", "improvise"}
    )
    delve_mechanics = tuple(
        mechanic
        for mechanic in mechanics
        if str(mechanic.get("kind") or "").casefold() == "delve"
    )
    if len(tap_mechanics) > 1 or len(delve_mechanics) > 1:
        return None
    for mechanic in mechanics:
        kind = str(mechanic.get("kind") or "").casefold()
        if kind == "affinity":
            _apply_affinity(host, seat, option, mechanic)
    if delve_mechanics and not _apply_delve(
        host,
        seat,
        option,
        response,
        hint=hint,
        spend_context=spend_context,
        followed_by_tap_payment=bool(tap_mechanics),
        choice_schema=choice_schema,
    ):
        return None
    if tap_mechanics:
        mechanic = tap_mechanics[0]
        kind = str(mechanic.get("kind") or "").casefold()
        if kind == "convoke":
            if not _apply_convoke(
                host,
                seat,
                option,
                response,
                hint=hint,
                spend_context=spend_context,
                selected_cards=selected_cards,
                choice_schema=choice_schema,
            ):
                return None
        elif not _apply_improvise(
            host,
            seat,
            option,
            mechanic,
            response,
            hint=hint,
            spend_context=spend_context,
            selected_cards=selected_cards,
            choice_schema=choice_schema,
        ):
            return None
    return choice_schema, selected_cards


def _maximum_affordable_x_with_mechanics(
    host: CastCostHost,
    seat: str,
    card: Any,
    mechanics: Sequence[Mapping[str, Any]],
    *,
    spend_context: Any,
    limit: int = 100,
) -> int:
    maximum = -1
    for value in range(limit + 1):
        printed, _ = host._compiled_printed_cost_options(
            seat,
            card,
            x_value=value,
            hint=False,
        )
        value_payable = False
        for raw_option in printed:
            option = copy.deepcopy(raw_option)
            option["requirements"] = host._mana_vector(option["requirements"])
            _apply_static_reductions(host, seat, card, option)
            payment = _apply_payment_mechanics(
                host,
                seat,
                option,
                mechanics,
                {},
                hint=True,
                spend_context=spend_context,
            )
            if payment is None:
                continue
            _, selected = payment
            if host._cost_is_affordable(
                seat,
                option["requirements"],
                exclude_sources={source.object_id for source in selected},
                spend_context=spend_context,
            ):
                value_payable = True
                break
        if not value_payable:
            break
        maximum = value
    return maximum


def _fixed_zone_change_selection(
    host: CastCostHost,
    *,
    seat: str,
    source_object_id: str,
    cost: FixedZoneChangeAdditionalCost,
    cost_position: int,
    response: Mapping[str, Any],
    hint: bool,
    choice_schema: dict[str, Any],
) -> tuple[bool, dict[str, Any] | None]:
    candidates = list(
        fixed_zone_change_cost_candidates(
            host,
            actor=seat,
            cost=cost,
            exclude_object_id=source_object_id,
        )
    )
    if not candidates:
        return False, None
    choice_schema[cost.choice_field] = {
        "type": "object_ref_array",
        "count": 1,
        "legal_refs": candidates,
        "zone": cost.origin_zone,
        "destination": cost.destination_zone,
        "payment": cost.log_kind,
    }
    if hint:
        return True, None
    raw_values = response.get(cost.choice_field)
    if raw_values is None:
        raw_values = response.get("cost_cards")
    if not isinstance(raw_values, (list, tuple)):
        return False, None
    values = list(raw_values)
    if (
        len(values) != 1
        or type(values[0]) is not str
        or values[0] not in candidates
    ):
        return False, None
    return True, {
        "kind": ZONE_CHANGE_COST_KIND,
        "operation": cost.operation,
        "card": values[0],
        "cost_position": cost_position,
    }


def _apply_additional_costs(
    host: CastCostHost,
    seat: str,
    card: Any,
    option: dict[str, Any],
    mandatory_costs: Sequence[Mapping[str, Any]],
    response: Mapping[str, Any],
    *,
    hint: bool,
    choice_schema: dict[str, Any],
) -> bool:
    selected_nonmana: list[dict[str, Any]] = []
    selected_refs: set[str] = set()
    for index, raw in enumerate(mandatory_costs):
        additional = dict(raw)
        kind = str(additional.get("kind") or "")
        try:
            counter_cost = fixed_counter_additional_cost(additional)
        except AdditionalCostError:
            return False
        if counter_cost is not None:
            if len(mandatory_costs) != 1:
                return False
            candidates = list(
                fixed_counter_cost_candidates(
                    host,
                    actor=seat,
                    cost=counter_cost,
                )
            )
            if not candidates:
                return False
            choice_schema[counter_cost.choice_field] = {
                "type": "object_ref",
                "legal_refs": candidates,
                "zone": "battlefield",
                "payment": "counter_placement",
                "counter": counter_cost.counter_name,
                "amount": counter_cost.amount,
            }
            if hint:
                continue
            selected = response.get(counter_cost.choice_field)
            if type(selected) is not str or selected not in candidates:
                return False
            selected_nonmana.append(
                {
                    "kind": counter_cost.kind,
                    "card": selected,
                    "cost_position": index,
                }
            )
            continue
        try:
            zone_change_cost = fixed_zone_change_additional_cost(additional)
        except AdditionalCostError:
            return False
        if zone_change_cost is not None:
            if len(mandatory_costs) != 1:
                return False
            valid, selected = _fixed_zone_change_selection(
                host,
                seat=seat,
                source_object_id=card.object_id,
                cost=zone_change_cost,
                cost_position=index,
                response=response,
                hint=hint,
                choice_schema=choice_schema,
            )
            if not valid:
                return False
            if selected is not None:
                selected_nonmana.append(selected)
            continue
        try:
            life_cost = fixed_life_payment_additional_cost(additional)
        except AdditionalCostError:
            return False
        if life_cost is not None:
            if len(mandatory_costs) != 1:
                return False
            if host.state.players[seat].life < life_cost.amount:
                return False
            continue
        if kind == "life_x":
            selected_x = int(response["x"]) if response.get("x") is not None else 0
            minimum = int(additional.get("minimum", 0))
            maximum = host.state.players[seat].life
            if maximum < minimum or not minimum <= selected_x <= maximum:
                return False
            choice_schema["x"] = {
                "type": "integer",
                "minimum": minimum,
                "maximum": maximum,
                "payment": "life",
            }
            continue
        if kind not in {"sacrifice", "discard"}:
            return not kind
        count = int(additional.get("count", 1))
        candidates = [
            ref
            for ref in legacy_additional_cost_candidates(
                host,
                actor=seat,
                source=card,
                specification=additional,
            )
            if ref not in selected_refs
        ]
        if len(candidates) < count:
            return False
        field = str(additional.get("choice_field") or f"{kind}_cards")
        choice_schema[field] = {
            "type": "object_ref_array",
            "count": count,
            "legal_refs": candidates,
            "zone": "hand" if kind == "discard" else "battlefield",
            "destination": "graveyard",
        }
        if hint:
            continue
        raw_values = response.get(field)
        if raw_values is None and len(mandatory_costs) == 1:
            raw_values = response.get("cost_cards")
        values = [str(value) for value in (raw_values or [])]
        if (
            len(values) != count
            or len(set(values)) != count
            or any(value not in candidates for value in values)
        ):
            return False
        selected_refs.update(values)
        selected_nonmana.append({"kind": kind, "cards": values, "index": index})
    option["additional_costs"] = [dict(value) for value in mandatory_costs]
    if selected_nonmana:
        option.setdefault("selected_additional_costs", []).extend(
            selected_nonmana
        )
    return True


def _finalize_option(
    host: CastCostHost,
    seat: str,
    card: Any,
    option: dict[str, Any],
    mandatory_costs: Sequence[Mapping[str, Any]],
    mechanics: Sequence[Mapping[str, Any]],
    response: Mapping[str, Any],
    *,
    hint: bool,
    spend_context: Any,
    has_x: bool,
    cast_type_line: str | None,
) -> CastCostOption | None:
    option = copy.deepcopy(option)
    selected_additional_costs = option.pop(
        "_selected_additional_costs", None
    )
    if selected_additional_costs is not None:
        mandatory_costs = list(selected_additional_costs)
    option_additional_costs = option.pop("_additional_option_costs", ())
    if option_additional_costs:
        mandatory_costs = [*mandatory_costs, *option_additional_costs]
    option["base_requirements"] = host._mana_vector(option["requirements"])
    _apply_static_reductions(
        host,
        seat,
        card,
        option,
        cast_type_line=cast_type_line,
    )
    exile_spec = option.get("exile_from_hand")
    if isinstance(exile_spec, Mapping):
        candidates = host._exile_cost_candidates(seat, card, exile_spec)
        if not candidates:
            return None
        option["exile_candidates"] = candidates
    payment = _apply_payment_mechanics(
        host,
        seat,
        option,
        mechanics,
        response,
        hint=hint,
        spend_context=spend_context,
    )
    if payment is None:
        return None
    choice_schema, selected_cards = payment
    if not host._cost_is_affordable(
        seat,
        option["requirements"],
        exclude_sources={card.object_id for card in selected_cards},
        spend_context=spend_context,
    ):
        return None
    if selected_cards:
        option["selected_tap_cost_cards"] = [card.ref for card in selected_cards]
    x_value_policy = option.get("x_value_policy")
    if x_value_policy == "zero":
        if response.get("x") is not None and int(response["x"]) != 0:
            return None
    elif x_value_policy is not None:
        return None
    elif has_x:
        maximum = (
            _maximum_affordable_x_with_mechanics(
                host,
                seat,
                card,
                mechanics,
                spend_context=spend_context,
            )
            if mechanics
            else host._maximum_affordable_x(seat, card)
        )
        if maximum < 0:
            return None
        choice_schema["x"] = {"type": "integer", "minimum": 0, "maximum": maximum}
    if not _apply_additional_costs(
        host,
        seat,
        card,
        option,
        mandatory_costs,
        response,
        hint=hint,
        choice_schema=choice_schema,
    ):
        return None
    if isinstance(exile_spec, Mapping):
        choice_schema["exile_card"] = {
            "type": "object_ref",
            "legal_refs": list(option["exile_candidates"]),
            "zone": "hand",
            "destination": "exile",
        }
        if not hint:
            selected = str(
                response.get("exile_card")
                or list(response.get("exile_cards") or [None])[0]
                or ""
            )
            if selected not in option["exile_candidates"]:
                return None
            option["selected_exile_card"] = selected
    if choice_schema:
        option["choice_schema"] = choice_schema
    return CastCostOption.from_dict(option)


def build_cast_cost_options(
    host: CastCostHost,
    seat: str,
    card: Any,
    program: Any,
    *,
    response: Mapping[str, Any] | None = None,
    hint: bool,
    force_without_mana_cost: bool = False,
    alternative_base: Mapping[str, Any] | None = None,
    cast_type_line: str | None = None,
    suppress_source_costs: bool = False,
) -> tuple[CastCostOption, ...]:
    """Return immutable, currently payable server-authoritative cost choices."""

    submission = dict(response or {})
    initial = _initial_options(
        host,
        seat,
        card,
        program,
        submission,
        hint=hint,
        force_without_mana_cost=force_without_mana_cost,
        alternative_base=alternative_base,
        suppress_source_costs=suppress_source_costs,
    )
    if initial is None:
        return ()
    expanded, mandatory, mechanics, has_x = initial
    result = []
    for option in expanded:
        option_cast_type_line = (
            str(option["cast_type_line"])
            if option.get("cast_type_line")
            else cast_type_line
        )
        spend_context = host._spell_mana_spend_context(
            str(
                option_cast_type_line
                if option_cast_type_line is not None
                else host._effective_card_data(card).get("type_line") or ""
            )
        )
        finalized = _finalize_option(
            host,
            seat,
            card,
            option,
            mandatory,
            mechanics,
            submission,
            hint=hint,
            spend_context=spend_context,
            has_x=has_x,
            cast_type_line=option_cast_type_line,
        )
        if finalized is not None:
            result.append(finalized)
    return tuple(result)


__all__ = [
    "CastCostHost",
    "build_cast_cost_options",
    "revalidate_convoke_payment",
    "revalidate_delve_payment",
]
