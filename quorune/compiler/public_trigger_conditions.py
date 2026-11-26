from __future__ import annotations

"""Typed intervening conditions over an independently compiled occurrence."""

from dataclasses import replace
from . import regex_working_set as re

from ..continuous_conditions import (
    GREATEST_CREATURE_POWER_FACTS, FixedPublicStateConditionKind, FixedPublicStateConditionSpec,
    FixedPublicStateFact, greatest_creature_power_condition_dependencies,
)
from ..characteristic_fragments import (
    CharacteristicQuantityScope, CharacteristicQuantitySpec, nonowner_control_quantity_capabilities,
)
from ..controller_creature_keyword_absence import (
    ControllerCreatureKeywordAbsence, is_controller_end_step_schedule,
)
from ..creature_subtypes import canonical_creature_subtype
from ..death_keyword_absence import DeathKeywordAbsence
from ..object_predicate import ObjectQuerySpec
from ..rules.scheduled_player_shapes import SCHEDULED_PLAYER_CAPABILITY, is_upkeep_participant_schedule
from ..rules.attached_greatest_power_condition import (
    ATTACHED_GREATEST_POWER_CAPABILITIES, AttachedGreatestPowerCondition,
)
from ..scheduled_player import UPKEEP_HAND_COUNT_CAPABILITY, UpkeepParticipantHandCondition
from .fixed_public_event_trigger_bindings import creature_graveyard_owner_spec
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .public_state_queries import fixed_public_state_condition
from .public_state_fact_queries import greatest_creature_power_control_condition
from .trigger_event_vocabulary import FixedCounterTriggerEvent


FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY = 'trigger.condition.fixed_public_state'
PublicTriggerCondition = (
    FixedPublicStateConditionSpec | UpkeepParticipantHandCondition
    | ControllerCreatureKeywordAbsence | DeathKeywordAbsence | AttachedGreatestPowerCondition
)
PUBLIC_INTERVENING_IF_TRIGGER = re.compile(
    r'^(?P<prefix>.+?), if (?P<condition>[^,]+), (?P<body>.+)$', re.I,
)
_UPKEEP_HAND_COUNT = re.compile(
    rf'that player has (?P<threshold>{FIXED_COUNT_PATTERN}) or (?P<direction>fewer|more) cards in hand',
    re.I,
)
_CREATURE_TOKEN_ABSENCE = re.compile(
    r"you don['’]t control (?:a|an) (?P<subtype>[A-Za-z][A-Za-z' -]*) creature token", re.I,
)
_OTHER_CONTROLLED_SUBTYPE_DEATH = re.compile(
    r"Whenever another (?P<subtype>[A-Z][A-Za-z'\-]*) you control dies", re.I,
)
_DECLARED_POWER_PREFIX = re.compile(r"(?:Pack tactics\s+[—-]\s+)?Whenever this creature attacks", re.I)
_DECLARED_POWER_CONDITION = re.compile(
    r"you attacked with creatures with total power (?P<minimum>[1-9][0-9]{0,2}) or greater this combat",
    re.I,
)


def upkeep_hand_condition(text: str) -> UpkeepParticipantHandCondition | None:
    if text.casefold() == 'that player has no cards in hand':
        return UpkeepParticipantHandCondition('at_most', 0)
    match = _UPKEEP_HAND_COUNT.fullmatch(text)
    if match is None:
        return None
    comparison = 'at_most' if match['direction'].casefold() == 'fewer' else 'at_least'
    threshold = fixed_number(match['threshold'])
    if threshold == 0 and comparison == 'at_least':
        return None
    return UpkeepParticipantHandCondition(comparison, threshold)


def _each_upkeep_creature_token_absence(prefix: str, text: str) -> FixedPublicStateConditionSpec | None:
    if prefix.casefold() != 'at the beginning of each upkeep':
        return None
    match = _CREATURE_TOKEN_ABSENCE.fullmatch(text)
    subtype = canonical_creature_subtype(match['subtype']) if match else None
    if subtype is None:
        return None
    query = ObjectQuerySpec(zones=('battlefield',), types_all=('creature',),
        subtypes_all=(subtype.casefold(),), token=True)
    quantity = CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE, query=query)
    return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.QUERY_COUNT_AT_MOST,
        amount=0, quantity=quantity, schema_version=2)

