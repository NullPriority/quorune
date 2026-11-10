from __future__ import annotations
from .token_creation_event_bindings import TOKEN_CREATION_VARIANTS
from .crime_bindings import CRIME_VARIANTS
from ..crime_model import crime_fact_dependencies

from dataclasses import dataclass, replace
from enum import Enum
from .trigger_event_vocabulary import FixedCounterTriggerEvent
import re
from typing import Any, Callable, Mapping, Sequence

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.source_references import SourceReferenceSpec
from .source_self_effect_templates import normalized_source_event_line, named_source_union_candidate
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
    FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
)
from ..rules.capabilities import CapabilityRegistry
from ..rules.event_subscriptions import FixedEventSubscriptionSet
from ..rules.scheduled_player_shapes import SCHEDULED_PLAYER_DEPARTURE_COVERAGE, SCHEDULED_PLAYER_MECHANIC
from .announced_damage_templates import fixed_announced_damage_template
from .dependency_gate import dependency_gate
from .modal_templates import FIXED_NONREPEATING_MODAL_MECHANIC
from .trigger_result_templates import binding_effect_template
from .fixed_public_event_trigger_bindings import source_damage_incarnation_condition, validate_public_trigger_metadata
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual
from .fixed_source_combat_growth import (
    FIXED_SOURCE_COMBAT_GROWTH_TEMPLATE_IDS,
    fixed_source_combat_growth_effect_template,
)
from .fixed_entry_return_requirements import (
    fixed_entry_return_effect_template,
)
from .spell_cast_predicates import (
    FixedSpellCastCharacteristicKind,
    FixedSpellCastCharacteristicQuery,
    FixedSpellCastCharacteristicTerm,
    FixedSpellCastController,
    FixedSpellCastOrigin,
    FixedSpellCastQuality,
    FixedSpellCastSubject,
    FixedSpellCastTurnRelation,
    fixed_spell_cast_binding_spec,
)
from .public_trigger_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY, PUBLIC_INTERVENING_IF_TRIGGER, PublicTriggerCondition,
    bind_public_intervening_trigger, public_trigger_condition_capabilities,
)
from .qualified_zone_event_bindings import QUALIFIED_ZONE_VARIANT, qualified_public_zone_event_binding_spec, public_binding_from_spec
from .aggregated_zone_event_bindings import AGGREGATED_ZONE_VARIANT, aggregated_public_zone_event_binding_spec
from .counter_placement_event_bindings import COUNTER_EVENT_VARIANTS
from .fixed_public_action_event_bindings import QUALIFIED_DAMAGE_BATCH_VARIANT, QUALIFIED_DAMAGE_SINGULAR_VARIANT
from .tap_state_event_bindings import (
    TAP_STATE_EVENT_VARIANT, TAP_STATE_EVENT_VARIANTS,
    public_trigger_binding_spec, tap_state_bound_result,
)
from .chosen_color_event_bindings import CHOSEN_COLOR_CAST_VARIANTS
from .chosen_type_event_bindings import CHOSEN_TYPE_EVENT_VARIANTS
from .explore_event_bindings import CREATURE_EXPLORE_VARIANT
from .mutate_event_bindings import MUTATE_SOURCE_VARIANT,MUTATE_TRIGGER_TEMPLATE


FIXED_COUNTER_EVENT_TRIGGER_MECHANIC = "fixed-counter-event-trigger"
FIXED_TYPED_EVENT_EFFECT_TRIGGER_MECHANIC = (
    "fixed-typed-event-effect-trigger"
)
FIXED_SPELL_CAST_CHARACTERISTIC_MECHANIC = (
    "trigger-event-spell-cast-fixed-characteristics"
)
FIXED_COUNTER_EVENT_TRIGGER_TEMPLATE_IDS = frozenset(
    {
        MUTATE_TRIGGER_TEMPLATE,
        'fixed-counter-creature-explore-trigger-v1',
        "fixed-counter-step-trigger-v1",
        "fixed-counter-controlled-land-entry-trigger-v1",
        "fixed-counter-controller-spell-cast-trigger-v1",
        "fixed-counter-spell-cast-trigger-v1",
        "fixed-counter-spell-cast-characteristic-trigger-v1",
        "fixed-counter-source-attacks-trigger-v1",
        "fixed-counter-controller-life-gain-trigger-v1",
        "fixed-counter-controller-card-draw-trigger-v1",
        "fixed-counter-controller-second-draw-trigger-v1",
        "fixed-counter-permanent-entry-trigger-v1",
        "fixed-counter-artifact-entry-trigger-v1",
        "fixed-counter-creature-entry-trigger-v1",
        "fixed-counter-enchantment-entry-trigger-v1",
        "fixed-counter-subtype-entry-trigger-v1",
        "fixed-counter-creature-death-trigger-v1",
        "fixed-counter-source-vehicle-entry-trigger-v1",
        "fixed-counter-source-vehicle-death-trigger-v1",
        "fixed-counter-source-graveyard-trigger-v1",
        "fixed-counter-source-combat-damage-player-trigger-v1",
        "fixed-counter-source-combat-damage-opponent-trigger-v1",
        "fixed-counter-source-damage-opponent-trigger-v1",
        "fixed-counter-source-dealt-damage-trigger-v1",
        "fixed-counter-source-received-damage-trigger-v1",
        "fixed-counter-source-received-combat-damage-trigger-v1",
        "fixed-counter-public-zone-trigger-v1",
        "fixed-counter-public-damage-trigger-v1",
        "fixed-counter-public-attack-trigger-v1",
        "fixed-counter-public-block-trigger-v1",
        "fixed-counter-public-cycle-trigger-v1",
        "fixed-counter-public-face-up-trigger-v1",
        "fixed-counter-public-designation-trigger-v1",
        "fixed-counter-public-multi-event-trigger-v1",
        "fixed-counter-public-room-door-trigger-v1",
        "fixed-counter-opponent-card-draw-trigger-v1",
        "fixed-counter-public-second-draw-trigger-v1",
        "fixed-counter-entry-return-public-zone-trigger-v1",
        "fixed-counter-heroic-spell-cast-trigger-v1",
        "fixed-counter-magecraft-spell-action-trigger-v1",
        "fixed-counter-constellation-entry-trigger-v1",
        "fixed-counter-battalion-attack-trigger-v1",
        "fixed-counter-public-state-source-zone-trigger-v1",
        "fixed-counter-tap-state-trigger-v1",
        "fixed-counter-placement-occurrence-trigger-v1",
    }
)
FIXED_TYPED_EVENT_EFFECT_TRIGGER_TEMPLATE_IDS = frozenset(
    template_id.replace(
        "fixed-counter-",
        "fixed-typed-effect-",
        1,
    )
    for template_id in FIXED_COUNTER_EVENT_TRIGGER_TEMPLATE_IDS
)
OPTIONAL_FIXED_COUNTER_EVENT_TRIGGER_MECHANIC = (
    "optional-fixed-counter-event-trigger"
)
OPTIONAL_COUNTER_PLACEMENT_OPERATION = "offer_optional_counter_placement"

