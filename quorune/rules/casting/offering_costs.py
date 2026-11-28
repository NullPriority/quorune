from __future__ import annotations

"""Price and bind Offering through the ordinary cast and sacrifice owners."""

from collections.abc import Mapping
from copy import deepcopy

from ...additional_cost_vocabulary import SACRIFICE_ONE_COST
from ...card_programs.admission import program_has_complete_card_program_admission
from ...cast_timing import CastTimingPermission, cast_timing_is_legal
from ...object_predicate import ObjectQuerySpec
from ...offering_model import (
    MANA_KEYS, OFFERING_FIELD, OFFERING_HANDLER, OfferingSpec, offering_reduction_vectors, reduce_offering_total,
)
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry
from ..casting_additional_costs import FixedZoneChangeAdditionalCost, fixed_zone_change_cost_candidates


def compiled_offering_spec(host, card) -> OfferingSpec | None:
    record = host.card_record(card)
    if record is None or record.layout != "normal" or record.faces or card.annotations.get("copy_overrides") is not None:
        return None
    registry = default_cast_cost_component_registry()
    declarations = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id, active_zone="all", event="cast.cost"):
        descriptors = [row for row in program.handlers if row.get("handler_id") == OFFERING_HANDLER]
        if not descriptors:
            continue
        if (not host.semantic_program_is_current_trusted(program)
                or not program_has_complete_card_program_admission(program)):
            return None
        for descriptor in descriptors:
            declarations.extend(registry.lower(descriptor, None))
    return declarations[0] if len(declarations) == 1 else None


def offering_sacrifice_cost(spec: OfferingSpec) -> FixedZoneChangeAdditionalCost:
    return FixedZoneChangeAdditionalCost(
        operation=SACRIFICE_ONE_COST, choice_field="sacrifice_cards",
        predicate=ObjectQuerySpec(zones=("battlefield",), controller="$actor", known_to_actor=True,
                                  subtypes_all=(spec.subtype,)),
    )


def _sacrifice_candidates(host, actor, card, cost):
    return fixed_zone_change_cost_candidates(host, actor=actor, cost=cost, exclude_object_id=card.object_id)


def _credits(host, victim):
    return offering_reduction_vectors(host._effective_card_data(victim).get("mana_cost"))


def _offering_choices(host, actor, card, cost):
    candidates = _sacrifice_candidates(host, actor, card, cost)
    for ref in candidates:
        victim = host._resolve_object(actor, ref, zones={"battlefield"}, controlled_only=True)
        try:
            credits = _credits(host, victim)
        except ValueError:
            continue
        for index, credit in enumerate(credits):
            yield index, victim, credit


def offering_timing_permissions(host, actor, card) -> tuple[CastTimingPermission, ...]:
    spec = compiled_offering_spec(host, card)
    if spec is None:
        return ()
    choices = _offering_choices(host, actor, card, offering_sacrifice_cost(spec))
    return (CastTimingPermission(),) if next(choices, None) is not None else ()


def offering_cost_variants(host, seat, card, options, *, suppress_source_costs, ignore_timing):
    if suppress_source_costs:
        return options
    spec = compiled_offering_spec(host, card)
    if spec is None:
        return options
    from .proposal import _combined_cast_timing_permissions

    record = host.card_record(card)
    face = host._select_cast_face(record, card.active_face)
    independent = _combined_cast_timing_permissions(
        host, seat, card, face=face, face_name=str(face.get("name") or "") if face else None,
        face_down=False, include_offering=False,
    )
    type_line = str(face.get("type_line") or "") if face else record.type_line
    result = list(options) if ignore_timing or cast_timing_is_legal(host.state, seat, type_line, independent) else []
    cost = offering_sacrifice_cost(spec)
    choices = tuple(_offering_choices(host, seat, card, cost))
    for raw in options:
        for index, victim, credit in choices:
            option = deepcopy(raw)
            option.update(
                id=f'{raw["id"]}+offering:{victim.ref}:{victim.zone_change_counter}:{index}',
                kind="optional_additional",
                label=f"Offering by sacrificing {victim.printed_name}",
            )
            option[OFFERING_FIELD] = {
                "spec": spec.to_dict(), "object_id": victim.object_id, "ref": victim.ref,
                "zone_change_counter": victim.zone_change_counter, "reduction": dict(credit),
            }
            option["_additional_option_costs"] = [*option.get("_additional_option_costs", ()), cost.to_descriptor()]
            result.append(option)
    return result


def _selected_offering(host, seat, card, option, *, check_credit):
    value = option.get(OFFERING_FIELD)
    if value is None:
        return None
    if (not isinstance(value, Mapping)
            or set(value) != {"spec", "object_id", "ref", "zone_change_counter", "reduction"}
            or type(value["object_id"]) is not str or type(value["ref"]) is not str
            or type(value["zone_change_counter"]) is not int or value["zone_change_counter"] < 0):
        raise ValueError("Offering requires its exact published sacrifice choice")
    spec = OfferingSpec.from_dict(value["spec"])
    if compiled_offering_spec(host, card) != spec:
        raise ValueError("Offering declaration is unavailable")
    victim = host.state.cards.get(value["object_id"])
    legal = _sacrifice_candidates(host, seat, card, offering_sacrifice_cost(spec))
    if (victim is None or victim.ref != value["ref"] or victim.zone_change_counter != value["zone_change_counter"]
            or victim.ref not in legal):
        raise ValueError("The selected Offering sacrifice is no longer available")
    if check_credit and value["reduction"] not in _credits(host, victim):
        raise ValueError("The Offering reduction changed before total-cost calculation")
    return value


def reduce_offering_cast_total(host, seat, card, option):
    selected = _selected_offering(host, seat, card, option, check_credit=True)
    if selected is None:
        return
    before = option["requirements"]
    requirements = reduce_offering_total(before, selected["reduction"])
    option["requirements"] = requirements
    option.setdefault("cost_reductions", []).append(
        {"kind": "offering", "count": sum(before[key] - requirements[key] for key in MANA_KEYS)},
    )


def finalize_offering_selection(host, seat, card, option, response, choice_schema, *, hint):
    selected = _selected_offering(host, seat, card, option, check_credit=True)
    if selected is None:
        return True
    refs = response.get("sacrifice_cards", response.get("cost_cards"))
    if not hint and (not isinstance(refs, (list, tuple)) or list(refs) != [selected["ref"]]):
        return False
    schema = choice_schema.get("sacrifice_cards")
    if not isinstance(schema, dict) or selected["ref"] not in schema.get("legal_refs", ()):
        return False
    schema["legal_refs"] = [selected["ref"]]
    return True


def validate_offering_after_mana_payment(host, seat, card, option):
    # CR 601.2f has sealed the amount. The payment must still use its original permanent.
    _selected_offering(host, seat, card, option, check_credit=False)
