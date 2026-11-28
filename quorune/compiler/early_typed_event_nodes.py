"""Specialized native event productions checked ahead of generic typed counters."""

from __future__ import annotations


def early_typed_event_node(*, node_id, line, material_line, span, card_name,
                           capability_registry, capability_profile, residuals):
    from .retained_die_roll import retained_dice_counter_node

    if die := retained_dice_counter_node(node_id=node_id, line=line, material_line=material_line, span=span,
            card_name=card_name, capability_registry=capability_registry,
            capability_profile=capability_profile, residuals=residuals):
        return die

    from .source_death_counter_shuffle import source_death_counter_shuffle_node

    departure = source_death_counter_shuffle_node(
        node_id=node_id, line=line, material_line=material_line, span=span, card_name=card_name,
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
    if departure is not None:
        return departure
    from .source_sacrifice_reflexive_search import source_sacrifice_reflexive_search_node

    sacrifice = source_sacrifice_reflexive_search_node(
        node_id=node_id, line=line, material_line=material_line, span=span,
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
    if sacrifice is not None:
        return sacrifice
    from .night_shift_dice import night_shift_token_node

    token = night_shift_token_node(node_id=node_id, line=line, material_line=material_line, span=span,
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
    if token is not None:
        return token
    return None
