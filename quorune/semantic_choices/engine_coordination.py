from __future__ import annotations

from .token_creation_queries import (
    attached_token_candidate_refs,
    semantic_choice_token_creation_preview,
    token_creation_choice_facts,
)

from ..target_source import stack_copy_targeting_source, stack_targeting_source
from ..ring_state import materialized_ring_choice_plans

from dataclasses import replace
from typing import Any, Mapping, Sequence

from ..errors import GameRuleError
from ..rules.hand_inspection import FIXED_HAND_INSPECTION_OPERATION
from .optional_effect import optional_choice_uses_draw_permission
from ..rules.target_library_inspection import scoped_target_library_inspection_libraries
from ..rules.opponent_library_shuffle import (
    admitted_opponent_shuffle_scope, scoped_opponent_shuffle_library,
)
from ..counter_state import player_counter_snapshot
from ..rules.name_stickers import choice_facts as name_sticker_choice_facts
from ..model import StackItem
from ..connive import connive_discard_characteristic_ids, bind_connive_instruction
from .endure import bind_endure_instruction
from .mana_contribution_draw import bind_mana_contribution_draw
from .mana_contribution_steps import complete_contribution_mana_choice
from ..object_query import object_query_result
from ..private_opponent_library_cast_model import (
    has_private_opponent_library_cast_access, has_private_opponent_library_cast_designation,
    uses_private_opponent_library_cast_selection,
)
from ..turn_history import drawn_hand_incarnations_this_turn
from ..replacement.immutable import FrozenMap, thaw_value
from ..semantic_runtime.draw_restrictions import current_draw_permission
from ..semantic_runtime.context import semantic_source_context
from ..semantic_runtime.hand_visibility import hand_card_is_known_to, public_hand_owners
from ..rules.hand_library_redraw import result_hand_snapshot as discard_result_hand_snapshot
from ..semantic_runtime import (
    IntentPlan,
    prepare_draw_resolution,
)
from ..replacement_effects import ReplacementChoiceRequired
from ..selection.copy_targets import (
    copy_target_public_schema, materialize_optional_stack_target_change,
    materialize_single_stack_target_change, prepare_copy_targets,
)
from ..stack_target_change_model import is_mandatory_single_stack_target_change, is_optional_stack_target_change
from ..targets import TargetGroup, available_modes, target_plan
from ..util import unique_preserving_order
from ..util import stable_json
from ..resolution_conditions import (
    RESOLUTION_CONDITION_OPERATION, ResolutionConditionBinding,
    validate_resolution_condition_instruction,
)
from ..cast_origin_replacement import CAST_REPLACEMENT_VERSIONS
from ..paid_sacrifice_conditions import PAID_SACRIFICE_CONDITION_VERSION
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

from .targeted_exile_land_entry import exile_land_entry_life_facts