def _controller_end_step_creature_keyword_absence(
    prefix: str, text: str,
) -> ControllerCreatureKeywordAbsence | None:
    if (prefix.casefold() == 'at the beginning of your end step'
            and text.casefold() == 'you control no creatures with decayed'):
        return ControllerCreatureKeywordAbsence('decayed')
    return None


def _death_keyword_absence(prefix: str, text: str) -> DeathKeywordAbsence | None:
    if text.casefold().replace("’", "'") != "it didn't have decayed":
        return None
    match = _OTHER_CONTROLLED_SUBTYPE_DEATH.fullmatch(prefix)
    subtype = canonical_creature_subtype(match['subtype']) if match else None
    return DeathKeywordAbsence(subtype) if subtype is not None else None


def _graveyard_source_intervening_binding(match, *, card_name, compile_binding):
    text = f"{match['prefix']}, {match['body']}"
    spec = creature_graveyard_owner_spec(text, active_zone="graveyard")
    if spec is None:
        return None
    base = compile_binding(text, card_name=card_name)
    if base is None or base.variant != spec.variant:
        return None
    condition = FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
        schema_version=2,
        predicate=ObjectQuerySpec(zones=("graveyard",)),
    )
    return replace(base, public_active_zone=spec.active_zone, public_condition=spec.condition,
                   public_state_condition=condition)


def _zero_mana_cast_counter_binding(match, *, card_name, compile_binding):
    if (
        match["prefix"].casefold() not in {"whenever a player casts a spell", "whenever an opponent casts a spell"}
        or match["body"].casefold() != "counter that spell."
    ):
        return None
    return _zero_mana_cast_binding(match, card_name=card_name, compile_binding=compile_binding)


def _zero_mana_cast_binding(match, *, card_name, compile_binding):
    base = compile_binding(f"{match['prefix']}, {match['body']}", card_name=card_name)
    if base is None or base.spell_subject is None:
        return None
    subject = replace(base.spell_subject, requires_no_mana_spent=True)
    return replace(base, variant=subject.variant, spell_subject=subject)


def _zero_mana_cast_damage_binding(match, *, card_name, compile_binding):
    if (
        match["prefix"].casefold() != "whenever a player casts a spell"
        or match["body"].casefold() != "this enchantment deals 5 damage to that player."
    ):
        return None
    return _zero_mana_cast_binding(match, card_name=card_name, compile_binding=compile_binding)


def _attached_greatest_power_trigger_condition(match) -> AttachedGreatestPowerCondition | None:
    if (
        match["prefix"].casefold() == "when this aura enters"
        and match["condition"].casefold()
        == "enchanted permanent is a creature with the greatest power among creatures on the battlefield"
        and match["body"].casefold() == "draw a card."
    ):
        return AttachedGreatestPowerCondition()
    return None


def _other_spell_self_cast_binding(match, *, card_name, compile_binding):
    if (match["prefix"].casefold() != "when you cast this spell"
            or match["condition"].casefold().replace("’", "'") != "you've cast another spell this turn"
            or match["body"].casefold() != "copy this spell."):
        return None
    base = compile_binding(f"{match['prefix']}, copy it.", card_name=card_name)
    if base is None or base.spell_subject is None or not base.spell_subject.source_spell:
        return None
    subject = replace(base.spell_subject, caster_spell_number_minimum=2)
    return replace(base, variant=subject.variant, spell_subject=subject)


