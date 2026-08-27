from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .semantic_runtime import (
    is_structural_activated_ability_catalog_program,
    validate_runtime_handler_descriptors,
)
from .util import stable_json

TRUST_LEVELS = {
    "trusted",
    "provisional",
    "unresolved",
    "intentionally_ignored",
}
SEMANTIC_SCHEMA_VERSION = 3
BUILTIN_PACK_DIRECTORY = Path(__file__).resolve().parent / "semantic_packs"
VALID_EFFECT_OPERATIONS = {
    "amass",
    "add_counter_selected",
    "place_counters",
    "place_counter_batch",
    "place_counters_on_set",
    "place_counters_on_targets",
    "place_player_counters",
    "remove_counters",
    "remove_all_counters",
    "add_type",
    "add_subtype",
    "add_type_until_end_of_turn",
    "add_types_until_end_of_turn",
    "add_subtype_until_end_of_turn",
    "prepare_graveyard_creature_aura",
    "reanimate_attached_creature_aura",
    "attach",
    "bounce",
    "return_permanent_targets_to_owner_hand",
    "return_graveyard_card_to_owner_hand",
    "return_graveyard_targets_to_owner_hand",
    "change_control",
    "change_control_until_end_of_turn",
    "choose_card_name",
    "choose_creature_type",
    "choose_objects",
    "choose_option",
    "choose_cards_apnap",
    "choose_damage_source",
    "choose_mana",
    "copy_all_tokens",
    "copy_stack_item",
    "copy_until_end_of_turn",
    "choose_modified_token_copy",
    "counter",
    "counter_all_subtype",
    "counter_or_destroy_blue",
    "counter_stack",
    "counter_stack_target",
    "counter_unless_pay",
    "cumulative_upkeep",
    "cumulative_upkeep_life",
    "death_return_with_counter",
    "create_token",
    "create_token_batch",
    "create_token_if_no_controlled_subtype",
    "create_treasure",
    "create_modified_token_copy",
    "create_token_copy_if_controlled_count",
    "create_token_if_distinct_controlled_names",
    "create_damage_prevention_shield",
    "create_damage_redirection",
    "damage",
    "damage_each_opponent",
    "damage_fixed_set",
    "delayed_mana",
    "delayed_pact_payment",
    "delayed_trigger",
    "destroy",
    "destroy_targets",
    "destroy_all",
    "destroy_selected",
    "discard",
    "drain_opponent",
    "drain_each_opponent",
    "draw",
    "draw_each_player",
    "draw_with_actions",
    "draw_if_opponent_cast_colors_this_turn",
    "draw_optional_land",
    "offer_draw",
    "end_turn",
    "explore",
    "fabricate",
    "fixed_self_counter_keyword_action",
    "fixed_bolster",
    "fixed_impulse_access",
    "energy",
    "exile",
    "exile_permanent",
    "exile_permanent_targets",
    "exile_all",
    "exile_graveyard",
    "exile_opponent_graveyards",
    "exile_public_graveyard_card",
    "exile_public_graveyard_targets",
    "extra_turn",
    "grant_ability_marker",
    "grant_ability_fragment",
    "grant_zone_object_keyword",
    "fomori_vault",
    "life",
    "lose_life",
    "lose_life_each_opponent",
    "lose_life_equal_mana_value",
    "look_top",
    "look_reorder_top",
    "move",
    "move_if_in_zone",
    "move_public_zone_set",
    "modify_stats_until_end_of_turn",
    "mana",
    "mill",
    "next_spell_improvise",
    "next_spell_uncounterable",
    "note",
    "reorder_top",
    "reveal_top_permanent",
    "search",
    "scry",
    "surveil",
    "sylvan_library_settle",
    "springheart_landfall",
    "station",
    "bestow_prepare",
    "become_monarch",
    "shuffle_into_library",
    "shuffle_graveyard_bottom_random",
    "sacrifice",
    "sacrifice_if_present",
    "tap",
    "tap_targets",
    "modify_all_matching_permanents_until_end_of_turn",
    "untap",
    "untap_targets",
    "untap_all_creatures",
    "unearth",
    "self_zone_move",
    "exchange_artifact_zones",
    "veil_of_summer",
    "pay_or_lose",
    "populate_with_haste",
    "put_land_from_hand",
    "protection_from_everything_until_next_turn",
    "proliferate",
    "regenerate",
    "pump_controlled_creatures",
    "reanimate",
    "retarget_stack_item",
    "remora_tax",
    "grant_keyword_until_end_of_turn",
    "grant_uncounterable_hexproof_from_colors_until_end",
    "goad",
    "grant_cast_permission",
    "grant_declaration_restriction_until_end_of_turn",
    "grant_play_without_mana_cost",
    "put_artifact_from_hand",
    "control_next_turn",
    "create_emblem",
    "daretti_exchange",
    "destroy_selected_and_reward_source",
    "discard_draw_up_to",
    "return_transformed",
    "transmute_artifact",
}


