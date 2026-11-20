"""Whole-line productions for the represented blocking assumptions."""
from ..block_as_though_model import (
    BLOCK_AS_THOUGH_CAPABILITY,BLOCK_AS_THOUGH_HANDLER,BLOCK_AS_THOUGH_KIND,BlockAsThoughPermission,
)
from ..declaration_costs import normalized_oracle_line
from .continuous_templates import attached_fixed_characteristics_handler

_SELF={
    "this creature can block creatures with shadow as though they didn't have shadow":'attacker_without_shadow',
    'this creature can block creatures with shadow as though it had shadow':'blocker_with_shadow',
    'this creature can block dragons as though it had reach':'reach_against_dragons',
}
_AURA="enchanted creature gets +1/+1, has reach, and can block creatures with shadow as though they didn't have shadow"


def static_block_as_though_handler(text,*,source_name):
    line=normalized_oracle_line(text,card_name=source_name).strip().rstrip('.').casefold()
    stats=None
    if line in _SELF:permission=BlockAsThoughPermission('self',_SELF[line])
    elif line=='tapped creatures you control can block as though they were untapped':
        permission=BlockAsThoughPermission('controlled_tapped_blocker','untapped')
    elif line==_AURA:
        permission=BlockAsThoughPermission('attached_blocker','attacker_without_shadow')
        stats=attached_fixed_characteristics_handler('Enchanted creature gets +1/+1 and has reach.',source_name=source_name)
        if stats is None:return None
    else:return None
    handler={'handler_id':BLOCK_AS_THOUGH_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
             'fragment':{'kind':BLOCK_AS_THOUGH_KIND,'value':permission.to_dict()}}
    handlers=(handler,) if stats is None else (handler,stats[1])
    return ('fixed-bounded-block-as-though-v1',handlers,
            tuple(sorted({BLOCK_AS_THOUGH_CAPABILITY,*(stats[2] if stats is not None else ())})))
