"""Exact permanent receiving heads and one attached tap/receiving pair."""
from . import regex_working_set as re

from ..entry_designations import LINKED_PLAYER_RECEIVED_DAMAGE_TEMPLATE
from ..rules.damage_capability_shapes import linked_player_received_damage_condition, linked_player_received_damage_effect
from ..received_damage_model import (
    RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT, RECEIVED_DAMAGE_MECHANIC,
)
from ..rules.event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition
from ..creature_subtypes import canonical_creature_subtype
from ..rules.event_damage_sources import (
    PAIRED_RECEIVING_DAMAGE_BINDING, PAIRED_RECEIVING_DAMAGE_RESULT,
    TARGETED_RECEIVING_DAMAGE_BINDING, TARGETED_RECEIVING_DAMAGE_RESULT,
    TARGETED_RECEIVING_LIFE_PROHIBITION_BINDING,
    controlled_permanent_receiving_damage_condition, controlled_permanent_receiving_damage_result,
    paired_receiving_damage_capabilities, paired_receiving_damage_condition,
    paired_receiving_damage_effect, paired_receiving_damage_target,
    targeted_receiving_damage_condition, targeted_receiving_damage_effect,
    targeted_receiving_damage_target, targeted_receiving_life_prohibition_effect,
)
from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC

RECEIVED_PERMANENT_VARIANTS = frozenset({
    'received_any_creature', 'received_enchanted_creature', 'received_equipped_creature',
    'enchanted_creature_tap_or_received_damage',
})
ATTACHED_TAP_RECEIVED_VARIANT = 'enchanted_creature_tap_or_received_damage'
_LINKED_PLAYER_RECEIVING_VARIANT = "source_received_damage_to_linked_player"
_CONTROLLED_PERMANENT_RECEIVING_VARIANT = "controlled_permanent_receiving_source_damage"
_CONTROLLED_PERMANENT_RECEIVING = re.compile(
    r"Whenever a permanent you control is dealt damage, "
    r"(?P<body>this creature deals that much damage to any target\.)",
    re.I,
)
_LINKED_PLAYER_RECEIVING = re.compile(
    r"Whenever this creature is dealt damage, (?P<body>it deals that much damage to the chosen player\.)",
    re.I,
)


def linked_player_receiving_result(binding, body):
    if binding.variant != _LINKED_PLAYER_RECEIVING_VARIANT or body != binding.body:
        return None
    return (
        (LINKED_PLAYER_RECEIVED_DAMAGE_TEMPLATE, (linked_player_received_damage_effect(),), None,
         ("cr-120-damage",)), True,
    )


def _creature_condition(*, attached=False, received=True):
    conditions = []
    if attached:
        conditions.append({'field': 'source_attachment_target_ref', 'op': 'eq', 'value': '$context.card'})
    if received:
        conditions.append({'field': 'target_kind', 'op': 'eq', 'value': 'permanent'})
    conditions.append({'field': 'target_types' if received else 'types', 'op': 'contains_any', 'value': ['creature']})
    return {'all': conditions}


def _targeted_receiving_binding(material_line, card_name):
    if " deals that much damage to " not in material_line.casefold():
        return None
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec, _source_subject

    match = re.fullmatch(
        r"Whenever (?P<subject>.+?) is dealt damage, "
        r"(?P<body>(?P<dealer>it|he|she) deals that much damage to (?P<target>.+)\.)",
        material_line, re.I,
    )
    if match is None:
        return None
    target = match["target"].casefold().replace("’", "'")
    if target == "any other target" and _source_subject(match["subject"], card_name=card_name):
        subtype = None
    else:
        recipient = re.fullmatch(r"a[n]? (?P<subtype>[a-z -]+) you control", match["subject"], re.I)
        if recipient is None or match["dealer"].casefold() != "it":
            return None
        subtype = canonical_creature_subtype(recipient["subtype"])
        if subtype is None or target != f"any target that isn't a {subtype}":
            return None
    return FixedPublicEventBindingSpec(
        event=RECEIVED_DAMAGE_EVENT, variant=TARGETED_RECEIVING_DAMAGE_RESULT, body=match["body"],
        template_id=TARGETED_RECEIVING_DAMAGE_BINDING, mechanic=RECEIVED_DAMAGE_MECHANIC,
        condition=targeted_receiving_damage_condition(subtype),
    )


def _paired_receiving_binding(material_line, card_name):
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec, _source_subject

    match = re.fullmatch(
        r"Whenever (?P<subject>.+?) or a creature it['’]s paired with is dealt damage, "
        r"(?P<body>(?P<dealer>.+?) deals that much damage to target opponent\.)",
        material_line, re.I,
    )
    if match is None or not all(
        _source_subject(match[role], card_name=card_name) for role in ("subject", "dealer")
    ):
        return None
    return FixedPublicEventBindingSpec(
        event=RECEIVED_DAMAGE_EVENT, variant=PAIRED_RECEIVING_DAMAGE_RESULT, body=match["body"],
        template_id=PAIRED_RECEIVING_DAMAGE_BINDING, mechanic=RECEIVED_DAMAGE_MECHANIC,
        condition=paired_receiving_damage_condition(),
        capabilities=paired_receiving_damage_capabilities(
            (paired_receiving_damage_effect(),), paired_receiving_damage_target(), ("cr-115-targets", "cr-120-damage"),
        ),
    )


_RECEIVING_LIFE_PROHIBITION_VARIANT = "targeted-receiving-life-prohibition-v1"
_RECEIVING_LIFE_PROHIBITION_RIDER = "if a player is dealt damage this way, they can't gain life for the rest of the game."


