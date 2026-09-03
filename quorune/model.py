from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .damage_modifier_state import (
    DamagePreventionShield,
    DamageRedirectionEffect,
)
from .declaration_rule_effects import (
    ContinuousJournalEffect,
    continuous_journal_effect_from_dict,
)
from .trigger_batches import PendingTriggerBatch, TriggerBatchError
from .util import normalize_mana_bundle, stable_json

CONTROL_HISTORY_VERSION = 1
_COLORED_MANA_SYMBOLS = tuple("WUBRG")

ZoneName = Literal[
    "library",
    "hand",
    "battlefield",
    "graveyard",
    "exile",
    "command",
    "stack",
    "outside",
]

ObjectKind = Literal[
    "card",
    "token",
    "spell_copy",
    "card_copy",
    "emblem",
]

PrincipalRole = Literal["pilot", "arbiter", "analyst", "spectator", "admin"]

# Public/checkpoint field for generic player counter state. The value is also
# an exact printed card name, so architecture analysis treats only this named
# structural constant as schema vocabulary rather than card dispatch.
PLAYER_COUNTERS_FIELD = "counters"

TurnHistoryEventKind = Literal[
    "spell_cast",
    "creature_attacked",
    "creature_died",
    "player_damaged",
    "permanent_damaged",
]


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

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["types"] = list(self.types)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurnHistoryEvent":
        payload = dict(data)
        payload["types"] = tuple(str(value) for value in payload.get("types", ()))
        return cls(**payload)


@dataclass(slots=True)
class TurnHistory:
    """Versioned current-turn facts used by deterministic rules queries."""

    schema_version: int = 1
    turn_sequence: int = 0
    events: list[TurnHistoryEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "turn_sequence": self.turn_sequence,
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurnHistory":
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            turn_sequence=int(data.get("turn_sequence", 0)),
            events=[
                TurnHistoryEvent.from_dict(event)
                for event in data.get("events", [])
            ],
        )


@dataclass(frozen=True, slots=True)
class GoadDesignation:
    """One player's noncopiable goad designation on one permanent.

    The subject owns this value by containment in ``CardInstance``. A zone
    change therefore removes it with the rest of the old object's state.
    ``expires_at_turns_begun`` is the goading player's next turn boundary,
    including an extra turn.
    """

    player: str
    expires_at_turns_begun: int
    created_turn_sequence: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GoadDesignation":
        return cls(
            player=str(data["player"]),
            expires_at_turns_begun=int(data["expires_at_turns_begun"]),
            created_turn_sequence=int(data.get("created_turn_sequence", 0)),
        )


