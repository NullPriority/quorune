"""Keep the printed own-combat restriction with its exact Entwine mode set."""

from ..extra_combat_model import EXTRA_COMBAT_MECHANIC, ExtraCombatUntap, extra_combat_effect
from .continuous_templates import controlled_characteristic_until_end_of_turn_effect
from .self_cast_restriction_nodes import self_cast_restriction_nodes


_OWN_COMBAT = "cast this spell only during combat on your turn."
_DOUBLE_STRIKE = "creatures you control gain double strike until end of turn."
_UNTAP_AND_COMBAT = (
    "untap all creatures you control. after this phase, there is an additional combat phase."
)


def split_own_combat_modal(
    rows, *, face_id, source_name, effect_template, capability_registry, capability_profile,
):
    if tuple(material.casefold() for _, material, _ in rows) != (
        _OWN_COMBAT, "choose one —", "• " + _DOUBLE_STRIKE, "• " + _UNTAP_AND_COMBAT, "entwine {1}{r}",
    ):
        return None
    line, material, span = rows[0]
    restriction = self_cast_restriction_nodes(
        node_id=f"{face_id}:n1", line=line, material_line=material, span=span, source_name=source_name,
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=[],
    )
    if not restriction or not restriction[0].exact:
        return None

    def compiled_mode(text, **kwargs):
        if text.casefold() == _UNTAP_AND_COMBAT:
            return (
                "untap-controlled-creatures-extra-combat-v1",
                (extra_combat_effect(ExtraCombatUntap.CONTROLLED_CREATURES),),
                None,
                (EXTRA_COMBAT_MECHANIC,),
            )
        if text.casefold() == _DOUBLE_STRIKE:
            compiled = controlled_characteristic_until_end_of_turn_effect(text)
            if compiled is not None:
                template_id, effects, mechanics = compiled
                return template_id, effects, None, mechanics
        return effect_template(text, **kwargs)

    return rows[1:], restriction, compiled_mode
