from __future__ import annotations

"""Ordinary Storm fragment validation, preserving its original descriptor contract."""

from dataclasses import dataclass
from typing import Any, Mapping
from ..ability_fragments import SpellCastKeywordTriggerSpec, SpellCastKeywordTriggerKind, StaticAbilityFragment
from .context import SemanticNodeError

STORM_FRAGMENT_HANDLER_ID = "ability.trigger.storm.v1"


@dataclass(frozen=True, slots=True)
class StormAbilityFragmentHandler:
    handler_id: str = STORM_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.storm"
    event: str = "spell.cast"
    rule_references: tuple[str, ...] = (
        "601.2i",
        "603.2",
        "603.3",
        "702.40",
        "702.40a",
        "702.40b",
    )
    capability_dependencies: tuple[str, ...] = ("trigger.keyword.storm",)

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> SpellCastKeywordTriggerSpec:
        from .ability_fragments import _fragment

        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=SpellCastKeywordTriggerSpec,
        )
        if fragment.kind is not SpellCastKeywordTriggerKind.STORM:
            raise SemanticNodeError(
                "The Storm runtime handler requires a Storm fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)
