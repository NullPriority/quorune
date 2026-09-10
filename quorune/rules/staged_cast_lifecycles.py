from __future__ import annotations

"""Shared Foretell and Plot no-stack staging actions."""

from dataclasses import replace
from typing import Any, Mapping, Protocol, Sequence

from ..cast_lifecycles import (
    FixedCastLifecycleKind,
    FixedCastLifecycleSpec,
    REBOUND_EXILE_CAST_PRODUCER,
)
from ..compiled_cast_lifecycles import compiled_fixed_cast_lifecycle_specs
from ..errors import GameRuleError
from ..mana_undo import clear_mana_undo_stack
from ..model import CardInstance
from ..model import StackItem
from ..replacement.immutable import thaw_value
from .action_proposals import ActionOffer, freeze_json
from ..zone_object_state import mark_fixed_zone_cast_designation


STAGED_CAST_ACTION = "stage_cast_lifecycle"
_STAGED_KINDS = frozenset(
    {FixedCastLifecycleKind.FORETELL, FixedCastLifecycleKind.PLOT}
)


class StagedCastLifecycleHost(Protocol):
    state: Any
    seats: Sequence[str]

    def _check_priority(self, seat: str) -> None: ...

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str],
        owned_only: bool = False,
    ) -> CardInstance: ...

    def _cost_is_affordable(
        self,
        seat: str,
        requirements: Mapping[str, int],
        *,
        spend_context: Any = None,
    ) -> bool: ...

    def _pay_for_cost(
        self,
        seat: str,
        requirements: Mapping[str, int],
        response: Mapping[str, Any],
        *,
        spend_context: Any = None,
    ) -> tuple[dict[str, int], list[dict[str, Any]]]: ...

    def move_card(self, object_id: str, destination: str, **kwargs: Any) -> Any: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any: ...

    turn_priority: Any


def _staged_spec(
    host: StagedCastLifecycleHost,
    card: CardInstance,
) -> FixedCastLifecycleSpec | None:
    values = tuple(
        spec
        for spec in compiled_fixed_cast_lifecycle_specs(host, card)
        if spec.kind in _STAGED_KINDS
    )
    return values[0] if len(values) == 1 else None


def _special_action_timing_is_legal(
    host: StagedCastLifecycleHost,
    seat: str,
    spec: FixedCastLifecycleSpec,
) -> bool:
    if host.state.active_player != seat:
        return False
    if spec.kind is FixedCastLifecycleKind.FORETELL:
        return True
    return bool(
        spec.kind is FixedCastLifecycleKind.PLOT
        and (host.state.phase, host.state.step)
        in {("precombat_main", "main"), ("postcombat_main", "main")}
        and not host.state.stack
    )


def _staging_requirements(spec: FixedCastLifecycleSpec) -> dict[str, int]:
    if spec.kind is FixedCastLifecycleKind.FORETELL:
        return {"GENERIC": 2, **{color: 0 for color in "WUBRGC"}}
    if spec.kind is FixedCastLifecycleKind.PLOT and spec.mana_cost is not None:
        return thaw_value(spec.mana_cost)
    raise GameRuleError("This lifecycle has no represented staging cost")


def build_staged_cast_offer(
    host: StagedCastLifecycleHost,
    seat: str,
    card: CardInstance,
) -> ActionOffer | None:
    spec = _staged_spec(host, card)
    if (
        spec is None
        or card.zone != "hand"
        or card.owner != seat
        or card.object_kind != "card"
        or card.annotations.get("copy_overrides") is not None
        or not _special_action_timing_is_legal(host, seat, spec)
    ):
        return None
    requirements = _staging_requirements(spec)
    if not host._cost_is_affordable(
        seat,
        requirements,
        spend_context=None,
    ):
        return None
    return ActionOffer(
        action_id=f"{STAGED_CAST_ACTION}:{card.ref}:{spec.ability_id}",
        action=STAGED_CAST_ACTION,
        seat=seat,
        label=f"{spec.kind.value.title()} {card.printed_name}",
        expiry_revision=host.state.revision,
        payload=freeze_json(
            {
                "kind": STAGED_CAST_ACTION,
                "card": card.ref,
                "ability": spec.ability_id,
                "lifecycle": spec.to_dict(),
                "requirements": requirements,
                "auto_pay": True,
            }
        ),
    )


