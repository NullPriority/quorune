"""Resolve the original death trigger bound to current private-library exiles."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..linked_ability_exile_state import current_linked_ability_exile
from ..linked_exile_return import return_linked_exiled_objects
from ..linked_exile_return_model import LinkedExileReturnSpec
from ..linked_library_imprint_model import LIBRARY_IMPRINT_PAIR, library_imprint_phase, library_imprint_source
from ..zone_object_state import reveal_exiled_card


def apply_linked_library_imprint(host, effect, *, actor, operation, reason):
    """Reveal first, then make one ordinary entry batch for the revealed creatures."""
    source_context = effect.get("_runtime_source")
    stack_ref = source_context.get("stack_ref") if isinstance(source_context, Mapping) else None
    item = next((item for item in host.state.stack if item.ref == stack_ref), None)
    instruction = {key: value for key, value in effect.items()
                   if key not in {"_runtime_source", "_replacement_selections"}}
    try:
        phase = library_imprint_phase(instruction)
        source = library_imprint_source(item, actor=actor)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    identities = current_linked_ability_exile(host, source=source, ability_pair=LIBRARY_IMPRINT_PAIR)
    if phase == "reveal":
        for identity in identities:
            card = host.state.cards[identity.object_id]
            if reveal_exiled_card(card, viewers=host.seats):
                host._log(
                    actor, "card.reveal", f"{actor} turned {card.printed_name} face up in exile.",
                    {"player": actor, "object": card.ref, "card": card.printed_name, "reason": reason},
                    changed_objects=[card.object_id], changed_players=[actor],
                )
        return []
    eligible = []
    for identity in identities:
        card = host.state.cards[identity.object_id]
        if card.face_down:
            raise GameRuleError("Private library imprint must reveal its original exile before creature entry")
        if "creature" in host._effective_type_parts(card)[0]:
            eligible.append(identity.to_dict())
    return return_linked_exiled_objects(
        host, {"op": "return_linked_exiled_objects", "objects": eligible,
               "spec": LinkedExileReturnSpec(controller="actor").to_dict(),
               "_runtime_source": source_context,
               "_replacement_selections": effect.get("_replacement_selections", ())},
        actor=actor, operation=operation, reason=reason,
    )


def apply_linked_artifact_owner_return(host, effect, *, actor, reason):
    from ..linked_ability_exile_state import return_linked_ability_exile
    from ..rules.linked_library_imprint import linked_artifact_library_source

    resolution = effect.get("_runtime_source")
    stack_ref = resolution.get("stack_ref") if isinstance(resolution, Mapping) else None
    item = next((entry for entry in host.state.stack if entry.ref == stack_ref), None)
    instruction = {key: value for key, value in effect.items()
                   if key not in {"_runtime_source", "_replacement_selections"}}
    try:
        source, pair = linked_artifact_library_source(host, item, instruction, actor=actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    return return_linked_ability_exile(
        host, source=source, ability_pair=pair, actor=actor, reason=reason, to_hand=True,
        owner_order=host.apnap_order(), replacement_selections=tuple(effect.get("_replacement_selections") or ()),
    )


def unavailable_optional_linked_artifact_return(host, item, wrapper):
    from ..linked_library_imprint_model import linked_artifact_library_choice, uses_linked_artifact_library_instruction
    from ..linked_exile_return_model import LINKED_RETURN_OPERATION
    from ..rules.linked_library_imprint import linked_artifact_library_source

    children = wrapper.get("effects", ())
    if (len(children) != 1 or not uses_linked_artifact_library_instruction(children[0])
            or children[0].get("op") != LINKED_RETURN_OPERATION):
        return False
    child = linked_artifact_library_choice(wrapper, player=item.controller)
    source, pair = linked_artifact_library_source(host, item, child, actor=item.controller)
    active = set(host.apnap_order())
    return not any(host.state.cards[identity.object_id].owner in active
                   for identity in current_linked_ability_exile(host, source=source, ability_pair=pair))