def _entrant_name_absence_binding(match, *, card_name, compile_binding):
    from ..rules.entrant_name_absence import ENTRANT_NAME_ABSENCE_CAPABILITY, entrant_name_absence_condition

    if tuple(match[field].casefold() for field in ("prefix", "condition", "body")) != (
        "whenever a nontoken creature you control enters",
        "it doesn't have the same name as another creature you control or a creature card in your graveyard",
        "draw a card.",
    ):
        return None
    base = compile_binding(f"{match['prefix']}, {match['body']}", card_name=card_name)
    if base is None or (base.event.value, base.active_zone) != ("creature.enter", "battlefield"):
        return None
    condition = entrant_name_absence_condition(base.event_condition)
    if condition is None:
        return None
    return replace(
        base, public_mechanic="trigger-event-normalized-zone-change", public_condition=condition,
        public_template_id="fixed-entrant-name-absence-trigger-v1", public_active_zone="battlefield",
        public_capabilities=(*base.public_capabilities, "trigger.condition.fixed_public_state",
                             ENTRANT_NAME_ABSENCE_CAPABILITY),
    )


def _combat_damage_entry_binding(match, *, card_name, compile_binding):
    from ..rules.combat_damage_entry_history import COMBAT_DAMAGE_ENTRY_CAPABILITIES, combat_damage_entry_condition

    if (match["prefix"].casefold() != "whenever a creature you control deals combat damage to a player"
            or match["condition"].casefold() != "that creature entered this turn"
            or match["body"].casefold() != "draw a card."):
        return None
    base = compile_binding(f"{match['prefix']}, {match['body']}", card_name=card_name)
    if base is None or base.event.value != "damage.dealt" or base.active_zone != "battlefield":
        return None
    condition = combat_damage_entry_condition(base.event_condition)
    if condition is None:
        return None
    required = tuple(dict.fromkeys((*base.public_capabilities, *COMBAT_DAMAGE_ENTRY_CAPABILITIES)))
    return replace(base, public_condition=condition, public_capabilities=required)


def _tapped_draw_damage_binding(match, *, card_name, compile_binding):
    if tuple(match[field].casefold() for field in ("prefix", "condition", "body")) != (
        "at the beginning of your draw step", "this artifact is tapped", "it deals 1 damage to you.",
    ):
        return None
    from ..rules.trigger_source_information import (
        TAPPED_DRAW_SOURCE_DAMAGE_CAPABILITY, TAPPED_DRAW_SOURCE_DAMAGE_TEMPLATE,
        tapped_draw_source_condition,
    )

    base = compile_binding(f"{match['prefix']}, This artifact deals 1 damage to you.", card_name=card_name)
    if base is None or (base.event.value, base.active_zone, base.variant) != (
        "step.begin", "battlefield", "your draw step",
    ):
        return None
    return replace(
        base, public_state_condition=tapped_draw_source_condition(),
        public_mechanic="cr-603-handling-triggered-abilities",
        public_template_id=TAPPED_DRAW_SOURCE_DAMAGE_TEMPLATE,
        public_active_zone=base.active_zone, public_condition=base.event_condition,
        public_capabilities=(*base.public_capabilities, TAPPED_DRAW_SOURCE_DAMAGE_CAPABILITY),
    )


def _opponent_damage_untapped_draw_binding(match):
    if (
        match["prefix"].casefold() != "whenever a source an opponent controls deals damage to you"
        or match["condition"].casefold() != "this artifact is untapped"
        or match["body"].casefold() != "you may draw a card."
    ):
        return None
    from .fixed_counter_trigger_nodes import FixedCounterTriggerBinding
    from ..rules.optional_counter_capability_shapes import opponent_damage_to_controller_condition
    from ..rules.trigger_source_information import (
        UNTAPPED_DAMAGE_DRAW_TEMPLATE, UNTAPPED_DAMAGE_DRAW_VARIANT, untapped_damage_source_condition,
    )

    return FixedCounterTriggerBinding(
        event=FixedCounterTriggerEvent.PUBLIC_DEALS_DAMAGE, variant=UNTAPPED_DAMAGE_DRAW_VARIANT,
        body=match["body"], public_active_zone="battlefield",
        public_mechanic="trigger-event-normalized-damage", public_template_id=UNTAPPED_DAMAGE_DRAW_TEMPLATE,
        public_condition=opponent_damage_to_controller_condition(), public_state_condition=untapped_damage_source_condition(),
    )


