from __future__ import annotations

"""Typed multi-event subscriptions for one triggered semantic program."""

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..cast_event_type_model import PHYSICAL_CARD_EVENT_FIELD
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
    FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
    GREATEST_ARTIFACT_MANA_VALUE_CAPABILITY, FixedPublicStateConditionSpec, FixedPublicStateFact,
    greatest_artifact_mana_value_condition, greatest_artifact_mana_value_condition_dependencies,
)
from ..controller_creature_keyword_absence import (
    CONTROLLER_CREATURE_KEYWORD_ABSENCE_COVERAGE, ControllerCreatureKeywordAbsence,
    is_controller_creature_keyword_absence, is_controller_end_step_schedule,
)
from ..death_keyword_absence import (
    DEATH_KEYWORD_ABSENCE_COVERAGE, DeathKeywordAbsence, is_death_keyword_absence,
)
from ..dungeon_progress import DUNGEON_COMPLETED_EVENT, DUNGEON_VENTURE_CAPABILITY
from ..errors import GameRuleError
from ..face_up_effect_model import (
    FACE_DOWN_ENTRY_CAPABILITY, FACE_DOWN_ENTRY_FIELD,
    TURN_FACE_UP_ACTOR_CAPABILITY, TURN_FACE_UP_ACTOR_FIELD, TURN_FACE_UP_EVENT,
)
from ..replacement.immutable import FrozenMap, freeze_value, thaw_value
from ..util import stable_json
from ..scheduled_player import (
    UPKEEP_HAND_COUNT_CAPABILITY, UpkeepParticipantHandCondition,
    is_upkeep_hand_condition, upkeep_hand_condition_holds,
)
from .scheduled_player_shapes import SCHEDULED_PLAYER_CAPABILITY, is_upkeep_participant_schedule


LEGACY_GREATEST_ARTIFACT_MANA_VALUE_FIELD = "source_controller_controls_highest_artifact_mana_value"

CONTROLLER_FOOD_SACRIFICE_GROUP_CAPABILITY = "trigger.event.controller_food_sacrifice_group"
CONTROLLER_FOOD_SACRIFICE_GROUP_VARIANT = "controller_sacrifices_food"
OWNED_ZONE_EXILE_EVENT = "card.exiled_from_library_or_graveyard.v1"
OWNED_ZONE_EXILE_VARIANT = "one_or_more_controller_cards_exiled_from_library_or_graveyard"
OWNED_ZONE_EXILE_CAPABILITY = "trigger.event.owned_library_graveyard_exile_batch"


def controller_food_sacrifice_group_condition():
    return {"all": [
        {"field": "previous_controller", "op": "eq", "value": "$source.controller"},
        {"field": "types", "op": "contains_any", "value": ["artifact"]},
        {"field": "subtypes", "op": "contains_any", "value": ["food"]},
    ]}


def controller_food_sacrifice_group_dependencies(program):
    matched = thaw_value(program.event_condition) == controller_food_sacrifice_group_condition()
    if not matched and CONTROLLER_FOOD_SACRIFICE_GROUP_CAPABILITY not in program.capability_dependencies:
        return ()
    if (not matched or (program.event, program.active_zone) != ("permanent.sacrificed", "battlefield")
            or "one_or_more_event_batch" not in program.coverage):
        raise ValueError("Grouped Food sacrifices require the controller's actual sacrifice batch and Food facts")
    return (CONTROLLER_FOOD_SACRIFICE_GROUP_CAPABILITY, "trigger.event.normalized_zone_change")


def owned_zone_exile_condition():
    return {"field": "owner", "op": "eq", "value": "$source.controller"}


def grouped_zone_event_dependencies(program):
    food = controller_food_sacrifice_group_dependencies(program)
    if program.event != OWNED_ZONE_EXILE_EVENT and OWNED_ZONE_EXILE_CAPABILITY not in program.capability_dependencies:
        return food
    if ((program.event, program.active_zone) != (OWNED_ZONE_EXILE_EVENT, "battlefield")
            or "one_or_more_event_batch" not in program.coverage
            or thaw_value(program.event_condition) != owned_zone_exile_condition()
            or program.target_schema is not None or program.cost_schema is not None
            or program.handlers or program.trigger_limit is not None):
        raise ValueError("Owned-zone exile requires one grouped controller-relative battlefield trigger")
    return (*food, OWNED_ZONE_EXILE_CAPABILITY, "trigger.event.normalized_zone_change")


EVENT_SUBSCRIPTIONS_FIELD = "event_subscriptions"
LAND_PLAY_EVENT = "land.played"
DRAW_EXCEPT_FIRST_OWN_STEP_EVENT = "card.draw_except_first_draw_step"
PHYSICAL_CARD_PLAY_TEMPLATE = "fixed-physical-card-play-trigger-v1"
PHYSICAL_EXILE_PLAY_TREASURE_TEMPLATE = "fixed-physical-exile-play-treasure-v1"
PLAYED_LAND_REFERENCE_FIELD = "played_land_reference"
OTHER_LAND_PLAY_SACRIFICE_TEMPLATE = "fixed-other-land-play-source-sacrifice-v1"
OTHER_LAND_PLAY_SACRIFICE_CAPABILITY = "trigger.event.other_land_play_source_sacrifice"
COMBAT_PARTICIPANT_DAMAGE_VARIANT = "creature_attacks_or_blocks_damage"
COMBAT_PARTICIPANT_DAMAGE_TRIGGER_TEMPLATE = "fixed-counter-combat-participant-damage-v1"
COMBAT_PARTICIPANT_DAMAGE_PROGRAM_TEMPLATE = COMBAT_PARTICIPANT_DAMAGE_TRIGGER_TEMPLATE.replace(
    "fixed-counter-", "fixed-typed-effect-", 1,
)
COMBAT_PARTICIPANT_DAMAGE_EVENT_CAPABILITIES = (
    "trigger.event.normalized_public_action", "trigger.subscription.fixed_multi_event",
)
GRAVEYARD_DEPARTURE_LOOKBACK_EVENT = "card.leave_graveyard.lookback.v1"
GRAVEYARD_CREATURE_TRANSITIONS_TEMPLATE = "fixed-counter-graveyard-creature-transitions-v1"
GRAVEYARD_CREATURE_TRANSITIONS_VARIANT = "graveyard_creature_transitions_damage"
GRAVEYARD_DEPARTURE_LOOKBACK_CAPABILITY = "trigger.event.normalized_zone_change"
_GRAVEYARD_CREATURE_TRANSITION_CAPABILITIES = (
    GRAVEYARD_DEPARTURE_LOOKBACK_CAPABILITY, "trigger.subscription.fixed_multi_event",
    "trigger.placement.apnap", "trigger.effect.fixed_event",
)