@dataclass(slots=True)
class CardInstance:
    object_id: str
    ref: str
    oracle_id: str
    printed_name: str
    owner: str
    controller: str
    zone: str
    is_token: bool = False
    is_commander: bool = False
    # CR 903.3 designation belongs to this physical card, not its Oracle
    # identity or current logical incarnation. ``None`` is retained for
    # historical Game Record v3 checkpoints created before identity v2.
    commander_designation_id: str | None = None
    # CR 903.9a offers one command-zone state action per graveyard/exile
    # incarnation. ``None`` preserves historical Game Record v3 payloads.
    commander_zone_choice_logical_id: str | None = None
    zone_change_counter: int = 0
    zone_timestamp: int = 0
    world_supertype_timestamp: int | None = None
    has_left_battlefield: bool = False
    tapped: bool = False
    face_down: bool = False
    active_face: str | None = None
    phased_out: bool = False
    counters: dict[str, int] = field(default_factory=dict)
    marked_damage: int = 0
    deathtouch_damage: bool = False
    # CR 701.19a one-shot destruction replacement effects attached to this
    # logical object. Zero is omitted from Game Record v3 for checkpoint
    # compatibility.
    regeneration_shields: int = 0
    temporary_keywords: list[str] = field(default_factory=list)
    goaded_by: list[GoadDesignation] = field(default_factory=list)
    # CR 701.37b noncopiable permanent designation. ``None`` means the
    # current logical object has not become monstrous.  The recorded value
    # supports rules text that later refers to the N used for monstrosity.
    monstrous_value: int | None = None
    # CR 702.112b public noncopiable designation for this logical object.
    # False is omitted from serialized state for historical replay parity.
    renowned: bool = False
    # CR 702.84a creates one public noncopiable designation and leave-
    # battlefield replacement on the returned logical object.
    unearthed: bool = False
    annotations: dict[str, Any] = field(default_factory=dict)
    attached_to: str | None = None
    attachments: list[str] = field(default_factory=list)
    acquired_control_turn_count: int = 0
    acquired_control_timestamp: int = 0
    entered_battlefield_turn_sequence: int = 0
    revealed_to: list[str] = field(default_factory=list)
    known_to: list[str] = field(default_factory=list)
    attacking: str | None = None
    blocking: str | None = None
    battle_protector: str | None = None
    object_kind: ObjectKind = "card"

    def __post_init__(self) -> None:
        """Keep legacy token state compatible with the typed object kind."""

        if self.is_token:
            self.object_kind = "token"
        elif self.object_kind == "token":
            self.is_token = True
        if self.object_kind not in {
            "card",
            "token",
            "spell_copy",
            "card_copy",
            "emblem",
        }:
            raise ValueError(
                f"Unsupported game object kind {self.object_kind!r}"
            )
        if self.commander_designation_id == "":
            raise ValueError("Commander designation IDs cannot be empty")
        if self.commander_designation_id is not None and not self.is_commander:
            raise ValueError(
                "Only a designated commander card may carry a commander ID"
            )
        if self.commander_zone_choice_logical_id == "":
            raise ValueError(
                "Commander zone-choice logical IDs cannot be empty"
            )
        if (
            self.commander_zone_choice_logical_id is not None
            and not self.is_commander
        ):
            raise ValueError(
                "Only a commander may carry zone-choice state"
            )
        if type(self.deathtouch_damage) is not bool:
            raise ValueError("Deathtouch damage state must be a boolean")
        if (
            type(self.regeneration_shields) is not int
            or self.regeneration_shields < 0
        ):
            raise ValueError(
                "Regeneration shield state must be a nonnegative integer"
            )
        if self.monstrous_value is not None and (
            type(self.monstrous_value) is not int
            or self.monstrous_value < 0
        ):
            raise ValueError(
                "A monstrous designation value must be a nonnegative integer"
            )
        if type(self.renowned) is not bool:
            raise ValueError("A renowned designation must be a boolean")
        if type(self.unearthed) is not bool:
            raise ValueError("An unearthed designation must be a boolean")
        if (
            type(self.acquired_control_timestamp) is not int
            or self.acquired_control_timestamp < 0
        ):
            raise ValueError(
                "Control-acquisition timestamp must be a nonnegative integer"
            )

    @property
    def logical_object_id(self) -> str:
        """Authoritative identity for the object's current incarnation."""

        return f"{self.object_id}@{self.zone_change_counter}"

    @property
    def is_card_object(self) -> bool:
        return self.object_kind == "card"

    @property
    def is_spell_copy(self) -> bool:
        return self.object_kind == "spell_copy"

    @property
    def is_card_copy(self) -> bool:
        return self.object_kind == "card_copy"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.commander_designation_id is None:
            # Preserve byte-for-byte historical checkpoint payloads. The
            # GameState identity-version marker distinguishes new records.
            payload.pop("commander_designation_id")
        if self.commander_zone_choice_logical_id is None:
            payload.pop("commander_zone_choice_logical_id")
        if self.monstrous_value is None:
            # Keep historical Game Record v3 card payloads byte-compatible.
            payload.pop("monstrous_value")
        if not self.renowned:
            # Keep historical Game Record v3 card payloads byte-compatible.
            payload.pop("renowned")
        if not self.unearthed:
            payload.pop("unearthed")
        if not self.regeneration_shields:
            # Keep checkpoints created before regeneration byte-compatible.
            payload.pop("regeneration_shields")
        if not self.acquired_control_timestamp:
            # Preserve historical records that predate upkeep-relative
            # control history. New battlefield acquisitions serialize it.
            payload.pop("acquired_control_timestamp")
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CardInstance":
        payload = dict(data)
        payload["goaded_by"] = [
            GoadDesignation.from_dict(value)
            for value in payload.get("goaded_by", [])
        ]
        return cls(**payload)


