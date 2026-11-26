"""Lower one controller's unviewed top-card exile and delayed hand result."""

from ..linked_exile_return_model import LINKED_EXILE_RETURN_MECHANIC, controller_library_top_hand_effects
from ..rules.linked_exile_return_shapes import controller_library_top_hand_price_is_closed
from .activated_costs import activated_ability_cost


_BODY = (
    "exile the top card of your library face down. "
    "put that card into your hand at the beginning of your next end step."
)


def controller_library_top_delayed_hand_activation(ability, text, *, public_timing):
    if (" ".join(text.split()).casefold() != _BODY or ability.zones != ("battlefield",)
            or ability.mana_ability or ability.sorcery_speed or ability.activation_conditions
            or " ".join(ability.cost_text.split()).casefold() != f"pay {ability.life_payment} life"):
        return None
    cost = activated_ability_cost(ability, public_timing=public_timing)
    if not controller_library_top_hand_price_is_closed(cost):
        return None
    return (
        "controller-library-top-delayed-hand-v1", controller_library_top_hand_effects(),
        None, (LINKED_EXILE_RETURN_MECHANIC,),
    )
