"""Closed destruction result descriptors and committed quantities."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
from ..mana_value_filter import ManaValueComparison, ManaValueFilter
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import (
    COMMITTED_TOKEN_CREATOR_COUNTS_FIELD,
    CommittedQuantityFollowup,
    fixed_followup_capabilities,
    fixed_quantity_effect,
    quantity_followup_from_count,
    token_followup_from_committed_controllers,
)
from .artifact_destruction_life import (
    ARTIFACT_DESTRUCTION_LIFE_CAPABILITY, ARTIFACT_DESTRUCTION_LIFE_VERSION, ARTIFACT_LIFE_RESULT, ARTIFACT_SCOPE,
)
from .destruction_sized_token import (
    DESTRUCTION_SIZED_TOKEN_CAPABILITY, DESTRUCTION_SIZED_TOKEN_VERSION, destruction_sized_token_result,
    validate_destruction_sized_token,
)
from .target_player_destruction_death_tokens import (
    TARGET_PLAYER_DEATH_TOKEN_CAPABILITY, TARGET_PLAYER_DEATH_TOKEN_VERSION,
    validate_target_player_death_scope, validate_target_player_death_token_definition,
)


DESTRUCTION_RESULT_CAPABILITY = "permanent.destroy.result_quantity"
DESTRUCTION_RESULT_MECHANIC = "committed-destruction-quantity"
DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY = "permanent.destroy.controller_token_result"
DESTRUCTION_TOKEN_COUNTS_FIELD = COMMITTED_TOKEN_CREATOR_COUNTS_FIELD
DESTRUCTION_MANA_ALLOCATION_CAPABILITY = "mana.production.committed_destruction_allocation"
_MANA_ALLOCATION_SCOPE = AffectedPermanentSetSpec(
    query=ObjectQuerySpec(zones=("battlefield",), excluded_types=("land",)),
    mana_value_filter=ManaValueFilter(ManaValueComparison.AT_MOST, 2),
)
OWNED_RETURN_VERSION = 7
_OWNED_RETURN = FrozenMap({"kind": "return_owned_committed_creature"})
_OWNED_RETURN_SCOPE = AffectedPermanentSetSpec(query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)))
_MANA_ALLOCATION_INTENT = FrozenMap({"kind": "destroyed_permanent_black_green_allocation"})

_CONTROLLER_TOKEN_DESTRUCTION_SCOPES = (
    AffectedPermanentSetSpec(query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))),
    AffectedPermanentSetSpec(query=ObjectQuerySpec(zones=("battlefield",), types_any=("artifact", "enchantment"))),
)


@dataclass(frozen=True, slots=True)
class DestructionFollowup:
    scope: AffectedPermanentSetSpec
    effect: FrozenMap
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.scope, AffectedPermanentSetSpec):
            raise ValueError("Destruction result requires a typed battlefield scope")
        if type(self.schema_version) is not int or self.schema_version not in (
            1, 2, 3, TARGET_PLAYER_DEATH_TOKEN_VERSION, DESTRUCTION_SIZED_TOKEN_VERSION, ARTIFACT_DESTRUCTION_LIFE_VERSION, OWNED_RETURN_VERSION,
        ):
            raise ValueError("Destruction follow-up version is unsupported")
        if self.uses_artifact_life:
            if self.scope != ARTIFACT_SCOPE or not isinstance(self.effect, Mapping) or FrozenMap(self.effect) != ARTIFACT_LIFE_RESULT:
                raise ValueError("Artifact life gain requires the original complete artifact set")
            object.__setattr__(self, "effect", ARTIFACT_LIFE_RESULT)
            return
        if self.uses_owned_return:
            if self.scope != _OWNED_RETURN_SCOPE or FrozenMap(self.effect) != _OWNED_RETURN:
                raise ValueError("A committed return requires its exact destruction and owned creature selection")
            object.__setattr__(self, "effect", _OWNED_RETURN)
            return
        if self.uses_mana_allocation:
            if (self.scope != _MANA_ALLOCATION_SCOPE or not isinstance(self.effect, Mapping)
                    or FrozenMap(self.effect) != _MANA_ALLOCATION_INTENT):
                raise ValueError("Destruction mana requires its exact nonland set and black-green result")
            object.__setattr__(self, "effect", _MANA_ALLOCATION_INTENT)
            return
        effect = fixed_quantity_effect(self.effect)
        if self.uses_sized_token:
            validate_destruction_sized_token(self.scope, effect)
        elif self.uses_target_player_death_tokens:
            validate_target_player_death_token_definition(self.scope, effect)
        elif self.uses_destroyed_controllers:
            definition = effect.get("characteristics", {})
            type_line = definition.get("type_line", "")
            if (self.scope not in _CONTROLLER_TOKEN_DESTRUCTION_SCOPES
                    or effect.get("op") != "create_token" or "tapped" in effect
                    or not type_line.startswith("Token Creature — ")):
                raise ValueError("Controller tokens require a fixed creature token and an exact destruction set")
        else:
            self._validate_quantity_scope()
        object.__setattr__(self, "effect", effect)

    def _validate_quantity_scope(self) -> None:
        query = self.scope.query
        if (
            self.scope.controller_relation not in (PermanentControllerRelation.ANY, PermanentControllerRelation.ACTOR)
            or self.scope.exclude_source
            or query.types_all not in ((), ("creature",), ("enchantment",))
            or query.token not in (None, False)
            or (query.token is False or self.scope.controller_relation is PermanentControllerRelation.ACTOR)
            and query.types_all != ("creature",)
            or query != ObjectQuerySpec(zones=("battlefield",), types_all=query.types_all, token=query.token)
        ):
            raise ValueError("Destruction result scope is unsupported")

    @property
    def uses_destroyed_controllers(self) -> bool:
        return self.schema_version == 2

    @property
    def uses_mana_allocation(self) -> bool:
        return self.schema_version == 3

    @property
    def uses_target_player_death_tokens(self) -> bool:
        return self.schema_version == TARGET_PLAYER_DEATH_TOKEN_VERSION

    @property
    def uses_sized_token(self) -> bool:
        return self.schema_version == DESTRUCTION_SIZED_TOKEN_VERSION

    @property
    def uses_artifact_life(self) -> bool:
        return self.schema_version == ARTIFACT_DESTRUCTION_LIFE_VERSION

    @property
    def uses_owned_return(self) -> bool:
        return self.schema_version == OWNED_RETURN_VERSION

    @classmethod
    def owned_creature_return(cls):
        return cls(_OWNED_RETURN_SCOPE, _OWNED_RETURN, schema_version=OWNED_RETURN_VERSION)

    def validate_destruction_set(self, spec: AffectedPermanentSetSpec) -> None:
        if self.uses_artifact_life and spec != self.scope:
            raise ValueError("Artifact life gain requires its original all-artifact set")
        if self.uses_owned_return and spec != self.scope:
            raise ValueError("The return requires the complete creature destruction scope")
        if self.uses_sized_token and spec != self.scope:
            raise ValueError("Token size requires its original all-creature destruction set")
        if self.uses_target_player_death_tokens:
            validate_target_player_death_scope(spec)
        if self.uses_destroyed_controllers and spec != self.scope:
            raise ValueError("Controller tokens require the same complete set as their destruction")
        if self.uses_mana_allocation and spec != self.scope:
            raise ValueError("Destruction mana requires its original complete nonland set")

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        if self.uses_artifact_life:
            return (DESTRUCTION_RESULT_CAPABILITY, ARTIFACT_DESTRUCTION_LIFE_CAPABILITY,
                    "life.change.effect")
        if self.uses_owned_return:
            from .gift_graveyard_spells import SWEEP_RETURN_CAPABILITY

            return (SWEEP_RETURN_CAPABILITY, "zone.graveyard.ordered_simultaneous_arrival", "zone.reanimate.fixed_target")
        if self.uses_mana_allocation:
            return (DESTRUCTION_RESULT_CAPABILITY, DESTRUCTION_MANA_ALLOCATION_CAPABILITY,
                    "mana.production.fixed_resolution_choice", "mana.production.variable_resolution_choice")
        if self.uses_sized_token:
            return (DESTRUCTION_RESULT_CAPABILITY, DESTRUCTION_SIZED_TOKEN_CAPABILITY,
                    *fixed_followup_capabilities(self.effect))
        if self.uses_target_player_death_tokens:
            return (DESTRUCTION_RESULT_CAPABILITY, TARGET_PLAYER_DEATH_TOKEN_CAPABILITY,
                    *fixed_followup_capabilities(self.effect))
        result = DESTRUCTION_CONTROLLER_TOKEN_CAPABILITY if self.uses_destroyed_controllers else DESTRUCTION_RESULT_CAPABILITY
        return (result, *fixed_followup_capabilities(self.effect))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "scope": self.scope.to_dict(), "effect": thaw_value(self.effect)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DestructionFollowup:
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "scope", "effect"}:
            raise ValueError("Destruction follow-up descriptor is malformed")
        return cls(AffectedPermanentSetSpec.from_dict(value["scope"]), value["effect"], value["schema_version"])

    @classmethod
    def from_destruction_effect(cls, effect: Mapping[str, Any]) -> DestructionFollowup:
        followup = cls.from_dict(effect["followup"])
        if followup.uses_artifact_life and effect.get("regeneration_prohibited") is not True:
            raise ValueError("Affected artifact life gain requires its original regeneration prohibition")
        if followup.uses_owned_return:
            raise ValueError("An owned return requires the original ordered sweep continuation")
        if followup.uses_sized_token and "regeneration_prohibited" in effect:
            raise ValueError("Destruction size requires ordinary regeneration")
        if followup.uses_target_player_death_tokens and "regeneration_prohibited" in effect:
            raise ValueError("This death-conditioned result uses ordinary destruction and regeneration")
        if (followup.uses_destroyed_controllers or followup.uses_mana_allocation
                or followup.uses_target_player_death_tokens or followup.uses_sized_token or followup.uses_artifact_life):
            followup.validate_destruction_set(AffectedPermanentSetSpec.from_dict(effect["set"]))
        return followup

    @classmethod
    def affected_artifact_life(cls):
        return cls(ARTIFACT_SCOPE, ARTIFACT_LIFE_RESULT, schema_version=ARTIFACT_DESTRUCTION_LIFE_VERSION)

    @classmethod
    def black_green_mana(cls):
        return cls(_MANA_ALLOCATION_SCOPE, _MANA_ALLOCATION_INTENT, schema_version=3)

    def mana_from_committed_count(self, count: int, *, actor: str, source_ref: str | None) -> CommittedQuantityFollowup:
        from ..variable_resolution_mana_model import VariableResolutionManaChoiceSpec

        if not self.uses_mana_allocation or type(count) is not int or count < 0:
            raise ValueError("Destruction mana requires a nonnegative committed destruction count")
        if count == 0:
            return CommittedQuantityFollowup(())
        if any(type(value) is not str or not value or value.startswith("$") for value in (actor, source_ref)):
            raise ValueError("Destruction mana requires its original controller and resolving source")
        choice = VariableResolutionManaChoiceSpec("combination", ("B", "G"), count)
        return CommittedQuantityFollowup((FrozenMap({**choice.effect(), "player": actor, "source": source_ref}),))

    def from_committed_count(self, count: int) -> CommittedQuantityFollowup:
        if self.uses_sized_token:
            return destruction_sized_token_result(self.effect, count)
        return quantity_followup_from_count(self.effect, count)

    def from_committed_controllers(self, controllers: Iterable[str]) -> CommittedQuantityFollowup:
        return token_followup_from_committed_controllers(self.effect, controllers)


NEXT_END_STEP_DESTRUCTION_FIELD = "next_end_step_destruction_version"
NEXT_END_STEP_DESTRUCTION_CAPABILITY = "trigger.delayed.fixed_permanent_set_destruction"
NEXT_END_STEP_DESTRUCTION_DEPENDENCIES = (
    NEXT_END_STEP_DESTRUCTION_CAPABILITY,
    "permanent.destroy.fixed_set",
    "trigger.placement.apnap",
)
_NEXT_END_STEP_DESTRUCTION_LABEL = "Destroy all permanents at the next end step"
_NEXT_END_STEP_DESTRUCTION_SET = AffectedPermanentSetSpec(query=ObjectQuerySpec(zones=("battlefield",)))


def next_end_step_destruction_effect() -> dict[str, Any]:
    from ..target_source import TARGETING_SOURCE_CONTEXT

    return {
        "op": "delayed_trigger",
        NEXT_END_STEP_DESTRUCTION_FIELD: 1,
        "controller": "$controller",
        "label": _NEXT_END_STEP_DESTRUCTION_LABEL,
        "event": "step.begin",
        "condition": {"phase": "ending", "step": "end_step"},
        "source_binding": f"$context.{TARGETING_SOURCE_CONTEXT}",
        "once": True,
        "stack": {
            "label": _NEXT_END_STEP_DESTRUCTION_LABEL,
            "context": {"dynamic_effects": [{
                "op": "destroy_all",
                "source": None,
                "set": _NEXT_END_STEP_DESTRUCTION_SET.to_dict(),
            }]},
        },
    }


def validate_next_end_step_destruction(value: Mapping[str, Any], *, actor: str | None = None) -> None:
    """Validate only one ordinary untargeted destruction at the next real end step."""
    if not isinstance(value, Mapping):
        raise ValueError("Delayed destruction requires a typed instruction")
    actual = thaw_value(value)
    version = actual.get(NEXT_END_STEP_DESTRUCTION_FIELD)
    expected = next_end_step_destruction_effect()
    if type(version) is not int or version != 1 or actual.get("once") is not True:
        raise ValueError("Delayed destruction requires its exact one-shot instruction")
    if actor is not None:
        from ..target_source import TargetingSourceBinding

        actual.pop("_runtime_source", None)
        actual.pop("reason", None)
        binding = TargetingSourceBinding.from_dict(actual.get("source_binding"))
        if binding.zone != "battlefield":
            raise ValueError("Delayed destruction requires its original battlefield source")
        expected.update(controller=actor, source_binding=binding.to_dict())
    if actual != expected:
        raise ValueError("Delayed destruction has a closed end-step and battlefield result")
    leaf = actual["stack"]["context"]["dynamic_effects"][0]
    if AffectedPermanentSetSpec.from_dict(leaf["set"]) != _NEXT_END_STEP_DESTRUCTION_SET:
        raise ValueError("Delayed destruction requires the ordinary all-permanents set")


CREATURE_TYPE_DESTRUCTION_FIELD = "creature_type_destruction"
CREATURE_TYPE_DESTRUCTION_CAPABILITY = "permanent.destroy.chosen_creature_type_set"
CREATURE_TYPE_DESTRUCTION_DEPENDENCIES = (
    CREATURE_TYPE_DESTRUCTION_CAPABILITY,
    "permanent.destroy.fixed_set",
)


class ChosenCreatureTypeDestruction(str, Enum):
    MATCHING = "matching"
    NONMATCHING = "nonmatching"

    def choice_effect(self) -> dict[str, Any]:
        return {
            "op": "choose_creature_type",
            CREATURE_TYPE_DESTRUCTION_FIELD: {"schema_version": 1, "scope": self.value},
        }

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any]) -> ChosenCreatureTypeDestruction:
        if (
            not isinstance(effect, Mapping)
            or set(effect) != {"op", CREATURE_TYPE_DESTRUCTION_FIELD}
            or effect["op"] != "choose_creature_type"
        ):
            raise ValueError("Creature-type destruction requires its exact choice instruction")
        spec = effect[CREATURE_TYPE_DESTRUCTION_FIELD]
        if (
            not isinstance(spec, Mapping)
            or set(spec) != {"schema_version", "scope"}
            or type(spec["schema_version"]) is not int
            or spec["schema_version"] != 1
            or type(spec["scope"]) is not str
        ):
            raise ValueError("Creature-type destruction has a closed versioned scope")
        return cls(spec["scope"])

    def destruction_effect(self, creature_type: str) -> dict[str, Any]:
        """Make an ordinary simultaneous destruction instruction for the chosen type."""
        from ..creature_subtypes import CREATURE_SUBTYPES

        if type(creature_type) is not str or creature_type not in CREATURE_SUBTYPES:
            raise ValueError("Creature-type destruction requires one canonical creature type")
        subtype_filter = (
            {"subtypes_all": (creature_type,)} if self is self.MATCHING
            else {"excluded_subtypes": (creature_type,)}
        )
        scope = AffectedPermanentSetSpec(query=ObjectQuerySpec(
            zones=("battlefield",), types_all=("creature",), **subtype_filter,
        ))
        return {"op": "destroy_all", "source": "$source", "set": scope.to_dict()}



def owned_creature_return_from_committed_batch(host, result, *, actor):
    options = []
    branches = {}
    for object_id in result.died_object_ids:
        card = host.state.cards.get(object_id)
        if card is None or card.zone != "graveyard" or card.owner != actor or not card.is_card_object or card.is_token:
            continue
        data = host._effective_card_data(card)
        if "creature" not in host._type_parts(data["type_line"])[0]:
            continue
        options.append({"id": card.ref, "label": card.printed_name})
        branches[card.ref] = [{"op": "move_if_in_zone", "card": card.ref, "from": "graveyard",
                               "expected_zone_change_counter": card.zone_change_counter,
                               "destination": "battlefield", "controller": actor}]
    if not options:
        return CommittedQuantityFollowup(())
    return CommittedQuantityFollowup((FrozenMap({
        "op": "choose_option", "player": actor, "prompt": "Choose a creature card put into your graveyard by this destruction.",
        "options": options, "then_by_choice": branches,
    }),))
