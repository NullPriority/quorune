from __future__ import annotations

"""Typed self-cast copy trigger whose public count is read on resolution."""

from dataclasses import dataclass

from ..ability_fragments import SpellCastKeywordTriggerKind, SpellCastKeywordTriggerSpec
from ..commander_cast_count_model import COMMANDER_CAST_COUNT_CAPABILITY
from .context import SemanticNodeError

COMMANDER_CAST_COPY_HANDLER = "ability.trigger.commander-cast-copy.v1"
COMMANDER_CAST_COPY_CAPABILITY = "trigger.commander_cast_copy"
COMMANDER_CAST_COPY_KEY = "builtin:commander-cast-copy"
COMMANDER_CAST_COPY_KINDS = frozenset({
    SpellCastKeywordTriggerKind.COMMANDER_CAST_COPY,
    SpellCastKeywordTriggerKind.COMMANDER_CAST_COPY_NEW_TARGETS,
})


@dataclass(frozen=True, slots=True)
class CommanderCastCopyHandler:
    handler_id: str = COMMANDER_CAST_COPY_HANDLER
    schema_version: int = 1
    family: str = "ability.trigger.commander_cast_copy"
    event: str = "spell.cast"
    rule_references: tuple[str, ...] = ("601.2i", "603.2", "603.3b", "608.2h", "707.10", "903.8")
    capability_dependencies: tuple[str, ...] = (COMMANDER_CAST_COPY_CAPABILITY, COMMANDER_CAST_COUNT_CAPABILITY)

    def validate(self, descriptor):
        from .ability_fragments import _fragment

        fragment = _fragment(descriptor, handler_id=self.handler_id, event=self.event,
            expected_type=SpellCastKeywordTriggerSpec)
        if fragment.kind not in COMMANDER_CAST_COPY_KINDS:
            raise SemanticNodeError("Commander-cast copying requires its explicit trigger kind")
        return fragment

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
