from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from ..errors import GameRuleError
from ..model import CardInstance, StackItem
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import object_matches_query, object_query_result
from ..replacement.immutable import FrozenMap, thaw_value
from ..replacement_effects import ReplacementChoiceRequired
from ..zone_transitions import ZoneTransitionOwner
from .model import (
    SelectionContract,
    SelectionContinuation,
    SelectionModelError,
    decode_selection_continuation,
)


SEARCH_OPERATION_ID = "selection.search.semantic.v1"
FETCH_OPERATION_ID = "selection.search.fetch.v1"
_SEARCH_COMPLETION_FIELD = "_search_completion"
_SEARCH_COMPLETION_FIELDS = {"actor", "choice_id", "selected_refs"}
_REPLACEMENT_SELECTIONS_FIELD = "_replacement_selections"


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
        for object_id in self.state.players[seat].zones["library"]:
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
        return {
            "builtin": "fetch_land",
            "land_types": list(land_types),
            "search_card": selected or None,
            "choice_made": bool(selected),
            "pay_life": bool(response.get("entry_pay_life", False)),
        }

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
                }
            ),
        )

    def _begin_fetch_search(self, item: StackItem) -> None:
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
        if "selection" in raw_continuation:
            payload = thaw_value(selection.payload)
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
        item.context["search_card"] = (
            str(selected) if selected is not None else None
        )
        item.context["choice_made"] = True
        item.context["pay_life"] = bool(
            response.get(
                "entry_pay_life",
                response.get("pay_life", False),
            )
        )
        self._resolve_fetch_land(item)

    def _resolve_fetch_land(self, item: StackItem) -> None:
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
                entry_pay_life=pay_entry_life,
                reason=f"{item.label} search",
                log=False,
                semantic_events=True,
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
                "library.search",
                f"{seat} did not find a card.",
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

    def _search_candidate_matches(
        self,
        card: CardInstance,
        selector: Mapping[str, Any],
    ) -> bool:
        record = self.card_record(card)
        if record is None:
            return False
        effective = self._effective_card_data(card)
        types, subtypes, supertypes = self._type_parts(
            str(effective.get("type_line") or "")
        )
        try:
            query = ObjectQuerySpec(
                zones=(card.zone,),
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
        row = object_query_result(
            card,
            effective,
            type_parts=(types, subtypes, supertypes),
            known_to_actor=True,
            attached_to_ref=None,
        )
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

    def _semantic_search_options(
        self,
        seat: str,
        effect: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
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
        return [
            {
                "id": self.state.cards[object_id].ref,
                "name": self.state.cards[object_id].printed_name,
            }
            for zone in zones
            for object_id in self.state.players[seat].zones[zone]
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
        if entry_choice and str(effect.get("destination")) == "battlefield":
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
        seat = str(effect.get("searching_player") or item.controller)
        self._require_seat(seat, in_game=True)
        options = self._semantic_search_options(seat, effect)
        count = dict(effect.get("count") or {})
        minimum = max(0, int(count.get("minimum", 1)))
        maximum = max(minimum, int(count.get("maximum", minimum)))
        maximum = min(maximum, len(options))
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
            continuation={
                "selection": self._search_selection_continuation(
                    actor=seat,
                    item=item,
                    effect=effect,
                    remaining=remaining,
                    destination=destination,
                    note=note,
                    frame=frame,
                    legal_refs=tuple(option["id"] for option in options),
                ).to_dict()
            },
        )
        frame["pending_choice_id"] = decision.decision_id
        decision.continuation["selection"] = (
            self._search_selection_continuation(
                actor=seat,
                item=item,
                effect=effect,
                remaining=remaining,
                destination=destination,
                note=note,
                frame=frame,
                legal_refs=tuple(option["id"] for option in options),
            ).to_dict()
        )

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
        if "selection" in raw_continuation and issued_options != options:
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
        maximum = max(minimum, int(count.get("maximum", minimum)))
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
        if not required <= len(values) <= min(maximum, len(options)):
            raise GameRuleError(
                f"Search requires between {required} and "
                f"{min(maximum, len(options))} selection(s)"
            )
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
                reason=f"{item.label} search",
                log=False,
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
                pay_entry_life = bool(
                    response.get(
                        "entry_pay_life",
                        response.get("pay_life", False),
                    )
                )
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
        if public_choice:
            public_details["objects"] = [card.ref for card in moved]
            if len(moved) == 1:
                public_details["object"] = moved[0].ref
        self._log(
            seat,
            "library.search",
            f"{seat} searched {effect.get('zone', 'library')} and found "
            f"{len(moved)} card(s).",
            public_details,
            importance=2,
            changed_objects=[card.object_id for card in moved],
            changed_players=[seat],
        )
        self._log(
            seat,
            "library.search.private",
            f"{seat} selected {len(moved)} private search object(s).",
            {
                **public_details,
                "objects": [card.ref for card in moved],
            },
            visibility=[seat, "analyst"],
            importance=0,
            changed_objects=[card.object_id for card in moved],
            changed_players=[seat],
        )
        if bool(effect.get("shuffle_after", True)):
            self.shuffle_library(seat, reason=f"{item.label} resolved")
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
            or set(raw_completion) != _SEARCH_COMPLETION_FIELDS
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
        if len(selected) != len(set(selected)) or len(selected) > 1:
            raise GameRuleError(
                "Replacement-resumed semantic search must select at most one card"
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
        seat = str(base_effect.get("searching_player") or item.controller)
        if actor != seat:
            raise GameRuleError("Semantic search completion actor changed")
        options = {
            option["id"]
            for option in self._semantic_search_options(seat, base_effect)
        }
        if any(value not in options for value in selected):
            raise GameRuleError("Semantic search completion candidate changed")
        destination_spec, resolved_destination, position = (
            self._semantic_search_destination(base_effect)
        )
        if resolved_destination != "hand":
            raise GameRuleError(
                "Replacement-resumed semantic search must request the hand"
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
                response={"search_cards": selected},
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
        values, search_zones = self._semantic_search_selected_values(context)
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
            if destination != "hand" or len(values) > 1:
                raise
            from ..replacement_decisions import issue_replacement_order_choice

            resume_effect = copy.deepcopy(context.effect)
            resume_effect[_SEARCH_COMPLETION_FIELD] = {
                "actor": context.seat,
                "choice_id": decision.decision_id,
                "selected_refs": values,
            }
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