OPPONENT_ATTACK_DRAW_CAST_TEMPLATE = "fixed-counter-opponent-attack-draw-cast-v1"
OPPONENT_ATTACK_DRAW_CAST_VARIANT = "opponent_attacks_or_draws_or_casts_second"
OPPONENT_ATTACK_DRAW_CAST_DEPENDENCIES = (
    "trigger.subscription.fixed_multi_event",
    "trigger.event.direct_player_attack",
    "trigger.event.normalized_card_draw",
    "trigger.event.normalized_spell_cast",
)


def opponent_attack_draw_cast_condition():
    from ..attack_batch_model import DIRECT_PLAYER_ATTACK_EVENT, direct_player_attack_condition

    return fixed_event_subscription_condition((
        FixedEventSubscription(DIRECT_PLAYER_ATTACK_EVENT,
                               direct_player_attack_condition("opponent_attacks_controller_with_two")),
        FixedEventSubscription("card.second_draw", {"field": "player", "op": "ne", "value": "$source.controller"}),
        FixedEventSubscription("spell.cast", {"all": [
            {"field": "controller", "op": "ne", "value": "$source.controller"},
            {"field": "caster_spell_number", "op": "eq", "value": 2},
        ]}),
    ))


def opponent_attack_draw_cast_runtime_dependencies(program, subscriptions):
    from ..attack_batch_model import DIRECT_PLAYER_ATTACK_EVENT

    expected = opponent_attack_draw_cast_condition()
    canonical = stable_json(thaw_value(program.event_condition)) == stable_json(expected)
    template = OPPONENT_ATTACK_DRAW_CAST_TEMPLATE.replace("fixed-counter-", "fixed-typed-effect-", 1)
    if not canonical and template not in str(program.provenance.get("template_id") or ""):
        return ()
    if (not canonical or subscriptions is None
            or (program.event, program.active_zone) != (DIRECT_PLAYER_ATTACK_EVENT, "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or program.trigger_limit is not None
            or stable_json(thaw_value(program.effects)) != stable_json([
                {"op": "draw", "player": "$controller", "count": 1, "private": True},
            ])):
        raise ValueError("The opponent event draw requires all three exact public subscriptions")
    return OPPONENT_ATTACK_DRAW_CAST_DEPENDENCIES


class EventSubscriptionError(ValueError):
    """A serialized fixed multi-event subscription is malformed."""


def physical_card_play_event_condition(*, from_exile: bool = False) -> dict[str, Any]:
    facts = [{"field": PHYSICAL_CARD_EVENT_FIELD, "op": "eq", "value": True}]
    if from_exile:
        facts.append({"field": "from", "op": "eq", "value": "exile"})
    subscriptions = tuple(
        FixedEventSubscription(event, {"all": [
            {"field": actor, "op": "eq", "value": "$source.controller"}, *facts,
        ]})
        for event, actor in ((LAND_PLAY_EVENT, "player"), ("spell.cast", "controller"))
    )
    return fixed_event_subscription_condition(subscriptions)


def other_land_play_condition() -> dict[str, Any]:
    return {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": PHYSICAL_CARD_EVENT_FIELD, "op": "eq", "value": True},
        {"field": PLAYED_LAND_REFERENCE_FIELD, "op": "truthy", "value": True},
        {"field": PLAYED_LAND_REFERENCE_FIELD, "op": "ne", "value": "$source.ref"},
    ]}


def _contains_physical_card_fact(value: Any) -> bool:
    if isinstance(value, Mapping):
        return value.get("field") in (PHYSICAL_CARD_EVENT_FIELD, PLAYED_LAND_REFERENCE_FIELD) or any(
            _contains_physical_card_fact(child) for child in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_physical_card_fact(child) for child in value)
    return False