_COUNTER_PLACEMENT_OPERATIONS = frozenset(
    {
        "place_counter_batch",
        "place_counters",
        "place_counters_on_set",
        "place_counters_on_targets",
        "place_player_counters",
    }
)
_ABILITY_WORD_PUBLIC_EVENT_VARIANTS = frozenset(
    {
        "heroic_source_targeted",
        "magecraft_instant_or_sorcery",
        "constellation_controlled_enchantment",
        "constellation_source_or_controlled_enchantment",
        "controlled_enchantment_entry_or_room_unlock",
        "battalion_source_and_two_others_attack",
    }
)
PUBLIC_ACTION_EVENT_BINDING_CLOSURE_VARIANTS = frozenset(
    {
        *TOKEN_CREATION_VARIANTS,
        *CRIME_VARIANTS,
        *CHOSEN_COLOR_CAST_VARIANTS,
        *CHOSEN_TYPE_EVENT_VARIANTS,
        CREATURE_EXPLORE_VARIANT,
        MUTATE_SOURCE_VARIANT,
        'player_expend',
        'public_waterbend',
        'public_ring_temptation', 'public_ring_bearer_choice',
        QUALIFIED_DAMAGE_BATCH_VARIANT, QUALIFIED_DAMAGE_SINGULAR_VARIANT,
        TAP_STATE_EVENT_VARIANT,
        "source_" + TAP_STATE_EVENT_VARIANT,
        "controller_attack_batch",
        "controller_attack_batch_at_least_2",
        "controller_attack_batch_at_least_3",
        "one_or_more_qualified_attack",
        "controller_cycles",
        "one_or_more_controlled_tokens_enter",
        "controlled_token_leaves",
        "another_controlled_creature_leaves",
        "another_controlled_creature_or_artifact_graveyard",
        "nontoken_creature_enters_controller_graveyard",
        "controlled_nontoken_artifact_graveyard",
        "another_controlled_nontoken_dragon_enters",
        "land_enters_during_controller_turn",
        "source_land_enters_untapped",
        "one_or_more_controlled_creatures_combat_damage_player",
        "one_or_more_controlled_artifact_creatures_combat_damage_player",
        "source_combat_damage_player_or_planeswalker",
        "source_combat_damage_player_or_battle",
        "opponent_dealt_noncombat_damage",
        "controller_sacrifices_source_or_another",
        *{
            f"{actor}_sacrifices_{subject}"
            for actor in ("controller", "opponent", "player")
            for subject in (
                "clue",
                "food",
                "blood_token",
                "token",
                "land",
                "creature",
                "artifact",
                "enchantment",
                "permanent",
                "artifact_or_creature",
                "qualified_permanent",
            )
        },
        *{
            f"source_{subject}_sacrificed"
            for subject in ("aura", "artifact", "creature")
        },
        *{
            f"{actor}_discards_{subject}"
            for actor in ("controller", "opponent", "player")
            for subject in ("card", "land", "creature", "nonland", "noncreature_nonland")
        },
        *{
            "one_or_more_controller_"
            f"{subject}_leave_graveyard{turn}"
            for subject in (
                "cards",
                "creature_cards",
                "artifact_cards",
                "artifact_or_creature_cards",
            )
            for turn in ("", "_during_turn")
        },
    }
)
MULTI_EVENT_BINDING_CLOSURE_VARIANTS = frozenset(
    {
        'source_block_pair_counterpart',
        "source_room_door_unlock",
        "controlled_enchantment_entry_or_room_unlock",
        "enchanted_creature_tap_or_received_damage",
        "independent_dual_event",
        "qualified_zone_event_pair",
        "source_enters_or_attacks",
        "source_enters_or_dies",
        "source_attacks_or_blocks",
        "source_blocks_or_becomes_blocked",
        "source_enters_or_leaves_the_battlefield",
        "source_enters_or_is_put_into_a_graveyard_from_the_battlefield",
        "source_enters_or_is_turned_face_up",
        "source_enters_or_deals_combat_damage_to_a_player",
        "source_enters_or_becomes_monstrous",
        "source_transform_pair",
        "source_artifact_enters_and_is_sacrificed",
        "enchanted_creature_attacks_or_blocks",
        "source_attacks_or_blocks_with_controlled_dinosaur",
    }
)
from .public_event_closure_variants import PUBLIC_EVENT_BINDING_CLOSURE_VARIANTS
_ALL_PUBLIC_EVENT_BINDING_CLOSURE_VARIANTS = frozenset(
    {
        *PUBLIC_EVENT_BINDING_CLOSURE_VARIANTS,
        *COUNTER_EVENT_VARIANTS,
        *PUBLIC_ACTION_EVENT_BINDING_CLOSURE_VARIANTS,
        *MULTI_EVENT_BINDING_CLOSURE_VARIANTS,
    }
)
_NONCOUNTER_PUBLIC_EVENT_VARIANTS = (_ALL_PUBLIC_EVENT_BINDING_CLOSURE_VARIANTS
    - TAP_STATE_EVENT_VARIANTS - COUNTER_EVENT_VARIANTS - {QUALIFIED_DAMAGE_BATCH_VARIANT, QUALIFIED_DAMAGE_SINGULAR_VARIANT})
_ONE_OR_MORE_PUBLIC_EVENT_VARIANTS = frozenset(
    {
        QUALIFIED_DAMAGE_BATCH_VARIANT,
        "one_or_more_qualified_attack",
        AGGREGATED_ZONE_VARIANT,
        "controller_attack_batch",
        "controller_attack_batch_at_least_2",
        "controller_attack_batch_at_least_3",
        "one_or_more_controlled_tokens_enter",
        "one_or_more_controlled_creatures_combat_damage_player",
        "one_or_more_controlled_artifact_creatures_combat_damage_player",
        *{
            variant
            for variant in PUBLIC_ACTION_EVENT_BINDING_CLOSURE_VARIANTS
            if variant.startswith("one_or_more_controller_")
            and "leave_graveyard" in variant
        },
    }
)
_SCHEDULED_TRIGGER = re.compile(
    r"^At the beginning of "
    r"(?P<schedule>your upkeep|each upkeep|your draw step|your end step|each end step|"
    r"the end step|combat on your turn|each combat|your first main phase|your precombat main phase|"
    r"your second main phase|your postcombat main phase), (?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_LAND_ENTRY_TRIGGER = re.compile(
    r"^(?:Landfall\s+[—-]\s+)?Whenever a land you control enters, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_ATTACK_TRIGGER = re.compile(
    r"^Whenever this creature attacks, (?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLER_LIFE_GAIN_TRIGGER = re.compile(
    r"^Whenever you gain life, (?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLER_CARD_DRAW_TRIGGER = re.compile(
    r"^Whenever you draw a card, (?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLER_SECOND_DRAW_TRIGGER = re.compile(
    r"^Whenever you draw your second card each turn, (?P<body>.+)$",
    re.IGNORECASE,
)
_ZONE_CHANGE_TRIGGER = re.compile(
    r"^Whenever "
    r"(?:this (?P<this_kind>artifact|creature|enchantment|permanent) or )?"
    r"(?P<article>another|a|an) "
    r"(?:(?P<token>nontoken|token) )?"
    r"(?P<kind>artifact|creature|enchantment|permanent)"
    r"(?: (?P<relation>you control|an opponent controls|you don't control))? "
    r"(?P<transition>enters|dies), (?P<body>.+)$",
    re.IGNORECASE,
)
_SUBTYPE_ENTRY_TRIGGER = re.compile(
    r"^Whenever "
    r"(?:(?P<this_source>this (?:artifact|creature|enchantment|permanent)) or )?"
    r"(?P<article>another|a|an) "
    r"(?P<subtype>[A-Z][A-Za-z'-]*)"
    r"(?: (?P<relation>you control|an opponent controls|you don't control))? "
    r"enters, (?P<body>.+)$",
)
_SOURCE_ARTIFACT_SUBTYPE_ZONE_TRIGGER = re.compile(
    r"^When this (?:Vehicle|Spacecraft) (?P<transition>enters|dies), (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_GRAVEYARD_TRIGGER = re.compile(
    r"^When this (?P<kind>artifact|Aura|enchantment|land) is put into a "
    r"graveyard from the battlefield, (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_DAMAGE_TRIGGER = re.compile(
    r"^Whenever this creature (?P<event>deals combat damage to a player|"
    r"deals combat damage to an opponent|deals damage to a player|deals damage to an opponent|"
    r"is dealt damage), (?P<body>.+)$",
    re.IGNORECASE,
)
_ZONE_SUBJECT_TYPES = frozenset(
    {"artifact", "creature", "enchantment", "land", "permanent"}
)


