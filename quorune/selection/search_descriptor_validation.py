from __future__ import annotations

"""Validate hidden search descriptions and their public-result disclosure."""

from typing import Any, Mapping, Sequence

from ..errors import GameRuleError
from ..public_library_piles_model import (
    DISTINCT_PUBLIC_SEARCH_SPLIT_VERSION, is_public_search_split, validate_public_search_split,
)
from ..rules.targeted_library_exile import (
    OPPONENT_LIBRARY_EXILE_SEARCH_CAPABILITIES, TARGET_FIXED_LIBRARY_EXILE_VERSIONS,
    validate_bound_target_name_multizone_search,
)
from ..library_search_model import (
    ANY_NUMBER_CREATURE_BATTLEFIELD_VERSIONS,
    CONDITIONAL_UNTAP_SEARCH_VERSION, QUERY_BOUND_SEARCH_VERSION, TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION,
    CAST_X_ZONE_SEARCH_VERSION,
    EXILED_TARGET_SEARCH_VERSION,
    CREATURE_SUBSET_MANA_VALUE_FIELD, CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION,
    FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION, COMMITTED_ATTACKING_EXILE_SEARCH_VERSION,
    COMMITTED_LAND_SEARCH_VERSION,
    RECRUITING_LIBRARY_SEARCH_VERSION, ANY_NUMBER_SUBTYPE_BATTLEFIELD_VERSION, SUBTYPE_GRAVEYARD_SEARCH_VERSION, SubtypeGraveyardSearchSpec, is_subtype_graveyard_search,
    SEARCHED_LAND_UNTAP_VERSION, SearchedLandUntapSearch,
    TARGET_NAME_LIBRARY_SEARCH_VERSION, TARGET_NAME_MULTIZONE_EXILE_VERSION,
    TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION,
    PRIVATE_SEARCH_RANDOM_DISCARD_VERSION,
    DRAGON_GRAVEYARD_COPY_SEARCH_VERSION,
    SEARCH_ALTERNATIVES,
    is_bargain_search_effect, is_up_to_two_forest_search,
    AnyNumberCreatureSubtypeSearch,
    CreatureSubsetManaValueSearch,
    FixedCountedLibrarySearchTemplate,
    RecruitingLibrarySearchSpec,
    SACRIFICE_BOUND_CREATURE_SEARCH_VERSION,
    TargetNameLibrarySearchSpec,
    is_counted_library_search, is_ordered_creature_graveyard_search,
    validate_fixed_creature_group_search, validate_committed_attacking_exile_search,
    validate_target_player_basic_land_search,
    validate_private_search_before_random_discard,
)
from ..named_search_model import (
    LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION,
    LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION,
    decode_literal_library_search,
)
from ..rules.bargain_search import bargain_search_limit
from ..rules.shared_land_type_search import shared_land_type_search_choice


_SEARCH_COMPLETION_FIELD = "_search_completion"
_REPLACEMENT_SELECTIONS_FIELD = "_replacement_selections"


def semantic_search_selector_is_restrictive(selector: Mapping[str, Any]) -> bool:
    return any(
        selector.get(key)
        for key in (
            "types", "types_any", "subtypes", "subtypes_any", "supertypes", "colors", "colors_any",
            "names", "mana_value", "mana_value_total", "predicate", SEARCH_ALTERNATIVES,
        )
    )


def semantic_search_actor(
    item: Any, effect: Mapping[str, Any], *, decision_actor: str | None = None,
) -> str:
    actor = str(effect.get("searching_player") or item.controller)
    if effect.get("schema_version") == CAST_X_ZONE_SEARCH_VERSION and decision_actor not in (None, item.controller):
        raise GameRuleError("Announced-X search belongs to the resolving controller")
    if effect.get("schema_version") == TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION:
        if len(item.targets) != 1 or item.targets[0] != actor:
            raise GameRuleError("The basic-land search lost its original targeted player")
        if decision_actor is not None and decision_actor != actor:
            raise GameRuleError("The targeted basic-land search decision belongs to another player")
    return actor


