from __future__ import annotations

"""Typed ordinary fixed-mana Unearth activation and resolution ownership."""

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol, Sequence

from .abilities import ActivatedAbility
from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .continuous_effect_state import ResolutionEffectSource
from .model import CardInstance, unearth_leave_replacement_version
from .replacement.immutable import FrozenMap, freeze_value, thaw_value
from .replacement.model import ReplacementClass, ReplacementEffect
from .replacement.operations import SetField
from .trigger_processing import schedule_delayed_trigger
from .util import mana_cost_to_vector
from .zone_object_keyword_grants import commit_zone_object_keyword_grant
from .zone_object_keyword_model import normalized_zone_object_keyword
from .zone_object_state import mark_card_unearthed


UNEARTH_ABILITY_HANDLER_ID = "ability.activated.unearth.v1"
UNEARTH_CAPABILITY_ID = "activation.unearth.fixed_mana"
UNEARTH_EFFECT_HANDLER_ID = "generic.unearth.v1"
UNEARTH_EFFECT_OPERATION = "unearth"
UNEARTH_MECHANIC_ID = "unearth"
_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*$")
_ORDINARY_COST = r"(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+"
_ORDINARY_UNEARTH = re.compile(
    rf"^Unearth\s+(?P<cost>{_ORDINARY_COST})\.?$",
    re.IGNORECASE,
)
_MANA_FIELDS = ("GENERIC", "W", "U", "B", "R", "G", "C")


class UnearthError(ValueError):
    """An Unearth descriptor, designation, or action is malformed."""


@dataclass(frozen=True, slots=True)
class UnearthIntent:
    action: str
    actor: str
    stack_ref: str
    object_id: str
    card_ref: str
    logical_object_id: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if self.action not in {"return", "exile"}:
            raise UnearthError("Unearth action must be return or exile")
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.stack_ref,
                self.object_id,
                self.card_ref,
                self.logical_object_id,
            )
        ):
            raise UnearthError("Unearth intents require complete source identity")
        frozen: list[str | FrozenMap] = []
        for value in self.replacement_selections:
            if isinstance(value, str):
                if not value:
                    raise UnearthError(
                        "Unearth replacement selections must be nonempty"
                    )
                frozen.append(value)
                continue
            selected = freeze_value(value, field="unearth.replacement_selection")
            if not isinstance(selected, FrozenMap):
                raise UnearthError(
                    "Unearth replacement selections must be strings or objects"
                )
            frozen.append(selected)
        object.__setattr__(self, "replacement_selections", tuple(frozen))


def _exact_fields(
    value: Mapping[str, Any],
    expected: set[str],
    *,
    field: str,
) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise UnearthError(f"{field} is missing: {', '.join(missing)}")
    if unknown:
        raise UnearthError(f"{field} has unknown fields: {', '.join(unknown)}")


