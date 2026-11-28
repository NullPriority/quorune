from __future__ import annotations

import json
import copy
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Iterable, Mapping, Sequence

from .permissions import decision_context_for_capability
from .carddb import CardDatabase
from .characteristic_read_frames import characteristic_read_frame
from .deck import DeckDefinition, DeckLoader
from .engine import ActionResult, CommanderEngine
from .errors import GameRuleError
from .model import GameConfig
from .oracle_ir import register_generated_programs
from .projection import ProjectionCursor, StateProjector
from .profiles import (
    DeckProfileCache,
    deck_list_fingerprint,
    deck_source_fingerprint,
)
from .rules.action_catalog import concealed_face_down_exile_cards, visible_decision_payload
from .rules.action_explanations import projected_action_explanations
from .rules.capabilities import CapabilityRegistry, load_default_capability_registry
from .rules.evidence import _capability_validation_snapshot
from .semantic_runtime.action_permissions import (
    land_play_permission_options,
    library_top_visibility,
)
from .semantic_runtime.combat_metadata import active_goading_players
from .semantic_runtime.hand_visibility import hand_is_public
from .record import (
    ENGINE_VERSION,
    RECORD_SCHEMA_VERSION,
    authoritative_state_hash,
    capability_id,
    checkpoint_envelope,
    database_fingerprint,
    semantics_fingerprint,
    utc_now,
    write_record,
    write_record_privacy_audit,
)
from .record_trust import (
    capture_stack_resolution_inputs,
    command_card_program_fingerprints,
    resolved_semantic_execution_provenance_rows,
    record_semantics_provenance,
    runtime_trust_provenance,
    submit_with_resolution_capture,
    verified_replay_inputs,
)
from .turn_priority_model import PRIORITY_WINDOW_HOLDS_FIELD, PriorityWindowHolds
from .review_artifacts import write_review_artifacts
from .semantics import PreparedOraclePrograms, SemanticRegistry


ACTION_ALIASES = {
    "p": "pass",
    "k": "keep",
    "m": "mulligan",
    "b": "bottom",
    "l": "play_land",
    "c": "cast",
    "x": "activate",
    "atk": "attack",
    "blk": "block",
    "dmg": "assign_damage",
    "r": "resolve",
    "ch": "choose",
    "ord": "order",
    "con": "concede",
}
FIELD_ALIASES = {
    "y": "yield",
    "src": "source",
    "t": "targets",
    "tg": "targets",
    "crd": "card",
    "cs": "cards",
    "atk": "attackers",
    "blk": "blocks",
    "eff": "effects",
    "sem": "semantic_key",
    "n": "note",
}


def _state_projector(
    card_db: CardDatabase,
    engine: CommanderEngine,
) -> StateProjector:
    def stack_effects(key: str):
        program = engine.semantics.get(key)
        return () if program is None else program.effects

    return StateProjector(
        card_db,
        engine.state,
        characteristic_resolver=engine._effective_card_data,
        action_explanation_resolver=lambda seat: (
            projected_action_explanations(engine, seat)
        ),
        library_top_visibility_resolver=lambda owner, viewer: (
            library_top_visibility(engine, owner, viewer or "") is not None
        ),
        hand_visibility_resolver=lambda owner: hand_is_public(engine, owner),
        stack_effects_resolver=stack_effects,
        goading_players_resolver=lambda card: active_goading_players(engine, card),
        land_play_count_resolver=lambda seat: len(
            land_play_permission_options(engine, seat)
        ),
        land_entry_life_resolver=engine._land_entry_life_amount,
    )


def _observation_provenance(cursor: ProjectionCursor | None) -> dict[str, Any]:
    return {
        "projected_state_hash": cursor.view_hash if cursor else None,
        "observation_revision": cursor.snapshot.get("rev") if cursor and cursor.snapshot else None,
        "observation_base_hash": cursor.view_hash if cursor else None,
    }


def _deck_rule_names(deck: DeckDefinition) -> tuple[str, ...]:
    return tuple(entry.name for entry in deck.entries if entry.board in {"mainboard", "commander"})


def _deck_program_names(decks: Mapping[str, DeckDefinition]) -> tuple[tuple[str, ...], ...]:
    return tuple(_deck_rule_names(deck) for deck in decks.values())


def _compile_session_rules(
    card_db: CardDatabase,
    deck_names: tuple[tuple[str, ...], ...],
    capabilities: CapabilityRegistry,
    review_profile: str,
    semantics_path: str | Path | None = None,
) -> SemanticRegistry:
    semantics = SemanticRegistry(semantics_path)
    deck_records = [card_db.lookup(name) for names in deck_names for name in names]
    register_generated_programs(
        card_db,
        semantics,
        deck_records,
        trust_level="provisional",
        capability_registry=capabilities,
        capability_profile=review_profile,
        promote_exact_runtime_handlers=True,
        promote_exact_trigger_programs=True,
        promote_exact_effect_programs=True,
        promote_exact_capability_declarations=True,
    )
    return semantics


@dataclass(frozen=True, slots=True)
class PreparedDeckRules:
    """The registered programs and verified groups produced by one deck check."""

    card_db: CardDatabase
    database_hash: str
    deck_names: tuple[str, ...]
    review_profile: str
    capability_fingerprints: tuple[str, str | None]
    pack_fingerprints: tuple[tuple[str, str], ...]
    oracle_programs: Mapping[str, PreparedOraclePrograms]

    @classmethod
    def from_preflight(
        cls, card_db: CardDatabase, deck: DeckDefinition, registry: SemanticRegistry,
        report: Mapping[str, Any], checked_card_programs: Mapping[str, Any],
    ) -> "PreparedDeckRules | None":
        names = _deck_rule_names(deck)
        rows = report.get("cards", ())
        capability_fingerprint = report.get("capability_registry_fingerprint")
        if len(rows) != len(names) or not capability_fingerprint or report.get("card_data_metadata") != card_db.metadata():
            return None
        oracle_ids = dict.fromkeys(row["oracle_id"] for row in rows)
        return cls(
            card_db, database_fingerprint(card_db)["metadata_hash"], names, report["capability_profile"],
            (capability_fingerprint, report.get("capability_evidence_fingerprint")),
            tuple((pack["path"], pack["hash"]) for pack in registry.loaded_packs),
            {
                oracle_id: PreparedOraclePrograms(
                    tuple(registry.programs_for_oracle(oracle_id)), checked_card_programs.get(oracle_id),
                )
                for oracle_id in oracle_ids
            },
        )


