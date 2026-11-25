"""Validate a full fixed-result projection before binding current life as X."""
from collections.abc import Mapping
import hashlib
from . import regex_working_set as re
from .declared_effect_amounts import declared_effect_amount_template, _result_slot
from ..query_effect_amount_model import CAST_X_AMOUNT_KIND, CastXAmountSpec
from ..current_life_effect_amount_model import (
    CURRENT_LIFE_AMOUNT_MECHANIC, CurrentLifeEffectAmountSpec,
)


_DEFINITION = re.compile(r', where X is (?:equal to )?your life total(?=\.|$)', re.I)


def current_life_effect_amount_template(text, *, source_name, compile_fixed):
    normalized = ' '.join(text.strip().split())
    if len(_DEFINITION.findall(normalized)) != 1:
        return None
    body = _DEFINITION.sub('', normalized)
    binding = 'life:' + hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:24]
    if re.fullmatch(r"This creature gets \+X/\+X until end of turn\.?", body, re.I):
        template, effects, schema, mechanics = compile_fixed(body.replace("+X", "+1").replace("+x", "+1"))
        if template is None or schema is not None or len(effects) != 1:
            return None
        effect, = effects
        if (effect.get("op") != "apply_source_characteristics_until_end_of_turn"
                or effect.get("card") != "$source.zone_object"
                or effect.get("power") != 1 or effect.get("toughness") != 1):
            return None
        value = CurrentLifeEffectAmountSpec(binding).to_dict()
        return (template, ({**effect, "power": value, "toughness": value},), schema,
                (*mechanics, CURRENT_LIFE_AMOUNT_MECHANIC))
    # The fixed one-target owner already binds this exact keyword follow-up.
    rider = re.fullmatch(r'Put X [^.]+ counters on target creature\. That creature gains [^.]+ until end of turn\.', body, re.I)
    if rider is not None:
        body = body.replace('. That creature gains ', '. It gains ')
    compiled = declared_effect_amount_template(body, source_name=source_name,
        compile_fixed=compile_fixed, cast_x_available=True)
    if compiled is None:
        return None
    replaced = 0

    def project(value, operation='', path=()):
        nonlocal replaced
        if isinstance(value, Mapping):
            if value.get('kind') == CAST_X_AMOUNT_KIND:
                if not _result_slot(operation, path):
                    raise ValueError('Current life cannot declare targets or costs')
                spec = CastXAmountSpec.from_dict(value)
                replaced += 1
                return CurrentLifeEffectAmountSpec(binding, spec.coefficient).to_dict()
            return {key: project(child, operation, (*path, key)) for key, child in value.items()}
        if isinstance(value, list):
            return [project(child, operation, path) for child in value]
        if isinstance(value, tuple):
            return tuple(project(child, operation, path) for child in value)
        return value

    try:
        # Result amounts never authorize target counts or selection prices.
        before = replaced
        project(compiled[2])
        if replaced != before:
            return None
        effects = tuple(project(effect, str(effect.get('op') or '')) for effect in compiled[1])
    except (TypeError, ValueError):
        return None
    if not replaced:
        return None
    if any(effect.get('op') not in {'create_token', 'place_counters', 'grant_keyword_until_end_of_turn'} for effect in effects):
        return None
    return compiled[0], effects, compiled[2], tuple(dict.fromkeys((*compiled[3], CURRENT_LIFE_AMOUNT_MECHANIC)))