@dataclass(frozen=True, slots=True)
class OrdinaryUnearthAbilitySpec:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    mana_cost: FrozenMap
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise UnearthError("Unsupported ordinary Unearth schema version")
        if _ABILITY_ID.fullmatch(self.ability_id) is None:
            raise UnearthError("Unearth ability ID must be abN")
        if type(self.line_index) is not int or self.line_index < 0:
            raise UnearthError("Unearth line index must be nonnegative")
        if type(self.oracle_line) is not str or not self.oracle_line:
            raise UnearthError("Unearth Oracle line is required")
        if re.fullmatch(_ORDINARY_COST, self.cost_text) is None:
            raise UnearthError("Unearth cost must use fixed ordinary mana")
        if not isinstance(self.mana_cost, FrozenMap):
            if not isinstance(self.mana_cost, Mapping):
                raise UnearthError("Unearth mana cost must be an object")
            object.__setattr__(self, "mana_cost", FrozenMap(self.mana_cost))
        mana = thaw_value(self.mana_cost)
        expected, complex_symbols = mana_cost_to_vector(self.cost_text)
        if (
            set(mana) != set(_MANA_FIELDS)
            or any(type(value) is not int or value < 0 for value in mana.values())
            or complex_symbols
            or mana != expected
        ):
            raise UnearthError("Unearth mana vector does not match its cost")

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "OrdinaryUnearthAbilitySpec":
        _exact_fields(
            value,
            {
                "schema_version",
                "ability_id",
                "line_index",
                "oracle_line",
                "cost_text",
                "mana_cost",
            },
            field="ordinary Unearth descriptor",
        )
        mana = value["mana_cost"]
        if not isinstance(mana, Mapping):
            raise UnearthError("Unearth mana cost must be an object")
        return cls(
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            mana_cost=FrozenMap(mana),
            schema_version=value["schema_version"],
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

    def to_activated_ability(self) -> ActivatedAbility:
        return ActivatedAbility(
            ability_id=self.ability_id,
            line_index=self.line_index,
            oracle_line=self.oracle_line,
            cost_text=self.cost_text,
            effect_text=(
                "Return this card from your graveyard to the battlefield "
                "with Unearth's haste, delayed exile, and leave replacement."
            ),
            zones=("graveyard",),
            mana=thaw_value(self.mana_cost),
            sorcery_speed=True,
        )


def compile_ordinary_unearth_ability(
    *,
    material_line: str,
    oracle_line: str,
    line_index: int,
) -> OrdinaryUnearthAbilitySpec | None:
    match = _ORDINARY_UNEARTH.fullmatch(material_line.strip())
    if match is None:
        return None
    cost_text = match.group("cost").upper()
    mana, complex_symbols = mana_cost_to_vector(cost_text)
    if complex_symbols:
        return None
    return OrdinaryUnearthAbilitySpec(
        ability_id=f"ab{line_index + 1}",
        line_index=line_index,
        oracle_line=oracle_line,
        cost_text=cost_text,
        mana_cost=FrozenMap(mana),
    )


def ordinary_unearth_handler_descriptor(
    spec: OrdinaryUnearthAbilitySpec,
) -> dict[str, Any]:
    return {
        "handler_id": UNEARTH_ABILITY_HANDLER_ID,
        "schema_version": 1,
        "event": "activate",
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        "ability": spec.to_dict(),
    }


def unearthed_leave_replacement(
    card: CardInstance, *, version: int | None,
) -> ReplacementEffect | None:
    """Return the mandatory CR 702.84a replacement for one incarnation."""

    if not isinstance(card, CardInstance) or not card.unearthed:
        return None
    if card.zone != "battlefield" or card.object_kind != "card":
        raise UnearthError("Unearthed designation requires a battlefield card")
    replacement_class = (ReplacementClass.OTHER if unearth_leave_replacement_version(version) == 1
                         else ReplacementClass.SELF_REPLACEMENT)
    return ReplacementEffect(
        effect_id=f"rule:unearth:{card.logical_object_id}",
        source_id=card.ref,
        event_kind="zone.change",
        replacement_class=replacement_class,
        conditions={
            "origin": {"eq": "battlefield"},
            "destination": {"not_in": ["exile"]},
            "object_ref": {"eq": card.ref},
            "logical_object_id": {"eq": card.logical_object_id},
        },
        operations=(SetField("destination", "exile"),),
        label=f"{card.ref}: exile the unearthed permanent instead",
    )


class UnearthHost(Protocol):
    state: Any
    seats: Sequence[str]

    def move_card(self, object_id: str, zone: str, **kwargs: Any) -> Any: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _next_zone_timestamp(self) -> int: ...


def _current_subject(host: UnearthHost, intent: UnearthIntent) -> Any | None:
    card = host.state.cards.get(intent.object_id)
    if (
        card is None
        or card.ref != intent.card_ref
        or card.logical_object_id != intent.logical_object_id
    ):
        return None
    return card


def _resolve_unearth_return(
    host: UnearthHost,
    intent: UnearthIntent,
) -> str | None:
    card = _current_subject(host, intent)
    if card is None or card.zone != "graveyard" or card.object_kind != "card":
        return None
    normalized_zone_object_keyword("Haste")
    host.move_card(
        card.object_id,
        "battlefield",
        controller=intent.actor,
        reason="Unearth resolved",
        semantic_events=True,
        replacement_selections=intent.replacement_selections,
    )
    if card.zone != "battlefield" or card.controller != intent.actor:
        return None
    mark_card_unearthed(card)
    source = ResolutionEffectSource(
        stack_ref=intent.stack_ref,
        object_id=card.object_id,
        logical_object_id=card.logical_object_id,
        card_ref=card.ref,
    )
    commit_zone_object_keyword_grant(
        host,
        card=card,
        source=source,
        keyword="Haste",
    )
    schedule_delayed_trigger(
        host,
        controller=intent.actor,
        label=f"Exile {card.ref} at the next end step",
        event_kind="step.begin",
        condition={"phase": "ending", "step": "end_step"},
        stack_template={
            "label": f"Unearth — exile {card.ref}",
            "context": {
                "dynamic_effects": [
                    {"op": UNEARTH_EFFECT_OPERATION, "action": "exile"}
                ]
            },
        },
        source_object_id=card.object_id,
        referred_object_ids=(card.object_id,),
        once=True,
    )
    host._log(
        intent.actor,
        "permanent.unearthed",
        f"{card.ref} returned with Unearth.",
        {
            "object": card.ref,
            "logical_object_id": card.logical_object_id,
            "controller": card.controller,
        },
        importance=2,
        changed_objects=[card.object_id],
        changed_players=[card.controller],
    )
    return card.ref


def _resolve_unearth_exile(
    host: UnearthHost,
    intent: UnearthIntent,
) -> str | None:
    card = _current_subject(host, intent)
    if (
        card is None
        or card.zone != "battlefield"
        or card.phased_out
        or not card.unearthed
    ):
        return None
    host.move_card(
        card.object_id,
        "exile",
        reason="Unearth delayed trigger",
        semantic_events=True,
        replacement_selections=intent.replacement_selections,
    )
    return card.ref if card.zone == "exile" else None


def resolve_unearth_intent(
    host: UnearthHost,
    intent: UnearthIntent,
) -> str | None:
    if not isinstance(intent, UnearthIntent):
        raise UnearthError("Unearth resolution requires a typed intent")
    if intent.action == "return":
        return _resolve_unearth_return(host, intent)
    if intent.action == "exile":
        return _resolve_unearth_exile(host, intent)
    raise UnearthError("Unknown Unearth action")


__all__ = [
    "compile_ordinary_unearth_ability",
    "OrdinaryUnearthAbilitySpec",
    "ordinary_unearth_handler_descriptor",
    "resolve_unearth_intent",
    "UNEARTH_ABILITY_HANDLER_ID",
    "UNEARTH_CAPABILITY_ID",
    "UNEARTH_EFFECT_HANDLER_ID",
    "UNEARTH_EFFECT_OPERATION",
    "UNEARTH_MECHANIC_ID",
    "UnearthIntent",
    "UnearthError",
    "UnearthHost",
    "unearthed_leave_replacement",
]
