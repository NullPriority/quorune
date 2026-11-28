"""Retain a private qualifying search and the selected card's immediate cast."""

from dataclasses import dataclass

from ..discover_model import discover_options_fingerprint
from ..library_order_model import fixed_shuffle_effect
from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..rules.qualified_library_cast import (
    CAPABILITY, OPERATION, QualifiedLibraryCastSpec, ResolveQualifiedLibraryCastIntent, chosen_library_cast,
)
from ..rules.resolving_cast_payments import resolving_price_requires_cast
from .bounded_hand_cast import _submitted_cast
from .model import (
    AutoContinue, CastOrDeclineChoice, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_FINGERPRINT = "_options_fingerprint"


def _search_base(effect, actor, stack):
    if set(effect) != {"op", "player", "search_cast", "phase", "stack"} or effect["stack"] != stack:
        raise SemanticChoiceError("The private search lost its originating activation")
    return QualifiedLibraryCastSpec.from_effect(
        {key: effect[key] for key in ("op", "player", "search_cast")}, actor=actor,
    )


def _search_window(query, actor):
    refs = query.library_search_refs(actor)
    rows = {row.ref: row for row in query.objects(zones=("library",), owner=actor)}
    return tuple(rows[ref] for ref in refs)


def _can_decline(options):
    return not any(resolving_price_requires_cast(row) for row in options)


@dataclass(frozen=True, slots=True)
class QualifiedLibraryCastChoice:
    operation: str = OPERATION
    handler_id: str = "choice.library.qualified-cast.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("107.3b", "118.8c", "601.2", "608.2g", "701.23", "701.24a")
    capability_dependencies: tuple[str, ...] = (CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("phase", "stack", "selected", "_allowed", _FINGERPRINT)
    private_data: tuple[str, ...] = ("qualified library candidates", "selected library card before casting")
    projected_fields: tuple[str, ...] = ("search_cards", "search_window", "cast_options", "choice_schema")
    mutation_path: tuple[str, ...] = ("ResolveQualifiedLibraryCastIntent", "fixed_shuffle_effect")
    replay_fixture: str = "qualified-library-cast"
    test_modules: tuple[str, ...] = ("tests.test_qualified_library_cast",)

    @staticmethod
    def _shuffle(actor):
        return (FrozenMap(fixed_shuffle_effect(actor)),)

    def _skip(self, actor, effect, reason):
        return SemanticChoicePreparation(None, FrozenMap(effect),
                                         auto_continue=AutoContinue(reason, prepend_effects=self._shuffle(actor)))

    def prepare(self, effect, context):
        actor = context.actor
        if actor != context.stack_controller or actor not in context.query.active_seats:
            raise SemanticChoiceError("The qualified library search belongs to its resolving controller")
        try:
            if effect.get("phase") == "cast":
                chosen_library_cast(effect, actor, context.stack_ref)
                options = tuple(context.query.authorized_cast_options())
                if not options:
                    return self._skip(actor, effect, "The found card cannot be cast")
                return SemanticChoicePreparation(
                    SemanticChoiceRequest("Cast the selected library card.",
                                          CastOrDeclineChoice(options, allow_decline=_can_decline(options)),
                                          public_context=FrozenMap({"cast_options": options})),
                    FrozenMap({**effect, _FINGERPRINT: discover_options_fingerprint(options)}),
                )
            QualifiedLibraryCastSpec.from_effect(effect, actor=actor)
            search = {**effect, "phase": "search", "stack": context.stack_ref}
            if not context.query.library_search_allowed(actor):
                return self._skip(actor, search, "Library search is prohibited")
            spec = _search_base(search, actor, context.stack_ref)
            window = _search_window(context.query, actor)
            candidates = tuple(row for row in window if spec.matches(row.types, row.colors, row.mana_value))
            refs = tuple(row.ref for row in candidates)
            return SemanticChoicePreparation(
                SemanticChoiceRequest(
                    "Choose one qualifying instant card or fail to find.",
                    ObjectChoice(field_name="card", legal_refs=refs, zones=("library",), minimum=0, maximum=int(bool(refs)),
                                 optional=True, visibility="actor_private", owner_relation="actor",
                                 schema_extras=FrozenMap({"rules_may_fail_to_find": True})),
                    public_context=FrozenMap({
                        "search_cards": [{"id": row.ref, "name": row.printed_name} for row in candidates],
                        "search_window": [{"id": row.ref, "name": row.printed_name} for row in window],
                    }),
                ),
                FrozenMap({**search, "_allowed": {row.ref: row.logical_object_id for row in candidates}}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = continuation.semantic_frame.controller
        stack_ref = continuation.stack_ref
        try:
            if effect.get("phase") == "search":
                base = {key: value for key, value in effect.items() if key != "_allowed"}
                spec = _search_base(base, actor, stack_ref)
                selected = response.get("card")
                if selected in (None, ""):
                    return SemanticChoiceCompletion(prepend_effects=self._shuffle(actor))
                row = query.object(selected, zones=("library",)) if type(selected) is str else None
                if (row is None or row.owner != actor or effect["_allowed"].get(selected) != row.logical_object_id
                        or selected not in query.library_search_refs(actor)
                        or not spec.matches(row.types, row.colors, row.mana_value)):
                    raise ValueError("Choose one current qualifying library card")
                identity = LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref)
                return SemanticChoiceCompletion(prepend_effects=(FrozenMap({
                    **base, "phase": "cast", "selected": identity.to_dict(),
                }),))
            raw = FrozenMap({key: value for key, value in effect.items() if key != _FINGERPRINT})
            chosen_library_cast(raw, actor, stack_ref)
            options = tuple(query.authorized_cast_options())
            fingerprint = effect[_FINGERPRINT]
            if not options or discover_options_fingerprint(options) != fingerprint:
                raise ValueError("The library cast options changed")
            cast = _submitted_cast(response)
            if cast is None:
                if not _can_decline(options):
                    raise ValueError("The selected library card must be cast if able")
                return SemanticChoiceCompletion(prepend_effects=self._shuffle(actor))
            selected = tuple(row for row in options if cast.get("cost_option") in (None, row["id"]))
            if len(selected) != 1:
                raise ValueError("Choose one issued library cast price")
            option, = selected
            return SemanticChoiceCompletion(intents=(ResolveQualifiedLibraryCastIntent(
                actor, stack_ref, raw, fingerprint, FrozenMap({**cast, "cost_option": option["id"]}),
            ),), prepend_effects=self._shuffle(actor))
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
