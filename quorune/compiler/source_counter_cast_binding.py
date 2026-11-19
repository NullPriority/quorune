"""Complete source-counter equality grammar over the ordinary cast event."""
from . import regex_working_set as re
from ..rules.source_counter_cast_value import SourceCounterCastValueSpec

def source_counter_cast_binding_spec(text,*,card_name=None):
    del card_name
    match=re.fullmatch(r'Whenever a player casts a spell with mana value equal to the number of (?P<counter>[A-Za-z][A-Za-z0-9 -]{0,39}) counters on this (?:artifact|creature|enchantment|permanent), (?P<body>.+)',text,re.I)
    if match is None:return None
    try:spec=SourceCounterCastValueSpec(' '.join(match['counter'].casefold().split()))
    except ValueError:return None
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
    return FixedPublicEventBindingSpec(event='spell.cast',variant='public_cast_value_source_counter',body=match['body'],template_id='fixed-counter-spell-cast-characteristic-trigger-v1',mechanic='trigger-event-normalized-spell-cast',condition=spec.condition(),capabilities=spec.capabilities)
