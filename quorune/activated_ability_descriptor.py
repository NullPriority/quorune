from __future__ import annotations

"""Closed serialized shape shared by activated-ability model boundaries.

This module deliberately validates only the canonical descriptor envelope.  The
rules-layer ``ActivatedAbility`` owns value semantics such as cost vocabulary,
activation limits, and executable outputs.  Domain models may depend on this
shape contract without importing that rules implementation.
"""

from typing import Any, Mapping


ACTIVATED_ABILITY_DESCRIPTOR_FIELDS = frozenset(
    {
        "schema_version",
        "ability_id",
        "line_index",
        "oracle_line",
        "cost_text",
        "effect_text",
        "zones",
        "mana",
        "complex_symbols",
        "tap_source",
        "untap_source",
        "discard_source",
        "sacrifice_source",
        "exile_source",
        "life_payment",
        "energy_payment",
        "loyalty_delta",
        "choices",
        "uncompiled_costs",
        "mana_ability",
        "sorcery_speed",
        "generic_reduction_per_legendary_creature",
        "builtin_semantic_key",
        "target_schema",
        "crew_threshold",
        "fixed_mana_outputs",
        "color_set_mana_output",
        "activation_limit",
        "library_search_types",
        "activation_conditions",
        "dynamic_mana_output",
        "mana_spend_restriction",
    }
)
_OPTIONAL_DESCRIPTOR_FIELDS = frozenset({"mana_cost_options"})

_SEQUENCE_FIELDS = (
    "zones",
    "complex_symbols",
    "choices",
    "uncompiled_costs",
    "fixed_mana_outputs",
    "library_search_types",
    "activation_conditions",
)
_TYPED_ENTRY_FIELDS = (
    "choices",
    "fixed_mana_outputs",
    "activation_conditions",
)


def validate_activated_ability_descriptor(value: Any) -> Mapping[str, Any]:
    """Validate and return one strict schema-v1 descriptor mapping.

    Callers must provide its canonical JSON-compatible representation.  Frozen
    values should be thawed before this boundary so tuple/list differences do
    not make domain validation depend on a particular immutable container.
    """

    if not isinstance(value, Mapping) or set(value) not in {
        ACTIVATED_ABILITY_DESCRIPTOR_FIELDS,
        ACTIVATED_ABILITY_DESCRIPTOR_FIELDS | _OPTIONAL_DESCRIPTOR_FIELDS,
    }:
        raise ValueError("activated abilities use a closed schema")
    if value["schema_version"] != 1:
        raise ValueError("unsupported activated ability schema version")
    if any(not isinstance(value[field], list) for field in _SEQUENCE_FIELDS):
        raise ValueError("activated ability sequence fields must be arrays")
    if not isinstance(value["mana"], Mapping):
        raise ValueError("activated ability mana must be an object")
    if value["target_schema"] is not None and not isinstance(
        value["target_schema"], Mapping
    ):
        raise ValueError("activated ability target_schema must be an object or null")
    if value["color_set_mana_output"] is not None and not isinstance(
        value["color_set_mana_output"], Mapping
    ):
        raise ValueError(
            "activated ability color_set_mana_output must be an object or null"
        )
    if any(
        not isinstance(entry, Mapping)
        for field in _TYPED_ENTRY_FIELDS
        for entry in value[field]
    ):
        raise ValueError("activated ability typed entries must be objects")
    if "mana_cost_options" in value and (
        not isinstance(value["mana_cost_options"], list)
        or any(
            not isinstance(entry, Mapping)
            for entry in value["mana_cost_options"]
        )
    ):
        raise ValueError("activation mana-cost options must be objects")
    return value
