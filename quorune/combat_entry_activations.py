from __future__ import annotations

"""Typed fixed Ninjutsu activation and midcombat-entry ownership."""

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol, Sequence

from .abilities import ActivatedAbility, CostChoice
from .additional_cost_vocabulary import RETURN_ONE_TO_OWNER_HAND_COST
from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .combat_relationship_state import (
    AttackDeclarationAssignment,
    commit_attack_declaration,
)
from .cast_lifecycles import FIXED_CAST_LIFECYCLE_CAPABILITY_ID
from .object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from .replacement.immutable import FrozenMap, freeze_value, thaw_value
from .trigger_processing import schedule_delayed_trigger
from .util import mana_cost_to_vector


NINJUTSU_ABILITY_HANDLER_ID = "ability.activated.ninjutsu.v1"
NINJUTSU_EFFECT_HANDLER_ID = "generic.ninjutsu-entry.v1"
NINJUTSU_EFFECT_OPERATION = "ninjutsu_entry"
NINJUTSU_MECHANICS = frozenset({"ninjutsu", "commander ninjutsu"})
ENCORE_ABILITY_HANDLER_ID = "ability.activated.encore.v1"
ENCORE_EFFECT_HANDLER_ID = "generic.encore-tokens.v1"
ENCORE_EFFECT_OPERATION = "encore_tokens"
ENCORE_MECHANIC_ID = "encore"
ENCORE_ATTACK_DESIGNATION = "encore_attack_if_able"
FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID = (
    "combat.entry.lifecycle.fixed_public"
)
FIXED_UNBLOCKED_ATTACKER_RETURN_COST_KIND = (
    "return_unblocked_attacker_to_owner_hand"
)
FIXED_COMBAT_RETURN_CONTEXT = "fixed_combat_return"
NINJUTSU_REVEAL_CONTEXT = "ninjutsu_reveal"

