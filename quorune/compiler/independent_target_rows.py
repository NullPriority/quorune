from __future__ import annotations

"""Source-spanned independent scalar clauses for the existing spell composer."""

from dataclasses import replace
import re

from .closed_effect_programs import _top_level_positions
from .ir_model import SourceSpan
from .oracle_source_text import without_parenthetical_reminder
from .program_composition import (
    DISTINCT_CLAUSE_TARGET_COVERAGE, _bounded_public_target_set_schema,
    _component_target_roles_are_exact, _fixed_public_single_target_schema, _scope_component_target,
)

_MUTATIONS = {'modify_stats_until_end_of_turn', 'grant_keyword_until_end_of_turn',
    'place_counters', 'tap', 'untap', 'damage', 'life', 'lose_life', 'draw', 'scry', 'mill'}
_TARGET_WORD = re.compile(r'\btarget\b', re.I)


def prepare_target_clause_context(material_rows,*,spell,face_id,oracle_text,card_name,type_line,keywords,
        trusted_mechanics,capability_registry,capability_profile,effect_template,activated_or_event_node,trigger_node):
    """Prepare source rows before their shared modal block classification."""
    distinct_spans=set()
    if spell:
        material_rows,distinct_spans=independent_target_source_rows(material_rows,
            source_name=card_name,compile_effect=effect_template)
    from .modal_context_nodes import fixed_nonrepeating_modal_context_blocks
    blocks=fixed_nonrepeating_modal_context_blocks(material_rows=material_rows,face_id=face_id,
        oracle_text=oracle_text,card_name=card_name,type_line=type_line,keywords=keywords,
        trusted_mechanics=trusted_mechanics,capability_registry=capability_registry,capability_profile=capability_profile,
        effect_template=effect_template,activated_or_event_node=activated_or_event_node,trigger_node=trigger_node)
    return material_rows,distinct_spans,blocks


def _closed_grouped_target_component(effects, schema, mechanics):
    if len(effects) == 2:
        from ..rules.tap_state_capability_shapes import targeted_tap_state_node_capabilities

        return bool(targeted_tap_state_node_capabilities(
            effects=effects, target_schema=schema, mechanic_ids=mechanics,
        ))
    operation = effects[0].get('op')
    if operation in _MUTATIONS:
        return True
    if operation not in {'destroy', 'place_counters_on_set', 'choose_option'}:
        return False
    from ..rules.fixed_effect_clause_shapes import closed_effect_component_capabilities

    return bool(closed_effect_component_capabilities(effects, target_schema=schema, mechanics=set(mechanics)))


def _target_partition(line, span, positions, *, conjunction, source_name, compile_effect):
    separator_length = 5 if conjunction else 2
    boundaries = (0, *(position + separator_length for position in positions), len(line))
    parts, marked, domains = [], [], []
    ordinary = True
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        fragment = line[start:end]
        if conjunction and index == 0:
            fragment = fragment.removesuffix(' and ')
        raw = fragment.strip()
        left = start + len(fragment) - len(fragment.lstrip())
        body = without_parenthetical_reminder(raw)
        another = bool(re.search(r'\banother target\b', body, re.I))
        if another:
            if len(parts) != 1:
                return None
            body = re.sub(r'\banother target\b', 'target', body, count=1, flags=re.I)
        template, effects, schema, mechanics = compile_effect(body, card_name=source_name)
        if (template is None or not 1 <= len(effects) <= 2 or not mechanics or schema is None
                or len(_TARGET_WORD.findall(body)) != 1
                or not _component_target_roles_are_exact(_scope_component_target(effects, 0), 0)):
            return None
        scalar = _fixed_public_single_target_schema(schema)
        regular = scalar and _closed_grouped_target_component(effects, schema, mechanics)
        if not regular and not _mixed_target_component(effects, schema, scalar):
            return None
        ordinary = ordinary and regular
        domains.append(tuple(schema['zones']))
        child_span = SourceSpan(span.start + left, span.start + left + len(raw), span.line)
        parts.append((raw, body, child_span))
        if another:
            marked.append((child_span.start, child_span.end))
    mixed = len(parts) == 2 and set(domains) == {('stack',), ('battlefield',)} and not marked
    if mixed or (ordinary and not conjunction and len(parts) >= 2 and (not marked or len(parts) == 2)):
        return parts, marked
    return None


def _mixed_target_component(effects, schema, scalar):
    if len(effects) != 1:
        return False
    operation = effects[0].get('op')
    return (
        scalar and schema.get('zones') == ['stack'] and operation == 'counter_stack_target'
        or scalar and schema.get('zones') == ['battlefield'] and operation == 'bounce'
        or operation == 'place_counters_on_targets' and _bounded_public_target_set_schema(schema)
    )


def independent_target_source_rows(rows, *, source_name, compile_effect):
    expanded = []
    distinct_spans = set()
    for line, material, span in rows:
        positions = _top_level_positions(line, '. ')
        conjunction = positions == () and material.casefold().startswith('counter target ')
        if conjunction:
            positions = _top_level_positions(line, ' and ')
            if positions is None or len(positions) != 1:
                expanded.append((line, material, span))
                continue
        targets = len(_TARGET_WORD.findall(material))
        if (positions is None or not 1 <= len(positions) <= 2 or targets < 2
                or compile_effect(material, card_name=source_name)[0] is not None):
            expanded.append((line, material, span))
            continue
        partitions = (positions,)
        if not conjunction and len(positions) == targets == 2:
            # A second sentence can finish the same target's independently owned result.
            partitions += tuple((position,) for position in positions)
        for partition in partitions:
            result = _target_partition(line, span, partition, conjunction=conjunction,
                                       source_name=source_name, compile_effect=compile_effect)
            if result is not None:
                parts, marked = result
                expanded.extend(parts)
                distinct_spans.update(marked)
                break
        else:
            expanded.append((line, material, span))
    return expanded, distinct_spans


def mark_independent_target_nodes(nodes,distinct_spans):
    return [replace(node,runtime_coverage=tuple(dict.fromkeys((*node.runtime_coverage,DISTINCT_CLAUSE_TARGET_COVERAGE))))
        if (node.span.start,node.span.end) in distinct_spans else node for node in nodes]
