"""Complete whole-hand discard instructions with explicitly linked draw counts."""
from . import regex_working_set as re
from ..discard_result_model import (
    DAMAGE_RECIPIENT_REFERENCE, DISCARD_RESULT_MECHANIC, DISCARD_RESULT_OPERATION, SELECTED_LAND_DISCARD_RESULT_VERSION,
    event_player_discard_result_effect,
)
from ..event_player_modifier_model import DEFENDING_PLAYER_REFERENCE
from ..discard_result_model import FIXED_SUBSET_DISCARD_DRAW_MECHANIC
from ..discard_result_model import VARIABLE_SUBSET_DISCARD_DRAW_MECHANIC
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


def selected_land_discard_result_template(body):
    if re.fullmatch(
        r'You may discard any number of land cards\. Put that many \+1/\+1 counters on this creature and draw that many cards\.',
        ' '.join(body.split()), re.I,
    ):
        effect = {'op': DISCARD_RESULT_OPERATION, 'schema_version': SELECTED_LAND_DISCARD_RESULT_VERSION,
            'actor': '$controller', 'players': ['$controller'], 'reduction': 'own', 'offset': 0}
        return 'source-entry-selected-land-discard-result-v4', (effect,), None, (DISCARD_RESULT_MECHANIC,)
    return None


def event_player_discard_result_template(binding, body):
    """Bind one printed whole-hand result to its own combat or damage occurrence."""
    if binding.active_zone != 'battlefield' or body != binding.body:
        return None
    scope = (binding.event.value, binding.variant)
    if scope == ('creature.becomes_blocked', 'this_creature_becomes_blocked'):
        subject, player = 'defending player', DEFENDING_PLAYER_REFERENCE
    elif scope == ('damage.dealt.self', 'source_damage_player'):
        subject, player = 'that player', DAMAGE_RECIPIENT_REFERENCE
    else:
        return None
    if not re.fullmatch(rf'{subject} discards all the cards in their hand, then draws that many cards\.', body, re.I):
        return None
    return ('event-player-whole-hand-discard-result-v3', (event_player_discard_result_effect(player),),
            None, (DISCARD_RESULT_MECHANIC,))


def discard_result_draw_template(text):
    body = ' '.join(text.strip().split())
    variable = re.fullmatch(r'(?:You )?discard any number of cards, then draw that many cards(?P<extra> plus one)?\.', body, re.I)
    if variable is not None:
        return 'variable-subset-discard-draw-v3', ({'op':'discard_draw_up_to','schema_version':3,
            'maximum':'hand_size','draw_offset':int(bool(variable['extra']))},), None, (VARIABLE_SUBSET_DISCARD_DRAW_MECHANIC,)
    subset = re.fullmatch(rf'(?:You )?discard up to (?P<maximum>{FIXED_COUNT_PATTERN}) cards?, then draw that many cards\.', body, re.I)
    if subset is not None:
        maximum = fixed_number(subset['maximum'])
        if maximum > 0:
            return 'fixed-subset-discard-draw-v2', ({'op':'discard_draw_up_to','schema_version':2,'maximum':maximum},), None, (FIXED_SUBSET_DISCARD_DRAW_MECHANIC,)
        return None
    match = re.fullmatch(r'(?:(?P<subject>Each player|You) )?discards? '
        r'(?:(?:all the cards in )?(?P<hand>their|your)(?: entire)? hands?)'
        r'(?:, then| and|\. Then) draws? (?P<count>that many cards(?: plus one| minus one)?|'
        r'cards equal to the (?:greatest number of cards a player discarded this way|number of cards you discarded this way))\.', body, re.I)
    if match is None:return None
    subject = (match['subject'] or 'You').casefold()
    if (subject == 'you') != (match['hand'].casefold() == 'your'):return None
    count = match['count'].casefold()
    if 'greatest' in count and subject != 'each player':return None
    if 'you discarded' in count and subject != 'you':return None
    reduced = count.endswith('minus one')
    version = 2 if reduced else 1
    effect = {'op': DISCARD_RESULT_OPERATION, 'schema_version': version, 'actor': '$controller',
        'players': 'all' if subject == 'each player' else ['$controller'],
        'reduction': 'greatest' if 'greatest' in count else 'own', 'offset': -1 if reduced else int(count.endswith('plus one'))}
    return f'whole-hand-discard-result-draw-v{version}', (effect,), None, (DISCARD_RESULT_MECHANIC,)
