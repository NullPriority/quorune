from __future__ import annotations

from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..semantics import SemanticProgram
from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID
from ..util import stable_json

from ..bloodthirst import BLOODTHIRST_MECHANIC
from ..death_return import PERSIST_KEYWORD, UNDYING_KEYWORD
from ..fixed_keyword_entry_counters import FIXED_KEYWORD_ENTRY_MECHANICS
from ..fixed_token_production import AFTERLIFE_MECHANIC_ID
from ..modular import MODULAR_MECHANIC_ID
from ..renown import RENOWN_MECHANIC_ID
from ..riot import RIOT_MECHANIC
from ..semantic_runtime.sunburst import SUNBURST_MECHANIC_ID
from ..unleash import UNLEASH_MECHANIC
from .attached_granted_ability_nodes import (
    GRANTED_ACTIVATED_ABILITY_KIND,
    GRANTED_MANA_ABILITY_KIND,
    GRANTED_TRIGGERED_ABILITY_KIND,
    attached_granted_program_ability_id,
)


_EVOLVE_MECHANIC = "evolve"
_CASCADE_MECHANIC = "cascade"
_PROWESS_MECHANIC = "prowess"
_TOXIC_MECHANIC = "toxic"


def generated_ability_id(
    *,
    kind: str,
    face_id: str,
    line: int,
    static_declaration: bool,
    node_id: str | None = None,
) -> str | None:
    if kind == 'granted_static_ability':
        slot = str(node_id or '').rsplit(':role-static:', 1)
        if len(slot) == 2 and slot[1].isdigit():
            return f'static:{face_id}:n{line}:role:{slot[1]}'
        return attached_granted_program_ability_id(kind=kind, face_id=face_id, line=line, node_id=node_id)
    granted = attached_granted_program_ability_id(
        kind=kind,
        face_id=face_id,
        line=line,
        node_id=node_id,
    )
    if granted is not None:
        return granted
    if kind == 'spell_variant':
        suffix = str(node_id or '').rsplit(':', 1)[-1]
        return f'spell:{face_id}:{suffix}' if suffix in {'overload', 'cleave'} else None
    if kind == "spell_ability":
        return f"spell:{face_id}"
    if kind in {"activated_ability", "mana_ability"}:
        parts = str(node_id or "").split(":")
        if len(parts) >= 2 and parts[-2] == 'reconfigure' and parts[-1] in {'attach', 'unattach'}:
            return f'ability:ab{line}-reconfigure-{parts[-1]}'
        if len(parts) >= 2 and parts[-2] == "typecycling-instance" and parts[-1] in {"1", "2", "3", "4"}:
            return f"ability:ab{line}:typecycling-instance:{parts[-1]}"
        return f"ability:ab{line}"
    if kind == "triggered_ability":
        base = f"trigger:{face_id}:n{line}"
        parts = str(node_id or "").split(":")
        if len(parts)>=2 and parts[-2]=='champion' and parts[-1] in {'enter','leave'}:
            return f'{base}:champion:{parts[-1]}'
        if (len(parts) >= 3 and parts[-3] == 'soulbond' and parts[-2].isdigit()
                and parts[-1] in {'self', 'other'}):
            return f'{base}:soulbond:{parts[-2]}:{parts[-1]}'
        if parts[-1] in {"lifecycle", "last-time-counter"}:
            if len(parts) >= 3 and parts[-2].isdigit() and parts[-3] in FIXED_KEYWORD_ENTRY_MECHANICS:
                return f"{base}:{parts[-3]}:{parts[-2]}:{parts[-1]}"
            if parts[-1] == "last-time-counter":
                return f"{base}:last-time-counter"
        if (
            len(parts) >= 2
            and parts[-1].isdigit()
            and parts[-2]
            in {
                _EVOLVE_MECHANIC,
                _CASCADE_MECHANIC,
                _PROWESS_MECHANIC,
                'increment',
                AFTERLIFE_MECHANIC_ID,
                "chapter",
                "backup",
                PERSIST_KEYWORD,
                RENOWN_MECHANIC_ID,
                UNDYING_KEYWORD,
            }
        ):
            return f"{base}:{parts[-2]}:{parts[-1]}"
        if (
            len(parts) >= 3
            and parts[-1] == "departure"
            and parts[-2].isdigit()
            and parts[-3] == MODULAR_MECHANIC_ID
        ):
            return f"{base}:{MODULAR_MECHANIC_ID}:{parts[-2]}:departure"
        return base
    if static_declaration:
        if str(node_id or '').endswith(':max-speed-scope'):return f'static:{face_id}:n{line}:max-speed-scope'
        parts = str(node_id or "").split(":")
        suffix = parts[-1]
        if suffix in {'flash-keyword','flash-stack'}:
            return f'static:{face_id}:n{line}:{suffix}'
        if suffix in {"unleash-entry", "unleash-block"}:
            if (
                len(parts) >= 3
                and parts[-2].isdigit()
                and parts[-3] == UNLEASH_MECHANIC
            ):
                return (
                    f"static:{face_id}:n{line}:{UNLEASH_MECHANIC}:"
                    f"{parts[-2]}:{suffix}"
                )
            return f"static:{face_id}:n{line}:{suffix}"
        if (
            suffix == "entry"
            and len(parts) >= 3
            and parts[-2].isdigit()
            and parts[-3]
            in {MODULAR_MECHANIC_ID, *FIXED_KEYWORD_ENTRY_MECHANICS}
        ):
            return (
                f"static:{face_id}:n{line}:{parts[-3]}:"
                f"{parts[-2]}:entry"
            )
        if (
            len(parts) >= 2
            and parts[-1].isdigit()
            and parts[-2]
            in {
                RIOT_MECHANIC,
                BLOODTHIRST_MECHANIC,
                SUNBURST_MECHANIC_ID,
                _TOXIC_MECHANIC,
                "affinity",
                "devour",
            }
        ):
            return f"static:{face_id}:n{line}:{parts[-2]}:{parts[-1]}"
        if len(parts) >= 2 and parts[-2] == 'player-restriction' and parts[-1] in {'cast', 'activation'}:
            return f'static:{face_id}:n{line}:player-restriction:{parts[-1]}'
        if str(node_id or "").endswith(":flash"):
            return f"static:{face_id}:n{line}:flash"
        node_parts = str(node_id or "").split(":")
        if str(node_id or "").endswith(":convoke") or (
            len(node_parts) >= 2
            and node_parts[-1].isdigit()
            and node_parts[-2] == "convoke"
        ):
            return f"static:{face_id}:n{line}:convoke"
        if str(node_id or "").endswith(":affinity"):
            return f"static:{face_id}:n{line}:affinity"
        return f"static:{face_id}:n{line}"
    return None


