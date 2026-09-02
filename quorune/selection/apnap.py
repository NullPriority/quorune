from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Protocol, Sequence

from ..errors import GameRuleError
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import object_matches_query, object_query_result
from ..replacement.immutable import FrozenMap, thaw_value
from ..semantic_choices.apnap_commit import APNAP_OBJECT_COMMIT_OPERATION
from .model import (
    SelectionContract,
    SelectionContinuation,
    SelectionModelError,
    decode_selection_continuation,
)


APNAP_OPERATION_ID = "selection.nontarget.apnap.v1"


class ApnapChoiceHost(Protocol):
    state: Any
    active_seats: Sequence[str]
    permissions: Any

    def apnap_order(self) -> list[str]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


class ApnapChoiceOwnerMixin:
    """Own ordered multiplayer collection and simultaneous choice commit."""

    def _apnap_selection_continuation(
        self,
        *,
        actor: str,
        state: Mapping[str, Any],
        legal_refs: tuple[str, ...],
    ) -> SelectionContinuation:
        resume = dict(state.get("resume") or {})
        stack_ref = str(resume.get("stack_ref") or "") or None
        item = next(
            (
                candidate
                for candidate in self.state.stack
                if candidate.ref == stack_ref
            ),
            None,
        )
        source_ref = self._stack_source_ref(item) if item is not None else None
        effect = dict(state.get("effect") or {})
        return SelectionContinuation(
            contract=SelectionContract.NONTARGET_CHOICE,
            operation_id=APNAP_OPERATION_ID,
            actor=actor,
            state_revision=self.state.revision,
            stack_ref=stack_ref,
            source_ref=source_ref,
            visibility="actor_private" if bool(effect.get("hidden")) else "public",
            payload=FrozenMap(
                {
                    "choice_state": dict(state),
                    "legal_refs": legal_refs,
                }
            ),
        )

    def _choice_options(self, seat: str, effect: Mapping[str, Any]) -> list[str]:
        zone = str(effect.get("zone") or "battlefield")
        raw_predicate = effect.get("predicate")
        predicate: ObjectQuerySpec | None = None
        if raw_predicate is not None:
            try:
                predicate = ObjectQuerySpec.from_dict(raw_predicate)
            except (ObjectQueryError, TypeError) as exc:
                raise GameRuleError(str(exc)) from exc
            if (
                predicate.zones not in {(), (zone,)}
                or predicate.owner is not None
                or predicate.controller is not None
                or predicate.known_to_actor is not None
                or predicate.exclude_ref is not None
                or predicate.include_phased_out
            ):
                raise GameRuleError(
                    "APNAP object choices require a controller-bound "
                    "zone predicate"
                )
            predicate = replace(
                predicate,
                zones=(zone,),
                controller=seat,
            )
        card_type = str((effect.get("filter") or {}).get("type") or "").casefold()
        controller_only = bool((effect.get("filter") or {}).get("controlled", True))
        candidates: list[str] = []
        excluded_ref = str(effect.get("exclude_ref") or "")
        for object_id in self.state.players[seat].zones.get(zone, []):
            card = self.state.cards[object_id]
            if excluded_ref and card.ref == excluded_ref:
                continue
            if controller_only and zone == "battlefield" and card.controller != seat:
                continue
            if card_type and card_type not in str(self._effective_card_data(card).get("type_line") or "").casefold():
                continue
            if predicate is not None:
                effective = self._effective_card_data(card)
                types, subtypes, supertypes = self._type_parts(
                    str(effective.get("type_line") or "")
                )
                row = object_query_result(
                    card,
                    effective,
                    type_parts=(types, subtypes, supertypes),
                    known_to_actor=True,
                    attached_to_ref=(
                        self.state.cards[card.attached_to].ref
                        if card.attached_to in self.state.cards
                        else None
                    ),
                    entered_this_turn=(
                        card.zone == "battlefield"
                        and card.entered_battlefield_turn_sequence > 0
                        and card.entered_battlefield_turn_sequence
                        == self.state.turn_sequence
                    ),
                )
                if not object_matches_query(row, predicate):
                    continue
            candidates.append(card.ref)
        return candidates

    def _issue_apnap_choice(self, *, effect: Mapping[str, Any], continuation: Mapping[str, Any]) -> None:
        players_spec = effect.get("players", "all")
        if players_spec == "all":
            queue = self.apnap_order()
        elif players_spec == "opponents":
            actor = str(effect.get("actor") or self.state.stack[-1].controller)
            if actor not in self.active_seats:
                raise GameRuleError("APNAP opponent choice actor is unavailable")
            queue = [seat for seat in self.apnap_order() if seat != actor]
        else:
            if (
                not isinstance(players_spec, (list, tuple))
                or not players_spec
                or any(
                    type(seat) is not str or seat not in self.active_seats
                    for seat in players_spec
                )
                or len(players_spec) != len(set(players_spec))
            ):
                raise GameRuleError("APNAP choice players are malformed")
            selected_players = set(players_spec)
            queue = [
                seat
                for seat in self.apnap_order()
                if seat in selected_players
            ]
        count = effect.get("count", 1)
        if type(count) is not int or count <= 0:
            raise GameRuleError("APNAP choice count must be positive")
        if effect.get("predicate") is not None:
            private_discard = (
                effect.get("zone") == "hand"
                and effect.get("then") == "discard"
            )
            allowed_fields = {
                "op",
                "actor",
                "players",
                "zone",
                "predicate",
                "count",
                "then",
                "prompt",
                *(("exclude_ref",) if "exclude_ref" in effect else ()),
                *(("require_full_count", "fallback_effects") if effect.get("require_full_count") is True else ()),
                *(("hidden",) if private_discard else ()),
                *(('target',) if "target" in effect else ()),
            }
            stack_ref = str(continuation.get("stack_ref") or "")
            item = next(
                (
                    candidate
                    for candidate in self.state.stack
                    if candidate.ref == stack_ref
                ),
                None,
            )
            if (
                set(effect) != allowed_fields
                or effect.get("op") != "choose_cards_apnap"
                or (
                    effect.get("zone"),
                    effect.get("then"),
                    effect.get("hidden", False),
                )
                not in {
                    ("battlefield", "sacrifice", False),
                    ("battlefield", "return_owner_hand", False),
                    ("hand", "discard", True),
                }
                or type(effect.get("actor")) is not str
                or effect.get("actor") not in self.active_seats
                or item is None
                or effect.get("actor") != item.controller
                or (
                    "target" in effect
                    and effect.get("players") != [effect.get("target")]
                )
                or type(effect.get("prompt")) is not str
                or not str(effect.get("prompt")).strip()
                or (
                    "exclude_ref" in effect
                    and (type(effect.get("exclude_ref")) is not str or not effect.get("exclude_ref"))
                )
                or (
                    effect.get("require_full_count") is True
                    and (
                        effect.get("then") != "return_owner_hand"
                        or not isinstance(effect.get("fallback_effects"), list)
                        or not effect.get("fallback_effects")
                    )
                )
            ):
                raise GameRuleError("Typed APNAP choice effect is malformed")
        choice_state = {
            "queue": queue,
            "player_order": list(queue),
            "selected": {},
            "effect": dict(effect),
            "resume": dict(continuation),
        }
        self._issue_next_apnap_choice(choice_state)

    def _issue_next_apnap_choice(self, state: dict[str, Any]) -> None:
        queue = list(state["queue"])
        if not queue:
            self._apply_apnap_choices(state)
            return
        seat = queue[0]
        effect = state["effect"]
        options = self._choice_options(seat, effect)
        count = min(int(effect.get("count", 1)), len(options))
        if effect.get("require_full_count") is True and count < int(
            effect.get("count", 1)
        ):
            selected = dict(state["selected"])
            selected[seat] = []
            state["selected"] = selected
            state["queue"] = queue[1:]
            self._issue_next_apnap_choice(state)
            return
        if count == 0:
            selected = dict(state["selected"])
            selected[seat] = []
            state["selected"] = selected
            state["queue"] = queue[1:]
            self._issue_next_apnap_choice(state)
            return
        self.permissions.issue(
            kind="choice.apnap",
            role="pilot",
            actors=[seat],
            allowed_actions=["choose"],
            payload_by_actor={
                seat: {
                    "prompt": str(effect.get("prompt") or "Choose card(s)"),
                    "count": count,
                    "options": options,
                    "prior_public_choices": dict(state["selected"]) if not effect.get("hidden") else {},
                }
            },
            continuation={
                "selection": self._apnap_selection_continuation(
                    actor=seat,
                    state=state,
                    legal_refs=tuple(options),
                ).to_dict()
            },
        )

    def _complete_apnap_choice(self, decision: Any) -> None:
        seat = decision.actors[0]
        raw_continuation = decision.continuation
        legacy_state = dict(raw_continuation.get("choice_state") or {})
        legacy = SelectionContinuation(
            contract=SelectionContract.NONTARGET_CHOICE,
            operation_id=APNAP_OPERATION_ID,
            actor=seat,
            state_revision=decision.created_revision,
            stack_ref=str(
                dict(legacy_state.get("resume") or {}).get("stack_ref") or ""
            )
            or None,
            visibility=(
                "actor_private"
                if bool(dict(legacy_state.get("effect") or {}).get("hidden"))
                else "public"
            ),
            payload=FrozenMap(
                {
                    "choice_state": legacy_state,
                    "legal_refs": self._choice_options(
                        seat, dict(legacy_state.get("effect") or {})
                    )
                    if legacy_state
                    else (),
                }
            ),
        )
        try:
            selection = decode_selection_continuation(
                raw_continuation,
                expected_contract=SelectionContract.NONTARGET_CHOICE,
                expected_operation_id=APNAP_OPERATION_ID,
                legacy=legacy,
            )
        except SelectionModelError as exc:
            raise GameRuleError(str(exc)) from exc
        if selection.actor != seat:
            raise GameRuleError("APNAP choice actor changed")
        if selection.state_revision != decision.created_revision:
            raise GameRuleError("APNAP choice state revision changed")
        payload = thaw_value(selection.payload)
        state = dict(payload.get("choice_state") or {})
        effect = dict(state.get("effect") or {})
        expected_visibility = (
            "actor_private" if bool(effect.get("hidden")) else "public"
        )
        if selection.visibility != expected_visibility:
            raise GameRuleError("APNAP choice visibility changed")
        queue = list(state.get("queue") or [])
        if not queue or queue[0] != seat:
            raise GameRuleError("APNAP choice queue actor changed")
        resume = dict(state.get("resume") or {})
        if str(resume.get("stack_ref") or "") != str(
            selection.stack_ref or ""
        ):
            raise GameRuleError("APNAP choice stack identity changed")
        if "selection" in raw_continuation and selection.stack_ref:
            item = next(
                (
                    candidate
                    for candidate in self.state.stack
                    if candidate.ref == selection.stack_ref
                ),
                None,
            )
            if item is None:
                raise GameRuleError(
                    "The APNAP choice's stack object no longer exists"
                )
            if selection.source_ref != self._stack_source_ref(item):
                raise GameRuleError("APNAP choice source identity changed")
        response = decision.responses[seat]
        values = list(response.get("cards") or response.get("choices") or [])
        options = self._choice_options(seat, effect)
        issued_options = tuple(str(value) for value in payload.get("legal_refs", ()))
        if "selection" in raw_continuation and tuple(options) != issued_options:
            raise GameRuleError("APNAP choice candidates changed")
        required = min(int(effect.get("count", 1)), len(options))
        if len(values) != required:
            raise GameRuleError(f"{seat} must choose exactly {required} option(s)")
        refs: list[str] = []
        for value in values:
            card = self._resolve_object(
                seat,
                str(value),
                zones={str(effect.get("zone") or "battlefield")},
            )
            if card.ref not in options or card.ref in refs:
                raise GameRuleError("Invalid or duplicate APNAP choice")
            refs.append(card.ref)
        selected = dict(state["selected"])
        selected[seat] = refs
        queue = list(state["queue"])[1:]
        state["selected"] = selected
        state["queue"] = queue
        self._issue_next_apnap_choice(state)

    def _apply_apnap_choices(self, state: dict[str, Any]) -> None:
        effect = state["effect"]
        then = str(effect.get("then") or "sacrifice")
        selected_refs: list[str] = []
        player_order = list(
            state.get("player_order") or state["selected"].keys()
        )
        for seat in player_order:
            refs = state["selected"].get(seat, ())
            for ref in refs:
                if ref in selected_refs:
                    raise GameRuleError("APNAP choices contain a duplicate object")
                selected_refs.append(ref)
        destination = {
            "sacrifice": "graveyard",
            "discard": "graveyard",
            "exile": "exile",
            "return_owner_hand": "hand",
        }.get(then)
        if destination is None:
            raise GameRuleError(f"Unsupported APNAP continuation {then}")
        resume = state["resume"]
        stack_ref = str(resume.get("stack_ref") or "")
        item = next(
            (
                candidate
                for candidate in self.state.stack
                if candidate.ref == stack_ref
            ),
            None,
        )
        actor = str(effect.get("actor") or "") or (
            item.controller if item is not None else ""
        )
        if not actor:
            raise GameRuleError("APNAP choice commit has no controller")
        semantic_frame = dict(resume.get("semantic_frame") or {})
        instruction_pointer = int(
            semantic_frame.get("instruction_pointer", 0)
        )
        full_count_required = effect.get("require_full_count") is True
        full_count_paid = len(selected_refs) == int(effect.get("count", 1))
        if full_count_required and not full_count_paid:
            commit_effects = [
                dict(value) for value in effect.get("fallback_effects", [])
            ]
        else:
            commit_effects = [
                {
                    "op": APNAP_OBJECT_COMMIT_OPERATION,
                    "actor": actor,
                    "object_refs": selected_refs,
                    "expected_zones": [
                        str(effect.get("zone") or "battlefield")
                    ],
                    "destination": destination,
                    "reason": (
                        f"simultaneous APNAP {then}"
                        if then != "exile"
                        else "simultaneous APNAP choice"
                    ),
                    "event_code": f"choice.{then}",
                    "message": f"Applied simultaneous {then} choices.",
                }
            ]
        self._continue_resolution(
            stack_ref=stack_ref,
            effects=[
                *commit_effects,
                *(dict(item) for item in resume.get("effects", [])),
            ],
            destination=resume.get("destination"),
            note=str(resume.get("note") or ""),
            instruction_pointer=instruction_pointer,
        )