class FixedCounterZoneController(str, Enum):
    """Closed controller relations for public zone-change subjects."""

    ANY = "any"
    SOURCE = "source_controller"
    OPPONENT = "opponent"


@dataclass(frozen=True, slots=True)
class FixedCounterZoneSubject:
    """Immutable public subject predicate for one normalized zone event."""

    permanent_type: str
    controller: FixedCounterZoneController
    exclude_source: bool = False
    token: bool | None = None
    subtype: str | None = None
    include_source: bool = False

    def __post_init__(self) -> None:
        if self.permanent_type not in _ZONE_SUBJECT_TYPES - {"land"}:
            raise ValueError("Fixed counter zone subjects require a closed type")
        if not isinstance(self.controller, FixedCounterZoneController):
            raise ValueError(
                "Fixed counter zone subjects require a closed controller relation"
            )
        if type(self.exclude_source) is not bool:
            raise ValueError(
                "Fixed counter zone subject exclusion must be a boolean"
            )
        if self.token is not None and type(self.token) is not bool:
            raise ValueError(
                "Fixed counter zone subject token state must be boolean or absent"
            )
        if self.subtype is not None:
            normalized_subtype = self.subtype.casefold()
            if (
                self.permanent_type != "permanent"
                or re.fullmatch(r"[a-z][a-z'-]*", normalized_subtype) is None
                or normalized_subtype in _ZONE_SUBJECT_TYPES
            ):
                raise ValueError(
                    "Fixed counter zone subject subtypes must be one closed "
                    "non-type word over permanent-entry facts"
                )
            object.__setattr__(self, "subtype", normalized_subtype)
        if type(self.include_source) is not bool:
            raise ValueError(
                "Fixed counter zone subject source inclusion must be a boolean"
            )
        if self.include_source and (
            self.subtype is None or self.exclude_source
        ):
            raise ValueError(
                "Fixed counter zone source inclusion requires one subtype and "
                "cannot exclude the source"
            )

    @property
    def event_condition(self) -> Mapping[str, Any] | None:
        conditions: list[Mapping[str, Any]] = []
        if self.controller is FixedCounterZoneController.SOURCE:
            conditions.append(
                {
                    "field": "controller",
                    "op": "eq",
                    "value": "$source.controller",
                }
            )
        elif self.controller is FixedCounterZoneController.OPPONENT:
            conditions.append(
                {
                    "field": "controller",
                    "op": "ne",
                    "value": "$source.controller",
                }
            )
        if self.subtype is not None:
            subtype_condition: Mapping[str, Any] = {
                "field": "subtypes",
                "op": "contains_any",
                "value": [self.subtype],
            }
            conditions.append(
                {
                    "any": [
                        {
                            "field": "card",
                            "op": "eq",
                            "value": "$source.ref",
                        },
                        subtype_condition,
                    ]
                }
                if self.include_source
                else subtype_condition
            )
        if self.exclude_source:
            conditions.append(
                {
                    "field": "card",
                    "op": "ne",
                    "value": "$source.ref",
                }
            )
        if self.token is not None:
            conditions.append(
                {
                    "field": "token",
                    "op": "eq",
                    "value": self.token,
                }
            )
        if not conditions:
            return {
                "field": "token",
                "op": "in",
                "value": [False, True],
            }
        if len(conditions) == 1:
            return conditions[0]
        return {"all": conditions}

    @property
    def variant(self) -> str:
        token = (
            "token"
            if self.token is True
            else "nontoken"
            if self.token is False
            else "any_object"
        )
        source = "other" if self.exclude_source else "including_source"
        values = [self.permanent_type, self.controller.value, source, token]
        if self.subtype is not None:
            values.append(f"subtype-{self.subtype}")
        return ":".join(values)


