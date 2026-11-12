from __future__ import annotations

"""Public death-event card returns through the existing zone mutation owner."""

import re

from ..event_card_return import (EVENT_CARD_RETURN_MECHANIC, event_card_return_effect,
    EVENT_BATTLEFIELD_RETURN_MECHANIC, event_battlefield_return_effect,
    EVENT_LIBRARY_MOVE_CAPABILITY,
    event_library_departure_condition, event_library_move_effect)
from ..public_zone_moves import FIXED_OWNER_ZONE_MOVE_MECHANIC
from ..rules.source_references import SourceReferenceSpec


_DEPARTURE_LIBRARY_TRIGGER = re.compile(
    r"(?:When|Whenever) (?P<source>.+?) (?P<departure>"
    r"dies(?: or is put into exile from the battlefield)?|is put into your graveyard from the battlefield), "
    r"(?P<body>.+)", re.I,
)
_LIBRARY_ORDINALS = {"second": 2, "third": 3, "fourth": 4, "fifth": 5}


def _departure_library_instruction(body, *, card_name, scope):
    match = re.fullmatch(r"(?P<optional>you may )?(?P<move>.+?)(?:, then scry (?P<scry>[1-9][0-9]*))?\.", body, re.I)
    if match is None or match["optional"] and match["scry"]:
        return None
    subject = r"(?:it|this card|" + SourceReferenceSpec(card_name).regex_pattern + r")"
    whose = r"its owner['’]s|your" if scope == "own_graveyard" else r"its owner['’]s"
    if re.fullmatch(rf"shuffle {subject} into (?:{whose}) library", match["move"], re.I):
        return event_library_move_effect(position="top", shuffle=True), bool(match["optional"]), match["scry"]
    put = re.fullmatch(
        rf"put {subject} (?P<edge>on (?:the )?top of|on (?:the )?bottom of|into) (?:{whose}) library"
        r"(?: (?P<ordinal>second|third|fourth|fifth) from the top)?",
        match["move"], re.I,
    )
    if put is None:
        return None
    edge = put["edge"].casefold()
    if edge == "into":
        if not put["ordinal"]:
            return None
        position = _LIBRARY_ORDINALS[put["ordinal"].casefold()]
    else:
        if put["ordinal"]:
            return None
        position = "top" if "top" in edge else "bottom"
    return event_library_move_effect(position=position, shuffle=False), bool(match["optional"]), match["scry"]


def source_departure_library_binding(material_line: str, *, card_name: str | None):
    from .fixed_public_event_trigger_bindings import _source_subject, _spec

    match = _DEPARTURE_LIBRARY_TRIGGER.fullmatch(material_line)
    if match is None or not card_name or not _source_subject(match["source"], card_name=card_name):
        return None
    departure = match["departure"].casefold()
    if "your graveyard" in departure:
        scope = "own_graveyard"
    else:
        scope = "graveyard_or_exile" if "exile" in departure else "graveyard"
    if _departure_library_instruction(match["body"], card_name=card_name, scope=scope) is None:
        return None
    return _spec("permanent.leave", "source_library_departure", match["body"],
        "fixed-counter-public-zone-trigger-v1", "trigger-event-normalized-zone-change",
        condition=event_library_departure_condition(scope), capabilities=(EVENT_LIBRARY_MOVE_CAPABILITY,))


