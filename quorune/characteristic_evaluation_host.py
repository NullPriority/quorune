from __future__ import annotations

from functools import partial
from typing import Any, Mapping, Sequence

from .carddb_characteristics import separate_custom_display_text
from .card_programs.runtime import collect_card_program_continuous_effects
from .characteristic_evaluation import evaluate_card_characteristics
from .continuous_effects import ContinuousEffect, Layer
from .continuous_effect_state import active_resolution_effects
from .dynamic_characteristics import (
    apply_dynamic_characteristic_fragments,
    query_characteristic_count,
)
from .errors import GameRuleError
from .model import CardInstance
from .object_query import ObjectQueryResult, object_query_result


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
    ) -> dict[str, Any]:
        """Delegate CR 613 evaluation to its rules-owned subsystem."""

        resolver = (
            partial(query_characteristic_count, self, card)
            if maximum_layer is None and card.zone == "battlefield"
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
    ) -> ObjectQueryResult:
        """Project one permanent through the shared cycle-safe layer-5 edge."""

        effective = self._effective_card_data(
            card,
            maximum_layer=Layer.COLOR,
        )
        attached = (
            self.state.cards.get(card.attached_to)
            if card.attached_to is not None
            else None
        )
        enchanted, equipped, modified = self._attachment_public_state(card)
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

    def _effective_card_data(
        self,
        value: str | CardInstance,
        *,
        printed_entry_characteristics: bool = False,
        ignore_face_down: bool = False,
        maximum_layer: Layer | None = None,
    ) -> dict[str, Any]:
        card = value if isinstance(value, CardInstance) else self.state.cards[value]
        record = self.card_record(card)
        base = self._compiled_base_characteristics(
            card,
            record,
            error_type=GameRuleError,
        )
        runtime_effects = (
            (
                *active_resolution_effects(self.state, card),
                *collect_card_program_continuous_effects(
                    self.state,
                    self.semantics,
                    self.semantic_program_is_current_trusted,
                    maximum_layer=maximum_layer,
                    public_object_resolver=(
                        self._public_object_query_result
                        if maximum_layer is None
                        or maximum_layer >= Layer.ABILITY
                        else None
                    ),
                    quantity_resolver=(
                        partial(query_characteristic_count, self)
                        if maximum_layer is None
                        or maximum_layer >= Layer.ABILITY
                        else None
                    ),
                ),
            )
            if card.zone == "battlefield"
            else ()
        )
        enchanted, equipped, modified = (
            self._attachment_public_state(card)
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