@dataclass(frozen=True, slots=True)
class FixedCounterTriggerBinding:
    """Immutable event subscription shared by closed fixed-effect triggers."""

    event: FixedCounterTriggerEvent
    variant: str
    body: str
    zone_subject: FixedCounterZoneSubject | None = None
    spell_subject: FixedSpellCastSubject | None = None
    public_condition: Mapping[str, Any] | None = None
    public_active_zone: str | None = None
    public_mechanic: str | None = None
    public_template_id: str | None = None
    public_capabilities: tuple[str, ...] = ()
    public_state_condition: PublicTriggerCondition | None = None
    attacked_player_life: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.event, FixedCounterTriggerEvent):
            raise ValueError("Fixed counter triggers require a closed event")
        if type(self.variant) is not str or not self.variant:
            raise ValueError("Fixed counter trigger variants must be nonempty")
        if type(self.body) is not str or not self.body:
            raise ValueError("Fixed counter trigger bodies must be nonempty")
        if type(self.attacked_player_life) is not bool or self.attacked_player_life and (
            self.event is not FixedCounterTriggerEvent.SOURCE_ATTACKS or self.variant != 'source_attacks'
            or self.public_mechanic is not None or self.public_state_condition is not None
        ):
            raise ValueError('Attacked-player life requires one source attack')
        zone_events = {
            FixedCounterTriggerEvent.PERMANENT_ENTER,
            FixedCounterTriggerEvent.ARTIFACT_ENTER,
            FixedCounterTriggerEvent.CREATURE_ENTER,
            FixedCounterTriggerEvent.ENCHANTMENT_ENTER,
            FixedCounterTriggerEvent.CREATURE_DIES,
        }
        if (
            self.public_mechanic is None
            and (self.event in zone_events) != (self.zone_subject is not None)
        ):
            raise ValueError(
                "Fixed counter zone-change events require exactly one typed subject"
            )
        if self.public_mechanic is None and (
            (
                self.event is FixedCounterTriggerEvent.CONTROLLER_SPELL_CAST
            )
            != (self.spell_subject is not None)
        ):
            raise ValueError(
                "Fixed spell-cast events require exactly one typed subject"
            )
        validate_public_trigger_metadata(self)
        if self.public_state_condition is not None and not isinstance(
            self.public_state_condition,
            PublicTriggerCondition,
        ):
            raise ValueError(
                "Intervening-if triggers require one typed public-state condition"
            )

    @property
    def template_id(self) -> str:
        if self.public_template_id is not None:
            return self.public_template_id
        source_template = {
            FixedCounterTriggerEvent.SOURCE_VEHICLE_ENTER:
                "fixed-counter-source-vehicle-entry-trigger-v1",
            FixedCounterTriggerEvent.SOURCE_VEHICLE_DIES:
                "fixed-counter-source-vehicle-death-trigger-v1",
            FixedCounterTriggerEvent.SOURCE_GRAVEYARD:
                "fixed-counter-source-graveyard-trigger-v1",
            FixedCounterTriggerEvent.SOURCE_DEALT_DAMAGE:
                "fixed-counter-source-received-damage-trigger-v1",
            FixedCounterTriggerEvent.SOURCE_RECEIVED_COMBAT_DAMAGE:
                "fixed-counter-source-received-combat-damage-trigger-v1",
        }.get(self.event)
        if source_template is not None:
            return source_template
        if self.event is FixedCounterTriggerEvent.SOURCE_DEALS_DAMAGE:
            return {
                "source_combat_damage_player": (
                    "fixed-counter-source-combat-damage-player-trigger-v1"
                ),
                "source_combat_damage_opponent": (
                    "fixed-counter-source-combat-damage-opponent-trigger-v1"
                ),
                "source_damage_opponent": (
                    "fixed-counter-source-damage-opponent-trigger-v1"
                ),
                "source_damage_player": "fixed-counter-source-damage-player-trigger-v1",
            }[self.variant]
        if self.zone_subject is not None and self.zone_subject.subtype is not None:
            return "fixed-counter-subtype-entry-trigger-v1"
        if (
            self.event is FixedCounterTriggerEvent.CONTROLLER_SPELL_CAST
            and self.spell_subject is not None
            and self.spell_subject.characteristic_query is not None
        ):
            return "fixed-counter-spell-cast-characteristic-trigger-v1"
        if (
            self.event is FixedCounterTriggerEvent.CONTROLLER_SPELL_CAST
            and self.variant
            not in {"noncreature", "instant_or_sorcery"}
        ):
            return "fixed-counter-spell-cast-trigger-v1"
        return {
            FixedCounterTriggerEvent.STEP_BEGIN:
                "fixed-counter-step-trigger-v1",
            FixedCounterTriggerEvent.CONTROLLED_LAND_ENTER:
                "fixed-counter-controlled-land-entry-trigger-v1",
            FixedCounterTriggerEvent.CONTROLLER_SPELL_CAST:
                "fixed-counter-controller-spell-cast-trigger-v1",
            FixedCounterTriggerEvent.SOURCE_ATTACKS:
                "fixed-counter-source-attacks-trigger-v1",
            FixedCounterTriggerEvent.CONTROLLER_LIFE_GAIN:
                "fixed-counter-controller-life-gain-trigger-v1",
            FixedCounterTriggerEvent.CONTROLLER_CARD_DRAW:
                "fixed-counter-controller-card-draw-trigger-v1",
            FixedCounterTriggerEvent.CONTROLLER_SECOND_DRAW:
                "fixed-counter-controller-second-draw-trigger-v1",
            FixedCounterTriggerEvent.PERMANENT_ENTER:
                "fixed-counter-permanent-entry-trigger-v1",
            FixedCounterTriggerEvent.ARTIFACT_ENTER:
                "fixed-counter-artifact-entry-trigger-v1",
            FixedCounterTriggerEvent.CREATURE_ENTER:
                "fixed-counter-creature-entry-trigger-v1",
            FixedCounterTriggerEvent.ENCHANTMENT_ENTER:
                "fixed-counter-enchantment-entry-trigger-v1",
            FixedCounterTriggerEvent.CREATURE_DIES:
                "fixed-counter-creature-death-trigger-v1",
        }[self.event]

    @property
    def typed_effect_template_id(self) -> str:
        """Return the generic typed-effect composition identity."""

        return self.template_id.replace(
            "fixed-counter-",
            "fixed-typed-effect-",
            1,
        )

    @property
    def active_zone(self) -> str:
        if self.public_active_zone is not None:
            return self.public_active_zone
        if self.spell_subject is not None and self.spell_subject.source_spell:
            return "stack"
        return "battlefield"

    @property
    def explicit_capabilities(self) -> tuple[str, ...]:
        from ..received_damage_model import RECEIVED_DAMAGE_CAPABILITY
        from ..rules.attacked_player_life import ATTACKED_PLAYER_LIFE_CAPABILITY
        receiving = (RECEIVED_DAMAGE_CAPABILITY,) if self.event in {
            FixedCounterTriggerEvent.SOURCE_DEALT_DAMAGE, FixedCounterTriggerEvent.SOURCE_RECEIVED_COMBAT_DAMAGE,
        } else ()
        from ..characteristic_fragments import characteristic_quantity_capabilities
        from ..public_history_query_model import public_history_query_dependencies
        condition_capabilities=characteristic_quantity_capabilities(
            self.public_state_condition.to_dict() if self.public_state_condition is not None else {})
        from ..rules.spell_cast_events import spell_cast_keyword_condition_dependencies

        ordinal=self.spell_subject.qualified_ordinal_spec if self.spell_subject is not None else None
        cast_keywords = spell_cast_keyword_condition_dependencies(
            self.spell_subject.event_condition if self.spell_subject is not None else None,
        )
        attacked_life = (ATTACKED_PLAYER_LIFE_CAPABILITY,) if self.attacked_player_life else ()
        return (*receiving,*self.public_capabilities,*condition_capabilities,*attacked_life,*cast_keywords,*(ordinal.capability_dependencies if ordinal else ()),*public_history_query_dependencies(
            self.public_state_condition.to_dict() if self.public_state_condition is not None else {}), *(("trigger.event.spell_cast_creature_target",)
            if self.spell_subject is not None and self.spell_subject.targets_creature else ()))

    @property
    def event_mechanics(self) -> tuple[str, ...]:
        """Return only the normalized-event owners this binding consumes."""

        if (
            self.event is FixedCounterTriggerEvent.CONTROLLER_SPELL_CAST
            and self.spell_subject is not None
            and (
                self.spell_subject.characteristic_query is not None
                or self.spell_subject.extended
            )
        ):
            return (
                "trigger-event-normalized-spell-cast",
                FIXED_SPELL_CAST_CHARACTERISTIC_MECHANIC,
            )
        if self.public_mechanic is not None:
            return (self.public_mechanic,)
        return {
            FixedCounterTriggerEvent.STEP_BEGIN: (),
            FixedCounterTriggerEvent.CONTROLLED_LAND_ENTER: (
                "trigger-event-normalized-zone-change",
            ),
            FixedCounterTriggerEvent.CONTROLLER_SPELL_CAST: (
                "trigger-event-normalized-spell-cast",
            ),
            FixedCounterTriggerEvent.SOURCE_ATTACKS: (
                "trigger-event-normalized-self-attack",
            ),
            FixedCounterTriggerEvent.CONTROLLER_LIFE_GAIN: (
                "trigger-event-normalized-life-gain",
            ),
            FixedCounterTriggerEvent.CONTROLLER_CARD_DRAW: (
                "trigger-event-normalized-card-draw",
            ),
            FixedCounterTriggerEvent.CONTROLLER_SECOND_DRAW: (
                "trigger-event-normalized-card-draw",
            ),
            FixedCounterTriggerEvent.PERMANENT_ENTER: (
                "trigger-event-normalized-zone-change",
            ),
            FixedCounterTriggerEvent.ARTIFACT_ENTER: (
                "trigger-event-normalized-zone-change",
            ),
            FixedCounterTriggerEvent.CREATURE_ENTER: (
                "trigger-event-normalized-zone-change",
            ),
            FixedCounterTriggerEvent.ENCHANTMENT_ENTER: (
                "trigger-event-normalized-zone-change",
            ),
            FixedCounterTriggerEvent.CREATURE_DIES: (
                "trigger-event-normalized-zone-change",
            ),
            FixedCounterTriggerEvent.SOURCE_VEHICLE_ENTER: (
                "trigger-event-normalized-zone-change",
            ),
            FixedCounterTriggerEvent.SOURCE_VEHICLE_DIES: (
                "trigger-event-normalized-zone-change",
            ),
            FixedCounterTriggerEvent.SOURCE_GRAVEYARD: (
                "trigger-event-normalized-zone-change",
            ),
            FixedCounterTriggerEvent.SOURCE_DEALS_DAMAGE: (
                "trigger-event-normalized-damage",
            ),
            FixedCounterTriggerEvent.SOURCE_DEALT_DAMAGE: (
                "trigger-event-normalized-damage",
                "trigger-event-normalized-received-damage",
            ),
            FixedCounterTriggerEvent.SOURCE_RECEIVED_COMBAT_DAMAGE: (
                "trigger-event-normalized-damage",
                "trigger-event-normalized-received-damage",
            ),
        }[self.event]

    @property
    def _base_event_condition(self) -> Mapping[str, Any] | None:
        if self.public_mechanic is not None:
            return self.public_condition
        if self.zone_subject is not None:
            return self.zone_subject.event_condition
        if self.event is FixedCounterTriggerEvent.CONTROLLED_LAND_ENTER:
            return {
                "field": "controller",
                "op": "eq",
                "value": "$source.controller",
            }
        if self.event is FixedCounterTriggerEvent.CONTROLLER_SPELL_CAST:
            assert self.spell_subject is not None
            return self.spell_subject.event_condition
        if self.event is FixedCounterTriggerEvent.SOURCE_ATTACKS:
            return {
                "field": "card",
                "op": "eq",
                "value": "$source.ref",
            }
        if self.event in {
            FixedCounterTriggerEvent.SOURCE_VEHICLE_ENTER,
            FixedCounterTriggerEvent.SOURCE_VEHICLE_DIES,
            FixedCounterTriggerEvent.SOURCE_GRAVEYARD,
        }:
            return None
        if self.event is FixedCounterTriggerEvent.SOURCE_DEALS_DAMAGE:
            conditions: list[Mapping[str, Any]] = [
                {
                    "field": "target_kind",
                    "op": "eq",
                    "value": "player",
                }
            ]
            if "combat_damage" in self.variant:
                conditions.append(
                    {
                        "field": "combat",
                        "op": "truthy",
                        "value": True,
                    }
                )
            if self.variant.endswith("opponent"):
                conditions.append(
                    {
                        "field": "target",
                        "op": "ne",
                        "value": "$source.controller",
                    }
                )
            conditions.append(source_damage_incarnation_condition())
            return {"all": conditions}
        if self.event in {FixedCounterTriggerEvent.SOURCE_DEALT_DAMAGE, FixedCounterTriggerEvent.SOURCE_RECEIVED_COMBAT_DAMAGE}:
            recipient = {
                "field": "target",
                "op": "eq",
                "value": "$source.ref",
            }
            return {"all": [recipient, {"field": "combat", "op": "eq", "value": True}]} if self.event is FixedCounterTriggerEvent.SOURCE_RECEIVED_COMBAT_DAMAGE else recipient
        if self.event in {
            FixedCounterTriggerEvent.CONTROLLER_LIFE_GAIN,
            FixedCounterTriggerEvent.CONTROLLER_CARD_DRAW,
            FixedCounterTriggerEvent.CONTROLLER_SECOND_DRAW,
        }:
            return {
                "field": "player",
                "op": "eq",
                "value": "$source.controller",
            }
        step, controller_only = {
            "your upkeep": ("upkeep", True),
            "each upkeep": ("upkeep", False),
            "your draw step": ("draw", True),
            "your end step": ("end_step", True),
            "each end step": ("end_step", False),
            "the end step": ("end_step", False),
            "combat on your turn": ("beginning_combat", True),
            "each combat": ("beginning_combat", False),
            'your first main phase': ('main',True),
            'your precombat main phase': ('main',True),
            'your second main phase': ('main',True),
            'your postcombat main phase': ('main',True),
        }[self.variant]
        conditions: list[Mapping[str, Any]] = [
            {"field": "step", "op": "eq", "value": step}
        ]
        if self.variant in {'your first main phase','your precombat main phase','your second main phase','your postcombat main phase'}:
            conditions.append({'field':'phase','op':'eq','value':'precombat_main' if self.variant in {'your first main phase','your precombat main phase'} else 'postcombat_main'})
        if controller_only:
            conditions.insert(
                0,
                {
                    "field": "player",
                    "op": "eq",
                    "value": "$source.controller",
                },
            )
        return {"all": conditions}

    @property
    def event_condition(self) -> Mapping[str, Any] | None:
        if self.attacked_player_life:
            from ..rules.attacked_player_life import attacked_player_life_condition
            return attacked_player_life_condition()
        condition = self._base_event_condition
        if self.public_state_condition is None:
            return condition
        public_condition = {
            "field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
            "op": "truthy",
            "value": True,
            "condition": self.public_state_condition.to_dict(),
        }
        if condition is None:
            return public_condition
        subscriptions = FixedEventSubscriptionSet.from_condition(condition)
        if subscriptions is not None:
            return subscriptions.with_shared_condition(public_condition).to_condition()
        return {"all": [condition, public_condition]}


