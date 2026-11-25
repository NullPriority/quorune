from __future__ import annotations

"""Full-consumption intrinsic copy-entry syntax with no runtime admission."""

from dataclasses import replace
from . import regex_working_set as re

from ..entry_copy import (EntryCopySpec, ENTRY_COPY_HANDLER, ENTRY_COPY_CAPABILITY,
    ENTRY_COPY_QUERY_HANDLER, ENTRY_COPY_QUERY_CAPABILITIES, ENTRY_COPY_VALUES_HANDLER, ENTRY_COPY_VALUES_CAPABILITY,
    ENTRY_COPY_TAPPED_HANDLER, ENTRY_COPY_TAPPED_CAPABILITIES,
    CONTROLLED_COPY_SHIELD_HANDLER, CONTROLLED_COPY_SHIELD_CAPABILITIES,
    ANNOUNCED_X_COPY_COUNTER_HANDLER, ANNOUNCED_X_COPY_COUNTER_CAPABILITIES,
    TYPE_DIRECTED_COPY_COUNTER_HANDLER, TYPE_DIRECTED_COPY_COUNTER_CAPABILITIES,
    LEGENDARY_SNOW_COPY_HANDLER, LEGENDARY_SNOW_COPY_CAPABILITIES, LEGENDARY_SNOW_EXCEPTION,
    CONDITIONAL_VANISHING_COPY_HANDLER, CONDITIONAL_VANISHING_COPY_CAPABILITIES,
    FACE_UP_OR_ENTRY_COPY_HANDLER, FACE_UP_OR_ENTRY_COPY_CAPABILITIES,
    GRAVEYARD_PAIR_COPY_HANDLER, GRAVEYARD_PAIR_COPY_CAPABILITIES, GRAVEYARD_PAIR_COPY_QUERY)
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..target_numeric import TargetNumericCharacteristicSpec, TargetNumericCharacteristic, TargetNumericComparison
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .public_state_queries import fixed_characteristic_battlefield_query_subject
from .token_copy_templates import _fixed_exception
from ..conditional_copy_model import ConditionalCopyException,CONDITIONAL_COPY_HANDLER,CONDITIONAL_COPY_CAPABILITY
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
    if normalized.casefold() == "it's a cave in addition to its other types":
        return TokenCopyExceptionSpec(schema_version=3, add_subtypes=('cave',))
    if normalized.casefold()=="it's an artifact creature in addition to its other types, and its power and toughness are equal to this creature's power and toughness":
        return TokenCopyExceptionSpec(schema_version=3,add_card_types=('artifact','creature'),retain_source_stats=True)
    return _fixed_exception(text)


