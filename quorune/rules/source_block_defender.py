"""Trust the fixed defender change only on its source's block occurrence."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..replacement.immutable import thaw_value
from ..resolution_characteristic_model import CHARACTERISTIC_OPERATION, fixed_resolution_characteristic_instruction
from ..source_block_defender_model import (
    SOURCE_BLOCK_DEFENDER_FIELD, SOURCE_BLOCK_DEFENDER_MECHANIC,
    SOURCE_BLOCK_DEFENDER_VERSION,
    SourceBlockDefenderSpec,
)
from .effect_shape_walkers import nested_effects


def source_block_defender_instruction(spec: SourceBlockDefenderSpec) -> dict[str, object]:
    return {
        "op": CHARACTERISTIC_OPERATION, "schema_version": SOURCE_BLOCK_DEFENDER_VERSION,
        "card": "$source.zone_object", SOURCE_BLOCK_DEFENDER_FIELD: spec.to_dict(),
    }


def is_source_block_defender_instruction(effect) -> bool:
    return (
        isinstance(effect, Mapping)
        and effect.get("op") == CHARACTERISTIC_OPERATION
        and effect.get("schema_version") == SOURCE_BLOCK_DEFENDER_VERSION
    )


def _source_block_defender_dependencies(effects, target_schema):
    if len(effects) != 1 or target_schema is not None or not is_source_block_defender_instruction(effects[0]):
        raise ValueError("The blocking source needs one untargeted characteristic result")
    spec, source = fixed_resolution_characteristic_instruction(effects[0])
    if not isinstance(spec, SourceBlockDefenderSpec) or source != "$source.zone_object":
        raise ValueError("The defender change needs the original blocking source")
    return spec.capability_dependencies


def source_block_defender_node_capabilities(*, effects, target_schema, mechanics):
    if not {SOURCE_BLOCK_DEFENDER_MECHANIC, "cr-611-continuous-effects"} <= set(mechanics):
        return ()
    try:
        return _source_block_defender_dependencies(effects, target_schema)
    except (TypeError, ValueError):
        return ()


def source_block_defender_runtime_dependencies(program):
    if not any(is_source_block_defender_instruction(effect)
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = _source_block_defender_dependencies(program.effects, program.target_schema)
    if (program.event, program.active_zone) != ("creature.blocks", "battlefield") or (
        thaw_value(program.event_condition) != {"field": "card", "op": "eq", "value": "$source.ref"}
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("The defender change requires the source's current block trigger")
    return required
