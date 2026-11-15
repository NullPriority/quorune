from __future__ import annotations

"""Choose a private incarnation-pinned set, then resume one atomic entry effect."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..object_query import object_matches_query
from ..private_hand_entry_model import (
    PRIVATE_HAND_COLLECTION_CAPABILITY, PRIVATE_HAND_COLLECTION_OPERATION,
    SELECTED_HAND_ENTRY_OPERATION, decode_private_hand_collection,
)
from ..replacement.immutable import FrozenMap
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceContinuation,
    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


@dataclass(frozen=True, slots=True)
class PrivateHandCollectionHandler:
    operation: str = PRIVATE_HAND_COLLECTION_OPERATION
    handler_id: str = "choice.object.private-hand-collection.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("400.7", "603.6a", "608.2c", "608.2d")
    capability_dependencies: tuple[str, ...] = (PRIVATE_HAND_COLLECTION_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "selection", "_choice_actor", "_legal_identities")
    private_data: tuple[str, ...] = ("actor hand", "eligible hand identities")
    projected_fields: tuple[str, ...] = ("prompt", "objects", "legal_actions.choice_schema.legal_refs")
    mutation_path: tuple[str, ...] = ("sealed selected hand-entry effect", "ZoneTransitionOwner.move_cards_simultaneously")
    replay_fixture: str = "private-hand-collection"
    test_modules: tuple[str, ...] = ("tests.test_private_hand_collections",)

    def prepare(self, effect: Mapping[str, Any], context: SemanticChoiceContext) -> SemanticChoicePreparation:
        try:
            spec = decode_private_hand_collection(effect, actor=context.actor)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        rows = tuple(sorted(
            (row for row in context.query.objects(zones=("hand",), owner=context.actor)
             if object_matches_query(row, spec.query)), key=lambda row: row.ref,
        ))
        if not rows:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="no matching hand cards"))
        maximum = len(rows) if spec.maximum is None else min(spec.maximum, len(rows))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose cards from your hand to put onto the battlefield together.",
                choice=ObjectChoice(field_name="cards", legal_refs=tuple(row.ref for row in rows),
                    zones=("hand",), minimum=0, maximum=maximum, optional=True,
                    visibility="actor_private", owner_relation="actor", predicates=FrozenMap(spec.query.canonical_dict())),
                public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation,
                    "objects": [{"id": row.ref, "name": row.printed_name} for row in rows]}),
            ),
            continuation_effect=FrozenMap({**dict(effect), "_choice_actor": context.actor,
                "_legal_identities": {row.ref: row.logical_object_id for row in rows}}),
        )

    def complete(self, continuation: SemanticChoiceContinuation, response: Mapping[str, Any],
                 query: SemanticChoiceQuery) -> SemanticChoiceCompletion:
        effect = continuation.effect
        actor = effect.get("_choice_actor")
        if type(actor) is not str or not actor:
            raise SemanticChoiceError("Private hand collection actor is missing")
        try:
            spec = decode_private_hand_collection(
                {key: effect[key] for key in ("op", "player", "selection")}, actor=actor)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        selected = response.get("cards", ())
        identities = effect.get("_legal_identities")
        if (not isinstance(selected, (list, tuple)) or isinstance(selected, str)
                or any(type(ref) is not str or not ref for ref in selected)
                or len(selected) != len(set(selected))
                or spec.maximum is not None and len(selected) > spec.maximum
                or not isinstance(identities, Mapping)
                or any(type(ref) is not str or not ref or type(identity) is not str or not identity
                       for ref, identity in identities.items())
                or any(ref not in identities for ref in selected)):
            raise SemanticChoiceError("Selected hand collection is outside its authoritative choices")
        for ref in selected:
            row = query.object(ref, zones=("hand",))
            if (row is None or row.owner != actor or row.logical_object_id != identities[ref]
                    or not object_matches_query(row, spec.query)):
                raise SemanticChoiceError("Selected hand card changed its incarnation or predicate")
        if not selected:
            return SemanticChoiceCompletion()
        return SemanticChoiceCompletion(prepend_effects=(FrozenMap({
            "op": SELECTED_HAND_ENTRY_OPERATION, "player": actor, "selection": spec.to_dict(),
            "selected": {ref: identities[ref] for ref in sorted(selected)},
        }),))
