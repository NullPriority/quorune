"""Complete independent per-player public-count life-loss clauses."""
from . import regex_working_set as re
from .fixed_numbers import FIXED_COUNT_PATTERN,fixed_number
from .query_characteristic_templates import query_characteristic_quantity
from ..characteristic_fragments import CharacteristicQuantitySpec, CharacteristicQuantityScope
from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec
from ..query_effect_amount_model import PublicQueryAmountSpec
from ..per_player_query_life_model import (
    PerPlayerQueryLifeLossSpec, PlayerLifeLossRelation, PER_PLAYER_QUERY_LIFE_MECHANIC,
    OPPONENT_SUBTYPE_ABSENCE_LIFE_VERSION, OPPONENT_POISON_THRESHOLD_LIFE_VERSION,
    PoisonCounterThresholdLifeLoss,
)


_OPPONENT_SUBTYPE_ABSENCE = re.compile(
    r"each opponent who doesn['’]t control an? (?P<subtype>[\w'’ -]+) loses 1 life\.", re.I,
)


def source_attack_opponent_subtype_absence_life_result(binding, body):
    material = body.strip()
    if (binding.variant != 'source_attacks'
            or material.casefold() != getattr(binding, 'body', '').strip().casefold()):
        return None
    match = _OPPONENT_SUBTYPE_ABSENCE.fullmatch(material)
    subtype = canonical_creature_subtype(match['subtype']) if match is not None else None
    if subtype is None:
        return None
    quantity = CharacteristicQuantitySpec(
        scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
        query=ObjectQuerySpec(zones=('battlefield',), subtypes_all=(subtype,)),
    )
    spec = PerPlayerQueryLifeLossSpec(
        PublicQueryAmountSpec(quantity, 1), PlayerLifeLossRelation.OPPONENTS,
        schema_version=OPPONENT_SUBTYPE_ABSENCE_LIFE_VERSION,
    )
    effect = {'op': 'lose_life_each_opponent', 'amount': spec.to_dict()}
    return ('per-player-opponent-subtype-absence-life-loss-v1', (effect,), None,
        (PER_PLAYER_QUERY_LIFE_MECHANIC, 'cr-119-life', 'cr-101-the-magic-golden-rules'))


_OPPONENT_POISON_THRESHOLD = re.compile(
    rf"Each opponent who has (?P<minimum>{FIXED_COUNT_PATTERN}) or more poison counters "
    rf"loses (?P<life>{FIXED_COUNT_PATTERN}) life\.", re.IGNORECASE,
)


def source_tap_opponent_poison_threshold_life_result(binding, body):
    if (binding.variant != 'source_public_tap_state_transition'
            or binding.event != 'permanent.tap'
            or binding.event_condition != {'field': 'card', 'op': 'eq', 'value': '$source.ref'}):
        return None
    match = _OPPONENT_POISON_THRESHOLD.fullmatch(body.strip())
    if match is None:
        return None
    minimum = fixed_number(match['minimum'])
    loss = fixed_number(match['life'])
    if minimum <= 0 or loss <= 0:
        return None
    spec = PerPlayerQueryLifeLossSpec(
        PoisonCounterThresholdLifeLoss(minimum, loss), PlayerLifeLossRelation.OPPONENTS,
        schema_version=OPPONENT_POISON_THRESHOLD_LIFE_VERSION,
    )
    effect = {'op': 'lose_life_each_opponent', 'amount': spec.to_dict()}
    return ('per-opponent-poison-threshold-life-loss-v1', (effect,), None,
            (PER_PLAYER_QUERY_LIFE_MECHANIC, 'cr-119-life', 'cr-101-the-magic-golden-rules'))


def per_player_query_life_template(text,*,source_name,compile_fixed):
    match=re.fullmatch(rf'Each (?P<players>player|opponent) loses (?P<n>{FIXED_COUNT_PATTERN}) life for each '
        r'(?P<objects>.+?) they control\.',text.strip(),re.I)
    if match is None:return None
    coefficient=fixed_number(match['n'])
    if coefficient<=0:return None
    quantity=query_characteristic_quantity(match['objects']+' you control',source_name=source_name)
    if quantity is None:return None
    relation=PlayerLifeLossRelation.ALL if match['players'].casefold()=='player' else PlayerLifeLossRelation.OPPONENTS
    try:spec=PerPlayerQueryLifeLossSpec(PublicQueryAmountSpec(quantity,coefficient),relation)
    except (TypeError,ValueError):return None
    compiled=compile_fixed('Each '+match['players']+' loses 1 life.')
    if compiled[0] is None or len(compiled[1])!=1 or compiled[2] is not None:return None
    effect=dict(compiled[1][0])
    if effect.get('op') not in {'lose_life','lose_life_each_opponent'} or effect.get('amount')!=1:return None
    effect['amount']=spec.to_dict()
    return 'per-player-public-query-life-loss-v1',(effect,),None,tuple(dict.fromkeys((PER_PLAYER_QUERY_LIFE_MECHANIC,*compiled[3])))
