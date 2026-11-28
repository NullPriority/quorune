"""Unblocked syntax binds to the post-blocker-declaration occurrence."""
from . import regex_working_set as re
from ..rules.source_references import SourceReferenceSpec
from ..unblocked_attacker_model import (
    UNBLOCKED_ATTACKER_EVENT, UNBLOCKED_ATTACKER_CAPABILITY, UNBLOCKED_ATTACKER_VARIANT,
    UNBLOCKED_PLAYER_ATTACK_EVENT, UNBLOCKED_PLAYER_ATTACK_VARIANT,
    intrinsic_unblocked_condition, unblocked_player_attack_condition, unblocked_source_control_effects,
)


def unblocked_attacker_binding_spec(text,*,card_name):
    grouped = re.fullmatch(
        r"Whenever one or more creatures an opponent controls attack you and aren[\x27’]t blocked, (?P<body>.+)",
        text, re.I,
    )
    if grouped is not None:
        from .fixed_public_event_trigger_bindings import _spec

        return _spec(
            UNBLOCKED_PLAYER_ATTACK_EVENT, UNBLOCKED_PLAYER_ATTACK_VARIANT, grouped["body"],
            "fixed-counter-public-attack-trigger-v1", "trigger-event-normalized-public-action",
            condition=unblocked_player_attack_condition(), capabilities=(UNBLOCKED_ATTACKER_CAPABILITY,),
        )
    match=re.fullmatch(r'(?:When|Whenever) (?P<source>.+?) attacks and isn[\x27’]t blocked, (?P<body>.+)',text,re.I)
    if match is None:return None
    if match['source'].casefold()!='this creature' and not (card_name and SourceReferenceSpec(card_name).matches(match['source'])):return None
    from .fixed_public_event_trigger_bindings import _spec
    return _spec(UNBLOCKED_ATTACKER_EVENT,UNBLOCKED_ATTACKER_VARIANT,match['body'],
        'fixed-counter-public-attack-trigger-v1','trigger-event-normalized-public-action',
        condition=intrinsic_unblocked_condition(), capabilities=(UNBLOCKED_ATTACKER_CAPABILITY,))


def unblocked_source_control_result(binding, body, *, card_name):
    if ((binding.event.value, binding.variant, binding.active_zone)
            != (UNBLOCKED_PLAYER_ATTACK_EVENT, UNBLOCKED_PLAYER_ATTACK_VARIANT, "battlefield")
            or binding.event_condition != unblocked_player_attack_condition()):
        return None
    from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
    from .event_player_results import EVENT_PLAYER_RESULT_MECHANIC
    from ..recipient_control_model import RECIPIENT_CONTROL_MECHANIC

    match = re.fullmatch(
        rf"That player draws (?P<count>{FIXED_COUNT_PATTERN}) cards? and gains control of (?P<source>[^.]+)\. Untap it\.",
        " ".join(body.split()), re.I,
    )
    if match is None:
        return None
    source = match["source"].casefold()
    if source not in {"this artifact", "this permanent"} and not SourceReferenceSpec(card_name).matches(match["source"]):
        return None
    count = fixed_number(match["count"])
    if count <= 0:
        return None
    return (
        "fixed-unblocked-player-draw-source-control-v1",
        unblocked_source_control_effects(count),
        None,
        (EVENT_PLAYER_RESULT_MECHANIC, RECIPIENT_CONTROL_MECHANIC, "cr-611-continuous-effects",
         "cr-121-drawing-a-card", "tap-and-untap"),
    ), False
