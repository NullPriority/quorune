from __future__ import annotations

"""Resolution-created keyword grants pinned to one battlefield incarnation."""

from typing import Any, Protocol

from .continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousOperation,
    Layer,
)
from .continuous_effect_state import (
    ContinuousEffectStateError,
    ResolutionEffectSource,
    create_resolution_continuous_effect,
)
from .zone_object_keyword_model import (
    ZONE_OBJECT_KEYWORDS,
    ZoneObjectKeywordGrantError,
    normalized_zone_object_keyword,
)


class ZoneObjectKeywordGrantHost(Protocol):
    state: Any

    def _next_ref(self, prefix: str) -> str: ...

    def _next_zone_timestamp(self) -> int: ...


def commit_zone_object_keyword_grant(
    host: ZoneObjectKeywordGrantHost,
    *,
    card: Any,
    source: ResolutionEffectSource,
    keyword: str,
    duration: ContinuousEffectDuration = ContinuousEffectDuration.ZONE_OBJECT,
) -> ContinuousEffect:
    """Grant a typed layer-6 keyword for this battlefield logical object."""

    if getattr(card, "zone", None) != "battlefield" or bool(
        getattr(card, "phased_out", False)
    ):
        raise ZoneObjectKeywordGrantError(
            "Zone-object keyword grants require a phased-in battlefield permanent"
        )
    if type(getattr(card, "object_id", None)) is not str or not card.object_id:
        raise ZoneObjectKeywordGrantError(
            "Zone-object keyword grants require physical object identity"
        )
    if (
        type(getattr(card, "logical_object_id", None)) is not str
        or not card.logical_object_id
    ):
        raise ZoneObjectKeywordGrantError(
            "Zone-object keyword grants require logical object identity"
        )
    if not isinstance(source, ResolutionEffectSource):
        raise ZoneObjectKeywordGrantError(
            "Zone-object keyword grants require typed resolution source identity"
        )
    normalized = normalized_zone_object_keyword(keyword)
    if duration not in {
        ContinuousEffectDuration.ZONE_OBJECT,
        ContinuousEffectDuration.UNTIL_CONTROL_CHANGE,
    }:
        raise ZoneObjectKeywordGrantError(
            "Zone-object keyword grant duration is unsupported"
        )
    try:
        effect = create_resolution_continuous_effect(
            host,
            source=source,
            targets=(card,),
            layer=Layer.ABILITY,
            sublayer="6",
            operations=(ContinuousOperation("add_ability", normalized),),
            duration=duration,
        )
    except ContinuousEffectStateError as exc:
        raise ZoneObjectKeywordGrantError(str(exc)) from exc
    if effect is None:
        raise ZoneObjectKeywordGrantError(
            "Zone-object keyword grants require continuous-effect state"
        )
    return effect


__all__ = [
    "ZONE_OBJECT_KEYWORDS",
    "ZoneObjectKeywordGrantError",
    "commit_zone_object_keyword_grant",
    "normalized_zone_object_keyword",
]