@dataclass(slots=True)
class YieldPolicy:
    mode: str = "none"
    created_revision: int = 0
    created_event_sequence: int = 0
    created_stack_change_epoch: int = 0
    created_public_change_epoch: int = 0
    created_draw_epoch: int = 0
    created_action_change_epoch: int = 0
    created_turn_sequence: int = 0
    created_priority_epoch: int = 0
    created_active_player: str | None = None
    created_phase: str | None = None
    created_step: str | None = None
    created_land_plays_remaining: int | None = None
    action_signature: str | None = None
    stack_signature: str | None = None
    expires_turn_sequence: int | None = None
    expires_on_stack_change: bool = True
    expires_on_hand_change: bool = True
    expires_on_battlefield_change: bool = True
    stop_phase: str | None = None
    stop_step: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "YieldPolicy":
        return cls(**(data or {}))


@dataclass(slots=True)
class PlayerState:
    seat: str
    name: str
    life: int = 40
    poison: int = 0
    energy: int = 0
    # Poison and energy retain their historical first-class fields. Other
    # public counter kinds live here so CR 122/701.34 does not require a new
    # PlayerState attribute for every future card-defined counter name.
    counters: dict[str, int] = field(default_factory=dict)
    in_game: bool = True
    mana_pool: dict[str, int] = field(default_factory=lambda: normalize_mana_bundle(None))
    zones: dict[str, list[str]] = field(
        default_factory=lambda: {
            "library": [],
            "hand": [],
            "battlefield": [],
            "graveyard": [],
            "exile": [],
            "command": [],
            "outside": [],
        }
    )
    # CR 303.4c/701.3 reciprocal player-attachment sources. Empty is omitted
    # from Game Record v3 checkpoints for historical compatibility.
    attachments: list[str] = field(default_factory=list)
    commander_casts: dict[str, int] = field(default_factory=dict)
    commander_damage_received: dict[str, int] = field(default_factory=dict)
    turns_begun: int = 0
    last_upkeep_timestamp: int = 0
    land_plays_remaining: int = 1
    max_hand_size: int = 7
    mulligans_taken: int = 0
    mulligan_penalty: int = 0
    mulligan_status: str = "pending"  # pending, bottoming, kept
    kept_hand: bool = False
    attempted_empty_draw: bool = False
    draw_history: list[dict[str, Any]] = field(default_factory=list)
    decision_notes: list[dict[str, Any]] = field(default_factory=list)
    rules_seen: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    yield_policy: YieldPolicy = field(default_factory=YieldPolicy)

    def __post_init__(self) -> None:
        if (
            type(self.last_upkeep_timestamp) is not int
            or self.last_upkeep_timestamp < 0
        ):
            raise ValueError(
                "Last-upkeep timestamp must be a nonnegative integer"
            )
        normalized: dict[str, int] = {}
        for raw_name, raw_amount in self.counters.items():
            name = " ".join(str(raw_name).casefold().split())
            if not name:
                raise ValueError("Player counters require a nonempty name")
            if name in {"poison", "energy"}:
                raise ValueError(
                    "Poison and energy use their compatibility state fields"
                )
            if type(raw_amount) is not int or raw_amount < 0:
                raise ValueError(
                    "Player counter amounts must be nonnegative integers"
                )
            if raw_amount:
                if name in normalized:
                    raise ValueError(
                        "Player counter names must remain unique after normalization"
                    )
                normalized[name] = raw_amount
        self.counters = normalized
        if any(
            type(object_id) is not str or not object_id
            for object_id in self.attachments
        ) or len(self.attachments) != len(set(self.attachments)):
            raise ValueError(
                "Player attachment sources must be unique nonempty object IDs"
            )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["yield_policy"] = self.yield_policy.to_dict()
        if not self.counters:
            # Preserve historical Game Record v3 checkpoint payloads until a
            # represented non-legacy player counter actually exists.
            payload.pop(PLAYER_COUNTERS_FIELD)
        if not self.last_upkeep_timestamp:
            # Preserve historical records until an upkeep boundary with a
            # nonzero timestamp has been observed.
            payload.pop("last_upkeep_timestamp")
        if not self.attachments:
            payload.pop("attachments")
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlayerState":
        payload = dict(data)
        payload["mana_pool"] = normalize_mana_bundle(payload.get("mana_pool"))
        payload["yield_policy"] = YieldPolicy.from_dict(payload.get("yield_policy"))
        return cls(**payload)


