from __future__ import annotations

"""Whole fixed counter result on one typed entry occurrence."""

import re

EVENT_COUNTER_CAPABILITY='counter.producer.fixed_event_object'
EVENT_COUNTER_MECHANIC='fixed-entry-event-object-counter'
EVENT_ENTRY_OBJECT='$event.entry_object'
PUBLIC_SOURCE_COUNTER_CAPABILITY='counter.producer.fixed_public_source_event'
PUBLIC_SOURCE_COUNTER_MECHANIC='fixed-public-event-source-counter'
PUBLIC_TARGET_COUNTER_MECHANIC='fixed-public-event-target-counter'


def public_event_counter_set_result(binding,body,*,effect_template,card_name,accepted_variants=()):
    if binding.variant not in accepted_variants or re.search(r'\bcounters?\b',body,re.I) is None:return None
    compiled=effect_template(body,card_name=card_name)
    if compiled[0] is None:return None
    from ..rules.public_event_counter_set_shapes import fixed_public_event_set_result_capabilities,PUBLIC_SET_EVENT_MECHANIC
    required=fixed_public_event_set_result_capabilities(effects=compiled[1],target_schema=compiled[2],mechanic_ids=compiled[3])
    if not required:return None
    return (compiled[0],compiled[1],compiled[2],(*compiled[3],PUBLIC_SET_EVENT_MECHANIC)),True


def public_event_target_counter_result(binding, body, *, effect_template, card_name, accepted_variants=()):
    """Compose the existing fixed direct-target counter leaf without changing its target."""
    if binding.variant not in accepted_variants or re.fullmatch(
        r'Put (?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|[1-9][0-9]*) '
        r'[^.]+? counters? on (?:another )?target [^.]+\.', body, re.I,
    ) is None:
        return None
    compiled = effect_template(body, card_name=card_name)
    from ..rules.counter_placement_capability_shapes import fixed_counter_placement_node_capabilities
    capabilities = fixed_counter_placement_node_capabilities(
        effects=compiled[1], target_schema=compiled[2], mechanic_ids=compiled[3],
    )
    if (compiled[0] is None or compiled[2] is None or len(compiled[1]) != 1
            or compiled[1][0].get('card') != '$target.0' or not capabilities):
        return None
    return (compiled[0], compiled[1], compiled[2], (*compiled[3], PUBLIC_TARGET_COUNTER_MECHANIC)), True


def public_event_source_counter_result(binding, body, *, effect_template, card_name, accepted_variants=()):
    """Bind one printed fixed source result independently from the event object."""
    from ..rules.source_references import SourceReferenceSpec
    if binding.variant not in accepted_variants:
        return None
    match = re.fullmatch(
        r'Put (?P<count>a|an|one|two|three|four|five|six|seven|eight|nine|ten|[1-9][0-9]*) '
        r'(?P<counter>[^.]+?) counters? on (?P<subject>this (?:creature|artifact|permanent)|'
        + SourceReferenceSpec(card_name).regex_pattern + r')\.', body, re.I,
    )
    if match is None:
        return None
    compiled = effect_template(body, card_name=card_name)
    if compiled[0] is None or compiled[2] is not None or len(compiled[1]) != 1:
        return None
    effect = compiled[1][0]
    if (set(effect) != {'op', 'card', 'counter', 'amount', 'source'}
            or effect.get('op') != 'place_counters' or effect.get('source') != '$source'
            or type(effect.get('counter')) is not str or not effect['counter']
            or effect.get('card') not in ('$source', '$source.zone_object')
            or type(effect.get('amount')) is not int or effect['amount'] < 1):
        return None
    return ('fixed-public-event-source-counter-v1',
            ({**effect, 'card': '$source.zone_object'},), None,
            (*compiled[3], PUBLIC_SOURCE_COUNTER_MECHANIC)), True


def entry_event_counter_result(binding,body,*,effect_template,card_name):
    entry=binding.event.value in {'permanent.enter','creature.enter','artifact.enter','enchantment.enter','land.enter'}
    action=(binding.event.value=='creature.attacks' and binding.variant.startswith('public_attack_')
        or binding.event.value=='damage.dealt' and binding.variant.startswith('public_deals_'))
    tap=binding.event.value in {'permanent.tap','permanent.untap'} and binding.variant in {'public_tap_state_transition','source_public_tap_state_transition'}
    if not entry and not action and not tap:return None
    if binding.variant.startswith('one_or_more') or binding.variant in {'aggregated_zone_event','independent_dual_event'}:return None
    match=re.fullmatch(r'(?P<head>Put .+? counters? on) (?P<ref>it|that creature|that artifact|that permanent)\.',body,re.I)
    if match is None:return None
    normalized=match['head']+' this creature.'
    from .scalar_effect_amounts import scalar_effect_amount_template
    scalar=scalar_effect_amount_template(normalized,source_name=card_name,event=binding.event.value,
        compile_fixed=lambda text:effect_template(text,card_name=card_name)) if action else None
    compiled=scalar or effect_template(normalized,card_name=card_name)
    if compiled[0] is None or compiled[2] is not None or len(compiled[1])!=1:return None
    effect=compiled[1][0]
    if effect.get('op')!='place_counters' or effect.get('card') not in ('$source','$source.zone_object'):return None
    return ('fixed-entry-event-object-counter-v1',({**effect,'card':EVENT_ENTRY_OBJECT if entry else '$event.counter_object'},),None,
        (*compiled[3],EVENT_COUNTER_MECHANIC)),True
