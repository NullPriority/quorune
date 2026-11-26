from ..errors import GameRuleError
from ..linked_ability_exile_state import exile_for_linked_ability
from ..rules.linked_hand_mana_model import PAIR, imprint_pair, imprintable_types
from ..linked_library_imprint_model import library_imprint_source
from ..rules.linked_hand_mana import linked_hand_mana_program_dependencies


def apply_linked_hand_imprint(host, effect, *, actor, reason):
    fields = {"op", PAIR, "card", "incarnation", "imprint_stack"}
    if set(effect) - {"reason", "_runtime_source", "_replacement_selections"} != fields:
        raise GameRuleError("Linked hand imprint commit has closed fields")
    item = next((item for item in host.state.stack if item.ref == effect["imprint_stack"]), None)
    try:
        source = library_imprint_source(item, actor=actor)
        program = host.semantics.get(item.semantic_key)
        if program is None or not linked_hand_mana_program_dependencies(program):
            raise ValueError("Linked hand imprint lost its original program")
        pair = imprint_pair(program.effects[0])
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if effect[PAIR] != pair:
        raise GameRuleError("Linked hand imprint changed its ability pair")
    card = host._resolve_object(actor, effect["card"], zones={"hand"}, owned_only=True)
    types, _, _ = host._type_parts(host._effective_card_data(card).get("type_line", ""))
    if card.logical_object_id != effect["incarnation"] or not imprintable_types(types):
        raise GameRuleError("Linked hand imprint lost its selected hand card")
    return exile_for_linked_ability(host, card=card, source=source, ability_pair=pair, reason=reason,
        replacement_selections=tuple(effect.get("_replacement_selections") or ()))
