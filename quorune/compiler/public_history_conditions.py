"""Closed printed current-turn history thresholds over existing typed facts."""

import re

from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..public_history_query_model import PublicHistoryQuerySpec
from ..continuous_conditions import FixedPublicStateConditionSpec,FixedPublicStateConditionKind
from .fixed_numbers import fixed_number

_N=r'a|an|one|two|three|four|five|six|seven|eight|nine|ten|[0-9]+'


def _condition(event,relation='controller',amount=1,*,types_all=(),types_any=(),excluded_types=(),at_most=False,reduction='count',origin=None):
    version=2 if origin is not None or event=='card_arrived_graveyard' and any((types_all,types_any,excluded_types)) else 1
    query=PublicHistoryQuerySpec(event,relation,types_all,types_any,excluded_types,reduction,schema_version=version,origin=origin)
    return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.HISTORY_COUNT_AT_MOST if at_most else FixedPublicStateConditionKind.HISTORY_COUNT_AT_LEAST,
        amount=amount,schema_version=8,history_query=query)


def fixed_public_history_condition(text):
    text=' '.join(text.strip().rstrip('.').casefold().replace('’',"'").split())
    if text in {"you haven't cast a spell from your hand this turn",'you have not cast a spell from your hand this turn'}:
        return _condition('spell_cast',amount=0,at_most=True,origin='hand')
    if re.fullmatch(r"you (?:didn't|did not|haven't|have not) cast a spell this turn", text):
        return _condition('spell_cast', amount=0, at_most=True)
    if re.fullmatch(r"you (?:didn't|did not) attack(?: with a creature)? this turn", text):
        return _condition('creature_attacked', amount=0, at_most=True)
    if text=='you descended this turn':
        return _condition('card_arrived_graveyard', types_any=tuple(sorted(PERMANENT_CARD_TYPES)))
    death=re.fullmatch(rf'(?P<n>{_N})(?P<more> or more)? creatures? died(?P<actor> under your control| under an opponent\'s control)? this turn',text)
    if death:
        amount=fixed_number(death['n'])
        if amount>1 and not death['more']:return None
        return _condition('creature_died','controller' if death['actor']==' under your control' else 'opponents' if death['actor'] else 'any_player',amount)
    entry=re.fullmatch(rf'(?P<n>{_N})(?P<more> or more)? (?P<types>artifacts?|battles?|creatures?|enchantments?|lands?|planeswalkers?|nonland permanents?|permanents?) entered(?: the battlefield)? under your control this turn',text)
    if entry:
        amount=fixed_number(entry['n'])
        if amount>1 and not entry['more']:return None
        kind=entry['types'].removesuffix('s')
        return _condition('permanent_entered',amount=amount,types_all=() if 'permanent' in kind else (kind,),excluded_types=('land',) if kind=='nonland permanent' else ())
    sacrifice=re.fullmatch(rf'you sacrificed (?P<n>{_N})(?P<more> or more)? (?P<types>permanents?|creatures?|artifacts?|lands?) this turn',text)
    if sacrifice:
        amount=fixed_number(sacrifice['n'])
        if amount>1 and not sacrifice['more']:return None
        kind=sacrifice['types'].removesuffix('s')
        return _condition('permanent_sacrificed',amount=amount,types_all=() if kind=='permanent' else (kind,))
    life=re.fullmatch(rf'(?P<actor>you|a player|an opponent) (?P<verb>gained|lost)(?: (?P<n>{_N}) or more)? life this turn',text)
    if life:
        relation={'you':'controller','a player':'any_player','an opponent':'opponents'}[life['actor']]
        amount=fixed_number(life['n']) if life['n'] else 1
        return _condition('player_gained_life' if life['verb']=='gained' else 'player_lost_life',relation,amount,
            reduction='sum_amount' if relation=='controller' else 'max_player_amount')
    if text in {'you gained or lost life this turn', 'you gained and lost life this turn'}:
        mode = FixedPublicStateConditionKind.ANY if ' or ' in text else FixedPublicStateConditionKind.ALL
        return FixedPublicStateConditionSpec(mode, schema_version=6,
            terms=(_condition('player_gained_life',reduction='sum_amount'),_condition('player_lost_life',reduction='sum_amount')))
    if re.fullmatch(r"you(?: have|'ve)? cast both a creature spell and a noncreature spell this turn", text):
        return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.ALL, schema_version=6, terms=(
            _condition('spell_cast', types_all=('creature',)),
            _condition('spell_cast', excluded_types=('creature',)),
        ))
    cast=re.fullmatch(rf'you(?: have|\'ve)? cast (?P<n>{_N})(?P<more> or more)? (?P<kind>noncreature |creature |artifact |instant or sorcery )?spells? this turn',text)
    if cast:
        amount=fixed_number(cast['n']);kind=(cast['kind'] or '').strip()
        if amount>1 and not cast['more']:return None
        return _condition('spell_cast',amount=amount,types_all=(kind,) if kind in {'artifact','creature'} else (),
            types_any=('instant','sorcery') if kind=='instant or sorcery' else (),excluded_types=('creature',) if kind=='noncreature' else ())
    if text=='you attacked this turn':return _condition('creature_attacked')
    if text in {'a permanent left the battlefield under your control this turn','a permanent you controlled left the battlefield this turn'}:
        return _condition('permanent_left')
    discard = re.fullmatch(r"(you|a player|an opponent) discarded a card this turn", text)
    if discard:
        return _condition('card_discarded', {'you': 'controller', 'a player': 'any_player', 'an opponent': 'opponents'}[discard[1]])
    if text=='a card was put into your graveyard from anywhere this turn':return _condition('card_arrived_graveyard')
    arrival = re.fullmatch(
        r"(?:a|an) (artifact|battle|creature|enchantment|kindred|land|planeswalker|instant|sorcery|permanent) card "
        r"was put into your graveyard from anywhere this turn", text,
    )
    if arrival:
        if arrival[1] == 'permanent':
            return _condition('card_arrived_graveyard', types_any=tuple(sorted(PERMANENT_CARD_TYPES)))
        return _condition('card_arrived_graveyard', types_all=(arrival[1],))
    return None
