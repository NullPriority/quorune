from __future__ import annotations

"""Typed public choice owner for one-shot casts from exile during resolution."""

import copy
import hashlib
import re
from typing import Any, Mapping, Sequence

from ..cast_timing import type_line_has_card_type
from ..cast_lifecycles import (
    FixedCastLifecycleError,
    FixedCastLifecycleKind,
    FixedCastLifecycleSpec,
)
from ..compiled_madness import compiled_fixed_madness_spec
from ..errors import GameRuleError, StateInvariantError
from ..model import CardInstance, StackItem
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.casting.model import CastProposalError
from ..rules.casting.proposal import aura_spell_target_schema
from ..semantic_runtime.intents import MadnessChoiceIntent
from ..stack_resolution import complete_stack_resolution
from ..util import stable_json
from ..zone_transitions import ZoneTransitionOwner
from .model import (
    SelectionContinuation,
    SelectionContract,
    SelectionModelError,
    decode_selection_continuation,
)


EXILE_CAST_OPERATION_ID = "selection.nontarget.exile-cast.v1"
EXILE_CAST_PRODUCER_CASCADE = "cascade"
_MANA_SYMBOL_RE = re.compile(r"\{([^{}]+)\}")
_PUBLIC_COST_OPTION_FIELDS = frozenset(
    {"id", "kind", "requirements", "choice_schema", "label"}
)


def mana_value_of_cost(cost: str, *, x_value: int = 0) -> float:
    """Return the stack mana value of one selected face's mana cost."""

    if type(cost) is not str or type(x_value) is not int or x_value < 0:
        raise GameRuleError("Mana-value inputs are malformed")
    symbols = _MANA_SYMBOL_RE.findall(cost.upper())
    if _MANA_SYMBOL_RE.sub("", cost).strip():
        raise GameRuleError("Mana value requires a canonical mana cost")
    result = 0.0
    for symbol in symbols:
        if symbol.isdigit():
            result += int(symbol)
        elif symbol == "X":
            result += x_value
        elif symbol in {"W", "U", "B", "R", "G", "C", "S"}:
            result += 1
        elif symbol.startswith("H") and symbol[1:] in {"W", "U", "B", "R", "G"}:
            result += 0.5
        elif "/" in symbol:
            parts = symbol.split("/")
            if not all(
                part in {"W", "U", "B", "R", "G", "C", "P", "2"}
                for part in parts
            ):
                raise GameRuleError("Unsupported hybrid mana-value symbol")
            result += 2 if "2" in parts else 1
        else:
            raise GameRuleError("Unsupported mana-value symbol")
    return result


def _identity(card: CardInstance) -> dict[str, Any]:
    return {
        "object_id": card.object_id,
        "logical_object_id": card.logical_object_id,
        "ref": card.ref,
        "owner": card.owner,
    }


