"""Complete alternative-quality discard instructions and preceding draw."""
from dataclasses import replace
import re
from ..alternative_discard_model import AlternativeDiscardCountSpec,ALTERNATIVE_DISCARD_MECHANIC
from ..object_predicate import ObjectQuerySpec
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from .affected_player_discard_templates import FixedAffectedPlayerDiscardTemplate,AffectedPlayerDiscardSubject
from .draw_templates import fixed_draw_effect_template
from .fixed_library_selection_templates import _term_predicates


def alternative_discard_quality(phrase):
    queries=[]
    for term in re.split(r'\s+or\s+',phrase,flags=re.I):
        keyword=None
        match=re.fullmatch(r'(?P<base>.+? card) with (?P<keyword>[A-Za-z ]+)',term,re.I)
        if match:
            keywords={word.casefold():word for word in FIXED_CHARACTERISTIC_KEYWORDS}
            keyword=keywords.get(match['keyword'].casefold())
            if keyword is None:return None
            term=match['base']
        predicates=_term_predicates(term)
        if predicates is None:return None
        for raw in predicates:
            query=replace(ObjectQuerySpec.from_dict(raw),zones=('hand',),token=False,
                **({'keywords_all':(keyword,)} if keyword else {}))
            if query not in queries:queries.append(query)
    try:return AlternativeDiscardCountSpec(tuple(queries))
    except (TypeError,ValueError):return None


def _discard(text):
    match=re.fullmatch(r'(?:(?P<subject>You|Target player|Target opponent|Each player|Each opponent) )?'
        r'discards? two cards unless (?P<who>you|they|that player) discards? (?P<quality>.+?)\.?',text,re.I)
    if match is None:return None
    subject=(match['subject'] or 'you').casefold()
    if (subject=='you') != (match['who'].casefold()=='you'):return None
    spec=alternative_discard_quality(match['quality'])
    if spec is None:return None
    member={'you':AffectedPlayerDiscardSubject.CONTROLLER,'target player':AffectedPlayerDiscardSubject.TARGET_PLAYER,
        'target opponent':AffectedPlayerDiscardSubject.TARGET_OPPONENT,'each player':AffectedPlayerDiscardSubject.EACH_PLAYER,
        'each opponent':AffectedPlayerDiscardSubject.EACH_OPPONENT}[subject]
    base=FixedAffectedPlayerDiscardTemplate(member,2)
    return ('alternative-quality-discard-v1',({**base.effects[0],'count':spec.to_dict()},),base.target_schema,
        (*base.mechanics,ALTERNATIVE_DISCARD_MECHANIC))


def alternative_discard_effect_template(text):
    normalized=' '.join(text.strip().split())
    standalone=_discard(normalized)
    if standalone is not None:return standalone
    match=re.fullmatch(r'(?P<draw>.+? draws? (?:a|one|two|three|four|[1-4]) cards?|'
        r'draw (?:a|one|two|three|four|[1-4]) cards?)[.,] (?:Then )?(?P<discard>.+)',normalized,re.I)
    if match is None:return None
    draw=fixed_draw_effect_template(match['draw']+'.')
    if draw is None or len(draw[1])!=1 or draw[1][0].get('op')!='draw':return None
    tail=match['discard']
    if draw[2] is not None:
        if re.match(r'(?:that player|they) discards ',tail,re.I) is None:return None
        relation='target opponent' if draw[2].get('player_relation')=='opponent' else 'target player'
        tail=re.sub(r'^(?:that player|they)',relation,tail,count=1,flags=re.I)
    elif re.match(r'(?:that player|they) ',tail,re.I):return None
    discard=_discard(tail)
    if discard is None or discard[2]!=draw[2]:return None
    if discard[1][0]['players']!=[draw[1][0]['player']]:return None
    return 'draw-then-alternative-quality-discard-v1',(*draw[1],*discard[1]),draw[2],tuple(dict.fromkeys((*draw[3],*discard[3])))
