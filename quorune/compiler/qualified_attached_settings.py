"""Compile live attached Creature/Vehicle qualifications into receiver predicates."""
from . import regex_working_set as re

from ..semantic_runtime.qualified_attached_settings import (
    HANDLER, DEPENDENCIES, CREATURE_QUERY, VEHICLE_QUERY, QualifiedAttachedCharacteristicsHandler,
)


def qualified_attached_characteristics_handler(text, *, source_name='source'):
    match = re.fullmatch(r'As long as enchanted permanent is a (?P<quality>creature|Vehicle), '
        r'(?P<body>it\b.+)', ' '.join(text.split()), re.I)
    if match is None:
        return None
    body = re.sub(r"^it['’]s\b", 'is', match['body'], flags=re.I)
    body = re.sub(r'^it\b\s*', '', body, flags=re.I)
    creature = match['quality'].casefold() == 'creature'
    from .continuous_templates import attached_fixed_characteristics_handler
    from .fixed_characteristic_settings import fixed_characteristic_setting_handler
    subject = 'Enchanted creature' if creature else 'Enchanted permanent'
    compiled = (attached_fixed_characteristics_handler(subject+' '+body, source_name=source_name)
                or fixed_characteristic_setting_handler(subject+' '+body, source_name=source_name))
    if compiled is None:
        return None
    descriptor = {'handler_id': HANDLER, 'schema_version': 1, 'event': 'characteristics.evaluate',
                  'receiver_query': (CREATURE_QUERY if creature else VEHICLE_QUERY).to_dict(),
                  'modifier': compiled[1]['modifier']}
    try:QualifiedAttachedCharacteristicsHandler().validate(descriptor)
    except (TypeError, ValueError):return None
    return ('continuous-qualified-attached-characteristics-v1', descriptor,
            tuple(sorted({*DEPENDENCIES, *compiled[2]})))


def attached_characteristics_handler(text, *, source_name='source'):
    """Preserve the original complete leaf before trying the qualified production."""
    from .continuous_templates import attached_fixed_characteristics_handler
    return (attached_fixed_characteristics_handler(text, source_name=source_name)
            or qualified_attached_characteristics_handler(text, source_name=source_name))
