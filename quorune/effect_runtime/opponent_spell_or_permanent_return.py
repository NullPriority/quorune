"""Adapt the revalidated target to the canonical owner-hand transition."""

from ..continuous_effect_state import ContinuousEffectStateError, ResolutionEffectSource
from ..errors import GameRuleError
from ..rules.opponent_spell_or_permanent_return import (
    COPY_RETURN_FIELD,
    ENTRY_FIELD,
    FIELD,
    OPPOSING_SPELL_FIELD,
    OWNER_HAND_RETURN_FIELDS,
    SPELL_FIELD,
    opponent_spell_or_permanent_return_runtime_dependencies,
)
from ..zone_transition_model import ZoneTransitionKind
from .linked_zone_movement import commit_resolved_zone_movement


def _target_card(host, ref, actor, *, opponent_only, spell_only=False, stack_only=False):
    target_item = next((item for item in host.state.stack if item.ref == ref), None)
    if target_item is not None:
        card = host.state.cards.get(target_item.card_object_id)
        if (target_item.kind not in {"spell", "spell_copy"} or opponent_only and target_item.controller == actor
                or card is None or card.zone != "stack"):
            raise GameRuleError("Owner-hand stack movement needs its current legal spell card")
        return card
    if spell_only:
        raise GameRuleError("Owner-hand stack movement requires its revalidated spell")
    if stack_only:
        raise GameRuleError("Copy and return needs the original live stack object")
    card = host._resolve_object(actor, ref, zones={"battlefield"})
    types, _, _ = host._type_parts(host._effective_card_data(card).get("type_line", ""))
    if card.ref != ref or card.phased_out or opponent_only and (card.controller == actor or "land" in types):
        raise GameRuleError("Owner-hand battlefield movement needs its current opposing nonland permanent")
    return card


def apply_opponent_spell_or_permanent_return(host, effect, *, actor, reason):
    variants = OWNER_HAND_RETURN_FIELDS.intersection(effect)
    if len(variants) != 1:
        raise GameRuleError("Owner-hand return requires exactly one move scope")
    field, = variants
    from_entry = field == ENTRY_FIELD
    from_cast = field == OPPOSING_SPELL_FIELD
    after_copy = field == COPY_RETURN_FIELD
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
    kinds = {"triggered_ability"} if from_entry or from_cast else {"spell", "spell_copy"}
    if (item is None or item.kind not in kinds or item.controller != actor
            or from_entry and item.context.get("event") != "permanent.enter"
            or from_cast and item.context.get("event") != "spell.cast"
            or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True
            or type(ref) is not str or ref == source.stack_ref or tuple(item.targets) != (ref,)):
        raise GameRuleError("Opponent spell-or-permanent return lost its revalidated target")
    try:
        program = host.semantics.get(item.semantic_key)
        if program is None or not opponent_spell_or_permanent_return_runtime_dependencies(program):
            raise ValueError("Owner-hand return lost its original program")
        if from_cast:
            modes = program.target_schema["modes"]
            if len(item.modes) != 1 or OPPOSING_SPELL_FIELD not in modes[item.modes[0]]["effects"][0]:
                raise ValueError("Owner-hand spell return lost its chosen mode")
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    card = _target_card(host, ref, actor, opponent_only=field in {FIELD, OPPOSING_SPELL_FIELD},
                        spell_only=field in {SPELL_FIELD, OPPOSING_SPELL_FIELD}, stack_only=after_copy)
    return commit_resolved_zone_movement(host, card, effect, destination="hand", reason=reason,
                                        transition_kind=ZoneTransitionKind.ORDINARY)