def _source_counter_upkeep_draw_binding(match, *, card_name, compile_binding):
    if (
        match["prefix"].casefold() != "at the beginning of your upkeep"
        or match["condition"].casefold() != "this creature has one or more +1/+1 counters on it"
        or match["body"].casefold() != "you may remove a +1/+1 counter from it. if you do, draw a card."
    ):
        return None
    from ..rules.fixed_effect_payment_shapes import source_counter_upkeep_draw_condition

    body = "You may remove a +1/+1 counter from this creature. If you do, draw a card."
    base = compile_binding(f"{match['prefix']}, {body}", card_name=card_name)
    if base is None or base.event is not FixedCounterTriggerEvent.STEP_BEGIN or base.active_zone != "battlefield":
        return None
    return replace(base, public_state_condition=source_counter_upkeep_draw_condition())


def _opponent_death_toughness_binding(match, *, card_name, compile_binding):
    if tuple(match[field].casefold() for field in ("prefix", "condition", "body")) != (
        "whenever a creature an opponent controls dies", "its toughness was less than 1", "draw a card.",
    ):
        return None
    base = compile_binding(f"{match['prefix']}, {match['body']}", card_name=card_name)
    opponent = {"field": "controller", "op": "ne", "value": "$source.controller"}
    if base is None or base.event.value != "creature.dies" or base.active_zone != "battlefield" or base.event_condition != opponent:
        return None
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
    from .qualified_zone_event_bindings import public_binding_from_spec

    # The intervening clause reads the same sealed toughness at both checks.
    spec = FixedPublicEventBindingSpec(
        event=base.event.value, variant="opponent_creature_death_low_toughness", body=base.body,
        template_id="fixed-counter-opponent-creature-death-low-toughness-v1",
        mechanic="trigger-event-normalized-zone-change",
        condition={"all": [opponent, {"field": "toughness", "op": "lt", "value": 1}]},
    )
    return public_binding_from_spec(spec, binding_type=type(base), event_type=type(base.event))


def _entrant_land_advantage_binding(match, *, card_name, compile_binding):
    if tuple(match[field].casefold() for field in ("prefix", "condition", "body")) != (
        "whenever a land an opponent controls enters",
        "that player controls more lands than you",
        "you may put a land card from your hand onto the battlefield.",
    ):
        return None
    from ..land_entry_condition_model import (
        ENTRANT_LAND_ADVANTAGE_CAPABILITY, entrant_land_advantage_condition,
    )

    base = compile_binding(f"{match['prefix']}, {match['body']}", card_name=card_name)
    if (base is None or (base.event.value, base.active_zone, base.variant)
            != ("land.enter", "battlefield", "opponent_land")):
        return None
    return replace(
        base,
        public_condition=entrant_land_advantage_condition(),
        public_template_id="fixed-counter-opponent-land-entry-current-advantage-v1",
        public_capabilities=(*base.public_capabilities, FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY,
                             ENTRANT_LAND_ADVANTAGE_CAPABILITY),
    )


def _opponent_other_land_arrival_binding(match):
    if tuple(match[field].casefold().replace("’", "'") for field in ("prefix", "condition", "body")) != (
        "whenever a land enters under an opponent's control",
        "that player had another land enter the battlefield under their control this turn",
        "this creature deals 3 damage to that player.",
    ):
        return None
    from ..land_entry_condition_model import (
        OTHER_LAND_ARRIVAL_EVENT, OTHER_LAND_ARRIVAL_VARIANT, opponent_other_land_arrival_condition,
    )
    from ..public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY
    from .fixed_counter_trigger_nodes import FixedCounterTriggerBinding
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
    from .qualified_zone_event_bindings import public_binding_from_spec

    spec = FixedPublicEventBindingSpec(
        event=OTHER_LAND_ARRIVAL_EVENT, variant=OTHER_LAND_ARRIVAL_VARIANT, body=match["body"],
        template_id="fixed-counter-opponent-land-entry-history-v1",
        mechanic="trigger-event-normalized-zone-change", condition=opponent_other_land_arrival_condition(),
        capabilities=(PUBLIC_HISTORY_QUERY_CAPABILITY, FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY),
    )
    return public_binding_from_spec(spec, binding_type=FixedCounterTriggerBinding, event_type=FixedCounterTriggerEvent)


