from __future__ import annotations

"""Compile a printed Splice declaration through existing price owners."""

import re

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..rules.casting_additional_cost_groups import fixed_zone_change_additional_cost_capability
from ..rules.casting_additional_costs import FixedZoneChangeAdditionalCost
from ..rules.splice import SPLICE_CAPABILITY, SPLICE_HANDLER, SpliceSpec
from ..util import mana_cost_to_vector
from .spell_additional_cost_templates import fixed_zone_change_additional_cost_template
from .static_runtime_nodes import runtime_handler_node


_SPLICE = re.compile(
    r"Splice onto (?P<quality>Arcane|instant or sorcery)"
    r"(?: (?P<mana>(?:\{(?:[0-9]+|[WUBRGC])\})+)|\s*—\s*(?P<action>.+?))\.?$",
    re.I,
)


def printed_splice_spec(material: str) -> SpliceSpec | None:
    match = _SPLICE.fullmatch(material.strip())
    if match is None:
        return None
    quality = match["quality"].casefold().replace(" ", "_")
    if match["mana"]:
        requirements, complex_symbols = mana_cost_to_vector(match["mana"].upper())
        return None if complex_symbols else SpliceSpec(quality, mana=requirements)
    action = match["action"]
    text = "As an additional cost to cast this spell, " + action[0].lower() + action[1:] + "."
    cost = fixed_zone_change_additional_cost_template(text, allow_counted=True)
    return SpliceSpec(quality, zone_change=FixedZoneChangeAdditionalCost.from_descriptor(cost.descriptor)) if cost else None


def splice_static_node(*, record, node_id, line, material_line, span, capability_registry,
        capability_profile, residuals):
    if record.layout != "normal" or record.faces or not (record.is_instant or record.is_sorcery):
        return None
    spec = printed_splice_spec(material_line)
    if spec is None:
        return None
    descriptor = {"handler_id": SPLICE_HANDLER, "schema_version": 1, "event": "cast.cost",
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, "splice": spec.to_dict()}
    cost_capability = fixed_zone_change_additional_cost_capability(spec.zone_change) if spec.zone_change else None
    return runtime_handler_node(
        node_id=node_id, line=line, span=span,
        compiled=("fixed-splice-text-v1", descriptor,
            (SPLICE_CAPABILITY, *((cost_capability,) if cost_capability else ()))),
        kind="keyword_ability", event="cast.cost", active_zone="hand",
        runtime_coverage=("typed_splice_text",),
        dependency_reason="Splice requires trusted casting and ordered spell text",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
