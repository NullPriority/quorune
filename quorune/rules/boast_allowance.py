from ..replacement.immutable import thaw_value


CAPABILITY = "activation.boast.controlled_twice_on_your_turn"
HANDLER_ID = "permission.activation.boast-twice.v1"
EVENT = "activation.permission"
PRINTED_ALLOWANCE = "creatures you control can boast twice during each of your turns rather than once."


def descriptor():
    return {"handler_id": HANDLER_ID, "schema_version": 1, "event": EVENT}


def _provider_applies(host, provider):
    from ..card_program_faces import program_matches_face
    from ..entry_copy import copiable_oracle_id
    from ..semantic_runtime.self_entry_counters import program_static_component_is_applicable
    from ..semantic_runtime.boast_allowance import BoastAllowanceHandler

    oracle_id = copiable_oracle_id(provider)
    for program in host.semantics.runtime_handler_programs_for_oracle(
            oracle_id, active_zone="battlefield", event=EVENT):
        handlers = tuple(row for row in program.handlers if row.get("handler_id") == HANDLER_ID)
        if not handlers:
            continue
        record = host.card_db.by_oracle_id(oracle_id)
        if (record is None or not host.semantic_program_is_current_trusted(program)
                or not program_matches_face(record, program, provider)
                or not program_static_component_is_applicable(
                    host, program, provider, prospective_zone="battlefield", prospective_controller=provider.controller)):
            continue
        BoastAllowanceHandler().validate(handlers[0])
        return True
    return False


def maximum_boast_activations(host, seat, ability, source):
    if not ability.boast or seat != host.state.active_player or source.controller != seat:
        return 1
    effective = host._effective_card_data(source)
    if "creature" not in host._type_parts(str(effective.get("type_line") or ""))[0]:
        return 1
    return 2 if any(provider.controller == seat and not provider.phased_out and _provider_applies(host, provider)
                    for provider in host._semantic_event_sources(zones={"battlefield"})) else 1


def boast_allowance_program_dependencies(program):
    handlers = tuple(row for row in program.handlers if row.get("handler_id") == HANDLER_ID)
    if not handlers and CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(handlers) != 1 or len(program.handlers) != 1 or thaw_value(handlers[0]) != descriptor()
            or program.event != EVENT or program.active_zone != "battlefield" or program.effects
            or program.target_schema is not None or program.event_condition is not None or program.cost_schema is not None):
        raise ValueError("The second boast permission requires its exact battlefield static component")
    return (CAPABILITY,)
