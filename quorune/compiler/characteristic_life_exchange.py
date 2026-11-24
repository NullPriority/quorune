"""Parse one source-bound numeric life exchange, preserving its player role."""

from __future__ import annotations

from ..rules.characteristic_life_exchange import CharacteristicLifeExchangeSpec
from ..rules.source_references import SourceReferenceSpec
from . import regex_working_set as re


def characteristic_life_exchange_template(text, *, source_name):
    source = SourceReferenceSpec(source_name).regex_pattern
    match = re.fullmatch(
        r"Exchange (?P<recipient>your life total|target opponent['’]s life total) with "
        rf"(?:{source}|this creature)['’]s (?P<characteristic>power|toughness)\.",
        text.strip(), re.IGNORECASE,
    )
    if match is None:
        return None
    characteristic = match["characteristic"].casefold()
    opponent = match["recipient"].casefold().startswith("target")
    if opponent and characteristic != "toughness":
        return None
    spec = CharacteristicLifeExchangeSpec(characteristic, "target_opponent" if opponent else "controller")
    return spec.to_instruction(), spec.to_target_schema()


def compiled_characteristic_life_exchange(text, *, source_name):
    result = characteristic_life_exchange_template(text, source_name=source_name)
    if result is None:
        return None
    effect, targets = result
    return (
        "activated-characteristic-life-exchange-v1", (effect,), targets,
        ("cr-119-life", *(("cr-115-targets",) if targets is not None else ())),
    )
