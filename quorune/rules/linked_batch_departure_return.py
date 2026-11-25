"""Closed exile scopes for separately printed entry and departure abilities."""

from __future__ import annotations

from ..compiler.direct_target import DirectPermanentTargetSpec
from ..continuous_effect_state import matching_battlefield_objects
from ..errors import GameRuleError
from ..linked_exile_return_model import LinkedBatchDepartureScope
from ..object_predicate import ObjectQuerySpec


def linked_batch_departure_target_schema(scope: LinkedBatchDepartureScope):
    if scope is not LinkedBatchDepartureScope.TWO_TARGET_LANDS:
        return None
    return {**DirectPermanentTargetSpec(types_any=("land",)).to_target_schema(), "count": 2}


def linked_batch_departure_query(scope: LinkedBatchDepartureScope, *, controller: str):
    if scope is LinkedBatchDepartureScope.ALL_LANDS:
        return ObjectQuerySpec(zones=("battlefield",), types_all=("land",))
    if scope is LinkedBatchDepartureScope.OTHER_CONTROLLED_PERMANENTS:
        return ObjectQuerySpec(zones=("battlefield",), controller=controller)
    return None


def select_linked_batch_departure_cards(host, item, source, scope, refs, *, actor):
    if not isinstance(refs, (tuple, list)) or any(type(ref) is not str or not ref for ref in refs):
        raise GameRuleError("A linked batch requires normalized public references")
    if scope is LinkedBatchDepartureScope.TWO_TARGET_LANDS:
        if (item.context.get("targets_revalidated") is not True
                or tuple(refs) != tuple(ref for ref in item.targets if ref is not None)
                or not 1 <= len(refs) <= 2 or len(refs) != len(set(refs))):
            raise GameRuleError("A linked land batch requires its surviving announced targets")
        cards = tuple(host._resolve_object(actor, ref, zones={"battlefield"}) for ref in refs)
        if any(card.phased_out or "land" not in host._effective_type_parts(card)[0] for card in cards):
            raise GameRuleError("A linked land batch lost a revalidated land target")
        return cards
    if refs:
        raise GameRuleError("An untargeted linked batch cannot contain announced references")
    query = linked_batch_departure_query(scope, controller=actor)
    candidates = matching_battlefield_objects(host, query)
    return tuple(card for card in candidates if not card.phased_out and (
        scope is not LinkedBatchDepartureScope.OTHER_CONTROLLED_PERMANENTS
        or card.logical_object_id != source.logical_object_id
    ))
