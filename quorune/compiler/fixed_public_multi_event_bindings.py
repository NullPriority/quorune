from __future__ import annotations

"""Closed same-zone multi-event grammar for typed triggered abilities."""

from . import regex_working_set as re
from typing import Any, Mapping

from ..rules.event_subscriptions import (
    DRAW_EXCEPT_FIRST_OWN_STEP_EVENT, FixedEventSubscription,
    COMBAT_PARTICIPANT_DAMAGE_EVENT_CAPABILITIES, COMBAT_PARTICIPANT_DAMAGE_TRIGGER_TEMPLATE,
    COMBAT_PARTICIPANT_DAMAGE_VARIANT, combat_participant_damage_condition,
    GRAVEYARD_CREATURE_TRANSITIONS_TEMPLATE, GRAVEYARD_CREATURE_TRANSITIONS_VARIANT,
    GRAVEYARD_DEPARTURE_LOOKBACK_EVENT, fixed_event_subscription_condition,
    graveyard_creature_transition_condition, graveyard_creature_transition_damage_effect,
)
from ..rules.source_references import SourceReferenceSpec
from .fixed_public_event_trigger_bindings import (
    FixedPublicEventBindingSpec,
    _face_transition_spec,
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
    r"^Whenever enchanted creature (?P<pair>attacks or blocks|blocks or becomes blocked), (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_ATTACK_BLOCK_DINOSAUR = re.compile(
    r"^Whenever this creature attacks or blocks while you control a Dinosaur, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)


_ENTRY_OR_CAST_SHARED_ORIGIN = re.compile(
    r"Whenever (?P<first>you cast a spell|a land you control enters) from "
    r"(?P<origin>exile|anywhere other than your hand) or "
    r"(?P<second>you cast a spell|a land you control enters) from (?P=origin), (?P<body>.+)",
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
    combat_to_player = FixedEventSubscription(
        "damage.dealt",
        _source_condition(
            {"field": "target_kind", "op": "eq", "value": "player"},
            {"field": "combat", "op": "truthy", "value": True},
        ),
    )
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
            (FixedEventSubscription("permanent.enter", source), combat_to_player),
            "trigger-event-normalized-zone-change",
            ("trigger.event.normalized_damage",),
        ),
        "deals combat damage to a player or dies": (
            (combat_to_player, FixedEventSubscription("creature.dies", source)),
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


def _scheduled_pair_has_local_linked_target(body, card_name):
    """An exiled-card reference can be bound inside the shared result."""
    if not card_name:
        return False
    from .linked_exile_return_templates import linked_exile_return_effect_template
    from .reflexive_payment_templates import reflexive_payment_template

    def compile_targeted_link(text):
        compiled = linked_exile_return_effect_template(text, card_name=card_name, source_is_permanent=True)
        return compiled if compiled is not None and compiled[2] is not None else (None, (), None, ())

    return (compile_targeted_link(body)[0] is not None or
            reflexive_payment_template(body, compile_effect=compile_targeted_link, bound_trigger=True) is not None)


def _scheduled_top_card_result(heads, body):
    if tuple(head.casefold() for head in heads) != (
        "when this artifact enters", "at the beginning of your upkeep",
    ):
        return False
    from .fixed_library_selection_templates import ChosenTypeTopSelectionEffectTemplate, fixed_library_selection_effect_template

    template = fixed_library_selection_effect_template(body)
    return isinstance(template, ChosenTypeTopSelectionEffectTemplate) and template.may_put_in_graveyard


def _scheduled_attached_control_result(heads, body):
    from .fixed_control_templates import is_attached_temporary_control_body

    return (tuple(head.casefold() for head in heads)
            == ("when this aura enters", "at the beginning of your upkeep")
            and is_attached_temporary_control_body(body))


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
    if (re.search(r'\b(?:it|its|they|them|their|that player|that creature|that card|'
            r'that spell|this way|that much)\b', match['body'], re.I)
            and not _scheduled_pair_has_local_linked_target(match['body'], card_name)
            and not _scheduled_top_card_result(heads, match['body'])
            and not _scheduled_attached_control_result(heads, match['body'])):
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


def _source_block_pair_spec(material_line):
    paired = re.fullmatch(r'(?:When|Whenever) this creature blocks or becomes blocked by a creature, (?P<body>.+)', material_line, re.I)
    if paired is not None:
        subscriptions = (
            FixedEventSubscription('creature.blocks', _source_condition(
                {'field': 'blocked_attacker', 'op': 'truthy', 'value': True})),
            FixedEventSubscription('creature.becomes_blocked_by', _source_condition()),
        )
        return _spec('creature.blocks', 'source_block_pair_counterpart', paired['body'],
            'fixed-source-block-pair-trigger-v1', 'trigger-event-normalized-public-action',
            condition=fixed_event_subscription_condition(subscriptions),
            capabilities=('trigger.subscription.fixed_multi_event', 'trigger.event.block_pair_counterpart'))

    from .qualified_block_pairs import qualified_block_pair_spec
    return qualified_block_pair_spec(material_line)


def _opponent_cast_or_source_death_spec(
    material_line: str, *, card_name: str | None,
) -> FixedPublicEventBindingSpec | None:
    match = re.fullmatch(
        r"Whenever (?P<cast>an opponent casts a spell during your turn) "
        r"(?:and|or) when (?P<source>this creature|[^,]+?) dies, (?P<body>.+)",
        material_line, re.IGNORECASE,
    )
    if match is None:
        return None
    source = match["source"]
    if source.casefold() != "this creature" and not (
        card_name and SourceReferenceSpec(card_name).matches(source)
    ):
        return None
    body = match["body"]
    if re.search(r"\b(?:it|its|they|them|their|that player|that creature|that card|"
                 r"that spell|this way|that much)\b", body, re.IGNORECASE):
        return None
    from .spell_cast_predicates import fixed_spell_cast_binding_spec

    cast = fixed_spell_cast_binding_spec(f"Whenever {match['cast']}, {body}")
    if cast is None:
        return None
    subscriptions = (
        FixedEventSubscription("spell.cast", cast.subject.event_condition),
        FixedEventSubscription("creature.dies", _source_condition()),
    )
    return _spec(
        "spell.cast", "opponent_cast_or_source_death", body,
        "fixed-cast-or-source-death-trigger-v1", "trigger-event-normalized-zone-change",
        condition=fixed_event_subscription_condition(subscriptions),
        capabilities=("trigger.subscription.fixed_multi_event", "trigger.event.normalized_spell_cast"),
    )


def _color_cast_or_land_entry_spec(material_line):
    match = re.fullmatch(
        r"Whenever you cast a (?P<color>white|blue|black|red|green) spell or "
        r"(?:a|an) (?P<subtype>Plains|Island|Swamp|Mountain|Forest) you control enters, "
        r"(?P<body>you gain 1 life\.?)", material_line, re.IGNORECASE,
    )
    if match is None:
        return None
    from .spell_cast_predicates import fixed_spell_cast_binding_spec

    body = match["body"]
    cast = fixed_spell_cast_binding_spec(f"Whenever you cast a {match['color']} spell, {body}")
    if cast is None:
        return None
    entry = {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "subtypes", "op": "contains_any", "value": [match["subtype"].casefold()]},
    ]}
    subscriptions = (
        FixedEventSubscription("spell.cast", cast.subject.event_condition),
        FixedEventSubscription("land.enter", entry),
    )
    return _spec(
        "spell.cast", "independent_dual_event", body,
        "fixed-color-cast-or-land-entry-trigger-v1", "cr-603-handling-triggered-abilities",
        condition=fixed_event_subscription_condition(subscriptions),
        capabilities=("trigger.subscription.fixed_multi_event", "trigger.event.normalized_spell_cast",
                      "trigger.event.normalized_zone_change"),
    )


