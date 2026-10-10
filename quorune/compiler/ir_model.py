from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


_STATUS_FIELD = "status"


@dataclass(frozen=True, slots=True)
class SourceSpan:
    start: int
    end: int
    line: int


@dataclass(frozen=True, slots=True)
class OracleResidual:
    residual_id: str
    kind: str
    text: str
    span: SourceSpan
    material: bool
    reason: str
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["span"] = asdict(self.span)
        value["blockers"] = list(self.blockers)
        return value


def append_residual(
    residuals: list[OracleResidual],
    *,
    kind: str,
    text: str,
    span: SourceSpan,
    reason: str,
    blockers: Sequence[str] = (),
) -> str:
    """Append one canonical material residual and return its stable face ID."""

    residual_id = f"r{len(residuals) + 1}"
    residuals.append(
        OracleResidual(
            residual_id=residual_id,
            kind=kind,
            text=text,
            span=span,
            material=True,
            reason=reason,
            blockers=tuple(blockers),
        )
    )
    return residual_id


@dataclass(frozen=True, slots=True)
class OracleNode:
    node_id: str
    kind: str
    text: str
    span: SourceSpan
    active_zone: str
    event: str
    lowerable: bool
    exact: bool
    template_id: str | None = None
    cost: Mapping[str, Any] | None = None
    effects: tuple[Mapping[str, Any], ...] = ()
    handlers: tuple[Mapping[str, Any], ...] = ()
    target_schema: Mapping[str, Any] | None = None
    event_condition: Mapping[str, Any] | None = None
    trigger_limit: Mapping[str, Any] | None = None
    runtime_coverage: tuple[str, ...] = ()
    mechanics: tuple[str, ...] = ()
    residual_ids: tuple[str, ...] = ()
    capability_dependencies: tuple[str, ...] = ()
    capability_closure: tuple[str, ...] = ()
    capability_profile: str | None = None
    capability_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "node_id": self.node_id,
            "kind": self.kind,
            "text": self.text,
            "span": asdict(self.span),
            "active_zone": self.active_zone,
            "event": self.event,
            "lowerable": self.lowerable,
            "exact": self.exact,
            "template_id": self.template_id,
            "cost": dict(self.cost) if self.cost is not None else None,
            "effects": [dict(effect) for effect in self.effects],
            "handlers": [dict(handler) for handler in self.handlers],
            "target_schema": (
                dict(self.target_schema)
                if self.target_schema is not None
                else None
            ),
            "event_condition": (
                dict(self.event_condition)
                if self.event_condition is not None
                else None
            ),
            "mechanics": list(self.mechanics),
            "residual_ids": list(self.residual_ids),
        }
        if self.runtime_coverage:
            value["runtime_coverage"] = list(self.runtime_coverage)
        if self.trigger_limit is not None:
            value["trigger_limit"] = dict(self.trigger_limit)
        if self.capability_dependencies:
            value["capability_dependencies"] = list(
                self.capability_dependencies
            )
            value["capability_closure"] = list(self.capability_closure)
            value["capability_profile"] = self.capability_profile
            value["capability_fingerprint"] = self.capability_fingerprint
        return value


@dataclass(frozen=True, slots=True)
class OracleFaceIR:
    face_id: str
    face_name: str
    oracle_text: str
    nodes: tuple[OracleNode, ...]
    residuals: tuple[OracleResidual, ...]

    @property
    def exact(self) -> bool:
        return not any(value.material for value in self.residuals) and all(
            node.exact for node in self.nodes
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "face_id": self.face_id,
            "face_name": self.face_name,
            "oracle_text": self.oracle_text,
            "exact": self.exact,
            "nodes": [node.to_dict() for node in self.nodes],
            "residuals": [
                residual.to_dict() for residual in self.residuals
            ],
        }


@dataclass(frozen=True, slots=True)
class OracleCardIR:
    oracle_id: str
    card_name: str
    schema_version: int
    compiler_version: str
    oracle_hash: str
    faces: tuple[OracleFaceIR, ...]
    semantic_hash: str

    @property
    def material_residuals(self) -> tuple[OracleResidual, ...]:
        return tuple(
            residual
            for face in self.faces
            for residual in face.residuals
            if residual.material
        )

    @property
    def status(self) -> str:
        if not self.material_residuals and all(
            face.exact for face in self.faces
        ):
            return "exact"
        if any(
            node.lowerable for face in self.faces for node in face.nodes
        ):
            return "partial"
        return "unresolved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "oracle_id": self.oracle_id,
            "card_name": self.card_name,
            "schema_version": self.schema_version,
            "compiler_version": self.compiler_version,
            "oracle_hash": self.oracle_hash,
            "semantic_hash": self.semantic_hash,
            _STATUS_FIELD: self.status,
            "material_residual_count": len(self.material_residuals),
            "faces": [face.to_dict() for face in self.faces],
        }


__all__ = [
    "OracleCardIR",
    "OracleFaceIR",
    "OracleNode",
    "OracleResidual",
    "SourceSpan",
    "append_residual",
]
