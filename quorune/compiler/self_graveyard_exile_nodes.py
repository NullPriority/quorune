from __future__ import annotations

"""A fixed source-local graveyard redirection, without paid-cost inference."""

import re
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.source_references import SourceReferenceSpec
from ..self_graveyard_exile_model import SELF_GRAVEYARD_EXILE_CAPABILITY, self_graveyard_exile_descriptor
from .static_runtime_nodes import runtime_handler_node


def self_graveyard_exile_node(*, source_name, node_id, line, material_line, span,
        capability_registry, capability_profile, residuals):
    match = re.fullmatch(r'If (?P<source>.+?) would be put into a graveyard from anywhere, exile it instead\.',
        material_line, re.I)
    if match is None:
        return None
    if (match['source'].casefold() not in {'this card', 'this creature', 'this artifact', 'this enchantment', 'this permanent'}
            and not SourceReferenceSpec(source_name).matches(match['source'])):
        return None
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('fixed-source-graveyard-exile-v1', self_graveyard_exile_descriptor(),
            (SELF_GRAVEYARD_EXILE_CAPABILITY,)), kind='replacement_effect', event='zone.change', active_zone='all',
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Source-local exile requires the current complete admitted ability',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
