from __future__ import annotations

"""Canonical current-turn fact DTOs; model.py preserves the historical import API."""

from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from .creature_subtypes import canonical_creature_subtype
from .death_history_name_model import (validate_event_rules_names, decode_event_rules_names,
    validate_death_name_history, death_name_history_fields, decode_death_name_history_version)

TurnHistoryEventKind = Literal[
    "first_venture",
    "card_drawn",
    "trigger_optional_result_used",
    "trigger_mode_chosen",
    "ability_resolved",
    "crime_committed",
    "spell_mana_spent",
    "spell_cast",
    "cast_creature_spell_countered",
    "spell_warped",
    "card_left_graveyard",
    "card_discarded",
    "creature_attacked",
    "creature_died",
    "permanent_died",
    "permanent_sacrificed",
    "player_damaged",
    "player_gained_life",
    "player_lost_life",
    "first_life_gain",
    "permanent_damaged",
    "permanent_entered",
    "permanent_left",
    "permanent_arrived_hand",
    "permanent_arrived_graveyard",
    "card_arrived_exile",
    "artifact_subtype_sacrificed",
    "card_arrived_graveyard",
    "card_milled_graveyard",
    "permanent_plus_one_counters_placed",
    "land_played",
    "permanent_counter_received",
]


LAND_PLAY_HISTORY_EVENT = "land_played"
DRAWN_HAND_CARD_EVENT = "card_drawn"
CAST_CREATURE_COUNTERED_EVENT = "cast_creature_spell_countered"
PLUS_ONE_COUNTERS_PLACED_EVENT = "permanent_plus_one_counters_placed"
TRIGGER_MODE_CHOSEN_EVENT = "trigger_mode_chosen"
PERMANENT_COUNTER_RECEIVED_EVENT = "permanent_counter_received"


def _validate_plus_one_placement(event):
    if event.kind != PLUS_ONE_COUNTERS_PLACED_EVENT:
        return
    if (any(type(value) is not str or not value for value in (event.actor, event.object_incarnation))
            or type(event.amount) is not int or event.amount <= 0
            or not isinstance(event.types, tuple)
            or any(type(value) is not str or not value or value != value.casefold() for value in event.types)
            or event.types != tuple(sorted(set(event.types)))
            or any(value is not None for value in (
                event.target, event.target_kind, event.target_object_incarnation, event.origin,
            ))):
        raise ValueError("Counter placement history requires its actor, current permanent and committed amount")


def _validate_plus_one_placement_history(version, events):
    if version is not None and (type(version) is not int or version != 1):
        raise ValueError("Unsupported +1/+1 counter placement history version")
    if version is None and any(event.kind == PLUS_ONE_COUNTERS_PLACED_EVENT for event in events):
        raise ValueError("Counter placement occurrences require explicit complete placement history")


COMMITTED_OCCURRENCE_HISTORY_KINDS = frozenset({"spell_warped", "card_left_graveyard"})
PUBLIC_MOVEMENT_HISTORY_KINDS = frozenset({
    "permanent_arrived_hand", "permanent_arrived_graveyard", "card_arrived_exile", "artifact_subtype_sacrificed",
})


