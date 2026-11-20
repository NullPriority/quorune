"""Closed keyword-qualified sacrifice data; existing zone owner pays the cost."""
from .object_predicate import ObjectQuerySpec

KEYWORD_SACRIFICE_COST_VERSION = 8
KEYWORD_SACRIFICE_CHOICE_FIELD = 'sacrifice_schema_version'
KEYWORD_SACRIFICE_CAPABILITY = 'activation.selected_zone_change.fixed_keyword'


def keyword_sacrifice_predicate(keyword):
    if type(keyword) is not str or keyword != 'defender':
        raise ValueError('Keyword sacrifice requires a represented canonical creature keyword')
    return ObjectQuerySpec(zones=('battlefield',),controller='$actor',
        known_to_actor=True,types_all=('creature',),keywords_all=(keyword,))


def validate_keyword_sacrifice_cost(cost):
    query=cost.predicate
    keyword=query.keywords_all[0] if len(query.keywords_all)==1 else None
    if (type(cost.schema_version) is not int or cost.schema_version!=KEYWORD_SACRIFICE_COST_VERSION
            or cost.operation!='sacrifice_one' or type(cost.count) is not int or cost.count!=1
            or cost.unblocked_attacker is not False or cost.count_parameter is not None
            or cost.predicate_union is not None or query!=keyword_sacrifice_predicate(keyword)):
        raise ValueError('Keyword sacrifice requires one current controlled defender creature and its explicit version')
