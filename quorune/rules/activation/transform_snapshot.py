from __future__ import annotations

"""Capture CR 701.27f only for an activation containing the transform operation."""


def activation_transform_context(host,proposal,source):
    program=host.semantics.get(proposal.semantic_key)
    if program is None or not any(effect.get('op')=='transform' for effect in program.effects):return {}
    return {'source_transform_count':source.transform_count}
