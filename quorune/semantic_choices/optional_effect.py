from __future__ import annotations

"""Generic resolution-time choice for one already represented effect clause."""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..compiler.optional_effect_templates import OPTIONAL_EFFECT_OPERATION
from ..rules.fixed_homogeneous_target_set_capability_shapes import (
    OPTIONAL_GRAVEYARD_TARGET_RETURN_VERSION, validate_optional_graveyard_target_return,
)
from ..rules.hand_inspection import (
    DEFENDING_HAND_OBSERVATION_VERSION_FIELD, is_defending_hand_observation,
)
from ..rules.optional_draw_discard import (
    is_source_shared_type_draw_discard, validate_source_shared_type_draw_discard,
)
from ..rules.optional_targeted_control import is_optional_targeted_control, validate_optional_targeted_control
from ..rules.landfall_life_loss_counters import (
    uses_optional_loss_source_counters, validate_optional_loss_source_counters,
)
from ..rules.source_upkeep_handoff import uses_source_upkeep_handoff
from .source_upkeep_handoff import complete_source_upkeep_handoff, prepare_source_upkeep_handoff
from ..replacement.immutable import FrozenMap, freeze_value
from ..rules.discarded_card_exile import (
    discarded_card_play_available, discarded_card_play_choice, uses_discarded_card_play,
)
from ..linked_exile_return_model import validate_linked_instruction
from ..library_search_model import (
    FixedCountedLibrarySearchTemplate,
    SubtypeGraveyardSearchSpec,
    is_counted_library_search,
    is_subtype_graveyard_search,
)
from ..rules.event_outcome_shapes import (
    cast_reveal_counter_program_dependencies,
    uses_cast_reveal_counter,
    validate_cast_reveal_counter,
)
from ..semantic_runtime.intents import RevealLibraryCardsIntent
from ..semantic_runtime import SemanticNodeError, default_semantic_interpreter
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _represented_payment_player_selection(effect, *, actor, query):
    """Check a resolved affected-player choice without inspecting its objects."""
    players=effect.get('players')
    if (effect.get('actor')!=actor or not isinstance(players,(list,tuple))
            or any(type(player)is not str or player not in query.seats for player in players)
            or len(players)!=len(set(players))):
        raise SemanticChoiceError('Optional affected-player selection has an invalid origin or participant')
    restored={**effect,'actor':'$controller','players':['$controller']}
    if effect.get('then')=='discard':
        from ..rules.affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities
        owned=fixed_affected_player_discard_node_capabilities(effects=(restored,),target_schema=None,
            mechanic_ids=('fixed-affected-player-discard','cr-402-hand'),allow_controller=True)
    elif effect.get('then')=='sacrifice':
        from ..rules.affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities
        owned=fixed_affected_player_sacrifice_node_capabilities(effects=(restored,),target_schema=None,
            mechanic_ids=('fixed-affected-player-sacrifice','sacrifice'))
    else:owned=()
    if not owned:raise SemanticChoiceError('Optional affected-player selection is outside its closed primitive')


def _represented_targeted_player_discard(effect, *, actor, query, allow_targeted_four=False):
    from ..rules.affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities
    players=effect.get('players');target=effect.get('target')
    if (effect.get('actor')!=actor or not isinstance(players,(list,tuple)) or len(players)!=1
            or type(target)is not str or target not in query.active_seats or tuple(players)!=(target,)):
        raise SemanticChoiceError('Optional targeted discard requires its one bound affected player')
    symbolic={**effect,'actor':'$controller','players':['$target.0'],'target':'$target.0'}
    schema={'zones':['player'],'categories':['player'],'player_relation':'any','count':1}
    if not fixed_affected_player_discard_node_capabilities(effects=(symbolic,),target_schema=schema,
            mechanic_ids=('fixed-affected-player-discard','cr-402-hand','cr-115-targets'),
            allow_targeted_four=allow_targeted_four):
        raise SemanticChoiceError('Targeted discard lacks its closed result owner')


