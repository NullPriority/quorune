from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, Mapping, Protocol, Sequence

from ..characteristic_read_frames import characteristic_read_method
from ..errors import GameRuleError
from ..public_library_piles_model import is_public_search_split
from ..life_state import LifeStateError, prepare_life_payment
from ..model import CardInstance, StackItem
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import object_matches_query, object_query_result
from ..replacement.immutable import FrozenMap, thaw_value
from ..replacement_effects import ReplacementChoiceRequired
from ..zone_transitions import ZoneTransitionOwner
from ..named_search_model import (
    LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION,
)
from ..library_search_model import (
    ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION,
    BARGAIN_SEARCH_VERSION, PRIVATE_SEARCH_PLAY_VERSION, is_bargain_search_effect,
    EXILED_TARGET_SEARCH_VERSION,
    FETCH_ENTRY_PLACEMENT_VERSION, QUERY_BOUND_SEARCH_VERSION, FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION, PAID_ARTIFACT_SEARCH_VERSION, PAID_CREATURE_SEARCH_VERSION,
    CAST_X_ZONE_SEARCH_VERSION, COMMITTED_ATTACKING_EXILE_SEARCH_VERSION, COMMITTED_LAND_SEARCH_VERSION,
    LINKED_ARTIFACT_SEARCH_VERSION,
    CREATURE_SUBSET_MANA_VALUE_FIELD, CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION,
    RECRUITING_LIBRARY_SEARCH_VERSION,
    SEARCHED_LAND_UNTAP_VERSION,
    SIMULTANEOUS_CREATURE_SEARCH_VERSIONS,
    is_up_to_two_forest_search,
    TARGET_NAME_LIBRARY_SEARCH_VERSION, TARGET_NAME_MULTIZONE_EXILE_VERSION,
    mana_cost_search_predicate_matches, semantic_search_maximum as _semantic_search_maximum,
    project_semantic_search_result_publication,
    TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION,
    decode_fixed_tapped_battlefield_search,
    SEARCH_ALTERNATIVES, SEARCH_SUBTYPES, is_counted_library_search, is_subtype_graveyard_search,
    is_ordered_creature_graveyard_search,
)
from ..rules.library_search_rules import library_search_constraint, library_search_object_ids, semantic_search_public_minimum
from ..rules.affected_library_search_batch import SEARCH_BATCH_VERSION, advance_exile_land_search, verify_exile_land_search
from .search_resolution import SemanticSearchCompletionContext, begin_exile_land_search
from .controlled_search import resolve_controlled_finds, search_control_payload
from .search_result_placement import move_counted_public_search_batch, untap_searched_land
from ..rules.targeted_library_exile import (
    TARGET_LIBRARY_EXILE_VERSION, TARGET_NAME_MULTIZONE_CAPTURE, TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION,
    bind_resolving_targeted_library_search, continue_departed_target_search, move_targeted_library_exile,
    prepare_target_name_multizone_exile, target_name_multizone_search_view, targeted_library_departed,
    targeted_library_owner, targeted_library_search_options,
)
from ..rules.dragon_graveyard_search_copy import (
    bind_dragon_graveyard_copy_search, copy_public_dragon_search_result, is_dragon_graveyard_copy_search,
    move_dragon_graveyard_copy_search_card,
)
from .different_name_search import validate_search_different_names
from ..rules.shared_land_type_search import validate_shared_land_type_search
from .fetch_search_entry import (
    FETCH_OPERATION_ID, finish_fetch_selection, is_fetch_search_entry_resume,
    requires_private_land_entry_choice, resume_fetch_search_entry,
)
from .forest_search_entry import (
    FOREST_ENTRY_PAID_REFS, begin_forest_entry_payment_choice, forest_entry_payment_refs,
    forest_entry_replacement_is_private,
)
from .mana_value_search import (
    creature_subset_search_options, validate_creature_subset_search_results, validate_legacy_search_mana_value_total,
)
from ..linked_ability_exile_state import retain_linked_artifact_search_result
from ..rules.linked_library_imprint import require_linked_artifact_library_search
from .search_descriptor_validation import (
    semantic_search_choice_schema,
    materialize_semantic_search_bound,
    semantic_search_actor,
    semantic_search_destination,
    semantic_search_selector_is_restrictive,
    validate_semantic_search_descriptor,
    validate_semantic_search_response,
)
from .model import (
    SelectionContract,
    SelectionContinuation,
    SelectionModelError,
    decode_selection_continuation,
)


SEARCH_OPERATION_ID = "selection.search.semantic.v1"
_FETCH_ENTRY_TAPPED = "fetch_entry_tapped"
_SEARCH_COMPLETION_FIELD = "_search_completion"
_SEARCH_COMPLETION_FIELDS = {"actor", "choice_id", "selected_refs"}
_ENTRY_LIFE_CHOICE = "entry_pay_life"
_REPLACEMENT_SELECTIONS_FIELD = "_replacement_selections"
_TARGETED_EXILE_SEARCH_VERSIONS = (
    TARGET_LIBRARY_EXILE_VERSION, TARGET_NAME_MULTIZONE_EXILE_VERSION, TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION,
)
_REPLACEMENT_EXILE_SEARCH_VERSIONS = (
    *_TARGETED_EXILE_SEARCH_VERSIONS, LINKED_ARTIFACT_SEARCH_VERSION,
    BARGAIN_SEARCH_VERSION, PRIVATE_SEARCH_PLAY_VERSION,
)
_REPLACEMENT_BATTLEFIELD_SEARCH_VERSIONS = (
    QUERY_BOUND_SEARCH_VERSION,
    TARGET_NAME_LIBRARY_SEARCH_VERSION,
    LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION,
    *SIMULTANEOUS_CREATURE_SEARCH_VERSIONS,
    COMMITTED_ATTACKING_EXILE_SEARCH_VERSION, COMMITTED_LAND_SEARCH_VERSION,
    TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION,
    PAID_ARTIFACT_SEARCH_VERSION, PAID_CREATURE_SEARCH_VERSION, CAST_X_ZONE_SEARCH_VERSION,
    SEARCHED_LAND_UNTAP_VERSION,
)


def _battlefield_search_can_resume(effect):
    return effect.get("schema_version") in _REPLACEMENT_BATTLEFIELD_SEARCH_VERSIONS or is_up_to_two_forest_search(effect)


def _search_entry_life_choice(response: Mapping[str, Any]) -> bool:
    return bool(response.get(_ENTRY_LIFE_CHOICE, response.get("pay_life", False)))


def _single_library_battlefield_entry(
    effect: Mapping[str, Any],
    values: Sequence[str],
    destination: str,
) -> bool:
    return (
        destination == "battlefield"
        and len(values) == 1
        and effect.get("zone", "library") == "library"
        and effect.get("schema_version") is None
        and (effect.get("count") or {}).get("maximum", 1) == 1
        and effect.get("enters_tapped_override") is None
    )


class HiddenSearchHost(Protocol):
    """Narrow callbacks required to issue and commit one hidden-zone search."""

    state: Any
    seats: Sequence[str]
    permissions: Any

    def card_record(self, card: Any) -> Any: ...
    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...
    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...
    def move_card(self, object_id: str, destination: str, **kwargs: Any) -> Any: ...
    def shuffle_library(self, seat: str, *, reason: str) -> None: ...


