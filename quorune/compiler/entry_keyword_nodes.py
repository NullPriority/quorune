"""Ordered compound entry keywords before ordinary keyword planning."""
from .ravenous_nodes import ravenous_keyword_nodes
from .devour_nodes import devour_keyword_nodes
from .amplify_nodes import amplify_keyword_nodes


def compound_entry_keyword_nodes(*,record,node_id,line,material_line,span,printed_card_types,
        capability_registry,capability_profile,residuals):
    values=dict(node_id=node_id,line=line,material_line=material_line,span=span,
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    ravenous=ravenous_keyword_nodes(record=record,printed_card_types=printed_card_types,**values)
    if ravenous is not None:
        return ravenous
    devour = devour_keyword_nodes(**values)
    return devour if devour is not None else amplify_keyword_nodes(**values)
