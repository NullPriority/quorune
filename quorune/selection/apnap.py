from __future__ import annotations

from ..apnap_object_payment import (
    DECLINED_LIFE_LOSS, ORDER_GRAVEYARD_ARRIVALS, OTHER_PLAYER_SACRIFICE_SCOPE,
    object_payment_cardinalities,
)

from dataclasses import replace
from typing import Any, Mapping, Protocol, Sequence

from ..errors import GameRuleError
from ..library_order_model import HAND_TOP_APNAP_RESULT, HAND_TOP_FIRST, is_affected_hand_top_choice
from ..linked_exile_return_model import (
    LINKED_ABILITY_TIMING, LINKED_EXILE_RETURN_OPERATION, LINKED_FADING_EXILE_VERSION, LINKED_FADING_HAND_BINDING,
    LinkedExileReturnSpec, LinkedFadingScope,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import object_matches_query, object_query_result
from ..power_sacrifice_model import (
    ATTACK_SACRIFICE_STAT_FIELD, CONTROLLER_SACRIFICE_LIFE_FIELD, ENTRY_SACRIFICE_POWER_DRAW_FIELD,
    GREATEST_POWER_SACRIFICE_FIELD, GREATEST_POWER_SACRIFICE_INCARNATIONS,
)
from ..public_characteristic_aggregates import greatest_power_creatures
from ..public_quantity_reductions import public_mana_value
from ..rules.counted_sacrifice_tokens import COUNTED_SACRIFICE_TOKEN_FIELD, counted_sacrifice_token_commit_fields
from ..rules.ordered_opponent_creature_sacrifice import ordered_opponent_sacrifice_commit_fields
from ..rules.any_number_sacrifice import (
    ANY_NUMBER_SACRIFICE_FIELD, any_number_sacrifice_commit_fields, any_number_sacrifice_count,
    any_number_sacrifice_row_matches, zero_land_sacrifice_search,
)
from ..rules.target_sacrifice_toughness import TARGET_SACRIFICE_TOUGHNESS_FIELD
from ..rules.opponent_caster_discard_payment import uses_caster_discard_payment
from ..rules.selected_discard_draw import SELECTED_DISCARD_DRAW_FIELD, selected_discard_draw_followup
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.counter_marked_keep import KEEP_COUNTER_COHORT, KEEP_COUNTER_FIELD, keep_counter_followups
from ..rules.keep_three_sacrifice import (
    KEEP_THREE_OFFERED_FIELD, KEEP_THREE_SACRIFICE_FIELD, unkept_permanents_in_apnap_order,
)
from ..semantic_choices.apnap_commit import APNAP_OBJECT_COMMIT_OPERATION
from .apnap_contract import (
    bind_most_lands_sacrifice_players, sacrifice_life_commit_fields, greatest_power_sacrifice_players, is_typed_hand_exile,
    sacrifice_can_decline, revalidate_sacrifice_incarnations, revalidate_ordered_hand_choices,
    caster_discard_player_for_choice, validate_apnap_choice_effect,
)
from ..rules.shared_type_sacrifice import SHARED_TYPE_SACRIFICE
from .shared_type_sacrifice import shared_sacrifice_followup
from .fractional_counts import seal_fractional_counts, apnap_required_count
from .alternative_discard import alternative_discard_options,validate_alternative_discard_choice
from ..alternative_discard_model import alternative_discard_count
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
        from ..sacrifice_prohibition_model import sacrifice_is_prohibited

        sacrifice = effect.get("then") == "sacrifice" and KEEP_COUNTER_FIELD not in effect
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
        greatest = effect.get(GREATEST_POWER_SACRIFICE_FIELD)
        greatest_rows = [] if GREATEST_POWER_SACRIFICE_FIELD in effect else None
        rank_mana_value = isinstance(greatest, Mapping) and greatest.get("schema_version") == 3
        mana_values: dict[str, int] = {}
        excluded_ref = str(effect.get("exclude_ref") or "")
        for object_id in self.state.players[seat].zones.get(zone, []):
            card = self.state.cards[object_id]
            if sacrifice and greatest_rows is None and sacrifice_is_prohibited(self.state, card):
                continue
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
                if not object_matches_query(row, predicate) or not any_number_sacrifice_row_matches(effect, row):
                    continue
                if greatest_rows is not None:
                    greatest_rows.append(row)
                    if rank_mana_value:
                        try:
                            mana_values[row.ref] = public_mana_value(effective)
                        except ValueError as exc:
                            raise GameRuleError(str(exc)) from exc
            candidates.append(card.ref)
        if greatest_rows is None:
            return candidates
        try:
            if rank_mana_value:
                maximum = max(mana_values.values(), default=None)
                ranked = (row for row in greatest_rows if mana_values[row.ref] == maximum)
            else:
                ranked = greatest_power_creatures(greatest_rows)
            # A prohibition removes a top option after ranking, never promotes a lower one.
            return [row.ref for row in ranked
                    if not sacrifice or not sacrifice_is_prohibited(self.state, row)]
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc

    def _issue_apnap_choice(self, *, effect: Mapping[str, Any], continuation: Mapping[str, Any]) -> None:
        from ..rules.fixed_opponent_land_search import uses_opponent_land_search
        from ..rules.colored_permanent_sacrifice import uses_all_colored_sacrifice
        from ..rules.created_token_fights import uses_created_token_fights
        from ..rules.apnap_library_reveals import uses_library_reveal_round

        if uses_library_reveal_round(effect):
            from .apnap_library_reveals import begin_library_reveal_round

            begin_library_reveal_round(self, effect, continuation)
            return
        from ..rules.repeated_apnap_payment import is_payment_round

        if uses_opponent_land_search(effect):
            from .fixed_opponent_land_search import begin_opponent_land_search

            begin_opponent_land_search(self, effect, continuation)
            return

        if uses_all_colored_sacrifice(effect):
            from .colored_permanent_sacrifice import resolve_all_colored_permanent_sacrifice

            resolve_all_colored_permanent_sacrifice(self, effect=effect, resume=continuation)
            return

        if uses_created_token_fights(effect):
            from .created_token_fights import begin_created_token_fights

            begin_created_token_fights(self, effect, continuation)
            return

        if is_payment_round(effect):
            from .repeated_apnap_payment import begin_payment_round

            begin_payment_round(self, effect, continuation)
            return
        effect = bind_most_lands_sacrifice_players(self, effect, continuation)
        players_spec = effect.get("players", "all")
        greatest_players = greatest_power_sacrifice_players(self, effect, continuation)
        if uses_caster_discard_payment(effect):
            caster = caster_discard_player_for_choice(self, effect, continuation)
            queue = [caster] if caster in self.active_seats else []
        elif greatest_players is not None:
            queue = greatest_players
            for seat in queue:
                self._choice_options(seat, effect)
        elif players_spec == "all":
            queue = self.apnap_order()
        elif players_spec in ("opponents", OTHER_PLAYER_SACRIFICE_SCOPE):
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
        validate_apnap_choice_effect(self, effect, continuation)
        choice_state = {
            "queue": queue,
            "player_order": list(queue),
            "selected": {},
            "effect": dict(effect),
            "resume": dict(continuation),
        }
        cohort = (KEEP_COUNTER_COHORT if KEEP_COUNTER_FIELD in effect else KEEP_THREE_OFFERED_FIELD
                  if KEEP_THREE_SACRIFICE_FIELD in effect else None)
        if cohort:
            choice_state[cohort] = self._retention_candidates(effect, queue)
        seal_fractional_counts(self, choice_state)
        self._issue_next_apnap_choice(choice_state)

    def _retention_candidates(self, effect, player_order):
        return {
            seat: {
                ref: self._resolve_object(seat, ref, zones={"battlefield"}).logical_object_id
                for ref in self._choice_options(seat, effect)
            }
            for seat in player_order
        }

    def _issue_next_apnap_choice(self, state: dict[str, Any]) -> None:
        queue = list(state["queue"])
        if not queue:
            self._apply_apnap_choices(state)
            return
        seat = queue[0]
        effect = state["effect"]
        options = self._choice_options(seat, effect)
        count = apnap_required_count(state, seat, options)
        requires_full_count = effect.get("require_full_count") is True or DECLINED_LIFE_LOSS in effect
        if requires_full_count and count < int(effect.get("count", 1)):
            selected = dict(state["selected"])
            selected[seat] = []
            state["selected"] = selected
            state["queue"] = queue[1:]
            self._issue_next_apnap_choice(state)
            return
        fixed_keep_set = (
            (KEEP_THREE_SACRIFICE_FIELD in effect or KEEP_COUNTER_FIELD in effect) and len(options) == count
        )
        if count == 0 or fixed_keep_set:
            selected = dict(state["selected"])
            selected[seat] = list(options) if fixed_keep_set else []
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
                    **({"minimum": 0} if sacrifice_can_decline(effect) or any_number_sacrifice_count(effect.get("count")) else {}),
                    **({"order": HAND_TOP_FIRST} if is_affected_hand_top_choice(effect) else {}),
                    "prior_public_choices": dict(state["selected"]) if not effect.get("hidden") else {},
                    **({DECLINED_LIFE_LOSS: effect[DECLINED_LIFE_LOSS],
                        "prior_payment_choices": {player: bool(refs) for player, refs in state["selected"].items()}}
                       if DECLINED_LIFE_LOSS in effect else {}),
                    **({'discard_options': alternative} if (alternative:=alternative_discard_options(self,effect,seat,options)) is not None else {}),
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
        from .apnap_library_reveals import OPERATION as library_reveal_operation, complete_library_reveal_choice
        from .repeated_apnap_payment import OPERATION as repeated_payment_operation, complete_payment_round_choice

        if (raw_continuation.get("selection") or {}).get("operation_id") == library_reveal_operation:
            complete_library_reveal_choice(self, decision)
            return
        if (raw_continuation.get("selection") or {}).get("operation_id") == repeated_payment_operation:
            complete_payment_round_choice(self, decision)
            return
        from .fixed_opponent_land_search import OFFER_OPERATION, complete_opponent_land_search_offer

        if (raw_continuation.get("selection") or {}).get("operation_id") == OFFER_OPERATION:
            complete_opponent_land_search_offer(self, decision)
            return
        from .created_token_fights import OPERATION, complete_created_token_fights

        if (raw_continuation.get("selection") or {}).get("operation_id") == OPERATION:
            complete_created_token_fights(self, decision)
            return
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
        hand_top = is_affected_hand_top_choice(effect)
        seal_sacrifice = effect.get("players") == OTHER_PLAYER_SACRIFICE_SCOPE or any(key in effect for key in (
            GREATEST_POWER_SACRIFICE_FIELD, CONTROLLER_SACRIFICE_LIFE_FIELD,
            ENTRY_SACRIFICE_POWER_DRAW_FIELD, ATTACK_SACRIFICE_STAT_FIELD,
            TARGET_SACRIFICE_TOUGHNESS_FIELD, COUNTED_SACRIFICE_TOKEN_FIELD, ANY_NUMBER_SACRIFICE_FIELD,
            SHARED_TYPE_SACRIFICE,
            ORDER_GRAVEYARD_ARRIVALS,
        ))
        if (is_typed_hand_exile(effect) or hand_top or DECLINED_LIFE_LOSS in effect or seal_sacrifice
                or KEEP_THREE_SACRIFICE_FIELD in effect or KEEP_COUNTER_FIELD in effect
                or 'scheduled_permanent_return_offered' in resume):
            validate_apnap_choice_effect(self, effect, resume)
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
        required = apnap_required_count(state, seat, options)
        arbitrary = any_number_sacrifice_count(effect.get("count"))
        if DECLINED_LIFE_LOSS in effect:
            allowed_counts = object_payment_cardinalities(effect["count"])
        else:
            allowed_counts = (0, required) if sacrifice_can_decline(effect) else (required,)

        def count_is_allowed(count):
            return count <= required if arbitrary else count in allowed_counts

        if not alternative_discard_count(effect.get('count')) and not count_is_allowed(len(values)):
            raise GameRuleError(f"{seat} must choose exactly {required} option(s)")
        refs: list[str] = []
        selected_incarnations = {}
        for value in values:
            card = self._resolve_object(
                seat,
                str(value),
                zones={str(effect.get("zone") or "battlefield")},
            )
            if card.ref not in options or card.ref in refs:
                raise GameRuleError("Invalid or duplicate APNAP choice")
            refs.append(card.ref)
            if hand_top or seal_sacrifice:
                selected_incarnations[card.ref] = card.logical_object_id
        if (not validate_alternative_discard_choice(self,effect,seat,options,refs,response.get('discard_mode'))
                and not count_is_allowed(len(refs))):
            raise GameRuleError(f"{seat} must choose exactly {required} option(s)")
        selected = dict(state["selected"])
        selected[seat] = refs
        if hand_top or seal_sacrifice:
            identity_field = "hand_top_incarnations" if hand_top else GREATEST_POWER_SACRIFICE_INCARNATIONS
            state[identity_field] = {**state.get(identity_field, {}), **selected_incarnations}
        queue = list(state["queue"])[1:]
        state["selected"] = selected
        state["queue"] = queue
        self._issue_next_apnap_choice(state)

    def _declined_object_payments(self, state, player_order, actor):
        effect = state['effect']
        if uses_caster_discard_payment(effect):
            caster = caster_discard_player_for_choice(self, effect, state["resume"])
            expected = [caster] if caster in self.active_seats else []
        else:
            expected = [seat for seat in self.apnap_order() if effect['players'] == 'all' or seat != actor]
        if player_order != expected or set(state['selected']) != set(expected):
            raise GameRuleError('APNAP optional payment seats changed')
        declining = []
        for seat in expected:
            refs = state['selected'][seat]
            if not isinstance(refs, (list, tuple)) or len(refs) not in object_payment_cardinalities(effect["count"]):
                raise GameRuleError("APNAP optional payment requires the full payment or a decline")
            if not refs:
                declining.append(seat)
                continue
            eligible = self._choice_options(seat, effect)
            if any(ref not in eligible for ref in refs):
                raise GameRuleError('An APNAP payment object is no longer eligible')
        return declining

    def _sacrifice_discard_followup(self, state, player_order, actor):
        from ..rules.sacrifice_discard_fallback import sacrifice_discard_fallback_effects, uses_sacrifice_discard_fallback

        effect = state["effect"]
        if not uses_sacrifice_discard_fallback(effect):
            return ()
        validate_apnap_choice_effect(self, effect, state["resume"])
        try:
            return sacrifice_discard_fallback_effects(
                actor=actor, player_order=player_order, selected=state["selected"], active_seats=self.active_seats,
            )
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc

    def _apply_apnap_choices(self, state: dict[str, Any]) -> None:
        if "scheduled_draw_result" in state["resume"]:
            from ..drawing.scheduled_draw_coordination import complete_scheduled_apnap_result

            complete_scheduled_apnap_result(self, state)
            return
        effect = state["effect"]
        then = str(effect.get("then") or "sacrifice")
        hand_top = is_affected_hand_top_choice(effect)
        selected_refs: list[str] = []
        player_order = list(
            state.get("player_order") or state["selected"].keys()
        )
        if hand_top:
            revalidate_ordered_hand_choices(self, state, player_order)
        revalidate_sacrifice_incarnations(self, state, player_order)
        for seat in player_order:
            refs = state["selected"].get(seat, ())
            # The zone owner appends arrivals. Reverse each owner's top-first choice.
            for ref in reversed(refs) if hand_top else refs:
                if ref in selected_refs:
                    raise GameRuleError("APNAP choices contain a duplicate object")
                selected_refs.append(ref)
        counter_keep = KEEP_COUNTER_FIELD in effect
        cohort = (KEEP_COUNTER_COHORT if counter_keep else KEEP_THREE_OFFERED_FIELD
                  if KEEP_THREE_SACRIFICE_FIELD in effect else None)
        kept_refs = tuple(selected_refs)
        if cohort:
            validate_apnap_choice_effect(self, effect, state["resume"])
            if player_order != self.apnap_order():
                raise GameRuleError("Retention choices lost their current APNAP player order")
            try:
                selected_refs = list(unkept_permanents_in_apnap_order(
                    player_order, state.get(cohort), state["selected"],
                    self._retention_candidates(effect, player_order), quota=1 if counter_keep else 3,
                ))
            except (TypeError, ValueError, KeyError) as exc:
                raise GameRuleError(str(exc)) from exc
        if counter_keep:
            from ..sacrifice_prohibition_model import sacrifice_is_prohibited

            selected_refs = [ref for ref in selected_refs if not sacrifice_is_prohibited(
                self.state, self._resolve_object(effect["actor"], ref, zones={"battlefield"}),
            )]
        destination = {
            "sacrifice": "graveyard",
            "discard": "graveyard",
            "exile": "exile",
            "return_owner_hand": "hand",
            HAND_TOP_APNAP_RESULT: "library",
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
        declining = []
        if DECLINED_LIFE_LOSS in effect:
            validate_apnap_choice_effect(self, effect, resume)
            declining = self._declined_object_payments(state, player_order, actor)
        order_arrivals = effect.get(ORDER_GRAVEYARD_ARRIVALS) is True or (
            SHARED_TYPE_SACRIFICE in effect and effect["players"] == "opponents"
        )
        semantic_frame = dict(resume.get("semantic_frame") or {})
        instruction_pointer = int(
            semantic_frame.get("instruction_pointer", 0)
        )
        full_count_required = effect.get("require_full_count") is True
        full_count_paid = not full_count_required or len(selected_refs) == int(effect.get("count", 1))
        if uses_caster_discard_payment(effect) and not player_order:
            commit_effects = []
        elif LINKED_FADING_HAND_BINDING in effect:
            validate_apnap_choice_effect(self, effect, resume)
            commit_effects = [{
                "op": LINKED_EXILE_RETURN_OPERATION, "phase": "exile", "cards": selected_refs,
                "binding_id": effect[LINKED_FADING_HAND_BINDING],
                "spec": LinkedExileReturnSpec(
                    timing=LINKED_ABILITY_TIMING,
                    schema_version=LINKED_FADING_EXILE_VERSION,
                    fading_scope=LinkedFadingScope.OPPONENT_HAND,
                ).to_dict(),
            }]
        elif full_count_required and not full_count_paid:
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
                    **({ORDER_GRAVEYARD_ARRIVALS: True} if order_arrivals else {}),
                    **sacrifice_life_commit_fields(effect, state, actor=actor, selected_refs=selected_refs),
                    **ordered_opponent_sacrifice_commit_fields(self, item, effect),
                    **counted_sacrifice_token_commit_fields(
                        effect, state, actor=actor, selected_refs=selected_refs,
                        incarnation_field=GREATEST_POWER_SACRIFICE_INCARNATIONS,
                    ),
                    **any_number_sacrifice_commit_fields(
                        self, item, effect, state, actor=actor, selected_refs=selected_refs,
                        incarnation_field=GREATEST_POWER_SACRIFICE_INCARNATIONS,
                    ),
                    **({ORDER_GRAVEYARD_ARRIVALS: True} if counter_keep else {}),
                    **({SELECTED_DISCARD_DRAW_FIELD: 1} if SELECTED_DISCARD_DRAW_FIELD in effect else {}),
                    **({DECLINED_LIFE_LOSS: effect[DECLINED_LIFE_LOSS], 'declining_players': declining}
                       if declining else {}),
                }
            ]
        placement, restriction = (), ()
        if counter_keep:
            identities = {ref: identity for rows in state[cohort].values() for ref, identity in rows.items()}
            placement, restriction = keep_counter_followups(
                kept_refs, identities, actor=actor, source=self._stack_source_ref(item) or item.ref,
            )
        self._continue_resolution(
            stack_ref=stack_ref,
            effects=[
                *placement,
                *commit_effects,
                *restriction,
                *self._sacrifice_discard_followup(state, player_order, actor),
                *shared_sacrifice_followup(self, effect, actor=actor, selected=selected_refs, declining=declining),
                *selected_discard_draw_followup(self, item, effect, selected_refs),
                *zero_land_sacrifice_search(self, item, effect, selected_refs),
                *(dict(item) for item in resume.get("effects", [])),
            ],
            destination=resume.get("destination"),
            note=str(resume.get("note") or ""),
            instruction_pointer=instruction_pointer,
        )
