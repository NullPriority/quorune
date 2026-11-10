from __future__ import annotations

"""Check a versioned entry return against the APNAP choice owner."""

from ..errors import GameRuleError
from ..replacement.immutable import thaw_value

ENTRY_RETURN_PAYMENT_VERSION_FIELD = "entry_return_payment_version"
ENTRY_RETURN_PAYMENT_VERSION = 1


def bind_checked_entry_return_payment(host, item, raw_effect, effect):
    from .entry_return_capability_shapes import checked_entry_return_payment_is_closed
    from ..selection.apnap_contract import validate_apnap_choice_effect

    if not checked_entry_return_payment_is_closed(thaw_value(raw_effect)):
        raise GameRuleError("Entry return payment requires its closed checked choice")
    actor = effect["player"]
    if actor != item.controller:
        raise GameRuleError("Entry return payment requires the resolving controller")
    branches = effect["then_by_choice"]
    payment, = branches["return"]
    validate_apnap_choice_effect(host, payment, {"stack_ref": item.ref})
    if len(host._choice_options(actor, payment)) < payment["count"]:
        return dict(branches["sacrifice"][0])
    return {key: value for key, value in effect.items() if key != ENTRY_RETURN_PAYMENT_VERSION_FIELD}