def _announces_revealed_top_search(effect: Mapping[str, Any]) -> bool:
    from ..qualified_library_search_model import QUALIFIED_LIBRARY_SEARCH_VERSION

    return (effect.get("schema_version") in {RECRUITING_LIBRARY_SEARCH_VERSION, QUALIFIED_LIBRARY_SEARCH_VERSION}
            and effect.get("shuffle_before_placement") is True)


class HiddenSearchOwnerMixin:
    """Own hidden-zone search advertisement, validation, and completion."""

    _validate_counted_search_effect = staticmethod(validate_semantic_search_descriptor)

    def _search_selection_continuation(
        self,
        *,
        actor: str,
        item: StackItem,
        effect: Mapping[str, Any],
        remaining: Sequence[Mapping[str, Any]],
        destination: str | None,
        note: str,
        frame: Mapping[str, Any],
        legal_refs: tuple[str, ...],
    ) -> SelectionContinuation:
        source_ref = str(
            dict(frame.get("locals") or {}).get("source_object") or ""
        ) or None
        return SelectionContinuation(
            contract=SelectionContract.SEARCH,
            operation_id=SEARCH_OPERATION_ID,
            actor=actor,
            state_revision=self.state.revision,
            stack_ref=item.ref,
            source_ref=source_ref,
            visibility="actor_private",
            payload=FrozenMap(
                {
                    "effect": dict(effect),
                    "remaining": [dict(value) for value in remaining],
                    "destination": destination,
                    "note": note,
                    "semantic_frame": dict(frame),
                    "legal_refs": legal_refs,
                }
            ),
        )

    def _fetch_land_options(
        self,
        seat: str,
        land_types: Sequence[str],
    ) -> list[dict[str, str]]:
        requested = {str(value).casefold() for value in land_types}
        options: list[dict[str, str]] = []
        for object_id in library_search_object_ids(self,seat,'library'):
            card = self.state.cards[object_id]
            record = self.card_record(card)
            if record is None or not record.is_land:
                continue
            types, subtypes, supertypes = self._type_parts(record.type_line)
            matches = (
                "basic land" in requested
                and "land" in types
                and "basic" in supertypes
            ) or bool(requested.intersection(subtypes))
            if matches:
                options.append({"id": card.ref, "name": record.name})
        return sorted(options, key=lambda item: (item["name"], item["id"]))

    def _fetch_context(
        self,
        seat: str,
        ability: Any,
        response: Mapping[str, Any],
        *,
        program: Any = None,
    ) -> dict[str, Any]:
        land_types = tuple(ability.library_search_types)
        if not land_types:
            return {}
        options = self._fetch_land_options(seat, land_types)
        selected = str(response.get("search_card") or "")
        if selected and selected not in {item["id"] for item in options}:
            raise GameRuleError(
                "Selected fetchland result is not a legal card in your library"
            )
        context = {
            "builtin": "fetch_land",
            "land_types": list(land_types),
            "search_card": selected or None,
            "choice_made": bool(selected),
            "pay_life": bool(response.get("entry_pay_life", False)),
        }
        if (getattr(self.state, "fetch_entry_placement_version", None) == FETCH_ENTRY_PLACEMENT_VERSION
                and program is not None and len(program.effects) == 1
                and decode_fixed_tapped_battlefield_search(program.effects[0]) is not None
                and self.semantic_program_is_current_trusted(program)):
            context[_FETCH_ENTRY_TAPPED] = True
        return context

    def _fetch_requested_tapped(self, item: StackItem) -> bool | None:
        if _FETCH_ENTRY_TAPPED not in item.context:
            return None
        if (item.context[_FETCH_ENTRY_TAPPED] is not True
                or getattr(self.state, "fetch_entry_placement_version", None) != FETCH_ENTRY_PLACEMENT_VERSION):
            raise GameRuleError("Invalid fetch entry placement")
        return True

    def _fetch_selection_continuation(
        self,
        *,
        item: StackItem,
        legal_refs: Sequence[str],
    ) -> SelectionContinuation:
        return SelectionContinuation(
            contract=SelectionContract.SEARCH,
            operation_id=FETCH_OPERATION_ID,
            actor=item.controller,
            state_revision=self.state.revision,
            stack_ref=item.ref,
            source_ref=self._stack_source_ref(item),
            visibility="actor_private",
            payload=FrozenMap(
                {
                    "land_types": list(item.context.get("land_types") or []),
                    "legal_refs": list(legal_refs),
                    **({_FETCH_ENTRY_TAPPED: True} if self._fetch_requested_tapped(item) else {}),
                }
            ),
        )

    def _begin_fetch_search(self, item: StackItem) -> None:
        if library_search_constraint(self,item.controller).prohibited:
            self._resolve_fetch_land(item)
            return
        options = self._fetch_land_options(
            item.controller,
            item.context.get("land_types", []),
        )
        legal_refs = [option["id"] for option in options]
        from ..semantic_runtime.life_prohibitions import life_payment_prohibited

        entry_payment = {} if life_payment_prohibited(self, item.controller) else {"entry_pay_life": "boolean"}
        self.permissions.issue(
            kind="search.fetch",
            role="pilot",
            actors=[item.controller],
            allowed_actions=["choose"],
            payload_by_actor={
                item.controller: {
                    **self._bounded_library_search_view(item.controller),
                    "stack": item.ref,
                    "instruction": (
                        "Choose a legal land to find, or omit search_card "
                        "to fail to find."
                    ),
                    "search_types": list(
                        item.context.get("land_types", [])
                    ),
                    "search_cards": options,
                    "legal_actions": [
                        {
                            "id": "choose",
                            "action": "choose",
                            "choice_schema": {
                                "search_candidates": legal_refs,
                                "may_fail_to_find": True,
                                **entry_payment,
                            },
                        }
                    ],
                }
            },
            continuation={
                "selection": self._fetch_selection_continuation(item=item, legal_refs=legal_refs).to_dict(),
                **search_control_payload(self, item, item.controller, ("library",)),
            },
        )

    def _complete_fetch_choice(self, decision: Any) -> None:
        seat = decision.actors[0]
        response = decision.responses[seat]
        raw_continuation = decision.continuation
        legacy_stack_ref = str(raw_continuation.get("stack_ref") or "")
        legacy = SelectionContinuation(
            contract=SelectionContract.SEARCH,
            operation_id=FETCH_OPERATION_ID,
            actor=seat,
            state_revision=decision.created_revision,
            stack_ref=legacy_stack_ref or None,
            visibility="actor_private",
            payload=FrozenMap({"land_types": [], "legal_refs": []}),
        )
        try:
            selection = decode_selection_continuation(
                raw_continuation,
                expected_contract=SelectionContract.SEARCH,
                expected_operation_id=FETCH_OPERATION_ID,
                legacy=legacy,
            )
        except SelectionModelError as exc:
            raise GameRuleError(str(exc)) from exc
        if selection.actor != seat:
            raise GameRuleError("Fetch search actor changed")
        if selection.state_revision != decision.created_revision:
            raise GameRuleError("Fetch search state revision changed")
        if selection.visibility != "actor_private":
            raise GameRuleError("Fetch search visibility changed")
        item = next(
            (
                candidate
                for candidate in self.state.stack
                if candidate.ref == selection.stack_ref
            ),
            None,
        )
        if item is None or item.context.get("builtin") != "fetch_land":
            raise GameRuleError(
                "The fetchland search object is no longer on the stack"
            )
        if item.controller != seat:
            raise GameRuleError("Fetch search controller changed")
        options = {
            option["id"]
            for option in self._fetch_land_options(
                seat,
                item.context.get("land_types", []),
            )
        }
        entry_tapped = self._fetch_requested_tapped(item)
        payload = thaw_value(selection.payload) if "selection" in raw_continuation else {}
        if ((_FETCH_ENTRY_TAPPED in payload) != (entry_tapped is True)
                or payload.get(_FETCH_ENTRY_TAPPED) is not entry_tapped):
            raise GameRuleError("Fetch search entry placement changed")
        if "selection" in raw_continuation:
            if selection.source_ref != self._stack_source_ref(item):
                raise GameRuleError("Fetch search source identity changed")
            if list(payload.get("land_types") or []) != list(
                item.context.get("land_types") or []
            ):
                raise GameRuleError("Fetch search specification changed")
            if set(payload.get("legal_refs") or []) != options:
                raise GameRuleError("Fetch search candidates changed")
        selected = response.get("search_card") or response.get("card")
        if selected is not None and str(selected) not in options:
            raise GameRuleError(
                "Selected fetchland result is no longer a legal library card"
            )
        control_result = resolve_controlled_finds(
            self, decision, item=item, actor=seat, zones=("library",),
            refs=(str(selected),) if selected is not None else (),
        )
        if control_result != "pending":
            finish_fetch_selection(
                self, item, selected=str(selected) if selected is not None and control_result == "unchanged" else None,
                pay_life=_search_entry_life_choice(response) if control_result == "unchanged" else False,
            )

    def _resolve_fetch_land(self, item: StackItem, *, replacement_selections=()) -> None:
        seat = item.controller
        selected = item.context.get("search_card")
        legal_refs = {
            option["id"]
            for option in self._fetch_land_options(
                seat,
                item.context.get("land_types", []),
            )
        }
        found: CardInstance | None = None
        if selected and str(selected) in legal_refs:
            try:
                found = self._resolve_object(
                    seat,
                    str(selected),
                    zones={"library"},
                    owned_only=True,
                )
            except GameRuleError:
                found = None
        if found is not None:
            record = self.card_record(found)
            if record is None:
                raise GameRuleError("Fetch search result has no card record")
            entry_life = self._land_entry_life_amount(record)
            pay_entry_life = bool(item.context.get("pay_life"))
            if pay_entry_life and entry_life <= 0:
                raise GameRuleError(
                    "This search result does not authorize an entry life payment"
                )
            life_before = self.state.players[seat].life
            self.move_card(
                found.object_id,
                "battlefield",
                controller=seat,
                putting_player=seat,
                tapped=self._fetch_requested_tapped(item),
                entry_pay_life=pay_entry_life,
                reason=f"{item.label} search",
                log=False,
                semantic_events=True,
                replacement_selections=replacement_selections,
            )
            tapped = found.tapped
            life_paid = life_before - self.state.players[seat].life
            self._log(
                seat,
                "library.search",
                f"{seat} found {found.ref} {found.printed_name}.",
                {
                    "source": item.ref,
                    "object": found.ref,
                    "tapped": tapped,
                    "life_paid": life_paid,
                },
                importance=2,
                changed_objects=[found.object_id],
                changed_players=[seat],
            )
        else:
            self._log(
                seat,
                'library.search.prohibited' if library_search_constraint(self,seat).prohibited else "library.search",
                f"{seat} could not search." if library_search_constraint(self,seat).prohibited else f"{seat} did not find a card.",
                {"source": item.ref},
                importance=1,
                changed_players=[seat],
            )
        self.shuffle_library(seat, reason=f"{item.label} resolved")
        self._begin_resolve_item(
            item,
            [],
            None,
            note="Built-in fetchland search resolved",
        )

    @staticmethod
    def _search_characteristic_query(selector: Mapping[str, Any], zone: str) -> ObjectQuerySpec:
        # This codec does not represent type exclusions. Reject a descriptor
        # carrying one rather than silently executing a weaker predicate.
        if "excluded_types" in selector:
            raise GameRuleError("Semantic search selector type exclusions are unsupported")
        subtypes_any = selector.get("subtypes_any", ())
        if (
            not isinstance(subtypes_any, (list, tuple))
            or any(type(value) is not str or value not in SEARCH_SUBTYPES for value in subtypes_any)
        ):
            raise GameRuleError("Semantic search selector subtype is outside the closed vocabulary")
        try:
            return ObjectQuerySpec(
                zones=(zone,),
                types_all=tuple(selector.get("types") or ()),
                types_any=tuple(selector.get("types_any") or ()),
                subtypes_all=tuple(selector.get("subtypes") or ()),
                subtypes_any=tuple(selector.get("subtypes_any") or ()),
                supertypes_all=tuple(selector.get("supertypes") or ()),
                colors_all=tuple(selector.get("colors") or ()),
                colors_any=tuple(selector.get("colors_any") or ()),
            )
        except (ObjectQueryError, TypeError) as exc:
            raise GameRuleError("Malformed semantic search selector") from exc

    @staticmethod
    def _search_leaf_matches(row, record, query: ObjectQuerySpec, selector: Mapping[str, Any]) -> bool:
        if not object_matches_query(row, query):
            return False
        names = {
            str(value).casefold() for value in selector.get("names") or []
        }
        if names and record.name.casefold() not in names:
            return False
        mana_value = selector.get("mana_value")
        if mana_value is not None:
            constraint = (
                dict(mana_value)
                if isinstance(mana_value, Mapping)
                else {"equal": mana_value}
            )
            if 'equal' in constraint and constraint['equal'] is None:
                # CR701.23c: an undefined quality permits searching, but no
                # card can be found. It must not become an unrestricted query.
                return False
            if (
                constraint.get("equal") is not None
                and row.mana_value != float(constraint["equal"])
            ):
                return False
            if (
                constraint.get("minimum") is not None
                and row.mana_value < float(constraint["minimum"])
            ):
                return False
            if (
                constraint.get("maximum") is not None
                and row.mana_value > float(constraint["maximum"])
            ):
                return False
        predicate = selector.get("predicate")
        if predicate in {None, ""}:
            return True
        if predicate == "noncreature":
            return "creature" not in row.types
        if predicate == "instant_or_sorcery":
            return bool(set(row.types).intersection({"instant", "sorcery"}))
        mana_cost_match = mana_cost_search_predicate_matches(
            predicate,
            record,
            zone=row.zone,
        )
        if mana_cost_match is not None:
            return mana_cost_match
        if predicate == "land_with_basic_land_type":
            return (
                "land" in row.types
                and bool(
                    set(row.subtypes).intersection(
                        {"plains", "island", "swamp", "mountain", "forest"}
                    )
                )
            )
        raise GameRuleError(f"Unsupported search predicate {predicate!r}")

    def _search_candidate_matches(self, card: CardInstance, selector: Mapping[str, Any]) -> bool:
        leaves = selector.get(SEARCH_ALTERNATIVES, (selector,))
        queries = tuple((leaf, self._search_characteristic_query(leaf, card.zone)) for leaf in leaves)
        record = self.card_record(card)
        if record is None:
            return False
        effective = self._effective_card_data(card)
        row = object_query_result(
            card, effective, type_parts=self._type_parts(str(effective.get("type_line") or "")),
            known_to_actor=True, attached_to_ref=None,
        )
        return any(self._search_leaf_matches(row, record, query, leaf) for leaf, query in queries)

    @characteristic_read_method
    def _semantic_search_options(
        self,
        seat: str,
        effect: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if effect.get('schema_version') in _TARGETED_EXILE_SEARCH_VERSIONS:
            return targeted_library_search_options(self, seat, effect)
        if effect.get("schema_version") in (LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION):
            from ..rules.qualified_library_search import literal_name_library_search_options
            return literal_name_library_search_options(self, seat, effect)
        if effect.get("schema_version") == TARGET_NAME_LIBRARY_SEARCH_VERSION:
            from ..rules.qualified_library_search import target_name_library_search_options
            return target_name_library_search_options(self, seat, effect)
        if effect.get('schema_version') == 7:
            from ..rules.qualified_library_search import qualified_library_search_options
            return qualified_library_search_options(self, seat,
                {key:value for key,value in effect.items() if key != _SEARCH_COMPLETION_FIELD})
        if effect.get("schema_version") == CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION or CREATURE_SUBSET_MANA_VALUE_FIELD in effect:
            self._validate_counted_search_effect(effect)
            return creature_subset_search_options(self, seat, effect[CREATURE_SUBSET_MANA_VALUE_FIELD])
        raw_zone = effect.get("zone") or "library"
        zones = (
            [str(value) for value in raw_zone]
            if isinstance(raw_zone, Sequence)
            and not isinstance(raw_zone, (str, bytes))
            else [str(raw_zone)]
        )
        if any(
            zone not in {"library", "graveyard", "hand", "exile"}
            for zone in zones
        ):
            raise GameRuleError(
                f"Unsupported semantic search zone {raw_zone!r}"
            )
        selector = dict(effect.get("selector") or {})
        if "excluded_types" in selector:
            raise GameRuleError("Semantic search selector type exclusions are unsupported")
        options = [
            {
                "id": self.state.cards[object_id].ref,
                "name": self.state.cards[object_id].printed_name,
            }
            for zone in zones
            for object_id in library_search_object_ids(self,seat,zone)
            if self._search_candidate_matches(self.state.cards[object_id], selector)
        ]
        from .fixed_opponent_land_search import filter_coordinated_search_rows

        return filter_coordinated_search_rows(self, seat, effect, options)

    _search_is_restrictive = staticmethod(semantic_search_selector_is_restrictive)

    def _begin_semantic_search(
        self,
        *,
        item: StackItem,
        effect: Mapping[str, Any],
        remaining: Sequence[Mapping[str, Any]],
        destination: str | None,
        note: str,
        instruction_pointer: int,
    ) -> None:
        if begin_exile_land_search(self, item, effect, remaining=remaining,
                                   destination=destination, note=note, instruction_pointer=instruction_pointer):
            return
        if is_fetch_search_entry_resume(effect):
            resume_fetch_search_entry(
                self, item, effect, remaining=remaining, destination=destination,
                instruction_pointer=instruction_pointer,
            )
            return
        if _SEARCH_COMPLETION_FIELD in effect:
            self._resume_semantic_search(
                item=item,
                effect=effect,
                remaining=remaining,
                destination=destination,
                note=note,
                instruction_pointer=instruction_pointer,
            )
            return
        if (effect.get("schema_version") == TARGET_NAME_MULTIZONE_EXILE_VERSION
                and TARGET_NAME_MULTIZONE_CAPTURE not in effect):
            prepared = prepare_target_name_multizone_exile(self, item, effect)
            self._continue_resolution(
                stack_ref=item.ref, effects=[*prepared, *remaining], destination=destination,
                note=note, instruction_pointer=instruction_pointer + 1,
            )
            return
        if effect.get("schema_version") == TARGET_NAME_LIBRARY_SEARCH_VERSION:
            from ..rules.qualified_library_search import bind_target_name_library_search
            effect = bind_target_name_library_search(self, item.controller, effect)
        effect = materialize_semantic_search_bound(self, item, effect, instruction_pointer)
        self._validate_counted_search_effect(effect)
        if effect.get("schema_version") == EXILED_TARGET_SEARCH_VERSION:
            from ..rules.exiled_target_search import require_combined_entry_program

            require_combined_entry_program(self, item)
        seat = semantic_search_actor(item, effect)
        self._require_seat(seat, in_game=True)
        if is_public_search_split(effect):
            from ..search_allocation import require_public_search_split_resolution

            require_public_search_split_resolution(self, item, effect)
        require_linked_artifact_library_search(self, item, effect, actor=seat)
        if effect.get('schema_version') in _TARGETED_EXILE_SEARCH_VERSIONS:
            if targeted_library_departed(self, seat, effect):
                self._continue_resolution(stack_ref=item.ref, effects=list(remaining),
                    destination=destination, note=note, instruction_pointer=instruction_pointer + 1)
                return
            effect = bind_resolving_targeted_library_search(self, item, effect, actor=seat)
        elif is_dragon_graveyard_copy_search(effect):
            effect = bind_dragon_graveyard_copy_search(self, item, effect, seat)
        raw_zone=effect.get('zone','library')
        zones=[raw_zone] if isinstance(raw_zone,str) else list(raw_zone)
        if 'library' in zones and library_search_constraint(self,seat).prohibited:
            others=[zone for zone in zones if zone!='library']
            if not others:
                self._finish_prohibited_library_search(item=item,effect=effect,remaining=remaining,destination=destination,
                    note=note,instruction_pointer=instruction_pointer,seat=seat)
                return
            effect={**effect,'zone':others}
        options = self._semantic_search_options(seat, effect)
        count = dict(effect.get("count") or {})
        minimum = max(0, int(count.get("minimum", 1)))
        maximum = _semantic_search_maximum(effect, minimum, len(options))
        selector = dict(effect.get("selector") or {})
        raw_search_zone = effect.get("zone") or "library"
        search_zones = (
            [str(value) for value in raw_search_zone]
            if isinstance(raw_search_zone, Sequence)
            and not isinstance(raw_search_zone, (str, bytes))
            else [str(raw_search_zone)]
        )
        rules_may_fail = bool(effect.get("optional", False)) or (
            "library" in search_zones
            and self._search_is_restrictive(selector)
        )
        minimum_choice = 0 if rules_may_fail else min(minimum, len(options))
        if (public_minimum := semantic_search_public_minimum(self, seat, effect, options)) is not None:
            minimum_choice, rules_may_fail = public_minimum, public_minimum == 0
        choice_schema = semantic_search_choice_schema(
            self, seat=seat,
            effect=effect,
            options=options,
            minimum=minimum_choice,
            maximum=maximum,
            rules_may_fail=rules_may_fail,
        )
        frame = self._semantic_search_frame(item, seat, instruction_pointer)
        selection = self._search_selection_continuation(actor=seat, item=item,
            effect=effect, remaining=remaining, destination=destination, note=note,
            frame=frame, legal_refs=tuple(option['id'] for option in options))
        if effect.get("schema_version") == TARGET_NAME_MULTIZONE_EXILE_VERSION:
            private_view = target_name_multizone_search_view(self, seat, effect)
        elif "library" in search_zones:
            private_view = self._bounded_library_search_view(
                seat, library_player=effect.get("library_player") if effect.get("schema_version") in _TARGETED_EXILE_SEARCH_VERSIONS else None,
            )
        else:
            private_view = {}
        from .fixed_opponent_land_search import coordinated_search_private_view

        private_view = coordinated_search_private_view(self, item, seat, effect, private_view)
        decision = self.permissions.issue(
            kind="semantic.search",
            role="pilot",
            actors=[seat],
            allowed_actions=["choose"],
            payload_by_actor={
                seat: {
                    "stack": item.ref,
                    "operation": "search",
                    "instruction": str(
                        effect.get("instruction")
                        or (
                            "Choose creature cards in graveyard order, top card first."
                            if is_ordered_creature_graveyard_search(effect)
                            else "Choose card(s) matching the search specification."
                        )
                    ),
                    **private_view,
                    "search_cards": options,
                    "search_spec": {
                        "zone": copy.deepcopy(raw_search_zone),
                        "selector": selector,
                        "count": {
                            "minimum": minimum_choice,
                            "maximum": maximum,
                        },
                        "destination": effect.get("destination"),
                        "reveal": bool(effect.get("reveal", False)),
                        "shuffle_after": bool(
                            effect.get("shuffle_after", True)
                        ),
                        "rules_may_fail_to_find": rules_may_fail,
                        **({"shuffle_before_placement": True} if _announces_revealed_top_search(effect) else {}),
                    },
                    "legal_actions": [
                        {
                            "id": "choose",
                            "action": "choose",
                            "choice_schema": choice_schema,
                        }
                    ],
                }
            },
            continuation={"selection": selection.to_dict(), **search_control_payload(
                self, item, seat, search_zones, library_owner=effect.get("library_player"),
            )},
        )
        frame["pending_choice_id"] = decision.decision_id
        decision.continuation['selection'] = replace(selection,
            payload=FrozenMap({**selection.payload, 'semantic_frame':frame})).to_dict()

    def _semantic_search_frame(self, item, seat, instruction_pointer):
        return self._semantic_frame(
            item,
            instruction_pointer=instruction_pointer,
            locals={
                "searching_player": seat,
                "source_object": (
                    self.state.cards[item.source_object_id].ref
                    if item.source_object_id in self.state.cards
                    else (
                        self.state.cards[item.card_object_id].ref
                        if item.card_object_id in self.state.cards
                        else None
                    )
                ),
            },
        )

    def _bounded_library_search_view(self,seat,library_player=None):
        constraint=library_search_constraint(self,seat)
        if constraint.prohibited or constraint.top_count is None:return {}
        owner = seat if library_player is None else library_player
        return {'search_window':[{'id':self.state.cards[oid].ref,'name':self.state.cards[oid].printed_name}
            for oid in library_search_object_ids(self,owner,'library',searching_player=seat)]}

    def _finish_prohibited_library_search(self,*,item,effect,remaining,destination,note,instruction_pointer,seat):
        self._log(seat,'library.search.prohibited',f'{seat} could not search their library.',{'source':item.ref},importance=1)
        if effect.get("schema_version") == EXILED_TARGET_SEARCH_VERSION:
            from ..rules.exiled_target_search import stage_combined_entry_selection

            stage_combined_entry_selection(self, item, effect, (), prohibited=True)
        if not effect.get('optional',False) and (effect.get('shuffle_after',True) or effect.get('shuffle_before_placement',False)):
            owner = targeted_library_owner(self, seat, effect) if effect.get('schema_version') in _TARGETED_EXILE_SEARCH_VERSIONS else seat
            self.shuffle_library(owner,reason=f'{item.label} resolved')
        self._continue_resolution(stack_ref=item.ref,effects=[dict(value) for value in remaining],destination=destination,
            note=note,instruction_pointer=instruction_pointer+1)

    def _semantic_search_completion_context(
        self,
        decision: Any,
    ) -> SemanticSearchCompletionContext:
        seat = decision.actors[0]
        response = decision.responses[seat]
        raw_continuation = decision.continuation
        legacy_payload = dict(raw_continuation)
        legacy_frame = dict(legacy_payload.get("semantic_frame") or {})
        legacy = SelectionContinuation(
            contract=SelectionContract.SEARCH,
            operation_id=SEARCH_OPERATION_ID,
            actor=seat,
            state_revision=decision.created_revision,
            stack_ref=str(legacy_payload.get("stack_ref") or "") or None,
            source_ref=str(
                dict(legacy_frame.get("locals") or {}).get("source_object") or ""
            )
            or None,
            visibility="actor_private",
            payload=FrozenMap(legacy_payload),
        )
        try:
            selection = decode_selection_continuation(
                raw_continuation,
                expected_contract=SelectionContract.SEARCH,
                expected_operation_id=SEARCH_OPERATION_ID,
                legacy=legacy,
            )
        except SelectionModelError as exc:
            raise GameRuleError(str(exc)) from exc
        if selection.actor != seat:
            raise GameRuleError("Semantic search actor changed")
        if selection.state_revision != decision.created_revision:
            raise GameRuleError("Semantic search state revision changed")
        if selection.visibility != "actor_private":
            raise GameRuleError("Semantic search visibility changed")
        continuation = thaw_value(selection.payload)
        stack_ref = str(selection.stack_ref or "")
        item = next(
            (
                candidate
                for candidate in self.state.stack
                if candidate.ref == stack_ref
            ),
            None,
        )
        if item is None:
            raise GameRuleError(
                "The semantic search's stack object no longer exists"
            )
        if (
            "selection" in raw_continuation
            and selection.source_ref != self._stack_source_ref(item)
        ):
            raise GameRuleError("Semantic search source identity changed")
        frame = dict(continuation.get("semantic_frame") or {})
        self._validate_semantic_frame(frame, item)
        effect = dict(continuation.get("effect") or {})
        materialize_semantic_search_bound(self, item, effect, frame.get('instruction_pointer'), saved_options=True)
        semantic_search_actor(item, effect, decision_actor=seat)
        options = {
            option["id"]
            for option in self._semantic_search_options(seat, effect)
        }
        issued_options = {
            str(value) for value in continuation.get("legal_refs", ())
        }
        departed = effect.get('schema_version') in _TARGETED_EXILE_SEARCH_VERSIONS and targeted_library_departed(self, seat, effect)
        if "selection" in raw_continuation and issued_options != options and not departed:
            raise GameRuleError("Semantic search candidates changed")
        return SemanticSearchCompletionContext(
            seat=seat,
            response=response,
            continuation=continuation,
            stack_ref=stack_ref,
            item=item,
            frame=frame,
            effect=effect,
            options=frozenset(options),
        )

    def _semantic_search_selected_values(
        self,
        context: SemanticSearchCompletionContext,
    ) -> tuple[list[str], list[str]]:
        response = context.response
        effect = context.effect
        options = context.options
        seat = context.seat
        validate_semantic_search_response(effect, response)
        explicit_search_cards = response.get("search_cards")
        if (effect.get("schema_version") == TARGET_NAME_MULTIZONE_EXILE_VERSION
                and {"cards", "search_card", "card"} & response.keys()):
            raise GameRuleError("The private name search accepts only its offered search_cards field")
        if explicit_search_cards is not None and (
            not isinstance(explicit_search_cards, Sequence)
            or isinstance(explicit_search_cards, (str, bytes))
            or any(
                not isinstance(value, str)
                for value in explicit_search_cards
            )
        ):
            raise GameRuleError(
                "search_cards must be an array of card-ref strings from "
                "choice_schema.legal_refs"
            )
        raw_values = (
            explicit_search_cards
            or response.get("cards")
            or (
                [response.get("search_card") or response.get("card")]
                if response.get("search_card") is not None
                or response.get("card") is not None
                else []
            )
        )
        values = [str(value) for value in raw_values if value is not None]
        if len(values) != len(set(values)) or any(
            value not in options for value in values
        ):
            raise GameRuleError(
                "Selected search result is no longer a legal candidate"
            )
        count = dict(effect.get("count") or {})
        minimum = max(0, int(count.get("minimum", 1)))
        maximum = _semantic_search_maximum(effect, minimum, len(options))
        selector = dict(effect.get("selector") or {})
        raw_search_zone = effect.get("zone") or "library"
        search_zones = (
            [str(value) for value in raw_search_zone]
            if isinstance(raw_search_zone, Sequence)
            and not isinstance(raw_search_zone, (str, bytes))
            else [str(raw_search_zone)]
        )
        rules_may_fail = bool(effect.get("optional", False)) or (
            "library" in search_zones
            and self._search_is_restrictive(selector)
        )
        required = 0 if rules_may_fail else min(minimum, len(options))
        if (public_minimum := semantic_search_public_minimum(self, seat, effect, options)) is not None:
            required = public_minimum
        if not required <= len(values) <= maximum:
            raise GameRuleError(f"Search requires between {required} and {maximum} selection(s)")
        if (total_constraint := selector.get("mana_value_total")) is not None:
            validate_legacy_search_mana_value_total(self, seat, values, search_zones, total_constraint)
        return values, search_zones

    _semantic_search_destination = staticmethod(semantic_search_destination)

    def _move_semantic_search_results(
        self,
        context: SemanticSearchCompletionContext,
        *,
        values: Sequence[str],
        search_zones: Sequence[str],
        destination: str,
        position: str | int,
        replacement_selections: Sequence[
            str | None | Mapping[str, Any]
        ] = (),
    ) -> list[CardInstance]:
        seat = context.seat
        response = context.response
        effect = context.effect
        item = context.item
        if effect.get('schema_version') in _TARGETED_EXILE_SEARCH_VERSIONS:
            return move_targeted_library_exile(self, context, values, replacement_selections)
        if is_dragon_graveyard_copy_search(effect):
            return move_dragon_graveyard_copy_search_card(self, context, values, replacement_selections)
        materialize_semantic_search_bound(self, item, effect, context.frame.get('instruction_pointer'), moving=True)
        reveal = bool(effect.get("reveal", False))
        selected_cards: list[CardInstance] = []
        for ref in values:
            card = self._resolve_object(
                seat,
                ref,
                zones=set(search_zones),
                owned_only=True,
            )
            selected_cards.append(card)
        validate_search_different_names(self, effect, selected_cards)
        validate_shared_land_type_search(self, effect, selected_cards)
        owner = ZoneTransitionOwner(self)
        from ..rules.private_search_play import uses_private_search_play

        private_search_play = uses_private_search_play(effect)
        if private_search_play or is_bargain_search_effect(effect):
            from .bargain_search import move_private_face_down_search_result

            moved = move_private_face_down_search_result(owner, context, selected_cards, replacement_selections)
            if private_search_play:
                from ..rules.private_opponent_top_exile import grant_private_library_search_play

                for card in moved:
                    grant_private_library_search_play(card, viewer=seat)
            return moved
        if effect.get('schema_version') == 5:
            from ..search_allocation import move_allocated_search_results
            return move_allocated_search_results(self, context, selected_cards, replacement_selections)
        announces_top = _announces_revealed_top_search(effect)
        if announces_top or is_counted_library_search(effect):
            self._validate_counted_search_effect(effect)
            if announces_top:
                from ..rules.library_partition import place_revealed_library_search_results_on_top

                place_revealed_library_search_results_on_top(
                    self, owner, seat, tuple(card.object_id for card in selected_cards),
                    reason=f"{item.label} resolved",
                )
                return selected_cards
            if effect.get("shuffle_before_placement") is True:
                if destination != "library" or position != "top" or len(selected_cards) > 1:
                    raise GameRuleError("Counted search shuffle/placement changed")
                object_ids = tuple(card.object_id for card in selected_cards)
                owner.reveal_library_search_results(seat, object_ids, public=reveal)
                owner.shuffle_library(seat, reason=f"{item.label} resolved", excluded_object_ids=object_ids)
            elif len(selected_cards) > 1 and (destination == "hand"
                    or is_subtype_graveyard_search(effect) or is_ordered_creature_graveyard_search(effect)):
                return move_counted_public_search_batch(
                    owner, seat, selected_cards, destination, item, reveal, replacement_selections,
                )
        if is_up_to_two_forest_search(effect):
            from .forest_search_entry import move_up_to_two_forests

            return move_up_to_two_forests(self, selected_cards, seat=seat, reason=f"{item.label} search",
                destination=destination, response=response, replacement_selections=replacement_selections)
        if effect.get("schema_version") in SIMULTANEOUS_CREATURE_SEARCH_VERSIONS:
            if destination != "battlefield":
                if effect["schema_version"] == ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION:
                    raise GameRuleError("Any-number subtype search changed its battlefield destination")
                raise GameRuleError("Fixed creature group search changed its battlefield destination")
            if effect["schema_version"] == CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION:
                validate_creature_subset_search_results(self, selected_cards, effect[CREATURE_SUBSET_MANA_VALUE_FIELD])
            return owner.move_cards_simultaneously(
                tuple((card.object_id, destination) for card in selected_cards),
                destination_controllers={card.object_id: seat for card in selected_cards},
                putting_players={card.object_id: seat for card in selected_cards},
                reason=f"{item.label} search",
                log=False,
                replacement_selections=replacement_selections,
            )

        if (
            len(selected_cards) > 1
            and destination == "battlefield"
            and effect.get("enters_tapped_override") is True
        ):
            return ZoneTransitionOwner(self).move_cards_simultaneously(
                tuple(
                    (card.object_id, destination)
                    for card in selected_cards
                ),
                tapped=True,
                putting_players={card.object_id: seat for card in selected_cards},
                reason=f"{item.label} search",
                log=False,
                replacement_selections=replacement_selections,
            )

        searched_land_untap = effect.get("schema_version") == SEARCHED_LAND_UNTAP_VERSION
        move_card = owner.move_card if searched_land_untap else self.move_card
        moved: list[CardInstance] = []
        for card in selected_cards:
            tapped = bool(effect.get("enters_tapped_override", False))
            pay_entry_life = False
            if (
                destination == "battlefield"
                and effect.get("enters_tapped_override") is None
            ):
                record = self.card_record(card)
                pay_entry_life = _search_entry_life_choice(response)
                entry_life = (
                    self._land_entry_life_amount(record)
                    if record is not None
                    else 0
                )
                if pay_entry_life and entry_life <= 0:
                    raise GameRuleError(
                        "This search result does not authorize an entry life payment"
                    )
            moved_card = move_card(
                card.object_id,
                destination,
                controller=seat if destination == "battlefield" else None,
                **({"putting_player": seat} if destination == "battlefield" else {}),
                tapped=tapped,
                entry_pay_life=pay_entry_life,
                position=position,
                reveal_to=self.seats if reveal else None,
                reason=f"{item.label} search",
                log=False,
                semantic_events=destination == "battlefield",
                replacement_selections=replacement_selections,
                **({"hidden_search_result_viewer": seat} if searched_land_untap else {}),
            )
            moved.append(retain_linked_artifact_search_result(self, item, effect, moved_card, actor=seat))
        return moved

    def _record_semantic_search_result(
        self,
        choice_id: str,
        context: SemanticSearchCompletionContext,
        *,
        destination_spec: str,
        destination: str,
        moved: Sequence[CardInstance],
    ) -> None:
        seat = context.seat
        effect = context.effect
        item = context.item
        reveal = bool(effect.get("reveal", False))
        untap_identity = (
            (moved[0].object_id, moved[0].logical_object_id)
            if effect.get("schema_version") == SEARCHED_LAND_UNTAP_VERSION
            and moved and moved[0].zone == "battlefield"
            else None
        )
        from ..search_allocation import allocated_search_result_details
        allocation_details = allocated_search_result_details(effect, moved)
        publication = project_semantic_search_result_publication(
            effect,
            source=item.ref,
            destination_spec=destination_spec,
            destination=destination,
            reveal=reveal,
            moved=moved,
            allocation_details=allocation_details,
        )
        self._log(
            seat,
            "library.search",
            f"{seat} searched {effect.get('zone', 'library')} and found "
            f"{publication.public_count} card(s).",
            publication.public_details,
            importance=2,
            changed_objects=[card.object_id for card in publication.public_changed_objects],
            changed_players=[seat],
        )
        self._log(
            seat,
            "library.search.private",
            f"{seat} selected {len(moved)} private search object(s).",
            publication.private_details,
            visibility=[seat, "analyst"],
            importance=0,
            changed_objects=[card.object_id for card in moved],
            changed_players=[seat],
        )
        if is_dragon_graveyard_copy_search(effect):
            copy_public_dragon_search_result(self, context, moved)
        if bool(effect.get("shuffle_after", True)):
            owner = targeted_library_owner(self, seat, effect) if effect.get('schema_version') in _TARGETED_EXILE_SEARCH_VERSIONS else seat
            self.shuffle_library(owner, reason=f"{item.label} resolved")
        if untap_identity is not None:
            untap_searched_land(self, seat, item, untap_identity)
        item.context.setdefault("semantic_continuations", []).append(
            {
                **context.frame,
                "pending_choice_id": choice_id,
                "choice_result": [card.ref for card in moved],
                "resumed": True,
            }
        )
        remaining = [dict(value) for value in context.continuation.get("remaining", [])]
        if is_bargain_search_effect(effect):
            from .bargain_search import bargain_search_followup

            remaining = bargain_search_followup(context, moved) + remaining
        self._continue_resolution(
            stack_ref=context.stack_ref,
            effects=remaining,
            destination=context.continuation.get("destination"),
            note=str(context.continuation.get("note") or ""),
            instruction_pointer=(
                int(context.frame.get("instruction_pointer", 0)) + 1
            ),
        )

    def _semantic_search_resume_context(
        self,
        *,
        item: StackItem,
        effect: Mapping[str, Any],
        remaining: Sequence[Mapping[str, Any]],
        destination: str | None,
        note: str,
        instruction_pointer: int,
    ) -> tuple[
        SemanticSearchCompletionContext,
        list[str],
        list[str],
        str,
        str,
        str | int,
    ]:
        raw_completion = effect.get(_SEARCH_COMPLETION_FIELD)
        if (
            not isinstance(raw_completion, Mapping)
            or set(raw_completion) not in (
                _SEARCH_COMPLETION_FIELDS,
                _SEARCH_COMPLETION_FIELDS | {_ENTRY_LIFE_CHOICE},
                _SEARCH_COMPLETION_FIELDS | {FOREST_ENTRY_PAID_REFS},
            )
        ):
            raise GameRuleError("Malformed semantic search completion")
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
        selected = list(raw_selected)
        counted = is_counted_library_search(effect) and (
            is_up_to_two_forest_search(effect) or effect.get("destination") == "hand"
            or effect.get("schema_version") in {
                8, FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION, COMMITTED_ATTACKING_EXILE_SEARCH_VERSION,
                COMMITTED_LAND_SEARCH_VERSION,
            }
        )
        maximum = effect.get("count", {}).get("maximum", 1) if counted or effect.get('schema_version') in _TARGETED_EXILE_SEARCH_VERSIONS else 1
        any_number = effect.get("schema_version") in {
            LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION,
            TARGET_NAME_MULTIZONE_EXILE_VERSION,
            CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION,
        }
        if (len(selected) != len(set(selected))
                or (not any_number and (type(maximum) is not int or len(selected) > maximum))):
            raise GameRuleError(
                "Replacement-resumed semantic search changed its cardinality"
            )
        base_effect = {
            key: copy.deepcopy(value)
            for key, value in effect.items()
            if key
            not in {
                _SEARCH_COMPLETION_FIELD,
                _REPLACEMENT_SELECTIONS_FIELD,
            }
        }
        self._validate_counted_search_effect(base_effect)
        seat = semantic_search_actor(item, base_effect)
        if actor != seat:
            raise GameRuleError("Semantic search completion actor changed")
        options = {
            option["id"]
            for option in self._semantic_search_options(seat, base_effect)
        }
        departed = base_effect.get('schema_version') in _TARGETED_EXILE_SEARCH_VERSIONS and targeted_library_departed(self, seat, base_effect)
        if any(value not in options for value in selected) and not departed:
            raise GameRuleError("Semantic search completion candidate changed")
        destination_spec, resolved_destination, position = (
            self._semantic_search_destination(base_effect)
        )
        response: dict[str, Any] = {"search_cards": selected}
        if FOREST_ENTRY_PAID_REFS in raw_completion:
            if not is_up_to_two_forest_search(base_effect):
                raise GameRuleError("Forest entry payment does not match its search")
            response[FOREST_ENTRY_PAID_REFS] = forest_entry_payment_refs(raw_completion[FOREST_ENTRY_PAID_REFS], selected)
        elif _ENTRY_LIFE_CHOICE in raw_completion:
            pay_entry_life = raw_completion[_ENTRY_LIFE_CHOICE]
            if type(pay_entry_life) is not bool or not _single_library_battlefield_entry(
                base_effect, selected, resolved_destination
            ):
                raise GameRuleError("Malformed semantic search entry life choice")
            response[_ENTRY_LIFE_CHOICE] = pay_entry_life
        elif (resolved_destination != "hand"
                and not (_battlefield_search_can_resume(base_effect)
                         and resolved_destination == "battlefield")
                and not (is_dragon_graveyard_copy_search(base_effect) and resolved_destination == "graveyard")
                and not (base_effect.get("schema_version") in _REPLACEMENT_EXILE_SEARCH_VERSIONS and resolved_destination == "exile")):
            raise GameRuleError(
                "Replacement-resumed semantic search changed its supported destination"
            )
        raw_zone = base_effect.get("zone") or "library"
        search_zones = (
            [str(value) for value in raw_zone]
            if isinstance(raw_zone, Sequence)
            and not isinstance(raw_zone, (str, bytes))
            else [str(raw_zone)]
        )
        frame = self._semantic_frame(
            item,
            instruction_pointer=instruction_pointer,
            locals={
                "searching_player": seat,
                "source_object": self._stack_source_ref(item),
            },
        )
        frame["pending_choice_id"] = choice_id
        return (
            SemanticSearchCompletionContext(
                seat=seat,
                response=response,
                continuation={
                    "remaining": [dict(value) for value in remaining],
                    "destination": destination,
                    "note": note,
                },
                stack_ref=item.ref,
                item=item,
                frame=frame,
                effect=base_effect,
                options=frozenset(options),
            ),
            selected,
            search_zones,
            choice_id,
            destination_spec,
            resolved_destination,
            position,
        )

    def _resume_semantic_search(
        self,
        *,
        item: StackItem,
        effect: Mapping[str, Any],
        remaining: Sequence[Mapping[str, Any]],
        destination: str | None,
        note: str,
        instruction_pointer: int,
    ) -> None:
        (
            context,
            values,
            search_zones,
            choice_id,
            destination_spec,
            resolved_destination,
            position,
        ) = self._semantic_search_resume_context(
            item=item,
            effect=effect,
            remaining=remaining,
            destination=destination,
            note=note,
            instruction_pointer=instruction_pointer,
        )
        if context.effect.get('schema_version') in _TARGETED_EXILE_SEARCH_VERSIONS and targeted_library_departed(self, context.seat, context.effect):
            continue_departed_target_search(self, context)
            return
        raw_selections = effect.get(_REPLACEMENT_SELECTIONS_FIELD, ())
        if (
            not isinstance(raw_selections, Sequence)
            or isinstance(raw_selections, (str, bytes))
        ):
            raise GameRuleError("Semantic search replacements are malformed")
        from ..resolving_library_exile import resolving_library_exile_source

        try:
            with resolving_library_exile_source(self, context.item, group_movements=True):
                moved = self._move_semantic_search_results(
                    context,
                    values=values,
                    search_zones=search_zones,
                    destination=resolved_destination,
                    position=position,
                    replacement_selections=raw_selections,
                )
        except ReplacementChoiceRequired as required:
            if (
                (_ENTRY_LIFE_CHOICE in context.response or is_dragon_graveyard_copy_search(context.effect))
                and required.pending.choice.chooser != context.seat
            ) or (is_up_to_two_forest_search(context.effect)
                  and not forest_entry_replacement_is_private(required, seat=context.seat, selected_refs=values)):
                raise
            from ..replacement_decisions import issue_replacement_order_choice

            issue_replacement_order_choice(
                self,
                item=item,
                effect=effect,
                remaining=remaining,
                destination=destination,
                note=note,
                instruction_pointer=instruction_pointer,
                required=required,
            )
            return
        self._record_semantic_search_result(
            choice_id,
            context,
            destination_spec=destination_spec,
            destination=resolved_destination,
            moved=moved,
        )

    def _complete_search_before_combined_entry(self, context, selected) -> None:
        from ..rules.exiled_target_search import stage_combined_entry_selection

        stage_combined_entry_selection(self, context.item, context.effect, selected)
        self._continue_resolution(
            stack_ref=context.stack_ref,
            effects=context.continuation["remaining"],
            destination=context.continuation.get("destination"),
            note=str(context.continuation.get("note") or ""),
            instruction_pointer=int(context.frame["instruction_pointer"]) + 1,
        )

    def _complete_semantic_search(self, decision: Any) -> None:
        context = self._semantic_search_completion_context(decision)
        compensation = context.effect.get("schema_version") == SEARCH_BATCH_VERSION
        if compensation:
            verify_exile_land_search(self, context.item, context.effect)
        if context.effect.get('schema_version') in _TARGETED_EXILE_SEARCH_VERSIONS and targeted_library_departed(self, context.seat, context.effect):
            continue_departed_target_search(self, context)
            return
        values, search_zones = self._semantic_search_selected_values(context)
        from .fixed_opponent_land_search import SEARCH_ROLE, complete_staged_opponent_find

        if context.effect.get(SEARCH_ROLE) == "opponent":
            complete_staged_opponent_find(self, decision, context, values, search_zones)
            return
        control_result = resolve_controlled_finds(
            self, decision, item=context.item, actor=context.seat, zones=search_zones, refs=values,
            library_owner=context.effect.get("library_player"),
        )
        if control_result == "pending":
            return
        if control_result == "applied":
            values = []

        if is_public_search_split(context.effect):
            from ..search_allocation import stage_public_search_split

            stage_public_search_split(self, context, values, choice_id=decision.decision_id)
            return
        if compensation:
            advance_exile_land_search(self, context, values)
            return
        if is_up_to_two_forest_search(context.effect) and begin_forest_entry_payment_choice(
            self, context, values, choice_id=decision.decision_id,
        ):
            return
        if context.effect.get("schema_version") == EXILED_TARGET_SEARCH_VERSION:
            self._complete_search_before_combined_entry(context, values)
            return
        from ..conditional_search_placement import materialize_conditional_search
        context=materialize_conditional_search(self,context,values)
        destination_spec, destination, position = (
            self._semantic_search_destination(context.effect)
        )
        try:
            moved = self._move_semantic_search_results(
                context,
                values=values,
                search_zones=search_zones,
                destination=destination,
                position=position,
            )
        except ReplacementChoiceRequired as required:
            dragon_search = is_dragon_graveyard_copy_search(context.effect)
            if dragon_search and required.pending.choice.chooser != context.seat:
                raise
            library_entry = _single_library_battlefield_entry(
                context.effect, values, destination
            )
            if library_entry:
                if not requires_private_land_entry_choice(required, context.seat):
                    raise
            elif not (
                (context.effect.get("schema_version") in _REPLACEMENT_EXILE_SEARCH_VERSIONS and destination == "exile")
                or (dragon_search and destination == "graveyard")
                or (
                    (destination == "hand" or (
                        destination == "battlefield"
                        and _battlefield_search_can_resume(context.effect)
                    ))
                    and (len(values) <= 1 or is_counted_library_search(context.effect))
                )
            ):
                raise
            if is_up_to_two_forest_search(context.effect) and not forest_entry_replacement_is_private(
                required, seat=context.seat, selected_refs=values,
            ):
                raise
            from ..replacement_decisions import issue_replacement_order_choice

            resume_effect = copy.deepcopy(context.effect)
            completion: dict[str, Any] = {
                "actor": context.seat,
                "choice_id": decision.decision_id,
                "selected_refs": values,
            }
            if library_entry:
                pay_life = _search_entry_life_choice(context.response)
                if pay_life:
                    # An unaffordable choice must return to the original search decision.
                    card = self._resolve_object(
                        context.seat, values[0], zones={"library"}, owned_only=True,
                    )
                    amount = self._land_entry_life_amount(self.card_record(card))
                    try:
                        prepare_life_payment(self, context.seat, amount)
                    except LifeStateError as exc:
                        raise GameRuleError(str(exc)) from exc
                completion[_ENTRY_LIFE_CHOICE] = pay_life
            resume_effect[_SEARCH_COMPLETION_FIELD] = completion
            issue_replacement_order_choice(
                self,
                item=context.item,
                effect=resume_effect,
                remaining=[
                    dict(value)
                    for value in context.continuation.get("remaining", [])
                ],
                destination=context.continuation.get("destination"),
                note=str(context.continuation.get("note") or ""),
                instruction_pointer=int(
                    context.frame.get("instruction_pointer", 0)
                ),
                required=required,
            )
            return
        self._record_semantic_search_result(
            decision.decision_id,
            context,
            destination_spec=destination_spec,
            destination=destination,
            moved=moved,
        )