def physical_card_play_runtime_dependencies(program) -> tuple[str, ...]:
    template = program.provenance.get("template_id")
    if template == OTHER_LAND_PLAY_SACRIFICE_TEMPLATE:
        from ..source_sacrifice_model import fixed_source_sacrifice_effect

        if (
            (program.event, program.active_zone) != (LAND_PLAY_EVENT, "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or stable_json(program.event_condition) != stable_json(other_land_play_condition())
            or stable_json(program.effects) != stable_json((fixed_source_sacrifice_effect(),))
        ):
            raise EventSubscriptionError("Other-land play requires the exact controller, different physical land, and source sacrifice")
        return (OTHER_LAND_PLAY_SACRIFICE_CAPABILITY, "trigger.event.normalized_public_action")
    if template not in {PHYSICAL_CARD_PLAY_TEMPLATE, PHYSICAL_EXILE_PLAY_TREASURE_TEMPLATE}:
        if _contains_physical_card_fact(program.event_condition):
            raise EventSubscriptionError("The physical-card fact requires the closed card-play production")
        return ()
    from_exile = template == PHYSICAL_EXILE_PLAY_TREASURE_TEMPLATE
    if (
        (program.event, program.active_zone) != (LAND_PLAY_EVENT, "battlefield")
        or program.target_schema is not None
        or program.cost_schema is not None
        or stable_json(program.event_condition) != stable_json(physical_card_play_event_condition(from_exile=from_exile))
        or stable_json(program.effects) != stable_json((_physical_play_result(from_exile),))
        or program.handlers
    ):
        raise EventSubscriptionError("Card play requires both exact physical-card subscriptions and their fixed result")
    return ("trigger.event.normalized_public_action", "trigger.event.normalized_spell_cast",
            "trigger.subscription.fixed_multi_event", *(("token.creation.fixed_definition",) if from_exile else ()))


def _physical_play_result(from_exile: bool) -> dict[str, Any]:
    if not from_exile:
        return {"op": "draw", "player": "$controller", "count": 1, "private": True}
    from ..fixed_token_production import predefined_token_identity
    from ..standard_token_abilities import TOKEN_ABILITY_PROFILE_FIELD

    name, characteristics = predefined_token_identity("Treasure", {
        "type_line": "Token Artifact — Treasure",
        "display_text": "{T}, Sacrifice this token: Add one mana of any color.",
        TOKEN_ABILITY_PROFILE_FIELD: "tap_sac_any_color_mana_v1",
    })
    return {"op": "create_token", "controller": "$controller", "name": name,
            "quantity": 1, "characteristics": characteristics}


def _public_state_leaves(value: Any):
    if isinstance(value, Mapping):
        if value.get("field") == FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD:
            yield value
        else:
            for child in value.values():
                yield from _public_state_leaves(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _public_state_leaves(child)


@dataclass(frozen=True, slots=True)
class FixedEventSubscription:
    event: str
    condition: FrozenMap | None = None

    def __post_init__(self) -> None:
        if type(self.event) is not str or not self.event:
            raise EventSubscriptionError(
                "Event subscription identities must be nonempty strings"
            )
        if self.condition is not None and isinstance(self.condition, Mapping):
            object.__setattr__(
                self,
                "condition",
                freeze_value(dict(self.condition), field="event condition"),
            )
        elif self.condition is not None:
            raise EventSubscriptionError(
                "Event subscription conditions must be immutable mappings"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "condition": (
                thaw_value(self.condition)
                if self.condition is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedEventSubscription":
        if set(value) != {"event", "condition"}:
            raise EventSubscriptionError(
                "Event subscription fields must be exactly event and condition"
            )
        condition = value["condition"]
        if condition is not None and not isinstance(condition, Mapping):
            raise EventSubscriptionError(
                "Event subscription condition must be an object or null"
            )
        return cls(
            event=str(value["event"] or ""),
            condition=(
                freeze_value(dict(condition), field="event condition")
                if condition is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class FixedEventSubscriptionSet:
    subscriptions: tuple[FixedEventSubscription, ...]

    def __post_init__(self) -> None:
        if len(self.subscriptions) < 2:
            raise EventSubscriptionError(
                "Multi-event programs require at least two subscriptions"
            )
        events = [subscription.event for subscription in self.subscriptions]
        if len(events) != len(set(events)):
            raise EventSubscriptionError(
                "Multi-event subscriptions must have unique event identities"
            )

    def subscription_for(self, event: str) -> FixedEventSubscription | None:
        return next(
            (
                subscription
                for subscription in self.subscriptions
                if subscription.event == event
            ),
            None,
        )

    def with_shared_condition(self, condition: Mapping[str, Any]) -> FixedEventSubscriptionSet:
        """Conjoin one shared predicate with each event's occurrence filter."""
        return FixedEventSubscriptionSet(tuple(
            FixedEventSubscription(
                subscription.event,
                {"all": [subscription.condition, condition]}
                if subscription.condition is not None else condition,
            )
            for subscription in self.subscriptions
        ))

    def shared_public_state_condition(self) -> Mapping[str, Any] | None:
        """Recover the one intervening predicate shared by every subscription."""
        leaves = tuple(tuple(_public_state_leaves(sub.condition)) for sub in self.subscriptions)
        if not any(leaves):
            return None
        if any(len(conditions) != 1 for conditions in leaves):
            raise EventSubscriptionError("Each conditioned event requires one intervening predicate")
        shared = leaves[0][0]
        if (set(shared) != {"field", "op", "value", "condition"}
                or shared["op"] != "truthy" or shared["value"] is not True):
            raise EventSubscriptionError("The shared intervening predicate is malformed")
        FixedPublicStateConditionSpec.from_dict(shared["condition"])
        for sub in self.subscriptions:
            condition = sub.condition
            if condition == shared:
                continue
            terms = condition["all"] if set(condition) == {"all"} else ()
            if (not isinstance(terms, tuple) or len(terms) != 2
                    or not isinstance(terms[0], Mapping) or terms[1] != shared):
                raise EventSubscriptionError("Conditioned events must conjoin the same intervening predicate")
        return shared

    def to_condition(self) -> dict[str, Any]:
        return {
            EVENT_SUBSCRIPTIONS_FIELD: [
                subscription.to_dict() for subscription in self.subscriptions
            ]
        }

    @classmethod
    def from_condition(
        cls,
        condition: Mapping[str, Any] | None,
    ) -> "FixedEventSubscriptionSet | None":
        if not isinstance(condition, Mapping):
            return None
        if EVENT_SUBSCRIPTIONS_FIELD not in condition:
            return None
        if set(condition) != {EVENT_SUBSCRIPTIONS_FIELD}:
            raise EventSubscriptionError(
                "Multi-event condition must contain only event_subscriptions"
            )
        raw = condition[EVENT_SUBSCRIPTIONS_FIELD]
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise EventSubscriptionError(
                "event_subscriptions must be an array"
            )
        if any(not isinstance(value, Mapping) for value in raw):
            raise EventSubscriptionError(
                "event_subscriptions must contain objects"
            )
        return cls(
            tuple(
                FixedEventSubscription.from_dict(value)
                for value in raw
                if isinstance(value, Mapping)
            )
        )


def fixed_event_subscription_condition(
    subscriptions: Sequence[FixedEventSubscription],
) -> dict[str, Any]:
    """Serialize one compiler-owned fixed subscription set."""

    return FixedEventSubscriptionSet(tuple(subscriptions)).to_condition()


def combat_participant_damage_condition() -> dict[str, Any]:
    return fixed_event_subscription_condition((
        FixedEventSubscription("creature.attacks"),
        FixedEventSubscription("creature.blocks"),
    ))


def graveyard_creature_transition_condition() -> dict[str, Any]:
    creature = {"field": "types", "op": "contains_any", "value": ["creature"]}
    return fixed_event_subscription_condition((
        FixedEventSubscription(GRAVEYARD_DEPARTURE_LOOKBACK_EVENT, {"all": [
            {"field": "owner", "op": "eq", "value": "$source.controller"}, creature,
        ]}),
        FixedEventSubscription("creature.dies", {"field": "card", "op": "ne", "value": "$source.ref"}),
        FixedEventSubscription("card.enter_graveyard", {"all": [
            {"field": "from", "op": "ne", "value": "battlefield"}, creature,
        ]}),
    ))


def graveyard_creature_transition_damage_effect() -> dict[str, Any]:
    from ..fixed_damage_set_model import FixedDamageSetSpec, PlayerDamageGroup, PlayerDamageRelation

    group = FixedDamageSetSpec((PlayerDamageGroup(PlayerDamageRelation.OPPONENTS),))
    return {"op": "damage_fixed_set", "source": "$source", "amount": 1, **group.to_dict()}


def graveyard_creature_transition_runtime_dependencies(program, subscriptions) -> tuple[str, ...]:
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from .grouped_graveyard_departure import COVERAGE, grouped_graveyard_departure_program_dependencies

    if COVERAGE in program.coverage:
        if subscriptions is not None:
            raise EventSubscriptionError("Grouped graveyard departure requires a single subscription")
        return grouped_graveyard_departure_program_dependencies(program, event=GRAVEYARD_DEPARTURE_LOOKBACK_EVENT)

    template = GRAVEYARD_CREATURE_TRANSITIONS_TEMPLATE.replace("fixed-counter-", "fixed-typed-effect-", 1)
    marked = template in str(program.provenance.get("template_id") or "").split("+")
    if not (marked or program.event == GRAVEYARD_DEPARTURE_LOOKBACK_EVENT or (
        subscriptions is not None and subscriptions.subscription_for(GRAVEYARD_DEPARTURE_LOOKBACK_EVENT) is not None
    )):
        return ()
    if (
        (program.event, program.active_zone) != (GRAVEYARD_DEPARTURE_LOOKBACK_EVENT, "battlefield")
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or not {CURRENT_ABILITY_FRAGMENT_COVERAGE, "fixed_multi_event_subscription"} <= set(program.coverage)
        or stable_json(program.event_condition) != stable_json(graveyard_creature_transition_condition())
        or stable_json(program.effects) != stable_json((graveyard_creature_transition_damage_effect(),))
    ):
        raise EventSubscriptionError("Graveyard creature transition damage requires all three exact current-source subscriptions")
    return _GRAVEYARD_CREATURE_TRANSITION_CAPABILITIES


def dungeon_completion_dependencies(program, subscriptions: FixedEventSubscriptionSet | None) -> tuple[str, ...]:
    if subscriptions is not None and subscriptions.subscription_for(DUNGEON_COMPLETED_EVENT) is not None:
        raise EventSubscriptionError("Dungeon completion requires a single event subscription")
    if program.event != DUNGEON_COMPLETED_EVENT:
        return ()
    player = {"battlefield": "$source.controller", "graveyard": "$source.owner"}.get(program.active_zone)
    if player is None or program.event_condition != {"field": "player", "op": "eq", "value": player}:
        raise EventSubscriptionError("Dungeon completion requires a source-relative completion subscription")
    return (DUNGEON_VENTURE_CAPABILITY,)


def event_has_capability_subscriber(host, event, capability):
    semantics = getattr(host, "semantics", None)
    return semantics is not None and any(
        capability in program.capability_dependencies for program in semantics.programs_for_event(event)
    )


def face_transition_event_dependencies(program, subscriptions):
    required = set()
    scopes = {
        FACE_DOWN_ENTRY_FIELD: ("permanent.enter", FACE_DOWN_ENTRY_CAPABILITY, True),
        TURN_FACE_UP_ACTOR_FIELD: (TURN_FACE_UP_EVENT, TURN_FACE_UP_ACTOR_CAPABILITY, "$source.controller"),
    }

    def visit(event, value):
        if isinstance(value, Mapping):
            field = value.get("field")
            if isinstance(field, str) and field in scopes:
                expected_event, capability, expected_value = scopes[field]
                if (event != expected_event or program.active_zone != "battlefield"
                        or value != {"field": field, "op": "eq", "value": expected_value}
                        or type(value["value"]) is not type(expected_value)):
                    raise EventSubscriptionError("Face-transition facts require their closed battlefield subscription")
                required.add(capability)
            for child in value.values():
                visit(event, child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                visit(event, child)

    if subscriptions is None:
        visit(program.event, program.event_condition)
    else:
        for child in subscriptions.subscriptions:
            visit(child.event, child.condition)
    return tuple(sorted(required))


def _upkeep_hand_condition_leaf(condition: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    leaves = tuple(_public_state_leaves(condition))
    if not any(is_upkeep_hand_condition(leaf.get('condition')) for leaf in leaves):
        return None
    clauses = condition['all'] if isinstance(condition, Mapping) and set(condition) == {'all'} else ()
    if (len(leaves) != 1 or not isinstance(clauses, (list, tuple)) or len(clauses) != 2
            or clauses[1] != leaves[0] or not is_upkeep_participant_schedule(clauses[0])):
        raise EventSubscriptionError('Upkeep hand conditions require a closed upkeep participant')
    leaf = leaves[0]
    if set(leaf) != {'field', 'op', 'value', 'condition'} or leaf['op'] != 'truthy' or leaf['value'] is not True:
        raise EventSubscriptionError('The upkeep intervening predicate is malformed')
    UpkeepParticipantHandCondition.from_dict(leaf['condition'])
    return leaf


def upkeep_hand_runtime_dependencies(program) -> tuple[str, ...]:
    leaf = _upkeep_hand_condition_leaf(program.event_condition)
    if leaf is None:
        if UPKEEP_HAND_COUNT_CAPABILITY in program.capability_dependencies:
            raise EventSubscriptionError('A declared upkeep hand condition requires its typed predicate')
        return ()
    if (program.event != 'step.begin' or program.active_zone != 'battlefield'
            or not {'intervening_condition', FIXED_PUBLIC_STATE_INTERVENING_COVERAGE} <= set(program.coverage)):
        raise EventSubscriptionError('Upkeep hand conditions require a battlefield intervening upkeep trigger')
    return ('trigger.condition.fixed_public_state', SCHEDULED_PLAYER_CAPABILITY, UPKEEP_HAND_COUNT_CAPABILITY)


def _greatest_artifact_upkeep_leaf(condition: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    leaves = tuple(_public_state_leaves(condition))
    if not any(isinstance(leaf.get("condition"), Mapping)
               and leaf["condition"].get("fact") == FixedPublicStateFact.CONTROLS_A_GREATEST_MANA_VALUE_ARTIFACT
               for leaf in leaves):
        return None
    from .cumulative_object_upkeep import CONTROLLER_UPKEEP

    expected = {
        "field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, "op": "truthy", "value": True,
        "condition": greatest_artifact_mana_value_condition().to_dict(),
    }
    if (stable_json(condition) != stable_json({"all": [CONTROLLER_UPKEEP, expected]})
            or len(leaves) != 1 or leaves[0]["value"] is not True):
        raise EventSubscriptionError("Greatest artifact mana value requires its closed controller upkeep")
    return leaves[0]


def greatest_artifact_upkeep_runtime_dependencies(program) -> tuple[str, ...]:
    leaf = _greatest_artifact_upkeep_leaf(program.event_condition)
    if leaf is None:
        if GREATEST_ARTIFACT_MANA_VALUE_CAPABILITY in program.capability_dependencies:
            raise EventSubscriptionError("The greatest artifact capability requires its typed predicate")
        return ()
    if ((program.event, program.active_zone) != ("step.begin", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or program.destination is not None or program.trigger_limit is not None or program.requires_arbiter
            or not {"intervening_condition", FIXED_PUBLIC_STATE_INTERVENING_COVERAGE} <= set(program.coverage)
            or stable_json(program.effects) != stable_json(({
                "op": "draw", "player": "$controller", "count": 1, "private": True,
            },))):
        raise EventSubscriptionError("Greatest artifact upkeep requires one untargeted controller draw")
    return greatest_artifact_mana_value_condition_dependencies(leaf["condition"])


def _controller_keyword_absence_leaf(condition: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    leaves = tuple(_public_state_leaves(condition))
    if not any(is_controller_creature_keyword_absence(leaf.get("condition")) for leaf in leaves):
        return None
    clauses = condition["all"] if isinstance(condition, Mapping) and set(condition) == {"all"} else ()
    if (len(leaves) != 1 or not isinstance(clauses, (list, tuple)) or len(clauses) != 2
            or clauses[1] != leaves[0] or not is_controller_end_step_schedule(clauses[0])):
        raise EventSubscriptionError("Controller creature keyword absence requires its controller end-step schedule")
    leaf = leaves[0]
    if set(leaf) != {"field", "op", "value", "condition"} or leaf["op"] != "truthy" or leaf["value"] is not True:
        raise EventSubscriptionError("Controller creature keyword absence requires its closed intervening predicate")
    ControllerCreatureKeywordAbsence.from_dict(leaf["condition"])
    return leaf


def controller_keyword_absence_runtime_dependencies(program) -> tuple[str, ...]:
    leaf = _controller_keyword_absence_leaf(program.event_condition)
    coverage = set(program.coverage)
    if leaf is None:
        if CONTROLLER_CREATURE_KEYWORD_ABSENCE_COVERAGE in coverage:
            raise EventSubscriptionError("Controller creature keyword absence coverage requires its typed predicate")
        return ()
    if (program.event != "step.begin" or program.active_zone != "battlefield"
            or not {CONTROLLER_CREATURE_KEYWORD_ABSENCE_COVERAGE, FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
                    "intervening_condition"} <= coverage):
        raise EventSubscriptionError("Controller creature keyword absence requires a battlefield intervening end-step trigger")
    return ("trigger.condition.fixed_public_state",)


def _controller_keyword_absence_holds(host, source, context, condition: ControllerCreatureKeywordAbsence) -> bool:
    from ..continuous_effect_model import Layer
    from ..object_predicate import ObjectQuerySpec
    from ..object_query import object_matches_query

    controller = context.get("intervening_condition_controller", source.controller)
    if (type(controller) is not str or controller not in host.state.players
            or context.get("event") not in (None, "step.begin") or context.get("step") != "end_step"
            or context.get("player") != controller):
        raise GameRuleError("Controller creature keyword absence lost its original end-step controller")
    query = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), keywords_all=(condition.keyword,))
    battlefield = host.state.players[controller].zones["battlefield"]
    return not any(
        object_matches_query(host._public_object_query_result(card, _maximum_layer=Layer.ABILITY), query)
        for object_id in battlefield
        if (card := host.state.cards[object_id]).controller == controller and not card.phased_out
    )


def _death_keyword_absence_leaf(condition: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    leaves = tuple(_public_state_leaves(condition))
    if not any(is_death_keyword_absence(leaf.get("condition")) for leaf in leaves):
        return None
    clauses = condition["all"] if isinstance(condition, Mapping) and set(condition) == {"all"} else ()
    if len(leaves) != 1 or not isinstance(clauses, (list, tuple)) or len(clauses) != 2 or clauses[1] != leaves[0]:
        raise EventSubscriptionError("Death keyword absence requires one closed occurrence and intervening predicate")
    leaf = leaves[0]
    if set(leaf) != {"field", "op", "value", "condition"} or leaf["op"] != "truthy" or leaf["value"] is not True:
        raise EventSubscriptionError("Death keyword absence requires its explicit intervening predicate")
    spec = DeathKeywordAbsence.from_dict(leaf["condition"])
    if not isinstance(clauses[0], Mapping) or FrozenMap(clauses[0]) != FrozenMap(spec.occurrence_condition()):
        raise EventSubscriptionError("Death keyword absence requires another controlled creature of its subtype")
    return leaf


def death_keyword_absence_runtime_dependencies(program) -> tuple[str, ...]:
    leaf = _death_keyword_absence_leaf(program.event_condition)
    coverage = set(program.coverage)
    if leaf is None:
        if DEATH_KEYWORD_ABSENCE_COVERAGE in coverage:
            raise EventSubscriptionError("Death keyword absence coverage requires its typed predicate")
        return ()
    if (program.event != "creature.dies" or program.active_zone != "battlefield"
            or not {DEATH_KEYWORD_ABSENCE_COVERAGE, FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
                    "intervening_condition"} <= coverage):
        raise EventSubscriptionError("Death keyword absence requires a battlefield intervening creature-death trigger")
    return ("trigger.condition.fixed_public_state", "trigger.event.normalized_zone_change")


def public_state_intervening_actual(
    host,
    condition: Mapping[str, Any],
    *,
    source,
    context: Mapping[str, Any],
) -> bool:
    raw_condition = condition.get("condition")
    if not isinstance(raw_condition, Mapping):
        raise GameRuleError(
            "Public-state intervening conditions require a typed descriptor"
        )
    try:
        from .attached_greatest_power_condition import (
            AttachedGreatestPowerCondition, current_attached_greatest_power_holds, is_attached_greatest_power,
        )
        if is_attached_greatest_power(raw_condition):
            AttachedGreatestPowerCondition.from_dict(raw_condition)
            return current_attached_greatest_power_holds(host, source)
        if is_upkeep_hand_condition(raw_condition):
            hand_condition = UpkeepParticipantHandCondition.from_dict(raw_condition)
            return upkeep_hand_condition_holds(host.state, context, hand_condition)
        if is_controller_creature_keyword_absence(raw_condition):
            keyword_condition = ControllerCreatureKeywordAbsence.from_dict(raw_condition)
            return _controller_keyword_absence_holds(host, source, context, keyword_condition)
        if is_death_keyword_absence(raw_condition):
            death_condition = DeathKeywordAbsence.from_dict(raw_condition)
            return death_condition.holds(context, controller=context.get("intervening_condition_controller", source.controller))
        public_condition = FixedPublicStateConditionSpec.from_dict(raw_condition)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    controller = context.get("intervening_condition_controller")
    if controller is not None and (
        type(controller) is not str or controller not in host.state.players
    ):
        raise GameRuleError(
            "Intervening-condition controller must name an active seat"
        )
    public_source = copy.copy(source)
    if isinstance(controller, str):
        public_source.controller = controller
    locked_identity = context.get("source_logical_object_id")
    if (
        isinstance(controller, str)
        and type(locked_identity) is str
        and locked_identity
        and public_source.logical_object_id != locked_identity
    ):
        public_source.zone = "departed"
        public_source.attached_to = None
        public_source.counters = {}
        identity_prefix = f"{public_source.object_id}@"
        incarnation = locked_identity.removeprefix(identity_prefix)
        if (
            not locked_identity.startswith(identity_prefix)
            or not incarnation.isdigit()
        ):
            raise GameRuleError(
                "Intervening-condition source identity is malformed"
            )
        public_source.zone_change_counter = int(incarnation)
    return host._fixed_public_state_condition_holds(
        public_source,
        public_condition,
    )


def intervening_recheck_condition(program, *, context, controller):
    """Select the intervening predicate without reapplying its occurrence filter."""
    condition = program.event_condition
    upkeep = UPKEEP_HAND_COUNT_CAPABILITY in program.capability_dependencies
    keyword_absence = CONTROLLER_CREATURE_KEYWORD_ABSENCE_COVERAGE in program.coverage
    death_absence = DEATH_KEYWORD_ABSENCE_COVERAGE in program.coverage
    if not (upkeep or keyword_absence or death_absence) and (
        FIXED_PUBLIC_STATE_INTERVENING_COVERAGE not in program.coverage or EVENT_SUBSCRIPTIONS_FIELD not in condition
    ):
        return condition
    try:
        if upkeep:
            shared = _upkeep_hand_condition_leaf(condition)
            events = (program.event,)
        elif keyword_absence:
            shared = _controller_keyword_absence_leaf(condition)
            events = (program.event,)
        elif death_absence:
            shared = _death_keyword_absence_leaf(condition)
            events = (program.event,)
        else:
            subscriptions = FixedEventSubscriptionSet.from_condition(condition)
            assert subscriptions is not None
            shared = subscriptions.shared_public_state_condition()
            events = tuple(sub.event.removesuffix('.self') for sub in subscriptions.subscriptions)
        if (shared is None or type(controller) is not str
                or context.get('intervening_condition_controller') != controller
                or context.get('event') not in events):
            raise EventSubscriptionError('Conditioned trigger lost its occurrence or locked controller')
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return shared


def other_land_arrival_holds(state, context):
    from ..public_history_query_model import PublicHistoryQuerySpec, matching_public_history_events

    player = context.get("controller")
    identity = context.get("card_object_identity")
    types = context.get("types")
    if (type(player) is not str or player not in state.players or type(identity) is not str or not identity
            or not isinstance(types, (tuple, list)) or "land" not in types):
        raise ValueError("Another-land history requires the exact committed land entry")
    entries = matching_public_history_events(state, player, PublicHistoryQuerySpec("permanent_entered", types_all=("land",)))
    if entries is None:
        return False
    identities = {row.object_incarnation for row in entries if row.object_incarnation}
    return identity in identities and len(identities) > 1


def entrant_land_advantage_holds(host, context, *, trigger_controller):
    """Compare the committed entering seat and the original trigger controller."""
    from ..continuous_effect_model import Layer
    from ..player_maximum import current_player_metric_values
    from ..player_maximum_model import LAND_COUNT_METRIC

    entrant = context.get("controller")
    identity = context.get("card_object_identity")
    types = context.get("types")
    if (type(entrant) is not str or entrant not in host.state.players
            or type(trigger_controller) is not str or trigger_controller not in host.state.players
            or type(identity) is not str or not identity
            or not isinstance(types, (tuple, list)) or "land" not in types):
        raise ValueError("Land advantage requires the original normalized land entrant and trigger controller")
    if entrant == trigger_controller:
        return False
    values = dict(current_player_metric_values(
        host.state, LAND_COUNT_METRIC,
        public_object_resolver=lambda card: host._public_object_query_result(card, _maximum_layer=Layer.COLOR),
    ))
    return entrant in values and trigger_controller in values and values[entrant] > values[trigger_controller]


def entrant_land_advantage_runtime_dependencies(program):
    """Require the original intervening event, one exact private result and its recheck."""
    from ..land_entry_condition_model import (
        ENTRANT_LAND_ADVANTAGE_CAPABILITY, ENTRANT_LAND_ADVANTAGE_COVERAGE,
        entrant_land_advantage_condition, entrant_land_advantage_leaf_is_closed, uses_entrant_land_advantage,
    )
    from ..object_predicate import ObjectQuerySpec
    from ..replacement.immutable import FrozenMap

    original = uses_entrant_land_advantage(program.event_condition)
    if (not original and ENTRANT_LAND_ADVANTAGE_CAPABILITY not in program.capability_dependencies
            and ENTRANT_LAND_ADVANTAGE_COVERAGE not in program.coverage):
        return ()
    expected = {"op": "put_card_from_hand", "player": "$controller", "tapped": False,
                "query": ObjectQuerySpec(zones=("hand",), types_all=("land",)).canonical_dict()}
    condition = FrozenMap(program.event_condition or {})
    if ((program.event, program.active_zone) != ("land.enter", "battlefield")
            or condition != FrozenMap(entrant_land_advantage_condition())
            or not entrant_land_advantage_leaf_is_closed(condition["all"][1])
            or len(program.effects) != 1 or FrozenMap(program.effects[0]) != FrozenMap(expected)
            or program.effects[0]["tapped"] is not False or program.target_schema is not None
            or program.cost_schema is not None or program.handlers
            or not {"intervening_condition", ENTRANT_LAND_ADVANTAGE_COVERAGE} <= set(program.coverage)):
        raise ValueError("Land advantage requires the closed opponent land trigger and its private land entry")
    return (ENTRANT_LAND_ADVANTAGE_CAPABILITY, "trigger.condition.fixed_public_state",
            "trigger.effect.fixed_event", "trigger.event.normalized_zone_change",
            "trigger.placement.apnap", "zone.change.destination_replacement", "zone.move.fixed_private_hand_choice")


def _recheck_greatest_artifact_upkeep(host, program, context, controller) -> bool:
    from ..public_characteristic_aggregates import current_greatest_artifact_mana_value_control

    greatest_artifact_upkeep_runtime_dependencies(program)
    observer = context.get("intervening_condition_controller")
    copy_from = context.get("copied_from_stack")
    if (context.get("event") != "step.begin" or context.get("step") != "upkeep"
            or type(observer) is not str or observer not in host.state.players or context.get("player") != observer
            or type(controller) is not str or controller not in host.state.players
            or ("copied_from_stack" in context and (type(copy_from) is not str or not copy_from))
            or (copy_from is None and controller != observer)):
        raise EventSubscriptionError("Greatest artifact recheck lost its original upkeep or resolving controller")
    # Occurrence facts stay sealed to the original upkeep, including for copies.
    # 'You' in the resolving condition belongs to the controller of this stack item.
    return current_greatest_artifact_mana_value_control(
        host.state, controller, host._public_object_query_result,
    ) is True


def source_event_intervening_recheck(host, program, *, context, controller, source_object_id):
    """Recheck a condition whose source occurrence carries the relevant facts."""
    from ..other_spell_history_model import OTHER_SPELL_CAST_MECHANIC
    from .attached_greatest_power_condition import attached_greatest_power_event_condition, recheck_attached_greatest_power
    from .combat_damage_entry_history import COMBAT_DAMAGE_ENTRY_COVERAGE, recheck_combat_damage_entry
    from .entrant_name_absence import ENTRANT_NAME_ABSENCE_COVERAGE, recheck_entrant_name_absence
    from .other_spell_history_shapes import recheck_other_spell_self_cast
    from .trigger_source_information import original_source_tap_expectation, recheck_original_source_tap
    from ..land_entry_condition_model import (
        ENTRANT_LAND_ADVANTAGE_COVERAGE, OTHER_LAND_ARRIVAL_COVERAGE, OTHER_LAND_ARRIVAL_EVENT,
    )

    from .departure_counter_transfer import (
        DEPARTURE_COUNTER_TRANSFER_COVERAGE, departure_counter_condition_holds,
    )

    from .opponent_off_turn_draw import OFF_TURN_DRAW_CAPABILITY, off_turn_draw_holds

    from .opposing_end_step_advantage import COVERAGE as END_STEP_ADVANTAGE, recheck_opposing_end_step_advantage
    try:
        if GREATEST_ARTIFACT_MANA_VALUE_CAPABILITY in program.capability_dependencies:
            return _recheck_greatest_artifact_upkeep(host, program, context, controller)
        if OFF_TURN_DRAW_CAPABILITY in program.capability_dependencies:
            if context.get("event") != "card.drawn":
                raise ValueError("An off-turn draw recheck lost its committed occurrence")
            return off_turn_draw_holds(host.state, context)
        if END_STEP_ADVANTAGE in program.coverage:
            return recheck_opposing_end_step_advantage(host, program, context, controller)
        if DEPARTURE_COUNTER_TRANSFER_COVERAGE in program.coverage:
            return departure_counter_condition_holds(context, controller)
        from ..extra_combat_model import MAIN_PHASE_EXTRA_COMBAT_MECHANIC, own_main_phase_for_combat
        from .effect_node_capabilities import main_phase_extra_combat_program_dependencies

        if MAIN_PHASE_EXTRA_COMBAT_MECHANIC in program.coverage:
            if context.get("event") != "land.enter" or not main_phase_extra_combat_program_dependencies(program):
                raise ValueError("Main-phase combat recheck lost its original landfall trigger")
            return own_main_phase_for_combat(host.state, controller)
        if ENTRANT_LAND_ADVANTAGE_COVERAGE in program.coverage:
            if context.get("event") != OTHER_LAND_ARRIVAL_EVENT:
                raise ValueError("The land advantage recheck lost its normalized event")
            return entrant_land_advantage_holds(host, context, trigger_controller=controller)
        if OTHER_LAND_ARRIVAL_COVERAGE in program.coverage:
            if context.get("event") != OTHER_LAND_ARRIVAL_EVENT:
                raise ValueError("The land entry history recheck lost its normalized event")
            return other_land_arrival_holds(host.state, context)
        expected_tapped = original_source_tap_expectation(host.state, program)
        if expected_tapped is not None:
            return recheck_original_source_tap(
                host, context, event=program.event, controller=controller, source_object_id=source_object_id,
                expected_tapped=expected_tapped,
            )
        if COMBAT_DAMAGE_ENTRY_COVERAGE in program.coverage:
            return recheck_combat_damage_entry(host.state, context)
        if ENTRANT_NAME_ABSENCE_COVERAGE in program.coverage:
            return recheck_entrant_name_absence(host, context, controller)
        if OTHER_SPELL_CAST_MECHANIC in program.coverage:
            return recheck_other_spell_self_cast(
                host.state, context, controller=controller, source_object_id=source_object_id,
            )
        if attached_greatest_power_event_condition(program.event_condition) is not None:
            return recheck_attached_greatest_power(
                host, context, controller=controller, source_object_id=source_object_id,
            )
    except (ValueError, TypeError) as exc:
        raise GameRuleError(str(exc)) from exc
    from .paid_noncreature_counter_transform import paid_cast_transform_runtime_dependencies, recheck_committed_payment

    try:
        if paid_cast_transform_runtime_dependencies(program):
            return recheck_committed_payment(context)
    except (ValueError, TypeError) as exc:
        raise GameRuleError(str(exc)) from exc

    from .event_outcome_shapes import uses_zero_mana_cast_condition, zero_mana_cast_result_recheck

    if uses_zero_mana_cast_condition(program.event_condition):
        try:
            return zero_mana_cast_result_recheck(program, context)
        except (ValueError, TypeError) as exc:
            raise GameRuleError(str(exc)) from exc

    from ..attack_batch_model import DECLARED_ATTACK_POWER_COVERAGE
    from .attacked_player_life import ATTACKED_PLAYER_LIFE_COVERAGE, attacked_player_life_holds

    if DECLARED_ATTACK_POWER_COVERAGE in program.coverage:
        from .attack_batches import declared_attack_power_recheck
        try:
            return declared_attack_power_recheck(program, context, controller=controller, source_object_id=source_object_id)
        except (ValueError, TypeError) as exc:
            raise GameRuleError(str(exc)) from exc
    if ATTACKED_PLAYER_LIFE_COVERAGE in program.coverage:
        try:
            return attacked_player_life_holds(host, context, source_object_id=source_object_id, controller=controller)
        except (ValueError, TypeError) as exc:
            raise GameRuleError(str(exc)) from exc
    if 'ravenous_entry_x' in program.coverage:
        from ..ravenous_model import EntryXInformation, ENTRY_X_FIELD, validate_ravenous_condition
        try:
            validate_ravenous_condition(program.event_condition)
            info = EntryXInformation.from_dict(context.get(ENTRY_X_FIELD))
            if info.object_id != source_object_id or info.logical_object_id != context.get('source_logical_object_id'):
                raise ValueError('Ravenous recheck changed its original entry identity')
            return info.amount >= 5
        except (ValueError, TypeError) as exc:
            raise GameRuleError(str(exc)) from exc
    if "cast_from_nonhand_entry_intervening" in program.coverage:
        from .entry_cast_information import nonhand_entrant_was_cast_by

        try:
            return nonhand_entrant_was_cast_by(host, context, controller=controller)
        except (ValueError, TypeError) as exc:
            raise GameRuleError(str(exc)) from exc
    if 'source_entry_cast_intervening' in program.coverage:
        from .entry_cast_information import source_entry_cast_value
        from ..source_entry_cast_information import validate_source_entry_cast_condition
        from ..continuous_effect_model import ContinuousObjectIdentity
        try:
            validate_source_entry_cast_condition(program.event_condition)
            return source_entry_cast_value(host, context, controller=controller,
                source=ContinuousObjectIdentity(source_object_id, context.get('source_logical_object_id')),
                origin=program.event_condition.get('origin'), any_caster=program.event_condition.get('caster')=='any')
        except (ValueError, TypeError) as exc:
            raise GameRuleError(str(exc)) from exc
    return None


__all__ = [
    "EVENT_SUBSCRIPTIONS_FIELD",
    "EventSubscriptionError",
    "FixedEventSubscription",
    "FixedEventSubscriptionSet",
    "fixed_event_subscription_condition",
    "intervening_recheck_condition",
    "public_state_intervening_actual",
    "upkeep_hand_runtime_dependencies",
    "controller_keyword_absence_runtime_dependencies",
    "death_keyword_absence_runtime_dependencies",
    "source_event_intervening_recheck",
]
