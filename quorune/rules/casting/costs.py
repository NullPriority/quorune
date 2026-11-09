from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ...additional_cost_vocabulary import ZONE_CHANGE_COST_KIND
from .face_queries import build_cast_cost_options
from .option_expansion import bind_granted_payment_mechanics
from .nonmoving_additional_costs import select_fixed_nonmoving_cost
from .additional_zone_costs import select_zone_change_additional_cost
from ...casting_payment_keywords import (
    AffinitySpec,
    CastingPaymentKeywordError,
    DelveCandidate,
    DelvePaymentPlan,
)
from ...compiled_cast_costs import (
    compiled_affinity_specs,
    compiled_assist_specs,
    compiled_convoke_specs,
    compiled_delve_specs,
    compiled_evoke_specs,
    compiled_improvise_specs,
)
from ...compiled_flashback import (
    compiled_fixed_mana_flashback_spec,
    compiled_ordinary_zone_cast_permission,
)
from ...compiled_kicker import compiled_kicker_spec
from .paid_entry_copy_costs import with_paid_entry_copy_cost_schema
from .multikicker_costs import with_multikicker_cost_schema
from .replicate_costs import with_replicate_cost_schema
from .base_cost_options import _flashback_base_options, _zone_cast_base_options
from .emerge_costs import emerge_base_options, reduce_emerge_total, finalize_emerge_selection
from .disturb_costs import disturb_back_cast_permission,disturb_base_options
from ...compiled_bestow import compiled_fixed_mana_bestow_spec
from .mutate_costs import with_mutate_cost_schema
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
from ...printed_mana_cost_model import cast_snow_payment_kwargs
from ..action_proposals import CastCostOption
from ..next_spell_payment import with_next_spell_payments
from ..casting_additional_costs import (
    AdditionalCostError,
    fixed_counter_additional_cost,
    fixed_counter_cost_candidates,
    legacy_additional_cost_candidates,
)
from ..casting_additional_cost_groups import (
    expand_fixed_alternative_options,
    fixed_life_payment_additional_cost, announced_additional_cost_dependencies,
)
from .lifecycle_costs import (
    alternative_cost_condition_met,
    retrace_base_options,
    with_fixed_cast_lifecycle_costs,
    with_fixed_public_alternative_costs,
    zone_lifecycle_base_options,
)
from .static_modifiers import (
    apply_static_reductions as _apply_owned_static_reductions,
    fixed_generic_reduction as _static_generic_reduction,
)
from .targeted_costs import target_cost_for_x_preview


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
        snow_required: int = 0,
        preserve_source_id: str | None = None,
    ) -> bool: ...

    def _maximum_affordable_x(self, seat: str, card: Any) -> int: ...

    def _current_turn_history(self, kind: str) -> tuple[Any, ...]: ...


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

    stats = host.state.players[seat].stats
    turn_sequence = host.state.turn_sequence
    if suppress_source_costs:
        return with_next_spell_payments(stats, turn_sequence, [])
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
    for kind in ("delve", "improvise"):
        specs = compiled_by_kind[kind]
        if specs:
            mechanics.append(specs[0].to_dict())
    for kind in compiled_by_kind:
        declared = kind in declared_kinds
        printed = kind in printed_keywords
        if (declared or printed) and not compiled_by_kind[kind]:
            return None
    return with_next_spell_payments(stats, turn_sequence, mechanics)