def entry_copy_spec(text: str, *, source_name: str) -> EntryCopySpec | None:
    predicate=None
    normalized=' '.join(text.strip().split())
    raid=re.fullmatch(r'Raid [—-] If you attacked this turn, (?P<copy>You may have .+)',normalized,re.I)
    if raid is not None:text=raid['copy'];predicate=EntryCopyPredicate('controller_attacked_this_turn')
    match = re.fullmatch(r'You may have (?P<source>.+?) enter (?P<tapped>tapped )?as a copy of '
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
    elif subject.casefold() == 'land card in a graveyard' and match['tapped']:
        query = ObjectQuerySpec(zones=('graveyard',), types_all=('land',))
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
    conditional=None
    exception_text=match['exception'] or ''
    if exception_text.casefold() == 'it enters with a shield counter on it if you control that creature':
        if match['tapped'] or predicate is not None:
            return None
        try:
            return EntryCopySpec(query, schema_version=7, controlled_shield=True)
        except ValueError:
            return None
    if exception_text.casefold()=="if this creature was embalmed, the token has no mana cost, it's white, and it's a zombie in addition to its other types":
        from ..token_copy_recipes import TokenCopyExceptionSpec
        exception=TokenCopyExceptionSpec(schema_version=2,remove_mana_cost=True,colors=('W',),add_subtypes=('zombie',));conditional=ConditionalCopyException()
    else:exception = _entry_copy_value_exception(exception_text,source_name=source_name)
    if exception is None:
        return None
    if match['tapped']:
        if predicate is not None or conditional is not None:
            return None
        try:
            return EntryCopySpec(query, exception, schema_version=6, tapped=True)
        except ValueError:
            return None
    try:
        if conditional is not None:return EntryCopySpec(query,exception,schema_version=5,conditional_exception=conditional)
        if predicate is not None:return EntryCopySpec(query,exception,schema_version=4,predicate=predicate)
        if exception.schema_version==3 or query.zones==('graveyard',):return EntryCopySpec(query,exception,schema_version=3)
        return EntryCopySpec(query, exception)
    except ValueError:
        try:
            return EntryCopySpec(query, exception, schema_version=2)
        except ValueError:
            return None


def _announced_x_copy_entry_spec(text, *, source_name):
    if 'x additional' not in text.casefold():
        return None
    match = re.fullmatch(r'(?P<copy>.+), except it enters with X additional \+1/\+1 counters on it\.?',
                         ' '.join(text.split()), re.I)
    if match is None:
        return None
    base = entry_copy_spec(match['copy'] + '.', source_name=source_name)
    if base is None or base.schema_version != 1:
        return None
    try:
        return replace(base, schema_version=8, announced_x_counter=True)
    except ValueError:
        return None


def _type_directed_copy_entry_spec(text, *, source_name):
    if 'additional loyalty counter' not in text.casefold():
        return None
    match = re.fullmatch(
        r"(?P<copy>.+), except it enters with an additional \+1/\+1 counter on it if it['’]s a creature, "
        r"it enters with an additional loyalty counter on it if it['’]s a planeswalker, and it isn['’]t legendary\.?",
        ' '.join(text.split()), re.I,
    )
    if match is None:
        return None
    from ..token_copy_recipes import TokenCopyExceptionSpec

    base = entry_copy_spec(match['copy'] + '.', source_name=source_name)
    if base is None or base.schema_version != 1:
        return None
    try:
        return replace(base, schema_version=9, type_directed_counters=True,
                       exception=TokenCopyExceptionSpec(remove_legendary=True))
    except ValueError:
        return None


def _legendary_snow_copy_entry_spec(text, *, source_name):
    if 'legendary and snow' not in text.casefold():
        return None
    match = re.fullmatch(
        r"(?P<copy>.+), except it['’]s legendary and snow in addition to its other types and, "
        r"if it['’]s a creature, it enters with two additional \+1/\+1 counters on it and has changeling\.?",
        ' '.join(text.split()), re.I,
    )
    if match is None:
        return None
    base = entry_copy_spec(match['copy'] + '.', source_name=source_name)
    if base is None or base.schema_version != 1:
        return None
    try:
        return replace(base, schema_version=10, exception=LEGENDARY_SNOW_EXCEPTION)
    except ValueError:
        return None


def _graveyard_pair_copy_spec(text, *, source_name):
    if 'exile two creature cards' not in text.casefold():
        return None
    match = re.fullmatch(
        r"As (?P<source>[^,]+) enters, you may exile two creature cards from graveyards\. "
        r"If you do, it enters as a copy of one of those cards with a number of additional "
        r"\+1/\+1 counters on it equal to the power of the other card\.?",
        " ".join(text.split()), re.I,
    )
    if not match or not (SourceReferenceSpec(source_name).matches(match["source"])
                         or source_self_permanent_type(match["source"])=="creature"):
        return None
    return EntryCopySpec(GRAVEYARD_PAIR_COPY_QUERY,schema_version=13)


def entry_copy_node(*, node_id, line, material_line, span, source_name,
        capability_registry, capability_profile, residuals, announced_x_available=False, spec=None):
    from .static_runtime_nodes import runtime_handler_node
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..rules.color_retaining_copy import RETAINED_COLOR_COPY_CAPABILITY, RETAINED_COLOR_ENTRY_HANDLER
    if spec is None and announced_x_available:
        spec = _announced_x_copy_entry_spec(material_line, source_name=source_name)
    spec = (spec or _type_directed_copy_entry_spec(material_line, source_name=source_name)
            or _legendary_snow_copy_entry_spec(material_line, source_name=source_name)
            or _graveyard_pair_copy_spec(material_line, source_name=source_name)
            or entry_copy_spec(material_line, source_name=source_name))
    if spec is None:
        return None
    handler, capabilities = {
        1: (ENTRY_COPY_HANDLER, (ENTRY_COPY_CAPABILITY,)),
        2: (ENTRY_COPY_QUERY_HANDLER, ENTRY_COPY_QUERY_CAPABILITIES),
        3: (ENTRY_COPY_VALUES_HANDLER, (ENTRY_COPY_CAPABILITY, ENTRY_COPY_VALUES_CAPABILITY)),
        4: (ENTRY_COPY_PREDICATE_HANDLER, (ENTRY_COPY_CAPABILITY, ENTRY_COPY_PREDICATE_CAPABILITY)),
        5: (CONDITIONAL_COPY_HANDLER, (ENTRY_COPY_CAPABILITY, CONDITIONAL_COPY_CAPABILITY)),
        6: (ENTRY_COPY_TAPPED_HANDLER, ENTRY_COPY_TAPPED_CAPABILITIES),
        7: (CONTROLLED_COPY_SHIELD_HANDLER, CONTROLLED_COPY_SHIELD_CAPABILITIES),
        8: (ANNOUNCED_X_COPY_COUNTER_HANDLER, ANNOUNCED_X_COPY_COUNTER_CAPABILITIES),
        9: (TYPE_DIRECTED_COPY_COUNTER_HANDLER, TYPE_DIRECTED_COPY_COUNTER_CAPABILITIES),
        10: (LEGENDARY_SNOW_COPY_HANDLER, LEGENDARY_SNOW_COPY_CAPABILITIES),
        11: (CONDITIONAL_VANISHING_COPY_HANDLER, CONDITIONAL_VANISHING_COPY_CAPABILITIES),
        12: (FACE_UP_OR_ENTRY_COPY_HANDLER, FACE_UP_OR_ENTRY_COPY_CAPABILITIES),
        13: (GRAVEYARD_PAIR_COPY_HANDLER, GRAVEYARD_PAIR_COPY_CAPABILITIES),
        14: (RETAINED_COLOR_ENTRY_HANDLER, (ENTRY_COPY_CAPABILITY, RETAINED_COLOR_COPY_CAPABILITY)),
    }[spec.schema_version]
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=(f'intrinsic-entry-copy-v{spec.schema_version}',
            {'handler_id': handler,
            'schema_version': spec.schema_version, 'event': 'zone.change', 'copy': spec.to_dict()},
            capabilities),
        kind='replacement_effect', event='zone.change', active_zone='all',
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Intrinsic copy entry requires the canonical copy/replacement owner.',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
