from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence

from ..errors import GameRuleError
from ..life_state import LifeStateError, prepare_life_payment
from ..model import CardInstance, StackItem
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import object_matches_query, object_query_result
from ..replacement.immutable import FrozenMap, thaw_value
from ..replacement_effects import ReplacementChoiceRequired
from ..zone_transitions import ZoneTransitionOwner
from ..named_search_model import (
    LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION, decode_literal_library_search,
)
from ..library_search_model import (
    FETCH_ENTRY_PLACEMENT_VERSION, RECRUITING_LIBRARY_SEARCH_VERSION, RecruitingLibrarySearchSpec,
    TARGET_NAME_LIBRARY_SEARCH_VERSION, TargetNameLibrarySearchSpec,
    decode_fixed_tapped_battlefield_search,
    FixedCountedLibrarySearchTemplate, SEARCH_ALTERNATIVES, SEARCH_SUBTYPES, is_counted_library_search,
)
from ..rules.library_search_rules import library_search_constraint,library_search_object_ids
from .fetch_search_entry import (
    FETCH_OPERATION_ID, finish_fetch_selection, is_fetch_search_entry_resume,
    requires_private_land_entry_choice, resume_fetch_search_entry,
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


def _semantic_search_maximum(effect, minimum, option_count):
    if effect.get("schema_version") in (LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, RECRUITING_LIBRARY_SEARCH_VERSION):
        return option_count
    count = effect.get("count") or {}
    return min(option_count, max(minimum, int(count.get("maximum", minimum))))


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


@dataclass(frozen=True, slots=True)
class SemanticSearchCompletionContext:
    seat: str
    response: Mapping[str, Any]
    continuation: dict[str, Any]
    stack_ref: str
    item: StackItem
    frame: dict[str, Any]
    effect: dict[str, Any]
    options: frozenset[str]


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


class HiddenSearchOwnerMixin:
    """Own hidden-zone search advertisement, validation, and completion."""

    @staticmethod
    def _validate_counted_search_effect(effect: Mapping[str, Any]) -> None:
        if type(effect.get('schema_version')) is int and effect['schema_version'] == 9:
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
        if effect.get("schema_version") == RECRUITING_LIBRARY_SEARCH_VERSION:
            try:
                RecruitingLibrarySearchSpec.from_effect(effect)
            except ValueError as exc:
                raise GameRuleError(str(exc)) from exc
            return
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
        if is_counted_library_search(effect):
            try:
                FixedCountedLibrarySearchTemplate.from_effect(effect)
            except (ValueError, TypeError, KeyError) as exc:
                raise GameRuleError("Counted library search instruction is malformed") from exc

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
                                "entry_pay_life": "boolean",
                            },
                        }
                    ],
                }
            },
            continuation={
                "selection": self._fetch_selection_continuation(
                    item=item,
                    legal_refs=legal_refs,
                ).to_dict()
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
        finish_fetch_selection(
            self, item, selected=str(selected) if selected is not None else None,
            pay_life=_search_entry_life_choice(response),
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
        if predicate == "mana_cost_0_or_1":
            return record.mana_cost in {"{0}", "{1}"}
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

    def _semantic_search_options(
        self,
        seat: str,
        effect: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if effect.get('schema_version') == 9:
            from ..rules.targeted_library_exile import targeted_library_search_options
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
        return [
            {
                "id": self.state.cards[object_id].ref,
                "name": self.state.cards[object_id].printed_name,
            }
            for zone in zones
            for object_id in library_search_object_ids(self,seat,zone)
            if self._search_candidate_matches(self.state.cards[object_id], selector)
        ]

    @staticmethod
    def _search_is_restrictive(selector: Mapping[str, Any]) -> bool:
        return any(
            selector.get(key)
            for key in (
                "types",
                "types_any",
                "subtypes",
                "subtypes_any",
                "supertypes",
                "colors",
                "colors_any",
                "names",
                "mana_value",
                "mana_value_total",
                "predicate",
                SEARCH_ALTERNATIVES,
            )
        )

    def _semantic_search_choice_schema(
        self,
        *,
        effect: Mapping[str, Any],
        options: Sequence[Mapping[str, str]],
        minimum: int,
        maximum: int,
        rules_may_fail: bool,
    ) -> dict[str, Any]:
        entry_choice = any(
            (
                (record := self.card_record(
                    next(
                        card.object_id
                        for card in self.state.cards.values()
                        if card.ref == option["id"]
                    )
                ))
                is not None
                and self._land_entry_life_amount(record) > 0
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
        if effect.get('schema_version') == 5:
            from ..search_allocation_model import SearchAllocationSpec
            choice_schema.update(SearchAllocationSpec.from_effect(effect).choice_metadata())
        if effect.get("schema_version") == RECRUITING_LIBRARY_SEARCH_VERSION:
            choice_schema["order"] = "top_to_bottom"
        if entry_choice and (str(effect.get("destination")) == "battlefield" or effect.get('schema_version')==4):
            choice_schema["entry_pay_life"] = "boolean"
            choice_schema["example"]["entry_pay_life"] = False
        return choice_schema

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
        if effect.get("schema_version") == TARGET_NAME_LIBRARY_SEARCH_VERSION:
            from ..rules.qualified_library_search import bind_target_name_library_search
            effect = bind_target_name_library_search(self, item.controller, effect)
        if effect.get('schema_version') == 8:
            from ..rules.query_bound_library_search import materialize_query_bound_search
            effect=materialize_query_bound_search(self,item,effect,instruction_pointer)
        self._validate_counted_search_effect(effect)
        seat = str(effect.get("searching_player") or item.controller)
        self._require_seat(seat, in_game=True)
        if effect.get('schema_version') == 9:
            from ..rules.targeted_library_exile import targeted_library_departed,bind_targeted_library_exile
            if targeted_library_departed(self, seat, effect):
                self._continue_resolution(stack_ref=item.ref, effects=list(remaining),
                    destination=destination, note=note, instruction_pointer=instruction_pointer + 1)
                return
            effect = bind_targeted_library_exile(self, seat, effect)
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
        if effect.get('schema_version') == 6:
            from ..rules.named_searches import named_zone_search_minimum
            minimum_choice = named_zone_search_minimum(self, seat, effect, options)
            rules_may_fail = minimum_choice == 0
        choice_schema = self._semantic_search_choice_schema(
            effect=effect,
            options=options,
            minimum=minimum_choice,
            maximum=maximum,
            rules_may_fail=rules_may_fail,
        )
        frame = self._semantic_frame(
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
        selection = self._search_selection_continuation(actor=seat, item=item,
            effect=effect, remaining=remaining, destination=destination, note=note,
            frame=frame, legal_refs=tuple(option['id'] for option in options))
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
                        or "Choose card(s) matching the search specification."
                    ),
                    **(self._bounded_library_search_view(seat,
                        library_player=effect.get('library_player') if effect.get('schema_version') == 9 else None)
                       if 'library' in search_zones else {}),
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
                        **({"shuffle_before_placement": True}
                           if effect.get("schema_version") == RECRUITING_LIBRARY_SEARCH_VERSION else {}),
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
            continuation={'selection':selection.to_dict()},
        )
        frame["pending_choice_id"] = decision.decision_id
        decision.continuation['selection'] = replace(selection,
            payload=FrozenMap({**selection.payload, 'semantic_frame':frame})).to_dict()

    def _bounded_library_search_view(self,seat,library_player=None):
        constraint=library_search_constraint(self,seat)
        if constraint.prohibited or constraint.top_count is None:return {}
        owner = seat if library_player is None else library_player
        return {'search_window':[{'id':self.state.cards[oid].ref,'name':self.state.cards[oid].printed_name}
            for oid in library_search_object_ids(self,owner,'library',searching_player=seat)]}

    def _finish_prohibited_library_search(self,*,item,effect,remaining,destination,note,instruction_pointer,seat):
        self._log(seat,'library.search.prohibited',f'{seat} could not search their library.',{'source':item.ref},importance=1)
        if not effect.get('optional',False) and (effect.get('shuffle_after',True) or effect.get('shuffle_before_placement',False)):
            if effect.get('schema_version') == 9:
                from ..rules.targeted_library_exile import targeted_library_owner
            owner = targeted_library_owner(self, seat, effect) if effect.get('schema_version') == 9 else seat
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
        options = {
            option["id"]
            for option in self._semantic_search_options(seat, effect)
        }
        issued_options = {
            str(value) for value in continuation.get("legal_refs", ())
        }
        if effect.get('schema_version') == 9:
            from ..rules.targeted_library_exile import targeted_library_departed
        departed = effect.get('schema_version') == 9 and targeted_library_departed(self, seat, effect)
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
        explicit_search_cards = response.get("search_cards")
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
        if effect.get('schema_version') == 6:
            from ..rules.named_searches import named_zone_search_minimum
            required = named_zone_search_minimum(self, seat,
                {key:value for key,value in effect.items() if key != _SEARCH_COMPLETION_FIELD}, options)
        if not required <= len(values) <= maximum:
            raise GameRuleError(f"Search requires between {required} and {maximum} selection(s)")
        total_constraint = selector.get("mana_value_total")
        if total_constraint is not None:
            constraint = (
                dict(total_constraint)
                if isinstance(total_constraint, Mapping)
                else {"maximum": total_constraint}
            )
            total = sum(
                float(
                    self.card_record(
                        self._resolve_object(
                            seat,
                            ref,
                            zones=set(search_zones),
                            owned_only=True,
                        )
                    ).mana_value
                )
                for ref in values
            )
            if (
                constraint.get("minimum") is not None
                and total < float(constraint["minimum"])
            ) or (
                constraint.get("maximum") is not None
                and total > float(constraint["maximum"])
            ):
                raise GameRuleError(
                    "Selected search cards do not satisfy the aggregate "
                    "mana-value constraint"
                )
        return values, search_zones

    @staticmethod
    def _semantic_search_destination(
        effect: Mapping[str, Any],
    ) -> tuple[str, str, str | int]:
        if effect.get('schema_version') == 5:return 'allocated', 'hand', 'top'
        destination_spec = str(effect.get("destination") or "hand")
        position: str | int = effect.get(
            "destination_position",
            "top",
        )
        if position is None or position == "":
            position = "top"
        destination = destination_spec
        if destination_spec in {"library_top", "top_of_library"}:
            destination, position = "library", "top"
        elif destination_spec in {"library_bottom", "bottom_of_library"}:
            destination, position = "library", "bottom"
        if destination not in {
            "hand",
            "battlefield",
            "graveyard",
            "exile",
            "library",
        }:
            raise GameRuleError(
                f"Unsupported semantic search destination {destination_spec!r}"
            )
        return destination_spec, destination, position

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
        if effect.get('schema_version') == 9:
            from ..rules.targeted_library_exile import move_targeted_library_exile
            return move_targeted_library_exile(self, context, values, replacement_selections)
        if effect.get('schema_version') == 8:
            from ..rules.query_bound_library_search import materialize_query_bound_search
            retained={k:v for k,v in effect.items() if k not in {_SEARCH_COMPLETION_FIELD,_REPLACEMENT_SELECTIONS_FIELD}}
            materialize_query_bound_search(self,item,retained,context.frame['instruction_pointer'])
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
        owner = ZoneTransitionOwner(self)
        if effect.get('schema_version') == 5:
            from ..search_allocation import move_allocated_search_results
            return move_allocated_search_results(self, context, selected_cards, replacement_selections)
        if is_counted_library_search(effect):
            self._validate_counted_search_effect(effect)
            if effect.get("schema_version") == RECRUITING_LIBRARY_SEARCH_VERSION:
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
            elif len(selected_cards) > 1 and destination == "hand":
                owner.reveal_library_search_results(seat, tuple(card.object_id for card in selected_cards), public=reveal)
                moved = owner.move_cards_simultaneously(
                    tuple((card.object_id, destination) for card in selected_cards),
                    reason=f"{item.label} search", log=False,
                    replacement_selections=replacement_selections,
                )
                if reveal:
                    owner.reveal_library_search_results(
                        seat, tuple(card.object_id for card in moved if card.zone == "hand"),
                        public=True, expected_zone="hand",
                    )
                return moved
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
            moved_card = self.move_card(
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
            )
            moved.append(moved_card)
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
        public_choice = reveal or destination in {
            "battlefield",
            "graveyard",
            "exile",
        }
        public_details: dict[str, Any] = {
            "source": item.ref,
            "destination": destination_spec,
            "count": len(moved),
            "revealed": reveal,
        }
        if effect.get('schema_version') == 6:
            public_details['searched_zones'] = list(effect['zone'])
        from ..search_allocation import allocated_search_result_details
        allocation_details = allocated_search_result_details(effect, moved)
        if reveal:public_details.update(allocation_details)
        public_order = (sorted(moved, key=lambda card: card.ref)
                        if effect.get("schema_version") == RECRUITING_LIBRARY_SEARCH_VERSION else moved)
        if public_choice:
            public_cards = ([card for card in moved if card.zone in {'battlefield','graveyard','exile','command'}
                and not card.face_down] if effect.get('schema_version') == 9 else list(public_order))
            public_details["objects"] = [card.ref for card in public_cards]
            if len(public_cards) == 1:
                public_details["object"] = public_cards[0].ref
        self._log(
            seat,
            "library.search",
            f"{seat} searched {effect.get('zone', 'library')} and found "
            f"{len(moved)} card(s).",
            public_details,
            importance=2,
            changed_objects=[card.object_id for card in public_order],
            changed_players=[seat],
        )
        self._log(
            seat,
            "library.search.private",
            f"{seat} selected {len(moved)} private search object(s).",
            {
                **public_details,
                **allocation_details,
                "objects": [card.ref for card in moved],
            },
            visibility=[seat, "analyst"],
            importance=0,
            changed_objects=[card.object_id for card in moved],
            changed_players=[seat],
        )
        if bool(effect.get("shuffle_after", True)):
            if effect.get('schema_version') == 9:
                from ..rules.targeted_library_exile import targeted_library_owner
            owner = targeted_library_owner(self, seat, effect) if effect.get('schema_version') == 9 else seat
            self.shuffle_library(owner, reason=f"{item.label} resolved")
        item.context.setdefault("semantic_continuations", []).append(
            {
                **context.frame,
                "pending_choice_id": choice_id,
                "choice_result": [card.ref for card in moved],
                "resumed": True,
            }
        )
        self._continue_resolution(
            stack_ref=context.stack_ref,
            effects=[
                dict(value)
                for value in context.continuation.get("remaining", [])
            ],
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
            effect.get("destination") == "hand" or effect.get("schema_version") == 8
        )
        maximum = effect.get("count", {}).get("maximum", 1) if counted or effect.get('schema_version') == 9 else 1
        any_number = effect.get("schema_version") == LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION
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
        seat = str(base_effect.get("searching_player") or item.controller)
        if actor != seat:
            raise GameRuleError("Semantic search completion actor changed")
        options = {
            option["id"]
            for option in self._semantic_search_options(seat, base_effect)
        }
        if base_effect.get('schema_version') == 9:
            from ..rules.targeted_library_exile import targeted_library_departed
        departed = base_effect.get('schema_version') == 9 and targeted_library_departed(self, seat, base_effect)
        if any(value not in options for value in selected) and not departed:
            raise GameRuleError("Semantic search completion candidate changed")
        destination_spec, resolved_destination, position = (
            self._semantic_search_destination(base_effect)
        )
        response: dict[str, Any] = {"search_cards": selected}
        if _ENTRY_LIFE_CHOICE in raw_completion:
            pay_entry_life = raw_completion[_ENTRY_LIFE_CHOICE]
            if type(pay_entry_life) is not bool or not _single_library_battlefield_entry(
                base_effect, selected, resolved_destination
            ):
                raise GameRuleError("Malformed semantic search entry life choice")
            response[_ENTRY_LIFE_CHOICE] = pay_entry_life
        elif (resolved_destination != "hand"
                and not (base_effect.get("schema_version") in (8, TARGET_NAME_LIBRARY_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION)
                         and resolved_destination == "battlefield")
                and not (base_effect.get("schema_version") == 9 and resolved_destination == "exile")):
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
        if context.effect.get('schema_version') == 9:
            from ..rules.targeted_library_exile import targeted_library_departed,continue_departed_target_search
        if context.effect.get('schema_version') == 9 and targeted_library_departed(self, context.seat, context.effect):
            continue_departed_target_search(self, context)
            return
        raw_selections = effect.get(_REPLACEMENT_SELECTIONS_FIELD, ())
        if (
            not isinstance(raw_selections, Sequence)
            or isinstance(raw_selections, (str, bytes))
        ):
            raise GameRuleError("Semantic search replacements are malformed")
        try:
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
                _ENTRY_LIFE_CHOICE in context.response
                and required.pending.choice.chooser != context.seat
            ):
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

    def _complete_semantic_search(self, decision: Any) -> None:
        context = self._semantic_search_completion_context(decision)
        if context.effect.get('schema_version') == 9:
            from ..rules.targeted_library_exile import targeted_library_departed,continue_departed_target_search
        if context.effect.get('schema_version') == 9 and targeted_library_departed(self, context.seat, context.effect):
            continue_departed_target_search(self, context)
            return
        values, search_zones = self._semantic_search_selected_values(context)
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
            library_entry = _single_library_battlefield_entry(
                context.effect, values, destination
            )
            if library_entry:
                if not requires_private_land_entry_choice(required, context.seat):
                    raise
            elif not (
                (context.effect.get("schema_version") == 9 and destination == "exile")
                or (
                    (destination == "hand" or (
                        destination == "battlefield"
                        and context.effect.get("schema_version") in (8, TARGET_NAME_LIBRARY_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION)
                    ))
                    and (len(values) <= 1 or is_counted_library_search(context.effect))
                )
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
