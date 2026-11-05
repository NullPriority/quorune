from __future__ import annotations

"""Typed owner for represented public nontarget object and seat choices."""

import copy
from typing import Any, Mapping, Sequence

from ..errors import GameRuleError, StateInvariantError
from ..commander_zones import (
    commit_commander_zone_choice_decline,
    CommanderZoneError,
    CommanderZoneStateChoice,
)
from ..model import CardInstance, StackItem
from ..replacement.immutable import FrozenMap, thaw_value
from ..state_based_legend_model import LEGEND_BATCH_OPERATION_ID, LegendChoicePlan
from .model import (
    SelectionContinuation,
    SelectionContract,
    SelectionModelError,
    decode_selection_continuation,
)


LEGEND_OPERATION_ID = "selection.nontarget.legend.v1"
BATTLE_ENTRY_OPERATION_ID = "selection.nontarget.battle-entry-protector.v1"
BATTLE_REPAIR_OPERATION_ID = "selection.nontarget.battle-protector.v1"
SIEGE_CAST_OPERATION_ID = "selection.nontarget.siege-cast.v1"
COMMANDER_ZONE_OPERATION_ID = "selection.nontarget.commander-zone.v1"


class PublicChoiceOwnerMixin:
    """Issue, validate, and commit represented public nontarget choices."""

    def _decode_public_choice(
        self,
        decision: Any,
        *,
        operation_id: str,
        legacy: SelectionContinuation | None,
    ) -> SelectionContinuation:
        try:
            selection = decode_selection_continuation(
                decision.continuation,
                expected_contract=SelectionContract.NONTARGET_CHOICE,
                expected_operation_id=operation_id,
                legacy=legacy,
            )
        except SelectionModelError as exc:
            raise GameRuleError(str(exc)) from exc
        seat = decision.actors[0]
        if selection.actor != seat:
            raise GameRuleError("Public choice actor changed")
        if selection.state_revision != decision.created_revision:
            raise GameRuleError("Public choice state revision changed")
        if selection.visibility != "public":
            raise GameRuleError("Public choice visibility changed")
        return selection

    def _begin_legend_choice(
        self,
        seat: str,
        name: str,
        object_ids: Sequence[str],
    ) -> None:
        if getattr(self.state, 'legend_rule_choice_version', None) is not None:
            raise GameRuleError('Legacy legend choice cannot begin in current state-based mode')
        candidates = [
            {
                "object_id": object_id,
                "logical_object_id": self.state.cards[
                    object_id
                ].logical_object_id,
                "ref": self.state.cards[object_id].ref,
            }
            for object_id in object_ids
        ]
        continuation = SelectionContinuation(
            contract=SelectionContract.NONTARGET_CHOICE,
            operation_id=LEGEND_OPERATION_ID,
            actor=seat,
            state_revision=self.state.revision,
            visibility="public",
            payload=FrozenMap({"candidates": candidates}),
        )
        self.permissions.issue(
            kind="state.legend",
            role="pilot",
            actors=[seat],
            allowed_actions=["choose"],
            payload_by_actor={
                seat: {
                    "name": name,
                    "keep_one": [row["ref"] for row in candidates],
                }
            },
            continuation={"selection": continuation.to_dict()},
        )

    def _begin_legacy_legend_choice_if_required(self) -> bool:
        if getattr(self.state, 'legend_rule_choice_version', None) is not None:
            return False
        groups = self._legend_groups()
        if not groups:
            return False
        self._begin_legend_choice(*groups[0])
        return True

    def _begin_state_based_legend_choice(self, plan: LegendChoicePlan) -> None:
        from ..state_based_legend_choices import validate_legend_choice_plan

        validate_legend_choice_plan(self, plan)
        actor = plan.actor
        if actor is None:
            raise GameRuleError('Legend keep collection is already complete')
        remaining = [group for index, group in enumerate(plan.groups)
                     if group.controller == actor and index not in plan.selected_groups]
        chosen = [{'player': group.controller, 'name': group.name, 'card': candidate.ref}
                  for object_id in plan.kept for group in plan.groups for candidate in group.candidates
                  if candidate.object_id == object_id]
        label = remaining[0].name if len(remaining) == 1 else 'Choose a permanent to keep for one legend conflict'
        if chosen:
            label += '. Earlier keeps: ' + '; '.join(row['player'] + ' — ' + row['name'] for row in chosen)
        continuation = SelectionContinuation(
            contract=SelectionContract.NONTARGET_CHOICE, operation_id=LEGEND_BATCH_OPERATION_ID,
            actor=actor, state_revision=self.state.revision, visibility='public',
            payload=FrozenMap({'plan': plan.to_dict()}),
        )
        self.permissions.issue(kind='state.legend', role='pilot', actors=[actor], allowed_actions=['choose'],
            payload_by_actor={actor: {'name': label, 'keep_one': [c.ref for c in plan.legal_candidates()],
                                     'earlier_keeps': chosen}},
            continuation={'selection': continuation.to_dict()})

    def _complete_state_based_legend_choice(self, decision: Any) -> None:
        from ..state_based_legend_choices import validate_legend_choice_plan

        selection = self._decode_public_choice(decision, operation_id=LEGEND_BATCH_OPERATION_ID, legacy=None)
        payload = thaw_value(selection.payload)
        if set(payload) != {'plan'}:
            raise GameRuleError('State-based legend continuation fields are malformed')
        plan = LegendChoicePlan.from_dict(payload['plan'])
        validate_legend_choice_plan(self, plan)
        if decision.actors != [plan.actor] or set(decision.responses) != {plan.actor}:
            raise GameRuleError('Legend choice principal changed')
        response = decision.responses[selection.actor]
        if set(response) - {'action', 'card', 'keep', 'label', 'choice_schema'}:
            raise GameRuleError('Legend keep response has unsupported fields')
        if response.get('action') != 'choose':
            raise GameRuleError('Legend keep response has an invalid action')
        if response.get('card') and response.get('keep') and response['card'] != response['keep']:
            raise GameRuleError('Legend keep response is ambiguous')
        updated = plan.choose(selection.actor, response.get('card') or response.get('keep'))
        if not updated.complete:
            self._begin_state_based_legend_choice(updated)
            return
        from ..state_based_legend_choices import state_based_legend_choices
        with state_based_legend_choices(self, updated):
            self._stabilize()

    def _finish_state_based_legend_choices(self, plan: LegendChoicePlan) -> None:
        for object_id in plan.kept:
            group = next(group for group in plan.groups if any(c.object_id == object_id for c in group.candidates))
            kept = next(candidate for candidate in group.candidates if candidate.object_id == object_id)
            moved = [candidate.ref for candidate in group.candidates if candidate.object_id != object_id]
            self._log(group.controller, 'state.legend',
                f'{group.controller} chose {kept.ref} to keep for the legend rule; {len(moved)} other permanent(s) moved.',
                {'kept': kept.ref, 'moved': moved}, importance=2,
                changed_objects=[candidate.object_id for candidate in group.candidates])

    def _discard_state_based_legend_choices(self) -> None:
        self.permissions.invalidate_current()

    def _begin_commander_zone_choice(
        self,
        candidate: CommanderZoneStateChoice,
    ) -> None:
        if not isinstance(candidate, CommanderZoneStateChoice):
            raise GameRuleError(
                "Commander zone choice requires a typed candidate"
            )
        continuation = SelectionContinuation(
            contract=SelectionContract.NONTARGET_CHOICE,
            operation_id=COMMANDER_ZONE_OPERATION_ID,
            actor=candidate.owner,
            state_revision=self.state.revision,
            source_ref=candidate.ref,
            visibility="public",
            payload=FrozenMap(candidate.to_dict()),
        )
        self.permissions.issue(
            kind="state.commander_zone",
            role="pilot",
            actors=[candidate.owner],
            allowed_actions=["choose"],
            payload_by_actor={
                candidate.owner: {
                    "commander": candidate.ref,
                    "origin": candidate.zone,
                    "choices": ["command", "remain"],
                    "legal_actions": [
                        {
                            "id": "command",
                            "action": "choose",
                            "choice": "command",
                            "choice_schema": {
                                "choice": "command",
                                "required": True,
                            },
                        },
                        {
                            "id": "remain",
                            "action": "choose",
                            "choice": "remain",
                            "choice_schema": {
                                "choice": "remain",
                                "required": True,
                            },
                        },
                    ],
                }
            },
            continuation={"selection": continuation.to_dict()},
        )

    def _complete_commander_zone_choice(self, decision: Any) -> None:
        seat = decision.actors[0]
        selection = self._decode_public_choice(
            decision,
            operation_id=COMMANDER_ZONE_OPERATION_ID,
            legacy=None,
        )
        try:
            candidate = CommanderZoneStateChoice.from_dict(
                thaw_value(selection.payload)
            )
        except CommanderZoneError as exc:
            raise GameRuleError(str(exc)) from exc
        card = self.state.cards.get(candidate.object_id)
        if (
            card is None
            or not card.is_commander
            or card.owner != seat
            or card.ref != candidate.ref
            or card.logical_object_id != candidate.logical_object_id
            or card.zone != candidate.zone
            or card.commander_designation_id != candidate.designation_id
        ):
            raise GameRuleError(
                "Commander zone choice no longer matches that incarnation"
            )
        choice = str(
            decision.responses[seat].get("choice")
            or decision.responses[seat].get("option")
            or ""
        )
        if choice not in {"command", "remain"}:
            raise GameRuleError(
                "Choose whether the commander moves to the command zone"
            )
        if choice == "command":
            self._move_cards_simultaneously(
                ((card.object_id, "command"),),
                reason="commander state-based action",
                log=False,
            )
        else:
            try:
                commit_commander_zone_choice_decline(card, candidate)
            except CommanderZoneError as exc:
                raise GameRuleError(str(exc)) from exc
        self._log(
            seat,
            "state.commander_zone",
            (
                f"{seat} moved {card.ref} to the command zone."
                if choice == "command"
                else f"{seat} left {card.ref} in {card.zone}."
            ),
            {
                "commander": card.ref,
                "origin": candidate.zone,
                "choice": choice,
                "destination": card.zone,
                "rule": "903.9a",
            },
            importance=2,
            changed_objects=[card.object_id],
            changed_players=[seat],
        )
        self._stabilize()

    def _complete_legend_choice(self, decision: Any) -> None:
        selection = decision.continuation.get('selection', {})
        if selection.get('operation_id') == LEGEND_BATCH_OPERATION_ID:
            self._complete_state_based_legend_choice(decision)
            return
        if getattr(self.state, 'legend_rule_choice_version', None) is not None:
            raise GameRuleError('Legacy legend continuation is unavailable in current state-based mode')
        seat = decision.actors[0]
        raw = decision.continuation
        legacy_ids = list(raw.get("object_ids") or [])
        legacy_candidates = [
            {
                "object_id": object_id,
                "logical_object_id": self.state.cards[
                    object_id
                ].logical_object_id,
                "ref": self.state.cards[object_id].ref,
            }
            for object_id in legacy_ids
            if object_id in self.state.cards
        ]
        legacy = SelectionContinuation(
            contract=SelectionContract.NONTARGET_CHOICE,
            operation_id=LEGEND_OPERATION_ID,
            actor=seat,
            state_revision=decision.created_revision,
            visibility="public",
            payload=FrozenMap({"candidates": legacy_candidates}),
        )
        selection = self._decode_public_choice(
            decision,
            operation_id=LEGEND_OPERATION_ID,
            legacy=legacy,
        )
        payload = thaw_value(selection.payload)
        candidates = list(payload.get("candidates") or [])
        if not candidates or any(
            not isinstance(row, Mapping) for row in candidates
        ):
            raise GameRuleError("Legend choice candidates are malformed")
        object_ids: list[str] = []
        legal_refs: set[str] = set()
        for row in candidates:
            object_id = str(row.get("object_id") or "")
            card = self.state.cards.get(object_id)
            if (
                card is None
                or card.zone != "battlefield"
                or card.controller != seat
                or card.logical_object_id
                != str(row.get("logical_object_id") or "")
                or card.ref != str(row.get("ref") or "")
            ):
                raise GameRuleError("Legend choice candidate identity changed")
            object_ids.append(object_id)
            legal_refs.add(card.ref)
        value = (
            decision.responses[seat].get("card")
            or decision.responses[seat].get("keep")
        )
        if str(value) not in legal_refs:
            raise GameRuleError(
                "Legend choice must keep one of the listed permanents"
            )
        card = self._resolve_object(
            seat,
            str(value),
            zones={"battlefield"},
            controlled_only=True,
        )
        moved = [
            object_id
            for object_id in object_ids
            if object_id != card.object_id
        ]
        self._move_cards_simultaneously(
            [(object_id, "graveyard") for object_id in moved],
            reason="legend rule",
            log=False,
        )
        self._log(
            seat,
            "state.legend",
            (
                f"{seat} kept {card.ref}; {len(moved)} legendary "
                "permanent(s) went to graveyards."
            ),
            {
                "kept": card.ref,
                "moved": [self.state.cards[oid].ref for oid in moved],
            },
            importance=2,
            changed_objects=[card.object_id, *moved],
        )
        self._stabilize()

    def _battle_choice_continuation(
        self,
        *,
        operation_id: str,
        battle: CardInstance,
        candidates: Sequence[str],
        stack_ref: str | None = None,
    ) -> SelectionContinuation:
        return SelectionContinuation(
            contract=SelectionContract.NONTARGET_CHOICE,
            operation_id=operation_id,
            actor=battle.controller,
            state_revision=self.state.revision,
            stack_ref=stack_ref,
            source_ref=battle.ref,
            visibility="public",
            payload=FrozenMap(
                {
                    "object_id": battle.object_id,
                    "logical_object_id": battle.logical_object_id,
                    "candidates": list(candidates),
                }
            ),
        )

    def _issue_battle_protector_choice(
        self,
        *,
        kind: str,
        operation_id: str,
        battle: CardInstance,
        candidates: Sequence[str],
        stack_ref: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "battle": battle.ref,
            "name": self.display_name(battle.object_id),
            "protectors": list(candidates),
            "legal_actions": [
                {
                    "id": "choose",
                    "action": "choose",
                    "choice_schema": {
                        "protector": {
                            "type": "seat",
                            "legal_seats": list(candidates),
                            "required": True,
                        }
                    },
                }
            ],
        }
        if stack_ref is not None:
            payload["stack"] = stack_ref
            payload["instruction"] = (
                "Choose an opponent to protect this Siege as it enters."
            )
        self.permissions.issue(
            kind=kind,
            role="pilot",
            actors=[battle.controller],
            allowed_actions=["choose"],
            payload_by_actor={battle.controller: payload},
            continuation={
                "selection": self._battle_choice_continuation(
                    operation_id=operation_id,
                    battle=battle,
                    candidates=candidates,
                    stack_ref=stack_ref,
                ).to_dict()
            },
        )

    def _begin_battle_entry_protector_choice(
        self,
        item: StackItem,
    ) -> bool:
        """Request the represented CR 310.9a protector choice."""

        if (
            item.default_destination != "battlefield"
            or item.card_object_id not in self.state.cards
        ):
            return False
        battle = self.state.cards[item.card_object_id]
        if battle.zone != "stack":
            return False
        card_types, subtypes, _ = self._type_parts(
            str(self._effective_card_data(battle).get("type_line") or "")
        )
        if "battle" not in card_types:
            return False
        if not subtypes:
            battle.battle_protector = battle.controller
            return False
        if "siege" not in subtypes:
            raise GameRuleError(
                "The protector predicate for Battle type(s) "
                f"{sorted(subtypes)} is not compiled"
            )
        if (
            battle.battle_protector in self.active_seats
            and battle.battle_protector != battle.controller
        ):
            return False
        candidates = [
            seat for seat in self.active_seats if seat != battle.controller
        ]
        if not candidates:
            raise GameRuleError("No opponent is available to protect this Siege")
        self._issue_battle_protector_choice(
            kind="battle.enter_protector",
            operation_id=BATTLE_ENTRY_OPERATION_ID,
            battle=battle,
            candidates=candidates,
            stack_ref=item.ref,
        )
        return True

    def _begin_battle_protector_repair_choice(
        self,
        battle: CardInstance,
        candidates: Sequence[str],
    ) -> None:
        self._issue_battle_protector_choice(
            kind="state.battle_protector",
            operation_id=BATTLE_REPAIR_OPERATION_ID,
            battle=battle,
            candidates=candidates,
        )

    def _legacy_battle_choice(
        self,
        decision: Any,
        *,
        operation_id: str,
    ) -> SelectionContinuation:
        raw = decision.continuation
        object_id = str(raw.get("object_id") or "")
        battle = self.state.cards.get(object_id)
        return SelectionContinuation(
            contract=SelectionContract.NONTARGET_CHOICE,
            operation_id=operation_id,
            actor=decision.actors[0],
            state_revision=decision.created_revision,
            stack_ref=str(raw.get("stack_ref") or "") or None,
            source_ref=battle.ref if battle is not None else None,
            visibility="public",
            payload=FrozenMap(
                {
                    "object_id": object_id,
                    "logical_object_id": str(
                        raw.get("source_logical_object_id") or ""
                    ),
                    "candidates": list(raw.get("candidates") or []),
                }
            ),
        )

    def _complete_battle_protector_selection(
        self,
        decision: Any,
        *,
        operation_id: str,
        entry: bool,
    ) -> None:
        seat = decision.actors[0]
        selection = self._decode_public_choice(
            decision,
            operation_id=operation_id,
            legacy=self._legacy_battle_choice(
                decision,
                operation_id=operation_id,
            ),
        )
        payload = thaw_value(selection.payload)
        battle = self.state.cards.get(str(payload.get("object_id") or ""))
        expected_zone = "stack" if entry else "battlefield"
        if (
            battle is None
            or battle.zone != expected_zone
            or battle.controller != seat
            or battle.logical_object_id
            != str(payload.get("logical_object_id") or "")
            or battle.ref != selection.source_ref
        ):
            raise GameRuleError(
                "The Battle protector choice no longer matches that object"
            )
        if entry:
            item = next(
                (
                    candidate
                    for candidate in self.state.stack
                    if candidate.ref == selection.stack_ref
                ),
                None,
            )
            if item is None or item.card_object_id != battle.object_id:
                raise GameRuleError(
                    "The Battle entry choice no longer matches that spell"
                )
        candidates = {
            str(value) for value in payload.get("candidates") or []
        }
        current_candidates = {
            value for value in self.active_seats if value != battle.controller
        }
        if candidates != current_candidates:
            raise GameRuleError("Battle protector candidates changed")
        protector = str(
            decision.responses[seat].get("protector")
            or decision.responses[seat].get("player")
            or ""
        )
        if protector not in candidates:
            raise GameRuleError("Choose one of the legal Battle protectors")
        battle.battle_protector = protector
        code = (
            "battle.protector.chosen"
            if entry
            else "state.battle_protector"
        )
        self._log(
            seat,
            code,
            (
                f"{seat} chose {protector} to protect {battle.ref}."
                if entry
                else f"{protector} became protector of {battle.ref}."
            ),
            {
                "stack": selection.stack_ref,
                "battle": battle.ref,
                "protector": protector,
                "reason": None if entry else "state-based protector repair",
            },
            importance=2,
            changed_objects=[battle.object_id],
            changed_players=[seat, protector],
        )
        if entry:
            self._prepare_stack_resolution()
        else:
            self._stabilize()

    def _complete_battle_entry_protector_choice(
        self,
        decision: Any,
    ) -> None:
        self._complete_battle_protector_selection(
            decision,
            operation_id=BATTLE_ENTRY_OPERATION_ID,
            entry=True,
        )

    def _complete_battle_protector_choice(self, decision: Any) -> None:
        self._complete_battle_protector_selection(
            decision,
            operation_id=BATTLE_REPAIR_OPERATION_ID,
            entry=False,
        )

    def _begin_siege_defeated_choice(
        self,
        *,
        item: StackItem,
        card: CardInstance,
        name: str,
        transformed_face: str,
        public_options: Sequence[Mapping[str, Any]],
    ) -> None:
        continuation = SelectionContinuation(
            contract=SelectionContract.NONTARGET_CHOICE,
            operation_id=SIEGE_CAST_OPERATION_ID,
            actor=item.controller,
            state_revision=self.state.revision,
            stack_ref=item.ref,
            source_ref=card.ref,
            visibility="public",
            payload=FrozenMap(
                {
                    "object_id": card.object_id,
                    "logical_object_id": card.logical_object_id,
                    "transformed_face": transformed_face,
                }
            ),
        )
        self.permissions.issue(
            kind="battle.siege_defeated",
            role="pilot",
            actors=[item.controller],
            allowed_actions=["choose"],
            payload_by_actor={
                item.controller: {
                    "stack": item.ref,
                    "battle": card.ref,
                    "name": name,
                    "transformed_face": transformed_face,
                    "cast_options": [
                        copy.deepcopy(dict(value))
                        for value in public_options
                    ],
                    "prompt": (
                        "Cast this card transformed without paying its "
                        "mana cost?"
                    ),
                    "legal_actions": [
                        {
                            "id": "cast",
                            "action": "choose",
                            "choice": "cast",
                            "choice_schema": {
                                "choice": "cast",
                                "cast_options": [
                                    copy.deepcopy(dict(value))
                                    for value in public_options
                                ],
                            },
                        },
                        {
                            "id": "decline",
                            "action": "choose",
                            "choice": "decline",
                            "choice_schema": {"choice": "decline"},
                        },
                    ],
                }
            },
            continuation={"selection": continuation.to_dict()},
        )

    def _complete_siege_defeated_choice(self, decision: Any) -> None:
        seat = decision.actors[0]
        raw = decision.continuation
        raw_object_id = str(raw.get("object_id") or "")
        legacy_card = self.state.cards.get(raw_object_id)
        legacy = SelectionContinuation(
            contract=SelectionContract.NONTARGET_CHOICE,
            operation_id=SIEGE_CAST_OPERATION_ID,
            actor=seat,
            state_revision=decision.created_revision,
            stack_ref=str(raw.get("stack_ref") or "") or None,
            source_ref=legacy_card.ref if legacy_card is not None else None,
            visibility="public",
            payload=FrozenMap(
                {
                    "object_id": raw_object_id,
                    "logical_object_id": str(
                        raw.get("exile_logical_object_id") or ""
                    ),
                    "transformed_face": str(
                        raw.get("transformed_face") or ""
                    ),
                }
            ),
        )
        selection = self._decode_public_choice(
            decision,
            operation_id=SIEGE_CAST_OPERATION_ID,
            legacy=legacy,
        )
        payload = thaw_value(selection.payload)
        item = next(
            (
                candidate
                for candidate in self.state.stack
                if candidate.ref == selection.stack_ref
                and candidate.semantic_key == "builtin:siege-defeated"
            ),
            None,
        )
        card = self.state.cards.get(str(payload.get("object_id") or ""))
        if item is None:
            raise GameRuleError(
                "The Siege defeated trigger is no longer on the stack"
            )
        if (
            card is None
            or card.zone != "exile"
            or card.logical_object_id
            != str(payload.get("logical_object_id") or "")
            or card.ref != selection.source_ref
        ):
            raise GameRuleError(
                "The exiled Siege is no longer the object offered for "
                "the transformed cast"
            )
        choice = str(
            decision.responses[seat].get("choice")
            or decision.responses[seat].get("option")
            or ""
        )
        if choice not in {"cast", "decline"}:
            raise GameRuleError(
                "Choose whether to cast the defeated Siege transformed"
            )
        if choice == "decline":
            self._finish_siege_defeated_resolution(
                item,
                outcome="declined",
                card=card,
            )
            return
        transformed_face = str(payload.get("transformed_face") or "")
        before_stack_refs = {candidate.ref for candidate in self.state.stack}
        cast_response = dict(decision.responses[seat].get("cast") or {})
        cast_response.update(
            {
                key: copy.deepcopy(value)
                for key, value in decision.responses[seat].items()
                if key not in {"action", "cast", "choice", "option"}
            }
        )
        cast_response.update(
            {
                "card": card.ref,
                "from": "exile",
                "face": transformed_face,
                "auto_pay": True,
            }
        )
        self._cast(
            seat,
            cast_response,
            authorized_from_zone="exile",
            required_face=transformed_face,
            force_without_mana_cost=True,
            ignore_priority=True,
            ignore_timing=True,
            during_resolution=True,
        )
        cast_item = next(
            (
                candidate
                for candidate in reversed(self.state.stack)
                if candidate.ref not in before_stack_refs
                and candidate.kind == "spell"
                and candidate.card_object_id == card.object_id
            ),
            None,
        )
        if cast_item is None:
            raise StateInvariantError(
                "The transformed Siege cast did not create a spell"
            )
        self._finish_siege_defeated_resolution(
            item,
            outcome="cast_transformed",
            card=card,
            cast_stack_ref=cast_item.ref,
        )


__all__ = ["PublicChoiceOwnerMixin"]