def _source_entered_end_step_phase_binding(match, *, card_name, compile_binding, compile_source_binding):
    if (match["prefix"].casefold() != "at the beginning of your end step"
            or match["body"].casefold() not in {"he phases out", "he phases out."}):
        return None
    from ..rules.source_references import SourceReferenceSpec

    entered = re.fullmatch(r"(?P<source>.+?) entered this turn", match["condition"], re.I)
    if entered is None or not SourceReferenceSpec(card_name).matches(entered["source"]):
        return None
    text = f"{match['prefix']}, this planeswalker phases out."
    base = compile_binding(text, card_name=card_name) or compile_source_binding(text, card_name=card_name)
    if (
        base is None or base.event is not FixedCounterTriggerEvent.STEP_BEGIN or base.active_zone != "battlefield"
        or not is_controller_end_step_schedule(base.event_condition)
    ):
        return None
    condition = FixedPublicStateConditionSpec(FixedPublicStateConditionKind.SOURCE_ENTERED_THIS_TURN)
    return replace(base, public_state_condition=condition)


def opponent_threshold_search_body(body):
    from .library_search_templates import fixed_creature_group_library_search_template

    match = re.fullmatch(r"Sacrifice this enchantment, (?P<search>search .+)", " ".join(body.split()), re.I)
    return fixed_creature_group_library_search_template(match["search"]) if match is not None else None


def opponent_creature_threshold_condition(text):
    match = re.fullmatch(rf"an opponent controls (?P<count>{FIXED_COUNT_PATTERN}) or more creatures", text, re.I)
    if match is None:
        return None
    count = fixed_number(match["count"])
    if not 1 <= count <= 10:
        return None
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST, amount=count, schema_version=4,
        fact=FixedPublicStateFact.OPPONENT_CREATURE_COUNT_MAXIMUM,
    )


def _unpaid_creature_entry_binding(match, *, card_name):
    from ..rules.source_references import SourceReferenceSpec
    from ..rules.unpaid_creature_entry import DEPS, condition
    from .fixed_counter_trigger_nodes import FixedCounterTriggerBinding
    from .fixed_public_event_trigger_bindings import _spec
    from .qualified_zone_event_bindings import public_binding_from_spec

    if (not card_name or match["condition"].casefold() != "none of them were cast or no mana was spent to cast them"
            or match["body"].casefold() != "draw a card."):
        return None
    prefix = re.fullmatch(r"Whenever (?P<source>.+?) and/or one or more other nontoken creatures you control enter",
                          match["prefix"], re.I)
    if prefix is None or not SourceReferenceSpec(card_name).matches(prefix["source"]):
        return None
    spec = _spec("permanent.enter", "unpaid_creature_entry_group", match["body"],
                 "unpaid-creature-entry-group-draw-v1", "trigger-event-normalized-zone-change",
                 condition=condition(), capabilities=DEPS)
    return public_binding_from_spec(spec, binding_type=FixedCounterTriggerBinding, event_type=FixedCounterTriggerEvent)


