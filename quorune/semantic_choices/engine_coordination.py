from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from ..errors import GameRuleError
from ..counter_state import player_counter_snapshot
from ..model import StackItem
from ..object_query import object_query_result
from ..replacement.immutable import FrozenMap, thaw_value
from ..semantic_runtime.draw_restrictions import current_draw_permission
from ..semantic_runtime import (
    IntentPlan,
    prepare_draw_resolution,
)
from ..targets import TargetGroup, available_modes, target_plan
from ..util import unique_preserving_order
from .context import (
    ChoiceObjectView,
    ChoiceStackView,
    SemanticChoiceContext,
    SnapshotSemanticChoiceQuery,
)
from .counter_coordination import continue_semantic_completion
from .defaults import default_semantic_choice_registry
from .preparation_coordination import continue_semantic_preparation
from .model import (
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoiceFrame,
)


class SemanticChoiceCoordinationMixin:
    def _semantic_choice_object_rows(
        self,
        actor: str,
    ) -> tuple[ChoiceObjectView, ...]:
        public_zones = {
            "battlefield", "graveyard", "exile", "command", "stack"
        }
        rows: list[ChoiceObjectView] = []
        for card in self.state.cards.values():
            if card.zone not in public_zones and card.owner != actor:
                continue
            effective = self._effective_card_data(card)
            types, subtypes, supertypes = self._type_parts(
                str(effective.get("type_line") or "")
            )
            row = object_query_result(
                card,
                effective,
                type_parts=(types, subtypes, supertypes),
                known_to_actor=(
                    actor in card.known_to or card.zone in public_zones
                ),
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
            if card.zone == "stack":
                item = next(
                    (
                        value
                        for value in self.state.stack
                        if value.card_object_id == card.object_id
                    ),
                    None,
                )
                if item is not None:
                    # The stack object is public; its underlying card/copy
                    # identity is authoritative implementation data.
                    row = replace(row, ref=item.ref)
            rows.append(row)
        return tuple(rows)

    def _semantic_choice_stack_rows(self) -> tuple[ChoiceStackView, ...]:
        return tuple(
            ChoiceStackView(
                ref=item.ref,
                controller=item.controller,
                label=item.label,
                semantic_key=item.semantic_key,
                targets=tuple(item.targets),
                modes=tuple(item.modes),
                target_groups=dict(item.context.get("target_groups") or {}),
            )
            for item in self.state.stack
        )

    def _semantic_choice_damage_source_candidates(
        self,
        actor: str,
        object_rows: tuple[ChoiceObjectView, ...],
    ) -> tuple[str, ...]:
        """Materialize the legally known CR 609.7a source universe.

        Ordinary permanents, spells, and face-up command-zone objects are
        always candidates. Objects outside those zones are included only when
        a public stack object, waiting typed prevention/replacement effect, or
        delayed trigger refers to their physical object.
        """

        by_id = {row.object_id: row for row in object_rows}
        by_ref = {row.ref: row for row in object_rows}
        refs = {
            row.ref
            for row in object_rows
            if row.known_to_actor
            and row.zone in {"battlefield", "command", "stack"}
            and not self.state.cards[row.object_id].face_down
        }

        def add_object_id(object_id: str | None) -> None:
            row = by_id.get(str(object_id or ""))
            if (
                row is not None
                and row.known_to_actor
                and not self.state.cards[row.object_id].face_down
            ):
                refs.add(row.ref)

        def add_ref(ref: Any) -> None:
            row = by_ref.get(str(ref or ""))
            if (
                row is not None
                and row.known_to_actor
                and not self.state.cards[row.object_id].face_down
            ):
                refs.add(row.ref)

        for item in self.state.stack:
            add_object_id(item.card_object_id)
            add_object_id(item.source_object_id)
            for object_id in item.referred_object_ids:
                add_object_id(object_id)
            for target in item.targets:
                if isinstance(target, str):
                    add_ref(target)
        for trigger in self.state.delayed_triggers:
            if trigger.active:
                add_object_id(trigger.source_object_id)
                for object_id in trigger.referred_object_ids:
                    add_object_id(object_id)
        for shield in self.state.damage_prevention_shields:
            add_object_id(shield.subject.object_id)
            if shield.chosen_source is not None:
                add_object_id(shield.chosen_source.object_id)
        for redirection in self.state.damage_redirections:
            add_object_id(redirection.subject.object_id)
            add_object_id(redirection.destination.object_id)
            if redirection.chosen_source is not None:
                add_object_id(redirection.chosen_source.object_id)
        return tuple(sorted(refs))

    def _semantic_choice_candidates(
        self,
        actor: str,
        effect: Mapping[str, Any],
        source_ref: str | None,
    ) -> tuple[str, ...]:
        if str(effect.get("op") or "") != "choose_objects":
            return ()
        selector = dict(effect.get("selector") or {})
        selector.setdefault("min", int(effect.get("minimum", 1)))
        selector.setdefault(
            "max", int(effect.get("maximum", selector["min"]))
        )
        group = TargetGroup.from_mapping(selector, default_id="choice")
        return tuple(
            str(row["ref"])
            for row in self._target_candidate_rows(actor, group)
            if self._target_row_matches(
                actor,
                group,
                row,
                source_ref=source_ref,
                as_target=False,
            )
        )

    def _semantic_choice_target_facts(
        self,
        actor: str,
        effect: Mapping[str, Any],
        response: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        target_schemas: dict[str, Any] = {}
        validated_targets: dict[str, Any] = {}
        operation = str(effect.get("op") or "")
        if operation not in {"copy_stack_item", "retarget_stack_item"}:
            return target_schemas, validated_targets
        target_ref = str(
            effect.get("_target_stack_ref") or effect.get("stack") or ""
        )
        target_item = next(
            (item for item in self.state.stack if item.ref == target_ref),
            None,
        )
        if target_item is None:
            return target_schemas, validated_targets
        validation_actor = str(
            effect.get("_validation_actor")
            or (
                actor
                if operation == "copy_stack_item"
                else target_item.controller
            )
        )
        raw_schema = effect.get("_target_schema")
        if not isinstance(raw_schema, Mapping):
            raw_schema = self._stack_target_schema(
                target_item,
                self.semantics.get(target_item.semantic_key),
            )
        if not isinstance(raw_schema, Mapping) or not target_item.targets:
            return target_schemas, validated_targets
        public_schema: Mapping[str, Any] | None = None
        if operation == "retarget_stack_item" and available_modes(raw_schema):
            plan = target_plan(
                raw_schema, target_item.modes, require_modes=True
            )
            candidates = self._target_candidate_map(
                target_item.controller,
                plan,
                source_ref=self._stack_source_ref(target_item),
            )
            if self._target_plan_feasible(plan, candidates):
                public_schema = {
                    "groups": [
                        group.public_dict(candidates[group.group_id])
                        for group in plan.groups
                    ],
                    "legal_refs": unique_preserving_order(
                        ref
                        for group in plan.groups
                        for ref in candidates[group.group_id]
                    ),
                }
        else:
            public_schema = self._public_target_schema(
                validation_actor,
                raw_schema,
                source_ref=self._stack_source_ref(target_item),
            )
        if public_schema is None:
            return target_schemas, validated_targets
        key = f"{validation_actor}:{target_ref}"
        target_schemas[key] = {
            "authoritative": dict(raw_schema),
            "public": dict(public_schema),
        }
        submitted = (response or {}).get("targets")
        if submitted is not None:
            selected, grouped = self._validate_semantic_targets(
                validation_actor,
                self.semantics.get(target_item.semantic_key),
                self._normalize_target_submission(submitted),
                modes=list(target_item.modes),
                source_ref=self._stack_source_ref(target_item),
                target_schema=dict(raw_schema),
            )
            validated_targets[key] = {
                "targets": selected,
                "groups": grouped,
            }
        return target_schemas, validated_targets

    def _semantic_choice_canonical_names(
        self,
        response: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        canonical: dict[str, str] = {}
        submitted = {
            str(value).strip()
            for key, value in (response or {}).items()
            if key == "card_name" and str(value).strip()
        }
        for value in submitted:
            try:
                canonical[value.casefold()] = self.card_db.lookup(value).name
            except KeyError:
                continue
        return canonical

    def _semantic_choice_affordable_costs(
        self,
        actor: str,
        effect: Mapping[str, Any],
        object_rows: tuple[ChoiceObjectView, ...],
    ) -> frozenset[str]:
        cost_value: Mapping[str, Any] | None = None
        if isinstance(effect.get("_requirements"), Mapping):
            cost_value = effect["_requirements"]
        elif isinstance(effect.get("cost"), Mapping):
            cost_value = effect["cost"]
        elif str(effect.get("op") or "") == "remora_tax":
            cost_value = {"GENERIC": 4}
        elif (
            str(effect.get("op") or "") == "transmute_artifact"
            and str(effect.get("stage") or "") == "pay"
        ):
            cost_value = {
                "GENERIC": max(0, int(effect.get("difference", 0)))
            }
        elif (
            str(effect.get("op") or "") == "cumulative_upkeep"
            and str(effect.get("stage") or "") == "pay"
        ):
            per_counter = self._mana_vector(
                effect.get("cost_per_counter") or {"GENERIC": 1}
            )
            source = next(
                (
                    row
                    for row in object_rows
                    if row.ref == str(effect.get("source") or "")
                ),
                None,
            )
            if source is not None:
                age = int(source.counters.get("age", 0))
                cost_value = {
                    key: int(value) * age
                    for key, value in per_counter.items()
                }
        if cost_value is None:
            return frozenset()
        requirements = self._mana_vector(cost_value)
        if not self._cost_is_affordable(actor, requirements):
            return frozenset()
        return frozenset(
            {
                SnapshotSemanticChoiceQuery._cost_key(actor, requirements)
            }
        )

    def _semantic_choice_authorized_cast_options(
        self,
        actor: str,
        effect: Mapping[str, Any],
        source_ref: str | None,
    ) -> tuple[Mapping[str, Any], ...]:
        if (
            str(effect.get("op") or "") != "madness_cast_choice"
            or source_ref is None
        ):
            return ()
        card = next(
            (
                value
                for value in self.state.cards.values()
                if value.ref == source_ref
            ),
            None,
        )
        if card is None:
            return ()
        return tuple(
            self._madness_exile_cast_options(
                actor=actor,
                card=card,
                raw_spec=effect.get("madness"),
            )
        )

    def _semantic_choice_query(
        self,
        actor: str,
        *,
        response: Mapping[str, Any] | None = None,
        effect: Mapping[str, Any] | None = None,
        source_ref: str | None = None,
    ) -> SnapshotSemanticChoiceQuery:
        """Materialize only actor-visible, immutable choice facts."""

        choice_effect = effect or {}
        object_rows = self._semantic_choice_object_rows(actor)
        target_schemas, validated_targets = (
            self._semantic_choice_target_facts(
                actor, choice_effect, response
            )
        )
        return SnapshotSemanticChoiceQuery(
            seat_order=tuple(self.seats),
            active_order=tuple(self.active_seats),
            object_rows=object_rows,
            stack_rows=self._semantic_choice_stack_rows(),
            life_by_seat={
                seat: self.state.players[seat].life for seat in self.seats
            },
            counters_by_seat={
                seat: player_counter_snapshot(self.state.players[seat])
                for seat in self.seats
            },
            libraries_by_seat={
                actor: [
                    self.state.cards[object_id].ref
                    for object_id in self.state.players[actor].zones["library"]
                ]
            },
            mana_by_seat={
                actor: dict(self.state.players[actor].mana_pool)
            },
            affordable_costs=self._semantic_choice_affordable_costs(
                actor, choice_effect, object_rows
            ),
            authorized_cast_option_rows=tuple(
                FrozenMap(value)
                for value in self._semantic_choice_authorized_cast_options(
                    actor,
                    choice_effect,
                    source_ref,
                )
            ),
            canonical_names=self._semantic_choice_canonical_names(response),
            target_schemas=target_schemas,
            validated_targets=validated_targets,
            drawn_this_turn_by_seat={
                actor: tuple(
                    str(entry.get("object"))
                    for entry in self.state.players[actor].draw_history
                    if entry.get("turn_sequence") == self.state.turn_sequence
                )
            },
            opponent_cast_colors_by_seat={
                seat: tuple(
                    sorted(
                        {
                            str(color).upper()
                            for event in self.state.events
                            if event.turn_sequence == self.state.turn_sequence
                            and event.code == "stack.cast"
                            and event.actor in self.active_seats
                            and event.actor != seat
                            for color in event.details.get("colors", [])
                            if str(color).upper() in {"W", "U", "B", "R", "G"}
                        }
                    )
                )
                for seat in self.seats
            },
            draw_permissions_by_seat=(
                {
                    seat: current_draw_permission(self, seat).to_dict()
                    for seat in self.active_seats
                }
                if choice_effect.get("op") == "offer_draw"
                else {}
            ),
            materialized_choice_candidates=self._semantic_choice_candidates(
                actor, choice_effect, source_ref
            ),
            materialized_damage_source_candidates=(
                self._semantic_choice_damage_source_candidates(
                    actor, object_rows
                )
            ),
            current_turn_sequence=self.state.turn_sequence,
        )
    def _semantic_choice_context(
        self,
        item: StackItem,
        actor: str,
        effect: Mapping[str, Any],
    ) -> SemanticChoiceContext:
        source_id = item.source_object_id or item.card_object_id or ""
        source = self.state.cards.get(source_id)
        program = self.semantics.get(item.semantic_key)
        return SemanticChoiceContext(
            actor=actor,
            stack_ref=item.ref,
            stack_controller=item.controller,
            stack_label=item.label,
            source_ref=source.ref if source is not None else None,
            card_ref=(
                self.state.cards[item.card_object_id].ref
                if item.card_object_id in self.state.cards
                else None
            ),
            semantic_program_id=item.semantic_key,
            semantic_program_version=program.version if program else None,
            query=self._semantic_choice_query(
                actor,
                effect=effect,
                source_ref=source.ref if source is not None else None,
            ),
            source_logical_object_id=(
                str(item.context.get("source_logical_object_id") or "")
                or (source.logical_object_id if source is not None else None)
            ),
            source_object_id=(
                source.object_id if source is not None else None
            ),
        )

    def _begin_registered_semantic_choice(
        self,
        *,
        item: StackItem,
        effect: Mapping[str, Any],
        remaining: Sequence[Mapping[str, Any]],
        destination: str | None,
        note: str,
        instruction_pointer: int,
    ) -> None:
        registry = default_semantic_choice_registry()
        handler = registry.handler_for_operation(str(effect.get("op") or ""))
        seat = str(effect.get("player") or item.controller)
        try:
            preparation = handler.prepare(
                effect,
                self._semantic_choice_context(item, seat, effect),
            )
        except SemanticChoiceError as exc:
            raise GameRuleError(str(exc)) from exc
        continuation = SemanticChoiceContinuation(
            handler_id=handler.handler_id,
            handler_version=handler.schema_version,
            stack_ref=item.ref,
            effect=preparation.continuation_effect,
            remaining=tuple(dict(value) for value in remaining),
            destination=destination,
            note=note,
            semantic_frame=SemanticChoiceFrame(
                semantic_program_id=str(item.semantic_key or ""),
                semantic_program_version=(
                    self.semantics.get(item.semantic_key).version
                    if self.semantics.get(item.semantic_key)
                    else None
                ),
                stack_object=item.ref,
                instruction_pointer=instruction_pointer,
                controller=item.controller,
            ),
        )
        preparation_intents = tuple(preparation.preparation_intents)
        # Only a single typed draw intent needs to leave this immediate
        # preparation path for the replacement-aware draw coordinator.  Other
        # preparations may intentionally be an ordered sequence (for example,
        # explore reveals a card and then moves it); each of those intents is
        # still committed separately below.
        preparation_plan = (
            IntentPlan(
                operation=handler.operation,
                handler_id=handler.handler_id,
                intents=preparation_intents,
            )
            if len(preparation_intents) == 1
            else None
        )
        draw_request = (
            prepare_draw_resolution(preparation_plan, ())
            if preparation_plan is not None
            else None
        )
        if draw_request is not None and draw_request.current is not None:
            if preparation.request is not None or preparation.auto_continue is None:
                raise GameRuleError(
                    "A semantic-choice draw must have one automatic resume path"
                )
            self._begin_draw_sequence(
                draw_request.current.player,
                draw_request.current.count,
                reason=draw_request.current.reason,
                private=draw_request.current.private,
                post_draw_actions=draw_request.current.post_draw_actions,
                continuation={
                    "kind": "semantic_resolution",
                    "stack_ref": item.ref,
                    "effects": [
                        *draw_request.remaining_effects,
                        *(
                            thaw_value(value)
                            for value in preparation.auto_continue.prepend_effects
                        ),
                        *(thaw_value(value) for value in remaining),
                    ],
                    "destination": destination,
                    "note": note,
                    "instruction_pointer": instruction_pointer + 1,
                },
            )
            return
        try:
            continue_semantic_preparation(
                self,
                continuation=continuation,
                actor=seat,
                preparation=preparation,
            )
        except SemanticChoiceError as exc:
            raise GameRuleError(str(exc)) from exc

    def _complete_registered_semantic_choice(self, decision: Any) -> None:
        seat = decision.actors[0]
        response = decision.responses[seat]
        registry = default_semantic_choice_registry()
        try:
            handler, continuation = registry.decode_continuation(
                decision.continuation
            )
        except SemanticChoiceError as exc:
            raise GameRuleError(str(exc)) from exc
        item = next(
            (
                candidate
                for candidate in self.state.stack
                if candidate.ref == continuation.stack_ref
            ),
            None,
        )
        if item is None:
            raise GameRuleError(
                "The semantic choice's stack object no longer exists"
            )
        self._validate_semantic_frame(
            continuation.semantic_frame.to_dict(),
            item,
        )
        try:
            completion = handler.complete(
                continuation,
                response,
                self._semantic_choice_query(
                    seat,
                    response=response,
                    effect=continuation.effect,
                    source_ref=(
                        self.state.cards[
                            item.source_object_id
                            or item.card_object_id
                            or ""
                        ].ref
                        if (
                            item.source_object_id
                            or item.card_object_id
                            or ""
                        )
                        in self.state.cards
                        else None
                    ),
                ),
            )
        except SemanticChoiceError as exc:
            raise GameRuleError(str(exc)) from exc
        try:
            continue_semantic_completion(
                self,
                item=item,
                continuation=continuation,
                actor=seat,
                response=response,
                completion=completion,
            )
        except SemanticChoiceError as exc:
            raise GameRuleError(str(exc)) from exc

    def _begin_semantic_choice(
        self,
        *,
        item: StackItem,
        effect: Mapping[str, Any],
        remaining: Sequence[Mapping[str, Any]],
        destination: str | None,
        note: str,
        instruction_pointer: int = 0,
    ) -> None:
        operation = str(effect.get("op") or "")
        if operation not in default_semantic_choice_registry().operations:
            raise GameRuleError(
                f"Unregistered semantic choice operation {operation!r}"
            )
        self._begin_registered_semantic_choice(
            item=item,
            effect=effect,
            remaining=remaining,
            destination=destination,
            note=note,
            instruction_pointer=instruction_pointer,
        )

    def _complete_semantic_choice(self, decision: Any) -> None:
        continuation = decision.continuation
        effect = continuation.get("effect")
        operation = (
            str(effect.get("op") or "")
            if isinstance(effect, Mapping)
            else ""
        )
        if operation not in default_semantic_choice_registry().operations:
            raise GameRuleError(
                f"Unregistered semantic choice continuation {operation!r}"
            )
        self._complete_registered_semantic_choice(decision)