def _represented_linked_library_effect(effect) -> bool:
    from ..linked_library_imprint_model import (
        uses_linked_artifact_library_instruction, validate_linked_artifact_library_instruction,
        uses_linked_saga_library_instruction, linked_saga_library_phase,
    )

    if uses_linked_saga_library_instruction(effect):
        try:
            if linked_saga_library_phase(effect) != "enter_permanents":
                raise ValueError("Only the linked Saga battlefield branch is optional")
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return True
    if uses_linked_artifact_library_instruction(effect):
        try:
            validate_linked_artifact_library_instruction(effect)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return True
    return False


def _represented_effect(
    effect: Mapping[str, Any],
    *,
    actor: str,
    query: SemanticChoiceQuery,
    allow_event_player_results: bool = False,
) -> None:
    operation = effect.get("op")
    if type(operation) is not str or not operation:
        raise SemanticChoiceError(
            "Optional effect instructions require a named operation"
        )
    if operation == OPTIONAL_EFFECT_OPERATION:
        raise SemanticChoiceError("Optional effects cannot nest")
    if _represented_linked_library_effect(effect):
        return
    if DEFENDING_HAND_OBSERVATION_VERSION_FIELD in effect:
        defender = effect.get("target")
        if defender not in query.seats or not is_defending_hand_observation(
            effect, actor=actor, defender=defender,
        ):
            raise SemanticChoiceError("Optional hand observation requires its bound defending player")
        return
    if operation == "move_if_in_zone":
        from ..event_card_return import is_event_library_move, validate_event_library_move

        if is_event_library_move(effect):
            try:
                validate_event_library_move(effect, resolved=True)
            except (TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            return
    if operation == "attach":
        from ..rules.attachment_actions import (
            ENTRY_ATTACHMENT_VERSION, TARGETED_EQUIPMENT_VERSION,
            validate_entry_equipment_attachment, validate_targeted_equipment_attachment,
        )

        if TARGETED_EQUIPMENT_VERSION in effect or ENTRY_ATTACHMENT_VERSION in effect:
            validate = (validate_targeted_equipment_attachment if TARGETED_EQUIPMENT_VERSION in effect
                        else validate_entry_equipment_attachment)
            try:
                validate(effect, resolved=True)
            except ValueError as exc:
                raise SemanticChoiceError(str(exc)) from exc
            return
    if operation == "discard_random_cards":
        from ..random_discard import random_discard_node_capabilities
        restored = {**effect, "actor": "$controller", "players": ["$controller"]}
        if (effect.get("actor") != actor or not isinstance(effect.get("players"), (list, tuple))
                or len(effect["players"])!=1 or effect["players"][0] not in query.active_seats
                or not random_discard_node_capabilities(effects=(restored,), target_schema=None,
                    mechanic_ids=("fixed-random-discard", "cr-402-hand"))):
            raise SemanticChoiceError("Optional single-player random discard instruction is malformed")
        return
    if operation == "choose_cards_apnap":
        if 'target' in effect and effect.get('then')=='discard':
            _represented_targeted_player_discard(effect,actor=actor,query=query)
            return
        if allow_event_player_results and effect.get('players') not in ([actor],(actor,)):
            _represented_payment_player_selection(effect,actor=actor,query=query)
            return
        from ..rules.affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities
        restored={**effect,'actor':'$controller','players':['$controller']}
        if effect.get('actor')!=actor or not isinstance(effect.get('players'),(list,tuple)) or list(effect['players'])!=[actor] or not fixed_affected_player_discard_node_capabilities(effects=(restored,),target_schema=None,mechanic_ids=('fixed-affected-player-discard','cr-402-hand'),allow_controller=True):
            raise SemanticChoiceError("Optional controller discard instruction is malformed")
        return
    from ..rules.searched_library_pairs import is_searched_pair_search, validate_searched_pair_search

    if is_searched_pair_search(effect):
        try:
            validate_searched_pair_search(effect)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return
    if operation == "search" and effect.get('schema_version') == 7:
        from ..qualified_library_search_model import QualifiedLibrarySearchSpec
        try:QualifiedLibrarySearchSpec.from_effect(effect)
        except (ValueError, TypeError, KeyError) as exc:raise SemanticChoiceError("Optional qualified search is malformed") from exc
        return
    if operation == "search":
        from ..named_search_model import (
            LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION, decode_literal_library_search,
        )

        if effect.get("schema_version") in (LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION):
            try:
                decode_literal_library_search(effect)
            except ValueError as exc:
                raise SemanticChoiceError(str(exc)) from exc
            return
    from ..library_search_model import (
        COMMITTED_ATTACKING_EXILE_SEARCH_VERSION, validate_committed_attacking_exile_search,
    )
    if operation == "search" and effect.get("schema_version") == COMMITTED_ATTACKING_EXILE_SEARCH_VERSION:
        try:
            validate_committed_attacking_exile_search(effect)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if effect["searching_player"] != actor:
            raise SemanticChoiceError("The original affected player must choose whether to search")
        return
    if operation == "search" and is_counted_library_search(effect):
        try:
            (SubtypeGraveyardSearchSpec if is_subtype_graveyard_search(effect) else FixedCountedLibrarySearchTemplate).from_effect(effect)
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError("Optional counted search is malformed") from exc
        return
    if operation == 'search':
        from ..rules.library_search_capability_shapes import fixed_library_search_node_capabilities, fixed_type_to_hand_search_node_capabilities
        if "searching_player" in effect:
            if effect["searching_player"] != actor:
                raise SemanticChoiceError("Optional search must use the chooser's library")
            effect = {key: value for key, value in effect.items() if key != "searching_player"}
        mechanics=('fixed-library-search-to-battlefield','fixed-library-search-to-hand')
        if not any(resolver(effects=(effect,),target_schema=None,mechanic_ids=mechanics)
                for resolver in (fixed_library_search_node_capabilities,fixed_type_to_hand_search_node_capabilities)):
            raise SemanticChoiceError('Optional fixed search is malformed')
        return
    try:
        plan = default_semantic_interpreter().lower_for_seats(
            effect,
            actor=actor,
            default_reason="Optional effect",
            seats=query.seats,
            active_seats=query.active_seats,
            apnap_order=query.active_seats,
            source=getattr(query, 'resolution_source', None),
        )
    except SemanticNodeError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if plan is not None:
        return
    from .defaults import default_semantic_choice_registry

    try:
        default_semantic_choice_registry().handler_for_operation(operation)
    except SemanticChoiceError as exc:
        raise SemanticChoiceError(
            "Optional effect instruction is not represented"
        ) from exc


def optional_choice_uses_draw_permission(effect: Mapping[str, Any]) -> bool:
    from ..permanent_event_state_model import optional_draw_proliferate_is_closed
    from ..rules.optional_draw_discard import is_entry_exempt_draw_discard

    from ..rules.draw_step_drawing_settlement import DRAW_STEP_SETTLEMENT_VERSION_FIELD

    operation = effect.get("op")
    return (operation == "offer_draw" or is_source_shared_type_draw_discard(effect)
            or is_entry_exempt_draw_discard(effect)
            or (operation == "choose_option" and DRAW_STEP_SETTLEMENT_VERSION_FIELD in effect)
            or (operation == "offer_optional_effect" and optional_draw_proliferate_is_closed(
                effect.get("effects"), player=effect.get("player"),
            )))


def _validated_effects(
    effect: Mapping[str, Any],
    *,
    actor: str,
    query: SemanticChoiceQuery,
) -> tuple[str, tuple[FrozenMap, ...]]:
    from ..rules.entry_creature_type_mana import is_optional_entry_shared_type_mana, validate_bound_entry_shared_type_mana
    from ..rules.optional_draw_discard import is_entry_exempt_draw_discard, validate_entry_exempt_draw_discard

    if uses_optional_loss_source_counters(effect):
        try:
            effects = validate_optional_loss_source_counters(effect, actor=actor, active_seats=query.active_seats)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return actor, effects
    shared_source = is_source_shared_type_draw_discard(effect)
    targeted_control = is_optional_targeted_control(effect)
    shared_entrant_mana = is_optional_entry_shared_type_mana(effect)
    entry_exempt = is_entry_exempt_draw_discard(effect)
    expected = {"op", "player", "effects"}
    graveyard_return = (
        isinstance(effect, Mapping) and effect.get("schema_version") == OPTIONAL_GRAVEYARD_TARGET_RETURN_VERSION
    )
    if shared_source or graveyard_return or targeted_control or shared_entrant_mana or entry_exempt:
        expected.add("schema_version")
    if not isinstance(effect, Mapping) or set(effect) != expected:
        raise SemanticChoiceError("Optional effect fields are malformed")
    if effect.get("op") != OPTIONAL_EFFECT_OPERATION:
        raise SemanticChoiceError("Optional effect operation is invalid")
    player = effect.get("player")
    if (
        type(player) is not str
        or player != actor
        or player not in query.active_seats
    ):
        raise SemanticChoiceError(
            "Optional effect must be issued to its active controller"
        )
    values = effect.get("effects")
    if uses_discarded_card_play(effect):
        try:
            return player, (FrozenMap(discarded_card_play_choice(effect, actor=player)),)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
    if uses_cast_reveal_counter(effect):
        try:
            validate_cast_reveal_counter(effect, actor=player)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return player, tuple(FrozenMap(value) for value in values)
    if entry_exempt:
        try:
            return player, tuple(FrozenMap(value) for value in validate_entry_exempt_draw_discard(effect, actor=player))
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
    if shared_entrant_mana:
        try:
            child = validate_bound_entry_shared_type_mana(effect, player)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return player, (FrozenMap(child),)
    if targeted_control:
        try:
            target = validate_optional_targeted_control(effect, actor=player, resolved=True)
        except (ValueError, TypeError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if query.object(target, zones=("battlefield",)) is None:
            raise SemanticChoiceError("The original optional control target is unavailable")
        return player, tuple(FrozenMap(value) for value in values)
    if graveyard_return:
        try:
            validate_optional_graveyard_target_return(effect, player=player, resolved=True)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
    if shared_source:
        try:
            validate_source_shared_type_draw_discard(effect, actor=player)
        except (ValueError, TypeError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return player, tuple(FrozenMap(value) for value in values)
    linked_pair = (
        isinstance(values, Sequence) and not isinstance(values, (str, bytes))
        and len(values) == 2 and all(isinstance(value, Mapping) for value in values)
        and values[0].get("phase") == "exile" and values[1].get("phase") == "return"
        and values[0].get("op") == "linked_exile_return" and values[1].get("op") == "linked_exile_return"
        and values[0].get("binding_id") == values[1].get("binding_id")
        and values[0].get("spec") == values[1].get("spec")
    )
    if linked_pair:
        try:
            for value in values:
                validate_linked_instruction(value)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
    from ..rules.optional_draw_discard import optional_draw_discard_is_closed
    draw_discard=optional_draw_discard_is_closed(values,player=player)
    from ..permanent_event_state_model import optional_draw_proliferate_is_closed

    draw_proliferate = optional_draw_proliferate_is_closed(values, player=player)
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes))
        or (len(values) != 1 and not linked_pair and not draw_discard and not draw_proliferate)
        or any(not isinstance(value, Mapping) for value in values)
    ):
        raise SemanticChoiceError(
            "Optional effect requires one represented instruction"
        )
    frozen: list[FrozenMap] = []
    for value in values:
        _represented_effect(value, actor=player, query=query)
        candidate = freeze_value(value)
        if not isinstance(candidate, FrozenMap):
            raise SemanticChoiceError(
                "Optional effect continuation is malformed"
            )
        frozen.append(candidate)
    return player, tuple(frozen)