def validate_semantic_search_response(effect: Mapping[str, Any], response: Mapping[str, Any]) -> None:
    from ..rules.searched_library_pairs import is_searched_pair_search

    if is_searched_pair_search(effect) and (
        set(response) - {"action", "action_id", "choice_schema", "search_cards"}
        or not isinstance(response.get("search_cards"), (list, tuple))
    ):
        raise GameRuleError("A searched pair accepts only its private search cards field")
    if effect.get("schema_version") == SEARCHED_LAND_UNTAP_VERSION and (
        {"entry_pay_life", "pay_life"} & set(response)
    ):
        raise GameRuleError("The tapped basic-land search does not offer an entry payment")
    if is_public_search_split(effect) and (
        set(response) - {"action", "choice_schema", "search_cards"} or "search_cards" not in response
    ):
        raise GameRuleError("The public split search accepts only the issued private search choice")
    from ..rules.affected_library_search_batch import is_exile_land_search

    if is_exile_land_search(effect) and (
        set(response) - {"action", "action_id", "choice_schema", "search_cards"}
        or not isinstance(response.get("search_cards", ()), (list, tuple))
    ):
        raise GameRuleError("Choose only the offered basic land cards or an empty selection")
    if effect.get("schema_version") == DRAGON_GRAVEYARD_COPY_SEARCH_VERSION and (
        set(response) - {"action", "choice_schema", "search_cards"}
        or ("search_cards" in response and response["search_cards"] is None)
    ):
        raise GameRuleError("The Dragon search accepts only its offered search_cards field")
    version = effect.get("schema_version")
    if version in {
        CONDITIONAL_UNTAP_SEARCH_VERSION, TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION,
        TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION, *OPPONENT_LIBRARY_EXILE_SEARCH_CAPABILITIES,
    } and (
        set(response) - {"action", "choice_schema", "search_cards"}
        or ("search_cards" in response and response["search_cards"] is None)
    ):
        if version in OPPONENT_LIBRARY_EXILE_SEARCH_CAPABILITIES:
            raise GameRuleError("The private opponent search accepts only its offered search_cards field")
        if version == CONDITIONAL_UNTAP_SEARCH_VERSION:
            raise GameRuleError("A conditional-untap search accepts only the chosen basic land or an empty selection")
        raise GameRuleError("A targeted basic-land search accepts only the chosen card or an empty selection")


def validate_explicit_search_cards(values):
    if values is not None and (
        not isinstance(values, Sequence) or isinstance(values, (str, bytes))
        or any(not isinstance(value, str) for value in values)
    ):
        raise GameRuleError("search_cards must be an array of card-ref strings from choice_schema.legal_refs")


def semantic_search_destination(effect: Mapping[str, Any]) -> tuple[str, str, str | int]:
    if effect.get("schema_version") == 5:
        return "allocated", "hand", "top"
    destination_spec = str(effect.get("destination") or "hand")
    position: str | int = effect.get("destination_position", "top")
    if position is None or position == "":
        position = "top"
    destination = destination_spec
    if destination_spec in {"library_top", "top_of_library"}:
        destination, position = "library", "top"
    elif destination_spec in {"library_bottom", "bottom_of_library"}:
        destination, position = "library", "bottom"
    if destination not in {"hand", "battlefield", "graveyard", "exile", "library"}:
        raise GameRuleError(f"Unsupported semantic search destination {destination_spec!r}")
    return destination_spec, destination, position


def _validate_search_land_untap(effect: Mapping[str, Any]) -> None:
    retained = {
        key: value for key, value in effect.items()
        if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}
    }
    try:
        SearchedLandUntapSearch.from_effect(retained)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc


def validated_search_completion(effect):
    from ..conditional_search_model import OPTIONAL_CONDITIONAL_PLACEMENT_FIELD, OPTIONAL_CONDITIONAL_SEARCH_VERSION
    from .forest_search_entry import FOREST_ENTRY_PAID_REFS

    fields = {"actor", "choice_id", "selected_refs"}
    raw_completion = effect.get(_SEARCH_COMPLETION_FIELD)
    if (
        not isinstance(raw_completion, Mapping)
        or set(raw_completion) not in (
            fields,
            fields | {"entry_pay_life"},
            fields | {FOREST_ENTRY_PAID_REFS},
            fields | {OPTIONAL_CONDITIONAL_PLACEMENT_FIELD},
        )
    ):
        raise GameRuleError("Malformed semantic search completion")
    if ((OPTIONAL_CONDITIONAL_PLACEMENT_FIELD in raw_completion)
            != (effect.get("schema_version") == OPTIONAL_CONDITIONAL_SEARCH_VERSION)):
        raise GameRuleError("The optional placement completion requires its matching search version")
    actor = raw_completion.get("actor")
    choice_id = raw_completion.get("choice_id")
    raw_selected = raw_completion.get("selected_refs")
    if (
        not isinstance(actor, str)
        or not actor
        or not isinstance(choice_id, str)
        or not choice_id
        or not isinstance(raw_selected, Sequence)
        or isinstance(raw_selected, (str, bytes))
        or any(
            not isinstance(value, str) or not value
            for value in raw_selected
        )
    ):
        raise GameRuleError("Malformed semantic search completion identity")
    return raw_completion, actor, choice_id, raw_selected


def _validate_linked_and_paid_search(effect):
    if effect.get("schema_version") == COMMITTED_LAND_SEARCH_VERSION:
        from ..rules.committed_land_sacrifice_search import validate_resolved_land_sacrifice_search

        retained = {key: value for key, value in effect.items()
                    if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}}
        try:
            validate_resolved_land_sacrifice_search(retained)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return True
    from ..rules.paid_artifact_library_search import is_paid_artifact_search, validate_paid_artifact_search_effect

    from ..rules.paid_creature_library_search import is_paid_creature_search, validate_paid_creature_search

    paid_validator = (validate_paid_artifact_search_effect if is_paid_artifact_search(effect) else
                      validate_paid_creature_search if is_paid_creature_search(effect) else None)
    if paid_validator is not None:
        retained = {key: value for key, value in effect.items()
                    if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}}
        try:
            paid_validator(retained, materialized=True)
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
        return True
    from ..linked_library_imprint_model import (
        uses_linked_artifact_library_instruction, validate_linked_artifact_library_instruction,
        uses_linked_saga_library_instruction, linked_saga_library_phase,
    )

    if uses_linked_saga_library_instruction(effect):
        retained = {key: value for key, value in effect.items()
                    if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}}
        try:
            if linked_saga_library_phase(retained) != "search":
                raise ValueError("Only the original linked Saga search can issue a private selection")
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
        return True
    if uses_linked_artifact_library_instruction(effect):
        retained = {key: value for key, value in effect.items()
                    if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}}
        try:
            validate_linked_artifact_library_instruction(retained)
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
        return True

    if effect.get("schema_version") == SEARCHED_LAND_UNTAP_VERSION:
        _validate_search_land_untap(effect)
        return True
    return False