@dataclass(slots=True)
class StackItem:
    stack_id: str
    ref: str
    kind: str
    controller: str
    label: str
    card_object_id: str | None = None
    source_object_id: str | None = None
    semantic_key: str | None = None
    targets: list[Any] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    x_value: int | None = None
    chosen_face: str | None = None
    notes: str = ""
    default_destination: str | None = None
    visibility: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    # Immutable public cast-payment provenance used by typed rules such as
    # Sunburst.  This records distinct colors, not mana amounts, and remains
    # empty for stack copies because they were not cast.
    mana_colors_spent: tuple[str, ...] = ()
    # Public physical objects explicitly referred to by this stack object for
    # CR 609.7a source choices. This is deliberately separate from arbitrary
    # semantic context so private implementation data is never searched.
    referred_object_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        mana_colors = self.mana_colors_spent
        if not isinstance(mana_colors, (list, tuple)) or any(
            type(value) is not str or value not in _COLORED_MANA_SYMBOLS
            for value in mana_colors
        ):
            raise ValueError(
                "Stack mana colors spent must be WUBRG symbols"
            )
        if len(mana_colors) != len(set(mana_colors)):
            raise ValueError(
                "Stack mana colors spent cannot contain duplicates"
            )
        self.mana_colors_spent = tuple(
            color for color in _COLORED_MANA_SYMBOLS if color in mana_colors
        )
        values = self.referred_object_ids
        if not isinstance(values, (list, tuple)) or any(
            type(value) is not str or not value for value in values
        ):
            raise ValueError(
                "Stack referred-object IDs must be nonempty strings"
            )
        self.referred_object_ids = list(dict.fromkeys(values))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.mana_colors_spent:
            # Preserve historical Game Record v3 checkpoint payloads.
            payload.pop("mana_colors_spent")
        if not self.referred_object_ids:
            # Preserve historical Game Record v3 checkpoint payloads.
            payload.pop("referred_object_ids")
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StackItem":
        return cls(**data)


@dataclass(slots=True)
class TurnEntry:
    turn_id: str
    player: str
    extra: bool = False
    source: str | None = None
    created_sequence: int = 0
    skip_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurnEntry":
        return cls(**data)


@dataclass(slots=True)
class DelayedTrigger:
    trigger_id: str
    ref: str
    controller: str
    label: str
    source_object_id: str | None
    event_kind: str
    condition: dict[str, Any]
    stack_template: dict[str, Any]
    source_logical_object_id: str | None = None
    once: bool = True
    created_turn_sequence: int = 0
    expires_turn_sequence: int | None = None
    active: bool = True
    # Objects explicitly referred to by the waiting delayed triggered ability.
    referred_object_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        values = self.referred_object_ids
        if not isinstance(values, (list, tuple)) or any(
            type(value) is not str or not value for value in values
        ):
            raise ValueError(
                "Delayed-trigger referred-object IDs must be nonempty strings"
            )
        self.referred_object_ids = list(dict.fromkeys(values))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.source_logical_object_id is None:
            payload.pop("source_logical_object_id")
        if not self.referred_object_ids:
            payload.pop("referred_object_ids")
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DelayedTrigger":
        return cls(**data)


@dataclass(slots=True)
class CombatState:
    attackers_declared: bool = False
    blockers_declared: bool = False
    # Historical CR 508.8 predicate. It survives removal from combat.
    had_attacking_creature: bool = False
    attackers: dict[str, str] = field(default_factory=dict)  # attacker object -> defender seat/object
    # The defending-player relationship and attacked-object kind are fixed
    # when an attacker is declared.  They remain available if an attacked
    # permanent later leaves combat, as required by CR 506.4c and 508.5.
    attack_target_context: dict[str, dict[str, str]] = field(
        default_factory=dict
    )
    # Every rules-defined defending player, including opponents not attacked.
    # Decision scheduling derives the actually attacked subset separately.
    defending_players: list[str] = field(default_factory=list)
    blocker_cursor: int = 0
    blockers: dict[str, list[str]] = field(default_factory=dict)  # attacker -> blocker object ids
    damage_assignments: list[dict[str, Any]] = field(default_factory=list)
    # Allocated once when this combat's first damage step is initialized.  The
    # same identity covers both first-strike and ordinary damage steps while a
    # later additional combat receives a distinct identity.
    damage_sequence_id: str | None = None
    damage_step_index: int = 0
    damage_step_initialized: bool = False
    first_strike_step: bool = False
    ordinary_second_damage_combatants: list[str] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # Historical Game Record v3 checkpoints predate this additive field.
        # Omitting the empty value preserves their canonical serialization.
        if self.damage_sequence_id is None:
            payload.pop("damage_sequence_id")
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CombatState":
        return cls(**data)


