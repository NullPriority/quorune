from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, Sequence

from .damage_modifier_state import (
    ChosenDamageSource,
    DamageModifierDuration,
    DamageModifierError,
    DamagePreventionShield,
    DamageRedirectionEffect,
    DamageSubject,
    PreventionDamageKind,
    PreventionMode,
    PreventionRecipientKind,
)
from .replacement import (
    PreventAmount,
    PreventUsingShield,
    RedirectDamage,
    ReplacementClass,
    ReplacementEffect,
)
from .util import stable_json


class DamageModifierHost(Protocol):
    state: Any
    active_seats: Sequence[str]


@dataclass(frozen=True, slots=True)
class DamageModifierSnapshot:
    """Immutable durable-modifier view used to prepare sequential damage."""

    shields: tuple[DamagePreventionShield, ...]
    redirections: tuple[DamageRedirectionEffect, ...]


def damage_modifier_snapshot(
    host: DamageModifierHost,
) -> DamageModifierSnapshot:
    return DamageModifierSnapshot(
        shields=tuple(host.state.damage_prevention_shields),
        redirections=tuple(host.state.damage_redirections),
    )


def _shield_replacement_effect(
    shield: DamagePreventionShield,
) -> ReplacementEffect:
    conditions = shield.subject.event_conditions()
    scope_conditions = shield.scope.event_conditions(
        controller=shield.controller
    )
    for field, predicate in scope_conditions.items():
        if field in conditions and conditions[field] != predicate:
            raise DamageModifierError(
                "Prevention subject and scope predicates conflict"
            )
        conditions[field] = predicate
    if shield.damage_kind != PreventionDamageKind.ANY:
        conditions["combat"] = {
            "eq": shield.damage_kind == PreventionDamageKind.COMBAT
        }
    if shield.recipient_kind != PreventionRecipientKind.ANY:
        conditions["target_kind"] = {"eq": shield.recipient_kind.value}
    if shield.chosen_source is not None:
        conditions.update(shield.chosen_source.event_conditions())
    operation: PreventAmount | PreventUsingShield
    if shield.mode == PreventionMode.ALL:
        operation = PreventAmount()
    else:
        operation = PreventUsingShield(
            shield_id=shield.shield_id,
            remaining=shield.remaining,
            consume_on_application=True,
        )
    return ReplacementEffect(
        effect_id=shield.effect_id,
        source_id=shield.source_id,
        event_kind="damage",
        replacement_class=ReplacementClass.OTHER,
        conditions=conditions,
        operations=(operation,),
        label=shield.label or f"Prevent damage with {shield.source_id}",
    )


def _redirection_replacement_effect(
    redirection: DamageRedirectionEffect,
    destination: Any,
) -> ReplacementEffect:
    conditions = redirection.subject.event_conditions()
    if redirection.chosen_source is not None:
        conditions.update(redirection.chosen_source.event_conditions())
    return ReplacementEffect(
        effect_id=redirection.effect_id,
        source_id=redirection.source_id,
        event_kind="damage",
        replacement_class=ReplacementClass.OTHER,
        conditions=conditions,
        operations=(
            RedirectDamage(
                target=destination.ref,
                target_kind=destination.kind,
                target_controller=destination.controller,
                target_object_id=destination.object_id,
                target_logical_object_id=destination.logical_object_id,
                target_owner=destination.owner,
                target_types=destination.types,
                target_subtypes=destination.subtypes,
            ),
        ),
        label=(
            redirection.label
            or f"Redirect damage with {redirection.source_id}"
        ),
    )


def _subject_is_current(host: DamageModifierHost, subject: DamageSubject) -> bool:
    if subject.kind == "player":
        return subject.ref in host.active_seats
    if subject.kind == "any":
        return True
    assert subject.object_id is not None
    card = host.state.cards.get(subject.object_id)
    return bool(
        card is not None
        and card.zone == "battlefield"
        and card.logical_object_id == subject.logical_object_id
    )


