from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from .ability_fragments import CombatKeywordTriggerKind
from .attack_transition_model import (
    AttackKeywordTriggerOccurrence,
    AttackTransitionError,
)
from .continuous_effect_model import (
    ContinuousEffectDuration,
    ContinuousOperation,
    Layer,
)
from .continuous_effect_state import (
    ResolutionEffectSource,
    create_resolution_continuous_effect,
)
from .created_token_cleanup import schedule_created_token_cleanup
from .created_token_cleanup_model import CreatedTokenCleanupSpec
from .model import StackItem


ATTACK_KEYWORD_TRIGGER_SEMANTIC_KEY = "builtin:attack-keyword-trigger"


def _identity(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise AttackTransitionError(f"{field} must be a nonempty string")
    return value


def attack_keyword_trigger_stack_item(
    occurrence: AttackKeywordTriggerOccurrence,
    *,
    ref: str,
    stack_id: str,
    visibility: Sequence[str],
) -> StackItem:
    """Project one immutable occurrence onto the public stack."""

    if not isinstance(occurrence, AttackKeywordTriggerOccurrence):
        raise AttackTransitionError(
            "An attack-trigger stack item requires a typed occurrence"
        )
    _identity(ref, field="Attack-trigger stack reference")
    _identity(stack_id, field="Attack-trigger stack identity")
    return StackItem(
        stack_id=stack_id,
        ref=ref,
        kind="triggered_ability",
        controller=occurrence.controller,
        label=occurrence.label,
        source_object_id=occurrence.source.object_id,
        semantic_key=ATTACK_KEYWORD_TRIGGER_SEMANTIC_KEY,
        visibility=list(visibility),
        context={
            "event": "combat.attack_transition",
            "attack_keyword_trigger": occurrence.to_dict(),
        },
        referred_object_ids=list(
            dict.fromkeys(
                (
                    occurrence.source.object_id,
                    *(value.object_id for value in occurrence.affected),
                )
            )
        ),
    )


class AttackKeywordResolutionHost(Protocol):
    state: Any

    def _log(self, *args: Any, **kwargs: Any) -> None: ...


def resolve_attack_keyword_trigger(
    host: AttackKeywordResolutionHost,
    occurrence: AttackKeywordTriggerOccurrence,
    *,
    stack_ref: str,
) -> tuple[str, ...]:
    """Resolve one typed attack trigger for its recorded incarnations."""

    if not isinstance(occurrence, AttackKeywordTriggerOccurrence):
        raise AttackTransitionError(
            "Attack-trigger resolution requires a typed occurrence"
        )
    _identity(stack_ref, field="Resolving attack-trigger stack reference")
    from .battle_cry_resolution import battle_cry_recipient_identities
    identities = (battle_cry_recipient_identities(host.state, occurrence)
        if occurrence.kind is CombatKeywordTriggerKind.BATTLE_CRY else occurrence.affected)
    targets = []
    for identity in identities:
        card = host.state.cards.get(identity.object_id)
        if (
            card is None
            or card.zone != "battlefield"
            or card.logical_object_id != identity.logical_object_id
        ):
            continue
        if (
            occurrence.kind is CombatKeywordTriggerKind.BATTLE_CRY
            and identity.object_id not in host.state.combat.attackers
        ):
            continue
        targets.append(card)
    source = ResolutionEffectSource(
        stack_ref=stack_ref,
        object_id=occurrence.source.object_id,
        logical_object_id=occurrence.source.logical_object_id,
        card_ref=occurrence.source.reference,
    )
    if occurrence.kind is CombatKeywordTriggerKind.DECAYED:
        schedule_created_token_cleanup(
            host,
            occurrence.controller,
            targets,
            CreatedTokenCleanupSpec("sacrifice", "end_of_combat"),
            label=occurrence.label,
            source=source,
        )
    elif targets and (occurrence.power_delta or occurrence.toughness_delta):
        effect = create_resolution_continuous_effect(
            host,
            source=source,
            targets=tuple(targets),
            layer=Layer.POWER_TOUGHNESS,
            sublayer="7c",
            operations=(
                ContinuousOperation(
                    "modify_power_toughness",
                    [occurrence.power_delta, occurrence.toughness_delta],
                ),
            ),
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
        )
        if effect is None:
            raise AttackTransitionError(
                "Attack-trigger resolution requires the continuous-effect journal"
            )
    applied = tuple(card.object_id for card in targets)
    host._log(
        occurrence.controller,
        "combat.attack_keyword.resolve",
        f"Resolved {occurrence.label} for {len(applied)} object(s).",
        {
            "occurrence": occurrence.occurrence_id,
            "transition": occurrence.transition_id,
            "kind": occurrence.kind.value,
            "source": occurrence.source.reference,
            "affected": [value.reference for value in occurrence.affected],
            "amount": occurrence.amount,
            "applied": list(applied),
        },
        importance=2,
        changed_objects=list(applied),
    )
    return applied


__all__ = [
    "ATTACK_KEYWORD_TRIGGER_SEMANTIC_KEY",
    "AttackKeywordResolutionHost",
    "attack_keyword_trigger_stack_item",
    "resolve_attack_keyword_trigger",
]