_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*$")
_ORDINARY_COST = r"(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+"
_NINJUTSU = re.compile(
    rf"^(?P<commander>Commander )?Ninjutsu (?P<cost>{_ORDINARY_COST})"
    r"(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_ENCORE = re.compile(
    rf"^Encore (?P<cost>{_ORDINARY_COST})(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_MANA_FIELDS = ("GENERIC", "W", "U", "B", "R", "G", "C")


class CombatEntryActivationError(ValueError):
    """A fixed combat-entry activation or intent is malformed."""


@dataclass(frozen=True, slots=True)
class FixedNinjutsuSpec:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    mana_cost: FrozenMap
    commander: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        match = _NINJUTSU.fullmatch(self.oracle_line.strip())
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or _ABILITY_ID.fullmatch(self.ability_id) is None
            or type(self.line_index) is not int
            or self.line_index < 0
            or self.ability_id != f"ab{self.line_index + 1}"
            or match is None
            or (match.group("commander") is not None) is not self.commander
            or match.group("cost").upper() != self.cost_text
        ):
            raise CombatEntryActivationError(
                "Fixed Ninjutsu declaration is malformed"
            )
        if not isinstance(self.mana_cost, FrozenMap):
            if not isinstance(self.mana_cost, Mapping):
                raise CombatEntryActivationError(
                    "Fixed Ninjutsu mana cost must be an object"
                )
            object.__setattr__(self, "mana_cost", FrozenMap(self.mana_cost))
        expected, complex_symbols = mana_cost_to_vector(self.cost_text)
        mana = thaw_value(self.mana_cost)
        if complex_symbols or set(mana) != set(_MANA_FIELDS) or mana != expected:
            raise CombatEntryActivationError(
                "Fixed Ninjutsu mana vector does not match its cost"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "cost_text": self.cost_text,
            "mana_cost": thaw_value(self.mana_cost),
            "commander": self.commander,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedNinjutsuSpec":
        fields = {
            "schema_version",
            "ability_id",
            "line_index",
            "oracle_line",
            "cost_text",
            "mana_cost",
            "commander",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise CombatEntryActivationError(
                "Fixed Ninjutsu descriptor fields are closed"
            )
        mana = value["mana_cost"]
        if not isinstance(mana, Mapping):
            raise CombatEntryActivationError(
                "Fixed Ninjutsu mana cost must be an object"
            )
        return cls(
            schema_version=value["schema_version"],
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            mana_cost=FrozenMap(mana),
            commander=value["commander"],
        )

    def to_activated_ability(self) -> ActivatedAbility:
        return ActivatedAbility(
            ability_id=self.ability_id,
            line_index=self.line_index,
            oracle_line=self.oracle_line,
            cost_text=(
                f"{self.cost_text}, reveal this card, return an unblocked "
                "attacking creature you control to its owner's hand"
            ),
            effect_text=(
                "Put this card onto the battlefield tapped and attacking "
                "the same player or permanent."
            ),
            zones=("hand", "command") if self.commander else ("hand",),
            mana=thaw_value(self.mana_cost),
            choices=(
                CostChoice(
                    kind=FIXED_UNBLOCKED_ATTACKER_RETURN_COST_KIND,
                    zone="battlefield",
                    predicate=FrozenMap(
                        ObjectQuerySpec(
                            zones=("battlefield",),
                            controller="$actor",
                            types_all=("creature",),
                            state_predicate=PermanentStatePredicateSpec(
                                attacking=True
                            ),
                            known_to_actor=True,
                        ).to_dict()
                    ),
                ),
            ),
        )


def compile_fixed_ninjutsu(
    *, material_line: str, oracle_line: str, line_index: int
) -> FixedNinjutsuSpec | None:
    match = _NINJUTSU.fullmatch(material_line.strip())
    if match is None:
        return None
    cost_text = match.group("cost").upper()
    mana, complex_symbols = mana_cost_to_vector(cost_text)
    if complex_symbols:
        return None
    return FixedNinjutsuSpec(
        ability_id=f"ab{line_index + 1}",
        line_index=line_index,
        oracle_line=oracle_line,
        cost_text=cost_text,
        mana_cost=FrozenMap(mana),
        commander=match.group("commander") is not None,
    )


def fixed_ninjutsu_handler_descriptor(spec: FixedNinjutsuSpec) -> dict[str, Any]:
    return {
        "handler_id": NINJUTSU_ABILITY_HANDLER_ID,
        "schema_version": 1,
        "event": "activate",
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        "ability": spec.to_dict(),
    }


@dataclass(frozen=True, slots=True)
class FixedEncoreSpec:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    mana_cost: FrozenMap
    schema_version: int = 1

    def __post_init__(self) -> None:
        match = _ENCORE.fullmatch(self.oracle_line.strip())
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or _ABILITY_ID.fullmatch(self.ability_id) is None
            or type(self.line_index) is not int
            or self.line_index < 0
            or self.ability_id != f"ab{self.line_index + 1}"
            or match is None
            or match.group("cost").upper() != self.cost_text
        ):
            raise CombatEntryActivationError("Fixed Encore declaration is malformed")
        if not isinstance(self.mana_cost, FrozenMap):
            if not isinstance(self.mana_cost, Mapping):
                raise CombatEntryActivationError(
                    "Fixed Encore mana cost must be an object"
                )
            object.__setattr__(self, "mana_cost", FrozenMap(self.mana_cost))
        expected, complex_symbols = mana_cost_to_vector(self.cost_text)
        mana = thaw_value(self.mana_cost)
        if complex_symbols or set(mana) != set(_MANA_FIELDS) or mana != expected:
            raise CombatEntryActivationError(
                "Fixed Encore mana vector does not match its cost"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "cost_text": self.cost_text,
            "mana_cost": thaw_value(self.mana_cost),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedEncoreSpec":
        fields = {
            "schema_version",
            "ability_id",
            "line_index",
            "oracle_line",
            "cost_text",
            "mana_cost",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise CombatEntryActivationError(
                "Fixed Encore descriptor fields are closed"
            )
        mana = value["mana_cost"]
        if not isinstance(mana, Mapping):
            raise CombatEntryActivationError(
                "Fixed Encore mana cost must be an object"
            )
        return cls(
            schema_version=value["schema_version"],
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            mana_cost=FrozenMap(mana),
        )

    def to_activated_ability(self) -> ActivatedAbility:
        return ActivatedAbility(
            ability_id=self.ability_id,
            line_index=self.line_index,
            oracle_line=self.oracle_line,
            cost_text=f"{self.cost_text}, exile this card from your graveyard",
            effect_text=(
                "Create one Haste copy token for each opponent with Encore's "
                "attack requirement and delayed sacrifice."
            ),
            zones=("graveyard",),
            mana=thaw_value(self.mana_cost),
            exile_source=True,
            sorcery_speed=True,
        )


def compile_fixed_encore(
    *, material_line: str, oracle_line: str, line_index: int
) -> FixedEncoreSpec | None:
    match = _ENCORE.fullmatch(material_line.strip())
    if match is None:
        return None
    cost_text = match.group("cost").upper()
    mana, complex_symbols = mana_cost_to_vector(cost_text)
    if complex_symbols:
        return None
    return FixedEncoreSpec(
        ability_id=f"ab{line_index + 1}",
        line_index=line_index,
        oracle_line=oracle_line,
        cost_text=cost_text,
        mana_cost=FrozenMap(mana),
    )


def fixed_encore_handler_descriptor(spec: FixedEncoreSpec) -> dict[str, Any]:
    return {
        "handler_id": ENCORE_ABILITY_HANDLER_ID,
        "schema_version": 1,
        "event": "activate",
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        "ability": spec.to_dict(),
    }


@dataclass(frozen=True, slots=True)
class NinjutsuEntryIntent:
    actor: str
    stack_ref: str
    source_object_id: str
    source_ref: str
    source_logical_object_id: str
    attack_target: FrozenMap
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.stack_ref,
                self.source_object_id,
                self.source_ref,
                self.source_logical_object_id,
            )
        ):
            raise CombatEntryActivationError(
                "Ninjutsu entry requires complete source identity"
            )
        if not isinstance(self.attack_target, FrozenMap):
            if not isinstance(self.attack_target, Mapping):
                raise CombatEntryActivationError(
                    "Ninjutsu entry requires a typed attack recipient"
                )
            object.__setattr__(
                self, "attack_target", FrozenMap(self.attack_target)
            )
        target = thaw_value(self.attack_target)
        if frozenset(target) not in {
            frozenset({"target", "kind", "defending_player"}),
            frozenset(
                {"target", "kind", "defending_player", "logical_object_id"}
            ),
        }:
            raise CombatEntryActivationError(
                "Ninjutsu attack-recipient fields are closed"
            )
        frozen: list[str | FrozenMap] = []
        for value in self.replacement_selections:
            if isinstance(value, str):
                if not value:
                    raise CombatEntryActivationError(
                        "Ninjutsu replacement selections must be nonempty"
                    )
                frozen.append(value)
            else:
                selected = freeze_value(
                    value, field="ninjutsu.replacement_selection"
                )
                if not isinstance(selected, FrozenMap):
                    raise CombatEntryActivationError(
                        "Ninjutsu replacement selections are malformed"
                    )
                frozen.append(selected)
        object.__setattr__(self, "replacement_selections", tuple(frozen))


class NinjutsuHost(Protocol):
    state: Any
    seats: Sequence[str]

    def move_card(self, object_id: str, zone: str, **kwargs: Any) -> Any: ...

    def create_token(self, controller: str, **kwargs: Any) -> list[str]: ...

    def _resolve_object(self, actor: str, ref: str, **kwargs: Any) -> Any: ...


def resolve_ninjutsu_entry(
    host: NinjutsuHost, intent: NinjutsuEntryIntent
) -> str | None:
    source = host.state.cards.get(intent.source_object_id)
    if (
        source is None
        or source.ref != intent.source_ref
        or source.logical_object_id != intent.source_logical_object_id
        or source.zone not in {"hand", "command"}
        or source.owner != intent.actor
        or host.state.combat is None
    ):
        return None
    host.move_card(
        source.object_id,
        "battlefield",
        controller=intent.actor,
        tapped=True,
        reason="Ninjutsu resolved",
        semantic_events=True,
        replacement_selections=intent.replacement_selections,
    )
    if source.zone != "battlefield" or source.controller != intent.actor:
        return None
    target = thaw_value(intent.attack_target)
    commit_attack_declaration(
        host.state.combat,
        host.state.cards,
        controller=intent.actor,
        assignments=(
            AttackDeclarationAssignment(
                attacker_object_id=source.object_id,
                target=str(target["target"]),
                target_kind=str(target["kind"]),
                defending_player=str(target["defending_player"]),
                target_logical_object_id=(
                    str(target["logical_object_id"])
                    if target.get("logical_object_id") is not None
                    else None
                ),
            ),
        ),
    )
    return source.ref


@dataclass(frozen=True, slots=True)
class EncoreTokensIntent:
    actor: str
    stack_ref: str
    source_object_id: str
    source_ref: str
    source_logical_object_id: str
    opponents: tuple[str, ...]
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.stack_ref,
                self.source_object_id,
                self.source_ref,
                self.source_logical_object_id,
            )
        ):
            raise CombatEntryActivationError(
                "Encore tokens require complete source identity"
            )
        if (
            not isinstance(self.opponents, tuple)
            or any(type(value) is not str or not value for value in self.opponents)
            or len(self.opponents) != len(set(self.opponents))
            or self.actor in self.opponents
        ):
            raise CombatEntryActivationError(
                "Encore opponents must be unique nonactor seats"
            )


