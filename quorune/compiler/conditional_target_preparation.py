"""Consume conditional bonuses separately from their unconditional fight or bite."""
from copy import deepcopy
from . import regex_working_set as re

from ..conditional_target_preparation import PreparationCondition, CONDITIONAL_PREPARATION_MECHANIC
from ..continuous_conditions import FixedPublicStateConditionSpec, FixedPublicStateConditionKind
from ..conditional_target_preparation_model import RESOLUTION_TARGET_COLOR_FIELD, RESOLUTION_TARGET_COLOR_MECHANIC
from ..object_predicate import ObjectQuerySpec
from ..target_numeric import TargetNumericCharacteristicSpec, TargetNumericCharacteristic, TargetNumericComparison
from .creature_power_damage_templates import fixed_creature_power_damage_effect_template, _REMINDER
from .public_state_queries import fixed_public_state_condition


_TARGET_COLOR_RESULT = re.compile(
    r"(?P<result>Counter target spell|Destroy target permanent) if it(?:['’]s| is) "
    r"(?P<color>white|blue|black|red|green)\.?",
    re.IGNORECASE,
)


def fixed_target_color_result_template(text):
    """Lower the direct target and retain the separate current-color condition."""
    from ..qualified_hexproof import HEXPROOF_COLORS
    from .counter_templates import targeted_counter_effect_template
    from .destruction_templates import targeted_destruction_effect_template

    match = _TARGET_COLOR_RESULT.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    result = match["result"]
    compile_result = (targeted_counter_effect_template if result.casefold().startswith("counter ")
                      else targeted_destruction_effect_template)
    primitive = compile_result(result + ".")
    opcode, = primitive.effects
    condition = {"schema_version": 1, "color": HEXPROOF_COLORS[match["color"].casefold()]}
    return (
        f"{primitive.template_id}-current-color-v1",
        ({**opcode, RESOLUTION_TARGET_COLOR_FIELD: condition},),
        primitive.target_schema,
        (RESOLUTION_TARGET_COLOR_MECHANIC, *primitive.mechanics),
    )


def preparation_predicate(text):
    if text.casefold() in {"it's night", 'it is night'}:return PreparationCondition('night')
    numeric = re.fullmatch(r"(?:its power is|it has power) (?P<number>[0-9]+)(?P<bound> or greater)?", text, re.I)
    if numeric is not None:
        comparisons = (TargetNumericComparison.AT_LEAST,) if numeric['bound'] else (TargetNumericComparison.AT_LEAST, TargetNumericComparison.AT_MOST)
        terms = tuple(FixedPublicStateConditionSpec(FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
            predicate=ObjectQuerySpec(zones=('battlefield',), types_all=('creature',),
                numeric_characteristic=TargetNumericCharacteristicSpec(TargetNumericCharacteristic.POWER, comparison, int(numeric['number']))),
            schema_version=2) for comparison in comparisons)
        condition = terms[0] if len(terms) == 1 else FixedPublicStateConditionSpec(FixedPublicStateConditionKind.ALL, terms=terms, schema_version=6)
        return PreparationCondition('target', condition)
    condition = fixed_public_state_condition(text, source_name='Preparation target')
    if condition is None:return None
    try:
        return PreparationCondition('target' if condition.kind is FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY else 'controller', condition)
    except ValueError:return None


def _preparation_branches(text):
    replacement = re.fullmatch(r'(?P<default>Target creature you control gets [^.]+)\. (?P<result>Put a \+1/\+1 counter on it) instead if (?P<condition>[^.]+)\.', text, re.I)
    if replacement is not None:
        return replacement['condition'], replacement['result'].replace('on it', 'on target creature you control') + '.', replacement['default'] + '.'
    leading = re.fullmatch(r'If (?P<condition>.+?), (?P<result>[^.]+)\.', text, re.I)
    suffix = re.fullmatch(r'(?P<result>[^.]+) if (?P<condition>[^.]+)\.', text, re.I)
    match = leading or suffix
    if match is None:return None
    return match['condition'], match['result'] + '.', None


def conditional_prepared_power_template(text, *, source_name, compile_atomic):
    text = re.sub(r"^[A-Z][A-Za-z' ]{0,80} — ", '', _REMINDER.sub('', text.strip()))
    parts = re.fullmatch(r'(?P<before>.+)\. Then (?P<last>[^.]+)\.', text, re.I)
    if parts is None:return None
    before = parts['before'] + '.';last = parts['last']
    choose = re.match(r"Choose target creature you control and target creature (?P<relation>you don't control|an opponent controls)\. ", before, re.I)
    if choose is not None:
        before = before[choose.end():]
        if not re.fullmatch(r'(?:those|the chosen) creatures fight each other', last, re.I):return None
        result = 'Target creature you control fights target creature ' + choose['relation'] + '.'
    elif re.match(r'(?:it|that creature) (?:fights|deals damage equal to its power to) ', last, re.I):
        result = re.sub(r'^(?:it|that creature)', 'Target creature you control', last, flags=re.I) + '.'
    else:return None
    damage = fixed_creature_power_damage_effect_template(result, card_name=source_name)
    branches = _preparation_branches(before)
    if damage is None or branches is None:return None
    condition_text, positive, otherwise = branches
    predicate = preparation_predicate(condition_text)
    if predicate is None:return None
    def compile_branch(body):
        body = re.sub(r'\b(?:the chosen creature you control|the creature you control)\b', 'target creature you control', body, flags=re.I)
        return compile_atomic(body)
    prepared = compile_branch(positive)
    alternate = compile_branch(otherwise) if otherwise else (None, (), None, ())
    first = dict(damage.target_schema['groups'][0]);first.pop('id')
    if prepared[0] is None or prepared[2] != first or otherwise and (alternate[0] is None or alternate[2] != first):return None
    wrapper = {'op': 'apply_if_public_condition', 'schema_version': 16, 'player': '$controller',
        'condition': predicate.to_dict(), 'condition_target': '$target.0', 'target_binding': first,
        'effects': deepcopy(list(prepared[1])), 'mechanic_ids': list(prepared[3]), 'prefix_mechanic_ids': [],
        'otherwise_effects': deepcopy(list(alternate[1])), 'otherwise_mechanic_ids': list(alternate[3])}
    effects = (wrapper, *damage.effects)
    mechanics = tuple(sorted({CONDITIONAL_PREPARATION_MECHANIC, 'fixed-resolution-public-condition', *prepared[3], *alternate[3], *damage.mechanics}))
    from ..rules.conditional_target_preparation import conditional_prepared_power_capabilities
    if not conditional_prepared_power_capabilities(effects=effects, target_schema=damage.target_schema, mechanic_ids=mechanics):return None
    return 'conditional-target-power-preparation-v1', effects, damage.target_schema, mechanics
