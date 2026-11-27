from __future__ import annotations

"""Keywords whose complete production depends on the printed current face."""

from .ascend_nodes import ascend_keyword_node
from .day_night_nodes import day_night_keyword_node
from .commander_companion_nodes import commander_companion_role_node


def face_keyword_node(**values):
    if values["mechanics"] == ("companion",):
        return commander_companion_role_node(**values)
    if values['mechanics']==('ascend',):return ascend_keyword_node(**values)
    if values["mechanics"] == ("living metal",):
        return living_metal_keyword_node(**{
            key: value for key, value in values.items() if key not in {"record", "face_id", "mechanics"}
        })
    return day_night_keyword_node(**values)


def living_metal_keyword_node(*, node_id, line, material_line, span, capability_registry, capability_profile, residuals):
    if material_line.strip().removesuffix(".").casefold() != "living metal":
        return None
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
    from ..object_predicate import ObjectQuerySpec
    from ..semantic_runtime.fixed_characteristic_settings import ConditionalCharacteristicSettingsHandler
    from .continuous_templates import _attached_modifier
    from .static_runtime_nodes import runtime_handler_node

    handler = ConditionalCharacteristicSettingsHandler()
    modifier = _attached_modifier()
    modifier["type_operations"] = [{"op": "add_types", "field": "card_types", "values": ["Artifact", "Creature"]}]
    descriptor = {
        "handler_id": handler.handler_id,
        "schema_version": handler.schema_version,
        "event": handler.event,
        "target": {"kind": "source", "controller": None, "exclude_source": False,
                   "predicate": ObjectQuerySpec(zones=("battlefield",)).to_dict()},
        "modifier": modifier,
        "source_condition": FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_TURN).to_dict(),
    }
    handler.validate(descriptor)
    return runtime_handler_node(
        node_id=node_id, line=line, span=span, kind="keyword_ability", event=handler.event,
        compiled=("living-metal-v1", descriptor, handler.capability_dependencies),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="Living Metal requires its controller-turn additive type setting",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
