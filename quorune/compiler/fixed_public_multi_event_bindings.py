from __future__ import annotations

"""Closed same-zone multi-event grammar for typed triggered abilities."""

import re
from typing import Any, Mapping

from ..rules.event_subscriptions import (
    FixedEventSubscription,
    fixed_event_subscription_condition,
)
from ..rules.source_references import SourceReferenceSpec
from .fixed_public_event_trigger_bindings import (
    FixedPublicEventBindingSpec,
    _spec,
)


_THIS_SOURCE_MULTI_EVENT = re.compile(
    r"^(?:When|Whenever) this (?P<kind>creature|artifact|Vehicle|permanent) "
    r"(?P<pair>enters or attacks|enters or dies|attacks or blocks|blocks or becomes blocked|"
    r"enters or leaves the battlefield|"
    r"enters or is put into a graveyard from the battlefield|"
    r"enters or is turned face up|"
    r"enters or deals combat damage to a player|"
    r"enters or becomes monstrous), (?P<body>.+)$",
    re.IGNORECASE,
)
_NAMED_SOURCE_MULTI_EVENT = re.compile(
    r"^(?:When|Whenever) (?P<subject>.+?) "
    r"(?P<pair>enters or attacks|enters or dies|attacks or blocks|blocks or becomes blocked), "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_ARTIFACT_ENTRY_SACRIFICE = re.compile(
    r"^When this artifact enters and when you sacrifice it, (?P<body>.+)$",
    re.IGNORECASE,
)
_ENCHANTED_CREATURE_ATTACK_BLOCK = re.compile(
    r"^Whenever enchanted creature attacks or blocks, (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_ATTACK_BLOCK_DINOSAUR = re.compile(
    r"^Whenever this creature attacks or blocks while you control a Dinosaur, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)


def _source_condition(*conditions: Mapping[str, Any]) -> Mapping[str, Any]:
    values = [
        {"field": "card", "op": "eq", "value": "$source.ref"},
        *[dict(condition) for condition in conditions],
    ]
    return values[0] if len(values) == 1 else {"all": values}


def _event_pair(
    pair: str,
) -> tuple[tuple[FixedEventSubscription, ...], str, tuple[str, ...]]:
    source = _source_condition()
    values: dict[
        str,
        tuple[tuple[FixedEventSubscription, ...], str, tuple[str, ...]],
    ] = {
        "enters or attacks": (
            (
                FixedEventSubscription("permanent.enter", source),
                FixedEventSubscription("creature.attacks", source),
            ),
            "trigger-event-normalized-zone-change",
            ("trigger.event.normalized_public_action",),
        ),
        "enters or dies": (
            (
                FixedEventSubscription("permanent.enter", source),
                FixedEventSubscription("creature.dies", source),
            ),
            "trigger-event-normalized-zone-change",
            (),
        ),
        "attacks or blocks": (
            (
                FixedEventSubscription("creature.attacks", source),
                FixedEventSubscription("creature.blocks", source),
            ),
            "trigger-event-normalized-public-action",
            (),
        ),
        "blocks or becomes blocked": (
            (
                FixedEventSubscription("creature.blocks", source),
                FixedEventSubscription("creature.becomes_blocked", source),
            ),
            "trigger-event-normalized-public-action",
            (),
        ),
        "enters or leaves the battlefield": (
            (
                FixedEventSubscription("permanent.enter", source),
                FixedEventSubscription("permanent.leave", source),
            ),
            "trigger-event-normalized-zone-change",
            (),
        ),
        "enters or is put into a graveyard from the battlefield": (
            (
                FixedEventSubscription("permanent.enter", source),
                FixedEventSubscription("permanent.graveyard", source),
            ),
            "trigger-event-normalized-zone-change",
            (),
        ),
        "enters or is turned face up": (
            (
                FixedEventSubscription("permanent.enter", source),
                FixedEventSubscription("permanent.turned_face_up", source),
            ),
            "trigger-event-normalized-zone-change",
            ("trigger.event.normalized_public_action",),
        ),
        "enters or deals combat damage to a player": (
            (
                FixedEventSubscription("permanent.enter", source),
                FixedEventSubscription(
                    "damage.dealt",
                    _source_condition(
                        {
                            "field": "target_kind",
                            "op": "eq",
                            "value": "player",
                        },
                        {
                            "field": "combat",
                            "op": "truthy",
                            "value": True,
                        },
                    ),
                ),
            ),
            "trigger-event-normalized-zone-change",
            ("trigger.event.normalized_damage",),
        ),
        "enters or becomes monstrous": (
            (
                FixedEventSubscription("permanent.enter", source),
                FixedEventSubscription("permanent.designated_monstrous", source),
            ),
            "trigger-event-normalized-zone-change",
            ("permanent.designation.monstrous", "trigger.event.normalized_permanent_designation"),
        ),
    }
    try:
        subscriptions, mechanic, capabilities = values[pair]
    except KeyError as exc:
        raise ValueError(f"Unsupported fixed event pair {pair!r}") from exc
    return (
        subscriptions,
        mechanic,
        (
            "trigger.subscription.fixed_multi_event",
            *capabilities,
        ),
    )


def _source_pair_spec(
    *,
    pair: str,
    body: str,
) -> FixedPublicEventBindingSpec:
    subscriptions, mechanic, capabilities = _event_pair(pair)
    variant = "source_" + pair.replace(" ", "_")
    return _spec(
        subscriptions[0].event,
        variant,
        body,
        "fixed-counter-public-multi-event-trigger-v1",
        mechanic,
        condition=fixed_event_subscription_condition(subscriptions),
        capabilities=capabilities,
    )


def _scheduled_pair_spec(material_line, *, card_name):
    """Compose a step and a closed entry/cast head without sharing predicates."""
    match = re.fullmatch(
        r'(?P<first>(?:When|Whenever|At the beginning) .+?) and '
        r'(?P<second>(?:when|whenever|at the beginning) .+?), (?P<body>.+)',
        material_line, re.I,
    )
    if match is None or not any(match[key].casefold().startswith('at the beginning')
            for key in ('first', 'second')):
        return None
    heads = (match['first'], match['second'])
    if any(re.search(r'\band (?:when|whenever|at the beginning)\b', head, re.I)
            for head in heads):
        return None
    # These bodies require their own event-result composer. Never infer one
    # branch's object/player from the other branch's primary event.
    if re.search(r'\b(?:it|its|they|them|their|that player|that creature|that card|'
            r'that spell|this way|that much)\b', match['body'], re.I):
        return None
    from .fixed_counter_trigger_nodes import (
        fixed_counter_trigger_binding, _public_state_source_self_trigger_binding,
    )
    children = tuple(fixed_counter_trigger_binding(head + ', ' + match['body'], card_name=card_name)
        or _public_state_source_self_trigger_binding(head + ', ' + match['body'], card_name=card_name)
        for head in heads)
    if any(child is None or child.active_zone != 'battlefield'
            or child.public_state_condition is not None
            or child.event_condition is not None and 'event_subscriptions' in child.event_condition
            for child in children):
        return None
    events = tuple(child.event.value for child in children)
    if events.count('step.begin') != 1 or any(event not in {
            'step.begin', 'spell.cast', 'permanent.enter.self',
        } for event in events):
        return None
    subscriptions = tuple(FixedEventSubscription(
        'permanent.enter' if event == 'permanent.enter.self' else event,
        _source_condition(child.event_condition) if event == 'permanent.enter.self'
        and child.event_condition is not None else _source_condition()
        if event == 'permanent.enter.self' else child.event_condition,
    ) for child, event in zip(children, events))
    from ..rules.capabilities import MECHANIC_CAPABILITY_DEPENDENCIES
    dependencies = {'trigger.subscription.fixed_multi_event',
        *(cap for child in children for cap in child.explicit_capabilities),
        *(cap for child in children for mechanic in child.event_mechanics
            for cap in MECHANIC_CAPABILITY_DEPENDENCIES.get(mechanic, ())),
    }
    return _spec(subscriptions[0].event, 'independent_dual_event', match['body'],
        'fixed-independent-dual-event-trigger-v1', 'cr-603-handling-triggered-abilities',
        condition=fixed_event_subscription_condition(subscriptions),
        capabilities=tuple(sorted(dependencies)))


def fixed_public_multi_event_binding_spec(
    material_line: str,
    *,
    card_name: str | None = None,
) -> FixedPublicEventBindingSpec | None:
    """Parse one exact two-event source subscription with one active zone."""

    scheduled = _scheduled_pair_spec(material_line, card_name=card_name)
    if scheduled is not None:
        return scheduled
    qualified = re.fullmatch(r'(?:When|Whenever) (?P<subject>(?:a|an|another) .+?) (?P<pair>enters or dies|enters or leaves the battlefield), (?P<body>.+)', material_line, re.I)
    if qualified is not None:
        from .qualified_zone_event_bindings import qualified_public_zone_event_binding_spec
        verbs = ('enters', 'dies' if qualified['pair'].casefold().endswith('dies') else 'leaves the battlefield')
        children = tuple(qualified_public_zone_event_binding_spec(
            f"Whenever {qualified['subject']} {verb}, {qualified['body']}", card_name=card_name)
            for verb in verbs)
        if any(child is None for child in children):
            return None
        subscriptions = tuple(FixedEventSubscription(child.event, child.condition) for child in children)
        return _spec(children[0].event, 'qualified_zone_event_pair', qualified['body'],
            'fixed-qualified-zone-event-pair-v1', 'trigger-event-normalized-zone-change',
            condition=fixed_event_subscription_condition(subscriptions),
            capabilities=tuple(sorted({'trigger.subscription.fixed_multi_event',
                *(cap for child in children for cap in child.capabilities)})))

    independent = re.fullmatch(r'(?P<first>(?:When|Whenever) .+?) and (?P<second>(?:when|whenever) .+?), (?P<body>.+)', material_line, re.I)
    if independent is not None and _SOURCE_ARTIFACT_ENTRY_SACRIFICE.fullmatch(material_line) is None:
        if re.search(r'\band (?:when|whenever)\b', independent['first'] + ' ' + independent['second'], re.I):
            return None
        source_entry = re.fullmatch(r'(?:When|Whenever) (?P<source>.+?) enters', independent['first'], re.I)
        if source_entry is None or not (
                source_entry['source'].casefold() in {'this artifact', 'this creature', 'this enchantment', 'this permanent', 'this land'}
                or card_name and SourceReferenceSpec(card_name).matches(source_entry['source'])):
            return None
        # Each complete prefix keeps its own typed predicate and controller.
        # Calling the ordinary parser is bounded: child lines lack this separator.
        from .fixed_counter_trigger_nodes import fixed_counter_trigger_binding, _public_state_source_self_trigger_binding
        children = tuple(fixed_counter_trigger_binding(
            independent[name] + ', ' + independent['body'], card_name=card_name)
            or _public_state_source_self_trigger_binding(
                independent[name] + ', ' + independent['body'], card_name=card_name)
            for name in ('first', 'second'))
        if any(child is None or child.public_state_condition is not None
                or child.event_condition is not None and 'event_subscriptions' in child.event_condition
                for child in children):
            return None
        first, second = children
        event_aliases = {'creature.enter': 'permanent.enter', 'land.enter': 'permanent.enter'}
        events = tuple(event_aliases.get(child.event.value.removesuffix('.self'), child.event.value.removesuffix('.self')) for child in children)
        if first.active_zone != second.active_zone or len(set(events)) != 2:
            return None
        # The primary subscription uses the established source-entry predicate.
        # Other children retain source identity in their typed event suffix;
        # removing it would widen a source damage/departure clause to all objects.
        subscriptions = tuple(FixedEventSubscription(
            event + ('.self' if index != 0 and child.event.value.endswith('.self') else ''),
            (_source_condition(child.event_condition) if child.event_condition is not None else _source_condition())
            if index == 0 else child.event_condition)
            for index, (child, event) in enumerate(zip(children, events)))
        from ..rules.capabilities import MECHANIC_CAPABILITY_DEPENDENCIES
        event_capabilities = {cap for child in children for mechanic in child.event_mechanics
            for cap in MECHANIC_CAPABILITY_DEPENDENCIES.get(mechanic, ())}
        return _spec(first.event.value.removesuffix('.self'), 'independent_dual_event', independent['body'],
            'fixed-independent-dual-event-trigger-v1', 'trigger-event-normalized-zone-change',
            condition=fixed_event_subscription_condition(subscriptions),
            capabilities=tuple(sorted({'trigger.subscription.fixed_multi_event',
                *(cap for child in children for cap in child.explicit_capabilities),
                *event_capabilities,
                *(('trigger.event.normalized_spell_cast',) if any(child.event.value == 'spell.cast' for child in children) else ())})),
            active_zone=first.active_zone)

    source = _THIS_SOURCE_MULTI_EVENT.fullmatch(material_line)
    if source is not None:
        return _source_pair_spec(
            pair=source.group("pair").casefold(),
            body=source.group("body"),
        )
    named = _NAMED_SOURCE_MULTI_EVENT.fullmatch(material_line)
    if (
        named is not None
        and card_name
        and SourceReferenceSpec(card_name).matches(named.group("subject"))
    ):
        return _source_pair_spec(
            pair=named.group("pair").casefold(),
            body=named.group("body"),
        )
    sacrifice = _SOURCE_ARTIFACT_ENTRY_SACRIFICE.fullmatch(material_line)
    if sacrifice is not None:
        source_condition = _source_condition()
        subscriptions = (
            FixedEventSubscription("permanent.enter", source_condition),
            FixedEventSubscription("permanent.sacrificed", source_condition),
        )
        return _spec(
            subscriptions[0].event,
            "source_artifact_enters_and_is_sacrificed",
            sacrifice.group("body"),
            "fixed-counter-public-multi-event-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=fixed_event_subscription_condition(subscriptions),
            capabilities=("trigger.subscription.fixed_multi_event",),
        )
    enchanted = _ENCHANTED_CREATURE_ATTACK_BLOCK.fullmatch(material_line)
    if enchanted is not None:
        attached_condition = {
            "field": "source_attachment_target_ref",
            "op": "eq",
            "value": "$context.card",
        }
        subscriptions = (
            FixedEventSubscription("creature.attacks", attached_condition),
            FixedEventSubscription("creature.blocks", attached_condition),
        )
        return _spec(
            subscriptions[0].event,
            "enchanted_creature_attacks_or_blocks",
            enchanted.group("body"),
            "fixed-counter-public-multi-event-trigger-v1",
            "trigger-event-normalized-public-action",
            condition=fixed_event_subscription_condition(subscriptions),
            capabilities=(
                "trigger.subscription.fixed_multi_event",
                "attachment.reference.current_or_lki",
            ),
        )
    dinosaur = _SOURCE_ATTACK_BLOCK_DINOSAUR.fullmatch(material_line)
    if dinosaur is None:
        return None
    condition = _source_condition(
        {
            "field": "source_controller_subtype_count",
            "op": "gte",
            "value": 1,
            "subtype": "dinosaur",
        }
    )
    subscriptions = (
        FixedEventSubscription("creature.attacks", condition),
        FixedEventSubscription("creature.blocks", condition),
    )
    return _spec(
        subscriptions[0].event,
        "source_attacks_or_blocks_with_controlled_dinosaur",
        dinosaur.group("body"),
        "fixed-counter-public-multi-event-trigger-v1",
        "trigger-event-normalized-public-action",
        condition=fixed_event_subscription_condition(subscriptions),
        capabilities=("trigger.subscription.fixed_multi_event",),
    )


__all__ = ["fixed_public_multi_event_binding_spec"]
