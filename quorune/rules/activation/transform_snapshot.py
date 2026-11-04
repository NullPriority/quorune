from __future__ import annotations

"""Capture CR 701.27f only for an activation containing the transform operation."""


def activation_transform_context(host,proposal,source):
    from ...defined_token_faces import DEFINED_TOKEN_TRANSFORM_KEY
    if proposal.semantic_key == DEFINED_TOKEN_TRANSFORM_KEY:
        return {'source_transform_count':source.transform_count}
    program=host.semantics.get(proposal.semantic_key)
    if program is None or not any(effect.get('op')=='transform' for effect in program.effects):return {}
    return {'source_transform_count':source.transform_count}
