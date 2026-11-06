from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..fractional_quantity_model import HALF_MILL_MECHANIC, HalfQuantitySpec
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


MILL_MECHANIC_ID = "mill"
MILL_CAPABILITY_ID = "zone.mill.fixed"
_LARGE_NUMBER_WORDS = {
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
_MILL_COUNT_PATTERN = (
    rf"{FIXED_COUNT_PATTERN}|" + "|".join(_LARGE_NUMBER_WORDS)
)
_MILL_SUBJECT = r"(?:(?P<subject>you|target player|target opponent|each player|each opponent) )?"
_FIXED_MILL = re.compile(
    _MILL_SUBJECT
    + rf"(?P<verb>mill|mills) (?P<count>{_MILL_COUNT_PATTERN}) "
    rf"(?P<card_word>cards?)\.?$",
    re.IGNORECASE,
)
_HALF_MILL = re.compile(
    _MILL_SUBJECT + r"(?P<verb>mill|mills) half (?P<possessive>your|their|his or her) library, "
    r"rounded (?P<rounding>up|down)\.?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FixedMillEffectTemplate:
    template_id: str
    effect: Mapping[str, Any]
    target_schema: Mapping[str, Any] | None = None
    mechanic_ids: tuple[str, ...] = (MILL_MECHANIC_ID,)

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


def _mill_count(value: str) -> int:
    normalized = value.casefold()
    return (
        _LARGE_NUMBER_WORDS[normalized]
        if normalized in _LARGE_NUMBER_WORDS
        else fixed_number(normalized)
    )


def _target_schema(relation: str) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
    }
    if relation == "opponent":
        schema["player_relation"] = "opponent"
    return schema


def _mill_template(subject, count, *, count_kind="fixed", extra_mechanics=()):
    mechanics = (MILL_MECHANIC_ID, *extra_mechanics)
    if subject in {"each player", "each opponent"}:
        return FixedMillEffectTemplate(
            template_id=f"mill-{count_kind}-{subject.replace(' ', '-')}-v1",
            effect={"op": "mill", "scope": "all_active_players" if subject == "each player" else "controller_opponents", "count": count},
            mechanic_ids=mechanics,
        )
    targeted = subject.startswith("target ")
    relation = "opponent" if subject == "target opponent" else "any"
    suffix = f"target-{relation}" if targeted else "controller"
    return FixedMillEffectTemplate(
        template_id=f"mill-{count_kind}-{suffix}-v1",
        effect={"op": "mill", "player": "$target.0" if targeted else "$controller", "count": count},
        target_schema=_target_schema(relation) if targeted else None,
        mechanic_ids=(*mechanics, "cr-115-targets") if targeted else mechanics,
    )


def fixed_mill_effect_template(text: str) -> FixedMillEffectTemplate | None:
    """Lower a mandatory fixed or explicitly rounded half-library Mill."""
    fixed = _FIXED_MILL.fullmatch(text.strip())
    match = fixed or _HALF_MILL.fullmatch(text.strip())
    if match is None:
        return None
    subject = str(match.group("subject") or "controller").casefold()
    controller = subject in {"controller", "you"}
    if controller != (match["verb"].casefold() == "mill"):
        return None
    if fixed is not None:
        count = _mill_count(match["count"])
        if (count == 1) != (match["card_word"].casefold() == "card"):
            return None
        return _mill_template(subject, count)
    if controller != (match["possessive"].casefold() == "your"):
        return None
    half = HalfQuantitySpec(match["rounding"].casefold())
    return _mill_template(subject, half.to_dict(), count_kind="half", extra_mechanics=(HALF_MILL_MECHANIC,))


__all__ = [
    "fixed_mill_effect_template",
    "FixedMillEffectTemplate",
    "MILL_CAPABILITY_ID",
    "MILL_MECHANIC_ID",
]


EVENT_MILL_RECIPIENT_CAPABILITY='zone.mill.event_recipient'
EVENT_MILL_RECIPIENT_MECHANIC='fixed-event-recipient-mill'


def event_recipient_mill_result(binding,body,*,compile_fixed,source_name):
    """One mandatory mill bound to an independently guaranteed event player."""
    match=re.fullmatch(r'(?P<subject>that player|they|defending player) mills? (?P<body>.+)',body,re.I)
    if match is None:return None
    player=None
    if match['subject'].casefold()=='defending player':
        from .event_player_results import _event_player
        player=_event_player(binding,'defending player')
    elif binding.event.value in {'damage.dealt','damage.dealt.self'}:
        def guarantees_player(condition):
            return isinstance(condition,Mapping) and (condition=={'field':'target_kind','op':'eq','value':'player'}
                or any(guarantees_player(child) for child in condition.get('all',())))
        if guarantees_player(binding.event_condition):player='$context.target'
    elif binding.event.value=='spell.cast':player='$context.player'
    if player is None:return None
    amount = match["body"]
    if re.match(r"^half\b", amount, re.IGNORECASE):
        half = re.fullmatch(r"half (?:their|his or her) library, rounded (up|down)\.?", amount, re.IGNORECASE)
        if half is None:
            return None
        amount = f"half your library, rounded {half[1]}."
    normalized='Mill '+amount
    from .scalar_effect_amounts import scalar_effect_amount_template
    scalar=scalar_effect_amount_template(normalized,source_name=source_name,event=binding.event.value,
        source_event=binding.event.value.endswith('.self'),compile_fixed=compile_fixed)
    fixed=fixed_mill_effect_template(normalized)
    compiled=scalar or (fixed.compiled() if fixed is not None else None)
    if compiled is None or compiled[2] is not None or len(compiled[1])!=1:return None
    effect=compiled[1][0]
    if effect.get('op')!='mill' or effect.get('player')!='$controller':return None
    return ('fixed-event-recipient-mill-v1',({**effect,'player':player},),None,
        (*compiled[3],EVENT_MILL_RECIPIENT_MECHANIC)),False
