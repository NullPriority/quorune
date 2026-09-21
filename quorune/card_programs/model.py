from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence, TYPE_CHECKING

from ..rules.event_subscriptions import FixedEventSubscriptionSet
from ..replacement.immutable import thaw_value
from ..util import stable_json
from .trust import build_program_trust_closure

if TYPE_CHECKING:
    from ..semantics import SemanticProgram


CARD_PROGRAM_SCHEMA_VERSION = 2
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROGRAM_FIELDS = {
    "schema_version",
    "compiler_version",
    "oracle_id",
    "card_identity",
    "faces",
    "oracle_source_hash",
    "rulings_source_hash",
    "abilities",
    "capability_dependencies",
    "trust_closure",
    "semantic_hash",
    "residuals",
    "provenance",
    "fingerprint",
}
_FACE_FIELDS = {"face_id", "name", "type_line", "oracle_text_hash"}
_ABILITY_FIELDS = {
    "semantic_key",
    "ability_id",
    "face_id",
    "kind",
    "label",
    "active_zones",
    "timing_permissions",
    "costs",
    "modes",
    "targets",
    "choices",
    "effect_nodes",
    "triggers",
    "static_effects",
    "replacement_effects",
    "prevention_effects",
    "continuous_effects",
    "linked_ability_ids",
    "durations",
    "delayed_effects",
    "copy_behavior",
    "zone_permissions",
    "capability_dependencies",
    "trust_closure",
    "source_span",
    "residual_ids",
    "runtime",
}
_RUNTIME_FIELDS = {
    "destination",
    "requires_arbiter",
    "notes",
    "version",
    "semantic_schema_version",
    "trust_level",
    "provenance",
    "tests",
    "handlers",
    "event_condition",
    "coverage",
}


class CardProgramError(ValueError):
    """A CardProgram is malformed, inconsistent, or fingerprint-mismatched."""


