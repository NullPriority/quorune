"""Complete whole-hand discard instructions with explicitly linked draw counts."""
import re
from ..discard_result_model import DISCARD_RESULT_MECHANIC, DISCARD_RESULT_OPERATION


def discard_result_draw_template(text):
    body = ' '.join(text.strip().split())
    match = re.fullmatch(r'(?:(?P<subject>Each player|You) )?discards? '
        r'(?:(?:all the cards in )?(?P<hand>their|your)(?: entire)? hands?)'
        r'(?:, then| and|\. Then) draws? (?P<count>that many cards(?: plus one)?|'
        r'cards equal to the (?:greatest number of cards a player discarded this way|number of cards you discarded this way))\.', body, re.I)
    if match is None:return None
    subject = (match['subject'] or 'You').casefold()
    if (subject == 'you') != (match['hand'].casefold() == 'your'):return None
    count = match['count'].casefold()
    if 'greatest' in count and subject != 'each player':return None
    if 'you discarded' in count and subject != 'you':return None
    effect = {'op': DISCARD_RESULT_OPERATION, 'schema_version': 1, 'actor': '$controller',
        'players': 'all' if subject == 'each player' else ['$controller'],
        'reduction': 'greatest' if 'greatest' in count else 'own', 'offset': int(count.endswith('plus one'))}
    return 'whole-hand-discard-result-draw-v1', (effect,), None, (DISCARD_RESULT_MECHANIC,)