def materialize_semantic_search_bound(host, item, effect, instruction_pointer, *, saved_options=False, moving=False):
    # Check the stack-owned bound before issuing or applying recorded candidates.
    from ..rules.searched_library_pairs import is_searched_pair_search, require_resolving_searched_pair

    if is_searched_pair_search(effect):
        require_resolving_searched_pair(host, item, effect)
        return effect
    from ..library_search_model import QUERY_BOUND_SEARCH_VERSION
    from ..rules.paid_artifact_library_search import is_paid_artifact_search, materialize_paid_artifact_search

    retained = {key: value for key, value in effect.items()
                if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}} if moving else effect
    from ..linked_library_imprint_model import uses_linked_saga_library_instruction
    from ..rules.linked_library_imprint import require_linked_artifact_library_search

    if moving and uses_linked_saga_library_instruction(retained):
        require_linked_artifact_library_search(host, item, retained, actor=semantic_search_actor(item, retained))
    if effect.get("schema_version") == COMMITTED_LAND_SEARCH_VERSION:
        from ..rules.committed_land_sacrifice_search import verify_land_sacrifice_search

        try:
            verify_land_sacrifice_search(host, item, retained)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return effect
    if effect.get("schema_version") == CAST_X_ZONE_SEARCH_VERSION:
        from ..rules.cast_x_zone_searches import verify_resolving_cast_x_zone_search

        verify_resolving_cast_x_zone_search(host, item, retained)
        return effect
    from ..rules.private_search_play import uses_private_search_play, verify_resolving_private_search_play

    if uses_private_search_play(effect):
        verify_resolving_private_search_play(host, item, retained)
        return effect
    if is_paid_artifact_search(effect):
        return materialize_paid_artifact_search(host, item, retained, already_materialized=saved_options or moving)
    from ..rules.paid_creature_library_search import is_paid_creature_search, materialize_paid_creature_search

    if is_paid_creature_search(effect):
        return materialize_paid_creature_search(host, item, retained, already_materialized=saved_options or moving)
    if (effect.get("schema_version") == SACRIFICE_BOUND_CREATURE_SEARCH_VERSION
            and not saved_options and not moving and isinstance(effect.get("zone"), (list, tuple))
            and "library" in effect["zone"]):
        from ..rules.library_search_rules import library_search_constraint

        if library_search_constraint(host, item.controller).prohibited:
            raise GameRuleError("Library search is currently prohibited")
    if effect.get("schema_version") == QUERY_BOUND_SEARCH_VERSION and not saved_options:
        from ..rules.query_bound_library_search import materialize_query_bound_search

        return materialize_query_bound_search(host, item, retained, instruction_pointer)
    return effect


def _validate_private_or_cast_x_search(effect: Mapping[str, Any]) -> bool:
    from ..rules.private_search_play import uses_private_search_play, validate_private_search_play

    if uses_private_search_play(effect):
        retained = {key: value for key, value in effect.items()
                    if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}}
        try:
            validate_private_search_play(retained)
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
        return True
    if effect.get("schema_version") == CAST_X_ZONE_SEARCH_VERSION:
        from ..cast_x_search_model import CastXZoneSearchSelection

        retained = {key: value for key, value in effect.items()
                    if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}}
        try:
            CastXZoneSearchSelection.from_effect(retained)
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
        return True
    return False


def _validate_search_zone_binding(effect: Mapping[str, Any]) -> None:
    if effect.get('schema_version') == 8:
        from ..library_search_model import QueryBoundLibrarySearchSpec
        try:QueryBoundLibrarySearchSpec.from_effect({k:v for k,v in effect.items() if k not in {_SEARCH_COMPLETION_FIELD,_REPLACEMENT_SELECTIONS_FIELD}},allow_resolved=True)
        except (ValueError,TypeError,KeyError) as exc:raise GameRuleError('Query-bounded search instruction is malformed') from exc
        return
    if effect.get('schema_version') == 7:
        from ..qualified_library_search_model import QualifiedLibrarySearchSpec
        try:
            QualifiedLibrarySearchSpec.from_effect({key:value for key,value in effect.items() if key != _SEARCH_COMPLETION_FIELD})
        except (ValueError, TypeError, KeyError) as exc:
            raise GameRuleError("Qualified library search instruction is malformed") from exc
        return
    if effect.get('schema_version') == 6:
        from ..named_search_model import decode_named_zone_selection
        try:
            decode_named_zone_selection({key:value for key,value in effect.items() if key != _SEARCH_COMPLETION_FIELD})
        except (ValueError, TypeError, KeyError) as exc:
            raise GameRuleError("Named zone search instruction is malformed") from exc
        return