@dataclass(slots=True)
class DecisionGroup:
    decision_id: str
    kind: str
    role: str
    actors: list[str]
    allowed_actions: list[str]
    payload_by_actor: dict[str, dict[str, Any]] = field(default_factory=dict)
    simultaneous: bool = False
    responses: dict[str, dict[str, Any]] = field(default_factory=dict)
    continuation: dict[str, Any] = field(default_factory=dict)
    created_revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionGroup":
        return cls(**data)


@dataclass(slots=True)
class Capability:
    token: str
    decision_id: str
    principal: str
    role: str
    actor: str | None
    allowed_actions: list[str]
    created_revision: int
    consumed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Capability":
        return cls(**data)


@dataclass(slots=True)
class Event:
    event_id: int
    revision: int
    turn_sequence: int
    active_player: str | None
    phase: str
    step: str
    actor: str | None
    code: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    visibility: list[str] = field(default_factory=list)
    importance: int = 1
    changed_objects: list[str] = field(default_factory=list)
    changed_players: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        return cls(**data)


@dataclass(slots=True)
class GameConfig:
    format_name: str = "commander"
    review_profile: str = "commander_review"
    profile: str = "auto"
    starting_life: int = 40
    poison_to_lose: int = 10
    commander_damage_to_lose: int = 21
    opening_hand_size: int = 7
    free_mulligans: int | None = None
    first_player_draws_on_turn_one: bool | None = None
    auto_untap: bool = True
    auto_draw: bool = True
    strict_timing: bool = True
    strict_mana: bool = True
    seed: int | None = None
    hidden_information_mode: str = "seat-projected"
    priority_optimization: str = "conservative-yield"
    auto_resolve_registered_semantics: bool = True
    semantic_policy: str = "arbitrate_or_pause"
    auto_pass_empty_priority: bool = True
    manual_active_main_phase: bool = False
    realistic_mulligan_guard: bool = True
    max_players: int = 6
    trace_level: str = "standard"

    def effective_profile(self, player_count: int) -> str:
        if self.profile != "auto":
            return self.profile
        return "commander_duel" if player_count == 2 else "commander_multiplayer"

    def effective_free_mulligans(self, player_count: int) -> int:
        if self.free_mulligans is not None:
            return self.free_mulligans
        return 0 if self.effective_profile(player_count) == "commander_duel" else 1

    def effective_first_player_draws(self, player_count: int) -> bool:
        # CR 103.8 applies the starting-player draw exception only to a
        # two-player game.  Player count is authoritative here: a stale or
        # deliberately mismatched profile must never suppress the starting
        # player's draw in a multiplayer game.
        if player_count >= 3:
            return True
        if self.first_player_draws_on_turn_one is not None:
            return self.first_player_draws_on_turn_one
        return False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameConfig":
        return cls(**data)


