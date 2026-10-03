from __future__ import annotations

"""One closed blink grammar across existing spell, trigger and activation shells."""

import hashlib
import re
from typing import Any, Mapping

from ..linked_exile_return_model import LinkedExileReturnSpec, LINKED_EXILE_RETURN_MECHANIC, LINKED_EXILE_RETURN_OPERATION
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .direct_target import direct_permanent_target_spec
from .fixed_homogeneous_target_sets import _singularize_subject
from .fixed_target_effect_sequences import _keyword_list


_INSTRUCTION = re.compile(
    r"^Exile (?P<subject>.+?)(?:, then |\. (?:Then )?)"
    r"(?:(?P<early>At the beginning of (?:the next|your next) end step, )?)"
    r"[Rr]eturn (?P<reference>it|that card|those cards|them) to the battlefield"
    r"(?P<tapped> tapped)?(?: under (?P<controller>its owner's|their owner's|their owners'|your) control)?"
    r"(?: with (?P<count>a|one|two|three) (?P<counter>\+1/\+1|flying|vigilance|lifelink) counters? on (?:it|them))?"
    r"(?P<late> at the beginning of (?:the next|your next) end step)?\.?(?P<tail>.*)$",
    re.IGNORECASE,
)
_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def scope_linked_return_bindings(value: Any, scope: str) -> Any:
    """Separate identical instructions in independently selected modes."""
    if isinstance(value, Mapping):
        result = {key: scope_linked_return_bindings(child, scope) for key, child in value.items()}
        if result.get("op") == LINKED_EXILE_RETURN_OPERATION:
            identity = result.get("binding_id")
            if type(identity) is not str or not identity.startswith("blink:"):
                raise ValueError("Linked instruction scope requires a valid binding")
            result["binding_id"] = "blink:" + hashlib.sha256((scope + ":" + identity).encode("utf-8")).hexdigest()[:24]
        return result
    if isinstance(value, (list, tuple)):
        return [scope_linked_return_bindings(child, scope) for child in value]
    return value


def linked_exile_return_effect_template(text: str, *, card_name: str,
                                      source_is_permanent: bool | None = None) -> tuple[str, tuple[Mapping[str, Any], ...], Mapping[str, Any] | None, tuple[str, ...]] | None:
    normalized = " ".join(text.strip().removeprefix("• ").split())
    if normalized.casefold().startswith("you may "):
        inner = linked_exile_return_effect_template(normalized[8:], card_name=card_name, source_is_permanent=source_is_permanent)
        if inner is None:
            return None
        template, effects, schema, mechanics = inner
        # Both phases belong to one optional instruction, not two decisions.
        wrapper = {"op": "offer_optional_effect", "player": "$controller",
                   "effects": [dict(effect) for effect in effects]}
        return template, (wrapper,), schema, ("fixed-optional-effect-choice", *mechanics)
    match = _INSTRUCTION.fullmatch(normalized)
    if match is None or match["early"] and match["late"]:
        return None
    subject = match["subject"]
    self_type = source_self_permanent_type(subject)
    self_subject = self_type is not None or SourceReferenceSpec(card_name).matches(subject)
    schema = None
    cards: Any = "$source.zone_object"
    if self_subject:
        if source_is_permanent is not True or match["reference"].casefold() not in {"it", "that card"}:
            return None
    else:
        # Cardinality and source exclusion are independent qualifiers. Capture
        # both before canonicalizing an "other" subject to "another target".
        cardinality = re.fullmatch(r"(?P<count>up to (?:one|two|three|four|five|six)|two|three|four|five|six|any number of) (?P<other>other )?target (?P<quality>.+)", subject, re.I)
        if cardinality:
            count_word = cardinality["count"].casefold()
            minimum = 0 if count_word.startswith(("up to ", "any number")) else _COUNTS[count_word]
            maximum = 999 if count_word == "any number of" else _COUNTS[count_word.removeprefix("up to ")]
            subject = ("another target " if cardinality["other"] else "target ") + _singularize_subject(cardinality["quality"])
        else:
            minimum = maximum = 1
        target = direct_permanent_target_spec(subject)
        if target is None:
            return None
        schema = target.to_target_schema()
        if minimum == 0:
            schema.pop("count")
            schema["up_to"] = maximum
        else:
            schema["count"] = maximum
        cards = "$targets"
    timing_text = match["early"] or match["late"] or ""
    timing = "controller_next_end_step" if "your next" in timing_text.casefold() else "next_end_step" if timing_text else "immediate"
    tail = match["tail"].strip()
    keywords: tuple[str, ...] = ()
    if tail:
        keyword = re.fullmatch(r"(?:It|That creature) gains (?P<keywords>.+?) until end of turn\.", tail, re.I)
        if keyword is None:
            return None
        keywords = _keyword_list(keyword["keywords"]) or ()
        if not keywords:
            return None
    try:
        spec = LinkedExileReturnSpec(timing=timing, controller="actor" if match["controller"] == "your" else "owner",
                                    tapped=bool(match["tapped"]), keywords=keywords,
                                    entry_counters=((match["counter"].casefold(), {"a":1,"one":1,"two":2,"three":3}[match["count"].casefold()]),) if match["counter"] else ())
    except ValueError:
        return None
    binding = "blink:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    effects = (
        {"op": LINKED_EXILE_RETURN_OPERATION, "phase": "exile", "cards": cards, "binding_id": binding, "spec": spec.to_dict()},
        {"op": LINKED_EXILE_RETURN_OPERATION, "phase": "return", "binding_id": binding, "spec": spec.to_dict()},
    )
    return "linked-exile-return-v1", effects, schema, (LINKED_EXILE_RETURN_MECHANIC, *(("cr-115-targets",) if schema else ()))
