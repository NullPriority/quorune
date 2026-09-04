from __future__ import annotations

"""Closed paired-face lowering for Daybound and Nightbound."""

from typing import Any

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..day_night_model import (
    DAY_NIGHT_CAPABILITY_ID,
    DAY_NIGHT_MECHANIC_ID,
    DayNightBoundMode,
    DayNightBoundSpec,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual
from .oracle_source_text import material_source_lines


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