def _unavailable_defending_hand(effects, query):
    return (len(effects) == 1 and DEFENDING_HAND_OBSERVATION_VERSION_FIELD in effects[0]
            and effects[0]["target"] not in query.active_seats)


def require_cast_reveal_choice_source(host, item, actor, effect):
    if not uses_cast_reveal_counter(effect):
        return
    try:
        stack, mana_value = validate_cast_reveal_counter(effect, actor=actor)
        if (item is None or item.kind != "triggered_ability" or item.controller != actor
                or item.context.get("currently_resolving") is not True or item.context.get("event") != "spell.cast"
                or item.targets or item.context.get("stack") != stack or item.context.get("mana_value") != mana_value
                or type(item.context.get("mana_value")) is not type(mana_value)):
            raise ValueError("The reveal choice lost its resolving trigger or original committed cast")
        program = host.semantics.get(item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or not cast_reveal_counter_program_dependencies(program)):
            raise ValueError("The reveal choice lost its exact opponent-cast program")
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _complete_cast_reveal_counter(continuation, actor, choice, query):
    if actor != continuation.semantic_frame.controller:
        raise SemanticChoiceError("The resolving controller owns the optional reveal")
    if choice == "decline":
        return SemanticChoiceCompletion()
    stack, mana_value = validate_cast_reveal_counter(continuation.effect, actor=actor)
    refs = query.library_refs(actor, top_first=True)
    top = query.object(refs[0], zones=("library",)) if refs else None
    if top is None or top.owner != actor or not top.physical_card:
        raise SemanticChoiceError("The resolving player cannot reveal a current library top")
    label = continuation.note or "Reveal the top card"
    reveal = RevealLibraryCardsIntent(
        actor, actor, actor, (top.ref,), label, public=True, announce_public_top=True,
    )
    counter = ({"op": "counter_stack_target", "stack": stack},) if (
        top.mana_value == mana_value and query.stack_object(stack) is not None
    ) else ()
    return SemanticChoiceCompletion(intents=(reveal,), prepend_effects=counter)