def _is_supported_effect_operation(operation: str) -> bool:
    if operation in VALID_EFFECT_OPERATIONS:
        return True
    from .semantic_runtime import default_semantic_handler_registry
    from .semantic_choices.defaults import default_semantic_choice_registry

    return bool(
        default_semantic_handler_registry().describe(operation)
        or operation in default_semantic_choice_registry().operations
    )


@dataclass(slots=True)
class SemanticProgram:
    key: str
    label: str
    effects: list[dict[str, Any]] = field(default_factory=list)
    destination: str | None = None
    requires_arbiter: bool = False
    notes: str = ""
    version: int = 1
    oracle_id: str | None = None
    ability_id: str = "spell:front"
    active_zone: str = "stack"
    event: str = "resolve"
    semantic_schema_version: int = SEMANTIC_SCHEMA_VERSION
    trust_level: str = "provisional"
    provenance: dict[str, Any] = field(default_factory=dict)
    tests: list[str] = field(default_factory=list)
    handlers: list[dict[str, Any]] = field(default_factory=list)
    target_schema: dict[str, Any] | None = None
    cost_schema: dict[str, Any] | None = None
    event_condition: dict[str, Any] | None = None
    coverage: list[str] = field(default_factory=list)
    capability_dependencies: list[str] = field(default_factory=list)
    capability_closure: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.trust_level not in TRUST_LEVELS:
            raise ValueError(f"Unknown semantic trust level {self.trust_level!r}")
        if self.version < 1 or self.semantic_schema_version < 1:
            raise ValueError("Semantic versions must be positive")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in self.capability_dependencies
        ):
            raise ValueError(
                "Capability dependencies must be nonempty strings"
            )
        if len(self.capability_dependencies) != len(
            set(self.capability_dependencies)
        ):
            raise ValueError("Capability dependencies must be unique")
        validate_runtime_handler_descriptors(self.handlers)
        for handler in self.handlers:
            if handler.get("event") != self.event:
                raise ValueError(
                    "Runtime handler event must match its semantic program"
                )
        if self.capability_dependencies and self.capability_closure is None:
            raise ValueError(
                "Capability dependencies require their resolved closure"
            )
        if self.capability_closure is not None:
            if not self.capability_dependencies:
                raise ValueError(
                    "A capability closure requires direct dependencies"
                )
            if not str(
                self.capability_closure.get("fingerprint") or ""
            ).strip():
                raise ValueError(
                    "A capability closure requires a fingerprint"
                )
            requested = self.capability_closure.get("requested")
            if (
                not isinstance(requested, list)
                or any(not isinstance(value, str) for value in requested)
                or sorted(requested)
                != sorted(self.capability_dependencies)
            ):
                raise ValueError(
                    "Capability closure requested IDs must match direct "
                    "dependencies"
                )
            for field in (
                "reachable",
                "profile",
                "trusted",
                "blockers",
                "registry_fingerprint",
                "evidence_fingerprint",
            ):
                if field not in self.capability_closure:
                    raise ValueError(
                        f"Capability closure requires {field}"
                    )
            if not isinstance(
                self.capability_closure.get("reachable"), list
            ) or not isinstance(
                self.capability_closure.get("blockers"), list
            ):
                raise ValueError(
                    "Capability closure reachability and blockers must be "
                    "lists"
                )
            if not isinstance(
                self.capability_closure.get("trusted"), bool
            ):
                raise ValueError(
                    "Capability closure trusted must be boolean"
                )
            if not str(self.capability_closure.get("profile") or "").strip():
                raise ValueError("Capability closure requires a profile")
            for field in (
                "registry_fingerprint",
                "evidence_fingerprint",
                "fingerprint",
            ):
                fingerprint = str(
                    self.capability_closure.get(field) or ""
                )
                if len(fingerprint) != 64 or any(
                    character not in "0123456789abcdef"
                    for character in fingerprint
                ):
                    raise ValueError(
                        f"Capability closure {field} must be a lowercase "
                        "SHA-256"
                    )
            if self.trust_level == "trusted" and (
                self.capability_closure.get("trusted") is not True
                or self.capability_closure.get("blockers") != []
            ):
                raise ValueError(
                    "Trusted semantics require a trusted unblocked "
                    "capability closure"
                )
        effects_to_validate = list(self.effects)
        mode_definitions = (
            self.target_schema.get("modes")
            if isinstance(self.target_schema, Mapping)
            else None
        )
        if isinstance(mode_definitions, Mapping):
            for definition in mode_definitions.values():
                if isinstance(definition, Mapping):
                    effects_to_validate.extend(
                        effect
                        for effect in definition.get("effects", [])
                        if isinstance(effect, Mapping)
                    )
        for effect in effects_to_validate:
            operation = str(effect.get("op") or "")
            if not _is_supported_effect_operation(operation):
                raise ValueError(
                    f"Unsupported semantic effect operation {operation!r}"
                )
        if self.trust_level == "trusted":
            if not self.oracle_id:
                raise ValueError("Trusted semantics require an oracle_id")
            required_provenance = {
                "source_oracle_hash",
                "source_rulings_hash",
                "authored_by",
                "review_status",
            }
            missing = sorted(
                key
                for key in required_provenance
                if not self.provenance.get(key)
            )
            if missing:
                raise ValueError(
                    "Trusted semantics require provenance fields: "
                    + ", ".join(missing)
                )
            if not self.tests:
                raise ValueError("Trusted semantics require characterization tests")

    def to_dict(self) -> dict[str, Any]:
        value = {
            "key": self.key,
            "label": self.label,
            "effects": self.effects,
            "destination": self.destination,
            "requires_arbiter": self.requires_arbiter,
            "notes": self.notes,
            "version": self.version,
            "oracle_id": self.oracle_id,
            "ability_id": self.ability_id,
            "active_zone": self.active_zone,
            "event": self.event,
            "semantic_schema_version": self.semantic_schema_version,
            "trust_level": self.trust_level,
            "provenance": self.provenance,
            "tests": self.tests,
            "handlers": self.handlers,
            "target_schema": self.target_schema,
            "cost_schema": self.cost_schema,
            "event_condition": self.event_condition,
            "coverage": self.coverage,
        }
        if self.capability_dependencies:
            value["capability_dependencies"] = (
                self.capability_dependencies
            )
            value["capability_closure"] = self.capability_closure
        return value

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticProgram":
        return cls(
            key=str(data["key"]),
            label=str(data.get("label") or data["key"]),
            effects=[dict(effect) for effect in data.get("effects", [])],
            destination=data.get("destination"),
            requires_arbiter=bool(data.get("requires_arbiter", False)),
            notes=str(data.get("notes") or ""),
            version=int(data.get("version", 1)),
            oracle_id=data.get("oracle_id"),
            ability_id=str(data.get("ability_id") or "spell:front"),
            active_zone=str(data.get("active_zone") or "stack"),
            event=str(data.get("event") or "resolve"),
            semantic_schema_version=int(
                data.get("semantic_schema_version", SEMANTIC_SCHEMA_VERSION)
            ),
            trust_level=str(data.get("trust_level") or "provisional"),
            provenance=dict(data.get("provenance") or {}),
            tests=[str(value) for value in data.get("tests", [])],
            handlers=[dict(value) for value in data.get("handlers", [])],
            target_schema=(
                dict(data["target_schema"])
                if isinstance(data.get("target_schema"), Mapping)
                else None
            ),
            cost_schema=(
                dict(data["cost_schema"])
                if isinstance(data.get("cost_schema"), Mapping)
                else None
            ),
            event_condition=(
                dict(data["event_condition"])
                if isinstance(data.get("event_condition"), Mapping)
                else None
            ),
            coverage=[str(value) for value in data.get("coverage", [])],
            capability_dependencies=[
                str(value)
                for value in data.get("capability_dependencies", [])
            ],
            capability_closure=(
                dict(data["capability_closure"])
                if isinstance(data.get("capability_closure"), Mapping)
                else None
            ),
        )