@dataclass(slots=True)
class GameState:
    game_id: str
    config: GameConfig
    players: dict[str, PlayerState]
    cards: dict[str, CardInstance]
    deck_names: dict[str, str]
    commander_oracle_ids: dict[str, list[str]]
    turn_order: list[str]
    current_turn: TurnEntry | None
    last_normal_turn_player: str | None
    # ``None`` is the explicit historical Game Record v3 compatibility mode
    # whose commander-damage ledgers were keyed by Oracle ID. New games use
    # physical commander designation identity version 2.
    commander_damage_identity_version: int | None = None
    # ``None`` preserves historical Game Record v3 command hashes. New games
    # use version 1 upkeep-relative control-acquisition history.
    control_history_version: int | None = None
    extra_turns: list[TurnEntry] = field(default_factory=list)
    active_player: str | None = None
    priority_player: str | None = None
    priority_passes: list[str] = field(default_factory=list)
    priority_epoch: int = 0
    turn_sequence: int = 0
    phase_index: int = 0
    phase: str = "setup"
    step: str = "mulligan"
    stack: list[StackItem] = field(default_factory=list)
    delayed_triggers: list[DelayedTrigger] = field(default_factory=list)
    # Additive Game Record v3 state for durable CR 609.7/614.9/615 effects.
    # Historical checkpoints omit both arrays and deserialize as empty.
    damage_prevention_shields: list[DamagePreventionShield] = field(
        default_factory=list
    )
    damage_redirections: list[DamageRedirectionEffect] = field(
        default_factory=list
    )
    # Additive Game Record v3 duration journal. ``None`` preserves historical
    # checkpoints whose temporary continuous effects used annotations.
    continuous_effects: list[ContinuousJournalEffect] | None = field(
        default_factory=list
    )
    # Authoritative CR 608.2i look-back facts. ``None`` is reserved for legacy
    # Game Record v3 checkpoints created before this additive feature existed.
    turn_history: TurnHistory | None = field(default_factory=TurnHistory)
    # CR 725 designation. ``None`` means no player has become the monarch.
    monarch: str | None = None
    pending_trigger_batches: list[PendingTriggerBatch] = field(
        default_factory=list
    )
    combat: CombatState = field(default_factory=CombatState)
    events: list[Event] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)
    action_opportunities: list[dict[str, Any]] = field(default_factory=list)
    opportunity_sequence: int = 0
    timestamp_sequence: int = 0
    pending_decision: DecisionGroup | None = None
    capabilities: dict[str, Capability] = field(default_factory=dict)
    started: bool = False
    game_over: bool = False
    winner: str | None = None
    draw: bool = False
    eliminated_players: list[str] = field(default_factory=list)
    revision: int = 0
    event_sequence: int = 0
    state_version: int = 3
    mulligan_round: int = 0
    ref_counters: dict[str, int] = field(default_factory=dict)

    def active_seats(self) -> list[str]:
        return [seat for seat in self.turn_order if self.players[seat].in_game]

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "config": self.config.to_dict(),
            "players": {seat: player.to_dict() for seat, player in self.players.items()},
            "cards": {object_id: card.to_dict() for object_id, card in self.cards.items()},
            "deck_names": dict(self.deck_names),
            "commander_oracle_ids": {
                seat: list(ids) for seat, ids in self.commander_oracle_ids.items()
            },
            **(
                {
                    "commander_damage_identity_version": (
                        self.commander_damage_identity_version
                    )
                }
                if self.commander_damage_identity_version is not None
                else {}
            ),
            **(
                {"control_history_version": self.control_history_version}
                if self.control_history_version is not None
                else {}
            ),
            "turn_order": list(self.turn_order),
            "current_turn": self.current_turn.to_dict() if self.current_turn else None,
            "last_normal_turn_player": self.last_normal_turn_player,
            "extra_turns": [turn.to_dict() for turn in self.extra_turns],
            "active_player": self.active_player,
            "priority_player": self.priority_player,
            "priority_passes": list(self.priority_passes),
            "priority_epoch": self.priority_epoch,
            "turn_sequence": self.turn_sequence,
            "phase_index": self.phase_index,
            "phase": self.phase,
            "step": self.step,
            "stack": [item.to_dict() for item in self.stack],
            "delayed_triggers": [trigger.to_dict() for trigger in self.delayed_triggers],
            "damage_prevention_shields": [
                shield.to_dict() for shield in self.damage_prevention_shields
            ],
            "damage_redirections": [
                effect.to_dict() for effect in self.damage_redirections
            ],
            **(
                {
                    "continuous_effects": [
                        effect.to_dict() for effect in self.continuous_effects
                    ]
                }
                if self.continuous_effects is not None
                else {}
            ),
            **(
                {"turn_history": self.turn_history.to_dict()}
                if self.turn_history is not None
                else {}
            ),
            "monarch": self.monarch,
            "pending_trigger_batches": [
                batch.to_dict() for batch in self.pending_trigger_batches
            ],
            "combat": self.combat.to_dict(),
            "events": [event.to_dict() for event in self.events],
            "annotations": copy.deepcopy(self.annotations),
            "action_opportunities": copy.deepcopy(self.action_opportunities),
            "opportunity_sequence": self.opportunity_sequence,
            "timestamp_sequence": self.timestamp_sequence,
            "pending_decision": self.pending_decision.to_dict() if self.pending_decision else None,
            "capabilities": {token: cap.to_dict() for token, cap in self.capabilities.items()},
            "started": self.started,
            "game_over": self.game_over,
            "winner": self.winner,
            "draw": self.draw,
            "eliminated_players": list(self.eliminated_players),
            "revision": self.revision,
            "event_sequence": self.event_sequence,
            "state_version": self.state_version,
            "mulligan_round": self.mulligan_round,
            "ref_counters": dict(self.ref_counters),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameState":
        return cls(
            game_id=str(data["game_id"]),
            config=GameConfig.from_dict(data["config"]),
            players={seat: PlayerState.from_dict(player) for seat, player in data["players"].items()},
            cards={oid: CardInstance.from_dict(card) for oid, card in data["cards"].items()},
            deck_names=dict(data["deck_names"]),
            commander_oracle_ids={seat: list(ids) for seat, ids in data["commander_oracle_ids"].items()},
            turn_order=list(data["turn_order"]),
            current_turn=(TurnEntry.from_dict(data["current_turn"]) if data.get("current_turn") else None),
            last_normal_turn_player=data.get("last_normal_turn_player"),
            commander_damage_identity_version=(
                int(data["commander_damage_identity_version"])
                if data.get("commander_damage_identity_version") is not None
                else None
            ),
            control_history_version=(
                cls._control_history_version_from_dict(data)
            ),
            extra_turns=[TurnEntry.from_dict(turn) for turn in data.get("extra_turns", [])],
            active_player=data.get("active_player"),
            priority_player=data.get("priority_player"),
            priority_passes=list(data.get("priority_passes", [])),
            priority_epoch=int(data.get("priority_epoch", 0)),
            turn_sequence=int(data.get("turn_sequence", 0)),
            phase_index=int(data.get("phase_index", 0)),
            phase=str(data.get("phase", "setup")),
            step=str(data.get("step", "mulligan")),
            stack=[StackItem.from_dict(item) for item in data.get("stack", [])],
            delayed_triggers=[DelayedTrigger.from_dict(item) for item in data.get("delayed_triggers", [])],
            damage_prevention_shields=[
                DamagePreventionShield.from_dict(item)
                for item in data.get("damage_prevention_shields", [])
            ],
            damage_redirections=[
                DamageRedirectionEffect.from_dict(item)
                for item in data.get("damage_redirections", [])
            ],
            continuous_effects=(
                cls._continuous_effects_from_dict(data["continuous_effects"])
                if "continuous_effects" in data
                else None
            ),
            turn_history=(
                TurnHistory.from_dict(data["turn_history"])
                if isinstance(data.get("turn_history"), dict)
                else None
            ),
            monarch=data.get("monarch"),
            pending_trigger_batches=cls._pending_trigger_batches_from_dict(
                data.get("pending_trigger_batches", [])
            ),
            combat=CombatState.from_dict(data.get("combat", {})),
            events=[Event.from_dict(event) for event in data.get("events", [])],
            annotations=list(data.get("annotations", [])),
            action_opportunities=list(data.get("action_opportunities", [])),
            opportunity_sequence=int(data.get("opportunity_sequence", 0)),
            timestamp_sequence=int(data.get("timestamp_sequence", 0)),
            pending_decision=(DecisionGroup.from_dict(data["pending_decision"]) if data.get("pending_decision") else None),
            capabilities={token: Capability.from_dict(cap) for token, cap in data.get("capabilities", {}).items()},
            started=bool(data.get("started", False)),
            game_over=bool(data.get("game_over", False)),
            winner=data.get("winner"),
            draw=bool(data.get("draw", False)),
            eliminated_players=list(data.get("eliminated_players", [])),
            revision=int(data.get("revision", 0)),
            event_sequence=int(data.get("event_sequence", 0)),
            state_version=int(data.get("state_version", 2)),
            mulligan_round=int(data.get("mulligan_round", 0)),
            ref_counters=dict(data.get("ref_counters", {})),
        )

    @staticmethod
    def _control_history_version_from_dict(data: Mapping[str, Any]) -> int | None:
        value = data.get("control_history_version")
        if value is None:
            return None
        if type(value) is not int:
            raise ValueError("Control-history version must be an integer")
        if value != CONTROL_HISTORY_VERSION:
            raise ValueError("Unsupported control-history version")
        return value

    @staticmethod
    def _pending_trigger_batches_from_dict(
        data: Any,
    ) -> list[PendingTriggerBatch]:
        if not isinstance(data, list):
            raise TriggerBatchError(
                "pending_trigger_batches must be an array"
            )
        return [PendingTriggerBatch.from_dict(batch) for batch in data]

    @staticmethod
    def _continuous_effects_from_dict(
        data: Any,
    ) -> list[ContinuousJournalEffect]:
        if not isinstance(data, list):
            raise ValueError("continuous_effects must be an array")
        return [continuous_journal_effect_from_dict(effect) for effect in data]

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(stable_json(self.to_dict()), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "GameState":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