def _entry_or_cast_origin_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    match = _ENTRY_OR_CAST_SHARED_ORIGIN.fullmatch(material_line)
    if match is None or match["first"].casefold() == match["second"].casefold():
        return None

    from .token_templates import fixed_token_creation_effect_template

    body = match["body"]
    token = fixed_token_creation_effect_template(body)
    if token is None or token.template_id != "create-fixed-creature-token-v2":
        return None
    characteristics = token.effect["characteristics"]
    if (token.effect["quantity"] != 1 or token.effect.get("tapped")
            or token.mechanics != ("cr-111-tokens",)
            or not characteristics["type_line"].startswith("Token Creature — ")):
        return None

    controller = {"field": "controller", "op": "eq", "value": "$source.controller"}
    origin: Mapping[str, Any] = {"field": "from", "op": "eq", "value": "exile"}
    if match["origin"].casefold() != "exile":
        # CR 400.3 identifies the hand by the object's sealed owner.
        origin = {"all": [
            {"field": "from", "op": "truthy", "value": True},
            {"any": [
                {"field": "from", "op": "ne", "value": "hand"},
                {"all": [
                    {"field": "owner", "op": "truthy", "value": True},
                    {"field": "owner", "op": "ne", "value": "$source.controller"},
                ]},
            ]},
        ]}
    condition = {"all": [controller, origin]}
    return _spec(
        "spell.cast", "independent_dual_event", body,
        "fixed-origin-cast-or-land-entry-trigger-v1", "trigger-event-normalized-zone-change",
        condition=fixed_event_subscription_condition((
            FixedEventSubscription("spell.cast", condition),
            FixedEventSubscription("land.enter", condition),
        )),
        capabilities=("trigger.subscription.fixed_multi_event", "trigger.event.normalized_spell_cast",
                      "trigger.event.normalized_zone_change"),
    )


