from dataclasses import replace
from hashlib import sha256

from ..linked_exile_characteristic_model import CAPABILITY
from ..linked_exile_return_model import LINKED_ABILITY_BINDING_PREFIX, LINKED_ABILITY_TIMING
from ..linked_exile_return_model import LINKED_EXILE_RETURN_MECHANIC, LINKED_EXILE_RETURN_OPERATION, LinkedExileReturnSpec
from ..rules.linked_exile_characteristics import descriptor_for_link
from ..rules.linked_exile_return_shapes import linked_ability_exile_node_capabilities
from .dependency_gate import explicit_capabilities_gate


_ENTRY = "when this creature enters, you may exile target nontoken creature."
_STATIC = ("as long as a card exiled with this creature is a creature card, this creature has the power, toughness, "
           "and creature types of the last creature card exiled with it. it's still a shapeshifter.")


def _normalized(text):
    return " ".join(text.replace("’", "'").casefold().split())


def paired_linked_exile_characteristics(nodes, residuals, *, source_name, face, capability_registry, capability_profile):
    if len(nodes) != 2:
        return nodes
    entry, setting = nodes
    if (_normalized(entry.text).removeprefix("imprint — ") != _ENTRY or _normalized(setting.text) != _STATIC
            or entry.event != "permanent.enter.self" or entry.event_condition is not None or not entry.exact
            or setting.event != "continuous" or entry.target_schema is None):
        return nodes
    if tuple(entry.effects) != ({"op": "offer_optional_effect", "player": "$controller",
                                  "effects": [{"op": "exile_permanent", "card": "$target.0"}]},):
        return nodes
    identity = f"{source_name}:{face}:{entry.span.line}:{setting.span.line}"
    binding = LINKED_ABILITY_BINDING_PREFIX + sha256(identity.encode("utf-8")).hexdigest()[:24]
    exile = {"op": LINKED_EXILE_RETURN_OPERATION, "phase": "exile", "binding_id": binding, "cards": "$targets",
             "spec": LinkedExileReturnSpec(schema_version=3, timing=LINKED_ABILITY_TIMING).to_dict()}
    mechanics = (*entry.mechanics, LINKED_EXILE_RETURN_MECHANIC)
    required = linked_ability_exile_node_capabilities(effects=(exile,), target_schema=entry.target_schema, mechanic_ids=mechanics)
    if not required:
        return nodes
    entry_gate = explicit_capabilities_gate((*entry.capability_dependencies, *required),
        capability_registry=capability_registry, capability_profile=capability_profile)
    setting_gate = explicit_capabilities_gate((CAPABILITY,), capability_registry=capability_registry,
                                            capability_profile=capability_profile)
    if entry_gate.blockers or setting_gate.blockers or entry_gate.closure is None or setting_gate.closure is None:
        return nodes
    residuals[:] = [residual for residual in residuals if residual.residual_id not in setting.residual_ids]
    return [replace(entry, effects=({"op": "offer_optional_effect", "player": "$controller", "effects": [exile]},),
                    template_id="linked-characteristic-exile-v1", mechanics=mechanics, capability_dependencies=entry_gate.capabilities,
                    capability_closure=entry_gate.closure.reachable, capability_profile=entry_gate.closure.profile,
                    capability_fingerprint=entry_gate.closure.fingerprint),
            replace(setting, event="characteristics.evaluate", lowerable=True, exact=True,
                    template_id="linked-exile-static-characteristics-v1",
                    handlers=(descriptor_for_link(binding),), residual_ids=(), capability_dependencies=setting_gate.capabilities,
                    capability_closure=setting_gate.closure.reachable, capability_profile=setting_gate.closure.profile,
                    capability_fingerprint=setting_gate.closure.fingerprint)]
