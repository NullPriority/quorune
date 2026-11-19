from __future__ import annotations

"""Exact basic-land searches whose maximum comes from an existing public quantity."""

from . import regex_working_set as re
from .live_battlefield_quantities import resolution_quantity
from ..query_effect_amount_model import PublicQueryAmountSpec
from ..library_search_model import QueryBoundLibrarySearchSpec


def query_bound_library_search_template(text):
    normalized=' '.join(text.strip().split())
    match=re.fullmatch(r'Search your library for up to X basic land cards, where X is '
        r'(?P<quantity>.+?)(?:,|\.) (?P<placement>.+)',normalized,re.I)
    if match is None:return None
    placement=match['placement']
    if re.fullmatch(r'put (?:them|those cards) onto the battlefield tapped, then shuffle\.',placement,re.I):
        destination,reveal,tapped='battlefield',False,True
    elif re.fullmatch(r'Reveal those cards, put them into your hand, then shuffle\.',placement,re.I):
        destination,reveal,tapped='hand',True,False
    else:return None
    quantity=resolution_quantity(match['quantity'],source_name='Source',complete_value=True)
    if quantity is None:return None
    try:return QueryBoundLibrarySearchSpec(PublicQueryAmountSpec(quantity),destination,reveal,tapped)
    except ValueError:return None
