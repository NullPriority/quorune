from __future__ import annotations

"""Full-consumption intrinsic copy-entry syntax with no runtime admission."""

from dataclasses import replace
import re

from ..entry_copy import (EntryCopySpec, ENTRY_COPY_HANDLER, ENTRY_COPY_CAPABILITY,
    ENTRY_COPY_QUERY_HANDLER, ENTRY_COPY_QUERY_CAPABILITIES)
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..target_numeric import TargetNumericCharacteristicSpec, TargetNumericCharacteristic, TargetNumericComparison
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .public_state_queries import fixed_characteristic_battlefield_query_subject
from .token_copy_templates import _fixed_exception


def _qualified_copy_query(subject):
    if subject.casefold() == 'nonland permanent':
        return ObjectQuerySpec(zones=('battlefield',), excluded_types=('land',))
    if subject.casefold() == 'permanent that entered this turn':
        return ObjectQuerySpec(zones=('battlefield',), state_predicate=PermanentStatePredicateSpec(entered_this_turn=True))
    numeric = re.fullmatch(r'creature(?P<owned> you control)? with power (?P<minimum>[0-9]+) or greater', subject, re.I)
    if numeric is not None:
        return ObjectQuerySpec(zones=('battlefield',), types_all=('creature',),
            controller='$controller' if numeric['owned'] else None,
            numeric_characteristic=TargetNumericCharacteristicSpec(
                TargetNumericCharacteristic.POWER, TargetNumericComparison.AT_LEAST, int(numeric['minimum'])))
    return None


def entry_copy_spec(text: str, *, source_name: str) -> EntryCopySpec | None:
    match = re.fullmatch(r'You may have (?P<source>.+?) enter as a copy of '
        r'(?P<subject>.+?)(?:, except (?P<exception>.+?))?\.?', ' '.join(text.strip().split()), re.I)
    if match is None or not (source_self_permanent_type(match['source']) is not None
            or SourceReferenceSpec(source_name).matches(match['source'])):
        return None
    subject = re.sub(r'^(?:any |a |an )', '', match['subject'], flags=re.I)
    subject = re.sub(r' on the battlefield$', '', subject, flags=re.I)
    subject = re.sub(r' an opponent controls$', ' your opponents control', subject, flags=re.I)
    relation = None
    for suffix, controller in ((' you control', '$controller'), (' your opponents control', 'opponents')):
        if subject.casefold().endswith(suffix):
            subject = subject[:-len(suffix)]
            relation = controller
            break
    union = re.fullmatch(r'(?P<first>artifact|creature|enchantment) or (?P<second>artifact|creature|enchantment)', subject, re.I)
    if union is not None:
        if union['first'].casefold() == union['second'].casefold():
            return None
        query = ObjectQuerySpec(zones=('battlefield',), types_any=(union['first'], union['second']))
    else:
        parsed = fixed_characteristic_battlefield_query_subject(subject)
        if parsed is not None and parsed[0] == 'any' and not parsed[2]:
            query = parsed[1]
        else:
            query = _qualified_copy_query(subject)
            if query is None:
                return None
    if relation == '$controller':
        query = replace(query, controller='$controller')
    elif relation == 'opponents':
        query = replace(query, excluded_controllers=('$controller',))
    exception = _fixed_exception(match['exception'] or '')
    if exception is None:
        return None
    try:
        return EntryCopySpec(query, exception)
    except ValueError:
        try:
            return EntryCopySpec(query, exception, schema_version=2)
        except ValueError:
            return None


def entry_copy_node(*, node_id, line, material_line, span, source_name,
        capability_registry, capability_profile, residuals):
    from .static_runtime_nodes import runtime_handler_node
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    spec = entry_copy_spec(material_line, source_name=source_name)
    if spec is None:
        return None
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=(f'intrinsic-entry-copy-v{spec.schema_version}',
            {'handler_id': ENTRY_COPY_HANDLER if spec.schema_version == 1 else ENTRY_COPY_QUERY_HANDLER,
            'schema_version': spec.schema_version, 'event': 'zone.change', 'copy': spec.to_dict()},
            ENTRY_COPY_CAPABILITY if spec.schema_version == 1 else ENTRY_COPY_QUERY_CAPABILITIES),
        kind='replacement_effect', event='zone.change', active_zone='all',
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Intrinsic copy entry requires the canonical copy/replacement owner.',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