def _face_transition_pair_spec(first, second, body):
    children = tuple(_face_transition_spec(head + ", " + body) for head in (first, second))
    if any(child is None for child in children) or tuple(child.variant for child in children) != (
        "controlled_face_down_permanent_enters", "controlled_permanent_turned_face_up",
    ):
        return None
    if re.search(r"\b(?:it|its|they|them|their|that (?:permanent|creature|player|card)|this way)\b", body, re.I):
        return None
    subscriptions = tuple(FixedEventSubscription(child.event, child.condition) for child in children)
    return _spec(
        subscriptions[0].event, "independent_dual_event", body,
        "fixed-independent-face-transition-pair-v1", "trigger-event-normalized-zone-change",
        condition=fixed_event_subscription_condition(subscriptions),
        capabilities=("trigger.subscription.fixed_multi_event", "trigger.event.normalized_public_action",
                      *children[0].capabilities, *children[1].capabilities),
    )


def _independent_pair_spec(independent, card_name):
    if re.search(r'\band (?:when|whenever)\b', independent['first'] + ' ' + independent['second'], re.I):
        return None
    face_transition_pair = _face_transition_pair_spec(
        independent["first"], independent["second"], independent["body"],
    )
    if face_transition_pair is not None:
        return face_transition_pair
    source_entry = re.fullmatch(r'(?:When|Whenever) (?P<source>.+?) enters', independent['first'], re.I)
    if source_entry is None or not (
            source_entry['source'].casefold() in {'this artifact', 'this creature', 'this enchantment', 'this permanent', 'this land'}
            or card_name and SourceReferenceSpec(card_name).matches(source_entry['source'])):
        return None
    opponent_draw = re.fullmatch(
        r"whenever an opponent draws a card except the first one they draw in each of their draw steps",
        independent["second"], re.I,
    )
    if opponent_draw is not None:
        return _spec(
            "permanent.enter", "independent_dual_event", independent["body"],
            "fixed-independent-dual-event-trigger-v1", "trigger-event-normalized-zone-change",
            condition=fixed_event_subscription_condition((
                FixedEventSubscription("permanent.enter", _source_condition()),
                FixedEventSubscription(DRAW_EXCEPT_FIRST_OWN_STEP_EVENT, {
                    "field": "player", "op": "ne", "value": "$source.controller",
                }),
            )),
            capabilities=("trigger.subscription.fixed_multi_event", "trigger.event.normalized_card_draw"),
        )
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


