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
    r"(?P<pair>enters or attacks|enters or dies|attacks or blocks|"
    r"enters or leaves the battlefield|"
    r"enters or is put into a graveyard from the battlefield|"
    r"enters or is turned face up|"
    r"enters or deals combat damage to a player|"
    r"enters or becomes monstrous), (?P<body>.+)$",
    re.IGNORECASE,
)
_NAMED_SOURCE_MULTI_EVENT = re.compile(
    r"^(?:When|Whenever) (?P<subject>.+?) "
    r"(?P<pair>enters or attacks|enters or dies|attacks or blocks), "
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
                FixedEventSubscription("permanent.becomes_monstrous", source),
            ),
            "trigger-event-normalized-zone-change",
            ("permanent.designation.monstrous",),
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


def fixed_public_multi_event_binding_spec(
    material_line: str,
    *,
    card_name: str | None = None,
) -> FixedPublicEventBindingSpec | None:
    """Parse one exact two-event source subscription with one active zone."""

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