def generated_coverage(*, kind: str, runtime_handler: bool) -> str:
    if kind in {"spell_ability", "spell_variant"}:
        return "spell_resolution"
    if kind in {"triggered_ability", GRANTED_TRIGGERED_ABILITY_KIND}:
        return (
            "granted_triggered_ability"
            if kind == GRANTED_TRIGGERED_ABILITY_KIND
            else "triggered_ability"
        )
    if kind in {"mana_ability", GRANTED_MANA_ABILITY_KIND}:
        return (
            "granted_activated_mana_ability"
            if kind == GRANTED_MANA_ABILITY_KIND
            else "activated_mana_ability"
        )
    if kind == GRANTED_ACTIVATED_ABILITY_KIND:
        return "granted_activated_ability"
    if runtime_handler:
        return "runtime_static_handler"
    return "activated_ability"


def runtime_handler_footprint(
    program: SemanticProgram,
) -> tuple[str, str, tuple[str, ...]] | None:
    handler_descriptors = tuple(
        sorted(
            _runtime_handler_semantic_descriptor(handler)
            for handler in program.handlers
            if handler.get("handler_id")
            != ACTIVATED_ABILITY_CATALOG_HANDLER_ID
        )
    )
    if not handler_descriptors or any(
        not value for value in handler_descriptors
    ):
        return None
    return program.active_zone, program.event, handler_descriptors

def _runtime_handler_semantic_descriptor(
    handler: dict[str, Any],
) -> str:
    handler_id = str(handler.get("handler_id") or "")
    if handler_id not in {
        "continuous.anthem.power_toughness.v1",
        "continuous.anthem.fixed-query.v2",
    }:
        return (
            stable_json(_canonical_semantic_value(handler))
            if handler_id
            else ""
        )

    condition = dict(handler.get("condition") or {})
    modifier = dict(handler.get("modifier") or {})
    if handler_id == "continuous.anthem.power_toughness.v1":
        predicate = ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("creature",),
            subtypes_all=tuple(
                condition.get("target_subtypes_all") or ()
            ),
        )
        exclude_source = False
    else:
        predicate = ObjectQuerySpec.from_dict(condition.get("predicate"))
        exclude_source = bool(condition.get("exclude_source", False))
    return stable_json(
        {
            "family": "continuous.anthem.fixed-query",
            "event": str(handler.get("event") or ""),
            "target_controller": str(
                condition.get("target_controller") or ""
            ),
            "predicate": predicate.canonical_dict(),
            "exclude_source": exclude_source,
            "modifier": modifier,
        }
    )

_CANONICAL_QUERY_FIELDS = frozenset(
    ObjectQuerySpec().canonical_dict()
)
_LEGACY_QUERY_FIELDS = _CANONICAL_QUERY_FIELDS - {"types_any"}


def _canonical_semantic_value(value: Any) -> Any:
    """Normalize typed query values inside a complete handler descriptor."""

    if isinstance(value, Mapping):
        fields = frozenset(value)
        if fields in {_CANONICAL_QUERY_FIELDS, _LEGACY_QUERY_FIELDS}:
            return ObjectQuerySpec.from_dict(value).canonical_dict()
        return {
            str(key): _canonical_semantic_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_semantic_value(item) for item in value]
    return value


__all__ = ["generated_ability_id", "generated_coverage", "runtime_handler_footprint"]
