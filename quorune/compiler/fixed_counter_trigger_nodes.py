from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Callable, Mapping, Sequence

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import dependency_gate
from .modal_templates import FIXED_NONREPEATING_MODAL_MECHANIC
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual
from .fixed_public_event_trigger_bindings import fixed_public_event_binding_spec
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


FIXED_COUNTER_EVENT_TRIGGER_MECHANIC = "fixed-counter-event-trigger"
FIXED_TYPED_EVENT_EFFECT_TRIGGER_MECHANIC = (
    "fixed-typed-event-effect-trigger"
)
FIXED_SPELL_CAST_CHARACTERISTIC_MECHANIC = (
    "trigger-event-spell-cast-fixed-characteristics"
)
FIXED_COUNTER_EVENT_TRIGGER_TEMPLATE_IDS = frozenset(
    {
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
        "fixed-counter-public-zone-trigger-v1",
        "fixed-counter-public-damage-trigger-v1",
        "fixed-counter-public-attack-trigger-v1",
        "fixed-counter-public-block-trigger-v1",
        "fixed-counter-public-cycle-trigger-v1",
        "fixed-counter-public-face-up-trigger-v1",
        "fixed-counter-opponent-card-draw-trigger-v1",
        "fixed-counter-entry-return-public-zone-trigger-v1",
        "fixed-counter-heroic-spell-cast-trigger-v1",
        "fixed-counter-magecraft-spell-action-trigger-v1",
        "fixed-counter-constellation-entry-trigger-v1",
        "fixed-counter-battalion-attack-trigger-v1",
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
        "battalion_source_and_two_others_attack",
    }
)
_SCHEDULED_TRIGGER = re.compile(
    r"^At the beginning of "
    r"(?P<schedule>your upkeep|each upkeep|your end step|each end step|"
    r"combat on your turn), (?P<body>.+)$",
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
_SOURCE_VEHICLE_ZONE_TRIGGER = re.compile(
    r"^When this Vehicle (?P<transition>enters|dies), (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_GRAVEYARD_TRIGGER = re.compile(
    r"^When this (?P<kind>artifact|Aura|enchantment|land) is put into a "
    r"graveyard from the battlefield, (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_DAMAGE_TRIGGER = re.compile(
    r"^Whenever this creature (?P<event>deals combat damage to a player|"
    r"deals combat damage to an opponent|deals damage to an opponent|"
    r"is dealt damage), (?P<body>.+)$",
    re.IGNORECASE,
)
_ZONE_SUBJECT_TYPES = frozenset(
    {"artifact", "creature", "enchantment", "land", "permanent"}
)
class FixedCounterTriggerEvent(str, Enum):
    """Closed normalized event families accepted by this compiler slice."""

    STEP_BEGIN = "step.begin"
    CONTROLLED_LAND_ENTER = "land.enter"
    CONTROLLER_SPELL_CAST = "spell.cast"
    SOURCE_ATTACKS = "creature.attacks"
    CONTROLLER_LIFE_GAIN = "life.gained"
    CONTROLLER_CARD_DRAW = "card.drawn"
    CONTROLLER_SECOND_DRAW = "card.second_draw"
    PERMANENT_ENTER = "permanent.enter"
    ARTIFACT_ENTER = "artifact.enter"
    CREATURE_ENTER = "creature.enter"
    ENCHANTMENT_ENTER = "enchantment.enter"
    CREATURE_DIES = "creature.dies"
    PERMANENT_GRAVEYARD = "permanent.graveyard"
    SOURCE_VEHICLE_ENTER = "permanent.enter.self"
    SOURCE_VEHICLE_DIES = "creature.dies.self"
    SOURCE_GRAVEYARD = "permanent.graveyard.self"
    SOURCE_DEALS_DAMAGE = "damage.dealt.self"
    SOURCE_DEALT_DAMAGE = "damage.dealt"
    ARTIFACT_GRAVEYARD = "artifact.graveyard"
    PUBLIC_DEALS_DAMAGE = "damage.dealt"
    PUBLIC_ATTACKS = "creature.attacks"
    CREATURE_BLOCKS = "creature.blocks"
    CREATURE_BECOMES_BLOCKED = "creature.becomes_blocked"
    CARD_CYCLED = "card.cycled"
    SOURCE_CYCLED = "card.cycled.self"
    PERMANENT_TURNED_FACE_UP = "permanent.turned_face_up"
    OPPONENT_CARD_DRAW = "card.drawn"
    SPELL_CAST_OR_COPY = "spell.cast_or_copy"


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

    def __post_init__(self) -> None:
        if not isinstance(self.event, FixedCounterTriggerEvent):
            raise ValueError("Fixed counter triggers require a closed event")
        if type(self.variant) is not str or not self.variant:
            raise ValueError("Fixed counter trigger variants must be nonempty")
        if type(self.body) is not str or not self.body:
            raise ValueError("Fixed counter trigger bodies must be nonempty")
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
        if self.public_mechanic is not None and self.spell_subject is not None:
            raise ValueError(
                "Public event triggers cannot carry a parallel spell subject"
            )
        if self.public_mechanic is not None:
            if not self.public_mechanic or not self.public_template_id:
                raise ValueError(
                    "Public event triggers require typed owner identities"
                )
            if self.public_active_zone not in {None, "battlefield", "hand"}:
                raise ValueError(
                    "Public event triggers require a closed active zone"
                )
        elif any(
            value is not None
            for value in (
                self.public_condition,
                self.public_active_zone,
                self.public_mechanic,
                self.public_template_id,
            )
        ):
            raise ValueError(
                "Only public event triggers accept public predicate metadata"
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
                "fixed-counter-source-dealt-damage-trigger-v1",
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
            ),
        }[self.event]

    @property
    def event_condition(self) -> Mapping[str, Any] | None:
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
            return {"all": conditions}
        if self.event is FixedCounterTriggerEvent.SOURCE_DEALT_DAMAGE:
            return {
                "field": "target",
                "op": "eq",
                "value": "$source.ref",
            }
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
            "your end step": ("end_step", True),
            "each end step": ("end_step", False),
            "combat on your turn": ("beginning_combat", True),
        }[self.variant]
        conditions: list[Mapping[str, Any]] = [
            {"field": "step", "op": "eq", "value": step}
        ]
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


