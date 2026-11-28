"""Recognize the complete fixed controller-scope damage redirection body."""

from ..attachment_references import AttachmentReferenceKind
from ..rules.controller_damage_redirection import (
    MECHANIC, TARGETED, creature_redirection_target_schema, redirection_choice, redirection_instruction,
)

_SCOPE = "all damage that would be dealt this turn to you and permanents you control is dealt to "
_CHOSEN = (
    "choose a creature or planeswalker you control. " + _SCOPE
    + "the chosen permanent instead"
)
_CHOSEN_REMINDER = " (if it's still on the battlefield)"
_ENCHANTED = _SCOPE + "enchanted creature instead."
_TARGETED_HEAD = (
    "choose target creature you control. until end of turn, all damage that would be dealt "
    "to you and creatures you control is dealt to the chosen creature instead"
)
_TARGETED_TAIL = ". when that creature dies this turn, put its counters on up to one target creature you control and draw a card."


def targeted_controller_damage_redirection_template(text):
    normalized = " ".join(text.casefold().split()).replace(" .", ".")
    if normalized not in {_TARGETED_HEAD + _TARGETED_TAIL, _TARGETED_HEAD + _CHOSEN_REMINDER + _TARGETED_TAIL}:
        return None
    return ("targeted-controller-creature-damage-redirection-v1", (redirection_instruction(TARGETED),),
            creature_redirection_target_schema(), (MECHANIC,))


def chosen_controller_damage_redirection_template(text):
    normalized = " ".join(text.casefold().split()).removesuffix(".").rstrip()
    if normalized in {_CHOSEN, _CHOSEN + _CHOSEN_REMINDER}:
        return "controller-choose-damage-redirection-v1", (redirection_choice(),), None, (MECHANIC,)
    return None


def controller_damage_redirection_template(text, *, source_is_permanent, source_attachment_relation):
    normalized = " ".join(text.casefold().split())
    if source_is_permanent is False:
        if spell := chosen_controller_damage_redirection_template(text) or targeted_controller_damage_redirection_template(text):
            return spell
    if source_attachment_relation is AttachmentReferenceKind.ENCHANTED and normalized == _ENCHANTED:
        return "controller-enchanted-damage-redirection-v1", (redirection_instruction(),), None, (MECHANIC,)
    return None
