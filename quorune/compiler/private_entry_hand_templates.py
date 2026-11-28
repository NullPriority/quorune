"""Lower private hand choices for an incoming permanent."""

from .entry_land_discard_templates import entry_land_discard_node
from .entry_reveal_templates import entry_reveal_node
from .day_night_nodes import initial_day_entry_node


def private_entry_hand_node(*, source_name, card_types, **context):
    return (entry_land_discard_node(card_types=card_types, **context)
            or entry_reveal_node(source_name=source_name, **context)
            or initial_day_entry_node(source_name=source_name, **context))