def _targeted_receiving_life_prohibition_binding(material_line, card_name):
    lead, separator, rider = material_line.rpartition(". ")
    if not separator or rider.casefold().replace("’", "'") != _RECEIVING_LIFE_PROHIBITION_RIDER:
        return None
    first = _targeted_receiving_binding(lead + ".", card_name)
    if first is None or first.condition != targeted_receiving_damage_condition(None):
        return None
    from dataclasses import replace

    return replace(
        first, variant=_RECEIVING_LIFE_PROHIBITION_VARIANT,
        template_id=TARGETED_RECEIVING_LIFE_PROHIBITION_BINDING, body=first.body + " " + rider,
    )


def targeted_receiving_damage_result(binding, body):
    if body != binding.body:
        return None
    if binding.variant == _CONTROLLED_PERMANENT_RECEIVING_VARIANT:
        effects, schema = controlled_permanent_receiving_damage_result()
        return (
            (TARGETED_RECEIVING_DAMAGE_RESULT, effects, schema,
             ("cr-115-targets", "cr-120-damage", SCALAR_AMOUNT_MECHANIC)),
            True,
        )
    if binding.variant == PAIRED_RECEIVING_DAMAGE_RESULT:
        return (
            (PAIRED_RECEIVING_DAMAGE_RESULT, (paired_receiving_damage_effect(),),
             paired_receiving_damage_target(), ("cr-115-targets", "cr-120-damage")),
            True,
        )
    if binding.variant == _RECEIVING_LIFE_PROHIBITION_VARIANT:
        return (
            (TARGETED_RECEIVING_DAMAGE_RESULT, (targeted_receiving_life_prohibition_effect(),),
             targeted_receiving_damage_target(None), ("cr-115-targets", "cr-120-damage", "cr-119-life")),
            True,
        )
    if binding.variant != TARGETED_RECEIVING_DAMAGE_RESULT:
        return None
    condition = binding.event_condition
    subtype = next(
        (row["value"][0] for row in condition["all"] if row["field"] == "target_subtypes"),
        None,
    )
    return (
        (
            TARGETED_RECEIVING_DAMAGE_RESULT,
            (targeted_receiving_damage_effect(),), targeted_receiving_damage_target(subtype),
            ("cr-115-targets", "cr-120-damage"),
        ),
        True,
    )


def received_permanent_binding_spec(material_line: str, *, card_name: str | None = None):
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec

    if controlled := _CONTROLLED_PERMANENT_RECEIVING.fullmatch(material_line):
        return FixedPublicEventBindingSpec(
            event=RECEIVED_DAMAGE_EVENT,
            variant=_CONTROLLED_PERMANENT_RECEIVING_VARIANT,
            body=controlled["body"],
            template_id=TARGETED_RECEIVING_DAMAGE_BINDING,
            mechanic=RECEIVED_DAMAGE_MECHANIC,
            condition=controlled_permanent_receiving_damage_condition(),
        )
    if life_prohibition := _targeted_receiving_life_prohibition_binding(material_line, card_name):
        return life_prohibition
    targeted = _targeted_receiving_binding(material_line, card_name) or _paired_receiving_binding(material_line, card_name)
    if targeted is not None:
        return targeted
    if linked := _LINKED_PLAYER_RECEIVING.fullmatch(material_line):
        return FixedPublicEventBindingSpec(
            event=RECEIVED_DAMAGE_EVENT, variant=_LINKED_PLAYER_RECEIVING_VARIANT, body=linked["body"],
            template_id=LINKED_PLAYER_RECEIVED_DAMAGE_TEMPLATE, mechanic=RECEIVED_DAMAGE_MECHANIC,
            condition=linked_player_received_damage_condition(),
        )
    pair = re.fullmatch(
        r'(?:When|Whenever) enchanted creature becomes tapped or is dealt damage, (?P<body>.+)',
        material_line, re.I,
    )
    if pair is not None:
        subscriptions = (
            FixedEventSubscription('permanent.tap', _creature_condition(attached=True, received=False)),
            FixedEventSubscription(RECEIVED_DAMAGE_EVENT, _creature_condition(attached=True)),
        )
        return FixedPublicEventBindingSpec(
            event='permanent.tap', variant=ATTACHED_TAP_RECEIVED_VARIANT, body=pair['body'],
            template_id='fixed-counter-public-multi-event-trigger-v1', mechanic=RECEIVED_DAMAGE_MECHANIC,
            condition=fixed_event_subscription_condition(subscriptions),
            capabilities=('trigger.event.normalized_public_action', 'trigger.subscription.fixed_multi_event',
                          'attachment.reference.current_or_lki'),
        )
    match = re.fullmatch(
        r'(?:When|Whenever) (?P<subject>a creature|enchanted creature|equipped creature) '
        r'is dealt (?P<combat>combat )?damage, (?P<body>.+)', material_line, re.I,
    )
    if match is None:
        return None
    subject = match['subject'].casefold()
    relation = 'any' if subject == 'a creature' else subject.split()[0]
    return FixedPublicEventBindingSpec(
        event=RECEIVED_COMBAT_DAMAGE_EVENT if match['combat'] else RECEIVED_DAMAGE_EVENT,
        variant='received_' + relation + '_creature', body=match['body'],
        template_id='fixed-counter-public-damage-trigger-v1', mechanic=RECEIVED_DAMAGE_MECHANIC,
        condition=_creature_condition(attached=relation != 'any'),
        capabilities=('attachment.reference.current_or_lki',) if relation != 'any' else (),
    )
