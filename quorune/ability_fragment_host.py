from __future__ import annotations

from typing import Any

from .ability_fragments import (
    AbilityFragmentError,
    StaticAbilityFragment,
    canonical_ability_fragments,
    static_keyword_scope_keywords,
)
from .carddb_characteristics import base_card_characteristics,copiable_card_characteristics
from .compiled_ability_fragments import (
    compiled_ability_fragment_dicts,
    compiled_enchant_spec,
    compiled_static_ability_fragments,
)
from .compiled_activated_abilities import (
    compiled_activated_ability_dicts,
)
from .enchant_spec import EnchantSpec


class AbilityFragmentHostMixin:
    """Narrow runtime facade for trusted compiled ability fragments."""

    def _copyable_characteristics(self,card):
        from .continuous_effect_model import Layer
        from .errors import GameRuleError
        from .face_down_copy import current_face_down_copy_values
        status_values=current_face_down_copy_values(self,card)
        if status_values is not None:return status_values
        if card.permanent_composition is not None:
            return self._effective_card_data(card,maximum_layer=Layer.COPY)
        record=self.card_record(card)
        base=self._compiled_base_characteristics(card,record,error_type=GameRuleError)
        return copiable_card_characteristics(card,record,base)

    def _spell_copy_characteristics(self,card):
        from .token_copy_faces import spell_copy_characteristics
        return spell_copy_characteristics(self,card)

    def _compiled_ability_fragments(
        self,
        card: Any,
        *,
        face_name: str | None = None,
    ) -> tuple[StaticAbilityFragment, ...]:
        return compiled_static_ability_fragments(
            self,
            card,
            face_name=face_name,
        )

    def _effective_ability_fragments(
        self,
        card: Any,
        *,
        error_type: type[Exception] | None = None,
    ) -> tuple[StaticAbilityFragment, ...]:
        """Return one canonical current static-ability snapshot."""

        try:
            return canonical_ability_fragments(
                self._effective_card_data(card).get(
                    "ability_fragments", ()
                )
            )
        except AbilityFragmentError as exc:
            if error_type is None:
                raise
            raise error_type(str(exc)) from exc

    def _compiled_enchant_spec(
        self,
        card: Any,
        *,
        face_name: str | None = None,
    ) -> EnchantSpec | None:
        return compiled_enchant_spec(
            self,
            card,
            face_name=face_name,
        )

    def _compiled_ability_fragment_dicts(
        self,
        card: Any,
        *,
        face_name: str | None = None,
        error_type: type[Exception] | None = None,
        record: Any = None,
    ) -> list[dict[str, Any]]:
        return compiled_ability_fragment_dicts(
            self,
            card,
            face_name=face_name,
            error_type=error_type,
            record=record,
        )

    def _compiled_base_characteristics(
        self,
        card: Any,
        record: Any,
        *,
        error_type: type[Exception] | None = None,
    ) -> dict[str, Any]:
        if getattr(card,'permanent_composition',None) is not None:
            from .composition_characteristics import composition_copiable_values
            return composition_copiable_values(card.permanent_composition)
        base = base_card_characteristics(card, record)
        from .rules.station_symbols import compiled_station_symbols
        if compiled_station_symbols(self, card, record=record) is not None:
            # CR721.2: the P/T box belongs to the symbol's static effect,
            # rather than unconditional printed characteristics.
            base.update(power=None, toughness=None)
        from .preparation import compiled_prepare_spell
        from .preparation_model import PREPARE_SPELL_FIELD
        prepare_spell=compiled_prepare_spell(self,card,record=record)
        if prepare_spell is not None:base[PREPARE_SPELL_FIELD]=prepare_spell.to_dict()
        from .rules.prototype import prototype_characteristic_values
        base.update(prototype_characteristic_values(card))
        from .rules.fuse import fuse_characteristic_values
        base.update(fuse_characteristic_values(card))
        from .rules.current_face_keywords import current_printed_face_keywords
        base['keywords']=current_printed_face_keywords(self,card,record,base)
        base["ability_fragments"] = self._compiled_ability_fragment_dicts(
            card,
            error_type=error_type,
            record=record,
        )
        scoped_keywords = {
            keyword.casefold()
            for keyword in static_keyword_scope_keywords(
                base["ability_fragments"]
            )
        }
        if scoped_keywords:
            base["keywords"] = [
                keyword
                for keyword in base.get("keywords", ())
                if str(keyword).casefold() not in scoped_keywords
            ]
        base["activated_abilities"] = compiled_activated_ability_dicts(
            self,
            card,
            error_type=error_type,
            record=record,
        )
        return base


__all__ = ["AbilityFragmentHostMixin"]
