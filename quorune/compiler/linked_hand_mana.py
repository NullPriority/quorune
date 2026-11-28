from dataclasses import replace
from hashlib import sha256

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..color_set_mana_abilities import (
    ColorSetActivatedManaAbilitySpec, ColorSetRelation, ColorSetSelection, color_set_mana_handler_descriptor,
)
from ..linked_exile_return_model import LINKED_ABILITY_BINDING_PREFIX
from ..rules.linked_hand_mana_model import ENTRY, OUTPUT, REQUIREMENTS, hand_imprint_effect, normalized
from .dependency_gate import explicit_capabilities_gate


def _closed_node(node, gate, **changes):
    return replace(node, lowerable=True, exact=True, residual_ids=(), capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable, capability_profile=gate.closure.profile,
        capability_fingerprint=gate.closure.fingerprint, runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        **changes)


def paired_linked_hand_mana(nodes, residuals, *, oracle_id, face_id, source_types,
                           capability_registry, capability_profile):
    if len(nodes) != 2 or "artifact" not in source_types:
        return nodes
    entry, mana = nodes
    if (normalized(entry.text).removeprefix("imprint — ") != ENTRY
            or normalized(mana.text) != "{t}: " + OUTPUT
            or (entry.kind, entry.event, entry.active_zone) != ("triggered_ability", "permanent.enter.self", "battlefield")
            or (mana.kind, mana.event, mana.active_zone) != ("mana_ability", "activate", "battlefield")
            or entry.event_condition is not None or entry.target_schema is not None or mana.target_schema is not None):
        return nodes
    gate = explicit_capabilities_gate(REQUIREMENTS, capability_registry=capability_registry,
                                     capability_profile=capability_profile)
    if gate.blockers or gate.closure is None:
        return nodes
    seed = f"{oracle_id}:{face_id}:{entry.span.line}:{mana.span.line}"
    pair = LINKED_ABILITY_BINDING_PREFIX + sha256(seed.encode()).hexdigest()[:24]
    cost, output = (part.strip() for part in mana.text.split(":", 1))
    spec = ColorSetActivatedManaAbilitySpec(
        ability_id="ab1", line_index=mana.span.line - 1, oracle_line=mana.text,
        cost_text=cost, effect_text=output, mana_cost=mana.cost["mana"], tap_source=True,
        sacrifice_source=False, life_payment=0, relation=ColorSetRelation.LINKED_EXILE,
        selection=ColorSetSelection.CHOOSE_ONE, query=None, linked_exile_pair=pair,
    )
    retired = {*entry.residual_ids, *mana.residual_ids}
    residuals[:] = [residual for residual in residuals if residual.residual_id not in retired]
    return [
        _closed_node(entry, gate, template_id="linked-hand-imprint-v1", effects=(hand_imprint_effect(pair),)),
        _closed_node(mana, gate, template_id="activated-mana-color-set-v1",
                     handlers=(color_set_mana_handler_descriptor(spec),)),
    ]