@dataclass(frozen=True, slots=True)
class TurnHistoryEvent:
    """One rules-relevant fact recorded during the current turn.

    This compact journal is authoritative game state, unlike the presentation
    event log.  It intentionally stores the characteristics and logical-object
    identity that existed when the event happened so CR 608.2i look-back
    queries never substitute the object's current characteristics or zone.
    """

    kind: TurnHistoryEventKind
    actor: str | None = None
    object_incarnation: str | None = None
    target: str | None = None
    target_kind: str | None = None
    target_object_incarnation: str | None = None
    types: tuple[str, ...] = ()
    amount: int = 0
    origin: str | None = None
    creature_subtypes: tuple[str, ...] | None = None
    token: bool | None = None
    rules_names: tuple[str, ...] | None = None
    spell_colors: tuple[str, ...] | None = None

    def __post_init__(self):
        validate_event_rules_names(self.kind, self.rules_names)
        _validate_plus_one_placement(self)
        if self.kind == PERMANENT_COUNTER_RECEIVED_EVENT and (
            type(self.object_incarnation) is not str or not self.object_incarnation
            or any(value is not None for value in (self.actor, self.target, self.target_kind,
                                                   self.target_object_incarnation, self.origin))
            or self.types or type(self.amount) is not int or self.amount != 0
        ):
            raise ValueError("A permanent counter receipt requires only the receiving incarnation")
        if self.spell_colors is not None and (
            self.kind != "spell_cast" or type(self.spell_colors) is not tuple
            or self.spell_colors != tuple(color for color in "WUBRG" if color in self.spell_colors)
            or type(self.actor) is not str or not self.actor
            or type(self.object_incarnation) is not str or not self.object_incarnation
        ):
            raise ValueError("As-cast spell colors require their canonical cast identity")
        if self.kind == "permanent_died":
            if (type(self.actor) is not str or not self.actor
                or type(self.object_incarnation) is not str or not self.object_incarnation
                or type(self.token) is not bool
                or not isinstance(self.types, tuple)
                or any(type(value) is not str or not value or value != value.casefold() for value in self.types)
                or self.types != tuple(sorted(set(self.types)))
                or not isinstance(self.creature_subtypes, tuple)
                or any(type(value) is not str or canonical_creature_subtype(value) != value
                       for value in self.creature_subtypes)
                or self.creature_subtypes != tuple(sorted(set(self.creature_subtypes)))
                or any(value is not None for value in (
                    self.target, self.target_kind, self.target_object_incarnation, self.origin,
                ))
                or type(self.amount) is not int or self.amount != 0):
                raise ValueError("Permanent death history requires its sealed departure facts")
        elif self.creature_subtypes is not None or self.token is not None:
            raise ValueError("Only permanent deaths carry subtype and token history")
        if self.kind in (COMMITTED_OCCURRENCE_HISTORY_KINDS | {LAND_PLAY_HISTORY_EVENT, DRAWN_HAND_CARD_EVENT}) and (
            type(self.actor) is not str or not self.actor
            or type(self.object_incarnation) is not str or not self.object_incarnation
            or any(value is not None for value in (
                self.target, self.target_kind, self.target_object_incarnation, self.origin,
            ))
            or self.types or type(self.amount) is not int or self.amount != 0
        ):
            raise ValueError("Committed occurrence history requires an actor and its exact object incarnation")
        if self.kind == CAST_CREATURE_COUNTERED_EVENT and (
            any(type(value) is not str or not value for value in (
                self.actor, self.object_incarnation, self.target,
            ))
            or self.target_kind != "player" or self.target_object_incarnation is not None
            or self.origin is not None or self.types != ()
            or type(self.amount) is not int or self.amount != 0
        ):
            raise ValueError("Creature spell counter history requires the cast and both player identities")
        if self.kind in PUBLIC_MOVEMENT_HISTORY_KINDS:
            self._validate_public_movement()
        if self.kind == 'first_life_gain' and (
            type(self.target) is not str or not self.target or self.target_kind != 'player'
            or any(value is not None for value in (self.actor, self.object_incarnation, self.target_object_incarnation, self.origin))
            or self.types or type(self.amount) is not int or self.amount != 0
        ):
            raise ValueError('First life gain history requires only its gaining seat')
        if self.kind == "first_venture" and (
            type(self.actor) is not str or not self.actor
            or any(value is not None for value in (self.object_incarnation, self.target, self.target_kind,
                                                  self.target_object_incarnation, self.origin))
            or self.types or type(self.amount) is not int or self.amount != 0
        ):
            raise ValueError("First Venture history requires only the venturing seat")
        if self.kind=='crime_committed' and (type(self.actor) is not str or not self.actor
                or type(self.target) is not str or not self.target or self.target_kind!='stack_object'
                or type(self.amount) is not int or self.amount!=1 or self.object_incarnation is not None
                or self.target_object_incarnation is not None or self.types or self.origin is not None):
            raise ValueError('Crime history requires one completed player action and its exact stack identity')
        if self.kind == TRIGGER_MODE_CHOSEN_EVENT and (
            any(type(value) is not str or not value for value in (self.actor, self.object_incarnation, self.target))
            or self.target_kind != "ability_program" or self.target_object_incarnation is not None
            or self.types or self.origin is not None or type(self.amount) is not int or self.amount not in (1, 2, 3)
        ):
            raise ValueError("Trigger mode usage requires its source incarnation, ability program and mode")
        if self.kind in {"trigger_optional_result_used", "ability_resolved"} and (
            any(type(value) is not str or not value for value in (self.actor, self.object_incarnation, self.target))
            or self.target_kind != 'ability_program' or self.target_object_incarnation is not None
            or self.types or self.origin is not None or type(self.amount) is not int or self.amount != 0
        ):
            label = "Optional result usage" if self.kind == "trigger_optional_result_used" else "Ability resolution"
            raise ValueError(f"{label} requires its controller, source incarnation and ability program")
        if self.kind=='spell_mana_spent' and (
                type(self.actor) is not str or not self.actor or type(self.amount) is not int or self.amount<=0
                or any(value is not None for value in (self.object_incarnation,self.target,self.target_kind,
                    self.target_object_incarnation,self.origin)) or self.types):
            raise ValueError('Spell-mana history requires one positive player payment')
        if self.origin is not None and (type(self.origin) is not str or self.origin not in {'battlefield','hand','library','graveyard','stack','exile','command','outside'}):
            raise ValueError('Turn-history arrival origin is closed')
        if self.kind=='card_arrived_graveyard' and (self.origin is None or self.origin=='graveyard' or type(self.object_incarnation) is not str or not self.object_incarnation):
            raise ValueError('Graveyard arrival needs its exact arrived incarnation and origin')
        if self.kind not in {'card_arrived_graveyard','spell_cast'} and self.origin is not None:raise ValueError('Only cast and graveyard arrival history has an origin')
        if self.kind=='spell_cast' and self.origin in {'battlefield','stack'}:raise ValueError('A cast needs a supported pre-stack origin')
        if self.kind=='card_milled_graveyard' and (type(self.object_incarnation) is not str or not self.object_incarnation):
            raise ValueError('Mill arrival history needs its exact graveyard incarnation')

    def _validate_public_movement(self) -> None:
        if (type(self.actor) is not str or not self.actor
                or type(self.object_incarnation) is not str or not self.object_incarnation
                or self.target_object_incarnation is not None or self.origin is not None
                or type(self.amount) is not int or self.amount != 0):
            raise ValueError("Public movement history requires a seat and a committed incarnation")
        if self.kind == "artifact_subtype_sacrificed":
            from .type_subtype_vocabulary import ARTIFACT_SUBTYPES

            if (self.target_kind != "artifact_subtype" or type(self.target) is not str
                    or self.target not in ARTIFACT_SUBTYPES or self.types):
                raise ValueError("Sacrifice history requires one canonical artifact subtype")
        elif self.target is not None or self.target_kind is not None:
            raise ValueError("Movement history cannot carry a target")
        elif self.kind == "card_arrived_exile":
            if self.types:
                raise ValueError("Card exile history carries no characteristics")
        elif (not self.types or not isinstance(self.types, tuple)
                or any(type(value) is not str or not value or value != value.casefold() for value in self.types)
                or self.types != tuple(sorted(set(self.types)))):
            raise ValueError("Permanent arrival history requires its prior types")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.rules_names is None:payload.pop("rules_names")
        else:payload["rules_names"] = list(self.rules_names)
        payload["types"] = list(self.types)
        if self.kind == "permanent_died":
            payload["creature_subtypes"] = list(self.creature_subtypes)
        else:
            payload.pop("creature_subtypes")
            payload.pop("token")
        if self.origin is None:payload.pop("origin")
        if self.spell_colors is None:
            payload.pop("spell_colors")
        else:
            payload["spell_colors"] = list(self.spell_colors)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurnHistoryEvent":
        payload = dict(data)
        payload["rules_names"] = decode_event_rules_names(payload)
        if "spell_colors" in payload:
            if not isinstance(payload["spell_colors"], list):
                raise ValueError("As-cast spell colors must be a recorded array")
            payload["spell_colors"] = tuple(payload["spell_colors"])
        if payload.get("kind") == PLUS_ONE_COUNTERS_PLACED_EVENT:
            if not isinstance(payload.get("types"), (list, tuple)):
                raise ValueError("Counter placement history requires explicit recipient types")
            payload["types"] = tuple(payload["types"])
        elif payload.get("kind") in {"permanent_died", *PUBLIC_MOVEMENT_HISTORY_KINDS}:
            if not isinstance(payload.get("types"), (list, tuple)):
                family = "Permanent death" if payload.get("kind") == "permanent_died" else "Public movement"
                raise ValueError(f"{family} history requires explicit card types")
            payload["types"] = tuple(payload["types"])
        else:
            payload["types"] = tuple(str(value) for value in payload.get("types", ()))
        if isinstance(payload.get("creature_subtypes"), list):
            payload["creature_subtypes"] = tuple(payload["creature_subtypes"])
        return cls(**payload)