def _with_kicker_cost(
    host: CastCostHost,
    card: Any,
    schema: Mapping[str, Any],
    *,
    suppress_source_costs: bool,
) -> dict[str, Any] | None:
    result = copy.deepcopy(dict(schema))
    kicker = (
        compiled_kicker_spec(host, card)
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
    for augment in (
        _with_kicker_cost,
        with_paid_entry_copy_cost_schema,
        with_multikicker_cost_schema,
        with_replicate_cost_schema,
        _with_bestow_cost,
        with_mutate_cost_schema,
        with_fixed_public_alternative_costs,
    ):
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
    schema = with_fixed_cast_lifecycle_costs(
        host,
        card,
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
    if (not suppress_source_costs and "assist" in {str(keyword).casefold() for keyword in record.keywords}
            and compiled_assist_specs(host, record.oracle_id, spell_program=program)):
        schema["compiled_assist"] = True
    return schema, mechanics


def _additional_x_with_free_mana(host, seat, card, program, *, suppress_source_costs):
    resolved = _cast_schema_and_mechanics(host, seat, card, program, suppress_source_costs=suppress_source_costs)
    if resolved is None or not announced_additional_cost_dependencies(resolved[0]):
        return False
    _options, printed_x = host._compiled_printed_cost_options(seat, card, x_value=0, hint=True)
    return not printed_x


def _printed_or_fixed_cast_base(host, seat, card, program, *, x_value, hint, cast_without_mana,
                                alternative_base, suppress_source_costs, resolving_exile_authority):
    if alternative_base is None and not cast_without_mana:
        if resolving_exile_authority:
            from .face_queries import current_printed_cast_mana_cost
            record = host.card_record(card)
            if record is None or not current_printed_cast_mana_cost(host, card, record).strip():
                return [], False
        return host._compiled_printed_cost_options(seat, card, x_value=int(x_value) if x_value is not None else None,
                                                   hint=hint)
    if alternative_base is not None and cast_without_mana:
        return None
    if x_value is not None and int(x_value) != 0 and not _additional_x_with_free_mana(
            host, seat, card, program, suppress_source_costs=suppress_source_costs):
        return None
    if alternative_base is not None:
        return [copy.deepcopy(dict(alternative_base))], False
    return [{"id": "without_mana_cost", "kind": "alternate", "label": "Cast without paying its mana cost",
             "requirements": host._mana_vector({})}], False


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
    cast_type_line: str | None = None,
    resolving_exile_authority: bool = False,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    bool,
] | None:
    x_value = response.get("x")
    temporary_permission = host._temporary_play_permission(seat, card)
    intrinsic_disturb = (disturb_back_cast_permission(host,seat,card)
        and not (force_without_mana_cost or suppress_source_costs or alternative_base is not None))
    cast_without_mana = force_without_mana_cost or bool(
        temporary_permission and temporary_permission.get("without_mana_cost")
        and not intrinsic_disturb and not resolving_exile_authority
    )
    printed_base = _printed_or_fixed_cast_base(host, seat, card, program, x_value=x_value, hint=hint,
        cast_without_mana=cast_without_mana, alternative_base=alternative_base,
        suppress_source_costs=suppress_source_costs, resolving_exile_authority=resolving_exile_authority)
    if printed_base is None:
        return None
    printed, has_x = printed_base
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
    if (cast_without_mana or alternative_base is not None) and announced_additional_cost_dependencies(schema):
        _printed, base_has_x = host._compiled_printed_cost_options(seat, card, x_value=0, hint=True)
        if base_has_x:
            printed = [{**option, 'x_value_policy': 'zero'} for option in printed]
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
    base = _zone_cast_base_options(
        host,
        seat,
        card,
        printed,
        cast_without_mana=cast_without_mana,
        force_without_mana_cost=force_without_mana_cost,
        alternative_base=alternative_base,
        suppress_source_costs=suppress_source_costs or alternative_base is not None,
        resolving_exile_authority=resolving_exile_authority,
    )
    base.extend(emerge_base_options(host, seat, card, commander_tax=commander_tax,
        enabled=not (cast_without_mana or suppress_source_costs or alternative_base is not None)))
    from .global_alternatives import battlefield_alternative_base_options
    base.extend(battlefield_alternative_base_options(host,seat,card,cast_type_line=cast_type_line,
        enabled=not(cast_without_mana or suppress_source_costs or alternative_base is not None or intrinsic_disturb),
        has_mana_x=has_x,x_value=x_value,commander_tax=commander_tax,temporary_permission=temporary_permission))
    for raw in [] if cast_without_mana or alternative_base is not None else schema.get("alternate_costs", []):
        alternative = dict(raw)
        if (
            alternative.get("source_zone") is not None
            and str(alternative["source_zone"]) != card.zone
        ):
            continue
        if not alternative_cost_condition_met(
            host, seat, card, dict(alternative.get("condition") or {})
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
    if intrinsic_disturb:
        base=disturb_base_options(host,seat,card,commander_tax=commander_tax)
    from .overload_costs import overload_cost_options
    overload_options = overload_cost_options(host, seat, card, program, commander_tax=commander_tax,
        force_without_mana_cost=force_without_mana_cost, alternative_base=alternative_base,
        suppress_source_costs=suppress_source_costs)
    if overload_options is None:
        return None
    base.extend(overload_options)
    from .cleave_costs import cleave_cost_options
    cleave_options = cleave_cost_options(host, seat, card, program, commander_tax=commander_tax,
        force_without_mana_cost=force_without_mana_cost, alternative_base=alternative_base,
        suppress_source_costs=suppress_source_costs)
    if cleave_options is None:
        return None
    base.extend(cleave_options)
    from .option_expansion import expand_optional_mana_costs
    expanded = expand_optional_mana_costs(host, card, base, schema, response=response, hint=hint)
    mandatory = [dict(value) for value in schema.get("additional_costs", [])]
    alternative_expansion = expand_fixed_alternative_options(
        expanded, mandatory, mana_vector=host._mana_vector,
    )
    if alternative_expansion is None:
        return None
    expanded, mandatory = alternative_expansion
    from .spree_costs import with_spree_mode_cost_options
    expanded = with_spree_mode_cost_options(program, schema, expanded, response, hint=hint)
    from .entwine_costs import with_entwine_mode_cost_options
    expanded = with_entwine_mode_cost_options(program,schema,expanded,response,hint=hint)
    from .option_expansion import expand_optional_resource_costs
    expanded = expand_optional_resource_costs(host, seat, card, expanded, suppress_source_costs=suppress_source_costs,
        mechanics=mechanics, mandatory=mandatory, cast_type_line=cast_type_line)
    from .aftermath import with_aftermath_stack_context
    from .assist_costs import ASSIST_OPTION_FIELD
    if schema.get("compiled_assist") is True:
        expanded = [{**option, ASSIST_OPTION_FIELD: 1} for option in expanded]
    return with_aftermath_stack_context(host,card,expanded), mandatory, mechanics, has_x


def _apply_static_reductions(
    host: CastCostHost,
    seat: str,
    card: Any,
    option: dict[str, Any],
    *,
    program: Any,
    cast_type_line: str | None = None,
) -> None:
    from ...morph import FACE_DOWN_CAST_METHODS
    face_down_method=option.get('id') if option.get('id') in FACE_DOWN_CAST_METHODS else None
    _apply_owned_static_reductions(
        host,
        seat,
        card,
        option,
        program=program,
        fixed_reduction=_static_generic_reduction(
            host,
            seat,
            card,
            cast_type_line=cast_type_line,
            face_down_method=face_down_method,
        ),
        cast_type_line=cast_type_line,
        face_down_method=face_down_method,
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
    reserved_cards: Sequence[Any] = (),
    snow_required: int = 0,
) -> bool:
    return host._cost_is_affordable(
        seat,
        plan.remaining_dict,
        exclude_sources=set(plan.selected_object_ids)|{c.object_id for c in reserved_cards},
        spend_context=spend_context,
        **({"snow_required": snow_required} if snow_required else {}),
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
        reserved_cards=selected_cards,
        **cast_snow_payment_kwargs(option),
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
            reserved_cards=selected_cards,
            **cast_snow_payment_kwargs(option),
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
    reserved_cards: Sequence[Any] = (),
    maximum_taps: int | None = None,
    snow_required: int = 0,
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
        if maximum_taps is not None and len(selected)>maximum_taps:return
        reduced["GENERIC"] -= len(selected)
        if host._cost_is_affordable(
            seat,
            reduced,
            exclude_sources={card.object_id for card in (*reserved_cards,*selected)},
            spend_context=spend_context,
            **({"snow_required": snow_required} if snow_required else {}),
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
        fixed_refs={row['object_ref'] for row in option.get('fixed_additional_tap_payment',{}).get('objects',())}
        tap_refs = tuple(ref for ref in option.get("selected_tap_cost_cards", ()) if ref not in fixed_refs)
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
                    **cast_snow_payment_kwargs(option),
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
    reserved_cards: Sequence[Any] = (),
) -> tuple[dict[str, Any], list[Any]] | None:
    from .mixed_tap_payment import apply_tap_payments, supports_mixed_tap_payment

    choice_schema: dict[str, Any] = {}
    selected_cards: list[Any] = list(reserved_cards)
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
    if len(delve_mechanics) > 1 or (len(tap_mechanics) > 1 and not supports_mixed_tap_payment(tap_mechanics)):
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
        followed_by_tap_payment=bool(tap_mechanics) or bool(reserved_cards),
        choice_schema=choice_schema,
    ):
        return None
    if not apply_tap_payments(host, seat, option, tap_mechanics, response, hint=hint,
                              spend_context=spend_context, selected_cards=selected_cards, choice_schema=choice_schema):
        return None
    return choice_schema, selected_cards


def _maximum_affordable_x_with_mechanics(
    host: CastCostHost,
    seat: str,
    card: Any,
    program: Any,
    mechanics: Sequence[Mapping[str, Any]],
    *,
    spend_context: Any,
    minimum: int = 0,
    limit: int = 100,
    reserved_cards: Sequence[Any] = (),
    targeted_cost: Mapping[str, Any] | None = None,
) -> int:
    maximum = -1
    for value in range(minimum, limit + 1):
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
            if targeted_cost is not None:
                option['targeted_cost'] = targeted_cost
                from .targeted_costs import apply_target_cost_increase
                apply_target_cost_increase(option)
            _apply_static_reductions(
                host,
                seat,
                card,
                option,
                program=program,
            )
            payment = _apply_payment_mechanics(
                host,
                seat,
                option,
                mechanics,
                {},
                hint=True,
                spend_context=spend_context,
                reserved_cards=reserved_cards,
            )
            if payment is None:
                continue
            _, selected = payment
            if host._cost_is_affordable(
                seat,
                option["requirements"],
                exclude_sources={source.object_id for source in selected},
                spend_context=spend_context,
                **cast_snow_payment_kwargs(option),
            ):
                value_payable = True
                break
        if not value_payable:
            break
        maximum = value
    return maximum



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
    spend_context: Any,
    exclude_sources: set[str],
) -> bool:
    selected_nonmana: list[dict[str, Any]] = []
    selected_refs: set[str] = set()
    for index, raw in enumerate(mandatory_costs):
        additional = dict(raw)
        kind = str(additional.get("kind") or "")
        nonmoving=select_fixed_nonmoving_cost(host,seat,card,option,mandatory_costs,response,
            hint=hint,choice_schema=choice_schema,position=index)
        if nonmoving is not None:
            if not nonmoving[0]:return False
            if nonmoving[1] is not None:selected_nonmana.append(nonmoving[1])
            continue
        try:
            counter_cost = fixed_counter_additional_cost(additional)
        except AdditionalCostError:
            return False
        if counter_cost is not None:
            if len(mandatory_costs) != 1:
                return False
            if counter_cost.schema_version == 2:
                from .blight_costs import payable_blight_cast_candidates

                if option.get("kind") != "additional_alternative":
                    return False
                candidates = list(payable_blight_cast_candidates(
                    host, seat, counter_cost, option["requirements"],
                    spend_context=spend_context, exclude_sources=exclude_sources,
                    **cast_snow_payment_kwargs(option),
                ))
            else:
                candidates = list(fixed_counter_cost_candidates(host, actor=seat, cost=counter_cost))
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
        zone_selection=select_zone_change_additional_cost(host,seat,card,option,mandatory_costs,response,
            position=index,hint=hint,choice_schema=choice_schema)
        if zone_selection is not None:
            valid,selected=zone_selection
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
            from ..random_discard_additional_cost import fixed_life_random_costs
            from ..fixed_payment_groups import fixed_life_zone_costs
            if len(mandatory_costs) != 1 and not (
                    fixed_life_random_costs(mandatory_costs) or fixed_life_zone_costs(mandatory_costs)):
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
    program: Any,
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
    mechanics = bind_granted_payment_mechanics(option, mechanics, response, hint=hint)
    if announced_additional_cost_dependencies({'additional_costs': list(mandatory_costs)}) and mechanics:
        return None
    selected_additional_costs = option.pop(
        "_selected_additional_costs", None
    )
    if selected_additional_costs is not None:
        mandatory_costs = list(selected_additional_costs)
        if option.get("fixed_blight_alternative") and not mandatory_costs and not hint:
            if "counter_cost_card" in response:
                return None
    option_additional_costs = option.pop("_additional_option_costs", ())
    if option_additional_costs:
        mandatory_costs = [*mandatory_costs, *option_additional_costs]
    from .waterbend_costs import add_waterbend_requirements
    add_waterbend_requirements(option,mandatory_costs)
    option["base_requirements"] = host._mana_vector(option["requirements"])
    from .targeted_costs import apply_target_cost_increase
    apply_target_cost_increase(option)
    _apply_static_reductions(
        host,
        seat,
        card,
        option,
        program=program,
        cast_type_line=cast_type_line,
    )
    reduce_emerge_total(host, seat, card, option)
    exile_spec = option.get("exile_from_hand")
    if isinstance(exile_spec, Mapping):
        candidates = host._exile_cost_candidates(seat, card, exile_spec)
        if not candidates:
            return None
        option["exile_candidates"] = candidates
    from .waterbend_costs import finalize_waterbend_cast_option
    return finalize_waterbend_cast_option(host,seat,card,option,program,mandatory_costs,mechanics,response,
        hint=hint,spend_context=spend_context,has_x=has_x,cast_type_line=cast_type_line)


def _finalize_option_payment(host,seat,card,option,program,mandatory_costs,mechanics,response,*,
        hint,spend_context,has_x,cast_type_line,reserved_cards=(),tap_choice_schema=None):
    """Complete the ordinary payment after fixed tap resources are reserved."""
    exile_spec=option.get('exile_from_hand')
    payment = _apply_payment_mechanics(
        host,
        seat,
        option,
        mechanics,
        response,
        hint=hint,
        spend_context=spend_context,
        reserved_cards=reserved_cards,
    )
    if payment is None:
        return None
    choice_schema, selected_cards = payment
    if tap_choice_schema is not None:choice_schema.update(tap_choice_schema)
    from .conspire_costs import conspire_payment_cards
    selected_cards = conspire_payment_cards(host, seat, card, option, selected_cards)
    if selected_cards is None:
        return None
    from .assist_costs import ASSIST_OPTION_FIELD, select_assist_cost
    excluded = {payment_card.object_id for payment_card in selected_cards}
    if option.get(ASSIST_OPTION_FIELD) == 1:
        if not select_assist_cost(host, seat, option, response, hint=hint, choice_schema=choice_schema,
                                  spend_context=spend_context, exclude_sources=excluded):
            return None
    elif not host._cost_is_affordable(
        seat, option["requirements"], exclude_sources=excluded, spend_context=spend_context,
        **cast_snow_payment_kwargs(option),
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
        minimum_x = option.get('library_top_minimum_x', 0)
        maximum = _maximum_affordable_x_with_mechanics(
            host,
            seat,
            card,
            program,
            mechanics,
            spend_context=spend_context,
            minimum=minimum_x,
            reserved_cards=reserved_cards,
            targeted_cost=target_cost_for_x_preview(option),
        )
        from .target_cardinality import target_count_preview_maximum
        maximum = target_count_preview_maximum(option, maximum)
        if maximum < minimum_x:
            return None
        choice_schema["x"] = {"type": "integer", "minimum": minimum_x, "maximum": maximum,
            **({'default': minimum_x} if minimum_x else {})}
    if not _apply_additional_costs(
        host,
        seat,
        card,
        option,
        mandatory_costs,
        response,
        hint=hint,
        choice_schema=choice_schema,
        spend_context=spend_context,
        exclude_sources={payment_card.object_id for payment_card in selected_cards},
    ):
        return None
    if not finalize_emerge_selection(host, seat, card, option, response, choice_schema, hint=hint):
        return None
    from .optional_life_costs import optional_life_payment_affordable
    if not optional_life_payment_affordable(host,seat,option,response):
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
    from .turn_cast_grants import finalize_turn_cast_grant_option
    finalize_turn_cast_grant_option(option)
    from .casualty_costs import finalize_casualty_option
    if not finalize_casualty_option(host,seat,card,option,hint=hint):return None
    from .bargain_costs import finalize_bargain_option
    if not finalize_bargain_option(host,seat,card,option,hint=hint):return None
    return CastCostOption.from_dict(option)


def _cast_cost_options_for_submission(host, seat, card, program, submission, *, hint, force_without_mana_cost,
        alternative_base, cast_type_line, suppress_source_costs, resolving_exile_authority,
        minimum_library_x, source_grant_only=None):
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
        cast_type_line=cast_type_line,
        resolving_exile_authority=resolving_exile_authority,
    )
    if initial is None:
        return ()
    expanded, mandatory, mechanics, has_x = initial
    from .turn_cast_grants import with_turn_cast_grant_choices
    expanded=with_turn_cast_grant_choices(host,seat,card,expanded,cast_type_line=cast_type_line,force_without_mana_cost=force_without_mana_cost)
    from .optional_life_costs import optional_life_cost_variants
    expanded=optional_life_cost_variants(host,seat,card,expanded,cast_type_line=cast_type_line)
    target_mana_x = has_x
    target_schema = getattr(program, "target_schema", None)
    from ...announced_damage import bind_cast_allocation_option, variable_damage_allocation
    allocation = variable_damage_allocation(target_schema)
    if not has_x and isinstance(target_schema, Mapping) and (
            "count_parameter" in target_schema or allocation is not None and allocation.quantity is None):
        _printed, target_mana_x = host._compiled_printed_cost_options(seat, card, x_value=0, hint=True)
        if target_mana_x:
            expanded = [{**option, "x_value_policy": "zero"} for option in expanded]
    declared_cost_x=bool(announced_additional_cost_dependencies(getattr(program,'cost_schema',None))) if not target_mana_x else False
    result = []
    from .face_queries import bind_library_top_cost_option
    for option in expanded:
        option = bind_library_top_cost_option(option, submission, has_x=has_x, minimum=minimum_library_x,
            source_grant_only=source_grant_only)
        if option is None:
            continue
        from .target_cardinality import bind_target_count_option
        option = bind_target_count_option(program, option, submission, has_mana_x=target_mana_x,has_declared_cost_x=declared_cost_x,
            target_limit=len(host.state.cards)+len(host.state.players)+len(host.state.stack))
        if option is None:
            continue
        option = bind_cast_allocation_option(host, seat, card, target_schema, option, submission,
                                             hint=hint, has_x=target_mana_x or declared_cost_x)
        if option is None:
            continue
        option_cast_type_line = (
            str(option["cast_type_line"])
            if option.get("cast_type_line")
            else cast_type_line
        )
        from ..splice import SPLICE_INTERNAL_SELECTION, SPLICE_SELECTION
        if SPLICE_SELECTION in submission or SPLICE_INTERNAL_SELECTION in submission:
            from .splice_costs import apply_splice_declaration
            option = apply_splice_declaration(host, seat, card, program, option, mandatory, submission,
                type_line=option_cast_type_line, hint=hint)
            if option is None:
                continue
        spend_context = host._spell_mana_spend_context(
            str(
                option_cast_type_line
                if option_cast_type_line is not None
                else host._effective_card_data(card).get("type_line") or ""
            )
        )
        from .targeted_costs import target_cost_variants
        from .target_count_costs import target_count_price_variants
        if hint:
            from .target_cardinality import target_count_cost_preview
            option = target_count_cost_preview(host, seat, card, program, option)
        target_options=(target_option for priced in target_count_price_variants(host,seat,card,program,option,submission,hint=hint)
            for target_option in target_cost_variants(host,seat,card,program,priced,submission,hint=hint))
        for target_option in target_options:
            finalized = _finalize_option(
                host,
                seat,
                card,
                target_option,
                program,
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
    from .targeted_costs import consolidate_target_cost_options
    return consolidate_target_cost_options(result,hint=hint)


__all__ = [
    "CastCostHost",
    "build_cast_cost_options",
    "revalidate_convoke_payment",
    "revalidate_delve_payment",
]
