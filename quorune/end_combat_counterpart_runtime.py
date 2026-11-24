"""Bind the resolving trigger's live counterparts to simultaneous operations."""

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from .combat import current_block_counterpart_ids
from .continuous_effect_model import ContinuousObjectIdentity
from .end_combat_counterpart_model import (
    END_COMBAT_COUNTERPART_FIELD, END_COMBAT_COUNTERPART_TEMPLATE,
    EndCombatCounterpartResult,
    EndCombatCounterpartSpec,
    end_combat_counterpart_condition,
)
from .replacement.immutable import FrozenMap
from .rules.effect_shape_walkers import contains_nested_key


class EndCombatCounterpartHost(Protocol):
    state: Any
    def _effective_type_parts(self, card: Any) -> tuple[set[str], set[str], set[str]]: ...


def current_end_combat_counterpart_identities(
    state: Any,
    source: ContinuousObjectIdentity,
    *,
    is_current_creature: Callable[[Any], bool],
) -> tuple[ContinuousObjectIdentity, ...]:
    """Read both combat directions for the original source still in combat."""
    if state.phase != "combat" or state.step != "end_combat":
        return ()
    current = state.cards.get(source.object_id)
    if (current is None or current.zone != "battlefield" or current.phased_out
            or current.logical_object_id != source.logical_object_id or not is_current_creature(current)):
        return ()
    return tuple(
        ContinuousObjectIdentity(card.object_id, card.logical_object_id)
        for object_id in current_block_counterpart_ids(
            state.combat.blockers, source.object_id, live_attackers=state.combat.attackers,
        )
        if (card := state.cards.get(object_id)) is not None
        and card.zone == "battlefield" and not card.phased_out and is_current_creature(card)
    )


def end_combat_counterpart_runtime_dependencies(program: Any) -> tuple[str, ...]:
    values = (*program.effects, *program.handlers)
    if not contains_nested_key(values, END_COMBAT_COUNTERPART_FIELD):
        return ()
    if (
        (program.event, program.active_zone) != ("step.begin", "battlefield")
        or program.provenance.get("template_id") != END_COMBAT_COUNTERPART_TEMPLATE
        or FrozenMap(program.event_condition or {}) != FrozenMap(end_combat_counterpart_condition())
        or program.target_schema is not None or program.cost_schema is not None
        or len(program.effects) != 1 or program.handlers
    ):
        raise ValueError("Live combat counterparts require one untargeted end-of-combat trigger")
    return EndCombatCounterpartSpec.from_effect(program.effects[0]).capabilities


def bind_end_combat_counterpart_result(
    host: EndCombatCounterpartHost,
    item: Any,
    effect: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Seal recipients from the original source before the common replacement path."""
    if (
        item.kind != "triggered_ability" or item.context.get("event") != "step.begin"
        or item.context.get("phase") != "combat" or item.context.get("step") != "end_combat"
    ):
        raise ValueError("Combat counterparts require the committed end-of-combat occurrence")
    source = ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))
    spec = EndCombatCounterpartSpec.from_effect(effect)

    def is_current_combat_creature(card: Any) -> bool:
        card_types, _, _ = host._effective_type_parts(card)
        return "creature" in card_types and "battle" not in card_types

    counterparts = current_end_combat_counterpart_identities(
        host.state, source, is_current_creature=is_current_combat_creature,
    )
    if not counterparts:
        return None

    refs = tuple(host.state.cards[identity.object_id].ref for identity in counterparts)
    bound = {"op": spec.operation, "cards": refs, "maximum_targets": len(refs)}
    if spec.result is EndCombatCounterpartResult.PLACE_MINUS_ZERO_MINUS_TWO_COUNTER:
        bound.update(counter="-0/-2", amount=1, source=host.state.cards[source.object_id].ref)
    return bound
