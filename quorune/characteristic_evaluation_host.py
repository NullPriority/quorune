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
            ),
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
                ),
            )
            if card.zone == "battlefield"
            else ()
        )
        base = self._apply_layered_characteristic_annotations(
            card,
            base,
            runtime_effects=runtime_effects,
            ignore_face_down=ignore_face_down,
            maximum_layer=maximum_layer,
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
