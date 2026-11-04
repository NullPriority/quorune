from __future__ import annotations

"""A pending intrinsic reveal preserves the already resolved draw instruction.

This value does not authorize a reveal, a trigger, or a cast. Its consumer must
revalidate the current opportunity, complete program, and source-policy list.
Battlefield-source reveal continuations retain their separate legacy schema.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..replacement import ReplacementEffect, ReplacementSelection
from ..semantic_runtime.draw_reveals import DrawRevealPolicy
from .continuation import DrawResume
from .miracle_model import MiracleDrawOpportunity, MiracleModelError
from .model import (
    DrawError, DrawEventRequest, PreparedDrawEvent, RevealDrawnCardBySource,
    DrawnCardAction, drawn_card_action_from_dict, prepare_draw_event,
)


def _exact(value, fields, name):
    if not isinstance(value, Mapping) or set(value) != fields:
        raise MiracleModelError(f"{name} has a closed schema")


def _objects(value, name):
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, Mapping) for item in value
    ):
        raise MiracleModelError(f"{name} requires a list of objects")
    return value


def _source_action(policy: DrawRevealPolicy) -> RevealDrawnCardBySource:
    return RevealDrawnCardBySource(
        source_object_id=policy.source_object_id,
        source_ref=policy.source_ref,
        source_logical_object_id=policy.source_logical_object_id,
        source_zone_change_counter=policy.source_zone_change_counter,
    )


@dataclass(frozen=True, slots=True)
class MiracleRevealContinuation:
    opportunity: MiracleDrawOpportunity
    request: DrawEventRequest
    effects: tuple[ReplacementEffect, ...]
    journal: tuple[ReplacementSelection, ...]
    remaining_draws: int
    after: DrawResume
    instruction_post_draw_actions: tuple[DrawnCardAction, ...]
    source_policies: tuple[DrawRevealPolicy, ...] = ()
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.opportunity, MiracleDrawOpportunity)
                or not isinstance(self.request, DrawEventRequest)
                or not isinstance(self.after, DrawResume)
                or type(self.remaining_draws) is not int or self.remaining_draws < 1):
            raise MiracleModelError("Miracle reveal continuation is malformed")
        opportunity = self.opportunity
        if (self.request.event_id, self.request.player, self.request.library_size) != (
            opportunity.event_id, opportunity.drawer, opportunity.library_size
        ):
            raise MiracleModelError("Miracle opportunity and draw instruction disagree")
        effects, journal, policies = (
            tuple(self.effects), tuple(self.journal), tuple(self.source_policies)
        )
        if any(not isinstance(effect, ReplacementEffect) or effect.event_kind != "draw"
               for effect in effects):
            raise MiracleModelError("Miracle continuation requires draw replacements")
        if any(not isinstance(selection, ReplacementSelection) for selection in journal):
            raise MiracleModelError("Miracle continuation requires a typed journal")
        if any(not isinstance(policy, DrawRevealPolicy) or policy.player != opportunity.drawer
               for policy in policies):
            raise MiracleModelError("Miracle source policies have the wrong drawer")
        identifiers = [policy.policy_id for policy in policies]
        if len(identifiers) != len(set(identifiers)):
            raise MiracleModelError("Miracle source policies must be unique")
        known_actions = {_source_action(policy) for policy in policies}
        selected = tuple(
            action for action in self.request.post_draw_actions
            if isinstance(action, RevealDrawnCardBySource)
        )
        selected_actions = set(selected)
        mandatory_actions = {
            _source_action(policy) for policy in policies if not policy.optional
        }
        if (len(selected) != len(selected_actions)
                or not mandatory_actions <= selected_actions <= known_actions):
            raise MiracleModelError("Miracle continuation changed source reveal choices")
        tail = tuple(self.instruction_post_draw_actions)
        # Source-specific as-drawn choices apply to this event only. Preserve
        # ordinary CR121.6c instruction actions for subsequent cards, separately.
        if any(isinstance(action, RevealDrawnCardBySource) for action in tail):
            raise MiracleModelError("Miracle instruction tail repeats a source reveal choice")
        validated_tail = DrawEventRequest(
            event_id=self.request.event_id, player=self.request.player,
            library_size=self.request.library_size, post_draw_actions=tail,
        ).post_draw_actions
        if validated_tail != tuple(action for action in self.request.post_draw_actions
                                   if not isinstance(action, RevealDrawnCardBySource)):
            raise MiracleModelError("Miracle continuation changed the instruction tail")
        object.__setattr__(self, "effects", effects)
        object.__setattr__(self, "journal", journal)
        object.__setattr__(self, "source_policies", policies)
        object.__setattr__(self, "instruction_post_draw_actions", validated_tail)

    def prepare(self, *, apnap_order: Sequence[str]) -> PreparedDrawEvent:
        """Rebuild the original closed result through the replacement owner."""
        prepared = prepare_draw_event(
            self.request, apnap_order=apnap_order, effects=self.effects,
            selections=tuple(selection.to_dict() for selection in self.journal),
        )
        if (prepared.pending is not None or prepared.resolution is None
                or prepared.resolution.kind != "draw"
                or prepared.resolution.player != self.opportunity.drawer):
            raise DrawError("Miracle reveal no longer represents an ordinary draw")
        return prepared

    def to_dict(self):
        return {
            "schema_version": 1,
            "kind": "miracle_reveal",
            "opportunity": self.opportunity.to_dict(),
            "request": {
                "event_id": self.request.event_id,
                "player": self.request.player,
                "library_size": self.request.library_size,
                "reason": self.request.reason,
                "private": self.request.private,
                "excluded_effect_ids": list(self.request.excluded_effect_ids),
                "post_draw_actions": [action.to_dict() for action in self.request.post_draw_actions],
            },
            "effects": [effect.to_dict() for effect in self.effects],
            "journal": [selection.to_dict() for selection in self.journal],
            "remaining_draws": self.remaining_draws,
            "after": self.after.to_dict(),
            "instruction_post_draw_actions": [action.to_dict() for action in self.instruction_post_draw_actions],
            "source_policies": [policy.to_dict() for policy in self.source_policies],
        }

    @classmethod
    def from_dict(cls, value):
        _exact(value, {"schema_version", "kind", "opportunity", "request", "effects",
                       "journal", "remaining_draws", "after", "source_policies",
                       "instruction_post_draw_actions"},
               "Miracle reveal continuation")
        if value["kind"] != "miracle_reveal":
            raise MiracleModelError("Miracle reveal continuation has the wrong role")
        request = value["request"]
        _exact(request, {"event_id", "player", "library_size", "reason", "private",
                         "excluded_effect_ids", "post_draw_actions"}, "Miracle draw request")
        if not isinstance(request["excluded_effect_ids"], (list, tuple)):
            raise MiracleModelError("Miracle draw exclusions require a list")
        return cls(
            schema_version=value["schema_version"],
            opportunity=MiracleDrawOpportunity.from_dict(value["opportunity"]),
            request=DrawEventRequest(**{
                **request,
                "excluded_effect_ids": tuple(request["excluded_effect_ids"]),
                "post_draw_actions": tuple(drawn_card_action_from_dict(action) for action in
                    _objects(request["post_draw_actions"], "Miracle draw actions")),
            }),
            effects=tuple(ReplacementEffect.from_dict(effect) for effect in
                          _objects(value["effects"], "Miracle draw effects")),
            journal=tuple(ReplacementSelection.from_dict(selection) for selection in
                          _objects(value["journal"], "Miracle draw journal")),
            remaining_draws=value["remaining_draws"],
            after=DrawResume.from_dict(value["after"]),
            instruction_post_draw_actions=tuple(drawn_card_action_from_dict(action) for action in
                _objects(value["instruction_post_draw_actions"], "Miracle instruction actions")),
            source_policies=tuple(DrawRevealPolicy.from_dict(policy) for policy in
                                 _objects(value["source_policies"], "Miracle source policies")),
        )
