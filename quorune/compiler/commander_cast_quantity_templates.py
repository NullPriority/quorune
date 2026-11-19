from __future__ import annotations

"""Complete-consumption productions for commander-cast-scaled quantities."""

from collections.abc import Mapping
from dataclasses import replace

from . import regex_working_set as re
from ..query_effect_amount_model import CAST_X_AMOUNT_KIND, CastXAmountSpec
from ..characteristic_fragments import CharacteristicQuantityScope as Scope, CharacteristicQuantitySpec
from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC, ScalarAmountOrigin, ScalarEffectAmountSpec

_COMMANDER_COUNT = r"time you(?:['’]ve| have) cast (?:your|a) commander from the command zone(?: this game)?"


def commander_cast_quantity(text):
    if re.fullmatch(_COMMANDER_COUNT, text, re.I) is None:
        return None
    return CharacteristicQuantitySpec(scope=Scope.CONTROLLER_COMMANDER_CAST_COUNT, schema_version=8)


def commander_cast_scalar_template(text, *, source_name, compile_fixed):
    from .declared_effect_amounts import declared_effect_amount_template

    match = re.fullmatch(r"(?P<body>[^.]+?) for each " + _COMMANDER_COUNT +
        r"\.(?: \(They['’]re affected by summoning sickness\.\))?", text.strip(), re.I)
    if match is None:
        return None
    body = match["body"]
    if re.fullmatch(r"Create (?:a|an|one) .+? token(?: with .+)?", body, re.I):
        body = re.sub(r"^Create (?:a|an|one) ", "Create X ", body, flags=re.I)
        body = re.sub(r"\btoken\b", "tokens", body, count=1, flags=re.I)
    elif re.fullmatch(r".+? get(?:s)? \+1/\+1 until end of turn", body, re.I):
        body = body.replace("+1/+1", "+X/+X")
    else:
        return None
    fixed = declared_effect_amount_template(body + ".", source_name=source_name,
        compile_fixed=compile_fixed, cast_x_available=True)
    if fixed is None:
        return None
    producer = ScalarEffectAmountSpec(ScalarAmountOrigin.CONTROLLER_COMMANDER_CAST_COUNT)
    def project(value):
        if isinstance(value, Mapping):
            if value.get("kind") == CAST_X_AMOUNT_KIND:
                return replace(producer, coefficient=CastXAmountSpec.from_dict(value).coefficient).to_dict()
            return {key: project(child) for key, child in value.items()}
        if isinstance(value, (tuple, list)):
            return type(value)(project(child) for child in value)
        return value
    return fixed[0], tuple(project(effect) for effect in fixed[1]), fixed[2], tuple(dict.fromkeys(
        (*fixed[3], SCALAR_AMOUNT_MECHANIC)))
