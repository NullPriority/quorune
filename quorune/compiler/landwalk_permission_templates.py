from __future__ import annotations

"""Whole-line lowering for fixed global and attached-blocker Landwalk rules."""

import re

from ..ability_fragments import ability_fragment_to_dict
from ..landwalk import LANDWALK_KEYWORDS
from ..landwalk_permission_model import (
    LANDWALK_PERMISSION_CAPABILITY, LANDWALK_PERMISSION_HANDLER, LandwalkBlockPermission,
)
from .continuous_templates import attached_fixed_characteristics_handler


def static_landwalk_permission_handler(text, *, source_name):
    normalized=" ".join(text.strip().rstrip('.').split())
    match=re.fullmatch(r"Creatures with (?P<subject>.+?) can be blocked as though they didn't have (?P<ignored>.+)",normalized,re.I)
    stats=None
    if match is not None:
        subject=match['subject'].casefold();ignored=match['ignored'].casefold()
        if subject=='landwalk abilities' and ignored=='those abilities':
            permission=LandwalkBlockPermission()
        elif subject in LANDWALK_KEYWORDS and subject==ignored:
            permission=LandwalkBlockPermission(keyword=subject)
        else:
            return None
    else:
        attached=re.fullmatch(r"Enchanted creature gets (?P<p>[+-]\d+)/(?P<t>[+-]\d+) and can block creatures with landwalk abilities as though they didn't have those abilities",normalized,re.I)
        if attached is None:
            return None
        permission=LandwalkBlockPermission(scope='attached_blocker')
        stats=attached_fixed_characteristics_handler(
            f"Enchanted creature gets {attached['p']}/{attached['t']}.",source_name=source_name)
        if stats is None:
            return None
    handler={'handler_id':LANDWALK_PERMISSION_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
        'fragment':ability_fragment_to_dict(permission)}
    handlers=(handler,) if stats is None else (handler,stats[1])
    capabilities={LANDWALK_PERMISSION_CAPABILITY}
    if stats is not None:
        capabilities.update(stats[2])
    return 'fixed-landwalk-block-permission-v1',handlers,tuple(sorted(capabilities))
