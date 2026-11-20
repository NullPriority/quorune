from __future__ import annotations

"""Library/hand result capability projection through existing typed validators."""

from types import MappingProxyType
from .fixed_hand_putback import fixed_putback_node_capabilities, fixed_putback_payload_dependencies
from .hand_library_redraw import hand_library_redraw_node_capabilities, hand_library_redraw_payload_dependencies
from .library_order_shapes import fixed_library_order_capabilities
from .variable_library_selection_shapes import variable_library_selection_node_capabilities
from .reveal_until_library_selection_shapes import reveal_until_library_selection_node_capabilities
from .library_search_capability_shapes import (
    fixed_library_search_node_capabilities, fixed_type_to_hand_search_node_capabilities,
    partner_with_search_node_capabilities,
)
from .library_selection_capability_shapes import fixed_library_selection_node_capabilities
from .hand_inspection_capability_shapes import fixed_hand_inspection_node_capabilities

# Projection of existing canonical capability identities, not another registry.
LIBRARY_RESULT_MECHANICS = MappingProxyType({
    'library.order.fixed_hand_putback':'fixed-draw-hand-putback',
    'library.search.named_library_graveyard': 'named-library-graveyard-search',
    'library.search.qualified_card': 'qualified-library-card-search',
    'library.inspect.fixed_target_player': 'fixed-target-library-inspection',
    'zone.shuffle.whole_graveyard': 'fixed-owner-zone-move',
    'library.search.ordered_destination_allocation': 'ordered-search-destination-allocation',
    'library.order.fixed_controller': 'fixed-controller-top-order',
    'library.order.fixed_hand_to_top': 'fixed-hand-to-library-top',
    'zone.hand.library_redraw': 'hand-library-redraw',
    'library.order.affected_player_hand_to_top': 'fixed-affected-player-hand-to-top',
    'library.order.graveyard_targets': 'ordered-graveyard-library-targets',
})


def library_result_node_capabilities(*, effects, target_schema, mechanic_ids):
    required = set()
    for resolver in (
        fixed_putback_node_capabilities,
        hand_library_redraw_node_capabilities,
        fixed_library_order_capabilities,
        variable_library_selection_node_capabilities,
        reveal_until_library_selection_node_capabilities,
        fixed_library_search_node_capabilities,
        fixed_type_to_hand_search_node_capabilities,
        partner_with_search_node_capabilities,
        fixed_library_selection_node_capabilities,
        fixed_hand_inspection_node_capabilities,
    ):
        required.update(resolver(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids))
    return tuple(sorted(required))


def library_result_payload_dependencies(value):
    if value.get('op')=='fixed_hand_putback':
        return fixed_putback_payload_dependencies(value)
    if value.get('op')=='hand_library_redraw':
        return hand_library_redraw_payload_dependencies(value)
    return ()