@dataclass(frozen=True, slots=True)
class OptionalEffectHandler:
    """Offer one choice before resuming ordinary typed effect resolution."""

    operation: str = OPTIONAL_EFFECT_OPERATION
    handler_id: str = "choice.effect.optional-fixed.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 109.5",
        "CR 608.2c",
        "CR 608.2d",
        "CR 609.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "effect.choice.optional_fixed",
    )
    continuation_fields: tuple[str, ...] = ("player", "effects", "schema_version")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "SemanticChoiceCompletion.prepend_effects",
        "CommanderEngine._continue_resolution",
    )
    replay_fixture: str = "fixed-optional-effect-choice"
    test_modules: tuple[str, ...] = (
        "tests.test_fixed_optional_effect_choices",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        from ..rules.entry_creature_type_mana import is_optional_entry_shared_type_mana
        from ..rules.optional_draw_discard import is_entry_exempt_draw_discard

        if uses_source_upkeep_handoff(effect):
            return prepare_source_upkeep_handoff(effect, context)
        if ((is_optional_targeted_control(effect) or uses_optional_loss_source_counters(effect))
                and context.actor != context.stack_controller):
            raise SemanticChoiceError("The resolving controller owns this optional choice")
        if is_optional_entry_shared_type_mana(effect) and context.actor != context.stack_controller:
            raise SemanticChoiceError("The resolving controller owns the optional mana choice")
        player, effects = _validated_effects(
            effect,
            actor=context.actor,
            query=context.query,
        )
        if uses_discarded_card_play(effect):
            if player != context.stack_controller:
                raise SemanticChoiceError("The resolving controller owns the discard-exile choice")
            if not discarded_card_play_available(effects[0], actor=player, query=context.query):
                return SemanticChoicePreparation(
                    request=None, continuation_effect=FrozenMap(effect),
                    auto_continue=AutoContinue("The original discarded card is unavailable in its graveyard"),
                )
        if (uses_optional_loss_source_counters(effect)
                and not context.query.life_gain_permissions.allows_loss(effects[0]["player"])):
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("The targeted player cannot lose life"),
            )
        cast_reveal = uses_cast_reveal_counter(effect)
        if cast_reveal and not context.query.library_refs(player, top_first=True):
            return SemanticChoicePreparation(None, FrozenMap(effect),
                auto_continue=AutoContinue("The optional reveal has an empty library"))
        shared_source = is_source_shared_type_draw_discard(effect)
        if ((shared_source or is_entry_exempt_draw_discard(effect))
                and not context.query.draw_permission(player).allows_complete_draw(effects[0]["count"])):
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("The player cannot legally choose that draw"),
            )
        from ..rules.announced_destruction_controller_tokens import uses_optional_graveyard_controller_tokens

        if (len(effects) == 1 and uses_optional_graveyard_controller_tokens(effects[0])
                and not effects[0]["cards"]):
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("The optional destruction has no announced target"),
            )
        if effect.get("schema_version") == OPTIONAL_GRAVEYARD_TARGET_RETURN_VERSION and not effects[0]["cards"]:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("The optional return has no announced card"),
            )
        if _unavailable_defending_hand(effects, context.query):
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("The defending player is no longer in the game"),
            )
        if _prohibited_optional_library_search(effects,player,context.query):
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='The optional library search cannot be chosen'))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Reveal the top card of your library?" if cast_reveal else "Apply the optional effect?",
                choice=ScalarChoice(
                    field_name="choice",
                    legal_values=("apply", "decline"),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    "op": self.operation,
                    "player": player,
                    "effects": effects,
                    **({"schema_version": effect["schema_version"]} if "schema_version" in effect else {}),
                }
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        from ..rules.entry_creature_type_mana import is_optional_entry_shared_type_mana
        from ..rules.optional_draw_discard import is_entry_exempt_draw_discard

        if uses_source_upkeep_handoff(continuation.effect):
            return complete_source_upkeep_handoff(continuation, response, query)
        actor = continuation.effect.get("player")
        if type(actor) is not str:
            raise SemanticChoiceError(
                "Optional effect continuation chooser is malformed"
            )
        life_counter = uses_optional_loss_source_counters(continuation.effect)
        if (is_optional_targeted_control(continuation.effect) or life_counter) and actor != continuation.semantic_frame.controller:
            raise SemanticChoiceError("The original resolving controller owns this optional choice")
        if uses_discarded_card_play(continuation.effect) and actor != continuation.semantic_frame.controller:
            raise SemanticChoiceError("The original resolving controller owns the discard-exile choice")
        shared_entrant_mana = is_optional_entry_shared_type_mana(continuation.effect)
        if shared_entrant_mana and actor != continuation.semantic_frame.controller:
            raise SemanticChoiceError("The original resolving controller owns the optional mana choice")
        _player, effects = _validated_effects(
            continuation.effect,
            actor=actor,
            query=query,
        )
        choice = response.get("choice")
        if type(choice) is not str or choice not in {"apply", "decline"}:
            raise SemanticChoiceError("Choose apply or decline")
        if uses_cast_reveal_counter(continuation.effect):
            return _complete_cast_reveal_counter(continuation, actor, choice, query)
        if choice == "apply" and _unavailable_defending_hand(effects, query):
            raise SemanticChoiceError("The defending player is no longer in the game")
        if choice=='apply' and _prohibited_optional_library_search(effects,actor,query):raise SemanticChoiceError('The optional library search cannot be chosen')
        from ..permanent_event_state_model import optional_draw_proliferate_is_closed

        if (choice == "apply" and optional_draw_proliferate_is_closed(effects, player=actor)
                and not query.draw_permission(actor).allows_complete_draw(1)):
            raise SemanticChoiceError("The player can no longer legally choose that draw")
        if choice == "apply" and is_source_shared_type_draw_discard(continuation.effect):
            count = effects[0]["count"]
            if not query.draw_permission(actor).allows_complete_draw(count):
                raise SemanticChoiceError("The player can no longer legally choose that draw")
            if count == 0:
                effects = effects[1:]
        if (choice == "apply" and is_entry_exempt_draw_discard(continuation.effect)
                and not query.draw_permission(actor).allows_complete_draw(1)):
            raise SemanticChoiceError("The player can no longer legally choose that draw")
        if life_counter and choice == "apply":
            loss, counters = effects
            if not query.life_gain_permissions.allows_loss(loss["player"]):
                raise SemanticChoiceError("The targeted player can no longer lose life")
            if counters["card"] is None:
                effects = (loss,)
        if shared_entrant_mana and effects[0]["amount"] == 0:
            effects = ()
        return SemanticChoiceCompletion(
            prepend_effects=effects if choice == "apply" else ()
        )


def _prohibited_optional_library_search(effects,player,query):
    if len(effects)!=1 or effects[0].get('op')!='search':return False
    zone=effects[0].get('zone','library')
    if zone!='library' and zone!=('library',) and zone!=['library']:return False
    return not query.library_search_allowed(effects[0].get('searching_player') or player)


OPTIONAL_EFFECT_CHOICE_HANDLERS = (OptionalEffectHandler(),)


__all__ = ["OPTIONAL_EFFECT_CHOICE_HANDLERS", "OptionalEffectHandler"]
