from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..library_battlefield_model import (
    LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION, PRIVATE_TOP_LAND_OPTIONAL_BOTTOM_VERSION,
    PRIVATE_TOP_LAND_OR_GOBLIN_VERSION, PRIVATE_TOP_LAND_OR_HAND_VERSION, PRIVATE_TOP_LAND_VERSIONS,
    RANDOM_REVEALED_CREATURE_ENTRY_VERSION, RevealedCreatureEntryFollowup, validate_random_revealed_creature_entry,
    validate_private_top_land_entry,
)
from ..linked_library_imprint_model import uses_library_imprint_selection, validate_library_imprint_selection
from ..private_opponent_library_cast_model import (
    uses_damage_library_cast_selection, uses_private_opponent_library_cast_selection, validate_private_opponent_library_cast_selection,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import object_matches_query
from ..rules.library_selection_capability_shapes import (
    CHOSEN_TYPE_LIBRARY_SELECTION_VERSION, CHOSEN_TYPE_TOP_SELECTION_VERSION,
    RESOLVED_TOP_RANDOM_BOTTOM_VERSION, TOP_RANDOM_BOTTOM_VERSIONS, FIXED_UP_TO_TWO_HAND_SELECTION_VERSION,
    PRIVATE_FIXED_TOP_TAKE_OR_GRAVEYARD_VERSION, REVEALED_TOP_HAND_LIFE_VERSION,
    PRIVATE_ONE_HAND_ORDERED_BOTTOM_VERSION, private_one_hand_ordered_bottom_shape,
    validate_chosen_type_library_selection, validate_chosen_type_top_selection,
    validate_private_fixed_top_take_or_graveyard,
    validate_fixed_top_random_bottom_selection, validate_fixed_up_to_two_hand_selection,
    validate_revealed_top_hand_life,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.library_play_partition import uses_private_hand_bottom_exile, validate_private_hand_bottom_exile
from ..rules.blocked_damage_cast import exile_offer, represents_window, window_dependencies, window_predicate
from ..rules.library_partition import LibraryPartitionError, partition_refs
from ..rules.library_selection import (
    LibrarySelectionArrangement,
    LibrarySelectionError,
    LibrarySelectionObjectIdentity,
)
from ..semantic_runtime.intents import (
    LibrarySelectionIntent,
    RevealLibraryCardsIntent,
)
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    LibraryPartitionChoice,
    ObjectChoice,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


_EFFECT_FIELDS = {
    "op",
    "player",
    "look_count",
    "public_reveal",
    "selected_reveal",
    "selection_policy",
    "minimum",
    "maximum",
    "predicate_groups",
    "remainder_destination",
    "remainder_order",
}


def _specs(value: Any) -> tuple[tuple[ObjectQuerySpec, ...], ...]:
    if not isinstance(value, (list, tuple)):
        raise SemanticChoiceError(
            "Library selection predicate groups must be an array"
        )
    groups: list[tuple[ObjectQuerySpec, ...]] = []
    try:
        for raw_group in value:
            if not isinstance(raw_group, (list, tuple)) or not raw_group:
                raise SemanticChoiceError(
                    "Library selection predicate groups must be nonempty"
                )
            groups.append(
                tuple(ObjectQuerySpec.from_dict(raw) for raw in raw_group)
            )
    except (ObjectQueryError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(
            "Library selection predicates are malformed"
        ) from exc
    return tuple(groups)


def _eligible_groups(
    rows: Sequence[Any],
    groups: tuple[tuple[ObjectQuerySpec, ...], ...],
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(
            row.ref
            for row in rows
            if any(object_matches_query(row, spec) for spec in group)
        )
        for group in groups
    )


def _slot_assignment_exists(
    selected: tuple[str, ...],
    eligible_groups: tuple[tuple[str, ...], ...],
) -> bool:
    def assign(index: int, remaining: tuple[int, ...]) -> bool:
        if index == len(selected):
            return True
        ref = selected[index]
        return any(
            ref in eligible_groups[group]
            and assign(
                index + 1,
                tuple(value for value in remaining if value != group),
            )
            for group in remaining
        )

    return assign(0, tuple(range(len(eligible_groups))))


def _identity(row: Any) -> LibrarySelectionObjectIdentity:
    try:
        return LibrarySelectionObjectIdentity(
            object_id=row.object_id,
            logical_object_id=row.logical_object_id,
            ref=row.ref,
        )
    except LibrarySelectionError as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _arrangement(effect, looked, selected, remainder):
    followup = effect.get("entry_followup")
    return LibrarySelectionArrangement(
        looked=looked, selected_refs=selected, remainder_refs=remainder,
        remainder_destination=effect["remainder_destination"], remainder_order=effect["remainder_order"],
        selected_destination=effect.get("selected_destination", "hand"), entry_tapped=effect.get("entry_tapped", False),
        entry_followup=RevealedCreatureEntryFollowup.from_dict(followup) if followup is not None else None,
    )


_PRIVATE_SINGLETON_SELECTION_VERSIONS = (
    CHOSEN_TYPE_TOP_SELECTION_VERSION, PRIVATE_FIXED_TOP_TAKE_OR_GRAVEYARD_VERSION,
    *PRIVATE_TOP_LAND_VERSIONS,
)


def _top_choice_options(effect, actor, row):
    try:
        if effect.get("schema_version") in PRIVATE_TOP_LAND_VERSIONS:
            predicate = validate_private_top_land_entry(effect, player=actor)
            choices = []
            if row is not None and object_matches_query(row, predicate):
                choices.append("put_onto_battlefield")
            if effect["schema_version"] == PRIVATE_TOP_LAND_OR_HAND_VERSION:
                return (*choices, "put_into_hand")
            if effect["schema_version"] == PRIVATE_TOP_LAND_OR_GOBLIN_VERSION:
                return (*choices, "keep_and_create_goblin")
            if row is not None and effect["schema_version"] == PRIVATE_TOP_LAND_OPTIONAL_BOTTOM_VERSION:
                choices.append("put_on_bottom")
            return (*choices, "keep")
        if effect.get("schema_version") == PRIVATE_FIXED_TOP_TAKE_OR_GRAVEYARD_VERSION:
            predicate = validate_private_fixed_top_take_or_graveyard(effect, player=actor)
        else:
            predicate = validate_chosen_type_top_selection(effect, actor=actor)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    options = []
    if row is not None and predicate is not None and object_matches_query(row, predicate):
        options.append("reveal_and_take")
    if effect["remainder_destination"] == "optional_graveyard":
        options.append("put_into_graveyard")
    return (*options, "keep")


def _current_library_top(query, actor):
    refs = query.library_refs(actor, top_first=True)
    if not refs:
        return None
    row = query.object(refs[0], zones=("library",))
    if row is None or row.owner != actor:
        raise SemanticChoiceError("The actor's private top card is unavailable")
    return row


def _prepare_revealed_top_hand_life(effect, context, *, repeat_offer=None):
    from ..public_quantity_reductions import public_mana_value

    actor = context.actor
    if actor != context.stack_controller:
        raise SemanticChoiceError("The public top procedure belongs to the resolving controller")
    try:
        if repeat_offer is None:
            validate_revealed_top_hand_life(effect, player=actor)
        top = _current_library_top(context.query, actor)
        if top is None:
            return SemanticChoicePreparation(None, FrozenMap(effect),
                auto_continue=AutoContinue("There is no top card to reveal or move"))
        amount = public_mana_value({"mana_value": top.mana_value})
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    arrangement = LibrarySelectionArrangement(
        looked=(_identity(top),), selected_refs=(top.ref,), remainder_refs=(),
        remainder_destination="graveyard", remainder_order="chosen",
    )
    life_loss = (FrozenMap({"op": "lose_life", "player": actor, "amount": amount}),) if amount else ()
    following = (*life_loss, FrozenMap(repeat_offer)) if repeat_offer is not None else life_loss
    return SemanticChoicePreparation(
        request=None, continuation_effect=FrozenMap(effect),
        preparation_intents=(
            RevealLibraryCardsIntent(actor, actor, actor, (top.ref,), context.stack_label, public=True),
            LibrarySelectionIntent(
                actor=actor, player=actor, arrangement=arrangement, reason=context.stack_label,
                source_stack_ref=context.stack_ref, looked_are_public=True, selected_are_public=True,
            ),
        ),
        auto_continue=AutoContinue("Apply the revealed mana value after the hand movement", prepend_effects=following),
    )


def _prepare_random_revealed_creature_entry(effect, context):
    actor = context.actor
    if actor != context.stack_controller:
        raise SemanticChoiceError("Random creature entry belongs to the resolving controller")
    try:
        count = validate_random_revealed_creature_entry(effect, player=actor)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    refs = context.query.library_refs(actor, top_first=True)[:count]
    selected = context.query.choice_candidate_refs()
    rows = tuple(context.query.object(ref, zones=("library",)) for ref in refs)
    if any(row is None or row.owner != actor or not row.physical_card for row in rows):
        raise SemanticChoiceError("Random creature entry requires the controller's exact revealed prefix")
    eligible = {row.ref for row in rows if "creature" in row.types}
    if len(selected) != min(1, len(eligible)) or not set(selected) <= eligible:
        raise SemanticChoiceError("Random creature entry lost its selected eligible card")
    auto_continue = AutoContinue("The random partition needs no decision")
    if not refs:
        return SemanticChoicePreparation(None, FrozenMap(effect), auto_continue=auto_continue)
    arrangement = _arrangement(effect, tuple(map(_identity, rows)), selected,
                               tuple(ref for ref in refs if ref not in selected))
    return SemanticChoicePreparation(
        None, FrozenMap(effect), auto_continue=auto_continue, preparation_intents=(
            RevealLibraryCardsIntent(actor, actor, actor, refs, context.stack_label,
                                     public=True, announce_public_prefix=True),
            LibrarySelectionIntent(actor, actor, arrangement, context.stack_label, context.stack_ref, True, False),
        ),
    )


def _private_top_after_entry(effect, context):
    raw = thaw_value(effect)
    attempted = raw.pop("_entry_attempt")
    if raw.get("schema_version") != PRIVATE_TOP_LAND_OR_GOBLIN_VERSION:
        raise SemanticChoiceError("A private land entry check requires its original Goblin result")
    try:
        validate_private_top_land_entry(raw, player=context.actor)
        identity = LibrarySelectionObjectIdentity.from_dict(attempted)
    except (LibrarySelectionError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    current = context.query.object(identity.ref, zones=("battlefield",))
    if current is not None and (current.object_id != identity.object_id
                                or current.logical_object_id == identity.logical_object_id):
        raise SemanticChoiceError("The committed land entry has an invalid incarnation")
    fallback = (FrozenMap(raw["otherwise_token"]),) if current is None else ()
    return SemanticChoicePreparation(None, FrozenMap(effect),
        auto_continue=AutoContinue(reason="The land entry outcome is known", prepend_effects=fallback))


def _prepare_private_library_top(effect, context):
    actor = context.actor
    if actor != context.stack_controller:
        raise SemanticChoiceError("The private top choice belongs to its resolving controller")
    if "_entry_attempt" in effect:
        return _private_top_after_entry(effect, context)
    row = _current_library_top(context.query, actor)
    choices = _top_choice_options(effect, actor, row)
    if row is None:
        fallback = (FrozenMap(effect["otherwise_token"]),) if effect.get("schema_version") == PRIVATE_TOP_LAND_OR_GOBLIN_VERSION else ()
        return SemanticChoicePreparation(None, FrozenMap(effect),
            auto_continue=AutoContinue(reason="The library is empty", prepend_effects=fallback))
    continuation = FrozenMap({
        **dict(effect), "_choice_actor": actor, "_stack_label": context.stack_label,
        "_looked_objects": [_identity(row).to_dict()],
    })
    request = SemanticChoiceRequest(
        prompt="Choose what to do with the top card.",
        choice=ScalarChoice(field_name="choice", legal_values=choices),
        public_context=FrozenMap({
            "stack": context.stack_ref, "operation": "fixed_library_selection",
            "objects": [{"id": row.ref, "name": row.printed_name}],
        }),
    )
    return SemanticChoicePreparation(request, continuation, preparation_intents=(
        RevealLibraryCardsIntent(actor, actor, actor, (row.ref,), context.stack_label, public=False),
    ))


def _complete_private_library_top(continuation, response, query):
    effect = thaw_value(continuation.effect)
    actor = continuation.semantic_frame.controller
    if effect.pop("_choice_actor", None) != actor or actor not in query.active_seats:
        raise SemanticChoiceError("The private top choice is bound to its resolving controller")
    reason = effect.pop("_stack_label", None)
    looked = effect.pop("_looked_objects", ())
    row = _current_library_top(query, actor)
    if (type(reason) is not str or not reason or not isinstance(looked, list)
            or len(looked) != 1 or row is None):
        raise SemanticChoiceError("The private top choice requires a saved library incarnation")
    try:
        identity = LibrarySelectionObjectIdentity.from_dict(looked[0])
    except (LibrarySelectionError, TypeError, ValueError) as exc:
        raise SemanticChoiceError("The looked-at top identity is malformed") from exc
    if identity != _identity(row):
        raise SemanticChoiceError("The looked-at top card no longer matches its saved incarnation")
    choice = response.get("choice")
    if choice not in _top_choice_options(effect, actor, row):
        raise SemanticChoiceError("The chosen action is unavailable for the looked-at card")
    if choice == "keep":
        return SemanticChoiceCompletion()
    if choice == "keep_and_create_goblin":
        return SemanticChoiceCompletion(prepend_effects=(FrozenMap(effect["otherwise_token"]),))
    reveal = choice == "reveal_and_take"
    enter = choice == "put_onto_battlefield"
    bottom = choice == "put_on_bottom"
    take = reveal or enter or choice == "put_into_hand"
    arrangement = LibrarySelectionArrangement(
        looked=(identity,),
        selected_refs=(row.ref,) if take else (),
        remainder_refs=() if take else (row.ref,),
        remainder_destination="library_bottom" if take or bottom else "graveyard",
        remainder_order="chosen",
        selected_destination="battlefield" if enter else "hand",
        entry_tapped=enter,
    )
    intents = (LibrarySelectionIntent(
        actor=actor, player=actor, arrangement=arrangement, reason=reason,
        source_stack_ref=continuation.stack_ref, looked_are_public=False, selected_are_public=reveal,
    ),)
    if enter and effect.get("schema_version") == PRIVATE_TOP_LAND_OR_GOBLIN_VERSION:
        followup = FrozenMap({**effect, "_entry_attempt": identity.to_dict()})
        return SemanticChoiceCompletion(intents=intents, prepend_effects=(followup,))
    return SemanticChoiceCompletion(intents=intents)


@dataclass(frozen=True, slots=True)
class FixedLibrarySelectionChoiceHandler:
    operation: str = "fixed_library_selection"
    handler_id: str = "choice.library.fixed-selection.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 400.2",
        "CR 401.4",
        "CR 404.3",
        "CR 701.20",
    )
    capability_dependencies: tuple[str, ...] = (
        "library.select.fixed_controller",
    )
    continuation_fields: tuple[str, ...] = (
        'schema_version','selected_destination',
        'entry_tapped','mana_value_limit',
        "otherwise_token",
        "_entry_attempt",
        "look_count",
        "maximum",
        "minimum",
        "op",
        "player",
        "predicate_groups",
        "public_reveal",
        "remainder_destination",
        "remainder_order",
        "selected_reveal",
        "selection_policy",
        "_choice_actor",
        "_effective_maximum",
        "_effective_minimum",
        "_eligible_groups",
        "_looked_objects",
        "_stack_label",
    )
    private_data: tuple[str, ...] = (
        "actor library top",
        "looked-at object identities",
        "eligible characteristic groups",
    )
    projected_fields: tuple[str, ...] = (
        "prompt",
        "objects",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "RevealLibraryCardsIntent",
        "LibrarySelectionIntent",
        "ZoneTransitionOwner.move_cards_simultaneously",
        "commit_ordered_library_partition",
    )
    replay_fixture: str = "semantic-choice-fixed-library-selection"
    test_modules: tuple[str, ...] = (
        "tests.test_fixed_library_selection",
    )

    def _validated_effect(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> tuple[tuple[ObjectQuerySpec, ...], ...]:
        if uses_private_hand_bottom_exile(effect):
            if context.stack_controller != context.actor:
                raise SemanticChoiceError("The private partition belongs to its resolving controller")
            try:
                validate_private_hand_bottom_exile(effect, player=context.actor)
            except (TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            return ()
        if represents_window(effect):
            if context.stack_controller != context.actor:
                raise SemanticChoiceError("The private damage window belongs to its resolving controller")
            try:
                window_dependencies(effect, actor=context.actor)
            except (TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            return ((window_predicate(),),)
        if uses_private_opponent_library_cast_selection(effect):
            player = effect.get("player")
            if context.stack_controller != context.actor or player == context.actor or player not in context.query.active_seats:
                raise SemanticChoiceError("Private opponent library selection needs its resolving controller and opponent")
            try:
                validate_private_opponent_library_cast_selection(effect, player=player)
            except (TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            return ()
        if uses_library_imprint_selection(effect):
            if context.stack_controller != context.actor:
                raise SemanticChoiceError("Private library imprint belongs to its resolving controller")
            try:
                validate_library_imprint_selection(effect, player=context.actor)
            except (TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            return ()
        if effect.get("schema_version") == CHOSEN_TYPE_LIBRARY_SELECTION_VERSION:
            if context.stack_controller != context.actor:
                raise SemanticChoiceError("Chosen-type library selection requires its resolving controller")
            try:
                query = validate_chosen_type_library_selection(effect, actor=context.actor)
            except (TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            return ((query,),) if query is not None else ((),)
        if effect.get("schema_version") == FIXED_UP_TO_TWO_HAND_SELECTION_VERSION:
            if context.stack_controller != context.actor:
                raise SemanticChoiceError("The private hand selection requires its resolving controller")
            try:
                validate_fixed_up_to_two_hand_selection(effect, player=context.actor)
            except (TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            return ()
        if effect.get("schema_version") in TOP_RANDOM_BOTTOM_VERSIONS:
            if context.stack_controller != context.actor:
                raise SemanticChoiceError("The private top selection requires its resolving controller")
            try:
                validate_fixed_top_random_bottom_selection(effect, player=context.actor)
            except (TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            return ()
        if (type(effect.get("schema_version")) is int
                and effect["schema_version"] == PRIVATE_ONE_HAND_ORDERED_BOTTOM_VERSION):
            if (context.stack_controller != context.actor
                    or not private_one_hand_ordered_bottom_shape(effect, player=context.actor)):
                raise SemanticChoiceError("The private hand and bottom partition is malformed")
            return ()
        version_two=type(effect.get('schema_version')) is int and effect.get('schema_version')==2
        if type(effect.get('schema_version')) is int and effect.get('schema_version') in (3, LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION):
            from ..library_battlefield_model import validate_library_battlefield_effect,fixed_selection_without_entry_fields
            try:validate_library_battlefield_effect(effect)
            except (TypeError,ValueError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
            effect=fixed_selection_without_entry_fields(effect)
        expected=_EFFECT_FIELDS|{'schema_version','selected_destination'} if version_two else _EFFECT_FIELDS
        if (set(effect)!=expected or version_two and (effect.get('selected_destination')!='library_top'
                or effect.get('remainder_destination')!='exile' or effect.get('selection_policy')!='up_to_any'
                or type(effect.get('minimum')) is not int or effect['minimum']!=0
                or type(effect.get('maximum')) is not int or effect['maximum']!=1
                or effect.get('remainder_order')!='chosen' or effect.get('public_reveal') is not False
                or effect.get('selected_reveal') is not False) or effect.get('selection_policy')=='up_to_any' and not version_two):
            raise SemanticChoiceError(
                "Fixed library selection effects have a closed field set"
            )
        if (
            effect.get("op") != self.operation
            or effect.get("player") != context.actor
            or context.stack_controller != context.actor
            or type(effect.get("look_count")) is not int
            or effect["look_count"] <= 0
            or type(effect.get("minimum")) is not int
            or type(effect.get("maximum")) is not int
            or effect["minimum"] < 0
            or effect["maximum"] <= 0
            or effect["minimum"] > effect["maximum"]
            or effect["maximum"] > effect["look_count"]
            or type(effect.get("public_reveal")) is not bool
            or type(effect.get("selected_reveal")) is not bool
            or effect.get("selection_policy")
            not in {
                "fixed_any",
                "up_to_matching",
                "all_matching",
                "optional_slots",
                "up_to_any",
            }
            or effect.get("remainder_destination")
            not in ({'exile'} if version_two else {"graveyard", "library_bottom"})
            or effect.get("remainder_order") not in {"chosen", "random"}
        ):
            raise SemanticChoiceError(
                "Fixed library selection effect is malformed"
            )
        groups = _specs(effect["predicate_groups"])
        policy = effect["selection_policy"]
        if (
            (policy in {"fixed_any",'up_to_any'} and groups)
            or (
                policy in {"up_to_matching", "all_matching"}
                and len(groups) != 1
            )
            or (
                policy == "optional_slots"
                and len(groups) != effect["maximum"]
            )
        ):
            raise SemanticChoiceError(
                "Fixed library selection policy and predicates disagree"
            )
        return groups

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        from .damage_library_cast_order import DAMAGE_SELECTED, prepare_damage_library_cast_commit

        if uses_damage_library_cast_selection(effect):
            if effect["player"] not in context.query.active_seats:
                return SemanticChoicePreparation(None, FrozenMap(effect),
                    auto_continue=AutoContinue(reason="The original damaged player left the game."))
            if DAMAGE_SELECTED in effect:
                return prepare_damage_library_cast_commit(effect, context)
        from ..rules.private_look_land_or_hand import PRIVATE_LOOK_LAND_OR_HAND_VERSION
        from .private_look_land_or_hand import prepare_private_land_hand

        if effect.get("schema_version") == PRIVATE_LOOK_LAND_OR_HAND_VERSION:
            return prepare_private_land_hand(effect, context)
        if effect.get("schema_version") == REVEALED_TOP_HAND_LIFE_VERSION:
            from ..rules.repeat_top_hand_life import REPEAT_TOP_HAND_LIFE_FIELD
            from .repeat_top_hand_life import prepare_repeat_top_hand_life

            if REPEAT_TOP_HAND_LIFE_FIELD in effect:
                return prepare_repeat_top_hand_life(effect, context, _prepare_revealed_top_hand_life)
            return _prepare_revealed_top_hand_life(effect, context)
        if effect.get("schema_version") == RANDOM_REVEALED_CREATURE_ENTRY_VERSION:
            return _prepare_random_revealed_creature_entry(effect, context)
        if effect.get("schema_version") in _PRIVATE_SINGLETON_SELECTION_VERSIONS:
            return _prepare_private_library_top(effect, context)
        groups = self._validated_effect(effect, context)
        private_cast = uses_private_opponent_library_cast_selection(effect)
        player = effect["player"] if private_cast else context.actor
        refs = context.query.library_refs(player, top_first=True)[
            : int(effect["look_count"])
        ]
        rows = []
        for ref in refs:
            row = context.query.object(ref, zones=("library",))
            if row is None or private_cast and row.owner != player:
                raise SemanticChoiceError(
                    "A looked-at library card is absent from the actor query"
                )
            rows.append(row)
        identities = tuple(_identity(row) for row in rows)
        from ..library_battlefield_model import library_mana_value_permits
        eligible = _eligible_groups(tuple(row for row in rows if library_mana_value_permits(effect,row)), groups)
        policy = str(effect["selection_policy"])
        if policy=='up_to_any':
            effective_maximum=min(int(effect['maximum']),len(refs));effective_minimum=min(int(effect['minimum']),effective_maximum)
            legal_refs=refs
        elif policy == "fixed_any":
            effective_maximum = min(int(effect["maximum"]), len(refs))
            effective_minimum = effective_maximum
            legal_refs = refs
        else:
            legal_refs = tuple(
                dict.fromkeys(ref for group in eligible for ref in group)
            )
            effective_maximum = min(
                int(effect["maximum"]),
                len(legal_refs),
            )
            effective_minimum = (
                1
                if int(effect["minimum"]) > 0 and effective_maximum > 0
                else 0
            )
        if policy == "all_matching":
            effective_minimum = len(legal_refs)
            effective_maximum = len(legal_refs)

        continuation = FrozenMap(
            {
                **dict(effect),
                "_choice_actor": context.actor,
                "_effective_minimum": effective_minimum,
                "_effective_maximum": effective_maximum,
                "_eligible_groups": [list(group) for group in eligible],
                "_looked_objects": [
                    identity.to_dict() for identity in identities
                ],
                "_stack_label": context.stack_label,
            }
        )
        preparation_intents = (
            RevealLibraryCardsIntent(
                actor=context.actor,
                player=player,
                viewer=context.actor,
                refs_top_first=refs,
                reason=context.stack_label,
                public=bool(effect["public_reveal"]),
            ),
        ) if refs else ()

        sole_partition = uses_private_hand_bottom_exile(effect) and len(refs) == 1
        deterministic = (policy == "all_matching" or not refs or sole_partition or (represents_window(effect) and not legal_refs)
                         or effect.get("schema_version") in {LIBRARY_REVEALED_ENTRY_FOLLOWUP_VERSION,
                                                             CHOSEN_TYPE_LIBRARY_SELECTION_VERSION} and not legal_refs)
        needs_remainder_order = (
            effect["remainder_order"] == "chosen"
            and len(refs) - effective_minimum > 1
        )
        if deterministic and not needs_remainder_order:
            selected = legal_refs if policy == "all_matching" or sole_partition else ()
            remainder = tuple(ref for ref in refs if ref not in selected)
            arrangement = _arrangement(effect, identities, selected, remainder)
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=continuation,
                auto_continue=AutoContinue(
                    reason="the fixed library partition needs no decision"
                ),
                preparation_intents=(
                    *preparation_intents,
                    self._intent(
                        continuation,
                        arrangement,
                        stack_ref=context.stack_ref,
                    ),
                ),
            )

        return SemanticChoicePreparation(
            request=self._choice_request(
                effect=effect,
                context=context,
                refs=refs,
                rows=rows,
                legal_refs=legal_refs,
                effective_minimum=effective_minimum,
                effective_maximum=effective_maximum,
            ),
            continuation_effect=continuation,
            preparation_intents=preparation_intents,
        )

    def _choice_request(
        self,
        *,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
        refs: Sequence[str],
        rows: Sequence[Any],
        legal_refs: Sequence[str],
        effective_minimum: int,
        effective_maximum: int,
    ) -> SemanticChoiceRequest:
        public_context = FrozenMap(
            {
                "stack": context.stack_ref,
                "operation": self.operation,
                "objects": [
                    {"id": row.ref, "name": row.printed_name}
                    for row in rows
                ],
            }
        )
        if uses_private_hand_bottom_exile(effect):
            from .library_play_partition import private_hand_bottom_exile_request

            return private_hand_bottom_exile_request(refs, public_context)
        if uses_private_opponent_library_cast_selection(effect):
            return SemanticChoiceRequest(
                prompt=("Choose one card to exile face down. The rest go to their owner's graveyard."
                        if uses_damage_library_cast_selection(effect) else
                        "Choose one card to exile face down. The rest go to the bottom in a random order."),
                choice=ObjectChoice(
                    field_name="cards", legal_refs=legal_refs, zones=("library",),
                    minimum=1, maximum=1, visibility="actor_private",
                ),
                public_context=public_context,
            )
        if uses_library_imprint_selection(effect):
            return SemanticChoiceRequest(
                prompt="Choose one card to exile face down and order the rest on the bottom of your library.",
                choice=LibraryPartitionChoice(
                    field_name="cards", legal_refs=refs, visibility="actor_private",
                    partitions=FrozenMap({
                        "exile": {"order": "selection_order", "label": "Face-down exile",
                                  "minimum": 1, "maximum": 1},
                        "bottom": {"order": "bottom_to_top", "label": "Bottom of library"},
                    }),
                    legacy_destination=None,
                ),
                public_context=public_context,
            )
        if effect["remainder_order"] == "chosen":
            destination = 'exile' if effect['remainder_destination']=='exile' else (
                "graveyard"
                if effect["remainder_destination"] == "graveyard"
                else "bottom"
            )
            choice: Any = LibraryPartitionChoice(
                field_name="cards",
                legal_refs=refs,
                visibility="actor_private",
                partitions=FrozenMap(
                    {
                        ('top' if effect.get('selected_destination')=='library_top' else
                         'battlefield' if effect.get('selected_destination')=='battlefield' else 'hand'): {
                            "order": 'top_to_bottom' if effect.get('selected_destination')=='library_top' else "selection_order",
                            "label": 'Top of library' if effect.get('selected_destination')=='library_top' else
                                'Battlefield' if effect.get('selected_destination')=='battlefield' else "Hand",
                            **({'minimum':effective_minimum,'maximum':effective_maximum}
                               if effect.get('selected_destination')=='library_top'
                               or effect.get("schema_version") in {
                                   FIXED_UP_TO_TWO_HAND_SELECTION_VERSION, PRIVATE_ONE_HAND_ORDERED_BOTTOM_VERSION,
                               } else {}),
                        },
                        destination: {
                            "order": (
                                "graveyard_top_to_bottom"
                                if destination == "graveyard"
                                else 'selection_order' if destination=='exile' else "bottom_to_top"
                            ),
                            "label": (
                                "Graveyard"
                                if destination == "graveyard"
                                else 'Exile' if destination=='exile' else "Bottom of library"
                            ),
                        },
                    }
                ),
                legacy_destination=None,
            )
        else:
            choice = ObjectChoice(
                field_name="cards",
                legal_refs=legal_refs,
                zones=("library",),
                minimum=effective_minimum,
                maximum=effective_maximum,
                optional=effective_minimum == 0,
                visibility="actor_private",
                owner_relation="actor",
            )
        if represents_window(effect):
            return SemanticChoiceRequest(
                prompt="Exile up to one nonland card. The rest go to the bottom in a random order.",
                choice=choice, public_context=public_context,
            )
        if effect.get("schema_version") == FIXED_UP_TO_TWO_HAND_SELECTION_VERSION:
            remainder = ("Order the rest into your graveyard." if effect["remainder_destination"] == "graveyard"
                         else "The rest go to the bottom of your library in a random order.")
            return SemanticChoiceRequest(
                prompt=f"Choose up to {effective_maximum} looked-at card(s) to put into your hand. {remainder}",
                choice=choice, public_context=public_context,
            )
        return SemanticChoiceRequest(
            prompt=(
                "Choose up to one looked-at card to keep on top. The rest go to the bottom in a random order."
                if effect.get("schema_version") in TOP_RANDOM_BOTTOM_VERSIONS else
                f"Choose up to {effective_maximum} looked-at card(s) to keep on top; exile the rest."
                if effect.get('selected_destination')=='library_top' else
                "Choose the looked-at cards to put onto the battlefield and complete the remainder ordering."
                if effect.get('selected_destination')=='battlefield' else
                "Choose the looked-at cards to put into your hand and complete the remainder ordering."
            ),
            choice=choice,
            public_context=public_context,
        )

    @staticmethod
    def _intent(
        continuation_effect: Mapping[str, Any],
        arrangement: LibrarySelectionArrangement,
        *,
        stack_ref: str,
    ) -> LibrarySelectionIntent:
        actor = str(continuation_effect["_choice_actor"])
        return LibrarySelectionIntent(
            actor=actor,
            player=str(continuation_effect["player"]) if uses_private_opponent_library_cast_selection(continuation_effect) else actor,
            arrangement=arrangement,
            reason=str(continuation_effect["_stack_label"]),
            source_stack_ref=stack_ref,
            looked_are_public=bool(
                continuation_effect["public_reveal"]
            ),
            selected_are_public=bool(
                continuation_effect["selected_reveal"]
            ),
            require_graveyard_order=uses_damage_library_cast_selection(continuation_effect),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        from .damage_library_cast_order import DAMAGE_GRAVE_ORDER, DAMAGE_SELECTED, complete_damage_library_graveyard_order

        from ..rules.repeat_top_hand_life import REPEAT_TOP_HAND_LIFE_FIELD
        from .repeat_top_hand_life import complete_repeat_top_hand_life

        from ..rules.private_look_land_or_hand import PRIVATE_LOOK_LAND_OR_HAND_VERSION
        from .private_look_land_or_hand import complete_private_land_hand

        effect = continuation.effect
        if REPEAT_TOP_HAND_LIFE_FIELD in effect:
            return complete_repeat_top_hand_life(continuation, response, query)
        if effect.get("schema_version") == PRIVATE_LOOK_LAND_OR_HAND_VERSION:
            return complete_private_land_hand(continuation, response, query)
        if effect.get("schema_version") == RANDOM_REVEALED_CREATURE_ENTRY_VERSION:
            raise SemanticChoiceError("Random creature entry accepts no player selection")
        if DAMAGE_GRAVE_ORDER in effect:
            return complete_damage_library_graveyard_order(continuation, response, query)
        if effect.get("schema_version") in _PRIVATE_SINGLETON_SELECTION_VERSIONS:
            return _complete_private_library_top(continuation, response, query)
        try:
            looked = tuple(
                LibrarySelectionObjectIdentity.from_dict(value)
                for value in effect.get("_looked_objects", ())
            )
        except (LibrarySelectionError, TypeError, ValueError) as exc:
            raise SemanticChoiceError(
                "Library selection continuation identities are malformed"
            ) from exc
        looked_refs = tuple(value.ref for value in looked)
        raw_cards = response.get("cards")
        if represents_window(effect) and not isinstance(raw_cards, (list, tuple)):
            raise SemanticChoiceError("Choose zero or one nonland from the private damage window")
        if effect.get("schema_version") == RESOLVED_TOP_RANDOM_BOTTOM_VERSION and (
            set(response) - {"action", "choice_schema", "cards"} or ("cards" in response and raw_cards is None)
        ):
            raise SemanticChoiceError("The private top selection accepts only the optional kept card")
        imprint = uses_library_imprint_selection(effect)
        if uses_private_opponent_library_cast_selection(effect):
            player = effect["player"]
            if (not isinstance(raw_cards, (list, tuple)) or player not in query.active_seats
                    or looked_refs != query.library_refs(player, top_first=True)[:int(effect["look_count"])]):
                raise SemanticChoiceError("Choose one card from the unchanged private library window")
        if imprint and not isinstance(raw_cards, Mapping):
            raise SemanticChoiceError("Private library imprint requires both exile and bottom partitions")
        if uses_private_hand_bottom_exile(effect) and not isinstance(raw_cards, Mapping):
            raise SemanticChoiceError("Submit both the private hand selection and the ordered remainder")
        if effect.get("schema_version") == PRIVATE_ONE_HAND_ORDERED_BOTTOM_VERSION and not isinstance(raw_cards, Mapping):
            raise SemanticChoiceError("Submit both the private hand selection and the bottom order")
        if effect.get("schema_version") == FIXED_UP_TO_TWO_HAND_SELECTION_VERSION:
            if (effect["remainder_order"] == "chosen") != isinstance(raw_cards, Mapping):
                raise SemanticChoiceError("Submit only the represented hand selection and ordering fields")
        if isinstance(raw_cards, Mapping):
            if effect.get("schema_version") in TOP_RANDOM_BOTTOM_VERSIONS:
                raise SemanticChoiceError("The private top selection accepts only the card to keep on top")
            destination = 'exile' if effect['remainder_destination']=='exile' else (
                "graveyard"
                if effect["remainder_destination"] == "graveyard"
                else "bottom"
            )
            selected_key=('exile' if imprint else 'top' if effect.get('selected_destination')=='library_top' else
                'battlefield' if effect.get('selected_destination')=='battlefield' else 'hand')
            if uses_private_hand_bottom_exile(effect):
                destination = "remainder"
            if set(raw_cards) != {selected_key, destination}:
                raise SemanticChoiceError(
                    "Library partition destinations are malformed"
                )
            try:
                selected = partition_refs(
                    raw_cards[selected_key],
                    field='cards.'+selected_key,
                )
                remainder = partition_refs(
                    raw_cards[destination],
                    field=f"cards.{destination}",
                )
            except LibraryPartitionError as exc:
                raise SemanticChoiceError(str(exc)) from exc
        else:
            try:
                selected = partition_refs(raw_cards, field="cards")
            except LibraryPartitionError as exc:
                raise SemanticChoiceError(str(exc)) from exc
            selected_set = set(selected)
            remainder = tuple(
                ref for ref in looked_refs if ref not in selected_set
            )
        if (
            len(selected) < int(effect["_effective_minimum"])
            or len(selected) > int(effect["_effective_maximum"])
        ):
            raise SemanticChoiceError(
                "Library selection cardinality is not authoritative"
            )
        policy = str(effect["selection_policy"])
        eligible = tuple(
            tuple(str(ref) for ref in group)
            for group in effect.get("_eligible_groups", ())
        )
        if policy in {"fixed_any",'up_to_any'}:
            valid_selection = all(ref in looked_refs for ref in selected)
        elif policy == "optional_slots":
            valid_selection = _slot_assignment_exists(selected, eligible)
        else:
            legal = set(eligible[0]) if eligible else set()
            valid_selection = all(ref in legal for ref in selected)
            if policy == "all_matching":
                valid_selection = valid_selection and set(selected) == legal
        if not valid_selection:
            raise SemanticChoiceError(
                "Selected cards do not satisfy the issued characteristics"
            )
        try:
            arrangement = _arrangement(effect, looked, selected, remainder)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if uses_damage_library_cast_selection(effect):
            return SemanticChoiceCompletion(repeat_effect=FrozenMap({**effect, DAMAGE_SELECTED: arrangement.selected_refs[0]}))
        if represents_window(effect):
            return SemanticChoiceCompletion(
                intents=(self._intent(effect, arrangement, stack_ref=continuation.stack_ref),),
                prepend_effects=exile_offer(looked, selected, actor=effect["_choice_actor"]),
            )
        intent = self._intent(effect, arrangement, stack_ref=continuation.stack_ref)
        if uses_private_hand_bottom_exile(effect):
            from .library_play_partition import private_hand_bottom_exile_intents

            return SemanticChoiceCompletion(intents=private_hand_bottom_exile_intents(intent))
        return SemanticChoiceCompletion(intents=(intent,))


FIXED_LIBRARY_SELECTION_CHOICE_HANDLERS = (
    FixedLibrarySelectionChoiceHandler(),
)


__all__ = [
    "FIXED_LIBRARY_SELECTION_CHOICE_HANDLERS",
    "FixedLibrarySelectionChoiceHandler",
]
