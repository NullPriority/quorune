"""Commit face-down library entries and keep the original look and choice private."""

from dataclasses import dataclass
from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..rules.cloak_shapes import (
    CLOAK_CAPABILITY,
    CLOAK_OPERATION,
    CLOAK_PARTITION_CAPABILITY,
    CLOAK_REMAINDER_OPERATION,
    validate_cloak_instruction,
)
from ..semantic_runtime.intents import MoveLibraryCardsToBottomIntent, RevealLibraryCardsIntent
from .manifest import _manifest_intent
from .model import (
    AutoContinue,
    ObjectChoice,
    SemanticChoiceCompletion,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _snapshot(value):
    if not isinstance(value, (list, tuple)) or not value or len(value) > 5:
        raise SemanticChoiceError("Cloak needs its original looked-at library cards")
    if any(
        not isinstance(entry, (list, tuple)) or len(entry) != 2
        or any(type(field) is not str or not field for field in entry)
        or not entry[1].rpartition("@")[0] or not entry[1].rpartition("@")[2].isdecimal()
        for entry in value
    ):
        raise SemanticChoiceError("Cloak library incarnations are malformed")
    result = tuple((ref, incarnation) for ref, incarnation in value)
    if len({ref for ref, _ in result}) != len(result):
        raise SemanticChoiceError("Cloak looked-at library cards must be distinct")
    return result


def _chosen(value, looked):
    count = min(2, len(looked))
    if type(value) is str and count == 1:
        value = (value,)
    if (
        not isinstance(value, (list, tuple))
        or len(value) != count
        or any(type(ref) is not str or ref not in dict(looked) for ref in value)
        or len(set(value)) != count
    ):
        raise SemanticChoiceError("Choose the required original cards in entry order")
    return tuple(value)


def _remaining_objects(query, actor, looked, selected):
    survivors = []
    remainder = []
    for ref, incarnation in looked:
        row = query.object(ref, zones=("library",))
        if row is None and ref in selected:
            continue
        if row is None or row.logical_object_id != incarnation:
            raise SemanticChoiceError("A Cloak library incarnation changed")
        survivors.append(ref)
        if ref not in selected:
            remainder.append(ref)
    if query.library_refs(actor, top_first=True)[:len(survivors)] != tuple(survivors):
        raise SemanticChoiceError("Cloak lost its original remaining library prefix")
    return tuple(remainder)


@dataclass(frozen=True, slots=True)
class CloakChoiceHandler:
    operation: str = CLOAK_OPERATION
    handler_id: str = "choice.library.cloak.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 701.58", "CR 708.2", "CR 708.5")
    capability_dependencies: tuple[str, ...] = (CLOAK_CAPABILITY,)
    continuation_fields: tuple[str, ...] = (
        "op", "schema_version", "form", "player", "_looked_entries",
    )
    private_data: tuple[str, ...] = ("actor top library identities",)
    projected_fields: tuple[str, ...] = ("prompt", "objects", "legal_actions.choice_schema.legal_refs")
    mutation_path: tuple[str, ...] = ("RevealLibraryCardsIntent", "ZoneMoveIntent", "MoveLibraryCardsToBottomIntent")
    replay_fixture: str = "cloak-controller-private-results"
    test_modules: tuple[str, ...] = ("tests.test_cloak_library_entry",)

    def prepare(self, effect, context):
        actor = context.actor
        try:
            form = validate_cloak_instruction(effect, actor)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        refs = context.query.library_refs(actor, top_first=True)[:1 if form == "top" else 5]
        rows = tuple(context.query.object(ref, zones=("library",)) for ref in refs)
        if any(row is None for row in rows):
            raise SemanticChoiceError("Cloak cannot bind its current library cards")
        if not rows:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="No library card to cloak"),
            )
        if form == "top":
            row, = rows
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                preparation_intents=(_manifest_intent(row.ref, row.logical_object_id, actor, context.stack_label, cloaked=True),),
                auto_continue=AutoContinue(reason="Cloak the current top library card"),
            )
        count = min(2, len(rows))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose the cards in the order they enter face down.",
                choice=ObjectChoice(field_name="cards", legal_refs=refs, zones=("library",),
                                    minimum=count, maximum=count, visibility="actor_private", owner_relation="actor",
                                    schema_extras=FrozenMap({"order": "selection_order"})),
                public_context=FrozenMap({"stack": context.stack_ref, "objects": [
                    {"id": row.ref, "name": row.printed_name} for row in rows
                ]}),
            ),
            continuation_effect=FrozenMap({**effect, "_looked_entries": [(row.ref, row.logical_object_id) for row in rows]}),
            preparation_intents=(RevealLibraryCardsIntent(
                actor=actor, player=actor, viewer=actor, refs_top_first=refs, reason=context.stack_label,
            ),),
        )

    def complete(self, continuation, response, query):
        original = dict(continuation.effect)
        looked = _snapshot(original.pop("_looked_entries", None))
        actor = original["player"]
        try:
            form = validate_cloak_instruction(original, actor)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if form != "top_five_choose_two" or actor != continuation.semantic_frame.controller:
            raise SemanticChoiceError("Cloak lost its original private selection")
        _remaining_objects(query, actor, looked, ())
        chosen = _chosen(response.get("cards"), looked)
        intents = tuple(_manifest_intent(ref, dict(looked)[ref], actor, continuation.note or "Cloak",
                                         cloaked=True) for ref in chosen)
        remainder = (
            FrozenMap({"op": CLOAK_REMAINDER_OPERATION, "player": actor, "schema_version": 1,
                       "looked": looked, "selected": chosen}),
        ) if len(looked) > len(chosen) else ()
        return SemanticChoiceCompletion(intents=intents, prepend_effects=remainder)


@dataclass(frozen=True, slots=True)
class CloakRemainderHandler:
    operation: str = CLOAK_REMAINDER_OPERATION
    handler_id: str = "choice.library.cloak-remainder.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 401.2", "CR 608.2c", "CR 701.58e")
    capability_dependencies: tuple[str, ...] = (CLOAK_PARTITION_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("op", "player", "schema_version", "looked", "selected")
    private_data: tuple[str, ...] = ("original looked and selected library identities",)
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("MoveLibraryCardsToBottomIntent",)
    replay_fixture: str = "cloak-controller-private-results"
    test_modules: tuple[str, ...] = ("tests.test_cloak_library_entry",)

    def prepare(self, effect, context):
        if (
            not isinstance(effect, Mapping)
            or set(effect) != {"op", "player", "schema_version", "looked", "selected"}
            or effect["op"] != self.operation
            or effect["player"] != context.actor
            or type(effect["schema_version"]) is not int
            or effect["schema_version"] != 1
        ):
            raise SemanticChoiceError("Cloak bottom ordering needs its earlier private selection")
        looked = _snapshot(effect["looked"])
        chosen = _chosen(effect["selected"], looked)
        refs = _remaining_objects(context.query, context.actor, looked, chosen)
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            preparation_intents=(MoveLibraryCardsToBottomIntent(
                actor=context.actor, player=context.actor, refs=refs,
                looked_count=len(looked), reason=context.stack_label,
                random_source_stack_ref=context.stack_ref,
            ),),
            auto_continue=AutoContinue(reason="Finish the original random library bottom placement"),
        )

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("Cloak remainder ordering has no player response")


CLOAK_CHOICE_HANDLERS = (CloakChoiceHandler(), CloakRemainderHandler())
