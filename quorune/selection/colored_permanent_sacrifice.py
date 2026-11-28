from __future__ import annotations

from ..apnap_object_payment import ORDER_GRAVEYARD_ARRIVALS
from ..errors import GameRuleError
from ..rules.colored_permanent_sacrifice import all_colored_sacrifice_is_closed, all_colored_sacrifice_program_dependencies
from ..semantic_choices.apnap_commit import APNAP_OBJECT_COMMIT_OPERATION


def resolve_all_colored_permanent_sacrifice(host, *, effect, resume):
    item = host.state.stack[-1] if host.state.stack else None
    if (
        item is None or item.ref != resume["stack_ref"] or item.kind not in {"spell", "spell_copy"}
        or item.targets or item.controller not in host.active_seats or item.context.get("currently_resolving") is not True
        or not all_colored_sacrifice_is_closed(effect)
    ):
        raise GameRuleError("All colored permanents require their original resolving spell")
    host._validate_semantic_frame(resume["semantic_frame"], item)
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not all_colored_sacrifice_program_dependencies(program)):
        raise GameRuleError("All colored permanents require their complete admitted program")
    refs = [ref for player in host.apnap_order() for ref in host._choice_options(player, effect)]
    commit = {
        "op": APNAP_OBJECT_COMMIT_OPERATION, "actor": item.controller,
        "object_refs": refs, "expected_zones": ["battlefield"], "destination": "graveyard",
        "reason": "Simultaneous colored permanent sacrifice", "event_code": "choice.sacrifice",
        "message": "Players sacrificed their colored permanents.",
        ORDER_GRAVEYARD_ARRIVALS: True,
    }
    host._continue_resolution(
        stack_ref=item.ref, effects=[commit, *resume["effects"]], destination=resume["destination"], note=resume["note"],
        instruction_pointer=resume["semantic_frame"]["instruction_pointer"],
    )