def _zone_change_trigger_binding(
    material_line: str,
    *,
    card_name: str | None = None,
) -> FixedCounterTriggerBinding | None:
    normalized_line = material_line
    if card_name:
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
    vehicle = _SOURCE_VEHICLE_ZONE_TRIGGER.fullmatch(material_line)
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
                "is dealt damage": "source_dealt_damage",
            }[event],
            body=damage.group("body"),
        )
    return None


def _public_trigger_binding(
    material_line: str,
    *,
    card_name: str | None,
) -> FixedCounterTriggerBinding | None:
    spec = fixed_public_event_binding_spec(material_line, card_name=card_name)
    if spec is None:
        return None
    return FixedCounterTriggerBinding(
        event=FixedCounterTriggerEvent(spec.event),
        variant=spec.variant,
        body=spec.body,
        public_condition=spec.condition,
        public_active_zone=spec.active_zone,
        public_mechanic=spec.mechanic,
        public_template_id=spec.template_id,
    )


def fixed_counter_trigger_binding(
    material_line: str,
    *,
    card_name: str | None = None,
) -> FixedCounterTriggerBinding | None:
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
    return _zone_change_trigger_binding(
        material_line,
        card_name=card_name,
    )


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


def _binding_effect_template(
    binding: FixedCounterTriggerBinding,
    body: str,
    *,
    card_name: str,
    effect_template: Callable[..., tuple[
        str | None,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]],
) -> tuple[
    tuple[
        str | None,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ],
    bool,
]:
    specialized = fixed_source_combat_growth_effect_template(
        body,
        event=binding.event.value,
        variant=binding.variant,
    )
    if specialized[0] is not None:
        return specialized, True
    if binding.variant == "fixed_entry_return_requirement":
        entry_return = fixed_entry_return_effect_template(body)
        if entry_return[0] is not None:
            return entry_return, True
    return effect_template(body, card_name=card_name), False


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
) -> OracleNode | None:
    """Lower one exact fixed counter effect over a normalized event."""

    binding = fixed_counter_trigger_binding(
        material_line,
        card_name=card_name,
    )
    if binding is None:
        return None
    optional_match = re.fullmatch(
        r"you may (?P<body>.+)",
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
        compiled = effect_template(body, card_name=card_name)
        requires_current_ability = False
    template, effects, target_schema, body_mechanics = compiled
    nested_counter_operations = _COUNTER_PLACEMENT_OPERATIONS.intersection(
        _nested_operations(effects)
    )
    if template is None or not nested_counter_operations:
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
        runtime_coverage=(
            (CURRENT_ABILITY_FRAGMENT_COVERAGE,)
            if requires_current_ability
            or binding.variant in _ABILITY_WORD_PUBLIC_EVENT_VARIANTS
            else ()
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
    )
    if counter_node is not None:
        return counter_node

    binding = fixed_counter_trigger_binding(
        material_line,
        card_name=card_name,
    )
    if binding is None:
        return None
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
            (CURRENT_ABILITY_FRAGMENT_COVERAGE,)
            if (
                requires_current_ability
                and (
                    template in FIXED_SOURCE_COMBAT_GROWTH_TEMPLATE_IDS
                    or binding.variant == "fixed_entry_return_requirement"
                )
            )
            or binding.variant in _ABILITY_WORD_PUBLIC_EVENT_VARIANTS
            else ()
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
    "OPTIONAL_COUNTER_PLACEMENT_OPERATION",
    "OPTIONAL_FIXED_COUNTER_EVENT_TRIGGER_MECHANIC",
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