def bind_public_intervening_trigger(match, *, card_name, compile_binding, compile_source_binding):
    if match["condition"].casefold().replace("’", "'") == "it's attacking":
        from .fixed_counter_trigger_nodes import _public_trigger_binding

        if binding := _public_trigger_binding(match.string, card_name=card_name):
            return binding
    from .counter_movement_templates import departure_counter_transfer_binding
    if departure := departure_counter_transfer_binding(match.string):
        from .fixed_counter_trigger_nodes import FixedCounterTriggerBinding
        from .qualified_zone_event_bindings import public_binding_from_spec
        return public_binding_from_spec(departure, binding_type=FixedCounterTriggerBinding,
                                        event_type=FixedCounterTriggerEvent)
    phase = _source_entered_end_step_phase_binding(
        match, card_name=card_name, compile_binding=compile_binding, compile_source_binding=compile_source_binding,
    )
    if phase is not None:
        return phase
    if unpaid := _unpaid_creature_entry_binding(match, card_name=card_name):
        return unpaid
    if land := _opponent_other_land_arrival_binding(match):
        return land
    if advantage := _entrant_land_advantage_binding(match, card_name=card_name, compile_binding=compile_binding):
        return advantage
    if death := _opponent_death_toughness_binding(match, card_name=card_name, compile_binding=compile_binding):
        return death
    if counter_draw := _source_counter_upkeep_draw_binding(match, card_name=card_name, compile_binding=compile_binding):
        return counter_draw
    if tapped_draw := _tapped_draw_damage_binding(match, card_name=card_name, compile_binding=compile_binding):
        return tapped_draw
    if untapped_draw := _opponent_damage_untapped_draw_binding(match):
        return untapped_draw
    if entrant_names := _entrant_name_absence_binding(match, card_name=card_name, compile_binding=compile_binding):
        return entrant_names
    if combat_entry := _combat_damage_entry_binding(match, card_name=card_name, compile_binding=compile_binding):
        return combat_entry
    if other_spell := _other_spell_self_cast_binding(match, card_name=card_name, compile_binding=compile_binding):
        return other_spell
    attached_maximum = _attached_greatest_power_trigger_condition(match)
    if attached_maximum is not None:
        text = f"{match['prefix']}, {match['body']}"
        base = compile_binding(text, card_name=card_name) or compile_source_binding(text, card_name=card_name)
        if base is None or base.event.value != "permanent.enter.self" or base.active_zone != "battlefield":
            return None
        return replace(base, public_state_condition=attached_maximum)
    if match["condition"].casefold() == "no mana was spent to cast it":
        return _zero_mana_cast_counter_binding(match, card_name=card_name, compile_binding=compile_binding)
    if match["condition"].casefold() == "no mana was spent to cast that spell":
        return _zero_mana_cast_damage_binding(match, card_name=card_name, compile_binding=compile_binding)
    power = _DECLARED_POWER_CONDITION.fullmatch(match["condition"])
    if power is not None and _DECLARED_POWER_PREFIX.fullmatch(match["prefix"]) is not None:
        base = compile_binding(f"Whenever this creature attacks, {match['body']}", card_name=card_name)
        return None if base is None else replace(base, minimum_declared_attack_power=int(power["minimum"]))
    if match["condition"].casefold() == "this card is in your graveyard":
        return _graveyard_source_intervening_binding(match, card_name=card_name, compile_binding=compile_binding)
    token_absence = _each_upkeep_creature_token_absence(match['prefix'], match['condition'])
    keyword_absence = _controller_end_step_creature_keyword_absence(match['prefix'], match['condition'])
    death_absence = _death_keyword_absence(match['prefix'], match['condition'])
    condition = (
        upkeep_hand_condition(match['condition']) or token_absence or keyword_absence or death_absence
        or greatest_creature_power_control_condition(match['condition'])
        or opponent_creature_threshold_condition(match["condition"])
        or fixed_public_state_condition(match['condition'], source_name=card_name or 'This source')
    )
    if condition is None:
        return None
    if isinstance(condition, FixedPublicStateConditionSpec) and any(leaf.kind in {
        FixedPublicStateConditionKind.SOURCE_ENTERED_THIS_TURN,
        FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST,
        FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY,
    } for leaf in condition.leaf_conditions()):
        return None
    body = match["body"]
    if (match["condition"].casefold() == "this permanent is an enchantment"
            and body.casefold().startswith("it becomes a ")):
        body = "This permanent " + body[3:]
    elif (match["condition"].casefold() == "this permanent is a creature"
            and re.fullmatch(r"it becomes an enchantment\.?", body, re.I)):
        body = "This permanent becomes an enchantment."
    if (
        isinstance(condition, FixedPublicStateConditionSpec)
        and condition.fact is FixedPublicStateFact.CONTROLLER_BELOW_STARTING_LIFE
        and re.fullmatch(r"it becomes equal to your starting life total\.?", body, re.I)
    ):
        body = "Your life total becomes equal to your starting life total."
    text = f"{match['prefix']}, {body}"
    base = compile_binding(text, card_name=card_name) or compile_source_binding(text, card_name=card_name)
    if base is None or base.public_state_condition is not None:
        return None
    if isinstance(condition, FixedPublicStateConditionSpec) and (
        condition.fact in GREATEST_CREATURE_POWER_FACTS and base.active_zone != 'battlefield'
    ):
        return None
    if (isinstance(condition, FixedPublicStateConditionSpec)
            and condition.fact is FixedPublicStateFact.OPPONENT_CREATURE_COUNT_MAXIMUM
            and ((base.active_zone, base.event.value, base.variant) != ("battlefield", "step.begin", "your upkeep")
                 or opponent_threshold_search_body(body) is None)):
        return None
    if token_absence is not None and (base.active_zone != 'battlefield' or base.event is not FixedCounterTriggerEvent.STEP_BEGIN):
        return None
    if isinstance(condition, UpkeepParticipantHandCondition) and (
        base.active_zone != 'battlefield' or base.event is not FixedCounterTriggerEvent.STEP_BEGIN
        or not is_upkeep_participant_schedule(base.event_condition)
    ):
        return None
    if keyword_absence is not None and (
        base.active_zone != 'battlefield' or base.event is not FixedCounterTriggerEvent.STEP_BEGIN
        or not is_controller_end_step_schedule(base.event_condition)
    ):
        return None
    if death_absence is not None and (
        base.active_zone != 'battlefield' or base.event is not FixedCounterTriggerEvent.CREATURE_DIES
        or base.event_condition != death_absence.occurrence_condition()
    ):
        return None
    return replace(base, public_state_condition=condition)