def _options_fingerprint(options: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(stable_json(list(options)).encode("utf-8")).hexdigest()


class OneShotExileCastChoiceOwnerMixin:
    """Issue and commit a source-pinned cast-or-decline exile permission."""

    def _remove_resolving_choice_item(
        self,
        item: StackItem,
        *,
        required: bool = True,
    ) -> None:
        if item in self.state.stack:
            complete_stack_resolution(
                self,
                item=item,
                destination=None,
                prepared_replacement=None,
            )
            return
        if required:
            raise StateInvariantError("Choice resolution left the stack early")

    def _finish_siege_defeated_resolution(
        self,
        item: StackItem,
        *,
        outcome: str,
        card: CardInstance | None = None,
        cast_stack_ref: str | None = None,
    ) -> None:
        self._remove_resolving_choice_item(item, required=False)
        self._log(
            item.controller,
            "battle.siege_defeated.resolve",
            f"Resolved {item.ref}: {item.label} ({outcome}).",
            {
                "stack": item.ref,
                "battle": card.ref if card is not None else None,
                "outcome": outcome,
                "cast_stack": cast_stack_ref,
            },
            importance=2,
            changed_objects=[card.object_id] if card is not None else [],
            changed_players=[item.controller],
        )
        if self._stabilize():
            return
        self._grant_priority(self.state.active_player)

    def _begin_siege_defeated_resolution(self, item: StackItem) -> None:
        """Resolve the intrinsic CR 310.12b Siege ability natively."""

        card = self.state.cards.get(item.source_object_id or "")
        expected_logical_object_id = str(
            item.context.get("source_logical_object_id") or ""
        )
        if (
            card is None
            or card.zone != "battlefield"
            or card.logical_object_id != expected_logical_object_id
        ):
            self._finish_siege_defeated_resolution(
                item,
                outcome="source_unavailable",
                card=card,
            )
            return
        self.move_card(
            card.object_id,
            "exile",
            reason="Siege defeated trigger",
            semantic_events=True,
        )
        if card.zone != "exile":
            self._finish_siege_defeated_resolution(
                item,
                outcome="exile_failed",
                card=card,
            )
            return
        record = self.card_record(card)
        can_cast_transformed = bool(
            card.is_card_object
            and record is not None
            and record.layout == "transform"
            and len(record.faces) >= 2
            and str(record.faces[1].get("name") or "")
        )
        if not can_cast_transformed:
            self._finish_siege_defeated_resolution(
                item,
                outcome="exiled_not_castable_transformed",
                card=card,
            )
            return
        transformed_face_data = dict(record.faces[1])
        transformed_face = str(transformed_face_data["name"])
        semantic_key = f"{record.oracle_id}:spell:{transformed_face}"
        program = self.semantics.get(semantic_key)
        transformed_types, _, _ = self._type_parts(
            str(transformed_face_data.get("type_line") or "")
        )
        if transformed_types.intersection({"instant", "sorcery"}) and (
            program is None
            or (
                program.target_schema is None
                and not self.semantic_program_is_current_trusted(program)
            )
        ):
            self.permissions.issue(
                kind="arbiter.resolve",
                role="arbiter",
                actors=["arbiter"],
                allowed_actions=[
                    "resolve",
                    "register_and_resolve",
                    "counter_as_rule",
                    "fizzle",
                ],
                payload_by_actor={
                    "arbiter": {
                        "stack": item.ref,
                        "label": item.label,
                        "controller": item.controller,
                        "semantic_key": item.semantic_key,
                        "default_destination": None,
                        "reason": (
                            "transformed Siege spell lacks trusted typed "
                            "cast semantics"
                        ),
                        "battle": card.ref,
                        "transformed_face": transformed_face,
                    }
                },
            )
            return
        options = self._cast_cost_options(
            item.controller,
            card,
            program,
            hint=True,
            force_without_mana_cost=True,
        )
        public_options: list[dict[str, Any]] = []
        for option in options:
            target_specification = (
                dict(option["target_schema"])
                if isinstance(option.get("target_schema"), Mapping)
                else program.target_schema if program is not None else None
            )
            public_target_schema = None
            if target_specification is not None:
                public_target_schema = self._public_target_schema(
                    item.controller,
                    target_specification,
                    source_ref=card.ref,
                )
                if public_target_schema is None:
                    continue
            public_option = {
                key: copy.deepcopy(value)
                for key, value in option.items()
                if key in _PUBLIC_COST_OPTION_FIELDS
            }
            if public_target_schema is not None:
                public_option["target_schema"] = public_target_schema
            public_options.append(public_option)
        if not public_options:
            self._finish_siege_defeated_resolution(
                item,
                outcome="exiled_cast_unavailable",
                card=card,
            )
            return
        self._begin_siege_defeated_choice(
            item=item,
            card=card,
            name=record.name,
            transformed_face=transformed_face,
            public_options=public_options,
        )

    def _begin_intrinsic_exile_cast_resolution(self, item: StackItem) -> bool:
        if item.semantic_key == "builtin:siege-defeated":
            self._begin_siege_defeated_resolution(item)
            return True
        from ..cascade import CASCADE_SEMANTIC_KEY, begin_cascade_resolution

        if item.semantic_key == CASCADE_SEMANTIC_KEY:
            begin_cascade_resolution(self, item)
            return True
        return False

    @staticmethod
    def _cast_faces(
        record: Any,
        *,
        required_face: str | None,
    ) -> tuple[Mapping[str, Any] | None, ...]:
        if not record.faces:
            return (None,) if required_face is None else ()
        if required_face is not None:
            return tuple(
                face
                for face in record.faces
                if str(face.get("name") or "").casefold()
                == required_face.casefold()
            )
        if record.layout in {"adventure", "modal_dfc", "split"}:
            return tuple(record.faces)
        return (record.faces[0],)

    def _one_shot_exile_cast_options(
        self,
        *,
        actor: str,
        card: CardInstance,
        maximum_mana_value: float,
        required_face: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        record = self.card_record(card)
        if record is None or card.zone != "exile":
            return ()
        result: list[dict[str, Any]] = []
        for face in self._cast_faces(record, required_face=required_face):
            face_name = str(face.get("name") or "") if face else None
            type_line = (
                str(face.get("type_line") or "") if face else record.type_line
            )
            if type_line_has_card_type(type_line, "land"):
                continue
            mana_cost = (
                str(face.get("mana_cost") or "") if face else record.mana_cost
            )
            try:
                resulting_mana_value = mana_value_of_cost(mana_cost)
            except GameRuleError:
                continue
            if resulting_mana_value >= maximum_mana_value:
                continue
            semantic_key = (
                f"{record.oracle_id}:spell:{face_name or 'front'}"
            )
            program = self.semantics.get(semantic_key)
            is_instant_or_sorcery = any(
                type_line_has_card_type(type_line, card_type)
                for card_type in ("instant", "sorcery")
            )
            if program is not None and not self.semantic_program_is_current_trusted(
                program
            ):
                continue
            if program is None and (
                is_instant_or_sorcery or not self._trusted_generic_spell(record)
            ):
                continue
            options = self._cast_cost_options(
                actor,
                card,
                program,
                hint=True,
                force_without_mana_cost=True,
                cast_type_line=type_line,
            )
            for option in options:
                target_specification = (
                    dict(option["target_schema"])
                    if isinstance(option.get("target_schema"), Mapping)
                    else program.target_schema if program is not None else None
                )
                public_target_schema = None
                if target_specification is not None:
                    public_target_schema = self._public_target_schema(
                        actor,
                        target_specification,
                        source_ref=card.ref,
                    )
                    if public_target_schema is None:
                        continue
                public_option = {
                    key: copy.deepcopy(value)
                    for key, value in option.items()
                    if key in _PUBLIC_COST_OPTION_FIELDS
                }
                public_option["mana_value"] = resulting_mana_value
                if face_name is not None:
                    public_option["face"] = face_name
                if public_target_schema is not None:
                    public_option["target_schema"] = public_target_schema
                result.append(public_option)
        return tuple(result)

    def _madness_exile_cast_options(
        self,
        *,
        actor: str,
        card: CardInstance,
        raw_spec: Any,
    ) -> tuple[dict[str, Any], ...]:
        try:
            spec = FixedCastLifecycleSpec.from_dict(raw_spec)
        except (FixedCastLifecycleError, TypeError):
            return ()
        if (
            spec.kind is not FixedCastLifecycleKind.MADNESS
            or card.zone != "exile"
            or card.owner != actor
            or compiled_fixed_madness_spec(self, card) != spec
        ):
            return ()
        record = self.card_record(card)
        if record is None:
            return ()
        result: list[dict[str, Any]] = []
        for face in self._cast_faces(record, required_face=None):
            face_name = str(face.get("name") or "") if face else None
            type_line = (
                str(face.get("type_line") or "") if face else record.type_line
            )
            if type_line_has_card_type(type_line, "land"):
                continue
            semantic_key = f"{record.oracle_id}:spell:{face_name or 'front'}"
            program = self.semantics.get(semantic_key)
            is_instant_or_sorcery = any(
                type_line_has_card_type(type_line, card_type)
                for card_type in ("instant", "sorcery")
            )
            if program is not None and not self.semantic_program_is_current_trusted(
                program
            ):
                continue
            if program is None and (
                is_instant_or_sorcery or not self._trusted_generic_spell(record)
            ):
                continue
            try:
                aura_target_schema = aura_spell_target_schema(
                    type_line=type_line,
                    enchant_spec=self._compiled_enchant_spec(
                        card,
                        face_name=face_name,
                    ),
                    reviewed_target_schema=(
                        getattr(program, "target_schema", None)
                    ),
                )
            except CastProposalError:
                continue
            options = self._cast_cost_options(
                actor,
                card,
                program,
                hint=True,
                alternative_base=spec.fixed_cost_option(),
                cast_type_line=type_line,
                suppress_source_costs=True,
            )
            for option in options:
                if option.get("id") != FixedCastLifecycleKind.MADNESS.value:
                    continue
                target_specification = (
                    dict(option["target_schema"])
                    if isinstance(option.get("target_schema"), Mapping)
                    else dict(aura_target_schema)
                    if aura_target_schema is not None
                    else program.target_schema if program is not None else None
                )
                public_target_schema = None
                if target_specification is not None:
                    public_target_schema = self._public_target_schema(
                        actor,
                        target_specification,
                        source_ref=card.ref,
                    )
                    if public_target_schema is None:
                        continue
                public_option = {
                    key: copy.deepcopy(value)
                    for key, value in option.items()
                    if key in _PUBLIC_COST_OPTION_FIELDS
                }
                if face_name is not None:
                    public_option["face"] = face_name
                if public_target_schema is not None:
                    public_option["target_schema"] = public_target_schema
                result.append(public_option)
        return tuple(result)

    def madness_choice_intent(self, intent: MadnessChoiceIntent) -> None:
        if not isinstance(intent, MadnessChoiceIntent):
            raise GameRuleError("Madness choice intent is malformed")
        item = next(
            (
                value
                for value in self.state.stack
                if value.ref == intent.source.stack_ref
                and value.controller == intent.actor
            ),
            None,
        )
        card = self.state.cards.get(intent.source.object_id or "")
        current = bool(
            item is not None
            and card is not None
            and card.zone == "exile"
            and card.ref == intent.source.card_ref
            and card.logical_object_id == intent.source.logical_object_id
            and card.owner == intent.actor
        )
        if not current:
            if intent.choice == "cast":
                raise GameRuleError("The Madness card incarnation changed")
            return
        assert item is not None and card is not None
        options = self._madness_exile_cast_options(
            actor=intent.actor,
            card=card,
            raw_spec=intent.lifecycle.to_dict(),
        )
        if _options_fingerprint(options) != intent.options_fingerprint:
            raise GameRuleError("Madness cast options changed")
        if intent.choice == "decline":
            self.move_card(
                card.object_id,
                "graveyard",
                reason="Madness cast declined",
                semantic_events=True,
            )
            return
        if not options:
            raise GameRuleError("The Madness cast is not currently payable")
        response = dict(thaw_value(intent.response))
        requested_face = str(response.get("face") or "") or None
        matches = [
            option
            for option in options
            if requested_face is None or option.get("face") == requested_face
        ]
        if len(matches) != 1:
            raise GameRuleError("Select one current Madness cast option")
        before_stack_refs = {value.ref for value in self.state.stack}
        response.update(
            {
                "card": card.ref,
                "from": "exile",
                "cost_option": FixedCastLifecycleKind.MADNESS.value,
                "auto_pay": True,
            }
        )
        if matches[0].get("face") is not None:
            response["face"] = matches[0]["face"]
        self._cast(
            intent.actor,
            response,
            authorized_from_zone="exile",
            authorized_cost_option=intent.lifecycle.fixed_cost_option(),
            ignore_priority=True,
            ignore_timing=True,
            during_resolution=True,
        )
        cast_item = next(
            (
                value
                for value in reversed(self.state.stack)
                if value.ref not in before_stack_refs
                and value.kind == "spell"
                and value.card_object_id == card.object_id
            ),
            None,
        )
        if cast_item is None:
            raise StateInvariantError("The Madness choice created no spell")

    def _begin_one_shot_exile_cast_choice(
        self,
        *,
        item: StackItem,
        card: CardInstance,
        cleanup_cards: Sequence[CardInstance],
        maximum_mana_value: float,
        producer: str,
    ) -> None:
        if producer != EXILE_CAST_PRODUCER_CASCADE:
            raise StateInvariantError("Unsupported one-shot exile-cast producer")
        options = self._one_shot_exile_cast_options(
            actor=item.controller,
            card=card,
            maximum_mana_value=maximum_mana_value,
        )
        if not options:
            raise StateInvariantError(
                "One-shot exile-cast choice requires a current legal cast option"
            )
        cleanup = [_identity(candidate) for candidate in cleanup_cards]
        continuation = SelectionContinuation(
            contract=SelectionContract.NONTARGET_CHOICE,
            operation_id=EXILE_CAST_OPERATION_ID,
            actor=item.controller,
            state_revision=self.state.revision,
            stack_ref=item.ref,
            source_ref=card.ref,
            visibility="public",
            payload=FrozenMap(
                {
                    "schema_version": 1,
                    "producer": producer,
                    "resolution_stack_id": item.stack_id,
                    "candidate": _identity(card),
                    "cleanup": cleanup,
                    "maximum_mana_value": maximum_mana_value,
                    "options_fingerprint": _options_fingerprint(options),
                }
            ),
        )
        self.permissions.issue(
            kind="selection.exile_cast",
            role="pilot",
            actors=[item.controller],
            allowed_actions=["choose"],
            payload_by_actor={
                item.controller: {
                    "stack": item.ref,
                    "card": card.ref,
                    "name": self.display_name(card.object_id),
                    "cast_options": [copy.deepcopy(value) for value in options],
                    "prompt": "Cast this exiled card without paying its mana cost?",
                    "legal_actions": [
                        {
                            "id": "cast",
                            "action": "choose",
                            "choice": "cast",
                            "choice_schema": {
                                "choice": "cast",
                                "cast_options": [
                                    copy.deepcopy(value) for value in options
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

    def _decode_one_shot_exile_cast_choice(
        self,
        decision: Any,
    ) -> tuple[SelectionContinuation, dict[str, Any]]:
        try:
            selection = decode_selection_continuation(
                decision.continuation,
                expected_contract=SelectionContract.NONTARGET_CHOICE,
                expected_operation_id=EXILE_CAST_OPERATION_ID,
            )
        except SelectionModelError as exc:
            raise GameRuleError(str(exc)) from exc
        actor = decision.actors[0]
        if selection.actor != actor:
            raise GameRuleError("Exile-cast choice actor changed")
        if selection.state_revision != decision.created_revision:
            raise GameRuleError("Exile-cast choice state revision changed")
        if selection.visibility != "public":
            raise GameRuleError("Exile-cast choice visibility changed")
        payload = thaw_value(selection.payload)
        expected = {
            "schema_version",
            "producer",
            "resolution_stack_id",
            "candidate",
            "cleanup",
            "maximum_mana_value",
            "options_fingerprint",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise GameRuleError("Exile-cast continuation is malformed")
        if payload["schema_version"] != 1:
            raise GameRuleError("Unsupported exile-cast continuation version")
        if payload["producer"] != EXILE_CAST_PRODUCER_CASCADE:
            raise GameRuleError("Exile-cast producer changed")
        return selection, payload

    def _current_exiled_identity(
        self,
        raw: Mapping[str, Any],
    ) -> CardInstance:
        if not isinstance(raw, Mapping) or set(raw) != {
            "object_id",
            "logical_object_id",
            "ref",
            "owner",
        }:
            raise GameRuleError("Exiled object identity is malformed")
        card = self.state.cards.get(str(raw.get("object_id") or ""))
        if (
            card is None
            or card.zone != "exile"
            or card.logical_object_id != str(raw.get("logical_object_id") or "")
            or card.ref != str(raw.get("ref") or "")
            or card.owner != str(raw.get("owner") or "")
        ):
            raise GameRuleError("Exiled object identity changed")
        return card

    def _finish_one_shot_exile_cast_resolution(
        self,
        *,
        item: StackItem,
        producer: str,
        cleanup_cards: Sequence[CardInstance],
        outcome: str,
        candidate_ref: str | None,
        cast_stack_ref: str | None = None,
    ) -> None:
        if producer != EXILE_CAST_PRODUCER_CASCADE:
            raise StateInvariantError("Unsupported one-shot exile-cast producer")
        owners = {card.owner for card in cleanup_cards}
        if len(owners) > 1:
            raise StateInvariantError("Exile-cast cleanup must have one owner")
        if cleanup_cards:
            ZoneTransitionOwner(self).move_exiled_cards_to_library_bottom_random(
                [card.object_id for card in cleanup_cards],
                owner=next(iter(owners)),
                randomization_key=item.stack_id,
                reason="Cascade random-bottom cleanup",
            )
        self._remove_resolving_choice_item(item)
        self._log(
            item.controller,
            "cascade.resolve",
            f"Resolved {item.ref}: {item.label} ({outcome}).",
            {
                "stack": item.ref,
                "candidate": candidate_ref,
                "outcome": outcome,
                "cast_stack": cast_stack_ref,
                "bottom_count": len(cleanup_cards),
            },
            importance=2,
            changed_objects=[card.object_id for card in cleanup_cards],
            changed_players=[item.controller],
        )
        if self._stabilize():
            return
        self._grant_priority(self.state.active_player)

    def _complete_one_shot_exile_cast_choice(self, decision: Any) -> None:
        selection, payload = self._decode_one_shot_exile_cast_choice(decision)
        actor = decision.actors[0]
        item = next(
            (
                candidate
                for candidate in self.state.stack
                if candidate.ref == selection.stack_ref
                and candidate.stack_id == payload["resolution_stack_id"]
                and candidate.semantic_key == "builtin:cascade"
                and candidate.controller == actor
            ),
            None,
        )
        if item is None:
            raise GameRuleError("The Cascade trigger is no longer on the stack")
        candidate = self._current_exiled_identity(payload["candidate"])
        cleanup_rows = payload["cleanup"]
        if not isinstance(cleanup_rows, list) or not cleanup_rows:
            raise GameRuleError("Exile-cast cleanup identities are malformed")
        cleanup_cards = tuple(
            self._current_exiled_identity(row) for row in cleanup_rows
        )
        if candidate not in cleanup_cards or candidate.ref != selection.source_ref:
            raise GameRuleError("Exile-cast candidate detached from cleanup")
        maximum = payload["maximum_mana_value"]
        if type(maximum) not in {int, float} or maximum <= 0:
            raise GameRuleError("Exile-cast mana-value boundary is malformed")
        options = self._one_shot_exile_cast_options(
            actor=actor,
            card=candidate,
            maximum_mana_value=float(maximum),
        )
        if _options_fingerprint(options) != payload["options_fingerprint"]:
            raise GameRuleError("Exile-cast options changed")
        response = decision.responses[actor]
        choice = str(response.get("choice") or response.get("option") or "")
        if choice not in {"cast", "decline"}:
            raise GameRuleError("Choose whether to cast the exiled card")
        if choice == "decline":
            self._finish_one_shot_exile_cast_resolution(
                item=item,
                producer=payload["producer"],
                cleanup_cards=cleanup_cards,
                outcome="declined",
                candidate_ref=candidate.ref,
            )
            return
        cast_response = dict(response.get("cast") or {})
        cast_response.update(
            {
                key: copy.deepcopy(value)
                for key, value in response.items()
                if key not in {"action", "cast", "choice", "option"}
            }
        )
        requested_face = str(cast_response.get("face") or "") or None
        requested_option = str(cast_response.get("cost_option") or "") or None
        matches = [
            option
            for option in options
            if (requested_face is None or option.get("face") == requested_face)
            and (requested_option is None or option.get("id") == requested_option)
        ]
        if len(matches) != 1:
            raise GameRuleError("Select one current exile-cast option")
        selected_option = matches[0]
        before_stack_refs = {candidate_item.ref for candidate_item in self.state.stack}
        cast_response.update(
            {
                "card": candidate.ref,
                "from": "exile",
                "auto_pay": True,
                "cost_option": selected_option.get("id"),
            }
        )
        if selected_option.get("face") is not None:
            cast_response["face"] = selected_option["face"]
        self._cast(
            actor,
            cast_response,
            authorized_from_zone="exile",
            force_without_mana_cost=True,
            ignore_priority=True,
            ignore_timing=True,
            during_resolution=True,
        )
        cast_item = next(
            (
                candidate_item
                for candidate_item in reversed(self.state.stack)
                if candidate_item.ref not in before_stack_refs
                and candidate_item.kind == "spell"
                and candidate_item.card_object_id == candidate.object_id
            ),
            None,
        )
        if cast_item is None:
            raise StateInvariantError("The exile-cast choice created no spell")
        self._finish_one_shot_exile_cast_resolution(
            item=item,
            producer=payload["producer"],
            cleanup_cards=tuple(
                card for card in cleanup_cards if card is not candidate
            ),
            outcome="cast",
            candidate_ref=candidate.ref,
            cast_stack_ref=cast_item.ref,
        )


__all__ = [
    "EXILE_CAST_OPERATION_ID",
    "EXILE_CAST_PRODUCER_CASCADE",
    "OneShotExileCastChoiceOwnerMixin",
    "mana_value_of_cost",
]
