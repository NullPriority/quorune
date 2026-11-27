from __future__ import annotations

from . import regex_working_set as re
from ..library_search_model import (LIBRARY_SEARCH_RULE_CAPABILITY,LIBRARY_SEARCH_RULE_HANDLER,LIBRARY_SEARCH_TEMP_CAPABILITY,
    LIBRARY_SEARCH_TEMP_OPERATION,LibrarySearchRuleSpec)
from .fixed_numbers import fixed_number


def fixed_library_search_rule_handler(text):
    material=' '.join(text.strip().split())
    deny=re.fullmatch(r"(?P<players>Players|Your opponents) can't search libraries\.?",material,re.I)
    top=re.fullmatch(r'If an opponent would search a library, that player searches the top (?P<count>one|two|three|four|five|six|seven|eight|nine|ten|[1-9][0-9]*) cards of that library instead\.?',material,re.I)
    if deny:rule=LibrarySearchRuleSpec('prohibit','all' if deny['players'].casefold()=='players' else 'opponents')
    elif top:rule=LibrarySearchRuleSpec('top','opponents',fixed_number(top['count']))
    else:
        from ..semantic_runtime.library_search_rules import SEARCH_CONTROL_CAPABILITY, SEARCH_CONTROL_HANDLER

        modes = {
            "you control your opponents while they're searching their libraries.": "control_opponent",
            ("while an opponent is searching their library, they exile each card they find. "
             "you may play those cards for as long as they remain exiled, and you may spend mana as though "
             "it were mana of any color to cast them."): "exile_opponent_found",
        }
        mode = modes.get(material.casefold().replace("’", "'"))
        if mode is None:
            return None
        rule = LibrarySearchRuleSpec(mode, 'opponents')
        return ('opponent-library-search-control-v1', {'handler_id': SEARCH_CONTROL_HANDLER, 'schema_version': 1,
                    'event': 'action.permission', 'requires_complete_card_program': True,
                    'rule': rule.to_dict()}, SEARCH_CONTROL_CAPABILITY)
    return ('fixed-library-search-rule-v1',{'handler_id':LIBRARY_SEARCH_RULE_HANDLER,'schema_version':1,'event':'action.permission',
        'requires_complete_card_program':True,'rule':rule.to_dict()},LIBRARY_SEARCH_RULE_CAPABILITY)


def temporary_library_search_prohibition_template(text):
    if re.fullmatch(r"Players can't search libraries this turn\.?",' '.join(text.strip().split()),re.I) is None:return None
    return ('prohibit-library-search-this-turn-v1',({'op':LIBRARY_SEARCH_TEMP_OPERATION,'actor':'$controller'},),None,
        ('temporary-library-search-prohibition',))
