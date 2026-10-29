from __future__ import annotations
from .entry_copy import copiable_oracle_id
from .ring_effects import ring_legendary_effects
from .ring_state import current_ring_rule_views
from .characteristic_read_frames import memoized_characteristic_read

import copy
from functools import partial
from typing import Any, Mapping, Sequence

from .carddb_characteristics import separate_custom_display_text
from .card_programs.runtime import (
    collect_card_program_continuous_effects,
    fixed_public_state_condition_matches,
)
from .continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
from .ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE,
    StaticComponentScopeSpec,
    StaticComponentSpec,
    ability_fragment_to_dict,
    canonical_ability_fragments,
    declared_static_component_keys,
    static_component_keys,
)
from .characteristic_evaluation import evaluate_card_characteristics
from .continuous_effects import (
    ContinuousEffect,
    ContinuousEffectOrigin,
    ContinuousEffectRelation,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from .continuous_effect_state import active_resolution_effects
from .dynamic_characteristics import (
    apply_dynamic_characteristic_fragments,
    query_characteristic_count,
)
from .errors import GameRuleError
from .model import CardInstance
from .rules.source_speed_designations import current_source_designations
from .object_query import ObjectQueryResult, object_query_result
from .semantic_runtime.ability_fragments import fragments_from_descriptors


_STATIC_COMPONENT_PRESENCE_OPERATIONS = frozenset(
    {
        "add_ability_fragment",
        "remove_ability_fragment",
        "remove_all_abilities",
        "set_basic_land_types",
    }
)


def _static_component_presence_effects(
    effects: Sequence[ContinuousEffect],
) -> tuple[ContinuousEffect, ...]:
    changes = tuple(
        effect
        for effect in effects
        if any(
            operation.op in _STATIC_COMPONENT_PRESENCE_OPERATIONS
            for operation in effect.operations
        )
    )
    if not changes:
        return ()
    # Earlier type changes can alter the affected sets of ability changes.
    return tuple(effect for effect in effects if effect in changes or effect.layer<=Layer.TYPE)


def _carries_query_power_toughness_definition(
    card: CardInstance,
    base: Mapping[str, Any],
) -> bool:
    """Detect the all-zone fragment without forcing every zone object layered."""

    raw_fragments = [
        *base.get("ability_fragments", ()),
        *dict(card.annotations.get("copy_overrides") or {}).get(
            "ability_fragments", ()
        ),
    ]
    return any(
        isinstance(fragment, Mapping)
        and fragment.get("kind") == "query_power_toughness_definition"
        for fragment in raw_fragments
    )


class CharacteristicEvaluationHostMixin:
    """Keep authoritative effective-characteristic integration out of the kernel."""

    def _fixed_public_state_condition_holds(
        self,
        source: CardInstance,
        condition: FixedPublicStateConditionSpec,
        *,
        require_available: bool = False,
    ) -> bool:
        if (condition.kind is FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS
                or condition.kind is FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY
                and condition.predicate is not None and condition.predicate.numeric_characteristic is not None):
            from .object_query import object_query_result
            def current_numeric_object(card):
                effective = self._effective_card_data(card)
                return object_query_result(card, effective,
                    type_parts=self._type_parts(str(effective.get('type_line') or '')), known_to_actor=True,
                    attached_to_ref=None)
            return fixed_public_state_condition_matches(self.state, source, condition,
                public_object_resolver=current_numeric_object, quantity_resolver=None,
                require_available=require_available)
        return fixed_public_state_condition_matches(
            self.state,
            source,
            condition,
            public_object_resolver=partial(
                self._public_object_query_result,
                _enforce_static_component_applicability=True,
            ),
            quantity_resolver=partial(
                query_characteristic_count,
                self,
                _enforce_static_component_applicability=True,
            ),
            require_available=require_available,
        )

    def _apply_layered_characteristic_annotations(
        self,
        card: CardInstance,
        base: Mapping[str, Any],
        *,
        runtime_effects: Sequence[ContinuousEffect] = (),
        ignore_face_down: bool = False,
        maximum_layer: Layer | None = None,
        enchanted: bool = False,
        equipped: bool = False,
        modified: bool = False,
        _enforce_static_component_applicability: bool = True,
    ) -> dict[str, Any]:
        """Delegate CR 613 evaluation to its rules-owned subsystem."""

        query_layer_reached = (
            maximum_layer is None or maximum_layer >= Layer.ABILITY
        )
        resolver = (
            partial(
                query_characteristic_count,
                self,
                card,
                _enforce_static_component_applicability=(
                    _enforce_static_component_applicability
                ),
            )
            if query_layer_reached
            and (
                card.zone == "battlefield"
                or _carries_query_power_toughness_definition(card, base)
            )
            else None
        )
        return separate_custom_display_text(
            card,
            evaluate_card_characteristics(
                card,
                base,
                runtime_effects=runtime_effects,
                ignore_face_down=ignore_face_down,
                query_count_resolver=resolver,
                maximum_layer=maximum_layer,
                enchanted=enchanted,
                equipped=equipped,
                modified=modified,
            ),
        )

    def _attachment_public_state(
        self,
        card: CardInstance,
        *,
        _enforce_static_component_applicability: bool = True,
    ) -> tuple[bool, bool, bool]:
        """Return enchanted, equipped, and controller-relative modified."""

        enchanted = False
        equipped = False
        modified = any(
            type(amount) is int and amount > 0
            for amount in card.counters.values()
        )
        for attachment_id in tuple(card.attachments):
            attachment = self.state.cards.get(attachment_id)
            if (
                attachment is None
                or attachment.zone != "battlefield"
                or attachment.phased_out
                or attachment.attached_to != card.object_id
            ):
                continue
            effective = self._effective_card_data(
                attachment,
                maximum_layer=Layer.COLOR,
                _enforce_static_component_applicability=(
                    _enforce_static_component_applicability
                ),
            )
            types, subtypes, _supertypes = self._type_parts(
                str(effective.get("type_line") or "")
            )
            is_aura = "enchantment" in types and "aura" in subtypes
            is_equipment = "artifact" in types and "equipment" in subtypes
            enchanted = enchanted or is_aura
            equipped = equipped or is_equipment
            modified = modified or is_equipment or (
                is_aura and attachment.controller == card.controller
            )
        return enchanted, equipped, modified

    def _effective_type_parts(self, card):
        """Use the shared layer-5 edge for completed layer-4 types."""
        data = self._effective_card_data(card, maximum_layer=Layer.COLOR)
        return self._type_parts(str(data.get('type_line') or ''))

    def _public_object_query_result(
        self,
        card: CardInstance,
        *,
        _enforce_static_component_applicability: bool = True,
        _maximum_layer: Layer | None = Layer.COLOR,
    ) -> ObjectQueryResult:
        """Project public facts; default to the cycle-safe layer-5 boundary."""

        effective = self._effective_card_data(
            card,
            maximum_layer=_maximum_layer,
            _enforce_static_component_applicability=(
                _enforce_static_component_applicability
            ),
        )
        attached = (
            self.state.cards.get(card.attached_to)
            if card.attached_to is not None
            else None
        )
        enchanted, equipped, modified = self._attachment_public_state(
            card,
            _enforce_static_component_applicability=(
                _enforce_static_component_applicability
            ),
        )
        return object_query_result(
            card,
            effective,
            type_parts=self._type_parts(
                str(effective.get("type_line") or "")
            ),
            known_to_actor=True,
            attached_to_ref=attached.ref if attached is not None else None,
            entered_this_turn=(
                self.state.turn_sequence > 0
                and card.entered_battlefield_turn_sequence
                == self.state.turn_sequence
            ),
            enchanted=enchanted,
            equipped=equipped,
            modified=modified,
        )

    @staticmethod
    def _declared_component_fragments(
        card: CardInstance,
        base: Mapping[str, Any],
    ) -> tuple[Any, ...]:
        copy_overrides = card.annotations.get("copy_overrides")
        fragments = (
            copy_overrides.get("ability_fragments", ())
            if isinstance(copy_overrides, Mapping)
            and "ability_fragments" in copy_overrides
            else base.get("ability_fragments", ())
        )
        return (
            *fragments,
            *(card.annotations.get("granted_ability_fragments") or ()),
        )

    def _effective_static_component_fragment_map(
        self,
    ) -> dict[str, tuple]:
        return memoized_characteristic_read(self, ('static-components',), self._read_static_component_fragments)

    def _read_static_component_fragments(self) -> dict[str, tuple]:
        """Resolve every source's shared layer-6 component snapshot once."""

        candidate_effects = self._static_component_candidate_effects()
        component_effects = _static_component_presence_effects(
            candidate_effects
        )
        result = {}
        for seat in self.state.turn_order:
            for object_id in tuple(
                self.state.players[seat].zones["battlefield"]
            ):
                source = self.state.cards[object_id]
                if (
                    source.controller != seat
                    or source.phased_out
                ):
                    continue
                resolution_effects = _static_component_presence_effects(
                    active_resolution_effects(self.state, source)
                )
                copy_overrides = source.annotations.get("copy_overrides")
                has_local_component_changes = bool(
                    source.annotations.get("granted_ability_fragments")
                    or source.face_down
                    or (
                        isinstance(copy_overrides, Mapping)
                        and "ability_fragments" in copy_overrides
                    )
                )
                ability_presence_programs = (
                    *self.semantics.programs_for_oracle(copiable_oracle_id(source)),
                    *self.semantics.runtime_handler_programs_for_oracle(
                        copiable_oracle_id(source),
                        active_zone="battlefield",
                        event="characteristics.evaluate",
                    ),
                )
                has_registered_static_component = any(
                    program.active_zone in {"all", "battlefield"}
                    and (
                        (
                            program.ability_id.startswith("static:")
                            and program.event == "characteristics.evaluate"
                            and bool(program.handlers)
                        )
                        or CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage
                    )
                    for program in ability_presence_programs
                )
                if (
                    not component_effects
                    and not resolution_effects
                    and not has_local_component_changes
                    and not has_registered_static_component
                ):
                    result[object_id] = ()
                    continue
                base = self._compiled_base_characteristics(
                    source,
                    self.card_record(source),
                    error_type=GameRuleError,
                )
                if (
                    not component_effects
                    and not resolution_effects
                    and not has_local_component_changes
                ):
                    result[object_id] = canonical_ability_fragments(
                        base.get("ability_fragments", ()),
                    )
                    continue
                enchanted, equipped, modified = self._attachment_public_state(
                    source,
                    _enforce_static_component_applicability=False,
                )
                effective = self._apply_layered_characteristic_annotations(
                    source,
                    base,
                    runtime_effects=(
                        *resolution_effects,
                        *component_effects,
                    ),
                    maximum_layer=Layer.ABILITY,
                    enchanted=enchanted,
                    equipped=equipped,
                    modified=modified,
                    _enforce_static_component_applicability=False,
                )
                result[object_id] = canonical_ability_fragments(
                    effective.get("ability_fragments", ()),
                )
        return result

    def _static_keys_from_fragment_map(self, fragments):
        return {object_id: static_component_keys(values, source_designations=current_source_designations(
            self.state, self.state.cards[object_id])) for object_id, values in fragments.items()}

    def _effective_static_component_key_map(self):
        return self._static_keys_from_fragment_map(self._effective_static_component_fragment_map())

    def _granted_characteristic_occurrences(self, source, fragments, keys):
        from .rules.granted_characteristic_shapes import granted_characteristic_occurrences
        return granted_characteristic_occurrences(fragments.get(source.object_id, ()), keys.get(source.object_id, ()))

    def _static_component_candidate_effects(
        self,
    ) -> tuple[ContinuousEffect, ...]:
        """Collect typed effects needed by current or prospective layer 6."""

        return memoized_characteristic_read(self,('eligible-static-candidates',),self._read_eligible_static_candidates)

    def _read_eligible_static_candidates(self):
        effects,participants=self._static_component_candidate_plan()
        result=[]
        for effect in effects:
            producer=participants.get(effect.effect_id)
            source=self.state.cards.get(producer[0]) if producer is not None else None
            if effect.layer==Layer.ABILITY and source is not None:
                if self._static_component_presence_at_ability_start(source,producer[1]) is False:continue
            result.append(effect)
        return tuple(result)

    def _static_component_candidate_plan(self):
        return memoized_characteristic_read(self,('static-candidate-plan',),self._read_static_component_candidate_plan)

    def _read_static_component_candidate_plan(self):
        def collect(presence_only,sources):
            return collect_card_program_continuous_effects(self.state,self.semantics,self.semantic_program_is_current_trusted,
                maximum_layer=Layer.ABILITY,static_component_presence_only=presence_only,
                public_object_resolver=partial(self._public_object_query_result,_enforce_static_component_applicability=False),
                quantity_resolver=partial(query_characteristic_count,self,_enforce_static_component_applicability=False),
                generated_effect_sources=sources)
        sources={}
        effects=(*ring_legendary_effects(current_ring_rule_views(self)),*collect(True,sources))
        removals={'remove_all_abilities','remove_ability_fragment','remove_ability','prohibit_ability','set_basic_land_types'}
        if any(operation.op in removals for effect in (*effects,*(e for e in self.state.continuous_effects or () if isinstance(e,ContinuousEffect)))
                for operation in effect.operations):
            sources={}
            effects=(*ring_legendary_effects(current_ring_rule_views(self)),*collect(False,sources))
        return effects,sources

    def _static_component_presence_at_ability_start(self,source,program_key):
        return memoized_characteristic_read(self,('static-ability-start',source.object_id,source.logical_object_id,program_key),
            lambda:self._read_static_component_presence_at_ability_start(source,program_key),identity=source)

    def _read_static_component_presence_at_ability_start(self,source,program_key):
        """Apply the canonical effect prefix before a printed layer-6 source starts."""
        from .continuous_effects import order_continuous_effects
        candidates,participants=self._static_component_candidate_plan()
        own={effect.effect_id for effect in candidates if participants.get(effect.effect_id)==(source.object_id,program_key)}
        if not own or min(effect.layer for effect in candidates if effect.effect_id in own)!=Layer.ABILITY:
            return None
        if source.zone!='battlefield':return True
        resolved={effect.effect_id:effect for card in self.state.cards.values() if card.zone=='battlefield'
            for effect in active_resolution_effects(self.state,card)}
        from .continuous_effect_model import ContinuousEffectDuration
        present=tuple(effect for effect in (*resolved.values(),*candidates)
            if effect.duration is not ContinuousEffectDuration.WHILE_SOURCE_PRESENT or effect.source_present)
        if not any(effect.effect_id in own for effect in present):return False
        ordered,_cycles=order_continuous_effects(present)
        prefix=[]
        for effect in ordered:
            if effect.effect_id in own:break
            producer=participants.get(effect.effect_id)
            if effect.layer==Layer.ABILITY and producer is not None:
                other=self.state.cards.get(producer[0])
                if other is not None and self._static_component_presence_at_ability_start(other,producer[1]) is False:
                    continue
            prefix.append(effect)
        base=self._compiled_base_characteristics(source,self.card_record(source),error_type=GameRuleError)
        effective=self._apply_layered_characteristic_annotations(source,base,runtime_effects=tuple(prefix),
            maximum_layer=Layer.ABILITY,_enforce_static_component_applicability=False)
        return program_key in static_component_keys(effective.get('ability_fragments',()),
            source_designations=current_source_designations(self.state,source))

    def _prospective_static_component_keys(
        self,
        card: CardInstance,
        *,
        prospective_zone: str,
        prospective_controller: str | None,
    ) -> tuple[str, ...]:
        """Resolve one supported future-zone layer-6 snapshot without mutation."""

        if prospective_zone != "battlefield":
            raise GameRuleError(
                "Prospective static components support battlefield entry only"
            )
        prospective = copy.copy(card)
        prospective.zone = prospective_zone
        if prospective_controller is not None:
            prospective.controller = prospective_controller
        base = self._compiled_base_characteristics(
            prospective,
            self.card_record(card),
            error_type=GameRuleError,
        )
        identity_continues = (
            card.zone == "stack" and prospective_zone == "battlefield"
        )
        resolution_effects = (
            active_resolution_effects(self.state, card)
            if identity_continues
            else ()
        )
        effective = self._apply_layered_characteristic_annotations(
            prospective,
            base,
            runtime_effects=(
                *resolution_effects,
                *self._static_component_candidate_effects(),
            ),
            maximum_layer=Layer.ABILITY,
            _enforce_static_component_applicability=False,
        )
        return static_component_keys(
            effective.get("ability_fragments", ()),
            source_designations=current_source_designations(self.state,prospective),
        )

    def _static_component_keys_at_layer(self, source, maximum_layer):
        """Keep earlier-layer ability participation independent of later removal."""
        data = self._effective_card_data(source, maximum_layer=maximum_layer,
            _enforce_static_component_applicability=False)
        return static_component_keys(data.get('ability_fragments', ()),
            source_designations=current_source_designations(self.state, source))

    def _effective_static_component_keys(
        self,
        card: CardInstance,
        *,
        prospective_zone: str | None = None,
        prospective_controller: str | None = None,
    ) -> tuple[str, ...]:
        """Resolve one source through the shared layer-6 batch owner."""

        if prospective_zone is not None:
            return self._prospective_static_component_keys(
                card,
                prospective_zone=prospective_zone,
                prospective_controller=prospective_controller,
            )
        if card.zone != "battlefield":
            if card.face_down or _static_component_presence_effects(
                active_resolution_effects(self.state, card)
            ):
                # The exact layer engine currently evaluates mutable ability
                # components only for battlefield objects.  A relevant
                # off-battlefield modification therefore makes applicability
                # unavailable and must fail closed rather than restoring the
                # printed component by assumption.
                return ()
            base = self._compiled_base_characteristics(
                card,
                self.card_record(card),
                error_type=GameRuleError,
            )
            return static_component_keys(
                self._declared_component_fragments(card, base),
                source_designations=current_source_designations(self.state,card),
            )
        return self._effective_static_component_key_map().get(
            card.object_id, ()
        )

    def _static_component_applicability_effects(
        self,
        card: CardInstance,
        base: Mapping[str, Any],
        applicable_keys: Sequence[str],
    ) -> tuple[ContinuousEffect, ...]:
        """Remove every payload owned only by an inapplicable component."""

        declared = set(
            declared_static_component_keys(
                self._declared_component_fragments(card, base)
            )
        )
        inactive = sorted(declared - set(applicable_keys))
        operations: list[ContinuousOperation] = []
        for semantic_key in inactive:
            operations.append(
                ContinuousOperation(
                    "remove_ability_fragment",
                    ability_fragment_to_dict(
                        StaticComponentSpec(semantic_key)
                    ),
                )
            )
            program = self.semantics.get(semantic_key)
            if program is None:
                continue
            operations.extend(
                ContinuousOperation(
                    "remove_ability_fragment",
                    ability_fragment_to_dict(fragment),
                )
                for fragment in fragments_from_descriptors(program.handlers)
            )
        applicable = set(applicable_keys)
        scopes = (
            fragment
            for fragment in canonical_ability_fragments(
                self._declared_component_fragments(card, base)
            )
            if isinstance(fragment, StaticComponentScopeSpec)
            and fragment.applicability is not None
            and fragment.parent_semantic_key in applicable
        )
        for scope in scopes:
            active_children = set(scope.child_semantic_keys).intersection(
                applicable
            )
            for keyword in scope.keywords:
                if any(
                    (
                        program := self.semantics.get(semantic_key)
                    ) is not None
                    and keyword.casefold()
                    in {
                        str(value).casefold()
                        for value in program.coverage
                    }
                    for semantic_key in active_children
                ):
                    operations.append(
                        ContinuousOperation("add_ability", keyword)
                    )
        if not operations:
            return ()
        identity = ContinuousObjectIdentity(
            object_id=card.object_id,
            logical_object_id=card.logical_object_id,
        )
        return (
            ContinuousEffect(
                effect_id=f"{card.object_id}:static-component-applicability",
                source_id=card.object_id,
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=max(0, int(card.zone_timestamp)),
                operations=tuple(operations),
                origin=ContinuousEffectOrigin.STATIC_ABILITY,
                relation=ContinuousEffectRelation.SOURCE_OBJECT,
                related_object=identity,
            ),
        )

    def _effective_card_data(
        self,
        value: str | CardInstance,
        *,
        printed_entry_characteristics: bool = False,
        ignore_face_down: bool = False,
        maximum_layer: Layer | None = None,
        _enforce_static_component_applicability: bool = True,
    ) -> dict[str, Any]:
        card = value if isinstance(value, CardInstance) else self.state.cards[value]
        key = ('card', id(card), printed_entry_characteristics, ignore_face_down,
            maximum_layer, _enforce_static_component_applicability)
        return memoized_characteristic_read(self, key, lambda: self._read_effective_card_data(
            card, printed_entry_characteristics=printed_entry_characteristics,
            ignore_face_down=ignore_face_down, maximum_layer=maximum_layer,
            _enforce_static_component_applicability=_enforce_static_component_applicability), identity=card)

    def _read_effective_card_data(
        self, value: str | CardInstance, *, printed_entry_characteristics: bool = False,
        ignore_face_down: bool = False, maximum_layer: Layer | None = None,
        _enforce_static_component_applicability: bool = True,
    ) -> dict[str, Any]:
        card = value if isinstance(value, CardInstance) else self.state.cards[value]
        record = self.card_record(card)
        base = self._compiled_base_characteristics(
            card,
            record,
            error_type=GameRuleError,
        )
        static_component_fragments = (
            self._effective_static_component_fragment_map()
            if card.zone == "battlefield"
            and _enforce_static_component_applicability
            else None
        )
        static_component_key_map = (self._static_keys_from_fragment_map(static_component_fragments)
            if static_component_fragments is not None else None)
        runtime_effects = (
            (
                *active_resolution_effects(self.state, card),
                *ring_legendary_effects(current_ring_rule_views(self), maximum_layer=maximum_layer),
                *(
                    self._static_component_applicability_effects(
                        card,
                        base,
                        static_component_key_map.get(card.object_id, ()),
                    )
                    if static_component_key_map is not None
                    else ()
                ),
                *collect_card_program_continuous_effects(
                    self.state,
                    self.semantics,
                    self.semantic_program_is_current_trusted,
                    maximum_layer=maximum_layer,
                    public_object_resolver=(
                        partial(
                            self._public_object_query_result,
                            _enforce_static_component_applicability=(
                                _enforce_static_component_applicability
                            ),
                        )
                        if maximum_layer is None
                        or maximum_layer >= Layer.ABILITY
                        else None
                    ),
                    quantity_resolver=(
                        partial(
                            query_characteristic_count,
                            self,
                            _enforce_static_component_applicability=(
                                _enforce_static_component_applicability
                            ),
                        )
                        if maximum_layer is None
                        or maximum_layer >= Layer.ABILITY
                        else None
                    ),
                    # Setting codecs admit graveyard counts or devotion, whose
                    # existing owner stops after copy/control/text (CR 700.5a).
                    setting_quantity_resolver=partial(
                        query_characteristic_count, self,
                        _enforce_static_component_applicability=_enforce_static_component_applicability,
                    ),
                    static_component_resolver=(
                        (
                            lambda source: static_component_key_map.get(
                                source.object_id, ()
                            )
                        )
                        if static_component_key_map is not None
                        else None
                    ),
                    early_static_component_resolver=self._static_component_keys_at_layer,
                    ability_source_participation=(self._static_component_presence_at_ability_start
                        if static_component_key_map is not None else None),
                    granted_characteristic_resolver=(
                        (lambda source: self._granted_characteristic_occurrences(source,
                            static_component_fragments, static_component_key_map))
                        if static_component_fragments is not None else None),
                ),
            )
            if card.zone == "battlefield"
            else ()
        )
        if card.zone=='stack' and (maximum_layer is None or maximum_layer>=Layer.ABILITY):
            from .compiled_cast_timing import compiled_conditional_flash_stack_effects
            runtime_effects=(*runtime_effects,*compiled_conditional_flash_stack_effects(self,card))
        enchanted, equipped, modified = (
            self._attachment_public_state(
                card,
                _enforce_static_component_applicability=(
                    _enforce_static_component_applicability
                ),
            )
            if card.zone == "battlefield"
            and (
                maximum_layer is None
                or maximum_layer >= Layer.ABILITY
            )
            else (False, False, False)
        )
        base = self._apply_layered_characteristic_annotations(
            card,
            base,
            runtime_effects=runtime_effects,
            ignore_face_down=ignore_face_down,
            maximum_layer=maximum_layer,
            enchanted=enchanted,
            equipped=equipped,
            modified=modified,
            _enforce_static_component_applicability=(
                _enforce_static_component_applicability
            ),
        )
        if maximum_layer is None:
            base = apply_dynamic_characteristic_fragments(self, card, base)
        if (
            card.zone == "battlefield"
            and not printed_entry_characteristics
            and "battle"
            in self._type_parts(str(base.get("type_line") or ""))[0]
        ):
            # CR 310.4c makes a battlefield Battle's defense equal to
            # its defense-counter count. The printed number remains the
            # copiable/off-battlefield characteristic.
            base["defense"] = str(
                max(0, int(card.counters.get("defense", 0)))
            )
        return base


__all__ = ["CharacteristicEvaluationHostMixin"]