def resolve_encore_tokens(host: NinjutsuHost, intent: EncoreTokensIntent) -> tuple[str, ...]:
    source = host.state.cards.get(intent.source_object_id)
    if (
        source is None
        or source.ref != intent.source_ref
        or source.logical_object_id != intent.source_logical_object_id
        or source.zone != "exile"
        or source.owner != intent.actor
        or any(opponent not in host.state.players for opponent in intent.opponents)
    ):
        return ()
    created_refs: list[str] = []
    for opponent in intent.opponents:
        refs = host.create_token(
            intent.actor,
            name="",
            quantity=1,
            copy_of=source.ref,
            copy_source_zone="exile",
            temporary_keywords=("Haste",),
            reason="Encore resolved",
            replacement_selections=intent.replacement_selections,
        )
        for created_ref in refs:
            created = host._resolve_object(
                intent.actor,
                created_ref,
                zones={"battlefield"},
                controlled_only=True,
            )
            created.annotations[ENCORE_ATTACK_DESIGNATION] = {
                "logical_object_id": created.logical_object_id,
                "opponent": opponent,
            }
            schedule_delayed_trigger(
                host,
                controller=intent.actor,
                label=f"Sacrifice {created.ref}",
                event_kind="step.begin",
                condition={"phase": "ending", "step": "end_step"},
                stack_template={
                    "label": f"Encore — sacrifice {created.ref}",
                    "context": {
                        "dynamic_effects": [
                            {
                                "op": "move_if_in_zone",
                                "card": created.ref,
                                "from": "battlefield",
                                "destination": "graveyard",
                                "transition_kind": "sacrifice",
                                "required_controller": intent.actor,
                                "expected_zone_change_counter": (
                                    created.zone_change_counter
                                ),
                                "expected_object_identity": (
                                    created.logical_object_id
                                ),
                            }
                        ]
                    },
                },
                source_object_id=created.object_id,
                referred_object_ids=(created.object_id,),
                once=True,
            )
            created_refs.append(created.ref)
    return tuple(created_refs)


