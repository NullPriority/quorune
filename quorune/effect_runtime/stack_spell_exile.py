"""Apply closed stack-spell exile and plot effects during resolution."""
from __future__ import annotations

from ..continuous_effect_state import ContinuousEffectStateError, ResolutionEffectSource
from ..errors import GameRuleError
from ..public_zone_moves import STACK_SPELL_EXILE_FIELD, StackSpellExileScope, StackSpellExileSpec


def apply_stack_spell_exile(host, effect, *, actor, reason):
    from ..zone_transitions import ZoneTransitionOwner

    try:
        spec = StackSpellExileSpec.from_dict(effect[STACK_SPELL_EXILE_FIELD])
        source_ref = ResolutionEffectSource.from_effect(effect).stack_ref
    except (ValueError, TypeError, ContinuousEffectStateError) as exc:
        raise GameRuleError(str(exc)) from exc
    plots = spec.scope is StackSpellExileScope.TARGET_PLOT
    selected = spec.scope in {StackSpellExileScope.SELECTED, StackSpellExileScope.TARGET_PLOT}
    allowed = {"op", STACK_SPELL_EXILE_FIELD, "_runtime_source", "reason", "_replacement_selections"}
    if selected:
        allowed.add("stacks")
    if set(effect) - allowed or effect.get("op") != "exile":
        raise GameRuleError("Stack exile effect fields are closed")
    stack = {item.ref: item for item in host.state.stack}
    source = stack.get(source_ref)
    source_kinds = {"triggered_ability"} if plots else {"spell", "spell_copy"}
    if (source is None or source.kind not in source_kinds or source.controller != actor
            or source.context.get("currently_resolving") is not True):
        raise GameRuleError("Stack exile requires its current resolving source")
    spell_kinds = {"spell", "spell_copy"}
    if selected:
        refs = effect.get("stacks")
        if (not isinstance(refs, (list, tuple)) or any(type(ref) is not str for ref in refs)
                or len(set(refs)) != len(refs) or source_ref in refs or (plots and len(refs) > 1)):
            raise GameRuleError("Stack exile requires distinct other spell targets")
        spells = [stack[ref] for ref in refs if ref in stack]
        if any(item.kind not in spell_kinds for item in spells):
            raise GameRuleError("Stack exile targets must be spells")
        abilities = ()
    else:
        spells = [item for item in stack.values() if item.ref != source_ref and item.kind in spell_kinds]
        abilities = tuple(item.ref for item in stack.values() if item.kind in {"activated_ability", "triggered_ability"})
    moves = []
    for item in spells:
        card = host.state.cards.get(item.card_object_id)
        if card is None or card.zone != "stack":
            raise GameRuleError("An exiled spell is missing its current stack object")
        moves.append((card.object_id, "exile"))
    with host.transaction():
        moved = ZoneTransitionOwner(host).move_cards_simultaneously(
            moves, reason=reason, log=False, replacement_selections=tuple(effect.get("_replacement_selections") or ()),
        )
        if plots:
            from ..cast_lifecycles import externally_plotted_cast_spec
            from ..zone_object_state import mark_fixed_zone_cast_designation

            exiled = {object_id for object_id, _ in moves}
            for card in moved:
                if (card.object_id in exiled and card.zone == "exile" and card.object_kind == "card"
                        and host.state.cards.get(card.object_id) is card):
                    mark_fixed_zone_cast_designation(
                        card, spec=externally_plotted_cast_spec(),
                        turn_sequence=host.state.turn_sequence, viewers=host.seats,
                    )
        for ref in abilities:
            host._counter_stack_item(ref, reason=reason, countered_by=actor)
    return moved
