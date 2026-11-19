"""Compile an exact additional-mana timing clause without granting Flash."""
from . import regex_working_set as re
from ..cast_timing import (
    CAST_PERMISSION_ACTIVE_ZONE,CAST_PERMISSION_EVENT,ManaSurchargeFlashSpec,
    SURCHARGE_FLASH_HANDLER,SURCHARGE_FLASH_CAPABILITY,
)
from .static_runtime_nodes import runtime_handler_node


def surcharge_flash_node(*,node_id,line,material_line,span,capability_registry,capability_profile,residuals):
    text=re.sub(r'\s+\([^()]*\)\.?$','',material_line.strip()).rstrip('.')
    match=re.fullmatch(r'You may cast this spell as though it had flash if you pay '
        r'\{(?P<generic>[1-9][0-9]*)\} more to cast it',text,re.I)
    if match is None:return None
    spec=ManaSurchargeFlashSpec(int(match['generic']))
    descriptor={'handler_id':SURCHARGE_FLASH_HANDLER,'schema_version':1,
                'event':CAST_PERMISSION_EVENT,'surcharge':spec.to_dict()}
    return runtime_handler_node(node_id=node_id,line=line,span=span,
        compiled=('mana-surcharge-self-flash-v1',descriptor,SURCHARGE_FLASH_CAPABILITY),
        kind='static_ability',event=CAST_PERMISSION_EVENT,active_zone=CAST_PERMISSION_ACTIVE_ZONE,
        runtime_coverage=('mana_surcharge_flash',),
        dependency_reason='Instant timing requires its exact face-bound additional mana payment.',
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