class SemanticRegistry:
    """
    Cache of card/ability semantics expressed in the engine's generic effect DSL.

    The registry is deliberately outside the rules kernel.  A rules model can
    compile an Oracle ability once, store it here, and all later simulations can
    resolve the same object without another LLM call.  A production client may
    replace this JSON registry with generated code or a database without changing
    pilot permissions or the command protocol.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        pack_paths: Iterable[str | Path] = (),
        include_builtin_packs: bool = True,
    ):
        self.path = Path(path) if path else None
        self._programs: dict[str, SemanticProgram] = {}
        self._card_program_cache: dict[str, Any] | None = None
        self._runtime_handler_compatibility: dict[
            tuple[str, str, str], tuple[SemanticProgram, ...]
        ] = {}
        self._runtime_handler_compatibility_enabled = False
        self.loaded_packs: list[dict[str, Any]] = []
        if include_builtin_packs and BUILTIN_PACK_DIRECTORY.exists():
            self.load_packs([BUILTIN_PACK_DIRECTORY])
            grouped: dict[tuple[str, str, str], list[SemanticProgram]] = {}
            for program in self._programs.values():
                if program.oracle_id and program.handlers:
                    grouped.setdefault(
                        (
                            program.oracle_id,
                            program.active_zone,
                            program.event,
                        ),
                        [],
                    ).append(program)
            self._runtime_handler_compatibility = {
                key: tuple(sorted(programs, key=lambda value: value.key))
                for key, programs in grouped.items()
            }
        if pack_paths:
            self.load_packs(pack_paths)
        if self.path and self.path.exists():
            self.load()

    def load(self) -> None:
        if not self.path:
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if raw.get("include_builtin_packs") is False:
            # A saved game carries a complete registry snapshot. Clearing the
            # runtime built-ins prevents a newer package from silently changing
            # the semantics used to replay an older accepted-command prefix.
            self._programs.clear()
            self._card_program_cache = None
            # Historical Game Record v3 registries predate the explicit flag,
            # so absence retains their replay-only Oracle compatibility path.
            # Current registries write ``False`` and must never acquire legacy
            # runtime interpretation merely because they were serialized.
            self._runtime_handler_compatibility_enabled = bool(
                raw.get("runtime_handler_compatibility_enabled", True)
            )
        serialized_card_programs = raw.get("card_programs")
        if serialized_card_programs is not None:
            if raw.get("card_program_schema_version") != 2:
                raise ValueError(
                    "card_programs require card_program_schema_version 2"
                )
            if not isinstance(serialized_card_programs, Mapping):
                raise ValueError("card_programs must be an object")
            from .card_programs import CardProgram

            parsed = {
                str(oracle_id): CardProgram.from_dict(value)
                for oracle_id, value in serialized_card_programs.items()
            }
            for oracle_id, card_program in parsed.items():
                if oracle_id != card_program.oracle_id:
                    raise ValueError(
                        "CardProgram registry key does not match oracle_id"
                    )
                for program in card_program.abilities:
                    self._programs[program.key] = program
            self._card_program_cache = parsed
        programs = raw.get("programs", raw)
        if not isinstance(programs, Mapping):
            raise ValueError("programs must be an object")
        for key, value in programs.items():
            program = SemanticProgram.from_dict(value)
            existing = self._programs.get(str(key))
            if existing is not None and existing.to_dict() != program.to_dict():
                raise ValueError(
                    "CardProgram and legacy semantic program views disagree "
                    f"for {key}"
                )
            self._programs[str(key)] = program
        if serialized_card_programs is None:
            self._card_program_cache = None

    @staticmethod
    def _source_hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def load_packs(self, paths: Iterable[str | Path]) -> None:
        """Load declarative semantic packs without coupling them to the kernel."""

        candidates: list[Path] = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                candidates.extend(sorted(path.glob("*.json")))
            elif path.exists():
                candidates.append(path)
        for path in candidates:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if int(raw.get("schema_version", 0)) != SEMANTIC_SCHEMA_VERSION:
                raise ValueError(
                    f"{path} must use semantic pack schema "
                    f"{SEMANTIC_SCHEMA_VERSION}"
                )
            programs = raw.get("programs", [])
            if isinstance(programs, Mapping):
                items = [
                    {"key": key, **dict(value)}
                    for key, value in programs.items()
                ]
            else:
                items = [dict(value) for value in programs]
            for value in items:
                program = SemanticProgram.from_dict(value)
                self._programs[program.key] = program
            self._card_program_cache = None
            self.loaded_packs.append(
                {
                    "name": str(raw.get("name") or path.stem),
                    "path": str(path),
                    "schema_version": int(raw.get("schema_version", 1)),
                    "hash": self._source_hash(path),
                    "program_count": len(items),
                }
            )

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SEMANTIC_SCHEMA_VERSION,
            "card_program_schema_version": 2,
            "include_builtin_packs": False,
            "runtime_handler_compatibility_enabled": False,
            "card_programs": {
                program.oracle_id: program.to_dict()
                for program in self.card_programs()
            },
            "programs": {
                key: program.to_dict() for key, program in sorted(self._programs.items())
            },
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(stable_json(payload), encoding="utf-8")
        temporary.replace(self.path)

    def get(self, key: str | None) -> SemanticProgram | None:
        if not key:
            return None
        return self._programs.get(key)

    def put(self, program: SemanticProgram | Mapping[str, Any]) -> SemanticProgram:
        if not isinstance(program, SemanticProgram):
            program = SemanticProgram.from_dict(program)
        self._programs[program.key] = program
        self._card_program_cache = None
        self.save()
        return program

    def remove(self, key: str) -> None:
        self._programs.pop(key, None)
        self._card_program_cache = None
        self.save()

    def keys(self) -> list[str]:
        return sorted(self._programs)

    def programs_for_oracle(
        self,
        oracle_id: str,
        *,
        active_zone: str | None = None,
        event: str | None = None,
    ) -> list[SemanticProgram]:
        return [
            program
            for program in self._programs.values()
            if program.oracle_id == oracle_id
            and (active_zone is None or program.active_zone == active_zone)
            and (event is None or program.event == event)
        ]

    def runtime_handler_programs_for_oracle(
        self,
        oracle_id: str,
        *,
        active_zone: str,
        event: str,
    ) -> list[SemanticProgram]:
        current = sorted(
            (
                program
                for program in self.programs_for_oracle(
                    oracle_id,
                    active_zone=active_zone,
                    event=event,
                )
                if program.handlers
            ),
            key=lambda value: value.key,
        )
        if current or not self._runtime_handler_compatibility_enabled:
            return current
        return list(
            self._runtime_handler_compatibility.get(
                (oracle_id, active_zone, event), ()
            )
        )

    def is_runtime_handler_compatibility_program(
        self,
        program: SemanticProgram,
    ) -> bool:
        if not self._runtime_handler_compatibility_enabled:
            return False
        candidates = self._runtime_handler_compatibility.get(
            (program.oracle_id, program.active_zone, program.event), ()
        )
        return any(
            candidate.to_dict() == program.to_dict()
            for candidate in candidates
        )

    @property
    def runtime_handler_compatibility_enabled(self) -> bool:
        """Whether this registry is replaying a historical v3 snapshot."""

        return self._runtime_handler_compatibility_enabled

    def trust_for_oracle(self, oracle_id: str) -> str:
        programs = tuple(
            program
            for program in self.programs_for_oracle(oracle_id)
            if not is_structural_activated_ability_catalog_program(program)
        )
        if not programs:
            return "unresolved"
        levels = {program.trust_level for program in programs}
        if "unresolved" in levels:
            return "unresolved"
        if "provisional" in levels:
            return "provisional"
        if levels == {"intentionally_ignored"}:
            return "intentionally_ignored"
        return "trusted"

    def programs(self) -> list[SemanticProgram]:
        return [self._programs[key] for key in sorted(self._programs)]

    def card_programs(self) -> list[Any]:
        """Return deterministic CardProgram V2 groups used by this registry."""

        if self._card_program_cache is None:
            from .card_programs.adapters import (
                card_programs_from_semantic_programs,
            )

            self._card_program_cache = card_programs_from_semantic_programs(
                self._programs.values()
            )
        return [
            self._card_program_cache[oracle_id]
            for oracle_id in sorted(self._card_program_cache)
        ]

    def card_program_for_oracle(self, oracle_id: str) -> Any | None:
        if self._card_program_cache is not None:
            return self._card_program_cache.get(oracle_id)

        # Runtime trust checks are scoped to one physical card's Oracle
        # program.  Building every compatibility group here lets an unrelated
        # provisional or malformed group prevent otherwise valid cards from
        # evaluating their pinned handlers.  Keep the all-card adapter strict
        # for snapshots and audits, but isolate the ordinary point lookup.
        programs = self.programs_for_oracle(oracle_id)
        if not programs:
            return None
        from .card_programs.adapters import (
            card_program_from_semantic_programs,
        )

        return card_program_from_semantic_programs(programs)

    def card_program_fingerprints(self) -> dict[str, str]:
        return {
            program.oracle_id: program.fingerprint
            for program in self.card_programs()
        }

    def card_program_fingerprints_for_keys(
        self,
        semantic_keys: Iterable[str],
    ) -> dict[str, str]:
        requested = set(semantic_keys)
        return {
            program.oracle_id: program.fingerprint
            for program in self.card_programs()
            if any(
                ability.key in requested for ability in program.abilities
            )
        }
