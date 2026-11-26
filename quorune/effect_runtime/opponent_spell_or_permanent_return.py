"""Adapt the revalidated target to the canonical owner-hand transition."""

from ..continuous_effect_state import ContinuousEffectStateError, ResolutionEffectSource
from ..errors import GameRuleError
from ..rules.opponent_spell_or_permanent_return import (
    ENTRY_FIELD, FIELD, opponent_spell_or_permanent_return_runtime_dependencies,
)
from ..zone_transition_model import ZoneTransitionKind
from .linked_zone_movement import commit_resolved_zone_movement


def _target_card(host, ref, actor, *, opponent_only):
    target_item = next((item for item in host.state.stack if item.ref == ref), None)
    if target_item is not None:
        card = host.state.cards.get(target_item.card_object_id)
        if (target_item.kind not in {"spell", "spell_copy"} or opponent_only and target_item.controller == actor
                or card is None or card.zone != "stack"):
            raise GameRuleError("Owner-hand stack movement needs its current opposing spell card")
        return card
    card = host._resolve_object(actor, ref, zones={"battlefield"})
    types, _, _ = host._type_parts(host._effective_card_data(card).get("type_line", ""))
    if card.ref != ref or card.phased_out or opponent_only and (card.controller == actor or "land" in types):
        raise GameRuleError("Owner-hand battlefield movement needs its current opposing nonland permanent")
    return card


def apply_opponent_spell_or_permanent_return(host, effect, *, actor, reason):
    from_entry = ENTRY_FIELD in effect
    field = ENTRY_FIELD if from_entry else FIELD
    fields = {"op", "card", "destination", field}
    if (set(effect) - {"reason", "_runtime_source", "_replacement_selections"} != fields
            or type(effect[field]) is not int or effect[field] != 1 or effect["destination"] != "hand"):
        raise GameRuleError("Opponent spell-or-permanent return has closed move fields")
    try:
        source = ResolutionEffectSource.from_effect(effect)
    except (ValueError, ContinuousEffectStateError) as exc:
        raise GameRuleError(str(exc)) from exc
    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    ref = effect["card"]
    kinds = {"triggered_ability"} if from_entry else {"spell", "spell_copy"}
    if (item is None or item.kind not in kinds or item.controller != actor
            or from_entry and item.context.get("event") != "permanent.enter"
            or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True
            or type(ref) is not str or ref == source.stack_ref or tuple(item.targets) != (ref,)):
        raise GameRuleError("Opponent spell-or-permanent return lost its revalidated target")
    try:
        program = host.semantics.get(item.semantic_key)
        if program is None or not opponent_spell_or_permanent_return_runtime_dependencies(program):
            raise ValueError("Opponent spell-or-permanent return lost its original program")
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    card = _target_card(host, ref, actor, opponent_only=not from_entry)
    return commit_resolved_zone_movement(host, card, effect, destination="hand", reason=reason,
                                        transition_kind=ZoneTransitionKind.ORDINARY)