class SemanticChoiceCoordinationMixin:
    def _semantic_choice_object_rows(
        self,
        actor: str,
        effect: Mapping[str, Any] | None = None,
        *,
        stack_item: StackItem | None = None,
    ) -> tuple[ChoiceObjectView, ...]:
        public_zones = {
            "battlefield", "graveyard", "exile", "command", "stack"
        }
        choice_effect = effect or {}
        from ..carddb_characteristics import public_object_rules_names
        from ..rules.graveyard_return_choices import uses_different_name_graveyard_return

        return_names = uses_different_name_graveyard_return(choice_effect)
        from ..rules.owned_commander_amount import OWNED_COMMANDER_AMOUNT_FIELD
        if OWNED_COMMANDER_AMOUNT_FIELD in choice_effect:
            from ..commander import current_commander_objects
            from .owned_commander_amount import materialize_owned_commander_amount_rows

            return materialize_owned_commander_amount_rows(
                current_commander_objects(self.state, actor), self._effective_card_data, actor, choice_effect,
            )
        from .revealed_creature_statistics import revealed_creature_top_id, revealed_top_effective_data
        revealed_top_id = revealed_creature_top_id(self, actor, choice_effect)
        from ..rules.target_library_inspection import scoped_target_library_inspection_refs
        from ..sacrifice_prohibition_model import sacrifice_is_prohibited
        inspected_library_refs = set(scoped_target_library_inspection_refs(self, actor, choice_effect))
        for refs in scoped_opponent_shuffle_library(self, stack_item, actor, choice_effect).values():
            inspected_library_refs.update(refs)

        from .public_library_piles import scoped_searched_pile_refs

        inspected_library_refs.update(scoped_searched_pile_refs(self, stack_item, actor, choice_effect))
        from ..discover_casting import resolving_exile_library_refs
        scan_refs = {ref for refs in resolving_exile_library_refs(self, actor=actor, effect=choice_effect).values()
                     for ref in refs}
        inspected_hand_owner = (
            str(choice_effect.get("target") or "")
            if choice_effect.get("op") == FIXED_HAND_INSPECTION_OPERATION
            else ""
        )
        if inspected_hand_owner not in self.active_seats:
            from ..rules.bounded_hand_cast import damaged_player_hand_inspection_owner

            inspected_hand_owner = damaged_player_hand_inspection_owner(
                self, item=stack_item, actor=actor, effect=choice_effect,
            )
        from ..rules.private_opponent_top_exile import has_private_top_exile_designation, private_top_exile_viewer

        rows: list[ChoiceObjectView] = []
        public_hands = public_hand_owners(self)
        for card in self.state.cards.values():
            known_hand = card.owner in self.active_seats and hand_card_is_known_to(self, card, actor, public_owners=public_hands)
            inspected_hidden_card = (
                card.zone == "hand" and card.owner == inspected_hand_owner
            )
            if (
                card.zone not in public_zones
                and card.owner != actor
                and not inspected_hidden_card
                and not known_hand
                and not (card.zone == 'library' and card.ref in scan_refs)
                and not (card.zone == 'library' and card.ref in inspected_library_refs)
            ):
                continue
            private_exile_mask = (
                card.face_down and has_private_opponent_library_cast_designation(card)
                and not has_private_opponent_library_cast_access(self, actor=actor, card=card)
            ) or (card.face_down and has_private_top_exile_designation(card)
                  and actor != private_top_exile_viewer(self.state, card))
            if private_exile_mask:
                effective = {}
            elif card.object_id == revealed_top_id:
                effective = revealed_top_effective_data(self, card)
            else:
                effective = self._effective_card_data(card)
            types, subtypes, supertypes = self._type_parts(
                str(effective.get("type_line") or "")
            )
            row = object_query_result(
                card,
                effective,
                type_parts=(types, subtypes, supertypes),
                sacrifice_prohibited=sacrifice_is_prohibited(self.state, card),
                public_rules_names=(public_object_rules_names(card, effective, self.card_record(card))
                    if return_names and card.zone == "graveyard" and card.owner == actor else ()),
                known_to_actor=not private_exile_mask and (
                    actor in card.known_to
                    or card.zone in public_zones
                    or inspected_hidden_card
                    or card.zone == 'library' and card.ref in inspected_library_refs
                    or known_hand
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
            if private_exile_mask:
                row = replace(row, printed_name="?")
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

    def _semantic_choice_stack_rows(self,effect=None) -> tuple[ChoiceStackView, ...]:
        from ..rules.announced_ability_copies import active_copy_scope, copy_prohibited
        from ..rules.event_spell_copy import capture_event_spell_copy_source, resolve_event_spell_copy_source
        from ..rules.targeted_discard_spell_copy import RESOLVING_COPY_FIELD, is_closed_targeted_discard_copy_program
        from ..rules.targeted_return_spell_copy import RETURN_COPY_VERSION, is_closed_targeted_return_copy_program

        source=resolve_event_spell_copy_source(self,effect or {})
        if source is None and RESOLVING_COPY_FIELD in (effect or {}):
            item = next((value for value in self.state.stack if value.ref == effect.get("stack")), None)
            program = self.semantics.get(item.semantic_key) if item is not None else None
            origin = (is_closed_targeted_return_copy_program if effect[RESOLVING_COPY_FIELD] == RETURN_COPY_VERSION
                      else is_closed_targeted_discard_copy_program)
            if (item is None or item.context.get("currently_resolving") is not True or program is None
                    or not origin(program)):
                raise SemanticChoiceError("The optional copy requires the current trusted resolving spell")
            source = capture_event_spell_copy_source(self, item)
        referenced=source.item() if source is not None else None
        items=tuple(i for i in self.state.stack if referenced is None or i.ref!=referenced.ref)+((referenced,) if referenced is not None else ())
        return tuple(
            ChoiceStackView(
                ref=item.ref,
                controller=item.controller,
                label=item.label,
                semantic_key=item.semantic_key,
                targets=tuple(item.targets),
                modes=tuple(item.modes),
                target_groups=dict(item.context.get("target_groups") or {}),
                target_snapshots=dict(item.context.get("target_snapshots") or {}),
                copy_source=source.to_dict() if source is not None and item.ref==referenced.ref else None,
                upkeep_player=item.context.get("player") if (item.context.get("event"), item.context.get("step")) == ("step.begin", "upkeep") else None,
                library_shuffle=admitted_opponent_shuffle_scope(self, item),
                kind=item.kind,
                copy_prohibited=protected,
                announced_copy_scope=active_copy_scope(self, item, protected=protected),
                resolving_graveyard_original=(
                    item is not referenced and item.kind == "spell"
                    and item.context.get("currently_resolving") is True
                    and item.context.get("cast_origin") == "graveyard"
                ),
            )
            for item in items
            for protected in (copy_prohibited(self, item),)
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
        *,
        stack_item: StackItem | None = None,
    ) -> tuple[str, ...]:
        from ..rules.attachment_actions import ATTACH_CREATED_TOKEN, CHOOSE_ENTRY_AURA_RECIPIENT, OFFER_ENTRY_AURA_ATTACHMENT
        from ..untargeted_untap_model import is_scheduled_land_untap
        from ..rules.linked_loyalty_exile_model import LINKED_LOYALTY_EXILE_FIELD
        from ..library_battlefield_model import RANDOM_REVEALED_CREATURE_ENTRY_VERSION

        if (effect.get("op") == "fixed_library_selection"
                and effect.get("schema_version") == RANDOM_REVEALED_CREATURE_ENTRY_VERSION):
            from ..rules.library_battlefield_entry import random_revealed_creature_refs

            try:
                return random_revealed_creature_refs(self, actor=actor, stack_item=stack_item, effect=effect)
            except (ValueError, TypeError, KeyError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
        from ..meld_model import uses_meld_pair

        if effect.get("op") == "choose_objects" and uses_meld_pair(effect):
            from ..meld_resolution import meld_partner_candidate_refs

            return meld_partner_candidate_refs(self, stack_item, actor, effect)
        from ..rules.linked_creature_settlement import FIELD as LINKED_CREATURE_SETTLEMENT
        from ..rules.linked_death_upkeep import FIELD as LINKED_DEATH_UPKEEP

        if effect.get("op") == "choose_objects" and any(
            field in effect for field in (LINKED_LOYALTY_EXILE_FIELD, LINKED_CREATURE_SETTLEMENT, LINKED_DEATH_UPKEEP)
        ):
            from ..effect_runtime.linked_loyalty_exile import linked_loyalty_choice_refs

            return linked_loyalty_choice_refs(self, stack_item, actor, effect)
        if is_scheduled_land_untap(effect):
            from ..rules.untargeted_untap_shapes import scheduled_land_untap_candidates

            return scheduled_land_untap_candidates(self, actor)
        from ..rules.linked_clue_copy import has_linked_clue_copy_version
        from ..rules.temporary_copy_subject import TEMPORARY_COPY_SUBJECT_OPERATION
        if effect.get("op") == TEMPORARY_COPY_SUBJECT_OPERATION and has_linked_clue_copy_version(effect):
            from .linked_clue_copy import linked_clue_copy_candidates

            return linked_clue_copy_candidates(self, stack_item, actor, effect)
        from ..rules.graveyard_return_choices import (
            OPERATION, graveyard_return_candidates, is_each_graveyard_return, is_paired_graveyard_return,
        )
        if is_paired_graveyard_return(effect) or is_each_graveyard_return(effect):
            return tuple(sorted(card.ref for card in self.state.cards.values()
                                if card.zone == "graveyard" and card.is_card_object))
        from ..rules.milled_creature_land_to_hand import MILL_HAND_CHOICE, mill_hand_choice_candidates

        if effect.get("op") == "choose_option" and MILL_HAND_CHOICE in effect:
            try:
                return tuple(row.ref for row in mill_hand_choice_candidates(effect, actor=actor))
            except (TypeError, ValueError, KeyError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
        from .committed_mill_choices import committed_mill_choice_refs

        if (milled := committed_mill_choice_refs(effect, actor=actor)) is not None:
            return milled
        from ..rules.targeted_graveyard_exile import targeted_graveyard_exile_candidates
        if effect.get('op') == 'choose_target_player_graveyard_exile':
            try:return targeted_graveyard_exile_candidates(self, actor, effect)
            except (ValueError, TypeError, KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
        if effect.get("op") == ATTACH_CREATED_TOKEN:
            return attached_token_candidate_refs(self, effect)
        from ..rules.death_battlefield_return import ATTACH_RETURNED_WITH_EQUIPMENT

        if effect.get("op") == ATTACH_RETURNED_WITH_EQUIPMENT:
            return attached_token_candidate_refs(self, effect, token_result=False)
        if effect.get("op") in {OFFER_ENTRY_AURA_ATTACHMENT, CHOOSE_ENTRY_AURA_RECIPIENT}:
            return attached_token_candidate_refs(self, effect, attachment_kind="aura")
        if effect.get('op') == OPERATION:
            try:return graveyard_return_candidates(self, actor, effect, stack_item=stack_item)
            except (ValueError, TypeError, KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
        if effect.get("op") == "offer_soulbond_pair":
            from ..soulbond_model import SoulbondOpportunity
            from ..soulbond_state import opportunity_candidates
            return opportunity_candidates(self, SoulbondOpportunity.from_effect(effect))
        from ..rules.void_counter_play import CHOICE_FIELD, eligible_exile_card_refs
        if CHOICE_FIELD in effect:
            return eligible_exile_card_refs(self, actor)
        from ..rules.controlled_return import CONTROLLED_RETURN_FIELD
        from ..rules.milled_permanent_recovery import CHOICE_FIELD, milled_permanent_choice_refs

        if CHOICE_FIELD in effect:
            try:
                return milled_permanent_choice_refs(self, stack_item, actor, effect)
            except (KeyError, TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
        if str(effect.get("op") or "") != "choose_objects" or CONTROLLED_RETURN_FIELD in effect:
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
        *,
        stack_item: StackItem | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        target_schemas: dict[str, Any] = {}
        validated_targets: dict[str, Any] = {}
        operation = str(effect.get("op") or "")
        if operation not in {"copy_stack_item", "retarget_stack_item"}:
            return target_schemas, validated_targets
        from ..rules.cast_creature_set_spell_copy import CAST_CREATURE_SET_COPY_FIELD, materialize_cast_creature_set_copy
        from ..rules.cast_source_spell_copy import SOURCE_CAST_COPY_FIELD, materialize_source_cast_copy
        if CAST_CREATURE_SET_COPY_FIELD in effect:
            return materialize_cast_creature_set_copy(self, effect, actor=actor, trigger=stack_item)
        if SOURCE_CAST_COPY_FIELD in effect:
            return materialize_source_cast_copy(self, effect, actor=actor, trigger=stack_item)
        from ..rules.source_directed_redirect_model import is_source_directed_redirect
        if is_source_directed_redirect(effect):
            from ..selection.source_directed_redirect import materialize_source_directed_redirect
            return materialize_source_directed_redirect(self, effect, response, actor=actor, stack_item=stack_item)
        if is_mandatory_single_stack_target_change(effect):
            return materialize_single_stack_target_change(self, effect, response)
        if is_optional_stack_target_change(effect):
            return materialize_optional_stack_target_change(self, effect, response)
        target_ref = str(
            effect.get("_target_stack_ref") or effect.get("stack") or ""
        )
        from ..rules.event_spell_copy import resolve_event_spell_copy_source
        bound_source=resolve_event_spell_copy_source(self,effect)
        target_item = next(
            (item for item in self.state.stack if item.ref == target_ref),
            None,
        )
        if bound_source is not None:target_item=bound_source.item()
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
        if bound_source is not None:raw_schema=thaw_value(bound_source.target_schema) if bound_source.target_schema is not None else None
        if not isinstance(raw_schema, Mapping):
            raw_schema = self._stack_target_schema(
                target_item,
                self.semantics.get(target_item.semantic_key),
            )
        if not isinstance(raw_schema, Mapping) or not target_item.targets:
            return target_schemas, validated_targets
        public_schema: Mapping[str, Any] | None = None
        from ..target_source import TARGETING_SOURCE_CONTEXT, TargetingSource
        bound_spell = bound_source is not None and bound_source.characteristics is not None
        copy_source_ref = (None if bound_spell else
            target_item.context.get(TARGETING_SOURCE_CONTEXT, {}).get('object_ref') if bound_source is not None
            else self._stack_source_ref(target_item))
        if operation == "copy_stack_item":
            acting_copy_source = (
                TargetingSource.from_characteristics('spell', bound_source.characteristics, controller=validation_actor)
                if bound_spell else stack_copy_targeting_source(self, target_item, controller=validation_actor)
            )
            public_schema = copy_target_public_schema(
                self, validation_actor, raw_schema, target_item.modes,
                list(target_item.targets), dict(target_item.context.get("target_groups") or {}),
                copy_source_ref, acting_source=acting_copy_source,
                original_snapshots=target_item.context.get("target_snapshots"),
            )
        elif available_modes(raw_schema):
            plan = target_plan(
                raw_schema, target_item.modes, require_modes=True
            )
            candidates = self._target_candidate_map(
                target_item.controller,
                plan,
                acting_source=stack_targeting_source(self,target_item), source_ref=self._stack_source_ref(target_item),
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
                acting_source=stack_targeting_source(self,target_item), source_ref=self._stack_source_ref(target_item),
            )
        if public_schema is None:
            return target_schemas, validated_targets
        key = f"{validation_actor}:{target_ref}"
        target_schemas[key] = {
            "authoritative": dict(raw_schema),
            "public": dict(public_schema),
        }
        submitted = (response or {}).get("targets")
        if operation == "copy_stack_item":
            assignment = prepare_copy_targets(
                self, actor=validation_actor, schema=raw_schema,
                modes=target_item.modes, source_ref=copy_source_ref,
                original_targets=list(target_item.targets),
                original_groups=dict(target_item.context.get("target_groups") or {}),
                original_snapshots=dict(target_item.context.get("target_snapshots") or {}),
                submitted=submitted, acting_source=acting_copy_source,
            )
            validated_targets[key] = {
                "targets": list(assignment.targets), "groups": thaw_value(assignment.groups),
                "snapshots": thaw_value(assignment.snapshots),
            }
        elif submitted is not None:
            selected, grouped = self._validate_semantic_targets(
                validation_actor,
                self.semantics.get(target_item.semantic_key),
                self._normalize_target_submission(submitted),
                modes=list(target_item.modes),
                acting_source=stack_targeting_source(self,target_item), source_ref=self._stack_source_ref(target_item),
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
        *,
        effect: Mapping[str, Any] | None = None,
    ) -> dict[str, str]:
        from ..reveal_until_library_selection_model import CHOSEN_NAME_REVEAL_VERSION, REVEAL_UNTIL_OPERATION

        exact_name = (effect or {}).get('op') == REVEAL_UNTIL_OPERATION and (effect or {}).get('schema_version') == CHOSEN_NAME_REVEAL_VERSION
        canonical: dict[str, str] = {}
        submitted = {
            str(value).strip()
            for key, value in (response or {}).items()
            if key == "card_name" and str(value).strip()
        }
        for value in submitted:
            if exact_name:
                from ..entry_designations import resolve_entry_card_name

                try:
                    canonical[value.casefold()] = resolve_entry_card_name(self.card_db, value, nonland=False)
                except ValueError:
                    pass
                continue
            try:
                from ..preparation import canonical_preparation_name
                if (name:=canonical_preparation_name(self.card_db.lookup(value),value)) is not None:canonical[value.casefold()]=name
            except KeyError:
                continue
        return canonical

    def _semantic_choice_affordable_costs(
        self,
        actor: str,
        effect: Mapping[str, Any],
        object_rows: tuple[ChoiceObjectView, ...],
    ) -> frozenset[str]:
        if effect.get('op')=='extort' and effect.get('stage')=='pay':
            return frozenset(SnapshotSemanticChoiceQuery._cost_key(actor,{color:1}) for color in ('W','B') if self._cost_is_affordable(actor,{color:1}))
        if (effect.get('op') == 'offer_optional_mana_payment' and effect.get('schema_version') == 2
            and isinstance(effect.get('payment'), Mapping) and effect['payment'].get('kind') == 'hybrid_mana'):
            from ..fixed_effect_payment import FixedEffectPaymentSpec

            payment = FixedEffectPaymentSpec.from_dict(effect['payment'])
            return frozenset(SnapshotSemanticChoiceQuery._cost_key(actor, option.requirements)
                for option in payment.hybrid_mana_options if self._cost_is_affordable(actor, option.requirements))
        cost_value: Mapping[str, Any] | None = None
        if isinstance(effect.get("_requirements"), Mapping):
            cost_value = effect["_requirements"]
        elif isinstance(effect.get("cost"), Mapping):
            cost_value = effect["cost"]
        elif effect.get("op") == "ward_counter_unless_pay" and self.state.ward_mana_offer_version == 1:
            from ..trigger_participation import WardSpec

            spec = WardSpec.from_dict(effect.get("ward_spec"))
            if spec.generic_cost is not None:
                cost_value = {"GENERIC": spec.generic_cost}
        elif (effect.get('op')=='offer_optional_mana_payment'
                and isinstance(effect.get('payment'),Mapping)
                and effect['payment'].get('kind')=='mana_discard'):
            from ..fixed_effect_payment import FixedEffectPaymentSpec
            cost_value=FixedEffectPaymentSpec.from_dict(effect['payment']).requirements
        elif effect.get('op') == 'offer_optional_mana_payment' and effect.get('schema_version') == 3:
            from ..source_maintenance import SourceMaintenanceSpec
            payment = SourceMaintenanceSpec.from_effect({
                key: value for key, value in effect.items() if not key.startswith('_')
            }).payment
            if payment is not None and payment.kind == 'mana':
                cost_value = payment.requirements
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
        stack_item: StackItem | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        from ..discover_model import EXILE_CAST_CHOICE_OPERATIONS
        from ..resolving_cast_budget_model import OPERATION as BUDGETED_RESOLVING_CAST
        if effect.get('op') == BUDGETED_RESOLVING_CAST:
            from ..rules.budgeted_resolving_cast import options
            return options(self,item=stack_item,actor=actor,effect=effect)
        from ..drawing.captured_hand import CAPTURED_DRAW_CAST_FIELD
        from ..bounded_hand_cast_model import BOUNDED_HAND_CAST_FIELD
        from ..rules.ripple import uses_revealed_library_cast
        from ..rules.qualified_library_cast import OPERATION as QUALIFIED_LIBRARY_CAST, qualified_library_cast_options
        if effect.get("op") == QUALIFIED_LIBRARY_CAST:
            return qualified_library_cast_options(self, item=stack_item, actor=actor, effect=effect)
        from ..hideaway_model import HIDEAWAY_PLAY_OPERATION
        if effect.get("op") == HIDEAWAY_PLAY_OPERATION:
            from ..rules.hideaway_play import hideaway_play_options
            from .hideaway_play import hideaway_offer_instruction

            hideaway_offer_instruction(effect, actor=actor, stack_ref=stack_item.ref if stack_item else None)
            return hideaway_play_options(self, item=stack_item, actor=actor, qualified=effect.get("_qualified") is True)
        if uses_revealed_library_cast(effect):
            from ..rules.ripple_casting import ripple_cast_options
            return ripple_cast_options(self,item=stack_item,actor=actor,effect=effect)
        from ..rules.shared_type_library_model import SHARED_TYPE_LIBRARY_OPERATION
        from ..rules.shared_type_library_play_model import PLAY_STAGE
        if effect.get("op") == SHARED_TYPE_LIBRARY_OPERATION and effect.get("_shared_type_stage") == PLAY_STAGE:
            from ..rules.shared_type_library_play import shared_type_library_play_options
            return shared_type_library_play_options(self, item=stack_item, actor=actor, effect=effect)
        if BOUNDED_HAND_CAST_FIELD in effect:
            from ..rules.bounded_hand_cast import bounded_hand_cast_options
            return bounded_hand_cast_options(self, item=stack_item, actor=actor, effect=effect)
        from ..fixed_top_exile import FIXED_TOP_EXILE_OFFER_OPERATION
        from ..targeted_graveyard_cast_model import GRAVEYARD_CAST_OPERATION
        from ..rules.graveyard_copy_cast_model import GRAVEYARD_COPY_OFFER

        if CAPTURED_DRAW_CAST_FIELD in effect and "_captured" in effect:
            from ..drawing.captured_hand_casting import captured_draw_cast_options
            return captured_draw_cast_options(self, item=stack_item, actor=actor, effect=effect)

        if effect.get("op") == GRAVEYARD_COPY_OFFER:
            from .graveyard_copy_cast import graveyard_copy_cast_options
            return graveyard_copy_cast_options(self, item=stack_item, actor=actor, effect=effect)

        from ..rules.discarded_card_cast import DISCARDED_CARD_CAST, _DISCARD_FIELD, discarded_card_cast_options

        if effect.get("op") == DISCARDED_CARD_CAST and _DISCARD_FIELD in effect:
            try:
                return discarded_card_cast_options(self, item=stack_item, actor=actor, effect=effect)
            except (ValueError, TypeError) as exc:
                raise SemanticChoiceError(str(exc)) from exc

        if effect.get("op") == GRAVEYARD_CAST_OPERATION:
            from .targeted_graveyard_cast import targeted_graveyard_cast_options
            try:
                return targeted_graveyard_cast_options(self, item=stack_item, actor=actor, effect=effect)
            except (ValueError, TypeError) as exc:
                raise SemanticChoiceError(str(exc)) from exc

        if effect.get("op") == FIXED_TOP_EXILE_OFFER_OPERATION:
            from ..discover_casting import fixed_top_exile_cast_options
            return fixed_top_exile_cast_options(self, item=stack_item, actor=actor, effect=effect)
        if effect.get('op') in EXILE_CAST_CHOICE_OPERATIONS:
            from ..discover_casting import discover_cast_options
            return discover_cast_options(self,item=stack_item,actor=actor,effect=effect)
        if effect.get('op') == 'miracle_cast_choice':
            from ..drawing.miracle_casting import miracle_cast_options
            try:
                return miracle_cast_options(self,item=stack_item,actor=actor,effect=effect)
            except (ValueError, TypeError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
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

    _semantic_choice_token_creation_preview = semantic_choice_token_creation_preview

    def _semantic_choice_resolution_condition_facts(self,actor,choice_effect,stack_item):
        """Snapshot a resolution condition through its existing rules owner."""
        from ..ability_resolution_ordinal import is_ability_resolution_ordinal
        from ..other_spell_history_model import is_other_spell_draw
        from ..rules.announced_x_draw_replacement import uses_announced_x_draw_replacement
        from ..rules.entrant_power_branch import uses_entrant_power_branch
        from ..rules.controlled_artifact_token_result import uses_controlled_artifact_result_branch
        from ..rules.counter_recipient_control import uses_counter_recipient_control
        from ..rules.named_cast_win import uses_named_cast_win

        condition_facts = {}
        from ..rules.combat_point_counters import uses_point_game_loss

        from ..rules.attack_trigger_copy import uses_quest_copy

        if uses_point_game_loss(choice_effect) or uses_quest_copy(choice_effect):
            return condition_facts
        if (uses_announced_x_draw_replacement(choice_effect)
                or is_ability_resolution_ordinal(choice_effect) or is_other_spell_draw(choice_effect)
                or uses_entrant_power_branch(choice_effect) or uses_controlled_artifact_result_branch(choice_effect)
                or uses_named_cast_win(choice_effect) or uses_counter_recipient_control(choice_effect)):
            return condition_facts
        if choice_effect.get("op") == RESOLUTION_CONDITION_OPERATION and choice_effect.get('schema_version') not in {2,4,5,6,7,10,11,12,18,PAID_SACRIFICE_CONDITION_VERSION,*CAST_REPLACEMENT_VERSIONS}:
            try:
                condition, _effects = validate_resolution_condition_instruction(choice_effect)
                if choice_effect["player"] != actor:
                    raise SemanticChoiceError("Resolution condition controller binding changed")
                if choice_effect.get('schema_version') == 17:
                    matched = self.state.day_night == 'night' if condition.scope == 'night' else False
                elif choice_effect.get('schema_version') == 16:
                    from ..conditional_target_preparation import preparation_condition_matches
                    matched = preparation_condition_matches(self, actor, choice_effect)
                else:
                    from ..resolution_conditions import is_other_blue_permanent_draw

                    source_ref = None
                    if is_other_blue_permanent_draw(choice_effect):
                        source = self.state.cards.get(stack_item.source_object_id) if stack_item is not None else None
                        if source is not None and source.logical_object_id == stack_item.context.get("source_logical_object_id"):
                            source_ref = source.ref
                    binding = ResolutionConditionBinding(controller=actor, ref=source_ref)
                    matched = self._fixed_public_state_condition_holds(binding, condition, require_available=True)
                condition_facts[stable_json(condition.to_dict())] = matched
            except (ValueError, TypeError, KeyError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
        return condition_facts

    def _semantic_choice_draw_history(self, actor):
        """Capture the recorded and versioned draw facts for this actor."""
        return {
            "drawn_hand_incarnations_by_seat": {actor: drawn_hand_incarnations_this_turn(
                self.state.turn_history, turn_sequence=self.state.turn_sequence, player=actor,
            )},
            "drawn_this_turn_by_seat": {
                actor: tuple(
                    str(entry.get("object"))
                    for entry in self.state.players[actor].draw_history
                    if entry.get("turn_sequence") == self.state.turn_sequence
                )
            },
        }

    def _semantic_choice_library_search_facts(
        self, actor: str, choice_effect: Mapping[str, Any],
    ) -> tuple[dict[str, bool], dict[str, tuple[str, ...]]]:
        """Read search authority and its bounded private candidates for this choice."""
        from ..rules.library_search_rules import (
            library_search_constraint, library_search_object_ids, optional_search_permissions,
        )
        from .artifact_exchange import transmute_search_uses_canonical_scope
        from .artifact_search_exchange import scoped_exchange_search
        from ..rules.qualified_library_cast import OPERATION as QUALIFIED_LIBRARY_CAST

        search_permissions = optional_search_permissions(self, actor, choice_effect)
        search_refs = {}
        qualified_search = (choice_effect.get("op") == QUALIFIED_LIBRARY_CAST
                            and choice_effect.get("phase") in (None, "search"))
        if transmute_search_uses_canonical_scope(choice_effect) or scoped_exchange_search(choice_effect) or qualified_search:
            search_permissions[actor] = not library_search_constraint(self, actor).prohibited
            search_refs[actor] = tuple(
                self.state.cards[object_id].ref
                for object_id in library_search_object_ids(self, actor, "library")
            )
        return search_permissions, search_refs

    def _semantic_choice_opponent_cast_colors(self, actor, effect):
        from ..public_history_query_model import current_opponent_spell_colors, uses_sealed_opponent_cast_colors

        if uses_sealed_opponent_cast_colors(effect):
            colors = current_opponent_spell_colors(self.state, actor, include_departed=True)
            if colors is None:
                raise GameRuleError("Current opposing spell-color facts are unavailable")
            return {actor: tuple(sorted(colors))}
        return {
            seat: tuple(sorted({
                str(color).upper()
                for event in self.state.events
                if event.turn_sequence == self.state.turn_sequence
                and event.code == "stack.cast"
                and event.actor in self.active_seats
                and event.actor != seat
                for color in event.details.get("colors", [])
                if str(color).upper() in {"W", "U", "B", "R", "G"}
            }))
            for seat in self.seats
        }

    def _semantic_choice_query(
        self,
        actor: str,
        *,
        response: Mapping[str, Any] | None = None,
        effect: Mapping[str, Any] | None = None,
        source_ref: str | None = None,
        stack_item: StackItem | None = None,
    ) -> SnapshotSemanticChoiceQuery:
        """Materialize immutable facts under the instruction's visibility scope."""

        from ..discover_casting import resolving_exile_library_refs
        from ..dungeon_progress import DUNGEON_VENTURE_FIELD, DungeonJourney
        from ..semantic_runtime.venture_limit import current_venture_allowed
        from ..discard_effect_origin import origin_for_resolving_hand_choice

        choice_effect = effect or {}
        from .optional_effect import require_cast_reveal_choice_source

        require_cast_reveal_choice_source(self, stack_item, actor, choice_effect)
        object_rows = (
            self._semantic_choice_object_rows(actor, choice_effect, stack_item=stack_item)
            if default_semantic_choice_registry().requires_object_rows(choice_effect)
            else ()
        )
        target_schemas, validated_targets = (
            self._semantic_choice_target_facts(
                actor, choice_effect, response, stack_item=stack_item,
            )
        )
        from ..rules.attacking_card_entry import attacking_card_entry_choice_facts
        from .one_revealed_card_placement import revealed_card_entry_choice_facts
        from .exile_until_same_name import materialize_exile_offer_names
        from .chosen_name_reveal import materialize_resolving_library_names
        from .stack_counter_name_scan import materialize_name_scan_exile_names
        from ..semantic_runtime.counter_permissions import collect_counter_permissions
        from ..semantic_runtime.life_prohibitions import life_permissions_for_choice
        from ..semantic_runtime.explore import materialize_explore_source_types
        from ..rules.shared_type_library import materialize_shared_type_library_source
        search_permissions, search_refs = self._semantic_choice_library_search_facts(actor, choice_effect)
        return SnapshotSemanticChoiceQuery(
            resolution_source_is_physical_card=(
                (self.state.cards[stack_item.card_object_id].object_kind == 'card')
                if stack_item is not None and stack_item.card_object_id in self.state.cards else False),
            explore_action_version=getattr(self.state, 'explore_action_version', None),
            ward_discard_order_version=self.state.ward_discard_order_version,
            explore_source_types=materialize_explore_source_types(self, stack_item, choice_effect),
            shared_type_library_source=materialize_shared_type_library_source(self, stack_item, choice_effect),
            resolution_source=semantic_source_context(stack_item, self.state.cards) if stack_item is not None else None,
            discard_effect_origin=origin_for_resolving_hand_choice(self.state, stack_item, choice_effect),
            ring_choice_plans=materialized_ring_choice_plans(self, actor, choice_effect),
            venture_journey=((self.state.players[actor].dungeon_journey or DungeonJourney())
                             if DUNGEON_VENTURE_FIELD in choice_effect else None),
            venture_allowed=DUNGEON_VENTURE_FIELD not in choice_effect or current_venture_allowed(self, actor),
            counter_permissions=collect_counter_permissions(self),
            life_gain_permissions=life_permissions_for_choice(self, choice_effect),
            library_search_allowed_by_seat=search_permissions,
            library_search_refs_by_seat=search_refs,
            rules_profile=self.state.config.effective_profile(len(self.seats)),
            seat_order=tuple(self.seats),
            active_order=tuple(self.active_seats),
            apnap_order=tuple(self.apnap_order()),
            discard_hand_inputs=discard_result_hand_snapshot(self, actor, choice_effect),
            object_rows=object_rows,
            exile_offer_rule_names={**materialize_exile_offer_names(self, choice_effect, actor),
                                    **materialize_name_scan_exile_names(self, choice_effect, actor)},
            stack_rows=self._semantic_choice_stack_rows(choice_effect),
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
                ],
                **resolving_exile_library_refs(self, actor=actor, effect=choice_effect),
                **scoped_target_library_inspection_libraries(self, actor, choice_effect),
                **scoped_opponent_shuffle_library(self, stack_item, actor, choice_effect),
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
                    stack_item=stack_item,
                )
            ),
            canonical_names=self._semantic_choice_canonical_names(response, effect=choice_effect),
            resolving_library_name_rows=materialize_resolving_library_names(self, stack_item, actor, choice_effect),
            target_schemas=target_schemas,
            validated_targets=validated_targets,
            **self._semantic_choice_draw_history(actor),
            opponent_cast_colors_by_seat=self._semantic_choice_opponent_cast_colors(actor, choice_effect),
            draw_permissions_by_seat=(
                {
                    seat: current_draw_permission(self, seat).to_dict()
                    for seat in self.active_seats
                }
                if optional_choice_uses_draw_permission(choice_effect)
                else {}
            ),
            materialized_choice_candidates=self._semantic_choice_candidates(
                actor, choice_effect, source_ref, stack_item=stack_item,
            ),
            materialized_damage_source_candidates=(
                self._semantic_choice_damage_source_candidates(actor, object_rows)
                if object_rows else ()
            ),
            **token_creation_choice_facts(self, actor, choice_effect),
            **attacking_card_entry_choice_facts(self, actor, choice_effect),
            **revealed_card_entry_choice_facts(self, actor, choice_effect),
            **exile_land_entry_life_facts(self, actor, choice_effect, object_rows),
            current_turn_sequence=self.state.turn_sequence,
            committed_discard_ids=(
                tuple(event.object_incarnation for event in self._current_turn_history('card_discarded')
                      if event.actor == actor and event.object_incarnation)
                if choice_effect.get('op') == 'connive' else ()
            ),
            defined_discard_characteristic_ids=(
                connive_discard_characteristic_ids(self, actor, choice_effect)
                if choice_effect.get('op') == 'connive' else ()
            ),
            resolution_condition_facts=self._semantic_choice_resolution_condition_facts(actor,choice_effect,stack_item),
            name_sticker_facts=name_sticker_choice_facts(self,stack_item,choice_effect),
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
                stack_item=item,
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
        try:
            from ..rules.opponent_event_mana_payment import bind_event_player_payment_choice
            from ..rules.gravestorm import bind_gravestorm_instruction
            bind_event_player_payment_choice(item, effect)
            from ..rules.optional_source_damage import require_optional_source_damage_choice
            require_optional_source_damage_choice(self, item, effect)
            from ..rules.collective_source_damage import bind_collective_source_damage
            from ..rules.combat_damage_entry_history import bind_entry_exempt_draw_discard
            effect = bind_collective_source_damage(self, item, effect)
            effect = bind_entry_exempt_draw_discard(self, item, effect)
            effect = bind_gravestorm_instruction(self,item,effect)
            effect = bind_connive_instruction(self, item, effect)
            effect = bind_endure_instruction(self, item, effect)
            from ..drawing.miracle_casting import bind_miracle_cast_instruction
            from ..drawing.captured_hand_casting import bind_captured_draw_offer
            from ..dungeon_runtime import check_dungeon_choice_instruction
            from .life_gain_counted_results import bind_capped_life_gain_draw, bind_life_gain_life_draw
            from .entrant_x_reflexive import bind_entrant_x_payment
            effect = bind_miracle_cast_instruction(item, effect)
            effect = bind_captured_draw_offer(item, effect)
            from ..rules.departing_x_reflexive import bind_choice as bind_departure_x_choice
            effect = bind_entrant_x_payment(self, item, bind_capped_life_gain_draw(self, item, effect))
            effect = bind_departure_x_choice(self, item, effect)
            bind_life_gain_life_draw(self, item, effect)
            check_dungeon_choice_instruction(self, item, effect)
            effect, payer = bind_mana_contribution_draw(self, item, effect)
        except (ValueError, TypeError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
        handler = registry.handler_for_operation(str(effect.get("op") or ""))
        seat = payer or (item.controller if uses_private_opponent_library_cast_selection(effect)
                         else str(effect.get("player") or item.controller))
        try:
            preparation = handler.prepare(
                effect,
                self._semantic_choice_context(item, seat, effect),
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
        from ..library_search_control import CONTROL_FIELD
        from ..selection.controlled_search import is_registered_library_search, resolve_controlled_finds

        from ..rules.secret_number_choices import completion_actor_and_response

        registry = default_semantic_choice_registry()
        try:
            handler, continuation = registry.decode_continuation(
                {key: value for key, value in decision.continuation.items() if key != CONTROL_FIELD}
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
        seat, response = completion_actor_and_response(decision, item.controller)
        if complete_contribution_mana_choice(self, decision, item, continuation, response):
            return
        if response.get("action") == "concede":
            from .public_library_pile_departure import concede_during_pile_choice
            from .fame_fortune import FameFortuneChoiceHandler, concede_during_fame_fortune

            concede = (concede_during_fame_fortune if continuation.handler_id == FameFortuneChoiceHandler().handler_id
                       else concede_during_pile_choice)
            try:
                concede(self, item=item, continuation=continuation, actor=seat, response=response)
            except SemanticChoiceError as exc:
                raise GameRuleError(str(exc)) from exc
            return
        try:
            from ..rules.optional_source_damage import require_optional_source_damage_choice
            require_optional_source_damage_choice(self, item, continuation.effect, resuming_actor=seat)
            from ..rules.opponent_event_mana_payment import bind_event_player_payment_choice
            try:
                bind_event_player_payment_choice(item, continuation.effect, resuming_actor=seat)
            except (ValueError, TypeError, KeyError) as exc:
                raise GameRuleError(str(exc)) from exc
            from ..rules.collective_source_damage import bind_collective_source_damage
            from ..rules.combat_damage_entry_history import bind_entry_exempt_draw_discard
            bind_collective_source_damage(self, item, continuation.effect, resuming_actor=seat)
            bind_entry_exempt_draw_discard(self, item, continuation.effect, resuming_actor=seat)
            def query_for(answer):
                source = self.state.cards.get(item.source_object_id or item.card_object_id or "")
                return self._semantic_choice_query(
                    seat, response=answer, effect=continuation.effect, stack_item=item,
                    source_ref=source.ref if source is not None else None,
                )

            completion = handler.complete(continuation, response, query_for(response))
            if is_registered_library_search(continuation.effect):
                ref = response.get("card")
                control_result = resolve_controlled_finds(
                    self, decision, item=item, actor=seat, zones=("library",),
                    refs=(ref,) if ref else (),
                )
                if control_result == "pending":
                    return
                if control_result == "applied":
                    response = {key: value for key, value in response.items() if key != "card"}
                    completion = handler.complete(continuation, response, query_for(response))
        except SemanticChoiceError as exc:
            raise GameRuleError(str(exc)) from exc
        if continuation.effect.get('op') == 'offer_optional_effect' and completion.prepend_effects:
            from ..rules.trigger_limits import commit_optional_result_usage

            try:
                commit_optional_result_usage(self, item, seat)
            except (ValueError, TypeError) as exc:
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