def _hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _exact_fields(
    value: Mapping[str, Any],
    expected: set[str],
    *,
    field: str,
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise CardProgramError(
            f"{field} is missing required fields: {', '.join(missing)}"
        )
    if unknown:
        raise CardProgramError(
            f"{field} has unknown fields: {', '.join(unknown)}"
        )


def _nonempty(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CardProgramError(f"{field} must be a nonempty string")
    return value


def _sha(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CardProgramError(f"{field} must be a lowercase SHA-256")
    return value


def _strings(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise CardProgramError(f"{field} must be a list of nonempty strings")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise CardProgramError(f"{field} must contain unique values")
    return result


def _objects(value: Any, *, field: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, Mapping) for item in value
    ):
        raise CardProgramError(f"{field} must be a list of objects")
    return tuple(_clone(dict(item)) for item in value)


def _ability_kind(ability_id: str, event: str) -> str:
    prefix = ability_id.partition(":")[0].casefold()
    if prefix in {
        "spell",
        "ability",
        "trigger",
        "static",
        "replacement",
        "prevention",
    }:
        return {
            "ability": "activated",
            "trigger": "triggered",
            "spell": "spell",
            "static": "static",
            "replacement": "replacement",
            "prevention": "prevention",
        }[prefix]
    if event not in {"resolve", "cast", "activate"}:
        return "triggered"
    return "ability"


def _ability_face_id(program: "SemanticProgram") -> str:
    explicit = str(program.provenance.get("face_id") or "").strip()
    if explicit:
        return explicit
    parts = program.ability_id.split(":")
    if len(parts) > 1 and parts[1] in {"front", "back"}:
        return parts[1]
    return "front"


def _modes(targets: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    definitions = (
        targets.get("modes") if isinstance(targets, Mapping) else None
    )
    if not isinstance(definitions, Mapping):
        return []
    return [
        {"mode_id": str(mode_id), "definition": _clone(definition)}
        for mode_id, definition in sorted(definitions.items())
    ]


def _choice_nodes(effects: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    markers = ("choose", "search", "select", "vote", "name_card")
    return [
        {"effect_index": index, "operation": str(effect.get("op") or "")}
        for index, effect in enumerate(effects)
        if any(marker in str(effect.get("op") or "") for marker in markers)
        or any(key in effect for key in ("choice", "choices", "prompt"))
    ]


def _classified_effects(
    program: "SemanticProgram",
) -> dict[str, list[dict[str, Any]]]:
    effects = [dict(effect) for effect in program.effects]
    coverage = {str(value).casefold() for value in program.coverage}
    event = str(program.event)
    trigger = _ability_kind(program.ability_id, event) == "triggered"
    replacement = (
        "replacement" in event.casefold()
        or any("replacement" in value for value in coverage)
    )
    prevention = any(
        "prevent" in str(effect.get("op") or "").casefold()
        for effect in effects
    ) or any("prevention" in value for value in coverage)
    static = any("static" in value for value in coverage)
    continuous = static or any(
        "continuous" in value or "layer" in value for value in coverage
    )
    delayed = [
        {"effect_index": index, "node": _clone(effect)}
        for index, effect in enumerate(effects)
        if "delayed" in str(effect.get("op") or "").casefold()
    ]
    if not delayed and any("delayed" in value for value in coverage):
        delayed = [{"event": event, "effect_nodes": list(range(len(effects)))}]
    copy_nodes = [
        {"effect_index": index, "node": _clone(effect)}
        for index, effect in enumerate(effects)
        if "copy" in str(effect.get("op") or "").casefold()
    ]
    zone_nodes = [
        {"effect_index": index, "node": _clone(effect)}
        for index, effect in enumerate(effects)
        if any(
            marker in str(effect.get("op") or "").casefold()
            for marker in ("cast_from", "play_from", "zone_permission")
        )
    ]
    subscriptions = FixedEventSubscriptionSet.from_condition(
        program.event_condition
    )
    trigger_nodes = (
        [
            {
                "event": subscription.event,
                "condition": (
                    _clone(thaw_value(subscription.condition))
                    if subscription.condition is not None
                    else None
                ),
                "effect_nodes": list(range(len(effects))),
            }
            for subscription in subscriptions.subscriptions
        ]
        if subscriptions is not None
        else [
            {
                "event": event,
                "condition": _clone(program.event_condition),
                "effect_nodes": list(range(len(effects))),
            }
        ]
    )
    durations = []
    for index, effect in enumerate(effects):
        if "duration" in effect:
            durations.append(
                {"effect_index": index, "duration": _clone(effect["duration"])}
            )
    return {
        "triggers": (
            trigger_nodes
            if trigger
            else []
        ),
        "static_effects": (
            [{"effect_nodes": list(range(len(effects)))}] if static else []
        ),
        "replacement_effects": (
            [{"event": event, "effect_nodes": list(range(len(effects)))}]
            if replacement
            else []
        ),
        "prevention_effects": (
            [{"effect_nodes": list(range(len(effects)))}] if prevention else []
        ),
        "continuous_effects": (
            [{"effect_nodes": list(range(len(effects)))}] if continuous else []
        ),
        "durations": durations,
        "delayed_effects": delayed,
        "copy_behavior": copy_nodes,
        "zone_permissions": zone_nodes,
    }


def ability_to_card_dict(program: "SemanticProgram") -> dict[str, Any]:
    classified = _classified_effects(program)
    effects = [_clone(effect) for effect in program.effects]
    targets = _clone(program.target_schema)
    linked = program.provenance.get("linked_ability_ids", [])
    if not isinstance(linked, list):
        linked = []
    return {
        "semantic_key": program.key,
        "ability_id": program.ability_id,
        "face_id": _ability_face_id(program),
        "kind": _ability_kind(program.ability_id, program.event),
        "label": program.label,
        "active_zones": [program.active_zone],
        "timing_permissions": {"event": program.event},
        "costs": _clone(program.cost_schema),
        "modes": _modes(targets),
        "targets": targets,
        "choices": _choice_nodes(effects),
        "effect_nodes": effects,
        "triggers": classified["triggers"],
        "static_effects": classified["static_effects"],
        "replacement_effects": classified["replacement_effects"],
        "prevention_effects": classified["prevention_effects"],
        "continuous_effects": classified["continuous_effects"],
        "linked_ability_ids": sorted(set(str(value) for value in linked)),
        "durations": classified["durations"],
        "delayed_effects": classified["delayed_effects"],
        "copy_behavior": classified["copy_behavior"],
        "zone_permissions": classified["zone_permissions"],
        "capability_dependencies": list(program.capability_dependencies),
        "trust_closure": _clone(program.capability_closure),
        "source_span": _clone(program.provenance.get("source_span")),
        "residual_ids": list(program.provenance.get("residual_ids", [])),
        "runtime": {
            "destination": program.destination,
            "requires_arbiter": program.requires_arbiter,
            "notes": program.notes,
            "version": program.version,
            "semantic_schema_version": program.semantic_schema_version,
            "trust_level": program.trust_level,
            "provenance": _clone(program.provenance),
            "tests": list(program.tests),
            "handlers": _clone(program.handlers),
            "event_condition": _clone(program.event_condition),
            "coverage": list(program.coverage),
        },
    }


def ability_from_card_dict(
    value: Mapping[str, Any],
    *,
    oracle_id: str,
) -> "SemanticProgram":
    from ..semantics import SemanticProgram

    _exact_fields(value, _ABILITY_FIELDS, field="ability")
    runtime = value.get("runtime")
    if not isinstance(runtime, Mapping):
        raise CardProgramError("ability.runtime must be an object")
    _exact_fields(runtime, _RUNTIME_FIELDS, field="ability.runtime")
    if type(runtime.get("requires_arbiter")) is not bool:
        raise CardProgramError(
            "ability.runtime.requires_arbiter must be boolean"
        )
    for field in ("version", "semantic_schema_version"):
        if type(runtime.get(field)) is not int or runtime[field] < 1:
            raise CardProgramError(
                f"ability.runtime.{field} must be a positive integer"
            )
    if not isinstance(runtime.get("notes"), str):
        raise CardProgramError("ability.runtime.notes must be a string")
    if not isinstance(runtime.get("provenance"), Mapping):
        raise CardProgramError(
            "ability.runtime.provenance must be an object"
        )
    if runtime.get("destination") is not None and not isinstance(
        runtime.get("destination"), str
    ):
        raise CardProgramError(
            "ability.runtime.destination must be a string or null"
        )
    if runtime.get("event_condition") is not None and not isinstance(
        runtime.get("event_condition"), Mapping
    ):
        raise CardProgramError(
            "ability.runtime.event_condition must be an object or null"
        )
    active_zones = _strings(value.get("active_zones"), field="active_zones")
    if len(active_zones) != 1:
        raise CardProgramError(
            "CardProgram V2 compatibility abilities require one active zone"
        )
    timing = value.get("timing_permissions")
    if not isinstance(timing, Mapping) or set(timing) != {"event"}:
        raise CardProgramError(
            "ability.timing_permissions must contain exactly event"
        )
    targets = value.get("targets")
    if targets is not None and not isinstance(targets, Mapping):
        raise CardProgramError("ability.targets must be an object or null")
    costs = value.get("costs")
    if costs is not None and not isinstance(costs, Mapping):
        raise CardProgramError("ability.costs must be an object or null")
    trust_closure = value.get("trust_closure")
    if trust_closure is not None and not isinstance(trust_closure, Mapping):
        raise CardProgramError("ability.trust_closure must be an object or null")
    program = SemanticProgram(
        key=_nonempty(value.get("semantic_key"), field="semantic_key"),
        label=_nonempty(value.get("label"), field="label"),
        effects=list(_objects(value.get("effect_nodes"), field="effect_nodes")),
        destination=runtime.get("destination"),
        requires_arbiter=runtime["requires_arbiter"],
        notes=runtime["notes"],
        version=runtime["version"],
        oracle_id=oracle_id,
        ability_id=_nonempty(value.get("ability_id"), field="ability_id"),
        active_zone=active_zones[0],
        event=_nonempty(timing.get("event"), field="timing event"),
        semantic_schema_version=runtime["semantic_schema_version"],
        trust_level=_nonempty(runtime.get("trust_level"), field="trust_level"),
        provenance=_clone(dict(runtime["provenance"])),
        tests=list(_strings(runtime.get("tests"), field="tests")),
        handlers=list(_objects(runtime.get("handlers"), field="handlers")),
        target_schema=_clone(dict(targets)) if targets is not None else None,
        cost_schema=_clone(dict(costs)) if costs is not None else None,
        event_condition=(
            _clone(dict(runtime["event_condition"]))
            if isinstance(runtime.get("event_condition"), Mapping)
            else None
        ),
        coverage=list(_strings(runtime.get("coverage"), field="coverage")),
        capability_dependencies=list(
            _strings(
                value.get("capability_dependencies"),
                field="capability_dependencies",
            )
        ),
        capability_closure=(
            _clone(dict(trust_closure)) if trust_closure is not None else None
        ),
    )
    expected = ability_to_card_dict(program)
    # oracle_id is supplied by the enclosing card after the deterministic
    # typed projections have been validated.
    for field in sorted(_ABILITY_FIELDS - {"runtime"}):
        if _clone(value.get(field)) != expected[field]:
            raise CardProgramError(
                f"ability.{field} does not match its runtime program"
            )
    return program


@dataclass(frozen=True, slots=True)
class CardProgramFace:
    face_id: str
    name: str | None
    type_line: str | None
    oracle_text_hash: str

    def __post_init__(self) -> None:
        _nonempty(self.face_id, field="face_id")
        _sha(self.oracle_text_hash, field="oracle_text_hash")
        for field, value in (("name", self.name), ("type_line", self.type_line)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise CardProgramError(f"face {field} must be nonempty or null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "face_id": self.face_id,
            "name": self.name,
            "type_line": self.type_line,
            "oracle_text_hash": self.oracle_text_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CardProgramFace":
        _exact_fields(value, _FACE_FIELDS, field="face")
        return cls(
            face_id=_nonempty(value.get("face_id"), field="face_id"),
            name=value.get("name"),
            type_line=value.get("type_line"),
            oracle_text_hash=_sha(
                value.get("oracle_text_hash"), field="oracle_text_hash"
            ),
        )


def _semantic_payload(
    faces: Sequence[CardProgramFace],
    abilities: Sequence["SemanticProgram"],
    residuals: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    ability_values = []
    for ability in abilities:
        value = ability_to_card_dict(ability)
        value.pop("runtime")
        value.pop("trust_closure")
        ability_values.append(value)
    return {
        "faces": [face.to_dict() for face in faces],
        "abilities": ability_values,
        "residuals": [_clone(dict(value)) for value in residuals],
    }


@dataclass(frozen=True, slots=True)
class CardProgram:
    compiler_version: str
    oracle_id: str
    card_name: str | None
    faces: tuple[CardProgramFace, ...]
    oracle_source_hash: str
    rulings_source_hash: str
    abilities: tuple["SemanticProgram", ...]
    residuals: tuple[dict[str, Any], ...]
    provenance: dict[str, Any]
    semantic_hash: str
    trust_closure: dict[str, Any]
    schema_version: int = CARD_PROGRAM_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CARD_PROGRAM_SCHEMA_VERSION:
            raise CardProgramError("Unsupported CardProgram schema_version")
        _nonempty(self.compiler_version, field="compiler_version")
        _nonempty(self.oracle_id, field="oracle_id")
        if self.card_name is not None and (
            not isinstance(self.card_name, str) or not self.card_name.strip()
        ):
            raise CardProgramError("card_name must be nonempty or null")
        _sha(self.oracle_source_hash, field="oracle_source_hash")
        _sha(self.rulings_source_hash, field="rulings_source_hash")
        _sha(self.semantic_hash, field="semantic_hash")
        face_ids = [face.face_id for face in self.faces]
        if len(face_ids) != len(set(face_ids)):
            raise CardProgramError("CardProgram face IDs must be unique")
        if not self.faces:
            raise CardProgramError("CardProgram requires at least one face")
        ability_ids = [ability.ability_id for ability in self.abilities]
        semantic_keys = [ability.key for ability in self.abilities]
        if len(ability_ids) != len(set(ability_ids)):
            raise CardProgramError("CardProgram ability IDs must be unique")
        if len(semantic_keys) != len(set(semantic_keys)):
            raise CardProgramError("CardProgram semantic keys must be unique")
        for ability in self.abilities:
            if ability.oracle_id != self.oracle_id:
                raise CardProgramError(
                    f"Ability {ability.ability_id} has a different oracle_id"
                )
            if _ability_face_id(ability) not in set(face_ids):
                raise CardProgramError(
                    f"Ability {ability.ability_id} references an unknown face"
                )
        expected_semantic = _hash(
            _semantic_payload(self.faces, self.abilities, self.residuals)
        )
        if self.semantic_hash != expected_semantic:
            raise CardProgramError("CardProgram semantic_hash does not match")
        expected_trust = build_program_trust_closure(
            self.abilities,
            self.residuals,
            oracle_source_hash=self.oracle_source_hash,
            rulings_source_hash=self.rulings_source_hash,
        )
        if _clone(self.trust_closure) != expected_trust:
            raise CardProgramError("CardProgram trust_closure does not match")

    @classmethod
    def create(
        cls,
        *,
        compiler_version: str,
        oracle_id: str,
        card_name: str | None,
        faces: Iterable[CardProgramFace],
        oracle_source_hash: str,
        rulings_source_hash: str,
        abilities: Iterable["SemanticProgram"],
        residuals: Iterable[Mapping[str, Any]] = (),
        provenance: Mapping[str, Any] | None = None,
    ) -> "CardProgram":
        face_values = tuple(sorted(faces, key=lambda value: value.face_id))
        ability_values = tuple(
            sorted(abilities, key=lambda value: (value.ability_id, value.key))
        )
        residual_values = tuple(
            sorted(
                (_clone(dict(value)) for value in residuals),
                key=lambda value: (
                    str(value.get("face_id") or ""),
                    str(value.get("residual_id") or ""),
                ),
            )
        )
        return cls(
            compiler_version=compiler_version,
            oracle_id=oracle_id,
            card_name=card_name,
            faces=face_values,
            oracle_source_hash=oracle_source_hash,
            rulings_source_hash=rulings_source_hash,
            abilities=ability_values,
            residuals=residual_values,
            provenance=_clone(dict(provenance or {})),
            semantic_hash=_hash(
                _semantic_payload(face_values, ability_values, residual_values)
            ),
            trust_closure=build_program_trust_closure(
                ability_values,
                residual_values,
                oracle_source_hash=oracle_source_hash,
                rulings_source_hash=rulings_source_hash,
            ),
        )

    @property
    def fingerprint(self) -> str:
        return _hash(self._payload())

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        return tuple(self.trust_closure["capability_dependencies"])

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "compiler_version": self.compiler_version,
            "oracle_id": self.oracle_id,
            "card_identity": {"name": self.card_name},
            "faces": [face.to_dict() for face in self.faces],
            "oracle_source_hash": self.oracle_source_hash,
            "rulings_source_hash": self.rulings_source_hash,
            "abilities": [
                ability_to_card_dict(ability) for ability in self.abilities
            ],
            "capability_dependencies": list(self.capability_dependencies),
            "trust_closure": _clone(self.trust_closure),
            "semantic_hash": self.semantic_hash,
            "residuals": [_clone(value) for value in self.residuals],
            "provenance": _clone(self.provenance),
        }

    def to_dict(self) -> dict[str, Any]:
        value = self._payload()
        value["fingerprint"] = self.fingerprint
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CardProgram":
        _exact_fields(value, _PROGRAM_FIELDS, field="CardProgram")
        if value.get("schema_version") != CARD_PROGRAM_SCHEMA_VERSION:
            raise CardProgramError("Unsupported CardProgram schema_version")
        identity = value.get("card_identity")
        if not isinstance(identity, Mapping) or set(identity) != {"name"}:
            raise CardProgramError(
                "card_identity must contain exactly the name field"
            )
        faces = tuple(
            CardProgramFace.from_dict(item)
            for item in _objects(value.get("faces"), field="faces")
        )
        raw_abilities = _objects(value.get("abilities"), field="abilities")
        oracle_id = _nonempty(value.get("oracle_id"), field="oracle_id")
        if not isinstance(value.get("provenance"), Mapping):
            raise CardProgramError("CardProgram provenance must be an object")
        if not isinstance(value.get("trust_closure"), Mapping):
            raise CardProgramError(
                "CardProgram trust_closure must be an object"
            )
        abilities = []
        for raw in raw_abilities:
            abilities.append(
                ability_from_card_dict(raw, oracle_id=oracle_id)
            )
        program = cls(
            compiler_version=_nonempty(
                value.get("compiler_version"), field="compiler_version"
            ),
            oracle_id=oracle_id,
            card_name=identity.get("name"),
            faces=faces,
            oracle_source_hash=_sha(
                value.get("oracle_source_hash"), field="oracle_source_hash"
            ),
            rulings_source_hash=_sha(
                value.get("rulings_source_hash"), field="rulings_source_hash"
            ),
            abilities=tuple(abilities),
            residuals=_objects(value.get("residuals"), field="residuals"),
            provenance=_clone(dict(value["provenance"])),
            semantic_hash=_sha(value.get("semantic_hash"), field="semantic_hash"),
            trust_closure=_clone(dict(value["trust_closure"])),
        )
        dependencies = _strings(
            value.get("capability_dependencies"),
            field="capability_dependencies",
        )
        if dependencies != program.capability_dependencies:
            raise CardProgramError(
                "CardProgram capability_dependencies do not match closure"
            )
        if _sha(value.get("fingerprint"), field="fingerprint") != program.fingerprint:
            raise CardProgramError("CardProgram fingerprint does not match")
        return program
