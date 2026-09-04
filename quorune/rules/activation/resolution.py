from __future__ import annotations

from dataclasses import dataclass

from ...replacement.immutable import FrozenMap, thaw_value


_HISTORICAL_GAIN_LIFE_KEY = "builtin:food"
_EXPLORE_KEYS = frozenset(
    {"builtin:explore-target", "builtin:map-explore"}
)
_EQUIP_KEY = "builtin:equip"
_JUNK_IMPULSE_KEY = "builtin:fixed-impulse-access:1:turn"


@dataclass(frozen=True, slots=True)
class BuiltinActivationResolution:
    effects: tuple[FrozenMap, ...]
    note: str

    def effect_dicts(self) -> list[dict[str, object]]:
        return [thaw_value(effect) for effect in self.effects]


def builtin_activation_resolution(
    semantic_key: str | None,
    controller: str,
) -> BuiltinActivationResolution | None:
    """Lower generic activation semantics into immutable resolution effects."""

    if semantic_key == _HISTORICAL_GAIN_LIFE_KEY:
        return _gain_life_resolution(controller, 3, reason="Food token")
    if semantic_key and semantic_key.startswith("builtin:gain-life:"):
        amount_text = semantic_key.rsplit(":", 1)[1]
        if not amount_text.isdigit():
            return None
        return _gain_life_resolution(
            controller,
            int(amount_text),
            reason="activated ability",
        )
    if semantic_key and semantic_key.startswith("builtin:draw:"):
        amount_text = semantic_key.rsplit(":", 1)[1]
        if not amount_text.isdigit() or int(amount_text) < 1:
            return None
        return BuiltinActivationResolution(
            effects=(
                FrozenMap(
                    {
                        "op": "draw",
                        "player": controller,
                        "count": int(amount_text),
                        "private": True,
                    }
                ),
            ),
            note="Built-in draw ability resolved",
        )
    if semantic_key in _EXPLORE_KEYS:
        return BuiltinActivationResolution(
            effects=(
                FrozenMap(
                    {
                        "op": "explore",
                        "player": controller,
                        "card": "$target.0",
                    }
                ),
            ),
            note="Built-in target-explore ability resolved",
        )
    if semantic_key == _JUNK_IMPULSE_KEY:
        return BuiltinActivationResolution(
            effects=(
                FrozenMap(
                    {
                        "op": "fixed_impulse_access",
                        "player": controller,
                        "count": 1,
                        "duration": "until_end_of_turn",
                    }
                ),
            ),
            note="Built-in Junk impulse access resolved",
        )
    if semantic_key == _EQUIP_KEY:
        return BuiltinActivationResolution(
            effects=(
                FrozenMap(
                    {
                        "op": "attach",
                        "equipment": "$source",
                        "creature": "$target.0",
                        "reason": "Equip",
                    }
                ),
            ),
            note="Built-in Equip ability resolved",
        )
    return None


def is_builtin_activation_semantic(semantic_key: str | None) -> bool:
    return builtin_activation_resolution(semantic_key, "_") is not None


def _gain_life_resolution(
    controller: str,
    amount: int,
    *,
    reason: str,
) -> BuiltinActivationResolution:
    return BuiltinActivationResolution(
        effects=(
            FrozenMap(
                {
                    "op": "life",
                    "player": controller,
                    "delta": amount,
                    "reason": reason,
                }
            ),
        ),
        note="Built-in gain-life ability resolved",
    )


__all__ = [
    "BuiltinActivationResolution",
    "builtin_activation_resolution",
    "is_builtin_activation_semantic",
]
