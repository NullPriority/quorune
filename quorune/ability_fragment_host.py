from __future__ import annotations

from typing import Any

from .ability_fragments import (
    AbilityFragmentError,
    StaticAbilityFragment,
    canonical_ability_fragments,
    static_keyword_scope_keywords,
)
from .carddb_characteristics import base_card_characteristics
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
    ) -> list[dict[str, Any]]:
        return compiled_ability_fragment_dicts(
            self,
            card,
            face_name=face_name,
            error_type=error_type,
        )

    def _compiled_base_characteristics(
        self,
        card: Any,
        record: Any,
        *,
        error_type: type[Exception] | None = None,
    ) -> dict[str, Any]:
        base = base_card_characteristics(card, record)
        base["ability_fragments"] = self._compiled_ability_fragment_dicts(
            card,
            error_type=error_type,
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
        )
        return base


__all__ = ["AbilityFragmentHostMixin"]
