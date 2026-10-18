from __future__ import annotations

"""Public activation price summary from already validated compiled cost facts."""


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
            }
        },
    }
    return payload
