"""Compile announced Reinforce X through ordinary cost and counter owners."""
from dataclasses import replace
from . import regex_working_set as re
from .oracle_source_text import without_parenthetical_reminder

_REINFORCE_X = re.compile(
    r'Reinforce X\s*[\-\u2013\u2014]\s*(?P<cost>(?:\{(?:X|0|[1-9]\d*|[WUBRGC])\})+)\.?', re.I)


def announced_reinforce_material(line):
    """Expand the complete keyword at compile time, preserving its source row."""
    match = _REINFORCE_X.fullmatch(without_parenthetical_reminder(line).strip())
    if match is None or match['cost'].upper().count('{X}') != 1:
        return None
    return (f"Reinforce — {match['cost'].upper()}, Discard this card: "
        'Put X +1/+1 counters on target creature.')


def parse_reinforce_activation_material(material, *, card_name, keywords):
    """Keep catalog discovery and node compilation on the same expansion."""
    from ..abilities import parse_activated_abilities
    lines = material.splitlines()
    expanded = {index: value for index, line in enumerate(lines)
        if (value := announced_reinforce_material(line)) is not None}
    normalized = '\n'.join(expanded.get(index, line) for index, line in enumerate(lines))
    abilities = parse_activated_abilities(card_name=card_name, oracle_text=normalized, keywords=keywords)
    return tuple(replace(ability, oracle_line=lines[ability.line_index])
        if ability.line_index in expanded else ability for ability in abilities)


def announced_reinforce_activation_node(*, record, node_id, line, material_line,
        span, mechanics, capability_registry, capability_profile, residuals):
    if record.faces or mechanics != ('reinforce',) or announced_reinforce_material(material_line) is None:
        return None
    from .activated_mana_nodes import activated_oracle_node
    from .counter_placement_templates import fixed_counter_placement_effect_template

    def compile_counter(text, **context):
        template = fixed_counter_placement_effect_template(text, card_name=record.name)
        return template.compiled() if template is not None else (None, (), None, ())

    return activated_oracle_node(node_id=node_id, line=line, span=span,
        card_name=record.name, type_line=record.type_line, keywords=record.keywords,
        trusted_mechanics=frozenset(), capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
        effect_template=compile_counter)
