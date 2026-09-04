from __future__ import annotations

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
    granted = attached_granted_program_ability_id(
        kind=kind,
        face_id=face_id,
        line=line,
    )
    if granted is not None:
        return granted
    if kind == "spell_ability":
        return f"spell:{face_id}"
    if kind in {"activated_ability", "mana_ability"}:
        return f"ability:ab{line}"
    if kind == "triggered_ability":
        base = f"trigger:{face_id}:n{line}"
        parts = str(node_id or "").split(":")
        if (
            len(parts) >= 2
            and parts[-1].isdigit()
            and parts[-2]
            in {
                _EVOLVE_MECHANIC,
                _CASCADE_MECHANIC,
                _PROWESS_MECHANIC,
                AFTERLIFE_MECHANIC_ID,
                "chapter",
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
        parts = str(node_id or "").split(":")
        suffix = parts[-1]
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
            }
        ):
            return f"static:{face_id}:n{line}:{parts[-2]}:{parts[-1]}"
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
    if kind == "spell_ability":
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


__all__ = ["generated_ability_id", "generated_coverage"]