_GRAVEYARD_CREATURE_TRANSITIONS = re.compile(
    r"Whenever another creature dies, or a creature card is put into a graveyard from anywhere other than the battlefield, "
    r"or a creature card leaves your graveyard, (?P<body>(?P<source>.+?) deals 1 damage to each opponent\.)",
    re.I,
)


def graveyard_creature_transition_binding(text, *, card_name):
    from .fixed_public_event_trigger_bindings import _source_subject

    match = _GRAVEYARD_CREATURE_TRANSITIONS.fullmatch(text)
    if match is None or not _source_subject(match["source"], card_name=card_name):
        return None
    return _spec(
        GRAVEYARD_DEPARTURE_LOOKBACK_EVENT, GRAVEYARD_CREATURE_TRANSITIONS_VARIANT, match["body"],
        GRAVEYARD_CREATURE_TRANSITIONS_TEMPLATE, "trigger-event-normalized-zone-change",
        condition=graveyard_creature_transition_condition(),
        capabilities=("trigger.event.normalized_zone_change", "trigger.subscription.fixed_multi_event"),
    )


def graveyard_creature_transition_result(binding, body):
    if binding.variant != GRAVEYARD_CREATURE_TRANSITIONS_VARIANT or body != binding.body:
        return None
    return (
        (GRAVEYARD_CREATURE_TRANSITIONS_TEMPLATE, (graveyard_creature_transition_damage_effect(),), None,
         ("cr-120-damage",)), True,
    )


def _combat_participant_damage_spec(material_line: str):
    match = re.fullmatch(r"Whenever a creature attacks or blocks, (?P<body>.+)", material_line, re.I)
    if match is None:
        return None
    return _spec(
        "creature.attacks", COMBAT_PARTICIPANT_DAMAGE_VARIANT, match["body"],
        COMBAT_PARTICIPANT_DAMAGE_TRIGGER_TEMPLATE, "trigger-event-normalized-public-action",
        condition=combat_participant_damage_condition(),
        capabilities=COMBAT_PARTICIPANT_DAMAGE_EVENT_CAPABILITIES,
    )


def fixed_public_multi_event_binding_spec(
    material_line: str,
    *,
    card_name: str | None = None,
) -> FixedPublicEventBindingSpec | None:
    """Parse one exact source subscription set with one active zone."""

    if transitions := graveyard_creature_transition_binding(material_line, card_name=card_name):
        return transitions
    paired = (_source_block_pair_spec(material_line)
              or _color_cast_or_land_entry_spec(material_line)
              or _entry_or_cast_origin_spec(material_line)
              or _combat_participant_damage_spec(material_line))
    if paired is not None:return paired

    cast_or_death = _opponent_cast_or_source_death_spec(material_line, card_name=card_name)
    if cast_or_death is not None:
        return cast_or_death

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
        return _independent_pair_spec(independent, card_name)

    if re.fullmatch(r"Whenever this creature deals combat damage to a player or dies, proliferate\.", material_line, re.I):
        return _source_pair_spec(pair="deals combat damage to a player or dies", body="proliferate.")
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
        if enchanted["pair"].casefold() == "blocks or becomes blocked":
            from ..rules.attack_event_results import ATTACHED_BLOCK_GROWTH_VARIANT, attached_block_growth_condition

            return _spec("creature.blocks", ATTACHED_BLOCK_GROWTH_VARIANT, enchanted["body"],
                "fixed-counter-public-multi-event-trigger-v1", "trigger-event-normalized-public-action",
                condition=attached_block_growth_condition(),
                capabilities=("trigger.subscription.fixed_multi_event", "attachment.reference.current_or_lki"))
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
