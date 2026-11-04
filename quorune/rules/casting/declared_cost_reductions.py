"""Apply server-announced linked discounts while determining the total cost."""
from ...card_programs.admission import program_has_complete_card_program_admission
from ...declared_cast_cost_model import DECLARED_CAST_COSTS_FIELD
from ...declared_cost_reductions import DECLARED_COST_REDUCTION_EVENT, DECLARED_COST_REDUCTION_HANDLER
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry


def apply_declared_cost_reductions(host, card, option, *, face_down_method=None):
    if face_down_method is not None:
        return
    registry=default_cast_cost_component_registry()
    reduction=0
    for program in host.semantics.runtime_handler_programs_for_oracle(
            card.oracle_id, active_zone='all', event=DECLARED_COST_REDUCTION_EVENT):
        if (not host.semantic_program_is_current_trusted(program)
                or not program_has_complete_card_program_admission(program)):
            continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') != DECLARED_COST_REDUCTION_HANDLER:
                continue
            spec=registry.lower(descriptor,None)[0]
            reduction+=spec.reduction_for(option.get(DECLARED_CAST_COSTS_FIELD))
    if reduction:
        applied=min(option['requirements']['GENERIC'],reduction)
        option['requirements']['GENERIC']-=applied
        option.setdefault('cost_reductions',[]).append({'kind':'self_declared_cost','count':applied})