def _zone_replacement_selections(
    response: Mapping[str, Any],
    *,
    card_ref: str,
) -> tuple[Any, ...]:
    raw = response.get("_mana_replacement_selections") or {}
    if not isinstance(raw, Mapping):
        raise GameRuleError("Staged cast replacement journal is malformed")
    rows = [
        value
        for event_id, value in raw.items()
        if type(event_id) is str
        and event_id.startswith("zone.change:")
        and event_id.endswith(f":{card_ref}")
    ]
    if len(rows) != len(raw) or len(rows) > 1:
        raise GameRuleError("Staged cast replacement journal is ambiguous")
    if rows and not isinstance(rows[0], (list, tuple)):
        raise GameRuleError("Staged cast replacement selections are malformed")
    return tuple(rows[0]) if rows else ()


def commit_staged_cast_lifecycle(
    host: StagedCastLifecycleHost,
    *,
    seat: str,
    response: Mapping[str, Any],
) -> None:
    host._check_priority(seat)
    ref = response.get("card") or response.get("id")
    if type(ref) is not str or not ref:
        raise GameRuleError("Staged cast actions require a card ref")
    card = host._resolve_object(
        seat,
        ref,
        zones={"hand"},
        owned_only=True,
    )
    offer = build_staged_cast_offer(host, seat, card)
    if offer is None:
        raise GameRuleError("This card cannot currently be staged")
    supplied = response.get("proposal_fingerprint")
    if supplied is not None:
        expiry = response.get("expiry_revision", offer.expiry_revision)
        if (
            type(expiry) is not int
            or host.state.revision not in {expiry, expiry + 1}
            or str(supplied)
            != replace(offer, expiry_revision=expiry).fingerprint
        ):
            raise GameRuleError("The advertised staged cast action is stale")
    spec = _staged_spec(host, card)
    if (
        spec is None
        or response.get("ability") != spec.ability_id
        or response.get("lifecycle") != spec.to_dict()
    ):
        raise GameRuleError("The staged cast contract changed before payment")
    requirements = _staging_requirements(spec)
    clear_mana_undo_stack(host.state.players[seat].stats)
    spent, activations = host._pay_for_cost(
        seat,
        requirements,
        response,
        spend_context=None,
    )
    host.move_card(
        card.object_id,
        "exile",
        reason=f"{spec.kind.value.title()} special action",
        log=False,
        semantic_events=True,
        replacement_selections=_zone_replacement_selections(
            response,
            card_ref=card.ref,
        ),
    )
    if card.zone == "exile":
        mark_fixed_zone_cast_designation(
            card,
            spec=spec,
            turn_sequence=host.state.turn_sequence,
            viewers=host.seats,
        )
    host._log(
        seat,
        f"card.{spec.kind.value}",
        f"{seat} used {spec.kind.value.title()} on {card.ref}.",
        {
            "card": card.ref,
            "destination": card.zone,
            "requirements": requirements,
            "payment": {key: value for key, value in spent.items() if value},
            "mana_sources": [
                activation.get("source_ref") or activation.get("source")
                for activation in activations
            ],
        },
        importance=2,
        changed_objects=[card.object_id],
        changed_players=[seat],
    )
    host.turn_priority.complete_special_action(seat)


def resolve_rebound_cast_trigger(host: Any, item: StackItem) -> None:
    card = host.state.cards.get(item.source_object_id or "")
    raw_spec = item.context.get("rebound_spec")
    try:
        spec = (
            FixedCastLifecycleSpec.from_dict(raw_spec)
            if isinstance(raw_spec, Mapping)
            else None
        )
    except (TypeError, ValueError):
        spec = None
    current = bool(
        card is not None
        and spec is not None
        and spec.kind is FixedCastLifecycleKind.REBOUND
        and card.zone == "exile"
        and card.logical_object_id
        == item.context.get("source_logical_object_id")
    )
    if not current:
        host._finish_one_shot_exile_cast_resolution(
            item=item,
            producer=REBOUND_EXILE_CAST_PRODUCER,
            cleanup_cards=(),
            outcome="source_unavailable",
            candidate_ref=card.ref if card is not None else None,
        )
        return
    assert card is not None
    options = host._one_shot_exile_cast_options(
        actor=item.controller,
        card=card,
        maximum_mana_value=None,
    )
    if not options:
        host._finish_one_shot_exile_cast_resolution(
            item=item,
            producer=REBOUND_EXILE_CAST_PRODUCER,
            cleanup_cards=(),
            outcome="cast_unavailable",
            candidate_ref=card.ref,
        )
        return
    host._begin_one_shot_exile_cast_choice(
        item=item,
        card=card,
        cleanup_cards=(),
        maximum_mana_value=None,
        producer=REBOUND_EXILE_CAST_PRODUCER,
    )


__all__ = [
    "build_staged_cast_offer",
    "commit_staged_cast_lifecycle",
    "resolve_rebound_cast_trigger",
    "STAGED_CAST_ACTION",
]
