"""Complete-source Mutate alternatives for the shared casting cost owner."""

from copy import deepcopy

from ...card_programs.admission import program_has_complete_card_program_admission
from ...card_program_faces import program_matches_face
from ...mutate_model import MutateSpec,MUTATE_HANDLER,MUTATE_PAYMENT_FIELD
from ...semantic_runtime.ability_fragments import default_ability_fragment_registry


def compiled_mutate_specs(host,card):
    record=host.card_record(card)
    if record is None or card.face_down or card.annotations.get('copy_overrides') is not None:
        return ()
    registry=default_ability_fragment_registry();result=[]
    for program in host.semantics.runtime_handler_programs_for_oracle(
            record.oracle_id,active_zone='all',event='characteristics.evaluate'):
        if (not host.semantic_program_is_current_trusted(program)
                or not program_has_complete_card_program_admission(program)
                or not program_matches_face(record,program,card)):
            continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id')==MUTATE_HANDLER:
                result.extend(spec for spec in registry.lower(descriptor,None) if isinstance(spec,MutateSpec))
    return tuple(result)


def with_mutate_cost_schema(host,card,schema,*,suppress_source_costs):
    result=deepcopy(dict(schema))
    if suppress_source_costs:return result
    specs=compiled_mutate_specs(host,card)
    alternatives=list(result.get('alternate_costs',()))
    if any(MUTATE_PAYMENT_FIELD in value for value in alternatives):
        return None
    if len(specs)>1:
        return None
    if not specs:return result
    alternatives.extend(specs[0].cast_cost_options())
    result['alternate_costs']=alternatives
    return result
