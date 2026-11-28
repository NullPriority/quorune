from __future__ import annotations

"""Public activation price summary from already validated compiled cost facts."""

from ...activated_ability_descriptor import SOURCE_COUNTER_PLACEMENT_OFFER_FIELD
from .granting_attachment import payable_granting_attachment
from .pricing import payable_activation_mana_options
from .commander_identity_life import current_commander_identity_life, uses_commander_identity_life


def activation_offer_cost_hint(host, seat, source, ability, *, selected=None):
    hint = ability.compact(source_ref=source.ref, zone=source.zone)
    if uses_commander_identity_life(ability):
        hint["life"] = current_commander_identity_life(host, seat, ability)
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
        from ...granted_activation_model import GrantingAttachmentPaymentKind
        kind = ("unattach" if payment.kind is GrantingAttachmentPaymentKind.UNATTACH_SOURCE else payment.kind.value)
        hint['granting_attachment'] = {'kind': kind, 'card': equipment.ref}
        name = host.display_name(equipment.object_id)
        attachment_label = f' — {kind.capitalize()} {name} ({equipment.ref})'
    if ability.mana_cost_options:
        hint['mana_options'] = [
            option.compact()
            for option in payable_activation_mana_options(host, seat, source, ability)
        ]
    from ...crew import activation_power_tap_hint
    from ...station import station_candidates, station_cost_choice
    from ..activation_costs import activation_choice_candidates

    selected = ability if selected is None else selected
    from ...paid_activation_card_types import has_bounded_sacrifice
    if ability.choices and not has_bounded_sacrifice(ability):
        hint["choose_cost"] = [
            {
                **choice.compact(),
                "legal_refs": list(
                    activation_choice_candidates(host, seat, source, choice, ability=selected)
                ),
            }
            for choice in ability.choices
        ]
    power_tap = activation_power_tap_hint(host, seat, source, selected)
    if power_tap is not None:
        hint["choose_cost"] = [power_tap]
    if station_cost_choice(selected) is not None:
        hint["choose_cost"] = [
            {
                "k": "station",
                "z": "battlefield",
                "minimum": 1,
                "maximum": 1,
                "legal_refs": [
                    candidate.ref
                    for candidate in station_candidates(host, seat, source)
                ],
            }
        ]
    fetch_types = selected.library_search_types
    if fetch_types:
        hint["search_types"] = list(fetch_types)
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
                "announced_energy_reanimation",
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
                "announced_x_minimum",
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
