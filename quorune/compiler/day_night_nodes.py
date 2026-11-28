from __future__ import annotations

"""Closed paired-face lowering for Daybound and Nightbound."""

from typing import Any
from . import regex_working_set as re

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..day_night_model import (
    DAY_NIGHT_CAPABILITY_ID,
    DAY_NIGHT_MECHANIC_ID,
    DayNightBoundMode,
    DayNightBoundSpec,
    DAY_NIGHT_DESIGNATION_OPERATION, DAY_NIGHT_DESIGNATION_MECHANIC,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual
from .oracle_source_text import material_source_lines


def fixed_day_night_designation_template(text):
    match=re.fullmatch(r'It becomes (?P<designation>day|night)\.',text.strip(),re.I)
    if match is None:return None
    designation=match['designation'].casefold()
    return 'fixed-day-night-designation-v1', ({'op':DAY_NIGHT_DESIGNATION_OPERATION,'designation':designation},), None, (DAY_NIGHT_DESIGNATION_MECHANIC,)


def _face_keyword_lines(face: Any) -> tuple[str, ...]:
    return tuple(
        material.casefold().rstrip(".")
        for _line, material, _span in material_source_lines(
            str(face.get("oracle_text") or "")
        )
        if material
    )


def _paired_mode(
    record: Any,
    *,
    face_id: str,
    mechanic: str,
) -> DayNightBoundMode | None:
    faces = tuple(getattr(record, "faces", ()))
    if getattr(record, "layout", None) != "transform" or len(faces) != 2:
        return None
    names = tuple(str(face.get("name") or "") for face in faces)
    if any(not name for name in names) or names[0] == names[1]:
        return None
    front_lines = _face_keyword_lines(faces[0])
    back_lines = _face_keyword_lines(faces[1])
    if (
        front_lines.count("daybound") != 1
        or back_lines.count("nightbound") != 1
        or "nightbound" in front_lines
        or "daybound" in back_lines
    ):
        return None
    expected = (
        DayNightBoundMode.DAYBOUND
        if mechanic == "daybound"
        else DayNightBoundMode.NIGHTBOUND
    )
    expected_face = names[0] if expected is DayNightBoundMode.DAYBOUND else names[1]
    return expected if face_id == expected_face else None


def day_night_keyword_node(
    *,
    record: Any,
    face_id: str,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    **_unused: Any,
) -> OracleNode | None:
    """Lower only one canonical keyword on its validated opposite face."""

    if len(mechanics) != 1 or mechanics[0] not in {"daybound", "nightbound"}:
        return None
    mechanic = mechanics[0]
    mode = _paired_mode(record, face_id=face_id, mechanic=mechanic)
    canonical_line = material_line.strip().rstrip(".").casefold() == mechanic
    residual_ids: tuple[str, ...] = ()
    gate = None
    if mode is None or not canonical_line:
        residual_ids = (
            append_residual(
                residuals,
                kind="unsupported_day_night_pairing",
                text=line,
                span=span,
                reason=(
                    "Daybound and Nightbound require one canonical keyword "
                    "on opposite faces of a nonmodal double-faced card"
                ),
                blockers=("paired Daybound/Nightbound transform faces",),
            ),
        )
    else:
        gate = explicit_capability_gate(
            DAY_NIGHT_CAPABILITY_ID,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
        if gate.blockers:
            residual_ids = (
                append_residual(
                    residuals,
                    kind="dependency_contract",
                    text=line,
                    span=span,
                    reason="Daybound/Nightbound requires its typed game-state owner",
                    blockers=gate.blockers,
                ),
            )
    spec = DayNightBoundSpec(
        mode=mode or (
            DayNightBoundMode.DAYBOUND
            if mechanic == "daybound"
            else DayNightBoundMode.NIGHTBOUND
        )
    )
    closure = gate.closure if gate is not None else None
    return OracleNode(
        node_id=node_id,
        kind="keyword_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="continuous",
        lowerable=mode is not None and canonical_line,
        exact=mode is not None and canonical_line and not residual_ids,
        template_id=spec.template_id,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=(mechanic, DAY_NIGHT_MECHANIC_ID),
        residual_ids=residual_ids,
        capability_dependencies=(
            gate.capabilities if gate is not None else (DAY_NIGHT_CAPABILITY_ID,)
        ),
        capability_closure=(closure.reachable if closure is not None else ()),
        capability_profile=(closure.profile if closure is not None else None),
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


__all__ = ["day_night_keyword_node"]


def initial_day_entry_node(*, node_id, line, span, material_line, source_name, capability_registry,
                           capability_profile, residuals):
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..day_night_model import DAY_ENTRY_CAPABILITY, day_entry_descriptor
    from ..rules.source_references import SourceReferenceSpec
    from .static_runtime_nodes import runtime_handler_node

    match = re.fullmatch(r"If it['’]s neither day nor night, it becomes day as (?P<source>.+?) enters\.",
                         material_line, re.IGNORECASE)
    if match is None or not SourceReferenceSpec(source_name).matches(match["source"]):
        return None
    return runtime_handler_node(
        node_id=node_id, line=line, span=span,
        compiled=("intrinsic-entry-initial-day-v1", day_entry_descriptor(), DAY_ENTRY_CAPABILITY),
        kind="replacement_effect", event="zone.change", active_zone="all",
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="Initial day requires the original intrinsic entry replacement.",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )


def day_night_counter_binding(text, *, card_name):
    from ..day_night_model import (
        DAY_TOGGLE_COUNTER_CAPABILITY, DAY_TOGGLE_COUNTER_VARIANT, DAY_TOGGLE_EVENT, day_night_toggle_condition,
    )
    from ..rules.source_references import SourceReferenceSpec
    from .fixed_public_event_trigger_bindings import _spec

    match = re.fullmatch(r"Whenever day becomes night or night becomes day, "
                         r"(?P<body>put a \+1/\+1 counter on (?P<source>.+?)\.)", text, re.IGNORECASE)
    if match is None or not card_name or not SourceReferenceSpec(card_name).matches(match["source"]):
        return None
    return _spec(
        DAY_TOGGLE_EVENT, DAY_TOGGLE_COUNTER_VARIANT, match["body"],
        "day-night-toggle-counter-v1", "trigger-event-normalized-public-action",
        condition=day_night_toggle_condition(), capabilities=(DAY_TOGGLE_COUNTER_CAPABILITY,),
    )
