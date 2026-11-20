from __future__ import annotations

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping


_CAPABILITY = "counter.placement.quantity_replacement"
_HANDLER_ID = "replacement.counter.quantity.v2"
_COUNTER_NAME_ANY = "counters"
_COUNTER_TARGET_GOBLIN = "goblin"
_COUNTER_TARGET_VEHICLE = "vehicle"


@dataclass(frozen=True, slots=True)
class CounterReplacementTarget:
    target_kinds: tuple[str, ...]
    target_controller_relation: str
    target_types_all: tuple[str, ...] = ()
    target_types_any: tuple[str, ...] = ()


_TARGETS = {
    "a permanent you control": CounterReplacementTarget(
        ("permanent",), "source_controller"
    ),
    "a creature you control": CounterReplacementTarget(
        ("permanent",), "source_controller", ("creature",)
    ),
    "a creature": CounterReplacementTarget(
        ("permanent",), "any", ("creature",)
    ),
    "an artifact or creature you control": CounterReplacementTarget(
        ("permanent",),
        "source_controller",
        target_types_any=("artifact", "creature"),
    ),
    "a creature or vehicle you control": CounterReplacementTarget(
        ("permanent",),
        "source_controller",
        target_types_any=("creature", _COUNTER_TARGET_VEHICLE),
    ),
    "a creature, spacecraft, or planet you control": CounterReplacementTarget(
        ("permanent",),
        "source_controller",
        target_types_any=("creature", "planet", "spacecraft"),
    ),
    "an army, goblin, or orc you control": CounterReplacementTarget(
        ("permanent",),
        "source_controller",
        target_types_any=("army", _COUNTER_TARGET_GOBLIN, "orc"),
    ),
    "a permanent or player": CounterReplacementTarget(
        ("permanent", "player"), "any"
    ),
}


_DIRECT = re.compile(
    r"^If (?P<actor>an effect|you) would put one or more "
    r"(?P<counters>counters|\+1/\+1 counters) on (?P<target>.+?), "
    r"(?:(?:it )?puts?) (?P<quantity>twice that many|that many plus one) "
    r"(?:of those counters|(?:of each of )?those kinds of counters|\+1/\+1 counters) on "
    r"(?:that permanent|that creature|that permanent or player|it) instead\.$",
    re.IGNORECASE,
)
_PASSIVE = re.compile(
    r"^If one or more (?P<counters>counters|\+1/\+1 counters) would be put "
    r"on (?P<target>.+?), (?P<quantity>twice that many|that many plus one) "
    r"(?:(?:of each of )?those kinds of counters|\+1/\+1 counters) "
    r"(?:are put on (?:that permanent|that creature|it)|are put on it) instead\.$",
    re.IGNORECASE,
)


def _descriptor(
    *,
    effect_scope: str,
    placing_player_relation: str,
    target: CounterReplacementTarget,
    counters: str,
    quantity: str,
) -> Mapping[str, Any]:
    multiplier = 2 if quantity.casefold() == "twice that many" else 1
    additional = 0 if multiplier == 2 else 1
    return {
        "handler_id": _HANDLER_ID,
        "schema_version": 2,
        "event": "counter.place",
        "condition": {
            "effect_scope": effect_scope,
            "placing_player_relation": placing_player_relation,
            "target_controller_relation": target.target_controller_relation,
            "target_kinds": list(target.target_kinds),
            "counter_names": (
                ["+1/+1"] if counters.casefold() != _COUNTER_NAME_ANY else []
            ),
            "target_types_all": list(target.target_types_all),
            "target_types_any": list(target.target_types_any),
        },
        "modification": {
            "multiplier": multiplier,
            "additional": additional,
        },
    }


def static_counter_quantity_replacement_handler(
    text: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    """Lower the closed ordinary counter-quantity replacement grammar."""

    match = _DIRECT.fullmatch(text)
    if match is not None:
        actor = match.group("actor").casefold()
        target = _TARGETS.get(match.group("target").casefold())
        if target is None:
            return None
        return (
            "static-counter-quantity-replacement-v2",
            _descriptor(
                effect_scope=("effect_only" if actor == "an effect" else "any"),
                placing_player_relation=(
                    "any" if actor == "an effect" else "source_controller"
                ),
                target=target,
                counters=match.group(_COUNTER_NAME_ANY),
                quantity=match.group("quantity"),
            ),
            _CAPABILITY,
        )
    match = _PASSIVE.fullmatch(text)
    if match is None:
        return None
    target = _TARGETS.get(match.group("target").casefold())
    if target is None:
        return None
    return (
        "static-counter-quantity-replacement-v2",
        _descriptor(
            effect_scope="any",
            placing_player_relation="any",
            target=target,
            counters=match.group(_COUNTER_NAME_ANY),
            quantity=match.group("quantity"),
        ),
        _CAPABILITY,
    )


__all__ = ["static_counter_quantity_replacement_handler"]