def _assemble_submitted_deck_rules(
    card_db: CardDatabase,
    database_hash: str,
    deck_names: tuple[tuple[str, ...], ...],
    capabilities: CapabilityRegistry,
    review_profile: str,
    submitted_decks: tuple[PreparedDeckRules, ...],
) -> SemanticRegistry | None:
    if len(submitted_decks) != len(deck_names):
        return None
    semantics = SemanticRegistry()
    packs = tuple((pack["path"], pack["hash"]) for pack in semantics.loaded_packs)
    capability_fingerprints = (capabilities.fingerprint, capabilities.evidence_fingerprint)
    for names, deck in zip(deck_names, submitted_decks, strict=True):
        if (
            deck.card_db is not card_db or deck.database_hash != database_hash or deck.deck_names != names
            or deck.review_profile != review_profile or deck.capability_fingerprints != capability_fingerprints
            or deck.pack_fingerprints != packs
        ):
            return None
    if not semantics.install_prepared_oracle_programs(deck.oracle_programs for deck in submitted_decks):
        return None
    return semantics


@dataclass(frozen=True, slots=True)
class PreparedSessionRules:
    """An in-memory seed for a new session on the same rule and card inputs."""

    _card_db: CardDatabase
    _database_hash: str
    _deck_names: tuple[tuple[str, ...], ...]
    _review_profile: str
    _capability_fingerprints: tuple[str, str | None]
    _pack_fingerprints: tuple[tuple[str, str], ...]
    _semantics: SemanticRegistry

    def _copy_for(
        self,
        card_db: CardDatabase,
        decks: Mapping[str, DeckDefinition],
        capabilities: CapabilityRegistry,
        review_profile: str,
    ) -> SemanticRegistry | None:
        if (
            self._card_db is not card_db
            or self._database_hash != database_fingerprint(card_db)["metadata_hash"]
            or self._deck_names != _deck_program_names(decks)
            or self._review_profile != review_profile
            or self._capability_fingerprints != (capabilities.fingerprint, capabilities.evidence_fingerprint)
            or self._pack_fingerprints != SemanticRegistry.builtin_pack_fingerprints()
        ):
            return None
        return copy.deepcopy(self._semantics)


