from __future__ import annotations

"""Count colored symbols in one complete current copiable mana cost."""

import re
from .util import parse_mana_symbols


def colored_mana_symbol_count(cost,colors):
    if cost is not None and type(cost) is not str:raise ValueError('A matched mana cost is unavailable')
    cost=(cost or '').strip();symbols=parse_mana_symbols(cost)
    if ''.join('{'+symbol+'}' for symbol in symbols)!=cost.upper():raise ValueError('A matched mana cost is malformed')
    count=0
    for symbol in symbols:
        if len(symbol.split('/'))!=len(set(symbol.split('/'))):raise ValueError('A matched mana symbol is unsupported')
        if not(symbol.isdigit() or symbol in {'W','U','B','R','G','C','S','X'}
                or re.fullmatch(r'[WUBRG](?:/[WUBRG])?(?:/P)?|(?:2|C)/[WUBRG]',symbol)):
            raise ValueError('A matched mana symbol is unsupported')
        count+=bool(set(symbol.split('/'))&set(colors))
    return count
