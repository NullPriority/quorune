"""Whole-clause colored hand reveal, preserving ordinary fixed result owners."""
from collections.abc import Mapping
from . import regex_working_set as re
from ..colored_hand_reveal_model import (
    ColoredHandRevealSpec, ColoredRevealResult, MatchingManaValueHandRevealSpec, reveal_count,
    COLORED_REVEAL_MECHANIC,
)
from ..rules.colored_hand_reveals import colored_reveal_result_dependencies
from ..replacement.immutable import FrozenMap

_HEAD=re.compile(r'Reveal any number of (?P<color>white|blue|black|red|green) cards in your hand\. (?P<body>.+)',re.I)
_COLORS=dict(zip(('white','blue','black','red','green'),'WUBRG',strict=True))


def _body(text,count):
    definition=re.fullmatch(r'(?P<body>.+), where X is the number of cards revealed this way\.?',text,re.I)
    if definition is not None:
        body=definition['body']
        if '.' in body or re.search(r'\bX\b',body) is None:return None
        return re.sub(r'\bX\b',str(count),body)+'.'
    gain=re.fullmatch(r'You gain (?P<n>[1-3]) life for each card revealed this way\.?',text,re.I)
    if gain:return f"You gain {int(gain['n'])*count} life."
    counter=re.fullmatch(r'Counter target spell unless its controller pays \{(?P<n>[1-3])\} for each card revealed this way\.?',text,re.I)
    if counter:return f"Counter target spell unless its controller pays {{{int(counter['n'])*count}}}."
    return None


def colored_hand_reveal_template(text,*,compile_fixed):
    if " ".join(text.split()).casefold() == (
        "reveal up to five nonland cards from your hand. for each of those cards that has the same mana value "
        "as another card revealed this way, create a treasure token."
    ):
        return ("matching-nonland-mana-value-reveal-v1", (MatchingManaValueHandRevealSpec().effect(),),
                None, (COLORED_REVEAL_MECHANIC,))
    match=_HEAD.fullmatch(text.strip())
    if match is None:return None
    a,b=_body(match['body'],2),_body(match['body'],3)
    if a is None or b is None:return None
    first,second=compile_fixed(a),compile_fixed(b)
    if (first[0] is None or first[0]!=second[0] or first[2]!=second[2]
            or len(first[1])!=1 or len(second[1])!=1 or first[3]!=second[3]):return None
    def project(x,y):
        if x==y and type(x) is type(y):return x
        if isinstance(x,Mapping) and isinstance(y,Mapping) and set(x)==set(y):
            return {key:project(x[key],y[key]) for key in x}
        if type(x) is int and type(y) is int and x==2*(y-x):return reveal_count(y-x)
        raise ValueError('Revealed count changed a nonnumeric result or target')
    try:
        result=ColoredRevealResult(FrozenMap(project(first[1][0],second[1][0])),
            FrozenMap(first[2]) if first[2] is not None else None)
        spec=ColoredHandRevealSpec(_COLORS[match['color'].casefold()],result)
        colored_reveal_result_dependencies(spec)
    except (ValueError,TypeError,KeyError):return None
    return ('colored-hand-reveal-result-v1',(spec.effect(),),first[2],(COLORED_REVEAL_MECHANIC,))
