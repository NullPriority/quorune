"""Closed result data for redirecting one announced target to a resolving source."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..stack_target_change_model import OptionalStackTargetChangeSpec


SOURCE_DIRECTED_REDIRECT_FIELD = "source_directed_redirect_version"
SOURCE_DIRECTED_REDIRECT_CAPABILITY = "stack.target.change.source_directed"
SOURCE_DIRECTED_REDIRECT_CONTINUATION_FIELDS = frozenset({
    "_target_stack_ref", "_target_schema", "_choice_actor", "_validation_actor", "_redirect_slots",
})


@dataclass(frozen=True, slots=True)
class SourceDirectedRedirectSpec:
    optional: bool

    def __post_init__(self):
        if type(self.optional) is not bool:
            raise ValueError("Source-directed redirection requires a typed choice policy")

    @property
    def target_schema(self):
        return OptionalStackTargetChangeSpec(spell_or_ability=True).target_schema

    @property
    def effects(self):
        return ({
            "op": "retarget_stack_item", SOURCE_DIRECTED_REDIRECT_FIELD: 1,
            "stack": "$target.0", "recipient": "$source.zone_object", "optional": self.optional,
        },)


def is_source_directed_redirect(effect) -> bool:
    return isinstance(effect, Mapping) and SOURCE_DIRECTED_REDIRECT_FIELD in effect


def source_directed_redirect_from_effect(effect, *, resolved=False, continuation=False):
    if not is_source_directed_redirect(effect):
        raise ValueError("A source-directed target change requires its typed instruction")
    marker = effect[SOURCE_DIRECTED_REDIRECT_FIELD]
    spec = SourceDirectedRedirectSpec(effect.get("optional"))
    expected, = spec.effects
    allowed = set(expected)
    if continuation:
        allowed.update(SOURCE_DIRECTED_REDIRECT_CONTINUATION_FIELDS)
    if resolved and "reason" in effect:
        allowed.add("reason")
    if set(effect) != allowed or effect["op"] != expected["op"] or type(marker) is not int or marker != 1:
        raise ValueError("A source-directed target change has closed fields")
    if resolved:
        if any(effect[key] is not None and (type(effect[key]) is not str or not effect[key])
               for key in ("stack", "recipient")):
            raise ValueError("A source-directed target change requires its resolved references")
    elif dict(effect) != expected:
        raise ValueError("A source-directed target change requires its original bindings")
    return spec
