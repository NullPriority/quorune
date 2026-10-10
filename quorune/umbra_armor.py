from __future__ import annotations

"""Current Aura-owned protection relationships for destruction planning."""

from typing import Any, Mapping, Protocol

from .ability_fragments import canonical_ability_fragments
from .characteristic_evaluation import type_parts
from .umbra_armor_model import UmbraArmorProtection, UmbraArmorSpec


class UmbraArmorQuery(Protocol):
    state: Any
    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...


def current_umbra_armor_protections(host: UmbraArmorQuery) -> tuple[UmbraArmorProtection, ...]:
    result = []
    for aura in sorted(host.state.cards.values(), key=lambda card: card.object_id):
        if aura.zone != "battlefield" or aura.phased_out or not aura.attached_to:
            continue
        data = host._effective_card_data(aura)
        _types, subtypes, _supertypes = type_parts(str(data.get("type_line") or ""))
        if "aura" not in subtypes:
            continue
        instances = sum(isinstance(fragment, UmbraArmorSpec)
                        for fragment in canonical_ability_fragments(data.get("ability_fragments", ())))
        if not instances:
            continue
        recipient = host.state.cards.get(aura.attached_to)
        if recipient is None or recipient.zone != "battlefield" or recipient.phased_out:
            continue
        for instance in range(instances):
            result.append(UmbraArmorProtection(
                aura.object_id, aura.ref, aura.logical_object_id, aura.controller,
                recipient.object_id, recipient.logical_object_id, recipient.controller, instance,
            ))
    return tuple(result)