def _zone_change_trigger_binding(
    material_line: str,
    *,
    card_name: str | None = None,
) -> FixedCounterTriggerBinding | None:
    normalized_line = material_line
    if card_name and named_source_union_candidate(material_line):
        named_source = re.match(
            rf"^Whenever {re.escape(card_name)} or another "
            r"(?P<subject>[A-Za-z][A-Za-z'-]*)\b",
            material_line,
            re.IGNORECASE,
        )
        if named_source is not None:
            subject = named_source.group("subject")
            source_kind = (
                subject
                if subject.casefold() in _ZONE_SUBJECT_TYPES - {"land"}
                else "permanent"
            )
            normalized_line = (
                f"Whenever this {source_kind} or another {subject}"
                + material_line[named_source.end() :]
            )
    match = _ZONE_CHANGE_TRIGGER.fullmatch(normalized_line)
    if match is None:
        subtype_match = _SUBTYPE_ENTRY_TRIGGER.fullmatch(normalized_line)
        if subtype_match is None:
            return None
        article = subtype_match.group("article").casefold()
        subtype = subtype_match.group("subtype").casefold()
        expected_article = "an" if subtype[0] in "aeiou" else "a"
        if (
            subtype in _ZONE_SUBJECT_TYPES
            or article not in {"another", expected_article}
        ):
            return None
        this_source = subtype_match.group("this_source")
        if this_source is not None and article != "another":
            return None
        relation = str(subtype_match.group("relation") or "").casefold()
        controller = {
            "": FixedCounterZoneController.ANY,
            "you control": FixedCounterZoneController.SOURCE,
            "an opponent controls": FixedCounterZoneController.OPPONENT,
            "you don't control": FixedCounterZoneController.OPPONENT,
        }[relation]
        subject = FixedCounterZoneSubject(
            permanent_type="permanent",
            controller=controller,
            exclude_source=(article == "another" and this_source is None),
            subtype=subtype,
            include_source=this_source is not None,
        )
        return FixedCounterTriggerBinding(
            event=FixedCounterTriggerEvent.PERMANENT_ENTER,
            variant=subject.variant,
            body=subtype_match.group("body"),
            zone_subject=subject,
        )
    article = match.group("article").casefold()
    kind = match.group("kind").casefold()
    expected_article = (
        "an" if kind in {"artifact", "enchantment"} else "a"
    )
    if article not in {"another", expected_article}:
        return None
    this_kind = match.group("this_kind")
    if this_kind is not None and (
        article != "another" or this_kind.casefold() != kind
    ):
        return None
    transition = match.group("transition").casefold()
    if transition == "dies" and kind != "creature":
        return None
    relation = str(match.group("relation") or "").casefold()
    controller = {
        "": FixedCounterZoneController.ANY,
        "you control": FixedCounterZoneController.SOURCE,
        "an opponent controls": FixedCounterZoneController.OPPONENT,
        "you don't control": FixedCounterZoneController.OPPONENT,
    }[relation]
    token_text = match.group("token")
    token = (
        None
        if token_text is None
        else token_text.casefold() == "token"
    )
    subject = FixedCounterZoneSubject(
        permanent_type=kind,
        controller=controller,
        exclude_source=article == "another" and this_kind is None,
        token=token,
    )
    event = (
        FixedCounterTriggerEvent.CREATURE_DIES
        if transition == "dies"
        else {
            "permanent": FixedCounterTriggerEvent.PERMANENT_ENTER,
            "artifact": FixedCounterTriggerEvent.ARTIFACT_ENTER,
            "creature": FixedCounterTriggerEvent.CREATURE_ENTER,
            "enchantment": FixedCounterTriggerEvent.ENCHANTMENT_ENTER,
        }[kind]
    )
    return FixedCounterTriggerBinding(
        event=event,
        variant=subject.variant,
        body=match.group("body"),
        zone_subject=subject,
    )


