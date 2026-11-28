"""Reveal a public prefix after the direct destruction finishes."""

from dataclasses import dataclass

from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..reveal_until_library_selection_model import read_reveal_until_prefix
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..rules.target_controller_library_reveal import (
    PARTITION_OPERATION, REVEAL_OPERATION, TARGET_CONTROLLER_REVEAL_CAPABILITY,
)
from ..semantic_runtime.intents import RevealLibraryCardsIntent
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation

_FIELDS = {"op", "schema_version", "player", "spell_controller", "target_ref", "target_identity", "target_types"}
_RULES = ("110.4a", "303.4f", "303.4g", "400.7", "401.4", "608.2b", "608.2c", "608.2h", "609.3", "701.8", "701.20a")


def _subject_types(effect, context):
    if (set(effect) != _FIELDS or type(effect.get("schema_version")) is not int
            or effect["schema_version"] != 1 or effect.get("op") != REVEAL_OPERATION
            or effect["player"] != context.actor or effect["spell_controller"] != context.stack_controller
            or any(type(effect[key]) is not str or not effect[key]
                   for key in ("player", "spell_controller", "target_ref", "target_identity"))):
        raise SemanticChoiceError("A public library prefix requires its committed subject and exact actor")
    stack = context.query.stack_object(context.stack_ref)
    ref = effect["target_ref"]
    snapshot = stack.target_snapshots.get(ref) if stack is not None and tuple(stack.targets) == (ref,) else None
    if not snapshot or effect["target_identity"] != f"{snapshot.get('object_id')}@{snapshot.get('zone_change_counter')}":
        raise SemanticChoiceError("A public library prefix requires the announced destruction target")
    types = effect["target_types"]
    if (not isinstance(types, (list, tuple)) or any(type(value) is not str or not value for value in types)
            or tuple(types) != tuple(sorted(set(types)))):
        raise SemanticChoiceError("Destruction target card types must be canonical")
    return tuple(types)


@dataclass(frozen=True, slots=True)
class TargetControllerLibraryRevealHandler:
    operation: str = REVEAL_OPERATION
    handler_id: str = "choice.library.destroy-target-controller-prefix.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = _RULES
    capability_dependencies: tuple[str, ...] = (TARGET_CONTROLLER_REVEAL_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "spell_controller", "target_ref",
                                          "target_identity", "target_types")
    private_data: tuple[str, ...] = ("the affected player's library prefix before its public reveal",)
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("RevealLibraryCardsIntent", "LibrarySelectionIntent")
    replay_fixture: str = "destroy-target-controller-reveal-prefix"
    test_modules: tuple[str, ...] = ("tests.test_wild_magic_surge",)

    def prepare(self, effect, context):
        types = _subject_types(effect, context)
        actor = context.actor
        if actor not in context.query.active_seats:
            return SemanticChoicePreparation(None, FrozenMap(effect), auto_continue=AutoContinue("The player left."))
        predicates = tuple(ObjectQuerySpec(zones=("library",), types_all=(kind,), types_any=types)
                           for kind in sorted(PERMANENT_CARD_TYPES)) if types else ()
        rows, selected = read_reveal_until_prefix(context.query, player=actor, predicates=predicates, stop_count=1)
        if not rows:
            return SemanticChoicePreparation(None, FrozenMap(effect), auto_continue=AutoContinue("The library is empty."))
        looked = tuple(LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref) for row in rows)
        movement = FrozenMap({
            "op": PARTITION_OPERATION, "schema_version": 1, "player": actor,
            "spell_controller": context.stack_controller, "stack_ref": context.stack_ref,
            "looked": [row.to_dict() for row in looked], "selected": selected[0] if selected else None,
        })
        reveal = RevealLibraryCardsIntent(
            actor=actor, player=actor, viewer=actor, refs_top_first=tuple(row.ref for row in looked),
            reason=context.stack_label, public=True, announce_public_prefix=True,
        )
        return SemanticChoicePreparation(
            None, FrozenMap(effect), preparation_intents=(reveal,),
            auto_continue=AutoContinue("Place the matching card and randomly bottom the rest.", prepend_effects=(movement,)),
        )

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("This committed public reveal has no direct player response")
