from __future__ import annotations

from functools import partial
from typing import Any, Mapping, Sequence

from .carddb_characteristics import separate_custom_display_text
from .card_programs.runtime import collect_card_program_continuous_effects
from .ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE,
    StaticComponentSpec,
    ability_fragment_to_dict,
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
from .object_query import ObjectQueryResult, object_query_result
from .semantic_runtime.ability_fragments import fragments_from_descriptors


_STATIC_COMPONENT_PRESENCE_OPERATIONS = frozenset(
    {
        "add_ability_fragment",
        "remove_ability_fragment",
        "remove_all_abilities",
    }
)


def _static_component_presence_effects(
    effects: Sequence[ContinuousEffect],
) -> tuple[ContinuousEffect, ...]:
    return tuple(
        effect
        for effect in effects
        if any(
            operation.op in _STATIC_COMPONENT_PRESENCE_OPERATIONS
            for operation in effect.operations
        )
    )


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

    def _public_object_query_result(
        self,
        card: CardInstance,
        *,
        _enforce_static_component_applicability: bool = True,
    ) -> ObjectQueryResult:
        """Project one permanent through the shared cycle-safe layer-5 edge."""

        effective = self._effective_card_data(
            card,
            maximum_layer=Layer.COLOR,
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

    def _effective_static_component_key_map(
        self,
    ) -> dict[str, tuple[str, ...]]:
        """Resolve every source's shared layer-6 component snapshot once."""

        candidate_effects = collect_card_program_continuous_effects(
            self.state,
            self.semantics,
            self.semantic_program_is_current_trusted,
            maximum_layer=Layer.ABILITY,
            public_object_resolver=partial(
                self._public_object_query_result,
                _enforce_static_component_applicability=False,
            ),
            quantity_resolver=partial(
                query_characteristic_count,
                self,
                _enforce_static_component_applicability=False,
            ),
        )
        component_effects = _static_component_presence_effects(
            candidate_effects
        )
        result: dict[str, tuple[str, ...]] = {}
        for seat in self.state.turn_order:
            for object_id in tuple(
                self.state.players[seat].zones["battlefield"]
            ):
                source = self.state.cards[object_id]
                if (
                    source.controller != seat
                    or source.phased_out
                    or getattr(source, "face_down", False)
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
                    *self.semantics.programs_for_oracle(source.oracle_id),
                    *self.semantics.runtime_handler_programs_for_oracle(
                        source.oracle_id,
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
                    result[object_id] = static_component_keys(
                        base.get("ability_fragments", ())
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
                result[object_id] = static_component_keys(
                    effective.get("ability_fragments", ())
                )
        return result

    def _effective_static_component_keys(
        self,
        card: CardInstance,
    ) -> tuple[str, ...]:
        """Resolve one source through the shared layer-6 batch owner."""

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
        record = self.card_record(card)
        base = self._compiled_base_characteristics(
            card,
            record,
            error_type=GameRuleError,
        )
        static_component_key_map = (
            self._effective_static_component_key_map()
            if card.zone == "battlefield"
            and _enforce_static_component_applicability
            else None
        )
        runtime_effects = (
            (
                *active_resolution_effects(self.state, card),
                *self._static_component_applicability_effects(
                    card,
                    base,
                    static_component_key_map.get(card.object_id, ()),
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
                    static_component_resolver=(
                        (
                            lambda source: static_component_key_map.get(
                                source.object_id, ()
                            )
                        )
                        if static_component_key_map is not None
                        else None
                    ),
                ),
            )
            if card.zone == "battlefield"
            else ()
        )
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
