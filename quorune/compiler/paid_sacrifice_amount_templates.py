from __future__ import annotations

"""Paid creature stats in independently closed spell result slots."""

from collections.abc import Mapping
import re

from ..query_effect_amount_model import CAST_X_AMOUNT_KIND, CastXAmountSpec
from ..scalar_effect_amount_model import (
    PAID_SACRIFICE_STAT_MECHANIC, SCALAR_AMOUNT_MECHANIC, ScalarAmountOrigin, ScalarEffectAmountSpec,
    scalar_amount_specs,
)
from .announced_target_sets import ANNOUNCED_TARGET_SET_MECHANIC
from .declared_effect_amounts import DECLARED_EFFECT_AMOUNT_MECHANIC, declared_effect_amount_template


_STAT = r"the sacrificed creature['’]s (?P<field>power|toughness)"
_DEFINITION = re.compile(rf'(?P<body>.+?), where X is (?:equal to )?{_STAT}\.', re.I)
_TWO_STATS = re.compile(
    r"You draw cards equal to the sacrificed creature['’]s power, then you gain life equal to "
    r"(?:its|the sacrificed creature['’]s) toughness\.", re.I,
)
_REWRITES = (
    (re.compile(rf'(?P<verb>deals?) damage equal to {_STAT} to ', re.I), r'\g<verb> X damage to '),
    (re.compile(rf'(?P<verb>deals?) damage to (?P<target>[^.]+?) equal to {_STAT}(?=\.)', re.I),
     r'\g<verb> X damage to \g<target>'),
    (re.compile(rf'(?P<verb>draws?) cards equal to {_STAT}', re.I), r'\g<verb> X cards'),
    (re.compile(rf'(?P<verb>gains?) life equal to {_STAT}', re.I), r'\g<verb> X life'),
)


def _stat_value(field: str, coefficient: int = 1):
    return ScalarEffectAmountSpec(ScalarAmountOrigin.PAID_SACRIFICE_CREATURE,
        characteristic=field, coefficient=coefficient, schema_version=9).to_dict()


def _single_stat_body(text: str) -> tuple[str, str] | None:
    definition = _DEFINITION.fullmatch(text)
    if definition is not None:
        return definition['body'] + '.', definition['field'].casefold()
    if re.search(r'\bX\b', text):
        return None
    fields = []
    for pattern, replacement in _REWRITES:
        def replace_stat(match):
            fields.append(match['field'].casefold())
            return match.expand(replacement)
        text = pattern.sub(replace_stat, text)
    if not fields or len(set(fields)) != 1:
        return None
    return text, fields[0]


def _replace_cast_x(value, field):
    if isinstance(value, Mapping):
        if value.get('kind') == CAST_X_AMOUNT_KIND:
            return _stat_value(field, CastXAmountSpec.from_dict(value).coefficient)
        return {key: _replace_cast_x(child, field) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_replace_cast_x(child, field) for child in value)
    return value


def paid_sacrifice_amount_template(text, *, source_name, compile_fixed):
    source = text.strip()
    if _TWO_STATS.fullmatch(source) is not None:
        template, effects, schema, mechanics = compile_fixed('You draw 2 cards, then you gain 3 life.')
        if template is None or schema is not None or len(effects) != 2:
            return None
        draw, life = effects
        if draw.get('op') != 'draw' or draw.get('count') != 2 or life.get('op') != 'life' or life.get('delta') != 3:
            return None
        effects = ({**draw, 'count': _stat_value('power')}, {**life, 'delta': _stat_value('toughness')})
    else:
        normalized = _single_stat_body(source)
        if normalized is None:
            return None
        body, field = normalized
        compiled = declared_effect_amount_template(body, source_name=source_name,
            compile_fixed=compile_fixed, cast_x_available=True)
        if compiled is None or ANNOUNCED_TARGET_SET_MECHANIC in compiled[3]:
            return None
        template, effects, schema, mechanics = compiled
        effects = tuple(_replace_cast_x(effect, field) for effect in effects)
        if not scalar_amount_specs(effects):
            return None
    owners = tuple(value for value in mechanics if value != DECLARED_EFFECT_AMOUNT_MECHANIC)
    return f'paid-sacrifice-{template}', effects, schema, (*owners, SCALAR_AMOUNT_MECHANIC, PAID_SACRIFICE_STAT_MECHANIC)