def _source_event_trigger_binding(
    material_line: str,
) -> FixedCounterTriggerBinding | None:
    vehicle = _SOURCE_ARTIFACT_SUBTYPE_ZONE_TRIGGER.fullmatch(material_line)
    if vehicle is not None:
        transition = vehicle.group("transition").casefold()
        return FixedCounterTriggerBinding(
            event=(
                FixedCounterTriggerEvent.SOURCE_VEHICLE_DIES
                if transition == "dies"
                else FixedCounterTriggerEvent.SOURCE_VEHICLE_ENTER
            ),
            variant=f"source_vehicle_{transition}",
            body=vehicle.group("body"),
        )
    graveyard = _SOURCE_GRAVEYARD_TRIGGER.fullmatch(material_line)
    if graveyard is not None:
        return FixedCounterTriggerBinding(
            event=FixedCounterTriggerEvent.SOURCE_GRAVEYARD,
            variant=f"source_{graveyard.group('kind').casefold()}_graveyard",
            body=graveyard.group("body"),
        )
    damage = _SOURCE_DAMAGE_TRIGGER.fullmatch(material_line)
    if damage is not None:
        event = damage.group("event").casefold()
        return FixedCounterTriggerBinding(
            event=(
                FixedCounterTriggerEvent.SOURCE_DEALT_DAMAGE
                if event == "is dealt damage"
                else FixedCounterTriggerEvent.SOURCE_DEALS_DAMAGE
            ),
            variant={
                "deals combat damage to a player": (
                    "source_combat_damage_player"
                ),
                "deals combat damage to an opponent": (
                    "source_combat_damage_opponent"
                ),
                "deals damage to an opponent": "source_damage_opponent",
                "deals damage to a player": "source_damage_player",
                "is dealt damage": "source_dealt_damage",
            }[event],
            body=damage.group("body"),
        )
    return None


