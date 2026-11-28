from __future__ import annotations

"""Source-spanned creature-subtype Offering grammar."""

from . import regex_working_set as re
from ..creature_subtypes import canonical_creature_subtype
from ..offering_model import OFFERING_CAPABILITY, OfferingSpec
from ..semantic_runtime.offering import offering_descriptor
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node


_OFFERING_PREFIX = re.compile(r"^[A-Za-z'-]+(?: [A-Za-z'-]+)? offering\b", re.I)
_OFFERING = re.compile(r"(?P<subtype>[A-Za-z'-]+(?: [A-Za-z'-]+)?) offering\.?", re.I)


def offering_static_node(*, record, node_id, line, material_line, span, capability_registry,
                         capability_profile, residuals):
    if not _OFFERING_PREFIX.match(material_line):
        return None
    match = _OFFERING.fullmatch(material_line)
    subtype = canonical_creature_subtype(match["subtype"]) if match else None
    occurrences = sum(bool(_OFFERING_PREFIX.match(row.strip())) for row in record.oracle_text.splitlines())
    if subtype is None or occurrences != 1 or record.layout != "normal" or record.faces:
        residual = append_residual(residuals, kind="keyword_ability", text=line, span=span,
            reason="One normal card face must declare a canonical creature-subtype Offering",
            blockers=("qualified or repeated Offering and unrepresented cast faces",))
        return OracleNode(node_id=node_id, kind="keyword_ability", text=line, span=span,
                          active_zone="all", event="unresolved", exact=False, lowerable=False,
                          residual_ids=(residual,))
    return runtime_handler_node(
        node_id=node_id, line=line, span=span,
        compiled=("creature-subtype-offering-v1", offering_descriptor(OfferingSpec(subtype)), (OFFERING_CAPABILITY,)),
        kind="keyword_ability", event="cast.cost", active_zone="all", runtime_coverage=("creature_subtype_offering",),
        dependency_reason="Offering requires one announcement-bound sacrifice and its complete price and timing authority",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