def collect_damage_modifier_effects(
    host: DamageModifierHost,
    *,
    snapshot: DamageModifierSnapshot | None = None,
) -> tuple[ReplacementEffect, ...]:
    """Lower current durable modifiers without exposing mutable GameState."""

    from .damage import DamageError, recipient_snapshot

    current = snapshot or damage_modifier_snapshot(host)
    effects: list[ReplacementEffect] = []
    for shield in current.shields:
        if _subject_is_current(host, shield.subject):
            effects.append(_shield_replacement_effect(shield))
    for redirection in current.redirections:
        if not _subject_is_current(host, redirection.subject) or not _subject_is_current(
            host, redirection.destination
        ):
            continue
        try:
            destination = recipient_snapshot(
                host,
                redirection.destination.ref,
                actor=redirection.controller,
            )
        except DamageError:
            # CR 614.9: an invalid destination makes redirection do nothing.
            continue
        effects.append(_redirection_replacement_effect(redirection, destination))
    ids = [effect.effect_id for effect in effects]
    if len(ids) != len(set(ids)):
        raise DamageModifierError("Durable damage modifier IDs must be unique")
    return tuple(sorted(effects, key=lambda effect: effect.effect_id))


@dataclass(frozen=True, slots=True)
class DamageModifierCommitPlan:
    state_fingerprint: str = ""
    shield_remaining: tuple[tuple[str, int], ...] = ()
    remove_shields: tuple[str, ...] = ()
    remove_redirections: tuple[str, ...] = ()


def _modifier_state_fingerprint(snapshot: DamageModifierSnapshot) -> str:
    return stable_json(
        {
            "shields": [
                shield.to_dict() for shield in snapshot.shields
            ],
            "redirections": [
                effect.to_dict() for effect in snapshot.redirections
            ],
        }
    )


def plan_damage_modifier_commit(
    host: DamageModifierHost,
    events: Iterable[Any],
    *,
    snapshot: DamageModifierSnapshot | None = None,
) -> DamageModifierCommitPlan:
    current = snapshot or damage_modifier_snapshot(host)
    by_effect: dict[str, int] = {}
    applied: set[str] = set()
    for event in events:
        applied.update(str(value) for value in event.applied_effects)
        for effect_id, amount in dict(
            event.payload.get("prevention_applied") or {}
        ).items():
            by_effect[str(effect_id)] = by_effect.get(str(effect_id), 0) + int(
                amount
            )

    remaining: list[tuple[str, int]] = []
    remove_shields: list[str] = []
    for shield in current.shields:
        prevented = by_effect.get(shield.effect_id, 0)
        if prevented <= 0:
            continue
        if shield.mode == PreventionMode.AMOUNT:
            assert shield.remaining is not None
            after = shield.remaining - prevented
            if after < 0:
                raise DamageModifierError(
                    "Prepared damage over-consumed a prevention shield"
                )
            if after == 0:
                remove_shields.append(shield.shield_id)
            else:
                remaining.append((shield.shield_id, after))
        elif shield.mode == PreventionMode.NEXT_INSTANCE:
            remove_shields.append(shield.shield_id)

    remove_redirections = [
        effect.redirection_id
        for effect in current.redirections
        if effect.consume_on_application and effect.effect_id in applied
    ]
    return DamageModifierCommitPlan(
        state_fingerprint=_modifier_state_fingerprint(current),
        shield_remaining=tuple(sorted(remaining)),
        remove_shields=tuple(sorted(set(remove_shields))),
        remove_redirections=tuple(sorted(set(remove_redirections))),
    )


def commit_damage_modifier_plan(
    host: DamageModifierHost, plan: DamageModifierCommitPlan
) -> None:
    validate_damage_modifier_plan(host, plan)
    remaining = dict(plan.shield_remaining)
    remove_shields = set(plan.remove_shields)
    updated: list[DamagePreventionShield] = []
    for shield in host.state.damage_prevention_shields:
        if shield.shield_id in remove_shields:
            continue
        if shield.shield_id in remaining:
            updated.append(
                DamagePreventionShield(
                    shield_id=shield.shield_id,
                    source_id=shield.source_id,
                    controller=shield.controller,
                    subject=shield.subject,
                    mode=shield.mode,
                    remaining=remaining[shield.shield_id],
                    duration=shield.duration,
                    created_turn_sequence=shield.created_turn_sequence,
                    damage_kind=shield.damage_kind,
                    recipient_kind=shield.recipient_kind,
                    scope=shield.scope,
                    chosen_source=shield.chosen_source,
                    label=shield.label,
                    aftermath=shield.aftermath,
                    triggered_ability=shield.triggered_ability,
                )
            )
        else:
            updated.append(shield)
    host.state.damage_prevention_shields = updated
    removed_redirections = set(plan.remove_redirections)
    host.state.damage_redirections = [
        effect
        for effect in host.state.damage_redirections
        if effect.redirection_id not in removed_redirections
    ]