def _validate_bound_target_library(effect):
    from ..rules.targeted_library_exile import bound_target_library_exile
    bound_target_library_exile(effect, effect.get('searching_player'))
    raw = effect.get('_target_library_identities')
    if raw is not None:
        from ..rules.library_selection import LibrarySelectionObjectIdentity
        if not isinstance(raw, (list, tuple)):
            raise GameRuleError('Target-library identities must be an array')
        try:
            identities = tuple(LibrarySelectionObjectIdentity.from_dict(row) for row in raw)
        except (ValueError, TypeError, KeyError) as exc:
            raise GameRuleError('Target-library identity is malformed') from exc
        if len({row.object_id for row in identities}) != len(identities):
            raise GameRuleError('Target-library identities must be distinct')


def validate_semantic_search_descriptor(effect: Mapping[str, Any]) -> None:
    if effect.get("schema_version") == SACRIFICE_BOUND_CREATURE_SEARCH_VERSION:
        from ..rules.named_searches import sacrifice_bound_creature_search_zones

        retained = {key: value for key, value in effect.items()
                    if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}}
        try:
            sacrifice_bound_creature_search_zones(retained)
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    from ..rules.affected_library_search_batch import is_exile_land_search, validate_exile_land_search

    if is_exile_land_search(effect):
        validate_exile_land_search(effect)
        return
    if _validate_private_or_cast_x_search(effect):
        return

    if is_public_search_split(effect):
        try:
            validate_public_search_split(effect, resolved=True)
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    from ..rules.searched_library_pairs import is_searched_pair_search, validate_searched_pair_search

    if is_searched_pair_search(effect):
        try:
            validate_searched_pair_search(effect)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if _validate_linked_and_paid_search(effect):
        return
    if effect.get("schema_version") == DRAGON_GRAVEYARD_COPY_SEARCH_VERSION:
        from ..rules.dragon_graveyard_search_copy import validate_dragon_graveyard_copy_search

        try:
            validate_dragon_graveyard_copy_search(effect, player=effect.get("searching_player"), bound=True)
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if effect.get("schema_version") == TARGET_NAME_MULTIZONE_EXILE_VERSION:
        validate_bound_target_name_multizone_search(effect)
        return
    if (
        effect.get("schema_version") == CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION
        or CREATURE_SUBSET_MANA_VALUE_FIELD in effect
    ):
        try:
            CreatureSubsetManaValueSearch.from_effect({
                key: value for key, value in effect.items()
                if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}
            })
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if is_bargain_search_effect(effect):
        retained = {key: value for key, value in effect.items()
                    if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}}
        try:
            bargain_search_limit(retained)
        except (ValueError, TypeError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if effect.get("schema_version") == EXILED_TARGET_SEARCH_VERSION:
        from ..exiled_target_library_search import validate_basic_land_search_with_exiled_target

        try:
            validate_basic_land_search_with_exiled_target(effect)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if (type(effect.get("schema_version")) is int and effect["schema_version"]
            in TARGET_FIXED_LIBRARY_EXILE_VERSIONS):
        _validate_bound_target_library(effect)
        return
    if {'library_player', '_target_library_identities'} & set(effect):
        raise GameRuleError('Historical search versions cannot carry target-library roles')
    if effect.get("schema_version") == TARGET_NAME_LIBRARY_SEARCH_VERSION:
        try:
            TargetNameLibrarySearchSpec.from_effect({key: value for key, value in effect.items()
                if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}}, materialized=True)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if effect.get("schema_version") in (LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION):
        try:
            decode_literal_library_search({key: value for key, value in effect.items()
                if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}})
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        return
    version = effect.get("schema_version")
    if version == TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION:
        retained = {key: value for key, value in effect.items()
                    if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}}
        actor = retained.get("searching_player")
        if type(actor) is not str or actor.startswith("$"):
            raise GameRuleError("The targeted basic-land search requires a resolved player")
        try:
            validate_target_player_basic_land_search(retained, searching_player=actor)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if version == PRIVATE_SEARCH_RANDOM_DISCARD_VERSION:
        retained = {key: value for key, value in effect.items()
                    if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}}
        try:
            validate_private_search_before_random_discard(retained)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if version in (FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION, COMMITTED_ATTACKING_EXILE_SEARCH_VERSION):
        retained = {key: value for key, value in effect.items()
                    if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}}
        validate = (validate_fixed_creature_group_search if version == FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION
                    else validate_committed_attacking_exile_search)
        try:
            validate(retained)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if effect.get("schema_version") in (*ANY_NUMBER_CREATURE_BATTLEFIELD_VERSIONS, ANY_NUMBER_SUBTYPE_BATTLEFIELD_VERSION, SUBTYPE_GRAVEYARD_SEARCH_VERSION):
        try:
            (SubtypeGraveyardSearchSpec if is_subtype_graveyard_search(effect) else AnyNumberCreatureSubtypeSearch).from_effect({
                key: value for key, value in effect.items()
                if key not in {_SEARCH_COMPLETION_FIELD, _REPLACEMENT_SELECTIONS_FIELD}
            })
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if effect.get("schema_version") == RECRUITING_LIBRARY_SEARCH_VERSION:
        try:
            RecruitingLibrarySearchSpec.from_effect(effect)
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        return
    if effect.get("schema_version") in (6, 7, 8):
        _validate_search_zone_binding(effect)
        return
    if is_counted_library_search(effect):
        try:
            FixedCountedLibrarySearchTemplate.from_effect(effect)
        except (ValueError, TypeError, KeyError) as exc:
            raise GameRuleError("Counted library search instruction is malformed") from exc


