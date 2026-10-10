from __future__ import annotations

"""Source-spanned Start Your Engines player speed marker."""

from ..rules.player_speed_model import START_ENGINES_HANDLER,SPEED_CAPABILITY
from .static_runtime_nodes import runtime_handler_node


def start_engines_keyword_node(*,node_id,line,material_line,span,mechanics,capability_registry,capability_profile,residuals,**_unused):
    if mechanics!=('start your engines!',) or material_line.casefold().rstrip('.')!='start your engines!':return None
    return runtime_handler_node(node_id=node_id,line=line,span=span,compiled=('start-engines-player-speed-v1',{'handler_id':START_ENGINES_HANDLER,'schema_version':1,'event':'characteristics.evaluate'},SPEED_CAPABILITY),
        kind='static_ability',event='characteristics.evaluate',dependency_reason='Player speed requires the canonical state-based start and inherent life-loss trigger owner.',
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
