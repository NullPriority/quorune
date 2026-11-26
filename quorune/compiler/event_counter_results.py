from __future__ import annotations

"""Whole fixed counter result on one typed public occurrence."""

from . import regex_working_set as re
from ..event_counter_reference import EVENT_ENTRY_OBJECT
from ..face_up_effect_model import TURN_FACE_UP_EVENT
from ..transform_timestamp_model import TRANSFORM_EVENT
from .public_event_closure_variants import ATTACHED_CREATURE_ATTACK_VARIANTS

EVENT_COUNTER_CAPABILITY='counter.producer.fixed_event_object'
EVENT_COUNTER_MECHANIC='fixed-entry-event-object-counter'
PUBLIC_SOURCE_COUNTER_CAPABILITY='counter.producer.fixed_public_source_event'
PUBLIC_SOURCE_COUNTER_MECHANIC='fixed-public-event-source-counter'
PUBLIC_TARGET_COUNTER_MECHANIC='fixed-public-event-target-counter'


def counter_placement_blocking_result(binding, body):
    from ..counter_placement_event_model import COUNTER_PLACEMENT_EVENT
    from ..replacement.immutable import FrozenMap
    from ..rules.counter_placement_blocking import (
        COUNTER_PLACEMENT_BLOCKING_MECHANICS, COUNTER_PLACEMENT_BLOCKING_TEMPLATE,
        counter_placement_blocking_condition, counter_placement_blocking_effect,
    )

    if ((binding.event.value, binding.active_zone) != (COUNTER_PLACEMENT_EVENT, "battlefield")
            or binding.variant != "counter_placement_one_or_more"
            or " ".join(body.split()).casefold()
            != "until end of turn, that creature can't be blocked by creatures your opponents control."
            or FrozenMap(binding.event_condition or {}) != FrozenMap(counter_placement_blocking_condition())):
        return None
    return (COUNTER_PLACEMENT_BLOCKING_TEMPLATE, (counter_placement_blocking_effect(),), None,
            COUNTER_PLACEMENT_BLOCKING_MECHANICS), True


def counter_recipient_control_result(binding, body):
    from ..counter_placement_event_model import COUNTER_PLACEMENT_EVENT
    from ..replacement.immutable import FrozenMap
    from ..rules.counter_recipient_control import (
        COUNTER_RECIPIENT_CONTROL_MECHANICS, COUNTER_RECIPIENT_CONTROL_TEMPLATE, counter_recipient_control_condition,
        counter_recipient_control_instructions,
    )

    if ((binding.event.value, binding.active_zone, binding.variant)
            != (COUNTER_PLACEMENT_EVENT, "battlefield", "counter_placement_one_or_more")
            or FrozenMap(binding.event_condition or {}) != FrozenMap(counter_recipient_control_condition())
            or " ".join(body.split()).casefold() != (
                "draw a card if you control that creature. if you don't control it, its controller loses 1 life."
            )):
        return None
    return (COUNTER_RECIPIENT_CONTROL_TEMPLATE, counter_recipient_control_instructions(),
            None, COUNTER_RECIPIENT_CONTROL_MECHANICS), True


def event_counter_threshold_result(binding, body, *, effect_template, card_name):
    if binding.variant != "enchanted_creature_attacks" or binding.event_condition != {
        "field": "source_attachment_target_ref", "op": "eq", "value": "$context.card",
    }:
        return None
    match = re.fullmatch(
        r"(?P<place>Put a \+1/\+1 counter on it\.) Then if it has "
        r"(?P<minimum>two|three|four|five) or more \+1/\+1 counters on it, sacrifice this Aura\.",
        body, re.I,
    )
    if match is None:
        return None
    placement = entry_event_counter_result(binding, match["place"], effect_template=effect_template, card_name=card_name)
    if placement is None:
        return None
    from ..rules.event_counter_threshold import EVENT_COUNTER_THRESHOLD_MECHANIC, event_counter_threshold_instruction

    compiled, current = placement
    minimum = {"two": 2, "three": 3, "four": 4, "five": 5}[match["minimum"].casefold()]
    return ("fixed-attached-attack-counter-threshold-v1",
            (*compiled[1], event_counter_threshold_instruction(minimum)), None,
            (*compiled[3], EVENT_COUNTER_THRESHOLD_MECHANIC)), current


