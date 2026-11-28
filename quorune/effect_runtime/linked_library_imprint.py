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
    from ..linked_library_imprint_model import linked_saga_library_phase, uses_linked_saga_library_instruction
    if len(children) == 1 and uses_linked_saga_library_instruction(children[0]):
        if linked_saga_library_phase(children[0]) != "enter_permanents":
            raise ValueError("Only the linked Saga battlefield branch is optional")
        _source, cards = _linked_saga_library_current_cards(host, item, children[0], item.controller)
        return not any(not card.face_down and "creature" in host._effective_type_parts(card)[0] for card, _ in cards)
    if (len(children) != 1 or not uses_linked_artifact_library_instruction(children[0])
            or children[0].get("op") != LINKED_RETURN_OPERATION):
        return False
    child = linked_artifact_library_choice(wrapper, player=item.controller)
    source, pair = linked_artifact_library_source(host, item, child, actor=item.controller)
    active = set(host.apnap_order())
    return not any(host.state.cards[identity.object_id].owner in active
                   for identity in current_linked_ability_exile(host, source=source, ability_pair=pair))


def _linked_saga_library_current_cards(host, item, instruction, actor):
    from ..linked_library_imprint_model import LINKED_SAGA_LIBRARY_PAIR
    from ..rules.linked_library_imprint import linked_saga_library_source

    source = linked_saga_library_source(host, item, instruction, actor=actor)
    identities = current_linked_ability_exile(host, source=source, ability_pair=LINKED_SAGA_LIBRARY_PAIR)
    return source, tuple((host.state.cards[identity.object_id], identity) for identity in identities)


def _reveal_saga_library_cards(host, cards, *, actor, reason):
    from ..public_quantity_reductions import public_mana_value
    from .life_effects import apply_effect as apply_life_effect

    for card, _identity in cards:
        if reveal_exiled_card(card, viewers=host.seats):
            host._log(
                actor, "card.reveal", f"{actor} turned {card.printed_name} face up in exile.",
                {"player": actor, "object": card.ref, "card": card.printed_name, "reason": reason},
                changed_objects=[card.object_id], changed_players=[actor],
            )
    if any("creature" in host._effective_type_parts(card)[0] for card, _ in cards):
        amount = sum(public_mana_value(host._effective_card_data(card)) for card, _ in cards)
        if amount:
            apply_life_effect(host, {"op": "lose_life", "player": actor, "amount": amount},
                              actor=actor, operation="lose_life", reason=reason)
    return []


def apply_linked_saga_library_result(host, effect, *, actor, operation, reason):
    from ..linked_ability_exile_state import return_linked_ability_exile
    from ..linked_library_imprint_model import LINKED_SAGA_LIBRARY_PAIR, linked_saga_library_phase

    transport_fields = {"_runtime_source", "_replacement_selections", "_aura_target_refs"}
    context = effect.get("_runtime_source")
    stack_ref = context.get("stack_ref") if isinstance(context, Mapping) else None
    item = next((item for item in host.state.stack if item.ref == stack_ref), None)
    instruction = {key: value for key, value in effect.items() if key not in transport_fields}
    try:
        phase = linked_saga_library_phase(instruction)
        source, cards = _linked_saga_library_current_cards(host, item, instruction, actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    selections = effect.get("_replacement_selections", ())
    if phase == "reveal_and_lose_life":
        return _reveal_saga_library_cards(host, cards, actor=actor, reason=reason)
    if phase == "owner_hands":
        return return_linked_ability_exile(
            host, source=source, ability_pair=LINKED_SAGA_LIBRARY_PAIR, actor=actor, reason=reason,
            to_hand=True, owner_order=host.apnap_order(), replacement_selections=selections,
        )
    if phase != "enter_permanents":
        raise GameRuleError("Linked Saga library search cannot use the linked-return mutation path")
    visible = [(host._effective_type_parts(card)[0], identity) for card, identity in cards if not card.face_down]
    if not any("creature" in types for types, _identity in visible):
        return []
    permanent_types = {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
    eligible = [identity.to_dict() for types, identity in visible if permanent_types.intersection(types)]
    return return_linked_exiled_objects(
        host, {"op": "return_linked_exiled_objects", "objects": eligible,
               "spec": LinkedExileReturnSpec(controller="actor").to_dict(),
               **{key: value for key, value in effect.items() if key in transport_fields}},
        actor=actor, operation=operation, reason=reason,
    )
