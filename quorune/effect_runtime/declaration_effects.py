from __future__ import annotations

"""Resolution-created typed combat declaration rules."""

from typing import Any, Mapping
from dataclasses import replace
from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
from ..declaration_rule_effects import declaration_group_query_is_closed
from ..continuous_effect_state import create_resolution_declaration_rule_effect, ContinuousEffectStateError
from ..continuous_effect_model import ContinuousEffectError

from ..continuous_effect_state import resolution_effect_source
from ..effect_contracts import effect_family_contract
from ..errors import GameRuleError
from ..rules.temporary_declaration_restrictions import (
    commit_temporary_declaration_restriction,
    TemporaryDeclarationRestrictionError,
    temporary_declaration_restriction,
)


OPERATIONS = effect_family_contract("declaration-effects.v1").operations


def _apply_require_blocking_this_combat(host,effect,*,actor,operation,reason):
    from ..rules.blocking_requirements import resolve_blocking_requirement
    try:return resolve_blocking_requirement(host,effect,actor=actor,reason=reason)
    except (TypeError,ValueError) as exc:raise GameRuleError(str(exc)) from exc


def _apply_grant_declaration_restriction_until_end_of_turn(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> str | list[str]:
    del operation
    allowed = {"op", "card", "cards", "set", "restriction", "reason", "_runtime_source"}
    unknown = set(effect) - allowed
    if unknown:
        raise GameRuleError(
            "Temporary declaration restriction has unknown fields: "
            + ", ".join(sorted(unknown))
        )
    if sum(key in effect for key in ("card", "cards", "set")) != 1:
        raise GameRuleError("Declaration rule requires exactly one recipient mode")
    from ..temporary_all_blocker_model import TEMPORARY_ALL_BLOCKER_KIND, TemporaryAllBlockerSpec
    requirement = isinstance(effect.get('restriction'), Mapping) and effect['restriction'].get('kind') == TEMPORARY_ALL_BLOCKER_KIND
    if requirement:
        try:
            TemporaryAllBlockerSpec.from_dict(effect['restriction'])
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        if 'card' not in effect:
            raise GameRuleError('Resolved all-blocker requirements need one locked subject')
        if effect['card'] is None:
            return []
    from ..source_block_pair_model import SOURCE_BLOCK_PAIR_KIND
    if isinstance(effect.get('restriction'), Mapping) and effect['restriction'].get('kind') == SOURCE_BLOCK_PAIR_KIND:
        from ..rules.source_block_pairs import apply_source_block_pair
        return apply_source_block_pair(host, effect, actor=actor, reason=reason)
    if "cards" in effect or "set" in effect:
        query = None
        try:
            if "cards" in effect:
                refs = effect["cards"]
                if not isinstance(refs, list) or len(refs) > 6 or any(type(ref) is not str or not ref for ref in refs) or len(set(refs)) != len(refs):
                    raise GameRuleError("Declaration target group is malformed")
                cards = tuple(host._resolve_object(actor, ref, zones={"battlefield"}) for ref in refs)
                if any(card.phased_out for card in cards):
                    raise GameRuleError("Declaration target group includes phased-out objects")
            else:
                spec = AffectedPermanentSetSpec.from_dict(effect["set"])
                if not declaration_group_query_is_closed(spec.query) or spec.exclude_source or spec.controller_relation not in {
                    PermanentControllerRelation.ANY, PermanentControllerRelation.ACTOR, PermanentControllerRelation.OPPONENTS,
                }:
                    raise GameRuleError("Live declaration recipient set is unsupported")
                query = spec.query
                if spec.controller_relation is PermanentControllerRelation.ACTOR:
                    query = replace(query, controller=actor)
                elif spec.controller_relation is PermanentControllerRelation.OPPONENTS:
                    query = replace(query, excluded_controllers=(actor,))
                cards = ()
                refs = []
            rule = create_resolution_declaration_rule_effect(
                host, source=resolution_effect_source(host, effect, fallback_card=cards[0] if cards else None),
                targets=cards, restriction=temporary_declaration_restriction(str(effect.get("restriction") or "")), live_query=query,
            )
        except (TypeError, ValueError, ContinuousEffectError, ContinuousEffectStateError) as exc:
            raise GameRuleError(str(exc)) from exc
        if rule is not None:
            host._log(actor, "combat.declaration_restriction", reason,
                {"objects": refs, "live_query": query.to_dict() if query is not None else None,
                 "restriction": effect["restriction"], "continuous_effect": rule.effect_id},
                importance=1, changed_objects=[card.object_id for card in cards])
        return refs
    card = host._resolve_object(
        actor,
        str(effect.get("card") or ""),
        zones={"battlefield"},
    )
    try:
        committed = commit_temporary_declaration_restriction(
            host,
            card=card,
            source=resolution_effect_source(
                host,
                effect,
                fallback_card=card,
            ),
            kind=effect['restriction'] if isinstance(effect.get('restriction'), Mapping) else str(effect.get("restriction") or ""),
        )
    except TemporaryDeclarationRestrictionError as exc:
        raise GameRuleError(str(exc)) from exc
    host._log(
        actor,
        "combat.declaration_restriction",
        f"{card.ref} is subject to a combat declaration restriction until end of turn.",
        {
            "object": card.ref,
            "restriction": effect['restriction'] if isinstance(effect.get('restriction'), Mapping) else str(effect.get("restriction") or ""),
            "continuous_effect": committed.effect_id,
            "reason": reason,
        },
        importance=1,
        changed_objects=[card.object_id],
    )
    return card.ref


HANDLERS = {
    'require_blocking_this_combat':_apply_require_blocking_this_combat,
    "grant_declaration_restriction_until_end_of_turn": (
        _apply_grant_declaration_restriction_until_end_of_turn
    ),
}


def apply_effect(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    handler = HANDLERS.get(operation)
    if handler is None:
        raise GameRuleError(f"Unsupported owned effect {operation!r}")
    return handler(
        host,
        effect,
        actor=actor,
        operation=operation,
        reason=reason,
    )


__all__ = ["apply_effect", "HANDLERS", "OPERATIONS"]
