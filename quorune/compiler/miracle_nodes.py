from __future__ import annotations

"""Source-spanned fixed-mana Miracle declarations and linked triggers."""

import re
from typing import Any

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..drawing.miracle_model import (
    FixedMiracleCost, MIRACLE_CAPABILITY_ID, MIRACLE_REVEAL_EVENT,
    MIRACLE_TRIGGER_EVENT, MIRACLE_CHOICE_OPERATION,
)
from ..semantic_runtime.intrinsic_draw_reveals import (
    miracle_reveal_descriptor, miracle_trigger_descriptor,
)
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


_FIXED_MIRACLE = re.compile(
    r"^Miracle (?P<cost>(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+)"
    r"(?: \(You may cast this card for its miracle cost when you draw it "
    r"if it's the first card you drew this turn\.\))?\.?$",
    re.IGNORECASE,
)


def miracle_keyword_nodes(
    *, node_id, line, material_line, span, mechanics, record, face_id,
    capability_registry, capability_profile, residuals, **_unused: Any,
) -> tuple[OracleNode, ...] | None:
    if mechanics != ("miracle",):
        return None
    match = _FIXED_MIRACLE.fullmatch(" ".join(line.strip().split()))
    if match is None or record.layout != "normal" or face_id != "front":
        return None
    if "land" in record.type_line.casefold().replace("—", "-").split("-", 1)[0].split():
        return None
    cost = FixedMiracleCost.from_cost_text(match["cost"].upper())
    gate = explicit_capabilities_gate(
        (MIRACLE_CAPABILITY_ID, "zone.draw.library_to_hand", "zone.draw.reveal_as_drawn",
         "trigger.placement.apnap"),
        capability_registry=capability_registry, capability_profile=capability_profile,
    )
    residual_ids = (append_residual(
        residuals, kind="dependency_contract", text=line, span=span,
        reason="Miracle requires the complete reveal, linked-trigger and casting lifecycle",
        blockers=gate.blockers,
    ),) if gate.blockers else ()
    closure = gate.closure
    common = dict(
        text=line, span=span, lowerable=True, exact=not residual_ids,
        mechanics=("miracle",), residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=closure.fingerprint if closure is not None else None,
    )
    return (
        OracleNode(
            node_id=f"{node_id}:reveal", kind="keyword_ability", active_zone="all",
            event=MIRACLE_REVEAL_EVENT, template_id="miracle-fixed-mana-reveal-v1",
            handlers=(miracle_reveal_descriptor(cost),),
            runtime_coverage=("miracle_intrinsic_as_drawn_reveal", CURRENT_ABILITY_FRAGMENT_COVERAGE),
            **common,
        ),
        OracleNode(
            node_id=f"{node_id}:trigger", kind="triggered_ability", active_zone="hand",
            event=MIRACLE_TRIGGER_EVENT, template_id="miracle-linked-cast-trigger-v1",
            handlers=(miracle_trigger_descriptor(cost),),
            effects=({"op": MIRACLE_CHOICE_OPERATION, "cost": cost.to_dict()},),
            runtime_coverage=("miracle_linked_cast_window",), **common,
        ),
    )
