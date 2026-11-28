"""Resolve an exact named reveal after its separate fixed top exile."""

from ..carddb_characteristics import library_card_rules_names
from ..replacement.immutable import FrozenMap
from ..reveal_until_library_selection_model import (
    CHOSEN_NAME_REVEAL_VERSION, REVEAL_UNTIL_OPERATION,
    read_reveal_until_prefix, validate_chosen_name_reveal,
)
from ..rules.library_selection import LibrarySelectionArrangement, LibrarySelectionObjectIdentity
from ..semantic_runtime.intents import LibrarySelectionIntent, MoveObjectsSimultaneouslyIntent
from ..semantic_runtime.intents import RecordChoiceIntent, RevealLibraryCardsIntent
from ..util import normalize_card_name
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _validate_stage(effect, actor):
    stage = effect.get("_stage")
    extras = {
        None: set(),
        "initial_exile": {"_stage", "_chosen_name"},
        "reveal": {"_stage", "_chosen_name"},
        "remainder_exile": {"_stage", "_chosen_name", "_remainder"},
    }
    if stage not in extras or not extras[stage] <= set(effect):
        raise SemanticChoiceError("The named library reveal lost its ordered stage")
    validate_chosen_name_reveal({key: value for key, value in effect.items() if key not in extras[stage]}, player=actor)
    if stage is not None and (type(effect["_chosen_name"]) is not str or not effect["_chosen_name"]):
        raise SemanticChoiceError("The resolving name is missing")
    return stage


def materialize_resolving_library_names(host, item, actor, effect):
    if (effect.get("op"), effect.get("schema_version"), effect.get("_stage")) != (
        REVEAL_UNTIL_OPERATION, CHOSEN_NAME_REVEAL_VERSION, "reveal",
    ):
        return {}
    if item is None or item.controller != actor or effect.get("player") != actor:
        raise SemanticChoiceError("Only the resolving controller can scan the named reveal")
    _validate_stage(effect, actor)
    chosen = normalize_card_name(effect["_chosen_name"])
    results = {}
    for object_id in reversed(host.state.players[actor].zones["library"]):
        card = host.state.cards[object_id]
        if card.zone != "library" or card.owner != actor or card.object_kind != "card":
            raise SemanticChoiceError("The named reveal requires current physical library cards")
        record = host.card_record(card)
        if record is None:
            raise SemanticChoiceError("The named reveal requires pinned library card names")
        names = library_card_rules_names(record)
        results[card.ref] = names
        if chosen in {normalize_card_name(name) for name in names}:
            break
    return results


def _advance(effect, *, intents=(), **following):
    return SemanticChoicePreparation(
        request=None, continuation_effect=FrozenMap(effect), preparation_intents=intents,
        auto_continue=AutoContinue(reason="Continue the ordered named library reveal.",
                                   prepend_effects=(FrozenMap({**effect, **following}),) if following else ()),
    )


def _batch_exile(actor, refs, reason):
    return (MoveObjectsSimultaneouslyIntent(
        actor, refs, ("library",), "exile", reason, owned_only=True,
    ),) if refs else ()


def _reveal_named_prefix(effect, context):
    actor = context.actor
    chosen = normalize_card_name(effect["_chosen_name"])

    def matches(row):
        return chosen in {normalize_card_name(name) for name in context.query.resolving_library_rules_names(row.ref)}

    rows, matches = read_reveal_until_prefix(context.query, player=actor, stop_count=1, matches_row=matches)
    if not rows:
        return _advance(effect)
    looked = tuple(LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref) for row in rows)
    remainder = looked[:-1] if matches else looked
    intents = (RevealLibraryCardsIntent(
        actor=actor, player=actor, viewer=actor, refs_top_first=tuple(card.ref for card in looked),
        reason=context.stack_label, public=True, announce_public_prefix=True,
    ),)
    if matches:
        arrangement = LibrarySelectionArrangement(
            looked, matches, tuple(card.ref for card in remainder), "library_unchanged", "chosen",
        )
        intents += (LibrarySelectionIntent(
            actor=actor, player=actor, arrangement=arrangement, reason=context.stack_label,
            source_stack_ref=context.stack_ref, looked_are_public=True, selected_are_public=True,
        ),)
    return _advance(effect, intents=intents, _stage="remainder_exile",
                    _remainder=tuple(card.to_dict() for card in remainder))


def _finish_exile(effect, context):
    identities = tuple(LibrarySelectionObjectIdentity.from_dict(card) for card in effect["_remainder"])
    refs = tuple(card.ref for card in identities)
    if context.query.library_refs(context.actor, top_first=True)[:len(refs)] != refs:
        raise SemanticChoiceError("The revealed remainder ceased to be the current prefix")
    for identity in identities:
        row = context.query.object(identity.ref, zones=("library",))
        if row is None or (row.object_id, row.logical_object_id) != (identity.object_id, identity.logical_object_id):
            raise SemanticChoiceError("A revealed remainder card changed its incarnation")
    return _advance(effect, intents=_batch_exile(context.actor, refs, "Exile the other revealed cards"))


def prepare_chosen_name_reveal(effect, context):
    try:
        stage = _validate_stage(effect, context.actor)
        if stage is None:
            return SemanticChoicePreparation(
                request=SemanticChoiceRequest(
                    prompt="Choose a Magic card name.",
                    choice=ScalarChoice(field_name="card_name", value_type="card_name", nonempty=True),
                    public_context=FrozenMap({"stack": context.stack_ref, "operation": REVEAL_UNTIL_OPERATION}),
                ),
                continuation_effect=FrozenMap(effect),
            )
        if stage == "initial_exile":
            refs = context.query.library_refs(context.actor, top_first=True)[:effect["exile_count"]]
            return _advance(effect, intents=_batch_exile(context.actor, refs, "Exile the fixed library tops"),
                            _stage="reveal")
        if stage == "reveal":
            return _reveal_named_prefix(effect, context)
        return _finish_exile(effect, context)
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def complete_chosen_name(continuation, response, query):
    effect = continuation.effect
    actor = effect.get("player")
    if actor not in query.active_seats or _validate_stage(effect, actor) is not None:
        raise SemanticChoiceError("Only the resolving controller can choose the name")
    submitted = response.get("card_name")
    name = query.canonical_card_name(submitted.strip()) if isinstance(submitted, str) else None
    if name is None:
        raise SemanticChoiceError("Choose an existing Magic card name")
    return SemanticChoiceCompletion(
        intents=(RecordChoiceIntent(actor, "card.name.chosen", f"{actor} chose {name}.",
                                    FrozenMap({"card_name": name})),),
        prepend_effects=(FrozenMap({**effect, "_stage": "initial_exile", "_chosen_name": name}),),
    )