def public_trigger_condition_capabilities(condition: PublicTriggerCondition | None) -> tuple[str, ...]:
    if condition is None:
        return ()
    if isinstance(condition, UpkeepParticipantHandCondition):
        return (FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY, SCHEDULED_PLAYER_CAPABILITY, UPKEEP_HAND_COUNT_CAPABILITY)
    if isinstance(condition, AttachedGreatestPowerCondition):
        return ATTACHED_GREATEST_POWER_CAPABILITIES
    if isinstance(condition, (ControllerCreatureKeywordAbsence, DeathKeywordAbsence)):
        return (FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY,)
    if condition.fact in GREATEST_CREATURE_POWER_FACTS:
        return (FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY,
                *greatest_creature_power_condition_dependencies(condition.to_dict()))
    if condition.fact is FixedPublicStateFact.UNIQUE_PLAYER_MAXIMUM:
        from ..player_maximum_model import PLAYER_MAXIMUM_CAPABILITY
        return (FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY,PLAYER_MAXIMUM_CAPABILITY)
    if condition.fact is FixedPublicStateFact.OPPONENT_CREATURE_COUNT_MAXIMUM:
        from ..player_maximum_model import OPPONENT_CREATURE_THRESHOLD_CAPABILITY

        return FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY, OPPONENT_CREATURE_THRESHOLD_CAPABILITY
    if condition.fact is FixedPublicStateFact.OPPONENT_HAS_GREATER_METRIC:
        from ..player_maximum_model import OPPONENT_MORE_METRIC_CAPABILITY
        return (FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY,OPPONENT_MORE_METRIC_CAPABILITY)
    if condition.kind is FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS:
        return (FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY, 'condition.creature.fixed_numeric_exists')
    from ..initiative_model import dungeon_condition_dependencies
    query_capabilities = {
        capability
        for leaf in condition.leaf_conditions()
        for capability in nonowner_control_quantity_capabilities(leaf.quantity)
    }
    return (FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY, *dungeon_condition_dependencies(condition),
            *sorted(query_capabilities))