def validate_damage_modifier_plan(
    host: DamageModifierHost, plan: DamageModifierCommitPlan
) -> None:
    if (
        not plan.state_fingerprint
        or plan.state_fingerprint
        != _modifier_state_fingerprint(damage_modifier_snapshot(host))
    ):
        raise DamageModifierError(
            "Prepared damage modifier state no longer matches GameState"
        )
    remaining = dict(plan.shield_remaining)
    remove_shields = set(plan.remove_shields)
    current = {
        shield.shield_id: shield
        for shield in host.state.damage_prevention_shields
    }
    if not set(remaining).union(remove_shields).issubset(current):
        raise DamageModifierError(
            "Prepared prevention state no longer matches GameState"
        )
    for shield_id, after in remaining.items():
        shield = current[shield_id]
        if (
            shield.mode != PreventionMode.AMOUNT
            or shield.remaining is None
            or after < 1
            or after >= shield.remaining
        ):
            raise DamageModifierError(
                "Prepared prevention consumption is stale or invalid"
            )
    redirection_ids = {
        effect.redirection_id for effect in host.state.damage_redirections
    }
    if not set(plan.remove_redirections).issubset(redirection_ids):
        raise DamageModifierError(
            "Prepared redirection state no longer matches GameState"
        )


def project_damage_modifier_snapshot(
    snapshot: DamageModifierSnapshot,
    plan: DamageModifierCommitPlan,
) -> DamageModifierSnapshot:
    """Return the exact post-commit view without mutating authoritative state."""

    if plan.state_fingerprint != _modifier_state_fingerprint(snapshot):
        raise DamageModifierError(
            "Prepared damage modifier state does not match its projection base"
        )
    remaining = dict(plan.shield_remaining)
    removed_shields = set(plan.remove_shields)
    shields: list[DamagePreventionShield] = []
    for shield in snapshot.shields:
        if shield.shield_id in removed_shields:
            continue
        after = remaining.get(shield.shield_id)
        if after is None:
            shields.append(shield)
            continue
        shields.append(
            DamagePreventionShield(
                shield_id=shield.shield_id,
                source_id=shield.source_id,
                controller=shield.controller,
                subject=shield.subject,
                mode=shield.mode,
                remaining=after,
                duration=shield.duration,
                created_turn_sequence=shield.created_turn_sequence,
                damage_kind=shield.damage_kind,
                recipient_kind=shield.recipient_kind,
                scope=shield.scope,
                chosen_source=shield.chosen_source,
                label=shield.label,
                aftermath=shield.aftermath,
                triggered_ability=shield.triggered_ability,
            )
        )
    removed_redirections = set(plan.remove_redirections)
    return DamageModifierSnapshot(
        shields=tuple(shields),
        redirections=tuple(
            effect
            for effect in snapshot.redirections
            if effect.redirection_id not in removed_redirections
        ),
    )


def expire_end_of_turn_damage_modifiers(state: Any) -> tuple[str, ...]:
    removed = tuple(
        sorted(
            [
                shield.shield_id
                for shield in state.damage_prevention_shields
                if shield.duration == DamageModifierDuration.UNTIL_END_OF_TURN
            ]
            + [
                effect.redirection_id
                for effect in state.damage_redirections
                if effect.duration == DamageModifierDuration.UNTIL_END_OF_TURN
            ]
        )
    )
    state.damage_prevention_shields = [
        shield
        for shield in state.damage_prevention_shields
        if shield.duration != DamageModifierDuration.UNTIL_END_OF_TURN
    ]
    state.damage_redirections = [
        effect
        for effect in state.damage_redirections
        if effect.duration != DamageModifierDuration.UNTIL_END_OF_TURN
    ]
    return removed


__all__ = [
    "ChosenDamageSource",
    "DamageModifierCommitPlan",
    "DamageModifierDuration",
    "DamageModifierError",
    "DamagePreventionShield",
    "DamageRedirectionEffect",
    "DamageSubject",
    "PreventionMode",
    "collect_damage_modifier_effects",
    "commit_damage_modifier_plan",
    "expire_end_of_turn_damage_modifiers",
    "plan_damage_modifier_commit",
    "validate_damage_modifier_plan",
]
