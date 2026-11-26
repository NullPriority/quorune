from __future__ import annotations

from ..target_source import TargetingSource, stack_targeting_source
from ..characteristic_read_frames import characteristic_read_method
from ..continuous_effect_state import fixed_target_color_effects

import copy
from typing import Any, Mapping, Protocol, Sequence

from ..announced_damage import announced_allocation_details
from ..entry_control_exchange_model import ENTRY_EXCHANGE_TARGET_FIELD
from ..errors import GameRuleError
from ..model import CardInstance, StackItem
from ..object_query import exact_numeric_characteristic
from ..object_predicate import (
    ObjectQueryError,
    permanent_state_predicate_matches,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.modal_selection import canonical_modes
from ..rules.private_opponent_top_exile import has_private_top_exile_designation
from ..zone_object_state import face_down_exile_viewers
from ..semantics import SemanticProgram
from ..target_characteristics import TargetCharacteristicSnapshot
from ..target_history import (
    target_damage_history_matches, target_graveyard_battlefield_arrival_matches, target_player_history_matches,
)
from ..target_predicates import TargetPredicateError, materialize_greatest_power_target_rows, target_predicate_matches
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
from .target_validation import (
    CopyTargetValidation, graveyard_target_mana_value, target_choice_requirement, shared_permanent_type_pair_matches, shared_type_pair_candidates,
    shared_type_pair_resolution_rejections, single_choice_required_target_candidates,
    target_assignment_meets_requirement, target_mana_value_matches_snapshot, validate_grouped_target_assignment,
)


TARGET_OPERATION_ID = "selection.target.semantic.v1"


def _target_characteristic_row_values(
    data: Mapping[str, Any],
    *,
    card: CardInstance | None,
) -> dict[str, Any]:
    """Project target characteristics through the exact numeric owner."""

    values = TargetCharacteristicSnapshot.from_effective_data(data).row_values()
    if card is not None:
        values["power"] = exact_numeric_characteristic(card, data, "power")
        values["toughness"] = exact_numeric_characteristic(
            card,
            data,
            "toughness",
        )
    return values


class TargetSelectionHost(Protocol):
    """Narrow authoritative facts/callbacks consumed by target selection."""

    state: Any
    active_seats: Sequence[str]
    semantics: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...
    def _type_parts(self, type_line: str) -> tuple[set[str], set[str], set[str]]: ...
    def _increment_optimization(self, seat: str, key: str, amount: int = 1) -> None: ...

    def _attachment_public_state(self, card: CardInstance, *,
        _enforce_static_component_applicability: bool = True) -> tuple[bool, bool, bool]: ...


def _target_group_public_state_matches(
    group: TargetGroup,
    *,
    card: CardInstance | None,
    turn_sequence: int,
    attachment_state: Mapping[str, bool] | None = None,
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
            attacking=card.attacking is not None,
            **dict(attachment_state or {}),
        )
    except ObjectQueryError as exc:
        raise GameRuleError(str(exc)) from exc


def _target_group_attachment_state(host, group, card):
    predicate = group.state_predicate
    if (predicate is None or not isinstance(card, CardInstance)
            or all(getattr(predicate, key) is None for key in ('enchanted', 'equipped', 'modified'))):
        return {}
    enchanted, equipped, modified = host._attachment_public_state(card)
    return {'enchanted': enchanted, 'equipped': equipped, 'modified': modified}


def _current_public_target_state_matches(host, group, card, actor):
    from ..rules.aura_attachment_targets import aura_attachment_target_matches

    if group.aura_attachment is not None and not aura_attachment_target_matches(
        host, group.aura_attachment, card, actor=actor,
    ):
        return False
    return _target_group_public_state_matches(
        group, card=card, turn_sequence=host.state.turn_sequence,
        attachment_state=_target_group_attachment_state(host, group, card),
    )


def _current_combat_target_matches(host, group, card, controller, source_ref):
    return _target_group_combat_state_matches(group,card=card,actor=controller,
        source_ref=source_ref,cards=host.state.cards,combat=host.state.combat)


def _target_group_combat_state_matches(
    group: TargetGroup,
    *,
    card: CardInstance | None,
    actor: str,
    source_ref: str | None,
    cards: Mapping[str, CardInstance],
    combat: Any = None,
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
    if group.combat_state == "attacking_or_tapped":
        return attacking or bool(card and card.tapped)
    if group.combat_state == "unblocked_attacking":
        from ..combat_relationship_state import is_unblocked_attacker_after_declaration

        return is_unblocked_attacker_after_declaration(combat, card)
    if group.combat_state == "blocking":
        return blocking
    if group.combat_state == "attacking_or_blocking":
        return attacking or blocking
    if group.combat_state == "attacking_actor":
        return bool(card and card.attacking == actor)
    if group.combat_state in {"blocking_source", "blocked_by_source"}:
        source = next(
            (
                candidate
                for candidate in cards.values()
                if candidate.ref == source_ref
                and candidate.zone == "battlefield"
                and not candidate.phased_out
            ),
            None,
        )
        if group.combat_state == "blocking_source":
            return bool(source and card and (card.object_id in combat.blockers.get(source.object_id,())
                        if combat is not None else card.blocking == source.object_id))
        return bool(source and card and (source.object_id in combat.blockers.get(card.object_id,())
                    if combat is not None else source.blocking == card.object_id))
    return True


def _target_group_object_facts_match(
    group: TargetGroup,
    *,
    card: CardInstance | None,
    history: Any,
    turn_sequence: int,
    actor: str,
    player: str | None,
    source_incarnation: str | None,
) -> bool:
    """Evaluate typed history and object facts outside the main target matcher."""

    history_matches = group.damage_history is None or bool(
        card is not None
        and target_damage_history_matches(
            group.damage_history,
            history=history,
            turn_sequence=turn_sequence,
            actor=actor,
            object_incarnation=card.logical_object_id,
        )
    )
    if group.player_history is not None:
        history_matches = player is not None and target_player_history_matches(
            group.player_history, history=history, turn_sequence=turn_sequence,
            player=player, source_incarnation=source_incarnation,
        )
    return bool(
        history_matches
        and (not group.graveyard_from_battlefield_this_turn or target_graveyard_battlefield_arrival_matches(
            history=history, turn_sequence=turn_sequence, actor=actor, card=card,
        ))
        and (
            group.commander is None
            or bool(card and card.is_commander) == group.commander
        )
        and (
            group.token is None
            or bool(card and card.is_token) == group.token
        )
        and (
            group.face_down is None
            or bool(card and card.face_down) == group.face_down
        )
    )


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
        acting_source: TargetingSource | None = None,
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
                    acting_source=acting_source,
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

    @characteristic_read_method
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
                ability_source_types = None
                ability_source_colors = None
                from ..ability_source_information import ABILITY_SOURCE_FILTERS, complete_ability_source_information
                if group.predicate in ABILITY_SOURCE_FILTERS and item.kind in {'activated_ability','triggered_ability'}:
                    if complete_ability_source_information(self.state):
                        source_facts = stack_targeting_source(self,item)
                        ability_source_types = source_facts.card_types
                        ability_source_colors = source_facts.colors
                characteristics = _target_characteristic_row_values(data, card=card)
                if item.kind in {"spell", "spell_copy"}:
                    from .exile_cast import mana_value_of_cost
                    characteristics["mana_value"] = mana_value_of_cost(
                        str(data.get("mana_cost") or ""), x_value=int(item.x_value or 0))
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
                        **characteristics,
                        "card": card,
                        "stack_item": item,
                        "stack_source_types": stack_source_types,
                        **({'ability_source_types':ability_source_types} if group.predicate in ABILITY_SOURCE_FILTERS else {}),
                        **({'ability_source_colors':ability_source_colors} if group.predicate == 'colorless_ability_source' else {}),
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
                    if zone == "battlefield" and card.phased_out:
                        continue
                    known_viewers = card.known_to
                    if zone == "exile" and card.face_down and has_private_top_exile_designation(card):
                        known_viewers = face_down_exile_viewers(self.state, card)
                    if card.face_down and controller not in known_viewers:
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
                        if zone == "battlefield" and fixed_target_color_effects(self.state, card):
                            data["colors"] = self._effective_card_data(card).get("colors", [])
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
                            **_target_characteristic_row_values(data, card=card),
                            "card": card,
                        }
                    )
        try:
            return materialize_greatest_power_target_rows(self, controller, group, rows)
        except TargetPredicateError as exc:
            raise GameRuleError(str(exc)) from exc

    def _target_row_player_relations_match(self, controller, group, row, acting_source):
        if not self._relation_matches(str(row.get("controller")), controller, group.controller_relation):
            return False
        if group.controller_seat is not None and str(row.get("controller")) != group.controller_seat:
            return False
        if group.owner_seat is not None and row.get("owner") != group.owner_seat:
            return False
        if group.owner_relation == "spell_owner":
            from ..rules.spell_owner_targets import target_has_spell_owner
            if not target_has_spell_owner(acting_source, row.get("owner"), row.get("category")):
                return False
        elif not self._relation_matches(str(row.get("owner")), controller, group.owner_relation):
            return False
        return row["category"] != "player" or self._relation_matches(str(row["ref"]), controller, group.player_relation)

    def _target_row_matches(
        self,
        controller: str,
        group: TargetGroup,
        row: Mapping[str, Any],
        *,
        source_ref: str | None,
        acting_source: TargetingSource | None = None,
        prohibitions=None,
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
            acting_source=acting_source, prohibitions=prohibitions,
        ) is not TargetProtectionVerdict.ALLOWED:
            return False
        if group.categories and str(row["category"]) not in {
            value.casefold() for value in group.categories
        }:
            return False
        if not self._target_row_player_relations_match(controller, group, row, acting_source):
            return False
        characteristics = TargetCharacteristicSnapshot.from_row(row)
        if group.entry_exchange_target is not None and not group.entry_exchange_target.matches(
            controller=row.get("controller"), types=characteristics.types,
        ):
            return False
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
        if not _current_combat_target_matches(self,group,card,controller,source_ref):
            return False
        if group.tapped is not None and (
            bool(card and card.tapped) != group.tapped
        ):
            return False
        if not _current_public_target_state_matches(
            self, group, card if isinstance(card, CardInstance) else None, controller,
        ):
            return False
        if not _target_group_object_facts_match(
            group,
            card=card if isinstance(card, CardInstance) else None,
            history=self.state.turn_history,
            turn_sequence=self.state.turn_sequence,
            actor=controller,
            player=str(row["ref"]) if row["category"] == "player" else None,
            source_incarnation=getattr(acting_source, "logical_object_id", None),
        ):
            return False
        derived = {
            name: name in types
            for name in ("land", "creature", "artifact", "enchantment")
        }
        derived["permanent"] = row["category"] == "permanent"
        try:
            if not target_predicate_matches(
                self, group, row,
                types=types,
                supertypes=supertypes,
                colors=colors,
                derived=derived,
                source_ref=source_ref, controller=controller,
                source_incarnation=getattr(acting_source, "logical_object_id", None),
                source_chosen_creature_type=getattr(acting_source, "chosen_creature_type", None),
                source_chosen_color=getattr(acting_source, "chosen_color", None),
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
        acting_source: TargetingSource | None = None,
        record_optimization: bool = True,
    ) -> list[str]:
        from ..target_restrictions import current_target_prohibitions
        prohibitions=current_target_prohibitions(self)
        values = [
            str(row["ref"])
            for row in self._target_candidate_rows(controller, group)
            if self._target_row_matches(
                controller,
                group,
                row,
                source_ref=source_ref,
                acting_source=acting_source, prohibitions=prohibitions,
            )
        ]
        values = unique_preserving_order(values)
        if record_optimization:
            self._optimization_stats(controller)["target_candidates_generated"] += len(values)
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
        acting_source: TargetingSource | None = None,
        record_optimization: bool = True,
    ) -> dict[str, list[str]]:
        return {
            group.group_id: self._target_candidates(
                controller,
                group,
                source_ref=source_ref,
                acting_source=acting_source,
                record_optimization=record_optimization,
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
                pair = plan.shared_permanent_type_pair
                return pair is None or shared_permanent_type_pair_matches(
                    self, pair, selected[pair.left][0], selected[pair.right][0],
                )
            group = slots[index]
            for ref in candidates.get(group.group_id, ()):
                own = selected.setdefault(group.group_id, [])
                if group.distinct and not group.allow_reuse and ref in own:
                    continue
                if group.distinct_controllers and any(self._target_snapshot(ref).get('controller')==self._target_snapshot(other).get('controller') for other in own):
                    continue
                if group.distinct_mana_values:
                    value = graveyard_target_mana_value(self._target_snapshot(ref))
                    if value is None or any(
                        value == graveyard_target_mana_value(self._target_snapshot(other)) for other in own
                    ):
                        continue
                if plan.globally_distinct and ref in globally_used:
                    continue
                if any(
                    ref in selected.get(other, ())
                    for other in group.different_from_groups
                ):
                    continue
                if group.same_owner:
                    owner = self._target_snapshot(ref).get("owner")
                    if type(owner) is not str or not owner:
                        continue
                    if own and any(
                        self._target_snapshot(selected_ref).get("owner")
                        != owner
                        for selected_ref in own
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

    def _public_target_group(self, group: TargetGroup, refs: Sequence[str]) -> dict[str, Any]:
        public = group.public_dict(refs)
        if group.distinct_mana_values:
            values = {ref: graveyard_target_mana_value(self._target_snapshot(ref)) for ref in refs}
            known = {ref: value for ref, value in values.items() if value is not None}
            public.update(legal_refs=list(known), max=min(group.max_targets, len(set(known.values()))))
        return public

    def _public_target_schema(
        self,
        controller: str,
        schema: Mapping[str, Any],
        *,
        source_ref: str | None,
        acting_source: TargetingSource | None = None,
        announcing_targets: bool = False,
    ) -> dict[str, Any] | None:
        from ..target_cardinality import bind_any_number_target_schema
        schema = bind_any_number_target_schema(schema,
            len(self.state.cards)+len(self.state.players)+len(self.state.stack))
        flagbearers = frozenset()
        if announcing_targets:
            from ..semantic_runtime.target_requirements import current_flagbearer_target_refs

            flagbearers = current_flagbearer_target_refs(self, controller)
        modes = available_modes(schema)
        if modes:
            maximum_modes = int(schema.get("max_modes", schema.get("mode_count", 1)))
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
                    acting_source=acting_source,
                )
                if not self._target_plan_feasible(plan, candidates):
                    continue
                if maximum_modes == 1:
                    candidates = single_choice_required_target_candidates(plan, candidates, flagbearers)
                    requirement = target_choice_requirement(self, plan, candidates, flagbearers)
                else:
                    requirement = None
                legal_modes.append(mode)
                mode_schemas[mode] = {
                    "groups": [
                        self._public_target_group(group, candidates[group.group_id])
                        for group in plan.groups
                    ]
                }
                if requirement is not None:
                    mode_schemas[mode]["target_choice_requirement"] = requirement
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
            combined_requirements = []
            if flagbearers and maximum_modes > 1:
                from itertools import combinations

                for size in range(max(1, minimum_modes), min(len(legal_modes), maximum_modes) + 1):
                    for selected_modes in combinations(legal_modes, size):
                        try:
                            plan = target_plan(schema, selected_modes)
                        except ValueError:
                            continue
                        rows = (group for mode in selected_modes for group in mode_schemas[mode]["groups"])
                        candidates = {group.group_id: row["legal_refs"]
                                      for group, row in zip(plan.groups, rows, strict=True)}
                        requirement = target_choice_requirement(self, plan, candidates, flagbearers)
                        if requirement is not None:
                            combined_requirements.append({"modes": list(selected_modes), **requirement})
            public_schema = {
                "mode_count": int(schema.get("mode_count", 1)),
                "min_modes": minimum_modes,
                "max_modes": maximum_modes,
                "legal_modes": legal_modes,
                "mode_schemas": mode_schemas,
                "legal_refs": legal_refs,
            }
            if combined_requirements:
                public_schema["combined_target_choice_requirements"] = combined_requirements
            return public_schema
        try:
            plan = target_plan(schema)
        except ValueError:
            return None
        candidates = self._target_candidate_map(
            controller,
            plan,
            source_ref=source_ref,
            acting_source=acting_source,
        )
        if plan.shared_permanent_type_pair is not None:
            candidates = shared_type_pair_candidates(self, plan.shared_permanent_type_pair, candidates)
        if not self._target_plan_feasible(plan, candidates):
            self._increment_optimization(
                controller, "illegal_target_actions_prevented"
            )
            self._increment_optimization(
                controller, "actions_removed_for_no_targets"
            )
            return None
        candidates = single_choice_required_target_candidates(plan, candidates, flagbearers)
        result = copy.deepcopy(dict(schema))
        result["groups"] = [
            self._public_target_group(group, candidates[group.group_id])
            for group in plan.groups
        ]
        if len(plan.groups) == 1:
            result["legal_refs"] = list(result["groups"][0]["legal_refs"])
        requirement = target_choice_requirement(self, plan, candidates, flagbearers)
        if requirement is not None:
            result["target_choice_requirement"] = requirement
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
        if not targets and all(group.min_targets == 0 for group in plan.groups):
            return grouped
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
        acting_source: TargetingSource | None = None,
        target_schema: Mapping[str, Any] | None = None,
        copy_targets: CopyTargetValidation | None = None,
        record_optimization: bool = True,
        announcing_targets: bool = False,
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
                if record_optimization:
                    self._increment_optimization(controller, "target_submissions_rejected")
                raise GameRuleError(
                    "This semantic program does not accept targets or modes"
                )
            return [], {}
        from ..target_cardinality import bind_any_number_target_schema
        schema = bind_any_number_target_schema(schema,
            len(self.state.cards)+len(self.state.players)+len(self.state.stack))
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
            if copy_targets is not None:
                plan = copy_targets.fixed_plan(plan)
            candidates = self._target_candidate_map(
                controller,
                plan,
                source_ref=source_ref,
                acting_source=acting_source,
                record_optimization=record_optimization,
            )
            grouped = self._group_target_submission(plan, targets)
            flattened = validate_grouped_target_assignment(
                self, plan, grouped, candidates, copy_targets=copy_targets,
            )
            if announcing_targets and flattened:
                from ..semantic_runtime.target_requirements import current_flagbearer_target_refs

                flagbearers = current_flagbearer_target_refs(self, controller)
                if not target_assignment_meets_requirement(self, plan, candidates, grouped, flagbearers):
                    raise GameRuleError("Choose at least one legal Flagbearer as a target")
            return flattened, grouped
        except (GameRuleError, ValueError) as exc:
            if record_optimization:
                self._increment_optimization(controller, "target_submissions_rejected")
            if isinstance(exc, GameRuleError):
                raise
            raise GameRuleError(str(exc)) from exc

    def _stack_target_schema(
        self,
        item: StackItem,
        program: SemanticProgram | None,
    ) -> Mapping[str, Any] | None:
        if "target_schema_override" in item.context:
            schema=dict(item.context["target_schema_override"])
        else:
            from ..rules.event_player_targets import bound_event_player_controller_schema
            schema=bound_event_player_controller_schema(program.target_schema,item.context) if program is not None else None
        from ..rules.entry_control_exchange import bind_entry_exchange_target_schema
        from ..rules.controlled_destruction_source_counters import bound_creature_role_schema

        schema = bound_creature_role_schema(schema, item.context, self.seats)
        schema = bind_entry_exchange_target_schema(self, item, program, schema)
        if schema is None:return None
        from ..rules.modal_selection import bind_distinct_turn_trigger_modes
        from ..target_cardinality import bind_any_number_target_schema
        schema = bind_distinct_turn_trigger_modes(self.state, item, schema)
        return bind_any_number_target_schema(schema,
            len(self.state.cards)+len(self.state.players)+len(self.state.stack))

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
        program = self.semantics.get(item.semantic_key)
        target_schema = self._stack_target_schema(item, program)
        legality_controller = seat
        if continuation_payload.get("trigger_creation") and target_schema and ENTRY_EXCHANGE_TARGET_FIELD in target_schema:
            from ..rules.entry_control_exchange import entry_exchange_target_chooser
            if seat != entry_exchange_target_chooser(target_schema):
                raise GameRuleError("The entry exchange target belongs to its entrant controller")
            legality_controller = item.controller
        if "selection" in raw_continuation:
            if selection.source_ref != self._stack_source_ref(item):
                raise GameRuleError("Semantic target source identity changed")
            current_public_schema = (
                self._public_target_schema(
                    legality_controller,
                    target_schema,
                    source_ref=(
                        self._stack_source_ref(item)
                        if continuation_payload.get("trigger_creation")
                        else item.ref
                    ),
                    acting_source=stack_targeting_source(self,item),
                )
                if target_schema is not None
                else None
            )
            if current_public_schema != continuation_payload.get(
                "public_schema"
            ):
                raise GameRuleError("Semantic target candidates changed")
        targets = self._normalize_target_submission(
            response.get("targets")
        )
        modes = [str(value) for value in response.get("modes") or []]
        validated, grouped = self._validate_semantic_targets(
            legality_controller,
            program,
            targets,
            modes=modes,
            source_ref=self._stack_source_ref(item) if continuation_payload.get('trigger_creation') else item.ref,
            acting_source=stack_targeting_source(self,item),
            target_schema=target_schema,
        )
        if target_schema is None:
            raise GameRuleError("Semantic target schema is no longer available")
        try:
            allocation = announced_allocation_details(target_schema, validated, response)
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        item.targets = validated
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
        item.context.update(allocation)
        if continuation_payload.get("trigger_creation"):
            from ..rules.modal_selection import commit_distinct_turn_trigger_mode
            from ..trigger_targeting import remove_declined_optional_trigger
            commit_distinct_turn_trigger_mode(self, item, target_schema)
            if remove_declined_optional_trigger(self, item, program):
                self._grant_priority(self.state.active_player)
                return
            from ..rules.target_announcements import dispatch_target_announcements
            dispatch_target_announcements(self, item)
            from ..rules.crime import record_crime_for_stack_placement
            record_crime_for_stack_placement(self,item)
            item.context.pop("trigger_target_selection_pending", None)
            item.context["targets_chosen_at_creation"] = True
            self._grant_priority(self.state.active_player)
        else:
            self._prepare_stack_resolution()

    def _invalidate_changed_target_pair(self, item, plan, grouped, flattened):
        rejected = shared_type_pair_resolution_rejections(self, plan.shared_permanent_type_pair, grouped)
        for group_id, ref in rejected:
            grouped[group_id] = [None]
            flattened[flattened.index(ref)] = None
            self._increment_optimization(item.controller, "targets_became_illegal_on_resolution")
            self._log(
                item.controller, "target.illegal", f"{ref} no longer shares the required type for {item.ref}.",
                {"stack": item.ref, "target": ref, "group": group_id, "reason": "shared_permanent_type_changed"},
                importance=2,
            )
        return len(rejected)

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
                source_ref=self._stack_source_ref(item),
                acting_source=stack_targeting_source(self,item),
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
                from .target_validation import target_controller_matches_snapshot
                controller_matches=target_controller_matches_snapshot(self,group,ref,original_snapshot)
                mana_value_matches = target_mana_value_matches_snapshot(self, group, ref, original_snapshot)
                if ref in legal and identity_matches and controller_matches and mana_value_matches:
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
        valid_count -= self._invalidate_changed_target_pair(item, plan, current_groups, updated)
        item.targets = updated
        item.context["target_groups_current"] = current_groups
        item.context["targets_revalidated"] = True
        if selected_count and valid_count == 0:
            from ..rules.mutate_resolution import mutating_spell_spec
            if mutating_spell_spec(item) is not None:
                self._log(item.controller,'mutate.target.illegal',
                    f'{item.ref} lost its Mutate target and will resolve as a creature.',
                    {'stack':item.ref},importance=2)
                return True
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
