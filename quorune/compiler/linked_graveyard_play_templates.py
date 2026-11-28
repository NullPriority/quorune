"""Lower the exact source-relative replacement and own-turn exile permission pair."""

from dataclasses import replace
from hashlib import sha256

from . import regex_working_set as re
from .dependency_gate import explicit_capabilities_gate
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..linked_exile_return_model import LINKED_ABILITY_BINDING_PREFIX
from ..rules.linked_graveyard_play import (
    PERMISSION_EVENT, PLAY_DEPENDENCIES, REPLACEMENT_DEPENDENCIES, REPLACEMENT_EVENT, LinkedGraveyardPlaySpec,
)
from ..rules.source_references import SourceReferenceSpec

_REPLACEMENT = (
    "if a card you didn't control would be put into an opponent's graveyard from anywhere, exile it instead."
)
_PERMISSION = re.compile(
    r"during your turn, you may play cards exiled with (?P<source>[^.]+)\. "
    r"if you cast a spell this way, pay life equal to its mana value rather than pay its mana cost\."
)


def _normalize(text):
    return " ".join(text.casefold().replace("’", "'").split())


def _replacement(node):
    return (node.kind == "replacement_effect" and node.active_zone == "battlefield"
            and _normalize(node.text) == _REPLACEMENT)


def _permission(node, source_name):
    if node.kind != "static_ability" or node.active_zone != "battlefield":
        return False
    match = _PERMISSION.fullmatch(_normalize(node.text))
    return (match is not None and (match["source"] == "this permanent"
                                  or SourceReferenceSpec(source_name).matches(match["source"])))


def _closed_node(node, gate, spec, *, replacement):
    return replace(
        node, lowerable=True, exact=True, residual_ids=(),
        event=REPLACEMENT_EVENT if replacement else PERMISSION_EVENT,
        template_id="linked-graveyard-exile-v1" if replacement else "linked-graveyard-exile-play-v1",
        capability_dependencies=gate.capabilities, capability_closure=gate.closure.reachable,
        capability_profile=gate.closure.profile, capability_fingerprint=gate.closure.fingerprint,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=("cr-607-linked-abilities",), handlers=(spec.descriptor(replacement=replacement),),
    )


def paired_linked_graveyard_play(nodes, residuals, *, source_name, oracle_id, face_id,
                                 capability_registry, capability_profile):
    producers = tuple(node for node in nodes if _replacement(node))
    readers = tuple(node for node in nodes if _permission(node, source_name))
    if len(producers) != 1 or len(readers) != 1:
        return nodes
    producer, = producers
    reader, = readers
    identity = f"{oracle_id}:{face_id}:{producer.span.line}:{reader.span.line}"
    spec = LinkedGraveyardPlaySpec(LINKED_ABILITY_BINDING_PREFIX + sha256(identity.encode()).hexdigest()[:24])
    gates = tuple(
        explicit_capabilities_gate(deps, capability_registry=capability_registry, capability_profile=capability_profile)
        for deps in (REPLACEMENT_DEPENDENCIES, PLAY_DEPENDENCIES)
    )
    if any(gate.blockers or gate.closure is None for gate in gates):
        return nodes
    retired = set(producer.residual_ids).union(reader.residual_ids)
    residuals[:] = [value for value in residuals if value.residual_id not in retired]
    lowered = {
        producer.node_id: _closed_node(producer, gates[0], spec, replacement=True),
        reader.node_id: _closed_node(reader, gates[1], spec, replacement=False),
    }
    return [lowered.get(node.node_id, node) for node in nodes]