@dataclass(slots=True)
class TurnHistory:
    """Versioned current-turn facts used by deterministic rules queries."""

    schema_version: int = 1
    turn_sequence: int = 0
    events: list[TurnHistoryEvent] = field(default_factory=list)
    # Additive summary retained across exactly one turn boundary. The live
    # event list continues to describe only ``turn_sequence``; these fields
    # preserve previous-turn spell counts for CR 502.2/731 and printed
    # intervening-if triggers without retaining an unbounded event journal.
    previous_turn_sequence: int | None = None
    previous_active_player: str | None = None
    previous_spell_cast_counts: dict[str, int] = field(default_factory=dict)
    # Absent preserves pre-extension event hashes; new games enable complete
    # committed battlefield-departure recording explicitly.
    departure_history_version: int | None = None
    arrival_history_version: int | None = None
    mill_history_version: int | None = None
    spell_origin_history_version: int | None = None
    spell_color_history_version: int | None = None
    creature_spell_counter_history_version: int | None = None
    graveyard_type_history_version: int | None = None
    spell_mana_history_version: int | None = None
    crime_history_version: int | None = None
    committed_occurrence_history_version: int | None = None
    damage_life_history_version: int | None = None
    permanent_death_history_version: int | None = None
    death_name_history_version: int | None = None
    public_movement_history_version: int | None = None
    first_life_gain_history_version: int | None = None
    venture_history_version: int | None = None
    draw_identity_history_version: int | None = None
    plus_one_counter_placement_history_version: int | None = None
    land_play_history_version: int | None = None
    permanent_counter_receipt_history_version: int | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise ValueError("Unsupported turn-history schema version")
        if type(self.turn_sequence) is not int or self.turn_sequence < 0:
            raise ValueError("Turn-history sequence must be nonnegative")
        if self.departure_history_version is not None and (
            type(self.departure_history_version) is not int
            or self.departure_history_version != 1
        ):
            raise ValueError("Unsupported departure-history version")
        if self.arrival_history_version is not None and (type(self.arrival_history_version) is not int or self.arrival_history_version!=1):raise ValueError('Unsupported arrival-history version')
        if self.mill_history_version is not None and (type(self.mill_history_version) is not int or self.mill_history_version!=1):raise ValueError('Unsupported mill-history version')
        for mode in (self.spell_origin_history_version,self.graveyard_type_history_version,self.spell_mana_history_version,self.crime_history_version):
            if mode is not None and (type(mode) is not int or mode!=1):raise ValueError('Unsupported qualified history version')
        land_play_version = self.land_play_history_version
        if land_play_version is not None and (type(land_play_version) is not int or land_play_version != 1):
            raise ValueError("Unsupported land-play history version")
        occurrence_version = self.committed_occurrence_history_version
        if occurrence_version is not None and (type(occurrence_version) is not int or occurrence_version != 1):
            raise ValueError("Unsupported committed occurrence history version")
        damage_life_version = self.damage_life_history_version
        if damage_life_version is not None and (type(damage_life_version) is not int or damage_life_version != 1):
            raise ValueError("Unsupported damage life history version")
        death_version = self.permanent_death_history_version
        if death_version is not None and (type(death_version) is not int or death_version != 1):
            raise ValueError("Unsupported permanent death history version")
        movement_version = self.public_movement_history_version
        if movement_version is not None and (type(movement_version) is not int or movement_version != 1):
            raise ValueError("Unsupported public movement history version")
        life_gain_version = self.first_life_gain_history_version
        if life_gain_version is not None and (type(life_gain_version) is not int or life_gain_version != 1):
            raise ValueError('Unsupported first life gain history version')
        draw_version = self.draw_identity_history_version
        if draw_version is not None and (type(draw_version) is not int or draw_version != 1):
            raise ValueError("Unsupported draw identity history version")
        venture_version = self.venture_history_version
        if venture_version is not None and (type(venture_version) is not int or venture_version != 1):
            raise ValueError("Unsupported Venture history version")
        if self.graveyard_type_history_version is not None and self.arrival_history_version!=1:raise ValueError('Graveyard type history requires complete arrival history')
        if not isinstance(self.events, list) or any(
            not isinstance(event, TurnHistoryEvent) for event in self.events
        ):
            raise ValueError("Turn-history events must be typed")
        if self.spell_color_history_version is not None and (
            type(self.spell_color_history_version) is not int or self.spell_color_history_version != 1
        ):
            raise ValueError("Unsupported as-cast spell-color history version")
        _validate_plus_one_placement_history(self.plus_one_counter_placement_history_version, self.events)
        receipt_version = self.permanent_counter_receipt_history_version
        if receipt_version is not None and (type(receipt_version) is not int or receipt_version != 1):
            raise ValueError("Unsupported permanent counter receipt history version")
        counter_recipients = [event.object_incarnation for event in self.events
                              if event.kind == PERMANENT_COUNTER_RECEIVED_EVENT]
        if counter_recipients and (receipt_version is None or len(counter_recipients) != len(set(counter_recipients))):
            raise ValueError("Counter receipts require complete history and distinct incarnations")
        if self.spell_color_history_version is None and any(event.spell_colors is not None for event in self.events):
            raise ValueError("As-cast color facts require their explicit history mode")
        if self.spell_mana_history_version is None and any(event.kind=='spell_mana_spent' for event in self.events):
            raise ValueError('Spell-mana facts require complete payment history')
        if self.crime_history_version is None and any(event.kind=='crime_committed' for event in self.events):
            raise ValueError('Crime facts require complete initial-action history')
        if land_play_version is None and any(event.kind == LAND_PLAY_HISTORY_EVENT for event in self.events):
            raise ValueError("Committed land plays require complete land-play history")
        if occurrence_version is None and any(event.kind in COMMITTED_OCCURRENCE_HISTORY_KINDS for event in self.events):
            raise ValueError("Committed occurrence facts require complete history")
        if death_version is None and any(event.kind == "permanent_died" for event in self.events):
            raise ValueError("Permanent death facts require complete history")
        if movement_version is None and any(event.kind in PUBLIC_MOVEMENT_HISTORY_KINDS for event in self.events):
            raise ValueError("Public movement facts require complete history")
        if draw_version is None and any(event.kind == DRAWN_HAND_CARD_EVENT for event in self.events):
            raise ValueError("Drawn card facts require complete draw identity history")
        validate_death_name_history(self.death_name_history_version, death_version, self.events)
        first_gainers = [event.target for event in self.events if event.kind == 'first_life_gain']
        if first_gainers and (life_gain_version is None or len(first_gainers) != len(set(first_gainers))):
            raise ValueError('First life gain facts require versioned history and unique gaining seats')
        first_ventures = [event.actor for event in self.events if event.kind == "first_venture"]
        if first_ventures and (venture_version is None or len(first_ventures) != len(set(first_ventures))):
            raise ValueError("First Venture facts require versioned history and unique venturing seats")
        counter_version = self.creature_spell_counter_history_version
        if counter_version is not None and (type(counter_version) is not int or counter_version != 1):
            raise ValueError("Unsupported creature-spell counter history version")
        countered_casts = [
            event.object_incarnation for event in self.events if event.kind == CAST_CREATURE_COUNTERED_EVENT
        ]
        if countered_casts and (counter_version is None or len(countered_casts) != len(set(countered_casts))):
            raise ValueError("Creature spell counter facts require versioned history and distinct casts")
        previous_absent = self.previous_turn_sequence is None
        if previous_absent != (self.previous_active_player is None):
            raise ValueError(
                "Previous-turn sequence and active player must be paired"
            )
        if previous_absent:
            if self.previous_spell_cast_counts:
                raise ValueError(
                    "Previous spell counts require a previous turn"
                )
            return
        if (
            type(self.previous_turn_sequence) is not int
            or self.previous_turn_sequence < 0
            or self.previous_turn_sequence != self.turn_sequence - 1
            or type(self.previous_active_player) is not str
            or not self.previous_active_player
            or not isinstance(self.previous_spell_cast_counts, dict)
            or any(
                type(player) is not str
                or not player
                or type(count) is not int
                or count < 0
                for player, count in self.previous_spell_cast_counts.items()
            )
        ):
            raise ValueError("Previous-turn spell history is malformed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "turn_sequence": self.turn_sequence,
            "events": [event.to_dict() for event in self.events],
            **({"arrival_history_version":1} if self.arrival_history_version is not None else {}),
            **({"mill_history_version":1} if self.mill_history_version is not None else {}),
            **({"spell_origin_history_version":1} if self.spell_origin_history_version is not None else {}),
            **({"spell_color_history_version": 1} if self.spell_color_history_version is not None else {}),
            **({"plus_one_counter_placement_history_version": 1}
               if self.plus_one_counter_placement_history_version is not None else {}),
            **({"permanent_counter_receipt_history_version": 1}
               if self.permanent_counter_receipt_history_version is not None else {}),
            **({"creature_spell_counter_history_version": 1}
               if self.creature_spell_counter_history_version is not None else {}),
            **({"graveyard_type_history_version":1} if self.graveyard_type_history_version is not None else {}),
            **({"spell_mana_history_version":1} if self.spell_mana_history_version is not None else {}),
            **({"crime_history_version":1} if self.crime_history_version is not None else {}),
            **({"committed_occurrence_history_version": 1}
               if self.committed_occurrence_history_version is not None else {}),
            **({"damage_life_history_version": 1}
               if self.damage_life_history_version is not None else {}),
            **death_name_history_fields(self.death_name_history_version),
            **({"permanent_death_history_version": 1}
               if self.permanent_death_history_version is not None else {}),
            **({"public_movement_history_version": 1}
               if self.public_movement_history_version is not None else {}),
            **({'first_life_gain_history_version': 1} if self.first_life_gain_history_version is not None else {}),
            **({"venture_history_version": 1} if self.venture_history_version is not None else {}),
            **({"land_play_history_version": 1} if self.land_play_history_version is not None else {}),
            **({"draw_identity_history_version": 1} if self.draw_identity_history_version is not None else {}),
            **({"departure_history_version": self.departure_history_version}
               if self.departure_history_version is not None else {}),
            **(
                {
                    "previous_turn": {
                        "turn_sequence": self.previous_turn_sequence,
                        "active_player": self.previous_active_player,
                        "spell_cast_counts": dict(
                            sorted(self.previous_spell_cast_counts.items())
                        ),
                    }
                }
                if self.previous_turn_sequence is not None
                else {}
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurnHistory":
        previous = data.get("previous_turn")
        if "land_play_history_version" in data and data["land_play_history_version"] is None:
            raise ValueError("Unsupported land-play history version")
        if "draw_identity_history_version" in data and data["draw_identity_history_version"] is None:
            raise ValueError("Unsupported draw identity history version")
        if "plus_one_counter_placement_history_version" in data and data["plus_one_counter_placement_history_version"] is None:
            raise ValueError("Unsupported +1/+1 counter placement history version")
        if "permanent_counter_receipt_history_version" in data and data["permanent_counter_receipt_history_version"] is None:
            raise ValueError("Unsupported permanent counter receipt history version")
        if "spell_color_history_version" in data and data["spell_color_history_version"] is None:
            raise ValueError("Unsupported as-cast spell-color history version")
        if "creature_spell_counter_history_version" in data and data["creature_spell_counter_history_version"] is None:
            raise ValueError("Unsupported creature-spell counter history version")
        if "public_movement_history_version" in data and data["public_movement_history_version"] is None:
            raise ValueError("Unsupported public movement history version")
        if previous is not None and not isinstance(previous, dict):
            raise ValueError("Previous-turn spell history must be an object")
        if isinstance(previous, dict) and set(previous) != {
            "turn_sequence",
            "active_player",
            "spell_cast_counts",
        }:
            raise ValueError("Previous-turn spell history has unknown fields")
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            departure_history_version=data.get("departure_history_version"),
            arrival_history_version=data.get("arrival_history_version"),
            mill_history_version=data.get("mill_history_version"),
            spell_origin_history_version=data.get("spell_origin_history_version"),
            spell_color_history_version=data.get("spell_color_history_version"),
            plus_one_counter_placement_history_version=data.get("plus_one_counter_placement_history_version"),
            permanent_counter_receipt_history_version=data.get("permanent_counter_receipt_history_version"),
            creature_spell_counter_history_version=data.get("creature_spell_counter_history_version"),
            graveyard_type_history_version=data.get("graveyard_type_history_version"),
            spell_mana_history_version=data.get("spell_mana_history_version"),
            crime_history_version=data.get("crime_history_version"),
            committed_occurrence_history_version=data.get("committed_occurrence_history_version"),
            damage_life_history_version=data.get("damage_life_history_version"),
            permanent_death_history_version=data.get("permanent_death_history_version"),
            death_name_history_version=decode_death_name_history_version(data),
            public_movement_history_version=data.get("public_movement_history_version"),
            first_life_gain_history_version=data.get('first_life_gain_history_version'),
            venture_history_version=data.get("venture_history_version"),
            land_play_history_version=data.get("land_play_history_version"),
            draw_identity_history_version=data.get("draw_identity_history_version"),
            turn_sequence=int(data.get("turn_sequence", 0)),
            events=[
                TurnHistoryEvent.from_dict(event)
                for event in data.get("events", [])
            ],
            previous_turn_sequence=(
                previous["turn_sequence"]
                if isinstance(previous, dict)
                and previous.get("turn_sequence") is not None
                else None
            ),
            previous_active_player=(
                previous["active_player"]
                if isinstance(previous, dict)
                and previous.get("active_player") is not None
                else None
            ),
            previous_spell_cast_counts=(
                previous.get("spell_cast_counts", {})
                if isinstance(previous, dict)
                else {}
            ),
        )