def semantic_search_choice_schema(
    host,
    *,
    seat: str,
    effect: Mapping[str, Any],
    options: Sequence[Mapping[str, Any]],
    minimum: int,
    maximum: int,
    rules_may_fail: bool,
) -> dict[str, Any]:
    combined_bound = effect.get(CREATURE_SUBSET_MANA_VALUE_FIELD)
    entry_choice = combined_bound is None and any(
        (
            (record := host.card_record(
                next(
                    card.object_id
                    for card in host.state.cards.values()
                    if card.ref == option["id"]
                )
            ))
            is not None
            and host._land_entry_life_amount(record) > 0
        )
        for option in options
    )
    choice_schema: dict[str, Any] = {
        "field": "search_cards",
        "shape": "ref_array",
        "element_type": "string",
        "minimum": minimum,
        "maximum": maximum,
        "legal_refs": [option["id"] for option in options],
        "rules_may_fail_to_find": rules_may_fail,
        "example": {
            "search_cards": (
                [options[0]["id"]]
                if options and maximum > 0
                else []
            ),
        },
    }
    choice_schema.update(shared_land_type_search_choice(host, effect, seat, options))
    from ..rules.searched_library_pairs import is_searched_pair_search

    if is_searched_pair_search(effect):
        choice_schema.update(allowed_counts=[0, 2] if maximum == 2 else [0], example={"search_cards": []})
    if is_public_search_split(effect):
        choice_schema["example"]["search_cards"] = [option["id"] for option in options[:minimum]]
        if effect["schema_version"] == DISTINCT_PUBLIC_SEARCH_SPLIT_VERSION:
            choice_schema["different_names"] = True
    if combined_bound is not None:
        choice_schema[CREATURE_SUBSET_MANA_VALUE_FIELD] = combined_bound
        choice_schema["label"] = f"Search cards (total mana value {combined_bound} or less)"
    if effect.get('schema_version') == 5:
        from ..search_allocation_model import SearchAllocationSpec
        choice_schema.update(SearchAllocationSpec.from_effect(effect).choice_metadata())
    if effect.get("schema_version") == RECRUITING_LIBRARY_SEARCH_VERSION:
        choice_schema["order"] = "top_to_bottom"
    if is_ordered_creature_graveyard_search(effect) or is_subtype_graveyard_search(effect):
        choice_schema["order"] = "graveyard_top_to_bottom"
    from ..semantic_runtime.life_prohibitions import life_payment_prohibited

    if (entry_choice and not is_up_to_two_forest_search(effect)
            and (str(effect.get("destination")) == "battlefield" or effect.get('schema_version')==4)
            and not life_payment_prohibited(host, seat)):
        choice_schema["entry_pay_life"] = "boolean"
        choice_schema["example"]["entry_pay_life"] = False
    return choice_schema
