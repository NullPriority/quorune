from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..damage_source import REPRESENTED_DAMAGE_SOURCE_ZONES
from ..object_query import (
    ObjectQueryError,
    ObjectQuerySpec,
    query_objects,
    validate_chosen_damage_source_predicate,
)
from ..replacement.immutable import FrozenMap, thaw_value
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    ObjectChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


_LEGACY_FILTER_FIELDS = frozenset(
    {
        "required_colors",
        "allowed_colors",
        "required_types",
        "required_subtypes",
        "required_supertypes",
        "required_keywords",
    }
)


def _source_predicate(effect: Mapping[str, Any]) -> ObjectQuerySpec:
    raw = effect.get("source_predicate")
    legacy_present = _LEGACY_FILTER_FIELDS.intersection(effect)
    if raw is not None and legacy_present:
        raise SemanticChoiceError(
            "Chosen-source predicates cannot mix canonical and legacy filters"
        )
    try:
        if raw is not None:
            predicate = ObjectQuerySpec.from_dict(thaw_value(raw))
        else:
            predicate = ObjectQuerySpec(
                zones=REPRESENTED_DAMAGE_SOURCE_ZONES,
                colors_all=effect.get("required_colors", ()),
                colors_any=effect.get("allowed_colors", ()),
                types_all=effect.get("required_types", ()),
                subtypes_all=effect.get("required_subtypes", ()),
                supertypes_all=effect.get("required_supertypes", ()),
                keywords_all=effect.get("required_keywords", ()),
                known_to_actor=True,
            )
    except ObjectQueryError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    try:
        return validate_chosen_damage_source_predicate(predicate)
    except ObjectQueryError as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _candidates(
    query: SemanticChoiceQuery,
    *,
    predicate: ObjectQuerySpec,
) -> tuple[Any, ...]:
    rows = query_objects(query.objects(zones=predicate.zones), predicate)
    legal_refs = frozenset(query.damage_source_candidate_refs())
    if not query.damage_source_candidates_are_complete:
        # Compatibility for manually constructed/query-v1 snapshots. Live
        # engine snapshots always materialize the full candidate provenance.
        legal_refs = frozenset(
            row.ref
            for row in rows
            if row.zone in {"battlefield", "command", "stack"}
        )
    return tuple(
        sorted(
            (
                row
                for row in rows
                if row.ref in legal_refs
            ),
            key=lambda row: row.ref,
        )
    )


@dataclass(frozen=True, slots=True)
class ChooseDamageSourceHandler:
    operation: str = "choose_damage_source"
    handler_id: str = "choice.damage.choose-source.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 609.7a", "CR 615.1")
    capability_dependencies: tuple[str, ...] = (
        "damage.prevention.persistent_amount",
    )
    continuation_fields: tuple[str, ...] = (
        "shield",
        "source_predicate",
        "_legal_refs",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "objects",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "PreventionShieldCreationRequest",
        "commit_prevention_shield_creation",
    )
    replay_fixture: str = "damage-prevention-chosen-source"
    test_modules: tuple[str, ...] = (
        "tests.test_damage_prevention_creation",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        shield = effect.get("shield")
        if not isinstance(shield, Mapping) or shield.get("op") != (
            "create_damage_prevention_shield"
        ):
            raise SemanticChoiceError(
                "A chosen source must continue into a prevention shield"
            )
        predicate = _source_predicate(effect)
        from ..attached_prevention_model import BOUND_SUBJECT_VERSION, validate_bound_prevention_version
        if BOUND_SUBJECT_VERSION in shield:
            validate_bound_prevention_version(shield[BOUND_SUBJECT_VERSION])
            if shield.get('subject') is None:
                return SemanticChoicePreparation(request=None,
                    continuation_effect=FrozenMap({**effect,'source_predicate':predicate.to_dict(),'_legal_refs':[]}),
                    auto_continue=AutoContinue(reason='The attached recipient is unavailable'))
        candidates = _candidates(
            context.query,
            predicate=predicate,
        )
        if not candidates:
            raise SemanticChoiceError(
                "No legally known damage source is available to choose"
            )
        legal_refs = tuple(row.ref for row in candidates)
        canonical_effect = {
            key: thaw_value(value)
            for key, value in effect.items()
            if key not in _LEGACY_FILTER_FIELDS
            and key != "source_predicate"
        }
        continuation = FrozenMap(
            {
                **canonical_effect,
                "source_predicate": predicate.to_dict(),
                "_legal_refs": list(legal_refs),
            }
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=str(effect.get("prompt") or "Choose a damage source."),
                choice=ObjectChoice(
                    field_name="source",
                    legal_refs=legal_refs,
                    zones=REPRESENTED_DAMAGE_SOURCE_ZONES,
                    visibility="public",
                    predicates=FrozenMap(
                        predicate.to_dict()
                    ),
                ),
                public_context=FrozenMap(
                    {
                        "operation": self.operation,
                        "objects": [
                            {
                                "id": row.ref,
                                "name": row.printed_name,
                                "zone": row.zone,
                            }
                            for row in candidates
                        ],
                    }
                ),
            ),
            continuation_effect=continuation,
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        selected = response.get("source")
        if not isinstance(selected, str) or not selected:
            raise SemanticChoiceError("A damage source selection is required")
        legal_refs = tuple(
            str(value) for value in continuation.effect.get("_legal_refs", ())
        )
        if selected not in legal_refs:
            raise SemanticChoiceError(
                "The selected damage source was not offered to this seat"
            )
        predicate = _source_predicate(continuation.effect)
        current = {
            row.ref: row
            for row in _candidates(
                query,
                predicate=predicate,
            )
        }
        if selected not in current:
            raise SemanticChoiceError(
                "The selected damage source is no longer a legal candidate"
            )
        shield = continuation.effect.get("shield")
        if not isinstance(shield, Mapping):
            raise SemanticChoiceError(
                "The chosen-source continuation lost its shield"
            )
        return SemanticChoiceCompletion(
            prepend_effects=(
                FrozenMap(
                    {
                        **thaw_value(shield),
                        "chosen_source": selected,
                        "source_predicate": predicate.to_dict(),
                    }
                ),
            )
        )


DAMAGE_PREVENTION_CHOICE_HANDLERS = (ChooseDamageSourceHandler(),)


__all__ = [
    "ChooseDamageSourceHandler",
    "DAMAGE_PREVENTION_CHOICE_HANDLERS",
]
