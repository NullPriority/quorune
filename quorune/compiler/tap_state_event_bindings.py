from __future__ import annotations

"""Closed subjects over canonical tap-state transitions, never targeting."""

from . import regex_working_set as re
from copy import deepcopy
from typing import Any, Mapping

from ..rules.source_references import SourceReferenceSpec
from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
from .qualified_zone_event_bindings import _subject_condition, _subject_query


TAP_STATE_EVENT_VARIANT = "public_tap_state_transition"
TAP_STATE_EVENT_PLAYER_MECHANIC = "tap-state-event-player-result"
EVENT_CONTROLLER_REFERENCE = "$context.controller"
TAP_STATE_EVENT_VARIANTS = frozenset({TAP_STATE_EVENT_VARIANT, "source_" + TAP_STATE_EVENT_VARIANT})
_SOURCE_ATTACK_NEXT_UNTAP = re.compile(
    r"It doesn['’]t untap during its controller['’]s next untap step\.", re.IGNORECASE,
)
_TRANSITION = re.compile(
    r"^(?:When|Whenever) (?P<subject>.+?) becomes (?P<state>tapped|untapped)"
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
    """Read current/LKI object-controller information without a target."""

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
    effect[field] = {'kind':'occurrence_controller_information','schema_version':1}
    return (
        "tap-state-object-controller-result-v2", (effect,), None,
        tuple(dict.fromkeys((
            *(mechanic for mechanic in mechanics if mechanic != "cr-115-targets"),
            TAP_STATE_EVENT_PLAYER_MECHANIC,
        ))),
    )


def monarch_became_binding_spec(material_line, *, card_name=None):
    from ..rules.monarch_transition import (
        MONARCH_BECAME_CAPABILITY, MONARCH_BECAME_EVENT, MONARCH_BECAME_TEMPLATE,
        MONARCH_BECAME_VARIANT, MONARCH_CONTROLLER_CONDITION,
    )
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec

    match = re.fullmatch(
        r"Whenever you become the monarch, (?P<body>target player sacrifices a creature(?: of their choice)?\.)",
        material_line, re.I,
    )
    if match is None:
        return None
    return FixedPublicEventBindingSpec(
        event=MONARCH_BECAME_EVENT, variant=MONARCH_BECAME_VARIANT, body=match["body"],
        template_id=MONARCH_BECAME_TEMPLATE, mechanic="trigger-event-normalized-public-action",
        condition=dict(MONARCH_CONTROLLER_CONDITION), capabilities=(MONARCH_BECAME_CAPABILITY,),
    )


def tapped_entry_untap_binding(material_line, *, card_name=None):
    from ..rules.entry_permanent_results import TAPPED_ENTRY_VARIANT, tapped_entry_untap_condition

    match = re.fullmatch(r"Whenever (?:a permanent you control enters tapped|"
                         r"a permanent enters tapped and under your control), (?P<body>untap it\.)",
                         material_line, re.I)
    if match is None:
        return None
    return FixedPublicEventBindingSpec(
        event="permanent.enter", variant=TAPPED_ENTRY_VARIANT, body=match["body"],
        template_id="fixed-tapped-entry-untap-v1", mechanic="trigger-event-normalized-zone-change",
        condition=tapped_entry_untap_condition(),
    )




def final_saga_chapter_binding_spec(material_line: str, *, card_name: str | None):
    from ..saga_lifecycle import (
        FINAL_SAGA_CHAPTER_CONTROLLED_CONDITION, FINAL_SAGA_CHAPTER_RESOLVED_CAPABILITY,
        FINAL_SAGA_CHAPTER_RESOLVED_EVENT,
    )

    chapter = re.fullmatch(
        r"Whenever the final chapter ability of a Saga you control resolves, (?P<body>.+)",
        material_line, re.IGNORECASE,
    )
    if chapter is None:
        return None
    return FixedPublicEventBindingSpec(
        event=FINAL_SAGA_CHAPTER_RESOLVED_EVENT, variant="controlled_final_saga_chapter",
        body=chapter["body"], template_id="fixed-controlled-final-saga-chapter-trigger-v1",
        mechanic="trigger-event-normalized-public-action", condition=FINAL_SAGA_CHAPTER_CONTROLLED_CONDITION,
        capabilities=(FINAL_SAGA_CHAPTER_RESOLVED_CAPABILITY,),
    )


def public_trigger_binding_spec(material_line: str, *, card_name: str | None):
    """Keep established public grammar precedence before the tap extension."""
    from .entrant_x_reflexive import fixed_public_binding_with_entrant_x as fixed_public_event_binding_spec
    from .qualified_zone_event_bindings import attached_player_creature_entry_binding_spec
    from .fixed_public_action_event_bindings import _point_combat_spec, fixed_public_action_event_binding_spec
    from .fixed_public_multi_event_bindings import (
        fixed_public_multi_event_binding_spec, opponent_attack_draw_cast_binding_spec,
    )
    from .room_bindings import eerie_binding_spec
    from .target_announcement_bindings import target_announcement_binding_spec
    from .phase_occurrence_templates import phase_occurrence_binding_spec
    from .event_card_return_templates import (
        self_death_return_binding, source_departure_hand_return_binding, source_departure_library_binding,
        source_death_land_exile_binding, entry_creature_pump_binding,
    )
    from .counter_placement_event_bindings import counter_placement_event_binding_spec
    from .scheduled_player_triggers import scheduled_player_binding_spec
    from .source_damage_death_bindings import source_damage_death_binding
    from .token_creation_event_bindings import token_creation_event_binding_spec
    from .crime_bindings import crime_event_binding_spec
    from .chosen_color_event_bindings import chosen_color_event_binding_spec
    from .chosen_type_event_bindings import chosen_type_event_binding_spec
    from .explore_event_bindings import explore_event_binding_spec
    from .mutate_event_bindings import mutate_event_binding_spec
    from .unblocked_attacker_bindings import unblocked_attacker_binding_spec
    from .attacking_token_results import attacking_entry_copy_binding

    if mixed := opponent_attack_draw_cast_binding_spec(material_line):
        return mixed
    if entry := attacking_entry_copy_binding(material_line, card_name=card_name):
        return entry
    if points := _point_combat_spec(material_line):
        return points
    if tapped_entry := tapped_entry_untap_binding(material_line, card_name=card_name):
        return tapped_entry
    for parser in (final_saga_chapter_binding_spec, monarch_became_binding_spec, source_death_land_exile_binding, source_departure_hand_return_binding, source_departure_library_binding, unblocked_attacker_binding_spec, attached_player_creature_entry_binding_spec,entry_creature_pump_binding,fixed_public_event_binding_spec, fixed_public_action_event_binding_spec,
                   tap_state_event_binding_spec, counter_placement_event_binding_spec, eerie_binding_spec, fixed_public_multi_event_binding_spec, phase_occurrence_binding_spec, target_announcement_binding_spec, self_death_return_binding, scheduled_player_binding_spec, source_damage_death_binding,token_creation_event_binding_spec,crime_event_binding_spec,chosen_color_event_binding_spec,chosen_type_event_binding_spec,explore_event_binding_spec,mutate_event_binding_spec):
        value = parser(material_line, card_name=card_name)
        if value is not None:
            return value
    return None


def tap_state_bound_result(binding, body: str, *, effect_template, card_name: str):
    from ..rules.tap_state_capability_shapes import SOURCE_ATTACK_CONDITION
    from .tap_state_templates import SourceTapStateEffectTemplate, TapStateAction

    if (
        (binding.event.value, binding.variant, binding.active_zone)
        == ("creature.attacks", "source_attacks", "battlefield")
        and binding.event_condition == SOURCE_ATTACK_CONDITION
        and body == binding.body
        and _SOURCE_ATTACK_NEXT_UNTAP.fullmatch(body)
    ):
        return SourceTapStateEffectTemplate(TapStateAction.PROHIBIT_NEXT_UNTAP, "creature").compiled(), False
    if binding.variant not in TAP_STATE_EVENT_VARIANTS:
        return None
    if binding.variant == "source_" + TAP_STATE_EVENT_VARIANT:
        from ..rules.tap_state_capability_shapes import is_source_untap_counter_sequence
        from .closed_effect_programs import closed_effect_program_template
        from .source_self_effect_templates import normalized_source_result_body, pin_source_result_incarnation

        def compile_source_clause(clause):
            source_clause = normalized_source_result_body(clause) or clause
            return pin_source_result_incarnation(effect_template(source_clause, card_name=card_name))

        sequence = closed_effect_program_template(body, compile_component=compile_source_clause)
        if sequence is not None and is_source_untap_counter_sequence(sequence.effects, sequence.target_schema):
            return sequence.compiled(), False
    from .per_player_query_life import source_tap_opponent_poison_threshold_life_result

    qualified = source_tap_opponent_poison_threshold_life_result(binding, body)
    if qualified is not None:
        return qualified, False
    value = tap_state_event_player_effect_template(
        body, compile_effect=effect_template, card_name=card_name,
    )
    return (value, True) if value is not None else None
