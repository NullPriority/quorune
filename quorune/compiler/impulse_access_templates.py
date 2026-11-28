from __future__ import annotations

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping

from ..impulse_access_model import ImpulseAccessDuration
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


IMPULSE_ACCESS_CAPABILITY_ID = "zone.impulse_access.fixed"
IMPULSE_ACCESS_MECHANIC_ID = "fixed-impulse-access"
_FIXED_IMPULSE_ACCESS = re.compile(
    rf"^Exile the top (?:(?P<single>card)|(?P<count>{FIXED_COUNT_PATTERN}) cards) "
    rf"of your library\. "
    rf"(?:(?P<prefix>Until (?:(?:the )?end of (?:(?:this|your next) turn|turn)|your next end step)), "
    rf"you may play (?P<prefix_ref>that card|those cards|it|them)|"
    rf"You may play (?P<suffix_ref>that card|those cards|it|them) "
    rf"(?P<suffix>this turn|until (?:the )?end of your next turn|until your next end step))\.?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FixedImpulseAccessTemplate:
    count: int
    duration: str
    spawn_settlement: bool = False

    @property
    def template_id(self) -> str:
        if self.spawn_settlement:
            return "fixed-impulse-spawn-settlement-v13"
        suffix = {ImpulseAccessDuration.END_OF_NEXT_TURN.value: "next-turn",
                  ImpulseAccessDuration.NEXT_END_STEP.value: "next-end-step"}.get(self.duration, "turn")
        return f"fixed-impulse-access-{suffix}-v1"

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        if self.spawn_settlement:
            from ..impulse_access_model import spawn_settlement_impulse_effect

            return self.template_id, (spawn_settlement_impulse_effect(),), None, (IMPULSE_ACCESS_MECHANIC_ID,)
        return (
            self.template_id,
            (
                {
                    "op": "fixed_impulse_access",
                    "player": "$controller",
                    "count": self.count,
                    "duration": self.duration,
                },
            ),
            None,
            (IMPULSE_ACCESS_MECHANIC_ID,),
        )


def fixed_impulse_access_effect_template(
    text: str,
) -> FixedImpulseAccessTemplate | None:
    """Lower only fixed own-library exile with unrestricted play access."""

    material = " ".join(text.strip().split())
    if material.casefold() == (
        "exile the top three cards of your library. you may play those cards this turn. "
        "at the beginning of the next end step, if any of those cards remain exiled, put them into your graveyard, "
        "then create a 0/1 colorless eldrazi spawn creature token for each card put into your graveyard this way. "
        'those tokens have "sacrifice this token: add {c}."'
    ):
        return FixedImpulseAccessTemplate(3, ImpulseAccessDuration.END_OF_TURN.value, spawn_settlement=True)
    match = _FIXED_IMPULSE_ACCESS.fullmatch(material)
    if match is None:
        return None
    count = 1 if match.group("single") else fixed_number(match.group("count"))
    reference = str(match.group("prefix_ref") or match.group("suffix_ref")).casefold()
    if (count == 1) != (reference in {"that card", "it"}):
        return None
    duration_text = str(match.group("prefix") or match.group("suffix")).casefold()
    if "next end step" in duration_text:
        duration = ImpulseAccessDuration.NEXT_END_STEP
    elif "next turn" in duration_text:
        duration = ImpulseAccessDuration.END_OF_NEXT_TURN
    else:
        duration = ImpulseAccessDuration.END_OF_TURN
    return FixedImpulseAccessTemplate(count=count, duration=duration.value)


def shuffled_top_free_play_activation(ability, text, *, public_timing):
    if " ".join(text.casefold().split()) != (
        "shuffle your library, then exile the top card. until end of turn, "
        "you may play that card without paying its mana cost."
    ):
        return None
    if ability.zones != ("battlefield",) or ability.mana_ability or ability.sorcery_speed or ability.activation_conditions:
        return None

    from ..library_order_model import SHUFFLE_MECHANIC
    from ..rules.impulse_access_capability_shapes import (
        SHUFFLED_TOP_FREE_PLAY_MECHANIC, shuffled_top_free_play_effects, shuffled_top_free_play_price_is_closed,
    )
    from .activated_costs import activated_ability_cost

    if not shuffled_top_free_play_price_is_closed(activated_ability_cost(ability, public_timing=public_timing)):
        return None
    return (
        "shuffle-top-free-play-activation-v1", shuffled_top_free_play_effects(), None,
        (SHUFFLED_TOP_FREE_PLAY_MECHANIC, SHUFFLE_MECHANIC, IMPULSE_ACCESS_MECHANIC_ID),
    )


def shuffled_top_free_play_activation_gate(**scope):
    from ..rules.impulse_access_capability_shapes import (
        SHUFFLED_TOP_FREE_PLAY_CAPABILITY, SHUFFLED_TOP_FREE_PLAY_MECHANIC,
        shuffled_top_free_play_activation_dependencies,
    )
    from .dependency_gate import DependencyGate, explicit_capabilities_gate

    if SHUFFLED_TOP_FREE_PLAY_MECHANIC not in scope["mechanics"]:
        return None
    required = shuffled_top_free_play_activation_dependencies(
        scope["effects"], scope["target_schema"], scope["active_zone"], scope["cost_schema"],
    )
    if not required:
        return DependencyGate(blockers=(f"capability:{SHUFFLED_TOP_FREE_PLAY_CAPABILITY}",))
    return explicit_capabilities_gate(
        required, capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
    )


__all__ = [
    "fixed_impulse_access_effect_template",
    "FixedImpulseAccessTemplate",
    "IMPULSE_ACCESS_CAPABILITY_ID",
    "IMPULSE_ACCESS_MECHANIC_ID",
]
