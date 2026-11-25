"""A closed damage head predicates source and damaged-creature facts separately."""
from . import regex_working_set as re
from ..damage_recipient_model import DAMAGE_PERMANENT_VARIANT,DAMAGE_PERMANENT_CAPABILITY
from ..rules.source_references import SourceReferenceSpec,source_self_permanent_type


def _damage_dealer_result_binding_spec(text):
    match = re.fullmatch(
        r"Whenever a (?P<kind>creature|permanent) deals (?P<combat>combat )?damage to you, (?P<body>.+)",
        text, re.I,
    )
    if match is None:
        return None
    role = (match["kind"].casefold(), bool(match["combat"]), match["body"].replace("’", "'").casefold())
    variant = {
        ("creature", False, "destroy it."): "damage_creature_destroy",
        ("permanent", False, "return it to its owner's hand."): "damage_permanent_return",
        ("creature", True, "destroy that creature. create a 1/1 white and black spirit creature token with flying."):
            "combat_damage_creature_destroy_token",
    }.get(role)
    if variant is None:
        return None
    from .fixed_public_event_trigger_bindings import _spec
    from ..rules.occurrence_object_results import damage_dealer_result_condition

    return _spec("damage.dealt", variant, match["body"], "fixed-counter-public-damage-dealer-response-v1",
                 "trigger-event-normalized-damage", condition=damage_dealer_result_condition(variant))


def _opponent_damage_counter_binding_spec(text):
    match = re.fullmatch(
        r"Whenever a source an opponent controls deals damage to you, "
        r"(?P<body>you may put that many \+1/\+1 counters on this creature\.)", text, re.I,
    )
    if match is None:
        return None
    from .fixed_public_event_trigger_bindings import _spec
    from ..rules.optional_counter_capability_shapes import opponent_damage_to_controller_condition

    return _spec(
        "damage.dealt", "opponent_damage_optional_source_counters", match["body"],
        "fixed-counter-opponent-damage-source-counters-v1", "trigger-event-normalized-damage",
        condition=opponent_damage_to_controller_condition(),
    )


def _recipient_damage_counter_binding_spec(text):
    match = re.fullmatch(
        r"Whenever you(?:['’]re| are) dealt damage, "
        r"(?P<body>you may put that many \+1/\+1 counters on this creature\.)", text, re.I,
    )
    if match is None:
        return None
    from .fixed_public_event_trigger_bindings import _spec
    from ..rules.optional_counter_capability_shapes import (
        RECIPIENT_DAMAGE_COUNTER_BINDING_TEMPLATE, RECIPIENT_DAMAGE_COUNTER_VARIANT, damage_to_controller_condition,
    )

    return _spec(
        "damage.dealt", RECIPIENT_DAMAGE_COUNTER_VARIANT, match["body"],
        RECIPIENT_DAMAGE_COUNTER_BINDING_TEMPLATE, "trigger-event-normalized-damage",
        condition=damage_to_controller_condition(),
    )


def damage_permanent_binding_spec(text,*,card_name):
    if counters := _opponent_damage_counter_binding_spec(text) or _recipient_damage_counter_binding_spec(text):
        return counters
    if dealer := _damage_dealer_result_binding_spec(text):
        return dealer
    control = re.fullmatch(
        r"Whenever a creature deals combat damage to you, "
        r"(?P<body>that creature[\'’]s controller gains control of this land\.?)", text, re.I,
    )
    if control is not None:
        from .fixed_public_event_trigger_bindings import _spec
        from ..rules.recipient_control_shapes import DAMAGE_DEALER_CONTROL_VARIANT, damage_dealer_control_condition

        return _spec("damage.dealt", DAMAGE_DEALER_CONTROL_VARIANT, control["body"],
                     "fixed-counter-public-damage-trigger-v1", "trigger-event-normalized-damage",
                     condition=damage_dealer_control_condition())
    match=re.fullmatch(r'(?:When|Whenever) (?P<subject>.+?) deals (?P<combat>combat )?damage to a creature, (?P<body>.+)',text,re.I)
    if match is None:return None
    from .fixed_public_event_trigger_bindings import _spec,_all_conditions,_damage_subject_condition
    subject=' '.join(match['subject'].casefold().split())
    if source_self_permanent_type(subject) is not None or card_name and SourceReferenceSpec(card_name).matches(match['subject']):
        condition=_all_conditions({'field':'source','op':'eq','value':'$source.ref'},
            {'field':'source_logical_object_id','op':'eq','value':'$source.logical_object_id'})
    else:
        condition=_damage_subject_condition(subject,card_name=card_name)
        if condition is None:return None
        if subject in {'a creature you control','an artifact creature you control'}:
            condition=_all_conditions(condition,{'field':'source_types','op':'contains_any','value':['creature']})
    return _spec('damage.dealt',DAMAGE_PERMANENT_VARIANT,match['body'],
        'fixed-counter-public-damage-trigger-v1','trigger-event-normalized-damage',
        condition=_all_conditions(condition,{'field':'target_kind','op':'eq','value':'permanent'},
            {'field':'target_types','op':'contains_any','value':['creature']},
            {'field':'combat','op':'truthy','value':True} if match['combat'] else None),
        capabilities=(DAMAGE_PERMANENT_CAPABILITY,))