def public_event_counter_set_result(binding,body,*,effect_template,card_name,accepted_variants=()):
    if controller_result := counter_recipient_control_result(binding, body):
        return controller_result
    if blocking := counter_placement_blocking_result(binding, body):
        return blocking
    if binding.variant == "one_or_more_qualified_attack" and re.fullmatch(
        r"Put a \+1/\+1 counter on each of them\.", body.strip(), re.I,
    ) is not None:
        from ..rules.attack_batches import declared_attack_counter_condition, declared_attack_counter_effect

        try:
            declared_attack_counter_condition(binding.event_condition)
        except ValueError:
            return None
        from ..rules.public_event_counter_set_shapes import PUBLIC_SET_EVENT_MECHANIC

        return ("fixed-declared-attack-group-counters-v1", (declared_attack_counter_effect(),),
                None, ("cr-122-counters", PUBLIC_SET_EVENT_MECHANIC)), True
    if (binding.variant == "artifact_any_graveyard" and binding.event.value == "artifact.graveyard"
            and binding.active_zone == "battlefield" and binding.event_condition is None):
        from .counter_resolution_effect_templates import source_and_controlled_subtype_counter_template

        source_subtype = source_and_controlled_subtype_counter_template(body, card_name)
        if source_subtype is not None:
            return source_subtype, True
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


def public_event_source_counter_result(binding, body, *, effect_template, card_name, accepted_variants=(), allow_source_pronoun=False):
    """Bind one printed fixed source result independently from the event object."""
    if binding.variant == "one_or_more_controlled_tokens_enter" and re.fullmatch(
        r"Put that many \+1/\+1 counters on this creature\.", body.strip(), re.I,
    ) is not None:
        from ..rules.aggregated_arrival_counters import (
            MATCHED_ARRIVAL_MECHANIC, matching_arrival_counter_effect, matching_token_arrival_condition,
        )

        if binding.event.value == "permanent.enter" and matching_token_arrival_condition(binding.event_condition):
            return ("matching-token-arrival-source-counter-v1", (matching_arrival_counter_effect(),),
                    None, ("cr-122-counters", MATCHED_ARRIVAL_MECHANIC)), True
    if binding.variant not in accepted_variants or re.search(r'\bcounters?\b', body, re.I) is None:
        return None
    if allow_source_pronoun:
        from .source_self_effect_templates import normalized_source_result_body
        body=normalized_source_result_body(body) or body
    compiled = effect_template(body, card_name=card_name)
    if compiled[0] is None or compiled[2] is not None:
        return None
    from ..rules.fixed_controller_effect_shapes import fixed_counter_controller_effect_sequence_node_capabilities
    sequence = fixed_counter_controller_effect_sequence_node_capabilities(
        effects=compiled[1], target_schema=None, mechanic_ids=compiled[3])
    if len(compiled[1]) != 1 and not sequence:
        return None
    counters = [effect for effect in compiled[1] if effect.get('op') == 'place_counters']
    if len(counters) != 1:
        return None
    effect = counters[0]
    if (set(effect) != {'op', 'card', 'counter', 'amount', 'source'}
            or effect.get('op') != 'place_counters' or effect.get('source') != '$source'
            or type(effect.get('counter')) is not str or not effect['counter']
            or effect.get('card') not in ('$source', '$source.zone_object')
            or type(effect.get('amount')) is not int or effect['amount'] < 1):
        return None
    return (compiled[0] if sequence else 'fixed-public-event-source-counter-v1',
            tuple({**child, 'card': '$source.zone_object'} if child is effect else child for child in compiled[1]), None,
            (*compiled[3], PUBLIC_SOURCE_COUNTER_MECHANIC)), True


def entry_event_counter_result(binding,body,*,effect_template,card_name):
    entry=binding.event.value in {'permanent.enter','creature.enter','artifact.enter','enchantment.enter','land.enter'}
    action = (
        binding.event.value == 'creature.attacks' and (
            binding.variant.startswith('public_attack_')
            or binding.variant in {'singular_qualified_attack', *ATTACHED_CREATURE_ATTACK_VARIANTS}
        )
        or binding.event.value == 'damage.dealt' and binding.variant.startswith('public_deals_')
    )
    tap=binding.event.value in {'permanent.tap','permanent.untap'} and binding.variant in {'public_tap_state_transition','source_public_tap_state_transition'}
    transform = binding.event.value == TRANSFORM_EVENT and binding.variant == "controlled_permanent_transforms_into"
    turn_up = binding.event.value == TURN_FACE_UP_EVENT and binding.variant in {
        "controller_turns_up_permanent", "controlled_permanent_turned_face_up",
    }
    if not (entry or action or tap or transform or turn_up):return None
    if binding.variant.startswith('one_or_more') or binding.variant in {'aggregated_zone_event','independent_dual_event'}:return None
    operation = r'Put .+? counters? on'
    if binding.variant in ATTACHED_CREATURE_ATTACK_VARIANTS:
        operation += r'|Double the number of .+? counters on'
    match = re.fullmatch(rf'(?P<head>{operation}) (?:it|that creature|that artifact|that permanent)\.', body, re.I)
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
