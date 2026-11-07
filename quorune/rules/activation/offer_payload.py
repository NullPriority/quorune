from __future__ import annotations

"""Public activation price summary from already validated compiled cost facts."""

from .granting_attachment import payable_granting_attachment
from .pricing import payable_activation_mana_options


def activation_offer_cost_hint(host, seat, source, ability):
    hint = ability.compact(source_ref=source.ref, zone=source.zone)
    attachment_label = ''
    payment = ability.granting_attachment_payment
    if payment is not None:
        equipment = payable_granting_attachment(host, seat, source, payment)
        if equipment is None:
            return None
        hint['granting_attachment'] = {'kind': payment.kind.value, 'card': equipment.ref}
        name = host.display_name(equipment.object_id)
        attachment_label = f' — {payment.kind.value.capitalize()} {name} ({equipment.ref})'
    if ability.mana_cost_options:
        hint['mana_options'] = [
            option.compact()
            for option in payable_activation_mana_options(host, seat, source, ability)
        ]
    return hint, attachment_label


def activation_offer_payload(source,selected,hint):
    payload = {
        "kind": "activate",
        "source": source.ref,
        "ability": selected.ability_id,
        "from": source.zone,
        "cost_summary": {
            key: value
            for key, value in hint.items()
            if key
            in {
                "m",
                "tap",
                "life",
                "energy",
                "random_discard",
                "loyalty",
                "remove_source_counter",
                "sac_self",
                "discard_self",
                "exile_self",
                "choose_cost",
                "mana_options",
                "announced_x_symbols",
                "granting_attachment",
            }
        },
    }
    return payload
