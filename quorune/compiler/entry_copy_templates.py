from __future__ import annotations

"""Full-consumption intrinsic copy-entry syntax with no runtime admission."""

from dataclasses import replace
import re

from ..entry_copy import (EntryCopySpec, ENTRY_COPY_HANDLER, ENTRY_COPY_CAPABILITY,
    ENTRY_COPY_QUERY_HANDLER, ENTRY_COPY_QUERY_CAPABILITIES, ENTRY_COPY_VALUES_HANDLER, ENTRY_COPY_VALUES_CAPABILITY)
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..target_numeric import TargetNumericCharacteristicSpec, TargetNumericCharacteristic, TargetNumericComparison
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .public_state_queries import fixed_characteristic_battlefield_query_subject
from .token_copy_templates import _fixed_exception
from ..entry_copy_predicates import EntryCopyPredicate,ENTRY_COPY_PREDICATE_HANDLER,ENTRY_COPY_PREDICATE_CAPABILITY


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


def _entry_copy_value_exception(text,*,source_name):
    from ..token_copy_recipes import TokenCopyExceptionSpec
    normalized=text.strip().rstrip('.')
    named=re.fullmatch(r"(?:its|his) name is (?P<name>.+)",normalized,re.I)
    if named is not None:
        if named['name']!=source_name:return None
        return TokenCopyExceptionSpec(schema_version=3,name=source_name)
    if normalized.casefold()=="it's a vehicle artifact and it loses all other card types":
        return TokenCopyExceptionSpec(schema_version=3,card_types=('artifact',),add_subtypes=('vehicle',))
    if normalized.casefold()=="it's an artifact":
        return TokenCopyExceptionSpec(schema_version=3,card_types=('artifact',))
    if normalized.casefold()=="it's an artifact creature in addition to its other types, and its power and toughness are equal to this creature's power and toughness":
        return TokenCopyExceptionSpec(schema_version=3,add_card_types=('artifact','creature'),retain_source_stats=True)
    return _fixed_exception(text)


def entry_copy_spec(text: str, *, source_name: str) -> EntryCopySpec | None:
    predicate=None
    normalized=' '.join(text.strip().split())
    raid=re.fullmatch(r'Raid [—-] If you attacked this turn, (?P<copy>You may have .+)',normalized,re.I)
    if raid is not None:text=raid['copy'];predicate=EntryCopyPredicate('controller_attacked_this_turn')
    match = re.fullmatch(r'You may have (?P<source>.+?) enter as a copy of '
        r'(?P<subject>.+?)(?:, except (?P<exception>.+?))?\.?', ' '.join(text.strip().split()), re.I)
    if match is None or not (source_self_permanent_type(match['source']) is not None
            or SourceReferenceSpec(source_name).matches(match['source'])):
        return None
    subject = re.sub(r'^(?:any |a |an )', '', match['subject'], flags=re.I)
    subject = re.sub(r' on the battlefield$', '', subject, flags=re.I)
    subject = re.sub(r' an opponent controls$', ' your opponents control', subject, flags=re.I)
    if subject.casefold()=='creature card in a graveyard that was put there from the battlefield this turn':
        subject='creature card in a graveyard';predicate=EntryCopyPredicate('graveyard_from_battlefield_this_turn')
    if subject.casefold()=='creature on the battlefield with mana value less than or equal to the amount of mana spent to cast this creature':
        subject='creature';predicate=EntryCopyPredicate('mana_value_at_most_mana_spent')
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
    elif subject.casefold()=='creature card in a graveyard':
        query=ObjectQuerySpec(zones=('graveyard',),types_all=('creature',))
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
    exception = _entry_copy_value_exception(match['exception'] or '',source_name=source_name)
    if exception is None:
        return None
    try:
        if predicate is not None:return EntryCopySpec(query,exception,schema_version=4,predicate=predicate)
        if exception.schema_version==3 or query.zones==('graveyard',):return EntryCopySpec(query,exception,schema_version=3)
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
            {'handler_id': {1:ENTRY_COPY_HANDLER,2:ENTRY_COPY_QUERY_HANDLER,3:ENTRY_COPY_VALUES_HANDLER,4:ENTRY_COPY_PREDICATE_HANDLER}[spec.schema_version],
            'schema_version': spec.schema_version, 'event': 'zone.change', 'copy': spec.to_dict()},
            ENTRY_COPY_CAPABILITY if spec.schema_version == 1 else ENTRY_COPY_QUERY_CAPABILITIES if spec.schema_version==2 else (ENTRY_COPY_CAPABILITY,ENTRY_COPY_VALUES_CAPABILITY) if spec.schema_version==3 else (ENTRY_COPY_CAPABILITY,ENTRY_COPY_PREDICATE_CAPABILITY)),
        kind='replacement_effect', event='zone.change', active_zone='all',
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Intrinsic copy entry requires the canonical copy/replacement owner.',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
