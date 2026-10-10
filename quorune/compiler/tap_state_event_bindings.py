from __future__ import annotations

"""Closed subjects over canonical tap-state transitions, never targeting."""

import re
from copy import deepcopy
from typing import Any, Mapping

from ..rules.source_references import SourceReferenceSpec
from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
from .qualified_zone_event_bindings import _subject_condition, _subject_query


TAP_STATE_EVENT_VARIANT = "public_tap_state_transition"
TAP_STATE_EVENT_PLAYER_MECHANIC = "tap-state-event-player-result"
EVENT_CONTROLLER_REFERENCE = "$context.controller"
TAP_STATE_EVENT_VARIANTS = frozenset({TAP_STATE_EVENT_VARIANT, "source_" + TAP_STATE_EVENT_VARIANT})
_TRANSITION = re.compile(
    r"^Whenever (?P<subject>.+?) becomes (?P<state>tapped|untapped)"
    r"(?P<turn> during your turn)?, (?P<body>.+)$",
    re.IGNORECASE,
)


def _field(field: str, value: Any) -> Mapping[str, Any]:
    return {"field": field, "op": "eq", "value": value}


def tap_state_event_binding_spec(
    material_line: str, *, card_name: str | None = None,
) -> FixedPublicEventBindingSpec | None:
    match = _TRANSITION.fullmatch(material_line)
    if match is None:
        return None
    subject = match.group("subject")
    conditions: list[Mapping[str, Any]] = []
    source = bool(re.fullmatch(
        r"this (?:creature|land|artifact|enchantment|permanent)",
        subject, re.IGNORECASE,
    ) or (card_name is not None and SourceReferenceSpec(card_name).matches(subject)))
    if source:
        conditions.append(_field("card", "$source.ref"))
    elif re.fullmatch(
        r"(?:enchanted|equipped|fortified) (?:creature|land|artifact|permanent)",
        subject, re.IGNORECASE,
    ):
        conditions.append(_field("source_attachment_target_ref", "$context.card"))
    else:
        article = re.fullmatch(r"(?P<article>a|an|another) (?P<query>.+)", subject, re.IGNORECASE)
        if article is None:
            return None
        parsed = _subject_query(
            ("another target " if article.group("article").casefold() == "another" else "target ")
            + article.group("query")
        )
        if parsed is None:
            return None
        query, token = parsed
        if any((query.state_predicate, query.combat_state, query.damage_history,
                query.commander, query.numeric_characteristic, query.color_count_min,
                query.color_count_equal)):
            return None
        condition = _subject_condition(query)
        if condition is not None:
            conditions.append(condition)
        if token is not None:
            conditions.append(_field("token", token))
    if match.group("turn"):
        conditions.append(_field("active_player", "$source.controller"))
    body = match.group("body")
    if body.startswith("if it isn't being declared as an attacker, "):
        conditions.append(_field("declared_attacker", False))
        body = body[len("if it isn't being declared as an attacker, "):]
    return FixedPublicEventBindingSpec(
        event="permanent.tap" if match.group("state").casefold() == "tapped" else "permanent.untap",
        variant=("source_" if source else "") + TAP_STATE_EVENT_VARIANT,
        body=body, template_id="fixed-counter-tap-state-trigger-v1",
        mechanic="trigger-event-normalized-public-action",
        condition=conditions[0] if len(conditions) == 1 else {"all": conditions},
    )


def tap_state_event_player_effect_template(
    body: str, *, compile_effect, card_name: str,
):
    """Bind an explicit occurrence controller without inventing a target."""

    reference = r"(?:that (?:permanent|creature|land|artifact)['’]s controller|its controller)"
    if not re.search(reference, body, re.IGNORECASE):
        return None
    fixed = re.sub(reference, "target player", body, flags=re.IGNORECASE)
    template, effects, schema, mechanics = compile_effect(fixed, card_name=card_name)
    if (
        template is None or len(effects) != 1 or schema is None
        or schema.get("zones") != ["player"] or schema.get("count") != 1
    ):
        return None
    effect = deepcopy(dict(effects[0]))
    field = "target" if effect.get("op") == "damage" else "player"
    if (
        effect.get("op") not in {"life", "lose_life", "mill", "damage"}
        or effect.get(field) != "$target.0"
    ):
        return None
    effect[field] = EVENT_CONTROLLER_REFERENCE
    return (
        "tap-state-event-player-result-v1", (effect,), None,
        tuple(dict.fromkeys((
            *(mechanic for mechanic in mechanics if mechanic != "cr-115-targets"),
            TAP_STATE_EVENT_PLAYER_MECHANIC,
        ))),
    )


def public_trigger_binding_spec(material_line: str, *, card_name: str | None):
    """Keep established public grammar precedence before the tap extension."""
    from .fixed_public_event_trigger_bindings import fixed_public_event_binding_spec
    from .fixed_public_action_event_bindings import fixed_public_action_event_binding_spec
    from .fixed_public_multi_event_bindings import fixed_public_multi_event_binding_spec
    from .target_announcement_bindings import target_announcement_binding_spec
    from .event_card_return_templates import self_death_return_binding
    from .counter_placement_event_bindings import counter_placement_event_binding_spec
    for parser in (fixed_public_event_binding_spec, fixed_public_action_event_binding_spec,
                   tap_state_event_binding_spec, counter_placement_event_binding_spec, fixed_public_multi_event_binding_spec, target_announcement_binding_spec, self_death_return_binding):
        value = parser(material_line, card_name=card_name)
        if value is not None:
            return value
    return None


def tap_state_bound_result(binding, body: str, *, effect_template, card_name: str):
    if binding.variant not in TAP_STATE_EVENT_VARIANTS:
        return None
    value = tap_state_event_player_effect_template(
        body, compile_effect=effect_template, card_name=card_name,
    )
    return (value, True) if value is not None else None
