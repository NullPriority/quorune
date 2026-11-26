from __future__ import annotations

"""Public activation price summary from already validated compiled cost facts."""

from ...activated_ability_descriptor import SOURCE_COUNTER_PLACEMENT_OFFER_FIELD
from .granting_attachment import payable_granting_attachment
from .pricing import payable_activation_mana_options


def activation_offer_cost_hint(host, seat, source, ability):
    hint = ability.compact(source_ref=source.ref, zone=source.zone)
    from ..chosen_color_draw_price import current_draw_price_color

    chosen_output, color = current_draw_price_color(host, source, ability)
    if chosen_output and color is not None:
        hint["chosen_payment_color"] = color
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
    from ..forage import has_forage_cost

    from ..selected_counter_costs import has_distributed_counter_cost

    payload = {
        "kind": "activate",
        "source": source.ref,
        "ability": selected.ability_id,
        "from": source.zone,
        "cost_summary": {
            key: value
            for key, value in hint.items()
            if (key != "choose_cost" or not has_distributed_counter_cost(selected))
            and key in {
                "m",
                "tap",
                "life",
                "energy",
                "random_discard",
                "mill_library",
                "loyalty",
                "loyalty_x",
                "remove_source_counter",
                SOURCE_COUNTER_PLACEMENT_OFFER_FIELD,
                "sac_self",
                "discard_self",
                "exile_self",
                "choose_cost",
                "mana_options",
                "announced_x_symbols",
                "granting_attachment",
                "chosen_payment_color",
            }
        },
    }
    from ..whole_hand_mana import DISCARD_HAND_COST_FIELD, is_whole_hand_mana_ability

    if is_whole_hand_mana_ability(selected):
        payload["cost_summary"][DISCARD_HAND_COST_FIELD] = 1
    if has_forage_cost(selected):
        # The typed choice describes both possible groups instead of the legacy single-card slot.
        payload["cost_summary"].pop("choose_cost", None)
    return payload
