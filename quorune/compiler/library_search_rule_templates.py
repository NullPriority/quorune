from __future__ import annotations

import re
from ..library_search_model import (LIBRARY_SEARCH_RULE_CAPABILITY,LIBRARY_SEARCH_RULE_HANDLER,LIBRARY_SEARCH_TEMP_CAPABILITY,
    LIBRARY_SEARCH_TEMP_OPERATION,LibrarySearchRuleSpec)
from .fixed_numbers import fixed_number


def fixed_library_search_rule_handler(text):
    material=' '.join(text.strip().split())
    deny=re.fullmatch(r"(?P<players>Players|Your opponents) can't search libraries\.?",material,re.I)
    top=re.fullmatch(r'If an opponent would search a library, that player searches the top (?P<count>one|two|three|four|five|six|seven|eight|nine|ten|[1-9][0-9]*) cards of that library instead\.?',material,re.I)
    if deny:rule=LibrarySearchRuleSpec('prohibit','all' if deny['players'].casefold()=='players' else 'opponents')
    elif top:rule=LibrarySearchRuleSpec('top','opponents',fixed_number(top['count']))
    else:return None
    return ('fixed-library-search-rule-v1',{'handler_id':LIBRARY_SEARCH_RULE_HANDLER,'schema_version':1,'event':'action.permission',
        'requires_complete_card_program':True,'rule':rule.to_dict()},LIBRARY_SEARCH_RULE_CAPABILITY)


def temporary_library_search_prohibition_template(text):
    if re.fullmatch(r"Players can't search libraries this turn\.?",' '.join(text.strip().split()),re.I) is None:return None
    return ('prohibit-library-search-this-turn-v1',({'op':LIBRARY_SEARCH_TEMP_OPERATION,'actor':'$controller'},),None,
        ('temporary-library-search-prohibition',))
