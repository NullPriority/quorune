from __future__ import annotations

"""Closed trigger forms for cycling with separate discard or death wording."""

from . import regex_working_set as re
from .fixed_public_event_trigger_bindings import _all_conditions, _spec


CYCLE_OR_DISCARD_CAPABILITY = 'trigger.event.cycle_or_discard'
_COMBINED = re.compile(r'^Whenever you cycle or discard (?P<object>a|another) card, (?P<body>.+)$', re.I)
_SELF_CYCLE_AND_DEATH = re.compile(
    r"When you cycle this card and when this creature dies, (?P<body>you may (?:"
    r"create a 1/1 white Soldier creature token|tap or untap target permanent|"
    r"exile target card from a graveyard|have it deal 1 damage to any target|"
    r"put a \+1/\+1 counter on target creature)\.)", re.I,
)


def cycle_and_death_trigger_nodes(*, node_id, line, material_line, span, source_name, printed_card_types,
                                  effect_template, trigger_node, trusted_mechanics,
                                  capability_registry, capability_profile):
    """Lower both printed occurrences through their ordinary source owners."""
    match = _SELF_CYCLE_AND_DEATH.fullmatch(material_line)
    if match is None or "creature" not in printed_card_types:
        return None
    from .fixed_counter_trigger_nodes import fixed_typed_event_effect_trigger_node

    result = []
    common = dict(line=line, span=span, card_name=source_name, trusted_mechanics=trusted_mechanics,
                  capability_registry=capability_registry, capability_profile=capability_profile,
                  residuals=[], effect_template=effect_template)
    for suffix, prefix, zone, event in (
        ("cycle", "When you cycle this card", "hand", "card.cycled.self"),
        ("dies", "When this creature dies", "battlefield", "creature.dies.self"),
    ):
        arguments = dict(common, node_id=f"{node_id}:cycle-and-dies:{suffix}", material_line=f"{prefix}, {match['body']}")
        node = fixed_typed_event_effect_trigger_node(**arguments) or trigger_node(**arguments)
        if node is None or not node.exact or (node.active_zone, node.event) != (zone, event):
            return None
        result.append(node)
    if result[0].target_schema != result[1].target_schema:
        return None
    return tuple(result)


def cycle_or_discard_binding_spec(material_line):
    match = _COMBINED.fullmatch(material_line)
    if match is None:
        return None
    conditions = [{'field': 'previous_controller', 'op': 'eq', 'value': '$source.controller'}]
    if match['object'].casefold() == 'another':
        conditions.append({'field': 'card', 'op': 'ne', 'value': '$source.ref'})
    # Cycling already discards as a cost. Subscribing separately to cycling
    # would incorrectly produce a second trigger for that same card.
    return _spec('card.discarded', 'controller_discards_card', match['body'],
        'fixed-counter-public-zone-trigger-v1', 'trigger-event-normalized-zone-change',
        condition=_all_conditions(*conditions), capabilities=(CYCLE_OR_DISCARD_CAPABILITY,))
