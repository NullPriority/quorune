from __future__ import annotations

"""Closed compiler grammar for one unmodified Proliferate instruction."""

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping

from ..counter_placement_event_model import PROLIFERATE_ORDER_VERSION_FIELD


@dataclass(frozen=True, slots=True)
class ProliferateEffectTemplate:
    template_id: str = "proliferate-once-v1"
    effects: tuple[Mapping[str, Any], ...] = (
        {"op": "proliferate", PROLIFERATE_ORDER_VERSION_FIELD: 1},
    )
    target_schema: Mapping[str, Any] | None = None
    mechanics: tuple[str, ...] = ("proliferate",)

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def single_proliferate_effect_template(
    text: str,
) -> ProliferateEffectTemplate | None:
    """Lower exactly one ordinary CR 701.34a instruction."""

    normalized = " ".join(text.strip().split())
    if re.fullmatch(
        r"Proliferate, then choose any number of permanents you control that had a counter put on them this way\. "
        r"Those permanents phase out\.",
        normalized, re.IGNORECASE,
    ):
        from ..rules.proliferate_phase_out import proliferate_phase_out_effect

        return ProliferateEffectTemplate(
            template_id="proliferate-then-phase-recipients-v1", effects=(proliferate_phase_out_effect(),),
        )
    if not re.fullmatch(r"proliferate\.?", normalized, re.IGNORECASE):
        return None
    return ProliferateEffectTemplate()


def controlled_arrival_proliferate_activation_result(ability, text, *, public_timing):
    from ..rules.controlled_arrival_proliferate import (
        CONTROLLED_ARRIVAL_PROLIFERATE_MECHANIC, controlled_arrival_proliferate_effect,
        controlled_arrival_proliferate_price_is_closed,
    )

    if (public_timing is not None or " ".join(text.split()).casefold() !=
            "proliferate x times, where x is the number of nontoken creatures you control that entered this turn."
            or not controlled_arrival_proliferate_price_is_closed(ability)):
        return None
    return ("controlled-nontoken-arrival-proliferate-v1", (controlled_arrival_proliferate_effect(),),
            None, (CONTROLLED_ARRIVAL_PROLIFERATE_MECHANIC,))


def controlled_arrival_proliferate_activation_gate(
    *, effects, target_schema, mechanics, active_zone, cost_schema, capability_registry, capability_profile,
):
    from ..rules.controlled_arrival_proliferate import (
        CONTROLLED_ARRIVAL_PROLIFERATE_CAPABILITY, CONTROLLED_ARRIVAL_PROLIFERATE_OPERATION,
        controlled_arrival_proliferate_node_capabilities,
    )
    from .dependency_gate import DependencyGate, explicit_capabilities_gate

    if not any(e.get("op") == CONTROLLED_ARRIVAL_PROLIFERATE_OPERATION for e in effects):
        return None
    required = controlled_arrival_proliferate_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanics, cost_schema=cost_schema,
    )
    if not required or active_zone != "battlefield":
        return DependencyGate(blockers=(f"capability:{CONTROLLED_ARRIVAL_PROLIFERATE_CAPABILITY}",))
    return explicit_capabilities_gate(
        required, capability_registry=capability_registry, capability_profile=capability_profile,
    )


__all__ = [
    "ProliferateEffectTemplate",
    "single_proliferate_effect_template",
]
