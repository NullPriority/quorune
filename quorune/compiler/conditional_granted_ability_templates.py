from __future__ import annotations

"""Closed public-state shells around separately compiled typed abilities."""

import re
from typing import Any, Mapping

from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_GRANTED_ABILITY_CAPABILITY_ID,
    FIXED_PUBLIC_STATE_GRANTED_ABILITY_HANDLER_ID,
)
from .continuous_templates import (
    _ATTACHED_QUOTED_ABILITY_SENTINELS,
    _attached_ability_capabilities,
    fixed_public_state_characteristics_handler,
)


def _conditional_quoted_ability_shell(
    oracle_line: str, *, source_name: str,
) -> tuple[str, tuple[str, Mapping[str, Any], tuple[str, ...]], str] | None:
    if oracle_line.count('"') != 2:
        return None
    start, end = oracle_line.find('"'), oracle_line.rfind('"')
    quoted = oracle_line[start + 1:end].strip()
    if not quoted or "\n" in quoted:
        return None
    for sentinel in _ATTACHED_QUOTED_ABILITY_SENTINELS:
        if sentinel.casefold() in oracle_line.casefold():
            continue
        synthetic = oracle_line[:start] + sentinel + oracle_line[end + 1:]
        # Both forms conjoin a fixed modifier and independently granted abilities.
        synthetic = re.sub(
            r", has (?P<keywords>[^,.]+), and has " + re.escape(sentinel),
            lambda match: " and has " + match.group("keywords") + " and " + sentinel,
            synthetic, flags=re.IGNORECASE,
        )
        compiled = fixed_public_state_characteristics_handler(
            synthetic, source_name=source_name,
        )
        if compiled is not None and compiled[1]["modifier"]["add_abilities"].count(sentinel) == 1:
            return quoted, compiled, sentinel
    return None


def conditional_quoted_ability_text(oracle_line: str, *, source_name: str) -> str | None:
    shell = _conditional_quoted_ability_shell(oracle_line, source_name=source_name)
    return shell[0] if shell is not None else None


def conditional_quoted_ability_handler(
    oracle_line: str, *, source_name: str,
    fragment: Mapping[str, Any], fragment_capabilities: tuple[str, ...],
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    shell = _conditional_quoted_ability_shell(oracle_line, source_name=source_name)
    if shell is None:
        return None
    _, compiled, sentinel = shell
    descriptor = dict(compiled[1])
    modifier = dict(descriptor["modifier"])
    modifier["add_abilities"] = [a for a in modifier["add_abilities"] if a != sentinel]
    modifier["add_ability_fragments"] = [dict(fragment)]
    descriptor.update(
        handler_id=FIXED_PUBLIC_STATE_GRANTED_ABILITY_HANDLER_ID,
        schema_version=2, modifier=modifier,
    )
    capabilities = (
        set(compiled[2]) - set(_attached_ability_capabilities((sentinel,)))
    ) | set(fragment_capabilities) | {FIXED_PUBLIC_STATE_GRANTED_ABILITY_CAPABILITY_ID}
    return "continuous-public-conditional-typed-grant-v1", descriptor, tuple(sorted(capabilities))