def encore_attack_requirement(card: Any) -> str | None:
    raw = card.annotations.get(ENCORE_ATTACK_DESIGNATION)
    if (
        not isinstance(raw, Mapping)
        or raw.get("logical_object_id") != card.logical_object_id
        or type(raw.get("opponent")) is not str
    ):
        return None
    return str(raw["opponent"])


def begin_ninjutsu_reveal(
    host: Any, source: Any, item: Any
) -> None:
    if FIXED_COMBAT_RETURN_CONTEXT not in item.context:
        return
    item.context[NINJUTSU_REVEAL_CONTEXT] = {
        "source_object_id": source.object_id,
        "source_logical_object_id": source.logical_object_id,
        "previous_revealed_to": list(source.revealed_to),
    }
    source.revealed_to = list(host.seats)


def cleanup_ninjutsu_reveal(host: Any, item: Any) -> None:
    raw = item.context.get(NINJUTSU_REVEAL_CONTEXT)
    if not isinstance(raw, Mapping):
        return
    source = host.state.cards.get(str(raw.get("source_object_id") or ""))
    if (
        source is not None
        and source.logical_object_id == raw.get("source_logical_object_id")
        and source.zone in {"hand", "command"}
        and isinstance(raw.get("previous_revealed_to"), list)
    ):
        source.revealed_to = list(raw["previous_revealed_to"])


__all__ = [
    "begin_ninjutsu_reveal",
    "cleanup_ninjutsu_reveal",
    "CombatEntryActivationError",
    "compile_fixed_encore",
    "compile_fixed_ninjutsu",
    "FIXED_COMBAT_RETURN_CONTEXT",
    "FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID",
    "FIXED_UNBLOCKED_ATTACKER_RETURN_COST_KIND",
    "FixedNinjutsuSpec",
    "FixedEncoreSpec",
    "fixed_encore_handler_descriptor",
    "fixed_ninjutsu_handler_descriptor",
    "NINJUTSU_ABILITY_HANDLER_ID",
    "NINJUTSU_EFFECT_HANDLER_ID",
    "NINJUTSU_EFFECT_OPERATION",
    "NINJUTSU_MECHANICS",
    "ENCORE_ABILITY_HANDLER_ID",
    "ENCORE_EFFECT_HANDLER_ID",
    "ENCORE_EFFECT_OPERATION",
    "ENCORE_MECHANIC_ID",
    "EncoreTokensIntent",
    "encore_attack_requirement",
    "NinjutsuEntryIntent",
    "resolve_ninjutsu_entry",
    "resolve_encore_tokens",
]