def _public_state_source_self_trigger_binding(
    material_line: str,
    *,
    card_name: str | None,
) -> FixedCounterTriggerBinding | None:
    """Bind only the source-self zone forms admitted by typed intervening-if."""

    subjects = (
        r"this (?:artifact|Aura|card|creature|enchantment|Equipment|land|permanent)"
    )
    if card_name:
        subjects = rf"(?:{subjects}|{SourceReferenceSpec(card_name).regex_pattern})"
    match = re.fullmatch(
        rf"(?:When|Whenever) {subjects} (?P<event>enters|dies), "
        r"(?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    event = match.group("event").casefold()
    return FixedCounterTriggerBinding(
        event=(
            FixedCounterTriggerEvent.SOURCE_VEHICLE_DIES
            if event == "dies"
            else FixedCounterTriggerEvent.SOURCE_VEHICLE_ENTER
        ),
        variant=f"public_state_source_{event}",
        body=match.group("body"),
        public_active_zone="battlefield",
        public_mechanic="trigger-event-normalized-zone-change",
        public_template_id="fixed-counter-public-state-source-zone-trigger-v1",
    )


def _public_trigger_binding(
    material_line: str,
    *,
    card_name: str | None,
) -> FixedCounterTriggerBinding | None:
    spec = public_trigger_binding_spec(material_line, card_name=card_name)
    if spec is None:
        return None
    from .attacking_card_entry_templates import source_graveyard_attacking_entry_binding
    return public_binding_from_spec(source_graveyard_attacking_entry_binding(spec),
        binding_type=FixedCounterTriggerBinding, event_type=FixedCounterTriggerEvent)


def fixed_counter_trigger_binding(
    material_line: str,
    *,
    card_name: str | None = None,
    _source_normalized: bool = False,
    _graveyard_self_return: bool = True,
) -> FixedCounterTriggerBinding | None:
    if _graveyard_self_return:
        from .graveyard_self_return import graveyard_self_return_binding
        returned=graveyard_self_return_binding(material_line,card_name=card_name,compile_binding=lambda text:
            fixed_counter_trigger_binding(text,card_name=card_name,_source_normalized=_source_normalized,_graveyard_self_return=False))
        if returned is not None:return returned
    normalized = normalized_source_event_line(material_line, source_name=card_name, named_only=True) if not _source_normalized and card_name else None
    if normalized is not None:
        source_binding = fixed_counter_trigger_binding(normalized, card_name=card_name, _source_normalized=True)
        if source_binding is not None:return source_binding
    intervening = PUBLIC_INTERVENING_IF_TRIGGER.fullmatch(material_line)
    if intervening is not None:
        if (intervening['prefix'].casefold() == 'whenever this creature attacks a player'
                and intervening['condition'].casefold() == 'no opponent has more life than that player'):
            return FixedCounterTriggerBinding(
                event=FixedCounterTriggerEvent.SOURCE_ATTACKS,
                variant='source_attacks', body=intervening['body'], attacked_player_life=True,
            )
        return bind_public_intervening_trigger(
            intervening, card_name=card_name, compile_binding=fixed_counter_trigger_binding,
            compile_source_binding=_public_state_source_self_trigger_binding,
        )
    public = _public_trigger_binding(
        material_line,
        card_name=card_name,
    )
    if public is not None:
        return public
    source_event = _source_event_trigger_binding(material_line)
    if source_event is not None:
        return source_event
    scheduled = _SCHEDULED_TRIGGER.fullmatch(material_line)
    if scheduled is not None:
        return FixedCounterTriggerBinding(
            event=FixedCounterTriggerEvent.STEP_BEGIN,
            variant=scheduled.group("schedule").casefold(),
            body=scheduled.group("body"),
        )
    land_entry = _CONTROLLED_LAND_ENTRY_TRIGGER.fullmatch(material_line)
    if land_entry is not None:
        return FixedCounterTriggerBinding(
            event=FixedCounterTriggerEvent.CONTROLLED_LAND_ENTER,
            variant="controlled_land",
            body=land_entry.group("body"),
        )
    spell_cast = fixed_spell_cast_binding_spec(material_line)
    if spell_cast is not None:
        return FixedCounterTriggerBinding(
            event=FixedCounterTriggerEvent.CONTROLLER_SPELL_CAST,
            variant=spell_cast.binding_variant,
            body=spell_cast.body,
            spell_subject=spell_cast.subject,
        )
    source_attack = _SOURCE_ATTACK_TRIGGER.fullmatch(material_line)
    if source_attack is not None:
        return FixedCounterTriggerBinding(
            event=FixedCounterTriggerEvent.SOURCE_ATTACKS,
            variant="source_attacks",
            body=source_attack.group("body"),
        )
    life_gain = _CONTROLLER_LIFE_GAIN_TRIGGER.fullmatch(material_line)
    if life_gain is not None:
        return FixedCounterTriggerBinding(
            event=FixedCounterTriggerEvent.CONTROLLER_LIFE_GAIN,
            variant="controller_life_gain",
            body=life_gain.group("body"),
        )
    card_draw = _CONTROLLER_CARD_DRAW_TRIGGER.fullmatch(material_line)
    if card_draw is not None:
        return FixedCounterTriggerBinding(
            event=FixedCounterTriggerEvent.CONTROLLER_CARD_DRAW,
            variant="controller_card_draw",
            body=card_draw.group("body"),
        )
    second_draw = _CONTROLLER_SECOND_DRAW_TRIGGER.fullmatch(material_line)
    if second_draw is not None:
        return FixedCounterTriggerBinding(
            event=FixedCounterTriggerEvent.CONTROLLER_SECOND_DRAW,
            variant="controller_second_draw",
            body=second_draw.group("body"),
        )
    result = _zone_change_trigger_binding(
        material_line,
        card_name=card_name,
    ) or public_binding_from_spec(aggregated_public_zone_event_binding_spec(material_line, card_name=card_name) or qualified_public_zone_event_binding_spec(material_line, card_name=card_name),
                                 binding_type=FixedCounterTriggerBinding, event_type=FixedCounterTriggerEvent)
    normalized = normalized_source_event_line(material_line, source_name=card_name) if not result and not _source_normalized and card_name else None
    return result or (fixed_counter_trigger_binding(normalized, card_name=card_name, _source_normalized=True) if normalized is not None else None)


def _nested_operations(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        operation = value.get("op")
        if isinstance(operation, str) and operation:
            result.add(operation)
        for child in value.values():
            result.update(_nested_operations(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            result.update(_nested_operations(child))
    return result


def _event_runtime_coverage(binding: FixedCounterTriggerBinding, *, current_ability: bool, effect_mechanics=()) -> tuple[str, ...]:
    from .trigger_runtime_coverage import trigger_runtime_coverage
    return trigger_runtime_coverage(binding, current_ability=current_ability, effect_mechanics=effect_mechanics,
        qualified_zone_variant=QUALIFIED_ZONE_VARIANT,
        aggregated_zone_variant=AGGREGATED_ZONE_VARIANT,
        ability_word_public_event_variants=_ABILITY_WORD_PUBLIC_EVENT_VARIANTS,
        public_event_variants=_ALL_PUBLIC_EVENT_BINDING_CLOSURE_VARIANTS,
        counter_event_variants=COUNTER_EVENT_VARIANTS,
        one_or_more_variants=_ONE_OR_MORE_PUBLIC_EVENT_VARIANTS,
        multi_event_variants=MULTI_EVENT_BINDING_CLOSURE_VARIANTS,
    )


def _binding_effect_template(binding, body, *, card_name, effect_template):
    compiled, current = binding_effect_template(binding, body, card_name=card_name,
        effect_template=effect_template, source_counter_variants=_NONCOUNTER_PUBLIC_EVENT_VARIANTS)
    from .cycle_or_discard_bindings import CYCLE_OR_DISCARD_CAPABILITY
    if CYCLE_OR_DISCARD_CAPABILITY in binding.public_capabilities:
        from .source_self_effect_templates import pin_source_result_incarnation
        compiled = pin_source_result_incarnation(compiled)
    from .room_bindings import EERIE_VARIANT, OWN_DOOR_VARIANT
    return compiled, current or binding.variant in {EERIE_VARIANT, OWN_DOOR_VARIANT}


def _counter_binding_accepts_result(binding, body_mechanics, *, effects, target_schema):
    from .room_bindings import OWN_DOOR_VARIANT
    if binding.variant == OWN_DOOR_VARIANT:
        from ..rules.node_capability_shapes import fixed_counter_placement_target_set_node_capabilities
        if fixed_counter_placement_target_set_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=body_mechanics,
        ):
            return True
    return (bool({'fixed-entry-event-object-counter', 'fixed-public-event-source-counter', 'fixed-public-event-target-counter','fixed-public-event-set-counter'}.intersection(body_mechanics))
        or binding.variant not in _NONCOUNTER_PUBLIC_EVENT_VARIANTS
        or binding.event in {FixedCounterTriggerEvent.PERMANENT_SACRIFICED,FixedCounterTriggerEvent.CARD_DISCARDED}
        and binding.variant in PUBLIC_ACTION_EVENT_BINDING_CLOSURE_VARIANTS)


def fixed_counter_event_trigger_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    card_name: str,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    effect_template: Callable[..., tuple[
        str | None,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]],
    binding: FixedCounterTriggerBinding | None = None,
) -> OracleNode | None:
    """Lower one exact fixed counter effect over a normalized event."""

    binding = binding or fixed_counter_trigger_binding(material_line, card_name=card_name)
    if binding is None:
        return None
    optional_match = re.fullmatch(
        r"you may (?P<body>(?!pay\b).+)",
        binding.body,
        re.IGNORECASE,
    )
    body = (
        optional_match.group("body")
        if optional_match is not None
        else binding.body
    )
    if optional_match is None:
        compiled, requires_current_ability = _binding_effect_template(
            binding,
            body,
            card_name=card_name,
            effect_template=effect_template,
        )
    else:
        compiled, requires_current_ability = _binding_effect_template(binding,body,card_name=card_name,effect_template=effect_template)
    template, effects, target_schema, body_mechanics = compiled
    if optional_match is not None and {'fixed-public-event-source-counter', 'fixed-public-event-target-counter'}.intersection(body_mechanics) and binding.event not in {FixedCounterTriggerEvent.PERMANENT_SACRIFICED, FixedCounterTriggerEvent.CARD_DISCARDED}:
        return None
    nested_counter_operations = _COUNTER_PLACEMENT_OPERATIONS.intersection(
        _nested_operations(effects)
    )
    if template is None or not nested_counter_operations or not _counter_binding_accepts_result(
        binding, body_mechanics, effects=effects, target_schema=target_schema,
    ):
        return None
    if optional_match is not None:
        if (
            len(effects) != 1
            or effects[0].get("op") not in _COUNTER_PLACEMENT_OPERATIONS
        ):
            return None
        effects = (
            {
                "op": OPTIONAL_COUNTER_PLACEMENT_OPERATION,
                "player": "$controller",
                "effect": effects[0],
            },
        )
        trigger_mechanic = OPTIONAL_FIXED_COUNTER_EVENT_TRIGGER_MECHANIC
        template_id = (
            f"{binding.template_id.removesuffix('-v1')}-optional-v1"
        )
    else:
        trigger_mechanic = FIXED_COUNTER_EVENT_TRIGGER_MECHANIC
        template_id = binding.template_id
    mechanics = (
        "cr-603-handling-triggered-abilities",
        trigger_mechanic,
        *binding.event_mechanics,
        *body_mechanics,
    )
    gate = dependency_gate(
        mechanics=mechanics,
        effects=effects,
        target_schema=target_schema,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        explicit_capabilities=(
            *(('counter.producer.fixed_public_source_event',)
              if 'fixed-public-event-source-counter' in body_mechanics else ()),
            *(('counter.producer.fixed_public_target_event',)
              if 'fixed-public-event-target-counter' in body_mechanics else ()),
            *binding.explicit_capabilities,
            *crime_fact_dependencies(binding.public_state_condition.to_dict() if binding.public_state_condition is not None else None),
            *public_trigger_condition_capabilities(binding.public_state_condition),
        ),
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=(
                    "fixed counter event trigger lacks a trusted capability "
                    "closure"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone=binding.active_zone,
        event=binding.event.value,
        event_condition=binding.event_condition,
        lowerable=True,
        exact=not residual_ids,
        template_id=template_id,
        effects=effects,
        target_schema=target_schema,
        runtime_coverage=_event_runtime_coverage(
            binding,
            current_ability=requires_current_ability,
        ),
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            closure.reachable if closure is not None else ()
        ),
        capability_profile=(closure.profile if closure is not None else None),
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


def fixed_typed_event_effect_trigger_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    card_name: str,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    effect_template: Callable[..., tuple[
        str | None,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]],
    binding: FixedCounterTriggerBinding | None = None,
) -> OracleNode | None:
    """Compose one closed event binding with one reviewed typed effect body."""

    counter_node = fixed_counter_event_trigger_node(
        node_id=node_id,
        line=line,
        material_line=material_line,
        span=span,
        card_name=card_name,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
        effect_template=effect_template,
        binding=binding,
    )
    if counter_node is not None:
        return counter_node

    binding = binding or fixed_counter_trigger_binding(material_line, card_name=card_name)
    if binding is None:
        return None
    allocation = fixed_announced_damage_template(
        binding.body,
        card_name=card_name,
        allow_source_pronoun=binding.variant in {"source_attacks", "source_enters_or_attacks"},
    )
    if allocation is not None:
        compiled, requires_current_ability = allocation, False
    else:
        compiled, requires_current_ability = _binding_effect_template(
            binding,
            binding.body,
            card_name=card_name,
            effect_template=effect_template,
        )
    template, effects, target_schema, body_mechanics = compiled
    if (
        template is None
        or (
            not effects
            and FIXED_NONREPEATING_MODAL_MECHANIC not in body_mechanics
        )
        or _COUNTER_PLACEMENT_OPERATIONS.intersection(
            _nested_operations(effects)
        )
    ):
        return None
    mechanics = (
        "cr-603-handling-triggered-abilities",
        FIXED_TYPED_EVENT_EFFECT_TRIGGER_MECHANIC,
        *binding.event_mechanics,
        *body_mechanics,
    )
    gate = dependency_gate(
        mechanics=mechanics,
        effects=effects,
        target_schema=target_schema,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        explicit_capabilities=(
            *binding.explicit_capabilities,
            *crime_fact_dependencies(binding.public_state_condition.to_dict() if binding.public_state_condition is not None else None),
            *public_trigger_condition_capabilities(binding.public_state_condition),
        ),
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=(
                    "fixed typed event-effect trigger lacks a trusted "
                    "capability closure"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone=binding.active_zone,
        event=binding.event.value,
        event_condition=binding.event_condition,
        lowerable=True,
        exact=not residual_ids,
        template_id=binding.typed_effect_template_id,
        effects=effects,
        target_schema=target_schema,
        runtime_coverage=(
            *_event_runtime_coverage(
                binding, effect_mechanics=body_mechanics,
                current_ability=(
                    requires_current_ability
                    and (
                        template in FIXED_SOURCE_COMBAT_GROWTH_TEMPLATE_IDS
                        or binding.variant == "fixed_entry_return_requirement"
                    )
                ),
            ),
            *((SCHEDULED_PLAYER_DEPARTURE_COVERAGE,) if SCHEDULED_PLAYER_MECHANIC in body_mechanics else ()),
        ),
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            closure.reachable if closure is not None else ()
        ),
        capability_profile=(closure.profile if closure is not None else None),
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


__all__ = [
    "FIXED_COUNTER_EVENT_TRIGGER_MECHANIC",
    "FIXED_COUNTER_EVENT_TRIGGER_TEMPLATE_IDS",
    "FIXED_TYPED_EVENT_EFFECT_TRIGGER_MECHANIC",
    "FIXED_TYPED_EVENT_EFFECT_TRIGGER_TEMPLATE_IDS",
    "FIXED_SPELL_CAST_CHARACTERISTIC_MECHANIC",
    "FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY",
    "OPTIONAL_COUNTER_PLACEMENT_OPERATION",
    "OPTIONAL_FIXED_COUNTER_EVENT_TRIGGER_MECHANIC",
    "MULTI_EVENT_BINDING_CLOSURE_VARIANTS",
    "PUBLIC_ACTION_EVENT_BINDING_CLOSURE_VARIANTS",
    "FixedCounterTriggerBinding",
    "FixedCounterTriggerEvent",
    "FixedCounterZoneController",
    "FixedCounterZoneSubject",
    "FixedSpellCastController",
    "FixedSpellCastCharacteristicKind",
    "FixedSpellCastCharacteristicQuery",
    "FixedSpellCastCharacteristicTerm",
    "FixedSpellCastQuality",
    "FixedSpellCastOrigin",
    "FixedSpellCastSubject",
    "FixedSpellCastTurnRelation",
    "fixed_counter_event_trigger_node",
    "fixed_counter_trigger_binding",
    "fixed_typed_event_effect_trigger_node",
]
