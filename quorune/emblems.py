from __future__ import annotations

"""Preparation and authoritative construction of public command-zone emblems."""

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from .errors import GameRuleError
from .model import CardInstance
from .emblem_program_model import CompiledEmblemSpec, EMBLEM_PROGRAM_ANNOTATION


class EmblemCreationHost(Protocol):
    @property
    def state(self) -> Any: ...
    seats: Sequence[str]

    def _require_seat(self, seat: str, *, in_game: bool) -> None: ...
    def _next_ref(self, prefix: str) -> str: ...
    def _stable_runtime_id(self, kind: str, value: str) -> str: ...
    def _next_zone_timestamp(self) -> int: ...
    def _log(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class PreparedEmblemCreation:
    owner: str
    abilities: tuple[str, ...]
    display_label: str
    semantic_key: str | None
    reason: str
    compiled: CompiledEmblemSpec | None = None

    def __post_init__(self) -> None:
        if type(self.owner) is not str or not self.owner or self.owner != self.owner.strip():
            raise GameRuleError("An emblem requires one canonical owner")
        if (not isinstance(self.abilities, tuple) or not self.abilities
                or any(type(ability) is not str or not ability or ability != ability.strip()
                       for ability in self.abilities)):
            raise GameRuleError("An emblem must have at least one nonempty ability")
        if type(self.display_label) is not str or not self.display_label:
            raise GameRuleError("An emblem requires one display label")
        if self.semantic_key is not None and type(self.semantic_key) is not str:
            raise GameRuleError("An emblem semantic reference must be text or absent")
        if type(self.reason) is not str:
            raise GameRuleError("An emblem creation reason must be text")
        if self.compiled is not None and (
                not isinstance(self.compiled, CompiledEmblemSpec)
                or self.semantic_key is not None
                or self.abilities != tuple(ability.text for ability in self.compiled.abilities)):
            raise GameRuleError('Compiled emblem creation requires its exact ability set')


def prepare_emblem_creation(
    host: EmblemCreationHost,
    owner: str,
    *,
    abilities: Sequence[str],
    display_label: str = "Emblem",
    semantic_key: str | None = None,
    reason: str = "emblem effect",
) -> PreparedEmblemCreation:
    host._require_seat(owner, in_game=True)
    return PreparedEmblemCreation(
        owner=owner,
        abilities=tuple(str(ability).strip() for ability in abilities),
        display_label=str(display_label or "Emblem"),
        semantic_key=semantic_key,
        reason=reason,
    )


def commit_emblem_creation(host: EmblemCreationHost, prepared: PreparedEmblemCreation) -> str:
    if not isinstance(prepared, PreparedEmblemCreation):
        raise GameRuleError("Emblem creation requires a prepared request")
    prepared.__post_init__()
    host._require_seat(prepared.owner, in_game=True)
    compiled_programs = ()
    fragments = ()
    if prepared.compiled is not None:
        from .emblem_programs import resolve_emblem_programs
        compiled_programs = resolve_emblem_programs(host.semantics, prepared.compiled,
            program_is_trusted=host.semantic_program_is_current_trusted)
        from .ability_fragments import StaticComponentSpec, ability_fragment_to_dict
        from .semantic_runtime.ability_fragments import fragments_from_descriptors
        fragments = tuple(fragment for program in compiled_programs for fragment in (
            StaticComponentSpec(program.key), *fragments_from_descriptors(program.handlers)))
    ref = host._next_ref("E")
    object_id = host._stable_runtime_id("emblem-object", ref)
    emblem = CardInstance(
        object_id=object_id,
        ref=ref,
        oracle_id="custom-emblem:" + host._stable_runtime_id("emblem-oracle", ref),
        printed_name=prepared.display_label,
        owner=prepared.owner,
        controller=prepared.owner,
        zone="command",
        object_kind="emblem",
        zone_timestamp=host._next_zone_timestamp(),
        annotations={
            **({EMBLEM_PROGRAM_ANNOTATION: prepared.compiled.to_dict()} if prepared.compiled is not None else {}),
            "display_label": prepared.display_label,
            "emblem_abilities": list(prepared.abilities),
            "emblem_semantic_key": prepared.semantic_key,
            "object_characteristics": {
                "type_line": "",
                "oracle_text": "\n".join(prepared.abilities),
                "colors": [],
                "keywords": [],
                **({'ability_fragments': [ability_fragment_to_dict(value) for value in fragments]} if fragments else {}),
            },
        },
        known_to=list(host.seats),
        revealed_to=list(host.seats),
    )
    return _commit_emblem_object(host.state, host, prepared, emblem)


def _commit_emblem_object(state, host, prepared, emblem):
    from .game_object_store import GameObjectStore
    GameObjectStore(host).commit(upserts=(emblem,))
    state.players[prepared.owner].zones["command"].append(emblem.object_id)
    state.players[prepared.owner].stats["emblem_objects_v1"] = True
    host._log(
        prepared.owner,
        "emblem.create",
        f"{prepared.owner} created {emblem.printed_name}.",
        {
            "object": emblem.ref,
            "label": emblem.printed_name,
            "abilities": list(prepared.abilities),
            "semantic_key": prepared.semantic_key,
            "reason": prepared.reason,
        },
        importance=3,
        changed_objects=[emblem.object_id],
        changed_players=[prepared.owner],
    )
    return emblem.ref


def create_compiled_emblem(host, owner: str, spec: CompiledEmblemSpec, *, reason: str) -> str:
    from .emblem_programs import resolve_emblem_programs
    host._require_seat(owner, in_game=True)
    resolve_emblem_programs(host.semantics, spec, program_is_trusted=host.semantic_program_is_current_trusted)
    return commit_emblem_creation(host, PreparedEmblemCreation(owner=owner,
        abilities=tuple(ability.text for ability in spec.abilities), display_label='Emblem',
        semantic_key=None, reason=reason, compiled=spec))


def create_emblem(
    host: EmblemCreationHost,
    owner: str,
    *,
    abilities: Sequence[str],
    display_label: str = "Emblem",
    semantic_key: str | None = None,
    reason: str = "emblem effect",
) -> str:
    prepared = prepare_emblem_creation(host, owner, abilities=abilities,
        display_label=display_label, semantic_key=semantic_key, reason=reason)
    return commit_emblem_creation(host, prepared)
