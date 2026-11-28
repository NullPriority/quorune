"""Lower the whole-hand payment on an ordinary target spell counter."""

from ..rules.stack_controller_payment_cost import STACK_CONTROLLER_PAYMENT_MECHANIC
from ..rules.whole_hand_counter_payment import whole_hand_counter_effect, whole_hand_counter_schema


def targeted_whole_hand_counter_payment_template(text):
    if " ".join(text.split()).casefold().replace("’", "'") not in {
        "counter target spell unless its controller discards their hand.",
        "counter target spell unless its controller discards his or her hand.",
    }:
        return None
    return ("counter-controller-whole-hand-payment-v7", (whole_hand_counter_effect(),),
            whole_hand_counter_schema(), ("counter", "cr-115-targets", STACK_CONTROLLER_PAYMENT_MECHANIC))
