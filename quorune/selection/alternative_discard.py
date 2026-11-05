"""Alternative discard legality shared by private offers and submissions."""
from ..alternative_discard_model import AlternativeDiscardCountSpec,alternative_discard_count
from ..errors import GameRuleError


def decoded_alternative_discard_count(value):
    try:return AlternativeDiscardCountSpec.from_dict(value)
    except (TypeError,ValueError) as exc:raise GameRuleError(str(exc)) from exc


def alternative_discard_options(host,effect,seat,options):
    if not alternative_discard_count(effect.get('count')):return None
    spec=decoded_alternative_discard_count(effect['count'])
    matching=set()
    for query in spec.qualifying_queries:
        matching.update(host._choice_options(seat,{**effect,'predicate':query.to_dict()}))
    matching=tuple(ref for ref in options if ref in matching)
    required=min(spec.fallback_count,len(options))
    rows=[{'id':'normal','label':f'Discard {required} card(s)',
        'choice_schema':{'cards':{'legal_refs':list(options),'minimum':required,'maximum':required}}}]
    if matching:
        rows.append({'id':'matching','label':'Discard one matching card',
            'choice_schema':{'cards':{'legal_refs':list(matching),'minimum':1,'maximum':1}}})
    return rows


def validate_alternative_discard_choice(host,effect,seat,options,refs,mode=None):
    rows=alternative_discard_options(host,effect,seat,options)
    if rows is None:return False
    if mode is not None and (type(mode) is not str or mode not in {r['id'] for r in rows}):
        raise GameRuleError('Alternative discard option is unavailable')
    for row in rows:
        if mode is not None and row['id']!=mode:continue
        schema=row['choice_schema']['cards']
        if len(refs)==schema['minimum'] and set(refs)<=set(schema['legal_refs']):return True
    raise GameRuleError('Discard the fallback subset or one matching card')
