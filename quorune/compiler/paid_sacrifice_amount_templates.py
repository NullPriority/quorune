from __future__ import annotations

"""Facts about a single paid sacrifice in closed spell result slots."""

from collections.abc import Mapping
import re

from ..query_effect_amount_model import CAST_X_AMOUNT_KIND, CastXAmountSpec
from ..rules.casting_additional_costs import fixed_single_sacrifice_price
from ..scalar_effect_amount_model import (
    PAID_SACRIFICE_MANA_VALUE_MECHANIC, PAID_SACRIFICE_STAT_MECHANIC,
    SCALAR_AMOUNT_MECHANIC, ScalarAmountOrigin, ScalarEffectAmountSpec,
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
_PAID_PERMANENT = r'the sacrificed (?P<permanent_type>artifact|creature|permanent)'
_MANA_VALUE = rf"{_PAID_PERMANENT}['’]s mana value"
_MANA_VALUE_REWRITES = (
    (re.compile(rf'Draw cards equal to {_MANA_VALUE}\.', re.I), 'Draw X cards.'),
    (re.compile(rf'Draw cards equal to the mana value of {_PAID_PERMANENT}\.', re.I), 'Draw X cards.'),
    (re.compile(rf'You gain life equal to {_MANA_VALUE}\.(?P<tail> Draw [^.]+\.)?', re.I),
     r'You gain X life.\g<tail>'),
    (re.compile(rf'(?P<body>Put X \+1/\+1 counters on target creature), where X is {_MANA_VALUE}\.', re.I),
     r'\g<body>.'),
)
_MANA_VALUE_OUTPUT = re.compile(rf'Add an amount of (?P<symbol>\{{[WUBRGC]\}}) equal to {_MANA_VALUE}\.', re.I)


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


def _replace_cast_x(value, scalar):
    if isinstance(value, Mapping):
        if value.get('kind') == CAST_X_AMOUNT_KIND:
            return scalar(CastXAmountSpec.from_dict(value).coefficient)
        return {key: _replace_cast_x(child, scalar) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_replace_cast_x(child, scalar) for child in value)
    return value


def _paid_sacrifice_stat_template(source, *, source_name, compile_fixed):
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
        effects = tuple(_replace_cast_x(effect, lambda coefficient: _stat_value(field, coefficient))
                        for effect in effects)
        if not scalar_amount_specs(effects):
            return None
    owners = tuple(value for value in mechanics if value != DECLARED_EFFECT_AMOUNT_MECHANIC)
    return f'paid-sacrifice-{template}', effects, schema, (*owners, SCALAR_AMOUNT_MECHANIC, PAID_SACRIFICE_STAT_MECHANIC)


def _matches_paid_type(match, sacrifice_cost):
    subject = match['permanent_type'].casefold()
    return subject == 'permanent' or sacrifice_cost.predicate.types_any == (subject,)


def _mana_value_scalar(coefficient=1):
    return ScalarEffectAmountSpec(
        ScalarAmountOrigin.PAID_SACRIFICE_MANA_VALUE,
        characteristic='mana_value', coefficient=coefficient, schema_version=12,
    ).to_dict()


def _paid_sacrifice_mana_value_template(source, *, sacrifice_cost, source_name, compile_fixed):
    if (match := _MANA_VALUE_OUTPUT.fullmatch(source)) is not None:
        if not _matches_paid_type(match, sacrifice_cost):
            return None
        from .fixed_resolution_mana import fixed_resolution_mana_effect_template

        template, (effect,), schema, mechanics = fixed_resolution_mana_effect_template(
            f"Add {match['symbol'].upper()}."
        )
        effects = ({**effect, 'amount': _mana_value_scalar()},)
    else:
        for pattern, replacement in _MANA_VALUE_REWRITES:
            match = pattern.fullmatch(source)
            if match is not None:
                break
        else:
            return None
        if not _matches_paid_type(match, sacrifice_cost):
            return None
        compiled = declared_effect_amount_template(
            match.expand(replacement), source_name=source_name,
            compile_fixed=compile_fixed, cast_x_available=True,
        )
        if compiled is None or ANNOUNCED_TARGET_SET_MECHANIC in compiled[3]:
            return None
        template, effects, schema, mechanics = compiled
        effects = tuple(_replace_cast_x(effect, _mana_value_scalar) for effect in effects)
        if len(scalar_amount_specs(effects)) != 1:
            return None
    owners = tuple(value for value in mechanics if value != DECLARED_EFFECT_AMOUNT_MECHANIC)
    return (
        f'paid-sacrifice-{template}', effects, schema,
        (*owners, SCALAR_AMOUNT_MECHANIC, PAID_SACRIFICE_MANA_VALUE_MECHANIC),
    )


def paid_sacrifice_amount_template(text, *, cost_schema, source_name, compile_fixed):
    sacrifice_cost = fixed_single_sacrifice_price(cost_schema)
    if sacrifice_cost is None:
        return None
    source = text.strip()
    value = _paid_sacrifice_mana_value_template(
        source, sacrifice_cost=sacrifice_cost, source_name=source_name, compile_fixed=compile_fixed,
    )
    if value is not None or sacrifice_cost.predicate.types_any != ('creature',):
        return value
    return _paid_sacrifice_stat_template(source, source_name=source_name, compile_fixed=compile_fixed)
