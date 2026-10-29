from __future__ import annotations

"""Conjunction of independently closed activation instructions."""


def compound_activation_restriction(text, *, source_name=None):
    from .activation_condition_model import activation_restriction_spec,ActivationRestrictionSpec
    parts=text.split(' and only ')
    if not 2<=len(parts)<=4 or any(not part for part in parts):return None
    specs=tuple(activation_restriction_spec(part,source_name=source_name) for part in parts)
    if any(spec is None for spec in specs):return None
    limits=[spec.activation_limit for spec in specs if spec.activation_limit is not None]
    if len(limits)>1 or sum(spec.sorcery_speed for spec in specs)>1:return None
    conditions=tuple(condition for spec in specs for condition in spec.conditions)
    if any(condition in conditions[:i] for i,condition in enumerate(conditions)):return None
    return ActivationRestrictionSpec(sorcery_speed=any(spec.sorcery_speed for spec in specs),
        activation_limit=limits[0] if limits else None,conditions=conditions)


def activation_restriction_from_effect(text, *, source_name=None):
    from .activation_condition_model import activation_restriction_spec
    normalized=' '.join(text.casefold().split())
    marker='activate only '
    return activation_restriction_spec(normalized.rsplit(marker,1)[1],source_name=source_name) if marker in normalized else None
