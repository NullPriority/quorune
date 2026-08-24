from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping

from ..util import stable_json
from .activated_abilities import (
    default_activated_ability_catalog_registry,
)
from .activation_restrictions import default_activation_restriction_registry
from .action_permissions import default_action_permission_registry
from .ability_fragments import default_ability_fragment_registry
from .block_restrictions import default_block_restriction_registry
from .cast_permissions import default_cast_permission_registry
from .casting_activation_metadata import (
    default_loyalty_cost_modifier_registry,
    default_self_zone_cast_permission_registry,
)
from .combat_metadata import default_goad_prohibition_registry
from .cast_costs import default_cast_cost_component_registry
from .context import SemanticNodeError
from .counter_replacements import (
    default_counter_placement_replacement_registry,
)
from .damage_replacements import default_damage_replacement_registry
from .damage_results import default_damage_result_replacement_registry
from .draw_replacements import default_draw_replacement_registry
from .draw_reveals import default_draw_reveal_registry
from .draw_restrictions import default_draw_restriction_registry
from .crew_abilities import default_ordinary_crew_ability_registry
from .station_abilities import default_ordinary_station_ability_registry
from .cycling_abilities import default_cycling_ability_registry
from .counter_keyword_abilities import (
    default_fixed_counter_keyword_ability_registry,
)
from .life_replacements import default_life_replacement_registry
from .color_set_mana_abilities import (
    default_color_set_mana_ability_registry,
)
from .mana_abilities import default_fixed_mana_ability_registry
from .morph import default_fixed_mana_morph_registry
from .bestow import default_fixed_mana_bestow_registry
from .flashback import default_fixed_mana_flashback_registry
from .kicker import default_fixed_mana_kicker_registry
from .unearth import default_ordinary_unearth_ability_registry
from .self_zone_move import default_self_zone_move_ability_registry
from .continuous_components import (
    default_continuous_effect_component_registry,
)
from .token_replacements import (
    default_token_creation_replacement_registry,
)
from .untap_steps import default_untap_step_component_registry
from .zone_replacements import default_zone_change_replacement_registry


def runtime_component_registries() -> tuple[Any, ...]:
    return (
        default_activated_ability_catalog_registry(),
        default_activation_restriction_registry(),
        default_action_permission_registry(),
        default_ability_fragment_registry(),
        default_block_restriction_registry(),
        default_cast_permission_registry(),
        default_goad_prohibition_registry(),
        default_loyalty_cost_modifier_registry(),
        default_self_zone_cast_permission_registry(),
        default_cast_cost_component_registry(),
        default_continuous_effect_component_registry(),
        default_counter_placement_replacement_registry(),
        default_damage_replacement_registry(),
        default_damage_result_replacement_registry(),
        default_draw_replacement_registry(),
        default_draw_reveal_registry(),
        default_draw_restriction_registry(),
        default_life_replacement_registry(),
        default_ordinary_crew_ability_registry(),
        default_ordinary_station_ability_registry(),
        default_cycling_ability_registry(),
        default_fixed_counter_keyword_ability_registry(),
        default_color_set_mana_ability_registry(),
        default_fixed_mana_ability_registry(),
        default_fixed_mana_morph_registry(),
        default_fixed_mana_bestow_registry(),
        default_fixed_mana_flashback_registry(),
        default_fixed_mana_kicker_registry(),
        default_ordinary_unearth_ability_registry(),
        default_self_zone_move_ability_registry(),
        default_token_creation_replacement_registry(),
        default_untap_step_component_registry(),
        default_zone_change_replacement_registry(),
    )


def runtime_component_inventory() -> list[dict[str, Any]]:
    inventory = [
        descriptor
        for registry in runtime_component_registries()
        for descriptor in registry.inventory()
    ]
    identifiers = [str(value["handler_id"]) for value in inventory]
    if len(identifiers) != len(set(identifiers)):
        raise SemanticNodeError(
            "Runtime handler IDs must be globally unique"
        )
    return sorted(inventory, key=lambda value: value["handler_id"])


def describe_runtime_handler(handler_id: str) -> dict[str, Any] | None:
    return next(
        (
            descriptor
            for descriptor in runtime_component_inventory()
            if descriptor["handler_id"] == handler_id
        ),
        None,
    )


def validate_runtime_handler_descriptors(
    descriptors: Iterable[Mapping[str, Any]],
) -> None:
    registries = runtime_component_registries()
    for descriptor in descriptors:
        handler_id = str(descriptor.get("handler_id") or "")
        registry = next(
            (
                value
                for value in registries
                if value.describe(handler_id) is not None
            ),
            None,
        )
        if registry is None:
            raise SemanticNodeError(
                f"Unknown runtime handler ID {handler_id!r}"
            )
        registry.validate(descriptor)


def runtime_component_registry_fingerprint() -> str:
    payload = {
        "schema_version": 1,
        "handlers": runtime_component_inventory(),
    }
    return hashlib.sha256(
        stable_json(payload).encode("utf-8")
    ).hexdigest()
