from __future__ import annotations

"""Compile printed fixed Prototype characteristics before keyword fallback."""

import re
from ..prototype_model import PrototypeSpec,PROTOTYPE_HANDLER,PROTOTYPE_CAPABILITY
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .static_runtime_nodes import runtime_handler_node


def prototype_keyword_node(*,node_id,line,material_line,span,mechanics,capability_registry,capability_profile,residuals,**_unused):
    match=re.fullmatch(r'Prototype (?P<cost>(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+)\s*[—–\-\ufffd]\s*(?P<power>\d+)/(?P<toughness>\d+)\.?',material_line.strip(),re.I)
    if mechanics!=('prototype',) or match is None:return None
    spec=PrototypeSpec(match['cost'],int(match['power']),int(match['toughness']))
    return runtime_handler_node(node_id=node_id,line=line,span=span,compiled=('fixed-prototype-characteristics-v1',
        {'handler_id':PROTOTYPE_HANDLER,'schema_version':1,'event':'characteristics.evaluate','prototype':spec.to_dict(),REQUIRES_COMPLETE_CARD_PROGRAM_FIELD:True},PROTOTYPE_CAPABILITY),
        kind='static_ability',event='characteristics.evaluate',active_zone='all',runtime_coverage=('prototype_cast_characteristics',),
        dependency_reason='Prototype requires shared cast selection and copiable stack-to-battlefield alternate characteristics.',
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
