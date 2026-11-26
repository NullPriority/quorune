"""Lower one source-spanned pair of color-cast exiles and their static reader."""

from dataclasses import replace
from hashlib import sha256

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..linked_exile_return_model import LINKED_ABILITY_BINDING_PREFIX
from ..rules.linked_color_cast_exile import (
    EXILE_NAME_CAST_CAPABILITY,
    EXILE_NAME_CAST_EVENT,
    TOP_EXILE_MECHANICS,
    TOP_EXILE_REQUIREMENTS,
    LinkedColorCastExile,
    exile_name_cast_descriptor,
    top_exile_target_schema,
)
from .dependency_gate import explicit_capabilities_gate
from .spell_cast_predicates import FixedSpellCastCharacteristicKind, fixed_spell_cast_binding_spec


def _normalize(text):
    return " ".join(text.casefold().replace("’", "'").split())


def _producer_spec(node, binding_id):
    if node.kind != "triggered_ability" or node.event != "spell.cast" or node.active_zone != "battlefield":
        return None
    binding = fixed_spell_cast_binding_spec(node.text)
    if binding is None or _normalize(binding.body) != "exile the top card of target player's library.":
        return None
    query = binding.subject.characteristic_query
    if query is None or len(query.terms_any) != 1:
        return None
    term = query.terms_any[0]
    if term.kind is not FixedSpellCastCharacteristicKind.COLOR:
        return None
    spec = LinkedColorCastExile(binding_id, term.value)
    return spec if binding.subject.event_condition == spec.event_condition else None


def _closed_node(node, gate, **changes):
    return replace(
        node, lowerable=True, exact=True, residual_ids=(),
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable,
        capability_profile=gate.closure.profile,
        capability_fingerprint=gate.closure.fingerprint,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        **changes,
    )


def _reader_names_original_source(node, source_name):
    if node.kind != "static_ability" or node.active_zone != "battlefield":
        return False
    prefix = "your opponents can't cast spells with the same name as a card exiled with "
    clause = _normalize(node.text)
    source = _normalize(source_name)
    names = {source, source.partition(",")[0], "this creature", "this permanent"}
    return clause.startswith(prefix) and clause.endswith(".") and clause[len(prefix):-1] in names


def paired_linked_color_cast_exile(nodes, residuals, *, source_name, oracle_id, face_id,
                                  source_types, capability_registry, capability_profile):
    if len(nodes) != 3 or not source_types.intersection(
        {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"},
    ):
        return nodes
    first, second, restriction = nodes
    if not _reader_names_original_source(restriction, source_name):
        return nodes
    identity = f"{oracle_id}:{face_id}:{first.span.line}:{second.span.line}:{restriction.span.line}"
    binding_id = LINKED_ABILITY_BINDING_PREFIX + sha256(identity.encode("utf-8")).hexdigest()[:24]
    first_spec, second_spec = (_producer_spec(node, binding_id) for node in (first, second))
    if first_spec is None or second_spec is None or first_spec.color == second_spec.color:
        return nodes
    producer_gate = explicit_capabilities_gate(
        TOP_EXILE_REQUIREMENTS, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    reader_gate = explicit_capabilities_gate(
        (EXILE_NAME_CAST_CAPABILITY,), capability_registry=capability_registry, capability_profile=capability_profile,
    )
    if any(gate.blockers or gate.closure is None for gate in (producer_gate, reader_gate)):
        return nodes
    retired = {residual_id for node in nodes for residual_id in node.residual_ids}
    residuals[:] = [residual for residual in residuals if residual.residual_id not in retired]
    lowered = [
        _closed_node(
            node, producer_gate, template_id="linked-color-cast-top-exile-v1",
            event_condition=spec.event_condition, target_schema=top_exile_target_schema(),
            effects=(spec.to_effect(),), mechanics=TOP_EXILE_MECHANICS,
        )
        for node, spec in ((first, first_spec), (second, second_spec))
    ]
    return [
        *lowered,
        _closed_node(
            restriction, reader_gate, template_id="linked-exile-names-cast-prohibition-v1",
            event=EXILE_NAME_CAST_EVENT, handlers=(exile_name_cast_descriptor(binding_id),),
            mechanics=("cr-607-linked-abilities",),
        ),
    ]
