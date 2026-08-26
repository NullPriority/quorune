from __future__ import annotations

import copy
from typing import Any, Mapping, Protocol, Sequence

from ..errors import GameRuleError
from ..model import CardInstance, StackItem
from ..object_predicate import (
    ObjectQueryError,
    permanent_state_predicate_matches,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.modal_selection import canonical_modes
from ..semantics import SemanticProgram
from ..target_characteristics import TargetCharacteristicSnapshot
from ..target_predicates import TargetPredicateError, target_predicate_matches
from ..target_protection import TargetProtectionVerdict
from ..target_protection_engine_adapter import target_protection_verdict_for_row
from ..targets import (
    TargetGroup,
    TargetPlan,
    available_modes,
    target_plan,
)
from ..util import unique_preserving_order
from .model import (
    SelectionContract,
    SelectionContinuation,
    SelectionModelError,
    decode_selection_continuation,
)
from .target_query import (
    target_identity_matches_snapshot,
    target_snapshot,
)


TARGET_OPERATION_ID = "selection.target.semantic.v1"


class TargetSelectionHost(Protocol):
    """Narrow authoritative facts/callbacks consumed by target selection."""

    state: Any
    active_seats: Sequence[str]
    semantics: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...
    def _type_parts(self, type_line: str) -> tuple[set[str], set[str], set[str]]: ...
    def _increment_optimization(self, seat: str, key: str, amount: int = 1) -> None: ...


def _target_group_public_state_matches(
    group: TargetGroup,
    *,
    card: CardInstance | None,
    turn_sequence: int,
) -> bool:
    """Evaluate one target group's typed current public-state predicate."""

    if group.state_predicate is None:
        return True
    if card is None:
        return False
    try:
        return permanent_state_predicate_matches(
            group.state_predicate,
            counters=card.counters,
            entered_this_turn=(
                card.zone == "battlefield"
                and card.entered_battlefield_turn_sequence > 0
                and card.entered_battlefield_turn_sequence == turn_sequence
            ),
            tapped=card.tapped,
        )
    except ObjectQueryError as exc:
        raise GameRuleError(str(exc)) from exc


def _target_group_combat_state_matches(
    group: TargetGroup,
    *,
    card: CardInstance | None,
) -> bool:
    """Evaluate legacy flags and the closed current combat-state predicate."""

    attacking = bool(card and card.attacking is not None)
    blocking = bool(card and card.blocking is not None)
    if group.attacking is not None and attacking != group.attacking:
        return False
    if group.blocking is not None and blocking != group.blocking:
        return False
    if group.combat_state == "attacking":
        return attacking
    if group.combat_state == "blocking":
        return blocking
    if group.combat_state == "attacking_or_blocking":
        return attacking or blocking
    return True


class TargetSelectionOwnerMixin:
    """Own target advertisement, submission validation, and revalidation."""

    def _target_selection_continuation(
        self,
        *,
        actor: str,
        item: StackItem,
        public_schema: Mapping[str, Any],
        trigger_creation: bool = False,
    ) -> SelectionContinuation:
        return SelectionContinuation(
            contract=SelectionContract.TARGETING,
            operation_id=TARGET_OPERATION_ID,
            actor=actor,
            state_revision=self.state.revision,
            stack_ref=item.ref,
            source_ref=self._stack_source_ref(item),
            visibility="public",
            payload=FrozenMap(
                {
                    "public_schema": dict(public_schema),
                    "trigger_creation": trigger_creation,
                }
            ),
        )

    def _semantic_target_options(
        self,
        controller: str,
        schema: Mapping[str, Any],
        *,
        modes: Sequence[str] = (),
        source_ref: str | None = None,
    ) -> list[str]:
        """Return the candidate-set union for a declarative target plan.

        Candidate sets are intentionally returned rather than target tuples.
        The submitted grouping/count/distinctness constraints are validated by
        the authoritative engine.
        """

        try:
            selected_modes = canonical_modes(
                schema,
                modes,
                require_modes=bool(available_modes(schema)),
            )
            plan = target_plan(
                schema,
                selected_modes,
                require_modes=bool(available_modes(schema)),
            )
        except ValueError:
            return []
        options: list[str] = []
        for group in plan.groups:
            options.extend(
                self._target_candidates(
                    controller,
                    group,
                    source_ref=source_ref,
                )
            )
        return unique_preserving_order(options)

    @staticmethod
    def _relation_matches(
        value: str | None,
        controller: str,
        relation: str,
    ) -> bool:
        if relation == "any":
            return True
        if relation == "you":
            return value == controller
        return value is not None and value != controller

    def _target_candidate_rows(
        self,
        controller: str,
        group: TargetGroup,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if "player" in group.zones:
            for seat in self.active_seats:
                rows.append(
                    {
                        "ref": seat,
                        "zone": "player",
                        "category": "player",
                        "controller": seat,
                        "owner": seat,
                        **TargetCharacteristicSnapshot().row_values(),
                        "card": None,
                    }
                )
        if "stack" in group.zones:
            for item in self.state.stack:
                card = self.state.cards.get(item.card_object_id or "")
                data = self._effective_card_data(card) if card else {}
                ability_source = self.state.cards.get(
                    item.source_object_id or ""
                )
                source_data = (
                    self._effective_card_data(ability_source)
                    if ability_source is not None
                    else data
                )
                stack_source_types, _, _ = self._type_parts(
                    str(source_data.get("type_line") or "")
                )
                rows.append(
                    {
                        "ref": item.ref,
                        "zone": "stack",
                        "category": (
                            "spell"
                            if item.kind in {"spell", "spell_copy"}
                            else "ability"
                        ),
                        "controller": item.controller,
                        "owner": card.owner if card else item.controller,
                        **TargetCharacteristicSnapshot.from_effective_data(
                            data
                        ).row_values(),
                        "card": card,
                        "stack_item": item,
                        "stack_source_types": stack_source_types,
                    }
                )
        for zone in (
            "battlefield",
            "graveyard",
            "exile",
            "command",
        ):
            if zone not in group.zones:
                continue
            for seat in self.active_seats:
                for object_id in self.state.players[seat].zones.get(zone, []):
                    card = self.state.cards[object_id]
                    if card.face_down and controller not in card.known_to:
                        # A face-down object in a public zone remains a public,
                        # targetable object.  Candidate generation must use only
                        # its public characteristics and never its hidden front.
                        data = {
                            "type_line": (
                                "Creature" if zone == "battlefield" else ""
                            ),
                            "colors": [],
                            "mana_value": 0,
                        }
                    else:
                        data = self._effective_card_data(card)
                    rows.append(
                        {
                            "ref": card.ref,
                            "zone": zone,
                            "category": (
                                "permanent" if zone == "battlefield" else "card"
                            ),
                            "controller": card.controller,
                            "owner": card.owner,
                            **TargetCharacteristicSnapshot.from_effective_data(
                                data
                            ).row_values(),
                            "card": card,
                        }
                    )
        return rows

    def _target_row_matches(
        self,
        controller: str,
        group: TargetGroup,
        row: Mapping[str, Any],
        *,
        source_ref: str | None,
        as_target: bool = True,
    ) -> bool:
        ref = str(row["ref"])
        if (group.source_exclusion or group.another) and ref == source_ref:
            return False
        card = row.get("card")
        if (
            row.get("category") == "card"
            and isinstance(card, CardInstance)
            and not card.is_card_object
        ):
            # CR 111.6/707.10: tokens and noncard copies may briefly exist in
            # another zone before the next state check, but are never cards.
            return False
        if as_target and target_protection_verdict_for_row(
            self,
            acting_controller=controller,
            row=row,
            source_ref=source_ref,
        ) is not TargetProtectionVerdict.ALLOWED:
            return False
        if group.categories and str(row["category"]) not in {
            value.casefold() for value in group.categories
        }:
            return False
        if not self._relation_matches(
            str(row.get("controller")),
            controller,
            group.controller_relation,
        ):
            return False
        if (
            group.controller_seat is not None
            and str(row.get("controller")) != group.controller_seat
        ):
            return False
        if not self._relation_matches(
            str(row.get("owner")),
            controller,
            group.owner_relation,
        ):
            return False
        if row["category"] == "player" and not self._relation_matches(
            str(row["ref"]),
            controller,
            group.player_relation,
        ):
            return False
        characteristics = TargetCharacteristicSnapshot.from_row(row)
        if not characteristics.matches(group):
            return False
        types = set(characteristics.types)
        supertypes = set(characteristics.supertypes)
        colors = set(characteristics.colors)
        if group.colors_any and not colors.intersection(group.colors_any):
            return False
        if group.colors_all and not set(group.colors_all).issubset(colors):
            return False
        if group.colors_none and colors.intersection(group.colors_none):
            return False
        if group.colorless is not None and (not colors) != group.colorless:
            return False
        if (
            group.color_count_equal is not None
            and len(colors) != group.color_count_equal
        ):
            return False
        if (
            group.color_count_min is not None
            and len(colors) < group.color_count_min
        ):
            return False
        mana_value = characteristics.mana_value
        if (
            group.mana_value_equal is not None
            and mana_value != group.mana_value_equal
        ):
            return False
        if (
            group.mana_value_min is not None
            and mana_value < group.mana_value_min
        ):
            return False
        if (
            group.mana_value_max is not None
            and mana_value > group.mana_value_max
        ):
            return False
        card = row.get("card")
        if not _target_group_combat_state_matches(group, card=card):
            return False
        if group.tapped is not None and (
            bool(card and card.tapped) != group.tapped
        ):
            return False
        if not _target_group_public_state_matches(
            group,
            card=card if isinstance(card, CardInstance) else None,
            turn_sequence=self.state.turn_sequence,
        ):
            return False
        if group.commander is not None and (
            bool(card and card.is_commander) != group.commander
        ):
            return False
        if group.token is not None and (
            bool(card and card.is_token) != group.token
        ):
            return False
        derived = {
            name: name in types
            for name in ("land", "creature", "artifact", "enchantment")
        }
        derived["permanent"] = row["category"] == "permanent"
        try:
            if not target_predicate_matches(
                self,
                group,
                row,
                types=types,
                supertypes=supertypes,
                colors=colors,
                derived=derived,
            ):
                return False
        except TargetPredicateError as exc:
            raise GameRuleError(str(exc)) from exc
        for name in (
            "land",
            "creature",
            "artifact",
            "enchantment",
            "permanent",
        ):
            expected = getattr(group, name)
            if expected is not None and derived[name] != expected:
                return False
        return True

    def _target_candidates(
        self,
        controller: str,
        group: TargetGroup,
        *,
        source_ref: str | None = None,
    ) -> list[str]:
        values = [
            str(row["ref"])
            for row in self._target_candidate_rows(controller, group)
            if self._target_row_matches(
                controller,
                group,
                row,
                source_ref=source_ref,
            )
        ]
        values = unique_preserving_order(values)
        self._optimization_stats(controller)["target_candidates_generated"] += len(
            values
        )
        return values

    def _target_snapshot(self, ref: str) -> dict[str, Any]:
        return target_snapshot(self, ref)

    def _target_identity_matches_snapshot(
        self,
        ref: str,
        snapshot: Mapping[str, Any],
    ) -> bool:
        """Return whether ``ref`` is still the originally selected object."""

        return target_identity_matches_snapshot(self, ref, snapshot)

    def _target_candidate_map(
        self,
        controller: str,
        plan: TargetPlan,
        *,
        source_ref: str | None,
    ) -> dict[str, list[str]]:
        return {
            group.group_id: self._target_candidates(
                controller,
                group,
                source_ref=source_ref,
            )
            for group in plan.groups
        }

    def _target_plan_feasible(
        self,
        plan: TargetPlan,
        candidates: Mapping[str, Sequence[str]],
    ) -> bool:
        for group in plan.groups:
            if len(candidates.get(group.group_id, ())) < group.min_targets:
                return False
        slots = [
            group
            for group in plan.groups
            for _ in range(group.min_targets)
        ]

        def choose(
            index: int,
            selected: dict[str, list[str]],
            globally_used: set[str],
        ) -> bool:
            if index >= len(slots):
                for left_group, right_group in plan.same_player_groups:
                    left = selected.get(left_group, [])
                    right = selected.get(right_group, [])
                    if not left or not right:
                        return False
                    if any(
                        self._target_snapshot(left_ref).get("controller")
                        != self._target_snapshot(right_ref).get(
                            "controller"
                        )
                        for left_ref in left
                        for right_ref in right
                    ):
                        return False
                return True
            group = slots[index]
            for ref in candidates.get(group.group_id, ()):
                own = selected.setdefault(group.group_id, [])
                if group.distinct and not group.allow_reuse and ref in own:
                    continue
                if plan.globally_distinct and ref in globally_used:
                    continue
                if any(
                    ref in selected.get(other, ())
                    for other in group.different_from_groups
                ):
                    continue
                own.append(ref)
                added_global = ref not in globally_used
                if added_global:
                    globally_used.add(ref)
                if choose(index + 1, selected, globally_used):
                    return True
                own.pop()
                if added_global:
                    globally_used.remove(ref)
            return False

        return choose(0, {}, set())

    def _public_target_schema(
        self,
        controller: str,
        schema: Mapping[str, Any],
        *,
        source_ref: str | None,
    ) -> dict[str, Any] | None:
        modes = available_modes(schema)
        if modes:
            legal_modes: list[str] = []
            mode_schemas: dict[str, Any] = {}
            for mode in modes:
                try:
                    plan = target_plan(schema, [mode], require_modes=False)
                except ValueError:
                    continue
                candidates = self._target_candidate_map(
                    controller,
                    plan,
                    source_ref=source_ref,
                )
                if not self._target_plan_feasible(plan, candidates):
                    continue
                legal_modes.append(mode)
                mode_schemas[mode] = {
                    "groups": [
                        group.public_dict(candidates[group.group_id])
                        for group in plan.groups
                    ]
                }
            minimum_modes = int(
                schema.get("min_modes", schema.get("mode_count", 1))
            )
            if len(legal_modes) < minimum_modes:
                self._increment_optimization(
                    controller, "illegal_target_actions_prevented"
                )
                self._increment_optimization(
                    controller, "actions_removed_for_mode_target_failure"
                )
                return None
            legal_refs = unique_preserving_order(
                ref
                for mode in legal_modes
                for group in mode_schemas[mode]["groups"]
                for ref in group["legal_refs"]
            )
            return {
                "mode_count": int(schema.get("mode_count", 1)),
                "min_modes": int(
                    schema.get("min_modes", schema.get("mode_count", 1))
                ),
                "max_modes": int(
                    schema.get("max_modes", schema.get("mode_count", 1))
                ),
                "legal_modes": legal_modes,
                "mode_schemas": mode_schemas,
                "legal_refs": legal_refs,
            }
        try:
            plan = target_plan(schema)
        except ValueError:
            return None
        candidates = self._target_candidate_map(
            controller,
            plan,
            source_ref=source_ref,
        )
        if not self._target_plan_feasible(plan, candidates):
            self._increment_optimization(
                controller, "illegal_target_actions_prevented"
            )
            self._increment_optimization(
                controller, "actions_removed_for_no_targets"
            )
            return None
        result = copy.deepcopy(dict(schema))
        result["groups"] = [
            group.public_dict(candidates[group.group_id])
            for group in plan.groups
        ]
        if len(plan.groups) == 1:
            result["legal_refs"] = list(candidates[plan.groups[0].group_id])
        return result

    @staticmethod
    def _normalize_target_submission(targets: Any) -> list[Any]:
        """Normalize flat refs or a typed group map for target validation."""

        if targets is None:
            return []
        if isinstance(targets, Mapping):
            normalized: list[dict[str, str]] = []
            for group_id, raw_refs in targets.items():
                if isinstance(raw_refs, str):
                    refs: Sequence[Any] = [raw_refs]
                elif isinstance(raw_refs, Sequence) and not isinstance(
                    raw_refs,
                    (str, bytes, bytearray),
                ):
                    refs = raw_refs
                else:
                    raise GameRuleError(
                        "Target group values must be a ref or an array of refs"
                    )
                for ref in refs:
                    if isinstance(ref, Mapping) or ref is None:
                        raise GameRuleError(
                            "Target group values must contain only refs"
                        )
                    normalized.append(
                        {
                            "group": str(group_id),
                            "ref": str(ref),
                        }
                    )
            return normalized
        if isinstance(targets, Sequence) and not isinstance(
            targets,
            (str, bytes, bytearray),
        ):
            return list(targets)
        raise GameRuleError(
            "Targets must be an array of refs or a group-to-refs object"
        )

    @staticmethod
    def _group_target_submission(
        plan: TargetPlan,
        targets: Sequence[Any],
    ) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {
            group.group_id: [] for group in plan.groups
        }
        if targets and all(isinstance(value, Mapping) for value in targets):
            for value in targets:
                group_id = str(value.get("group") or value.get("group_id") or "")
                ref = value.get("ref", value.get("target"))
                if group_id not in grouped or ref is None:
                    raise GameRuleError("Grouped target selection is malformed")
                grouped[group_id].append(str(ref))
            return grouped
        if len(plan.groups) == 1:
            grouped[plan.groups[0].group_id] = [str(value) for value in targets]
            return grouped
        cursor = 0
        if all(group.min_targets == group.max_targets for group in plan.groups):
            for group in plan.groups:
                grouped[group.group_id] = [
                    str(value)
                    for value in targets[
                        cursor : cursor + group.min_targets
                    ]
                ]
                cursor += group.min_targets
            if cursor == len(targets):
                return grouped
        raise GameRuleError(
            "Multiple variable target groups require "
            "{group, ref} target selections"
        )

    def _validate_semantic_targets(
        self,
        controller: str,
        program: SemanticProgram | None,
        targets: Sequence[Any],
        *,
        modes: Sequence[str] = (),
        source_ref: str | None = None,
        target_schema: Mapping[str, Any] | None = None,
    ) -> tuple[list[str], dict[str, list[str]]]:
        schema = (
            target_schema
            if target_schema is not None
            else program.target_schema
            if program is not None
            else None
        )
        if schema is None:
            if targets or modes:
                self._increment_optimization(
                    controller, "target_submissions_rejected"
                )
                raise GameRuleError(
                    "This semantic program does not accept targets or modes"
                )
            return [], {}
        try:
            selected_modes = canonical_modes(
                schema,
                modes,
                require_modes=bool(available_modes(schema)),
            )
            plan = target_plan(
                schema,
                selected_modes,
                require_modes=bool(available_modes(schema)),
            )
            candidates = self._target_candidate_map(
                controller,
                plan,
                source_ref=source_ref,
            )
            grouped = self._group_target_submission(plan, targets)
            used_global: set[str] = set()
            for group in plan.groups:
                chosen = grouped[group.group_id]
                if not (
                    group.min_targets
                    <= len(chosen)
                    <= group.max_targets
                ):
                    raise GameRuleError(
                        f"Target group {group.group_id} requires between "
                        f"{group.min_targets} and {group.max_targets} target(s)"
                    )
                if (
                    group.distinct
                    and not group.allow_reuse
                    and len(set(chosen)) != len(chosen)
                ):
                    raise GameRuleError(
                        f"Target group {group.group_id} requires distinct targets"
                    )
                legal = set(candidates[group.group_id])
                if any(ref not in legal for ref in chosen):
                    raise GameRuleError(
                        "Selected target is not legal for this target group"
                    )
                if any(
                    ref in grouped.get(other, ())
                    for other in group.different_from_groups
                    for ref in chosen
                ):
                    raise GameRuleError(
                        "Selected targets violate a different-target restriction"
                    )
                if plan.globally_distinct and any(
                    ref in used_global for ref in chosen
                ):
                    raise GameRuleError(
                        "Target groups require globally distinct targets"
                    )
                used_global.update(chosen)
            for left_group, right_group in plan.same_player_groups:
                left = grouped.get(left_group, [])
                right = grouped.get(right_group, [])
                if not left or not right:
                    raise GameRuleError(
                        "Related target groups must both contain a target"
                    )
                if any(
                    self._target_snapshot(left_ref).get("controller")
                    != self._target_snapshot(right_ref).get("controller")
                    for left_ref in left
                    for right_ref in right
                ):
                    raise GameRuleError(
                        "Related targets must belong to the same player"
                    )
            flattened = [
                ref
                for group in plan.groups
                for ref in grouped[group.group_id]
            ]
            return flattened, grouped
        except (GameRuleError, ValueError) as exc:
            self._increment_optimization(
                controller, "target_submissions_rejected"
            )
            if isinstance(exc, GameRuleError):
                raise
            raise GameRuleError(str(exc)) from exc

    @staticmethod
    def _stack_target_schema(
        item: StackItem,
        program: SemanticProgram | None,
    ) -> Mapping[str, Any] | None:
        if "target_schema_override" in item.context:
            return dict(item.context["target_schema_override"])
        return program.target_schema if program is not None else None

    def _stack_source_ref(self, item: StackItem) -> str:
        if (
            item.source_object_id
            and item.source_object_id in self.state.cards
        ):
            return self.state.cards[item.source_object_id].ref
        if (
            item.card_object_id
            and item.card_object_id in self.state.cards
        ):
            return self.state.cards[item.card_object_id].ref
        return item.ref

    def _complete_semantic_target(self, decision: Any) -> None:
        seat = decision.actors[0]
        response = decision.responses[seat]
        raw_continuation = decision.continuation
        legacy = SelectionContinuation(
            contract=SelectionContract.TARGETING,
            operation_id=TARGET_OPERATION_ID,
            actor=seat,
            state_revision=decision.created_revision,
            stack_ref=str(raw_continuation.get("stack_ref") or "") or None,
            visibility="public",
            payload=FrozenMap(
                {
                    "public_schema": {},
                    "trigger_creation": bool(
                        raw_continuation.get("trigger_creation")
                    ),
                }
            ),
        )
        try:
            selection = decode_selection_continuation(
                raw_continuation,
                expected_contract=SelectionContract.TARGETING,
                expected_operation_id=TARGET_OPERATION_ID,
                legacy=legacy,
            )
        except SelectionModelError as exc:
            raise GameRuleError(str(exc)) from exc
        if selection.actor != seat:
            raise GameRuleError("Semantic target actor changed")
        if selection.state_revision != decision.created_revision:
            raise GameRuleError("Semantic target state revision changed")
        if selection.visibility != "public":
            raise GameRuleError("Semantic target visibility changed")
        stack_ref = str(selection.stack_ref or "")
        continuation_payload = thaw_value(selection.payload)
        item = next(
            (candidate for candidate in self.state.stack if candidate.ref == stack_ref),
            None,
        )
        if item is None:
            raise GameRuleError("The targeted semantic object is no longer on the stack")
        if "selection" in raw_continuation:
            if selection.source_ref != self._stack_source_ref(item):
                raise GameRuleError("Semantic target source identity changed")
            target_schema = self._stack_target_schema(
                item,
                self.semantics.get(item.semantic_key),
            )
            current_public_schema = (
                self._public_target_schema(
                    seat,
                    target_schema,
                    source_ref=(
                        self._stack_source_ref(item)
                        if continuation_payload.get("trigger_creation")
                        else item.ref
                    ),
                )
                if target_schema is not None
                else None
            )
            if current_public_schema != continuation_payload.get(
                "public_schema"
            ):
                raise GameRuleError("Semantic target candidates changed")
        program = self.semantics.get(item.semantic_key)
        targets = self._normalize_target_submission(
            response.get("targets")
        )
        modes = [str(value) for value in response.get("modes") or []]
        target_schema = self._stack_target_schema(item, program)
        validated, grouped = self._validate_semantic_targets(
            seat,
            program,
            targets,
            modes=modes,
            source_ref=item.ref,
            target_schema=target_schema,
        )
        item.targets = validated
        if target_schema is None:
            raise GameRuleError("Semantic target schema is no longer available")
        item.modes = list(
            canonical_modes(
                target_schema,
                modes,
                require_modes=bool(available_modes(target_schema)),
            )
        )
        item.context["target_groups"] = grouped
        item.context["target_snapshots"] = {
            ref: self._target_snapshot(ref) for ref in validated
        }
        item.context["targets_revalidated"] = False
        if continuation_payload.get("trigger_creation"):
            item.context.pop("trigger_target_selection_pending", None)
            item.context["targets_chosen_at_creation"] = True
            self._grant_priority(self.state.active_player)
        else:
            self._prepare_stack_resolution()

    def _revalidate_resolution_targets(self, item: StackItem) -> bool:
        if item.context.get("targets_revalidated"):
            return True
        program = self.semantics.get(item.semantic_key)
        target_schema = self._stack_target_schema(item, program)
        if target_schema is None:
            item.context["targets_revalidated"] = True
            return True
        try:
            plan = target_plan(
                target_schema,
                item.modes,
                require_modes=bool(available_modes(target_schema)),
            )
            candidates = self._target_candidate_map(
                item.controller,
                plan,
                source_ref=item.ref,
            )
            grouped = dict(item.context.get("target_groups") or {})
            if not grouped:
                grouped = self._group_target_submission(plan, item.targets)
        except (ValueError, GameRuleError):
            self._counter_stack_item(
                item.ref,
                reason="target schema invalid at resolution",
                as_rule=True,
                countered_by=item.controller,
            )
            self._grant_priority(self.state.active_player)
            return False
        updated: list[Any] = []
        valid_count = 0
        selected_count = 0
        current_groups: dict[str, list[Any]] = {}
        for group in plan.groups:
            legal = set(candidates[group.group_id])
            current: list[Any] = []
            for raw_ref in grouped.get(group.group_id, []):
                selected_count += 1
                ref = str(raw_ref)
                original_snapshot = dict(
                    item.context.get("target_snapshots", {}).get(
                        ref, {}
                    )
                )
                identity_matches = (
                    self._target_identity_matches_snapshot(
                        ref,
                        original_snapshot,
                    )
                )
                if ref in legal and identity_matches:
                    current.append(ref)
                    updated.append(ref)
                    valid_count += 1
                    continue
                current.append(None)
                updated.append(None)
                self._increment_optimization(
                    item.controller,
                    "targets_became_illegal_on_resolution",
                )
                self._log(
                    item.controller,
                    "target.illegal",
                    f"{ref} is no longer a legal target for {item.ref}.",
                    {
                        "stack": item.ref,
                        "target": ref,
                        "group": group.group_id,
                        "reason": (
                            "object_identity_changed"
                            if ref in legal and not identity_matches
                            else "candidate_no_longer_matches"
                        ),
                    },
                    importance=2,
                )
            current_groups[group.group_id] = current
        item.targets = updated
        item.context["target_groups_current"] = current_groups
        item.context["targets_revalidated"] = True
        if selected_count and valid_count == 0:
            if item.context.get("cost_option") == "bestow":
                self._log(
                    item.controller,
                    "bestow.target.illegal",
                    (
                        f"{item.ref} lost its bestow target and will "
                        "resolve as a creature."
                    ),
                    {"stack": item.ref},
                    importance=2,
                )
                return True
            self._counter_stack_item(
                item.ref,
                reason="all targets illegal on resolution",
                as_rule=True,
                countered_by=item.controller,
            )
            self._grant_priority(self.state.active_player)
            return False
        return True
