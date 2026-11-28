"""Current permanent components that retain a color of mana across step boundaries."""
from ..ability_fragments import StaticComponentSpec
from ..replacement.immutable import FrozenMap

RETENTION_EVENT = "mana.step.retention"
RETENTION_HANDLER = "rule.mana.step-color-retention.v1"
RETENTION_CAPABILITY = "mana.pool.static_color_retention"


def retention_descriptor():
    return {"handler_id":RETENTION_HANDLER,"schema_version":1,"event":RETENTION_EVENT,"color":"R"}


def validate_retention_descriptor(descriptor):
    if (FrozenMap(descriptor)!=FrozenMap(retention_descriptor())
            or type(descriptor["schema_version"]) is not int):
        raise ValueError("Step retention requires the exact static red-mana descriptor")
    return descriptor["color"]


def current_mana_retention(host):
    programs={program.key:program for program in host.semantics.programs_for_event(RETENTION_EVENT)}
    if not programs:
        return {}
    result={}
    for source in host.state.cards.values():
        if source.zone!="battlefield" or source.phased_out or source.controller not in host.active_seats:
            continue
        for fragment in host._effective_ability_fragments(source):
            if not isinstance(fragment,StaticComponentSpec):
                continue
            program=programs.get(fragment.semantic_key)
            if program is None or not host.semantic_program_is_current_trusted(program):
                continue
            for handler in program.handlers:
                if handler.get("handler_id")==RETENTION_HANDLER:
                    result.setdefault(source.controller,set()).add(validate_retention_descriptor(handler))
    return result