def event_library_move_template(binding, body, *, card_name, effect_template):
    if binding.variant != "source_library_departure" or body != binding.body:
        return None
    scope = "own_graveyard" if binding.event_condition == event_library_departure_condition("own_graveyard") else "graveyard"
    result = _departure_library_instruction(body, card_name=card_name, scope=scope)
    if result is None:
        return None
    effect, optional, scry = result
    mechanics = (FIXED_OWNER_ZONE_MOVE_MECHANIC,)
    template = "fixed-event-library-move-v1"
    if optional:
        from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_MECHANIC, FixedOptionalEffectTemplate

        return FixedOptionalEffectTemplate(template, (effect,), None, (FIXED_OPTIONAL_EFFECT_MECHANIC, *mechanics)).compiled()
    if scry:
        from .closed_effect_programs import CLOSED_EFFECT_PROGRAM_MECHANIC

        child, instructions, targets, child_mechanics = effect_template(f"Scry {scry}.", card_name=card_name)
        if child is None or targets is not None or len(instructions) != 1 or instructions[0].get("op") != "scry":
            return None
        return (template, (effect, *instructions), None,
                (CLOSED_EFFECT_PROGRAM_MECHANIC, *mechanics, *child_mechanics))
    return template, (effect,), None, mechanics


def self_death_return_binding(material_line: str, *, card_name: str | None = None):
    from .fixed_public_event_trigger_bindings import _spec
    match = re.fullmatch(r'(?:When|Whenever) (?P<source>this creature|.+?) dies, (?P<body>.+)', material_line, re.I)
    if match is None or not (match['source'].casefold() == 'this creature' or card_name and SourceReferenceSpec(card_name).matches(match['source'])):
        return None
    subject = r"it|this card"
    if card_name:
        subject += "|" + SourceReferenceSpec(card_name).regex_pattern
    if not re.fullmatch(r"return (?:" + subject + r") to (?:its owner['’]s hand|the battlefield "
            r"(?:tapped )?under its owner['’]s control(?: with a \+1/\+1 counter on it)?)\.?", match['body'], re.I):
        return None
    battlefield = 'the battlefield' in match['body'].casefold()
    return _spec('creature.dies.self', 'source_death_return', match['body'],
        'fixed-counter-source-death-return-v1', 'trigger-event-normalized-zone-change',
        capabilities=('zone.return.fixed_event_battlefield' if battlefield else 'zone.return.fixed_event_card',))


def event_card_return_template(binding, body: str, *, card_name: str, effect_template):
    if binding.variant == "source_library_departure":
        return event_library_move_template(binding, body, card_name=card_name, effect_template=effect_template)
    attached_graveyard = (binding.event.value == 'permanent.graveyard'
                          and binding.variant == 'enchanted_permanent_graveyard')
    if binding.event.value not in {'creature.dies.self', 'permanent.graveyard.self', 'creature.dies'} and not attached_graveyard:
        return None
    source_event = binding.event.value.endswith('.self') or binding.variant in {'source_creature_dies', 'source_dies'}
    attached = binding.variant in {'enchanted_creature_dies', 'equipped_creature_dies'}
    if not (source_event or attached or attached_graveyard):
        return None
    if (attached_graveyard or attached and body.casefold().startswith('return it ')) and body != binding.body:
        return None
    subject = r'it|this card|' + SourceReferenceSpec(card_name).regex_pattern if source_event else r'it|that card'
    battlefield = re.fullmatch(r'return (?:' + subject + r") to the battlefield "
        r"(?P<tapped>tapped )?under (?P<controller>its owner['’]s|your) control"
        r"(?P<counter> with a \+1/\+1 counter on it)?\.?", body, re.I)
    if battlefield is not None:
        return_to_controller = battlefield['controller'].casefold() == 'your'
        if attached_graveyard or return_to_controller and (not attached or body != binding.body):
            return None
        return ('fixed-departed-event-battlefield-return-v1',
            (event_battlefield_return_effect(tapped=battlefield['tapped'] is not None,
                entry_counter='+1/+1' if battlefield['counter'] else None,
                controller='$controller' if return_to_controller else '$context.owner'),), None,
            (EVENT_BATTLEFIELD_RETURN_MECHANIC,))
    if not re.fullmatch(r'return (?:' + subject + r") to its owner['’]s hand\.?", body, re.I):
        return None
    return ('fixed-departed-event-card-return-v1', (event_card_return_effect(),), None, (EVENT_CARD_RETURN_MECHANIC,))
