from dataclasses import replace
from hashlib import sha256

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..color_set_mana_abilities import (
    ColorSetActivatedManaAbilitySpec, ColorSetRelation, ColorSetSelection, color_set_mana_handler_descriptor,
)
from ..linked_exile_return_model import LINKED_ABILITY_BINDING_PREFIX
from ..replacement.immutable import FrozenMap
from ..rules.linked_graveyard_mana import (
    ENTRY, OUTPUT, PRODUCER_REQUIREMENTS, READER_REQUIREMENTS, graveyard_exile_instruction,
    graveyard_target_schema, linked_graveyard_exile_instruction,
)
from ..rules.linked_hand_mana_model import normalized
from .dependency_gate import explicit_capabilities_gate


def _with_closure(node, gate, **changes):
    return replace(
        node, lowerable=True, exact=True, residual_ids=(), capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable, capability_profile=gate.closure.profile,
        capability_fingerprint=gate.closure.fingerprint,
        runtime_coverage=tuple(dict.fromkeys((*node.runtime_coverage, CURRENT_ABILITY_FRAGMENT_COVERAGE))),
        **changes,
    )


def paired_linked_graveyard_mana(nodes, residuals, *, oracle_id, face_id, source_types,
                                capability_registry, capability_profile):
    if "land" not in source_types:
        return nodes
    entries = [node for node in nodes if normalized(node.text) == ENTRY]
    readers = [node for node in nodes if normalized(node.text) == "{t}: " + OUTPUT]
    if len(entries) != 1 or len(readers) != 1:
        return nodes
    entry, = entries
    reader, = readers
    if (not entry.exact or entry.kind != "triggered_ability" or entry.event != "permanent.enter.self"
            or entry.active_zone != "battlefield" or entry.trigger_limit is not None or entry.event_condition is not None
            or tuple(map(FrozenMap, entry.effects)) != (FrozenMap(graveyard_exile_instruction()),)
            or FrozenMap(entry.target_schema or {}) != FrozenMap(graveyard_target_schema())
            or reader.kind != "mana_ability" or reader.event != "activate" or reader.active_zone != "battlefield"
            or reader.target_schema is not None or reader.effects or reader.handlers
            or not reader.residual_ids or not reader.cost):
        return nodes
    producer_gate, reader_gate = (
        explicit_capabilities_gate(values, capability_registry=capability_registry, capability_profile=capability_profile)
        for values in (PRODUCER_REQUIREMENTS, READER_REQUIREMENTS)
    )
    if any(gate.blockers or gate.closure is None for gate in (producer_gate, reader_gate)):
        return nodes
    identity = f"{oracle_id}:{face_id}:{entry.span.line}:{reader.span.line}"
    pair = LINKED_ABILITY_BINDING_PREFIX + sha256(identity.encode()).hexdigest()[:24]
    cost, output = (part.strip() for part in reader.text.split(":", 1))
    spec = ColorSetActivatedManaAbilitySpec(
        ability_id=f"ab{reader.span.line}", line_index=reader.span.line - 1, oracle_line=reader.text,
        cost_text=cost, effect_text=output, mana_cost=reader.cost["mana"], tap_source=True,
        sacrifice_source=False, life_payment=0, relation=ColorSetRelation.LINKED_GRAVEYARD_EXILE,
        selection=ColorSetSelection.CHOOSE_ONE, query=None, linked_exile_pair=pair,
    )
    residuals[:] = [item for item in residuals if item.residual_id not in reader.residual_ids]
    linked_entry = _with_closure(entry, producer_gate, template_id="linked-graveyard-mana-exile-v1",
                                 effects=(linked_graveyard_exile_instruction(pair),))
    linked_reader = _with_closure(reader, reader_gate, template_id="activated-mana-color-set-v1",
                                  handlers=(color_set_mana_handler_descriptor(spec),))
    return [linked_entry if node is entry else linked_reader if node is reader else node for node in nodes]
