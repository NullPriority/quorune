from __future__ import annotations

"""Closed Oracle lowering for fixed life-gain and life-loss effects."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


LIFE_MECHANIC = "cr-119-life"
_LIFE_OPERATION = "life"

_DOUBLE_CONTROLLER_LIFE_GAIN = re.compile(
    r"^If you would gain life, you gain twice that much life instead\.?$",
    re.IGNORECASE,
)


def static_life_gain_prohibition_handler(text):
    from ..life_gain_permission import (FixedLifeGainProhibitionSpec,LIFE_GAIN_PROHIBITION_CAPABILITY,
        LIFE_GAIN_PROHIBITION_HANDLER,LIFE_GAIN_PROHIBITION_EVENT)
    match = re.fullmatch(r"(?P<players>Players|Your opponents) can't gain life\.?",text.strip(),re.I)
    if match is None:
        return None
    relation = 'any' if match['players'].casefold() == 'players' else 'opponent'
    return (f'static-life-gain-prohibition-{relation}-v1',
        {'handler_id':LIFE_GAIN_PROHIBITION_HANDLER,'schema_version':1,'event':LIFE_GAIN_PROHIBITION_EVENT,
         'condition':FixedLifeGainProhibitionSpec(relation).to_dict()},LIFE_GAIN_PROHIBITION_CAPABILITY)


def static_life_handler(
    text: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    """Lower one closed static life-change replacement wording family."""

    if _DOUBLE_CONTROLLER_LIFE_GAIN.fullmatch(text) is None:
        return None
    return (
        "life-gain-double-controller-static-v1",
        {
            "handler_id": "replacement.life.gain.multiplier.v1",
            "schema_version": 1,
            "event": "life.change",
            "condition": {
                "affected_player_relation": "source_controller",
            },
            "modification": {"multiplier": 2},
        },
        "life.gain.replacement.static_multiplier",
    )


@dataclass(frozen=True, slots=True)
class FixedLifeEffectTemplate:
    template_id: str
    effect: Mapping[str, Any]
    target_schema: Mapping[str, Any] | None = None
    mechanic_ids: tuple[str, ...] = (LIFE_MECHANIC,)

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            (self.effect,),
            self.target_schema,
            self.mechanic_ids,
        )


def _player_target_schema(relation: str) -> dict[str, Any]:
    if relation not in {"any", "opponent"}:
        raise ValueError("Life-change player relation is unsupported")
    return {
        "zones": ["player"],
        "categories": ["player"],
        "player_relation": relation,
        "count": 1,
    }


def fixed_actual_life_transfer_template(text: str):
    from ..rules.actual_life_transfer import ACTUAL_LIFE_TRANSFER_OPERATION
    match=re.fullmatch(rf'each opponent loses (?P<count>{FIXED_COUNT_PATTERN}) life(?:, then|\. Then) you gain life equal to the life lost this way\.?',text,re.I)
    if match is None:return None
    count=fixed_number(match['count'])
    if count<=0:return None
    return FixedLifeEffectTemplate(template_id='actual-opponent-life-transfer-v1',
        effect={'op':ACTUAL_LIFE_TRANSFER_OPERATION,'schema_version':1,'player':'$controller','amount':count},
        mechanic_ids=(LIFE_MECHANIC,))


def fixed_life_effect_template(
    text: str,
) -> FixedLifeEffectTemplate | None:
    """Lower closed fixed controller, target-player, and opponent changes."""

    normalized = " ".join(text.strip().split())
    actual=fixed_actual_life_transfer_template(normalized)
    if actual is not None:return actual
    match = re.fullmatch(
        rf"target opponent loses (?P<count>{FIXED_COUNT_PATTERN}) life "
        rf"and you gain (?P<gain>{FIXED_COUNT_PATTERN}) life\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        count = fixed_number(match.group("count"))
        gain = fixed_number(match.group("gain"))
        if count <= 0 or gain != count:
            return None
        return FixedLifeEffectTemplate(
            template_id="drain-target-opponent-v1",
            effect={
                "op": "drain_opponent",
                "target": "$target.0",
                "amount": count,
            },
            target_schema=_player_target_schema("opponent"),
            mechanic_ids=(LIFE_MECHANIC, "cr-115-targets"),
        )
    match = re.fullmatch(
        rf"each opponent loses (?P<count>{FIXED_COUNT_PATTERN}) life "
        rf"and you gain (?P<gain>{FIXED_COUNT_PATTERN}) life\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        count = fixed_number(match.group("count"))
        gain = fixed_number(match.group("gain"))
        if count <= 0 or gain != count:
            return None
        return FixedLifeEffectTemplate(
            template_id="drain-each-opponent-v1",
            effect={"op": "drain_each_opponent", "amount": count},
            mechanic_ids=(
                LIFE_MECHANIC,
                "cr-101-the-magic-golden-rules",
            ),
        )
    match = re.fullmatch(
        rf"target player gains (?P<count>{FIXED_COUNT_PATTERN}) life\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        count = fixed_number(match.group("count"))
        if count <= 0:
            return None
        return FixedLifeEffectTemplate(
            template_id="gain-life-target-player-v1",
            effect={
                "op": _LIFE_OPERATION,
                "player": "$target.0",
                "delta": count,
            },
            target_schema=_player_target_schema("any"),
            mechanic_ids=(LIFE_MECHANIC, "cr-115-targets"),
        )
    match = re.fullmatch(
        rf"target (?P<relation>player|opponent) loses "
        rf"(?P<count>{FIXED_COUNT_PATTERN}) life\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        count = fixed_number(match.group("count"))
        if count <= 0:
            return None
        relation = match.group("relation").casefold()
        return FixedLifeEffectTemplate(
            template_id=f"lose-life-target-{relation}-v1",
            effect={
                "op": "lose_life",
                "player": "$target.0",
                "amount": count,
            },
            target_schema=_player_target_schema(
                "opponent" if relation == "opponent" else "any"
            ),
            mechanic_ids=(LIFE_MECHANIC, "cr-115-targets"),
        )
    match = re.fullmatch(
        rf"(?:you )?gain (?P<count>{FIXED_COUNT_PATTERN}) life\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        count = fixed_number(match.group("count"))
        if count <= 0:
            return None
        return FixedLifeEffectTemplate(
            template_id="gain-life-controller-v1",
            effect={
                "op": _LIFE_OPERATION,
                "player": "$controller",
                "delta": count,
            },
        )
    match = re.fullmatch(
        rf"(?:you )?lose (?P<count>{FIXED_COUNT_PATTERN}) life\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        count = fixed_number(match.group("count"))
        if count <= 0:
            return None
        return FixedLifeEffectTemplate(
            template_id="lose-life-controller-v1",
            effect={
                "op": "lose_life",
                "player": "$controller",
                "amount": count,
            },
        )
    match = re.fullmatch(
        rf"each (?P<relation>opponent|player) loses (?P<count>{FIXED_COUNT_PATTERN}) life\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        count = fixed_number(match.group("count"))
        if count <= 0:
            return None
        all_players = match.group("relation").casefold() == "player"
        return FixedLifeEffectTemplate(
            template_id="lose-life-each-player-v1" if all_players else "lose-life-each-opponent-v1",
            effect={
                "op": "lose_life" if all_players else "lose_life_each_opponent",
                **({"scope": "all_active_players"} if all_players else {}),
                "amount": count,
            },
            mechanic_ids=(
                LIFE_MECHANIC,
                "cr-101-the-magic-golden-rules",
            ),
        )
    return None


__all__ = [
    "LIFE_MECHANIC",
    "FixedLifeEffectTemplate",
    "fixed_life_effect_template",
    "static_life_handler",
]