def _load_persisted_plans(path: Path, seats: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    stored_plans = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(stored_plans, Mapping):
        return {}
    pilots = {f"pilot:{seat}" for seat in seats}
    return {
        principal: [
            copy.deepcopy(dict(item))
            for item in items[:64]
            if isinstance(item, Mapping) and item.get("action_id")
        ]
        for principal, items in stored_plans.items()
        if principal in pilots and isinstance(items, list)
    }


@dataclass(slots=True)
class CommanderSession:
    """Untrusted client facade over the authoritative rules engine.

    A single conversation may route all principals sequentially. For strict
    hidden information, run one session client per pilot context against the
    same server-side engine; the command and projection formats are identical.
    """

    card_db: CardDatabase
    engine: CommanderEngine
    projector: StateProjector
    cursors: dict[str, ProjectionCursor] = field(default_factory=dict)
    initial_checkpoint: dict[str, Any] = field(default_factory=dict)
    commands: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    replay_mode: str = "command_replay"
    plans: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    pilot_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    deck_provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    profile_validation: dict[str, dict[str, Any]] = field(default_factory=dict)
    arena_metadata: dict[str, Any] = field(default_factory=dict)
    record_status: str = "created"
    pause_reason: dict[str, Any] | None = None
    _record_semantic_provenance: tuple[bytes, tuple[str, dict[str, dict[str, Any]]]] | None = field(
        default=None, init=False, repr=False,
    )
    _verified_runtime_trust: dict[str, Any] | None = field(default=None, init=False, repr=False)

    @staticmethod
    @_capability_validation_snapshot()
    def prepare_rules(
        card_db: CardDatabase, decks: Mapping[str, DeckDefinition], *, review_profile: str,
        submitted_decks: tuple[PreparedDeckRules, ...] | None = None,
    ) -> PreparedSessionRules:
        capabilities = load_default_capability_registry()
        database_hash = database_fingerprint(card_db)["metadata_hash"]
        deck_names = _deck_program_names(decks)
        semantics = (
            _assemble_submitted_deck_rules(
                card_db, database_hash, deck_names, capabilities, review_profile, submitted_decks,
            )
            if submitted_decks is not None else None
        )
        if semantics is None:
            semantics = _compile_session_rules(card_db, deck_names, capabilities, review_profile)
        semantics.card_programs()
        return PreparedSessionRules(
            card_db,
            database_hash,
            deck_names,
            review_profile,
            (capabilities.fingerprint, capabilities.evidence_fingerprint),
            tuple((pack["path"], pack["hash"]) for pack in semantics.loaded_packs),
            semantics,
        )

    @classmethod
    @_capability_validation_snapshot()
    def create(
        cls,
        card_db: CardDatabase,
        decks: Mapping[str, DeckDefinition],
        *,
        first_player: str,
        player_names: Mapping[str, str] | None = None,
        seed: int | None = None,
        config: GameConfig | None = None,
        semantics_path: str | Path | None = None,
        prepared_rules: PreparedSessionRules | None = None,
    ) -> "CommanderSession":
        game_config = config or GameConfig(seed=seed)
        if seed is not None:
            game_config.seed = seed
        capabilities = load_default_capability_registry()
        semantics = (
            prepared_rules._copy_for(card_db, decks, capabilities, game_config.review_profile)
            if prepared_rules is not None and semantics_path is None else None
        )
        if semantics is None:
            semantics = _compile_session_rules(
                card_db, _deck_program_names(decks), capabilities, game_config.review_profile, semantics_path,
            )
        engine = CommanderEngine.create(
            card_db,
            decks,
            first_player=first_player,
            player_names=player_names,
            config=game_config,
            semantics=semantics,
        )
        profile_cache = DeckProfileCache()
        pilot_profiles: dict[str, dict[str, Any]] = {}
        validations: dict[str, dict[str, Any]] = {}
        provenance: dict[str, dict[str, Any]] = {}
        for seat, deck in decks.items():
            principal = f"pilot:{seat}"
            result = profile_cache.load_validated(deck)
            validation = {
                "status": result.status,
                "profile_fingerprint_match": (
                    result.profile_fingerprint_match
                ),
                "deck_list_fingerprint": deck_list_fingerprint(deck),
                "deck_source_fingerprint": deck_source_fingerprint(deck),
                "warning": result.warning,
            }
            validations[principal] = validation
            if result.profile is not None:
                pilot_profiles[principal] = {
                    **result.profile.to_dict(),
                    "validation": validation,
                }
            provenance[seat] = {
                "source": deck.source,
                "metadata": copy.deepcopy(deck.metadata),
                "deck_list_fingerprint": deck_list_fingerprint(deck),
                "deck_source_fingerprint": deck_source_fingerprint(deck),
            }
        return cls(
            card_db=card_db,
            engine=engine,
            projector=_state_projector(card_db, engine),
            initial_checkpoint=checkpoint_envelope(engine.state),
            pilot_profiles=pilot_profiles,
            deck_provenance=provenance,
            profile_validation=validations,
        )

    @classmethod
    def from_sources(
        cls,
        card_db: CardDatabase,
        sources: Mapping[str, str | Path],
        *,
        commanders: Mapping[str, str] | None = None,
        first_player: str | None = None,
        player_names: Mapping[str, str] | None = None,
        seed: int | None = None,
        cache_dir: str | Path | None = None,
        force_refresh: bool = False,
        semantics_path: str | Path | None = None,
        config: GameConfig | None = None,
    ) -> "CommanderSession":
        loader = DeckLoader(card_db, cache_dir=cache_dir)
        decks: dict[str, DeckDefinition] = {}
        for seat, source in sources.items():
            decks[seat] = loader.load(
                source,
                commander=(commanders or {}).get(seat),
                force_refresh=force_refresh,
            )
            issues = loader.validate_commander_deck(decks[seat])
            if issues:
                raise ValueError(f"{seat} deck validation failed: {'; '.join(issues)}")
        order = list(sources)
        session = cls.create(
            card_db,
            decks,
            first_player=first_player or order[0],
            player_names=player_names,
            seed=seed,
            config=config,
            semantics_path=semantics_path,
        )
        return session

    @property
    def state(self):
        return self.engine.state

    def _cursor(self, principal: str) -> ProjectionCursor:
        return self.cursors.setdefault(principal, ProjectionCursor())

    def pending_principals(self) -> list[str]:
        return list(dict.fromkeys(capability.principal for capability in self.engine.permissions.pending_capabilities()))

    def packet(
        self,
        principal: str,
        *,
        full: bool = False,
        cursor_key: str | None = None,
        observation_epoch: object | None = None,
    ) -> dict[str, Any]:
        # Rebind after a loaded/rolled-back engine state.
        self.projector.state = self.engine.state
        with characteristic_read_frame(self.engine):
            return self.projector.packet(
                principal,
                self._cursor(cursor_key or principal),
                force_full=full,
                observation_epoch=observation_epoch,
            )

    def drop_projection_cursor(self, cursor_key: str) -> None:
        if cursor_key != "arbiter" and not cursor_key.startswith("pilot:"):
            self.cursors.pop(cursor_key, None)

    def next_task(self, *, full: bool = False) -> dict[str, Any] | None:
        for _ in range(64):
            principals = self.pending_principals()
            if not principals:
                self.engine.pump()
                principals = self.pending_principals()
            if not principals:
                return None
            principal = principals[0]
            for planned_principal in list(self.plans):
                if planned_principal != principal:
                    # Another actor receiving a decision is a conservative
                    # public-state invalidation point.
                    self.plans.pop(planned_principal, None)
            queue = self.plans.get(principal) or []
            if not queue:
                return self.packet(principal, full=full)
            planned = dict(queue.pop(0))
            if queue:
                self.plans[principal] = queue
            else:
                self.plans.pop(principal, None)
            future_choices = planned.pop("future_choices", None)
            if (
                isinstance(future_choices, Mapping)
                and str(planned.get("action_id")) != "choose"
            ):
                remainder = self.plans.get(principal) or []
                self.plans[principal] = [
                    {
                        "action_id": "choose",
                        "future_choices": copy.deepcopy(
                            dict(future_choices)
                        ),
                    },
                    *remainder,
                ]
            elif isinstance(future_choices, Mapping):
                try:
                    self._resolve_future_choices(
                        principal,
                        planned,
                        dict(future_choices),
                    )
                except ValueError:
                    self.plans.pop(principal, None)
                    return self.packet(principal, full=full)
            planned.setdefault("automatic", True)
            planned.setdefault("reason", "Execute a still-legal action from the accepted ordered plan.")
            before_stack = tuple(item.ref for item in self.state.stack)
            before_event = self.state.event_sequence
            before_kind = (
                self.state.pending_decision.kind
                if self.state.pending_decision
                else None
            )
            result = self.act(principal, planned)
            if not result.ok:
                self.plans.pop(principal, None)
                return self.packet(principal, full=full)
            new_events = [
                event
                for event in self.state.events
                if event.event_id > before_event
            ]
            expected_continuation = self._ordered_plan_continuation_allowed(
                principal,
                before_kind=before_kind,
                new_events=new_events,
            )
            if (
                tuple(item.ref for item in self.state.stack) != before_stack
                and not expected_continuation
                or any(event.code == "card.draw.private" for event in new_events)
            ):
                self.plans.pop(principal, None)
        raise RuntimeError("Ordered plan exceeded 64 automatic actions without yielding")

    def _resolve_future_choices(
        self,
        principal: str,
        planned: dict[str, Any],
        future_choices: Mapping[str, Any],
    ) -> None:
        decision = self.state.pending_decision
        capability = self.engine.permissions.capability_for(principal)
        if decision is None or capability is None:
            raise ValueError("Future choice has no current capability")
        actor = capability.actor or principal
        context = decision_context_for_capability(decision, capability)
        if principal not in {"analyst", "admin"}:
            context = visible_decision_payload(self.state, actor, context)
        for key, value in future_choices.items():
            if key != "search_card_name":
                planned[str(key)] = copy.deepcopy(value)
                continue
            name = str(value).casefold()
            candidates = list(
                context.get("search_cards")
                or context.get("options")
                or []
            )
            matches = [
                item
                for item in candidates
                if isinstance(item, Mapping)
                and str(item.get("name") or "").casefold() == name
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"Future search name {value!r} is not one unique legal candidate"
                )
            planned["search_card"] = str(matches[0]["id"])

    def _install_plan(
        self,
        principal: str,
        plan: Any,
        *,
        current_action_id: str | None,
    ) -> None:
        if not isinstance(plan, list):
            return
        actionable: list[dict[str, Any]] = []
        for item in plan:
            if isinstance(item, Mapping) and item.get("action_id"):
                planned = copy.deepcopy(dict(item))
                choices = planned.pop("choices", None)
                if isinstance(choices, Mapping):
                    for key, value in choices.items():
                        planned.setdefault(str(key), copy.deepcopy(value))
                actionable.append(planned)
            elif isinstance(item, str) and (
                item in {"keep", "mulligan", "pass", "concede"}
                or ":" in item
            ):
                actionable.append({"action_id": item})
        if actionable and current_action_id and actionable[0].get("action_id") == current_action_id:
            completed = actionable.pop(0)
            future = completed.get("future_choices")
            if isinstance(future, Mapping):
                actionable.insert(
                    0,
                    {
                        "action_id": "choose",
                        "future_choices": copy.deepcopy(dict(future)),
                    },
                )
        if actionable:
            self.plans[principal] = actionable

    def _ordered_plan_continuation_allowed(
        self,
        principal: str,
        *,
        before_kind: str | None,
        new_events: Sequence[Any],
    ) -> bool:
        """Allow only the predeclared choice/resolution inside a fetch plan."""

        queue = self.plans.get(principal) or []
        if not queue:
            return False
        if any(event.code == "card.draw.private" for event in new_events):
            return False
        pending = self.state.pending_decision
        pending_principals = self.pending_principals()
        if pending and pending.kind in {"search.fetch", "semantic.search"}:
            return (
                pending_principals == [principal]
                and str(queue[0].get("action_id")) == "choose"
            )
        if before_kind in {"search.fetch", "semantic.search"}:
            return (
                not pending_principals
                or pending_principals == [principal]
            )
        return False

    @staticmethod
    def expand_action(response: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        raw = dict(response)
        action = str(raw.pop("action", raw.pop("a", "")))
        action = ACTION_ALIASES.get(action, action)
        if not action:
            raise ValueError("Response needs 'a' or 'action'")
        payload: dict[str, Any] = {}
        for key, value in raw.items():
            payload[FIELD_ALIASES.get(key, key)] = value
        return action, payload

    def _expand_action_id(
        self,
        principal: str,
        response: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        raw = dict(response)
        choices = raw.pop("choices", None)
        if isinstance(choices, Mapping):
            for key, value in choices.items():
                raw.setdefault(str(key), copy.deepcopy(value))
        audit_keys = {
            "reason",
            "plan",
            "plan_category",
            "confidence",
            "provider",
            "model",
            "model_id",
            "invocation_id",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
            "latency_ms",
            "provider_invoked",
            "reasoning_effort",
            "thread_id",
            "thread_handle",
            "thread_label",
            "parent_session_id",
            "invoked_at",
            "estimated_input_tokens",
            "retry_count",
            "automatic",
            "fallback",
            "automatic_fallback",
            "memory_update",
            "provider_identity_verified",
            "model_identity_verified",
            "model_configured",
            "reasoning_effort_configured",
        }
        audit = {key: copy.deepcopy(raw.pop(key)) for key in list(raw) if key in audit_keys}
        action_id = raw.pop("action_id", None)
        if not action_id and isinstance(audit.get("plan"), list) and audit["plan"]:
            first = audit["plan"][0]
            if isinstance(first, Mapping):
                action_id = first.get("action_id")
            elif isinstance(first, str) and (
                first in {"keep", "mulligan", "pass", "concede"}
                or ":" in first
            ):
                action_id = first
        if not action_id:
            action, payload = self.expand_action(raw)
            return action, payload, audit
        decision = self.state.pending_decision
        capability = self.engine.permissions.capability_for(principal)
        if decision is None or capability is None:
            raise ValueError("No pending decision for action_id")
        actor_key = capability.actor or principal
        context = decision_context_for_capability(decision, capability)
        if principal not in {"analyst", "admin"}:
            context = visible_decision_payload(self.state, actor_key, context)
        legal = context.get("legal", {})
        catalog = list(
            legal.get("actions")
            or context.get("legal_actions")
            or (
                {"id": allowed, "action": allowed}
                for allowed in capability.allowed_actions
            )
        )
        selected = next(
            (item for item in catalog if str(item.get("id")) == str(action_id)),
            None,
        )
        if selected is None:
            raise ValueError(f"Unknown or stale action_id {action_id!r}")
        action = str(selected["action"])
        payload = {
            key: copy.deepcopy(value)
            for key, value in selected.items()
            if key not in {"id", "action", "search_cards"}
        }
        for key, value in raw.items():
            if key not in {"a", "action"}:
                payload[FIELD_ALIASES.get(key, key)] = value
        audit["action_id"] = str(action_id)
        return action, payload, audit

    def _reject_concealed_exile_source(
        self, actor: str, action: str, payload: Mapping[str, Any],
    ) -> None:
        if action not in {"cast", "play_land", "activate"}:
            return
        raw_zones = payload.get("from")
        if action == "play_land":
            zones = {str(raw_zones or "hand")}
        elif raw_zones is None:
            zones = (
                {"battlefield", "hand", "graveyard", "exile"}
                if action == "activate"
                else {"hand", "command"}
            )
        elif isinstance(raw_zones, str):
            zones = {raw_zones}
        elif isinstance(raw_zones, Sequence) and not isinstance(raw_zones, (bytes, bytearray)):
            zones = {str(zone) for zone in raw_zones}
        else:
            return
        if "exile" not in zones:
            return
        field = "source" if action == "activate" else "card"
        reference = str(payload.get(field) or payload.get("id") or "")
        normalized = reference.casefold().strip()
        concealed = concealed_face_down_exile_cards(self.state, actor)
        if not any(
            reference == card.object_id
            or normalized in {card.ref.casefold(), card.printed_name.casefold()}
            or normalized == self.engine.display_name(card.object_id).casefold()
            for card in concealed
        ):
            return
        try:
            source = self.engine._resolve_object(actor, reference, zones=zones)
        except GameRuleError:
            source = None
        if source is None or any(source is card for card in concealed):
            raise ValueError(f"Could not find {reference!r} for {actor} in requested zones")

    @staticmethod
    def _legal_alternatives(capability: Any, context: Mapping[str, Any]) -> list[dict[str, Any]]:
        legal = context.get("legal") or {}
        return copy.deepcopy(
            list(
                legal.get("actions")
                or context.get("legal_actions")
                or (
                    {"id": action, "action": action}
                    for action in capability.allowed_actions
                )
            )
        )

    def act(
        self,
        principal: str,
        response: Mapping[str, Any],
        *,
        client_command_id: str | None = None,
        priority_window_holds: Mapping[str, PriorityWindowHolds] | None = None,
    ) -> ActionResult:
        capability = self.engine.permissions.capability_for(principal)
        if capability is None:
            return ActionResult(False, f"No pending capability for {principal}", [], state_changed=False)
        try:
            action, payload, audit = self._expand_action_id(principal, response)
            if principal not in {"analyst", "admin"}:
                self._reject_concealed_exile_source(capability.actor or principal, action, payload)
        except ValueError as exc:
            decision = self.state.pending_decision
            actor = capability.actor or principal
            cursor = self.cursors.get(principal)
            actor_context = copy.deepcopy(
                decision_context_for_capability(decision, capability) if decision else {}
            )
            current_hash = authoritative_state_hash(self.state)
            retry_count = sum(
                row.get("decision_id") == (decision.decision_id if decision else None)
                and row.get("principal") == principal
                and not row.get("accepted")
                for row in self.decisions
            )
            reason = response.get("reason")
            self.decisions.append(
                {
                    "sequence": len(self.decisions) + 1,
                    "decision_id": decision.decision_id if decision else None,
                    "kind": decision.kind if decision else None,
                    "role": capability.role,
                    "principal": principal,
                    "actor": actor,
                    "seat": actor if actor in self.state.players else None,
                    "capability_id": capability_id(capability.token),
                    "action": None,
                    "action_id": response.get("action_id"),
                    "payload": {},
                    "legal_alternatives": self._legal_alternatives(capability, actor_context),
                    "decision_context": actor_context,
                    "reason": str(reason)[:180] if reason is not None else None,
                    "plan": response.get("plan"),
                    "plan_category": response.get("plan_category") or (
                        response.get("plan")
                        if isinstance(response.get("plan"), str)
                        else None
                    ),
                    "confidence": response.get("confidence"),
                    "provider": response.get("provider"),
                    "model": response.get("model_id", response.get("model")),
                    "invocation_id": response.get("invocation_id"),
                    "reasoning_effort": response.get(
                        "reasoning_effort"
                    ),
                    "thread_id": response.get("thread_id"),
                    "thread_handle": response.get(
                        "thread_handle", response.get("thread_id")
                    ),
                    "thread_label": response.get("thread_label"),
                    "parent_session_id": response.get(
                        "parent_session_id"
                    ),
                    "invoked_at": response.get("invoked_at"),
                    "provider_invoked": bool(response.get("provider_invoked", False)),
                    "provider_identity_verified": bool(
                        response.get("provider_identity_verified", False)
                    ),
                    "model_identity_verified": bool(
                        response.get("model_identity_verified", False)
                    ),
                    "model_configured": response.get("model_configured"),
                    "reasoning_effort_configured": response.get(
                        "reasoning_effort_configured"
                    ),
                    "metrics": {
                        key: response[key]
                        for key in (
                            "input_tokens",
                            "cached_input_tokens",
                            "output_tokens",
                            "reasoning_output_tokens",
                            "latency_ms",
                            "estimated_input_tokens",
                        )
                        if response.get(key) is not None
                    },
                    "automatic": False,
                    "fallback": response.get(
                        "automatic_fallback", response.get("fallback")
                    ),
                    "retry_count": retry_count,
                    "accepted": False,
                    "rejection": str(exc),
                    "before_state_hash": current_hash,
                    "after_state_hash": current_hash,
                    "turn": self.state.turn_sequence,
                    "phase": self.state.phase,
                    "step": self.state.step,
                    **_observation_provenance(cursor),
                }
            )
            return ActionResult(False, str(exc), [], state_changed=False)
        decision = self.state.pending_decision
        turn = self.state.turn_sequence
        phase = self.state.phase
        step = self.state.step
        cursor = self.cursors.get(principal)
        before_event_sequence = self.state.event_sequence
        before_stack_items = capture_stack_resolution_inputs(self.state)
        before_hash = authoritative_state_hash(self.state)
        before_shuffles = {
            seat: int(player.stats.get("shuffle_count", 0))
            for seat, player in self.state.players.items()
        }
        actor = capability.actor or principal
        actor_context = copy.deepcopy(
            decision_context_for_capability(decision, capability) if decision else {}
        )
        result, resolution_inputs = submit_with_resolution_capture(self.engine, before_stack_items,
            token=capability.token,
            principal=principal,
            action=action,
            payload=payload,
            priority_window_holds=priority_window_holds,
        )
        after_hash = authoritative_state_hash(self.state)
        after_shuffles = {
            seat: int(player.stats.get("shuffle_count", 0))
            for seat, player in self.state.players.items()
        }
        programs_used = resolved_semantic_execution_provenance_rows(
            self.engine, events=self.state.events, before_event_sequence=before_event_sequence,
            before_stack_items=resolution_inputs, capability_profile=self.state.config.review_profile,
        )
        card_programs_used = command_card_program_fingerprints(
            self.engine.semantics, (row["key"] for row in programs_used),
        )
        retry_count = sum(
            row.get("decision_id") == (decision.decision_id if decision else None)
            and row.get("principal") == principal
            and not row.get("accepted")
            for row in self.decisions
        )
        reason = audit.get("reason")
        plan = audit.get("plan")
        decision_row = {
            "sequence": len(self.decisions) + 1,
            "decision_id": decision.decision_id if decision else None,
            "kind": decision.kind if decision else None,
            "role": capability.role,
            "principal": principal,
            "actor": actor,
            "seat": actor if actor in self.state.players else None,
            "capability_id": capability_id(capability.token),
            "action": action,
            "action_id": audit.get("action_id"),
            "payload": copy.deepcopy(payload),
            "legal_alternatives": self._legal_alternatives(capability, actor_context),
            "decision_context": actor_context,
            "reason": str(reason)[:180] if reason is not None else None,
            "plan": plan,
            "plan_category": audit.get("plan_category") or (
                plan if isinstance(plan, str) else None
            ),
            "confidence": audit.get("confidence"),
            "provider": audit.get("provider"),
            "model": audit.get("model_id", audit.get("model")),
            "invocation_id": audit.get("invocation_id"),
            "reasoning_effort": audit.get("reasoning_effort"),
            "thread_id": audit.get("thread_id"),
            "thread_handle": audit.get(
                "thread_handle", audit.get("thread_id")
            ),
            "thread_label": audit.get("thread_label"),
            "parent_session_id": audit.get("parent_session_id"),
            "invoked_at": audit.get("invoked_at"),
            "provider_invoked": bool(audit.get("provider_invoked", False)),
            "provider_identity_verified": bool(
                audit.get("provider_identity_verified", False)
            ),
            "model_identity_verified": bool(
                audit.get("model_identity_verified", False)
            ),
            "model_configured": audit.get("model_configured"),
            "reasoning_effort_configured": audit.get(
                "reasoning_effort_configured"
            ),
            "metrics": {
                key: audit[key]
                for key in (
                    "input_tokens",
                    "cached_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                    "latency_ms",
                    "estimated_input_tokens",
                )
                if key in audit and audit[key] is not None
            },
            "automatic": bool(audit.get("automatic", False)),
            "fallback": audit.get("automatic_fallback", audit.get("fallback")),
            "retry_count": max(retry_count, int(audit.get("retry_count", 0))),
            "accepted": result.ok,
            "rejection": None if result.ok else result.summary,
            "before_state_hash": before_hash,
            "after_state_hash": after_hash,
            "turn": turn,
            "phase": phase,
            "step": step,
            **_observation_provenance(cursor),
        }
        if not audit.get("automatic"):
            self.decisions.append(decision_row)
        if result.ok:
            if self.state.game_over:
                self.record_status = "complete"
                self.pause_reason = None
            elif self.engine._semantic_pause_annotation() is not None:
                # A trusted-only browser game must surface an unsupported
                # rules boundary as a lifecycle pause. Leaving the session
                # "active" with no pilot capability makes every client look
                # like it is endlessly passing priority.
                self.pause()
            else:
                self.record_status = "in_progress"
                self.pause_reason = None
            selected_refs: list[str] = []
            for key in (
                "card",
                "source",
                "search_card",
            ):
                if payload.get(key) is not None:
                    selected_refs.append(str(payload[key]))
            for key in ("cards", "cost_cards", "targets"):
                selected_refs.extend(str(value) for value in payload.get(key) or [])
            registry_fingerprint = semantics_fingerprint(self.engine.semantics)
            self.commands.append(
                {
                    "sequence": len(self.commands) + 1,
                    "command_id": f"C{len(self.commands) + 1}",
                    "client_command_id": client_command_id,
                    "decision_id": decision.decision_id if decision else None,
                    "principal": principal,
                    "actor": actor,
                    "execution": (
                        "planned_automatic"
                        if audit.get("automatic")
                        else "external_decision"
                    ),
                    "action_template_id": audit.get("action_id"),
                    "capability_id": capability_id(capability.token),
                    "action": action,
                    "payload": copy.deepcopy(payload),
                    "normalized_payload": copy.deepcopy(payload),
                    **({PRIORITY_WINDOW_HOLDS_FIELD: {seat: holds.to_dict() for seat, holds in priority_window_holds.items()}}
                        if priority_window_holds else {}),
                    "selected_object_refs": list(dict.fromkeys(selected_refs)),
                    "targets": copy.deepcopy(list(payload.get("targets") or [])),
                    "modes": copy.deepcopy(list(payload.get("modes") or [])),
                    "x": payload.get("x"),
                    "selected_costs": {
                        key: copy.deepcopy(payload[key])
                        for key in (
                            "cost_cards",
                            "payment",
                            "mana",
                            "pay_life",
                            "entry_pay_life",
                        )
                        if key in payload
                    },
                    "before_state_hash": before_hash,
                    "after_state_hash": after_hash,
                    "rng": {
                        "shuffle_counts_before": before_shuffles,
                        "shuffle_counts_after": after_shuffles,
                        "consumed": before_shuffles != after_shuffles,
                        "results": {
                            seat: after_shuffles[seat]
                            for seat in after_shuffles
                            if after_shuffles[seat] != before_shuffles.get(seat)
                        },
                    },
                    "semantics_fingerprint": registry_fingerprint,
                    "semantics": {
                        "registry_schema_version": 1,
                        "registry_hash": registry_fingerprint,
                        "programs_used": programs_used,
                        "card_program_schema_version": 2,
                        "card_programs_used": card_programs_used,
                    },
                    "continuation": (
                        {
                            "kind": decision.kind,
                            "semantic_frame": copy.deepcopy(
                                decision.continuation.get(
                                    "semantic_frame"
                                )
                            ),
                        }
                        if decision
                        and decision.kind.startswith("semantic.")
                        else None
                    ),
                }
            )
            new_events = [
                event
                for event in self.state.events
                if event.event_id > before_event_sequence
            ]
            expected_continuation = (
                self._ordered_plan_continuation_allowed(
                    principal,
                    before_kind=decision.kind if decision else None,
                    new_events=new_events,
                )
            )
            plan_stop = (
                tuple(item.ref for item in self.state.stack)
                != tuple(before_stack_items)
                and not expected_continuation
                or any(
                    event.code == "card.draw.private"
                    for event in new_events
                )
                or self.state.pending_decision is not None
                and principal not in self.pending_principals()
            )
            if plan_stop:
                self.plans.pop(principal, None)
            else:
                self._install_plan(
                    principal,
                    audit.get("plan"),
                    current_action_id=audit.get("action_id"),
                )
        self.projector.state = self.engine.state
        return result

    def rules(
        self,
        values: Sequence[str],
        *,
        include_rulings: bool = True,
        max_rulings_per_card: int = 6,
        format: str = "markdown",
    ) -> Any:
        names: list[str] = []
        by_ref = {card.ref: card for card in self.state.cards.values()}
        for value in values:
            card = by_ref.get(value)
            names.append(card.printed_name if card else value)
        return self.card_db.rules_digest(
            names,
            include_rulings=include_rulings,
            max_rulings_per_card=max_rulings_per_card,
            format=format,
        )

    @_capability_validation_snapshot()
    def save(
        self,
        directory: str | Path,
        *,
        include_review: bool = True,
        runtime_trust_snapshot: Mapping[str, Any] | None = None,
        reuse_semantic_snapshot: bool = False,
    ) -> dict[str, float]:
        started = time.perf_counter()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if not self.initial_checkpoint:
            self.initial_checkpoint = checkpoint_envelope(self.state)
        registry = self.engine.semantics
        registry.path = directory / "semantics.json"
        saved_semantics = registry.save(return_payload=not reuse_semantic_snapshot, reuse_unchanged=reuse_semantic_snapshot)
        semantic_provenance = None
        if reuse_semantic_snapshot:
            digest = registry.record_snapshot_digest
            cached = self._record_semantic_provenance
            if digest is not None and cached is not None and cached[0] == digest:
                semantic_provenance = cached[1]
            else:
                saved_semantics = json.loads(registry.path.read_bytes())
                semantic_provenance = record_semantics_provenance(registry, saved_semantics)
                self._record_semantic_provenance = (digest, semantic_provenance) if digest is not None else None
        manifest = write_record(
            directory,
            state=self.state,
            card_db=self.card_db,
            semantics=self.engine.semantics,
            initial_checkpoint=self.initial_checkpoint,
            commands=self.commands,
            decisions=self.decisions,
            created_at=self.created_at,
            replay_mode=self.replay_mode,
            deck_provenance=self.deck_provenance,
            profile_validation=self.profile_validation,
            codex_arena=self.arena_metadata,
            status=self.record_status,
            pause_reason=self.pause_reason,
            saved_semantics=saved_semantics,
            saved_semantics_provenance=semantic_provenance,
            runtime_trust_snapshot=runtime_trust_snapshot,
            include_privacy_audit=False,
        )
        self.arena_metadata = copy.deepcopy(
            dict(manifest.get("codex_arena") or {})
        )
        cursor_payload = {
            principal: {
                "event_id": cursor.event_id,
                "snapshot": cursor.snapshot,
                "seen_oracles": sorted(cursor.seen_oracles),
                "packet_no": cursor.packet_no,
                "view_hash": cursor.view_hash,
            }
            for principal, cursor in self.cursors.items()
            if not principal.startswith("network:")
        }
        (directory / "cursors.json").write_text(
            json.dumps(cursor_payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        (directory / "pilot-profiles.json").write_text(
            json.dumps(self.pilot_profiles, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (directory / "plans.json").write_text(
            json.dumps(self.plans, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        authoritative_seconds = time.perf_counter() - started
        derived_started = time.perf_counter()
        if include_review:
            write_record_privacy_audit(directory, self.decisions)
            write_review_artifacts(
                directory,
                self.engine,
                decisions=self.decisions,
                manifest=manifest,
            )
        else:
            # Reviews are derived analyst artifacts, not part of the durable
            # command commit.  Remove an older paused/final review after a
            # resumed live save rather than leaving a misleading snapshot.
            for name in ("review.json", "review.md", "hidden-information-audit.json"):
                (directory / name).unlink(missing_ok=True)
        derived_seconds = time.perf_counter() - derived_started
        return {
            "authoritative_seconds": authoritative_seconds,
            "derived_review_seconds": derived_seconds,
            "total_seconds": time.perf_counter() - started,
        }

    def pause(
        self,
        reason: Mapping[str, Any] | None = None,
    ) -> None:
        from .record import pause_reason_for_state

        self.record_status = "paused"
        self.pause_reason = copy.deepcopy(
            dict(reason or pause_reason_for_state(self.state) or {})
        )

    def resume(self) -> None:
        if self.state.game_over:
            raise ValueError("A completed game cannot be resumed")
        if self.record_status == "aborted":
            raise ValueError("An aborted game cannot be resumed")
        self.record_status = "in_progress"
        self.pause_reason = None
        for annotation in self.state.annotations:
            if annotation.get("kind") == "semantic_unsupported":
                annotation["active"] = False

    def abort(self, reason: str) -> None:
        if self.state.game_over:
            raise ValueError("A completed game cannot be aborted")
        self.record_status = "aborted"
        self.pause_reason = {
            "kind": "aborted",
            "label": str(reason)[:500],
        }

    @classmethod
    @_capability_validation_snapshot()
    def load(
        cls,
        card_db: CardDatabase,
        directory: str | Path,
        *,
        semantics_path: str | Path | None = None,
        verify_rules: bool = False,
    ) -> "CommanderSession":
        directory = Path(directory)
        manifest_path = directory / "manifest.json"
        has_record = verify_rules or manifest_path.exists()
        if verify_rules:
            manifest, semantics = verified_replay_inputs(
                directory, semantics_path=semantics_path, record_schema=RECORD_SCHEMA_VERSION,
                engine_version=ENGINE_VERSION, fingerprint_semantics=semantics_fingerprint,
            )
        else:
            persisted_semantics = directory / "semantics.json"
            semantics = SemanticRegistry(
                semantics_path or (persisted_semantics if persisted_semantics.exists() else None)
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if has_record else {}
        if has_record:
            if not isinstance(manifest, dict):
                raise ValueError("Game Record manifest must be an object")
            from .record import load_record_state, read_initial_checkpoint

            state = load_record_state(directory)
            engine = CommanderEngine(card_db, state, semantics)
            engine.permissions.reissue_pending()
            initial_checkpoint = read_initial_checkpoint(directory / "initial-checkpoint.json.gz")
            commands = [
                json.loads(line)
                for line in (directory / "commands.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            decisions = [
                json.loads(line)
                for line in (directory / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            created_at = str(manifest.get("created_at") or utc_now())
            replay_mode = str(
                manifest.get("replay", {}).get("mode") or "command_replay"
            )
            record_status = str(
                manifest.get("status") or "in_progress"
            )
            pause_reason = (
                dict(manifest["pause_reason"])
                if isinstance(manifest.get("pause_reason"), Mapping)
                else None
            )
        else:
            engine = CommanderEngine.load(card_db, str(directory / "game.json"), semantics)
            initial_checkpoint = checkpoint_envelope(engine.state)
            commands = []
            decisions = []
            created_at = utc_now()
            replay_mode = "legacy_snapshot"
            record_status = "in_progress"
            pause_reason = None
        cursors: dict[str, ProjectionCursor] = {}
        cursor_path = directory / "cursors.json"
        if cursor_path.exists():
            for principal, value in json.loads(cursor_path.read_text(encoding="utf-8")).items():
                cursors[principal] = ProjectionCursor(
                    event_id=int(value.get("event_id", 0)),
                    snapshot=value.get("snapshot"),
                    seen_oracles=set(value.get("seen_oracles", [])),
                    packet_no=int(value.get("packet_no", 0)),
                    view_hash=value.get("view_hash"),
                )
        profiles_path = directory / "pilot-profiles.json"
        pilot_profiles = (
            json.loads(profiles_path.read_text(encoding="utf-8"))
            if profiles_path.exists()
            else {}
        )
        plans = _load_persisted_plans(directory / "plans.json", engine.state.players)
        deck_provenance: dict[str, dict[str, Any]] = {}
        profile_validation: dict[str, dict[str, Any]] = {}
        arena_metadata: dict[str, Any] = {}
        if has_record:
            for player in manifest.get("players", []):
                seat = str(player.get("seat") or "")
                if not seat:
                    continue
                deck_provenance[seat] = {
                    "source": player.get("deck_source"),
                    "deck_list_fingerprint": player.get(
                        "deck_list_fingerprint"
                    )
                    or player.get("deck_fingerprint"),
                    "deck_source_fingerprint": player.get(
                        "deck_source_fingerprint"
                    ),
                }
                if player.get("profile_validation") is not None:
                    profile_validation[f"pilot:{seat}"] = dict(
                        player["profile_validation"]
                    )
            arena_metadata = dict(manifest.get("codex_arena") or {})
        for principal, profile in pilot_profiles.items():
            if (
                principal not in profile_validation
                and isinstance(profile, Mapping)
                and isinstance(profile.get("validation"), Mapping)
            ):
                profile_validation[str(principal)] = copy.deepcopy(
                    dict(profile["validation"])
                )
        session = cls(
            card_db=card_db,
            engine=engine,
            projector=_state_projector(card_db, engine),
            cursors=cursors,
            initial_checkpoint=initial_checkpoint,
            commands=commands,
            decisions=decisions,
            created_at=created_at,
            replay_mode=replay_mode,
            plans=plans,
            pilot_profiles=pilot_profiles,
            deck_provenance=deck_provenance,
            profile_validation=profile_validation,
            arena_metadata=arena_metadata,
            record_status=record_status,
            pause_reason=pause_reason,
        )
        if verify_rules and manifest.get("runtime_trust") is not None:
            session._verified_runtime_trust = runtime_trust_provenance()
        return session
