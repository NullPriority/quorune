"""Closed future actions preserve their independently established object role."""
from . import regex_working_set as re
from ..delayed_object_model import DELAYED_OBJECT_MECHANIC,SCHEDULE_OBJECT_ACTION
from ..rules.source_references import SourceReferenceSpec
from ..damage_recipient_model import DAMAGE_PERMANENT_VARIANT,DAMAGE_PERMANENT_OBJECT


def target_death_return_activation_template(ability, text):
    from ..delayed_object_model import (
        TARGET_DEATH_RETURN_MECHANICS, target_death_return_effect, target_death_return_target_schema,
    )

    if (ability.mana_ability or ability.zones != ("battlefield",)
            or ability.sorcery_speed or ability.activation_conditions):
        return None
    body = " ".join(text.split())
    battlefield = ability.sacrifice_source and re.fullmatch(
        r"When target creature is put into your graveyard this turn, return that card to the battlefield\.",
        body, re.I,
    )
    if battlefield:
        return (
            "target-creature-death-return-battlefield-v1", (target_death_return_effect(battlefield=True),),
            target_death_return_target_schema(battlefield=True), TARGET_DEATH_RETURN_MECHANICS,
        )
    if re.fullmatch(
        r"Choose target creature with a counter on it\. "
        r"When that creature dies this turn, return that card to its owner['’]s hand\.",
        body, re.I,
    ):
        return (
            "target-counter-creature-death-return-v1", (target_death_return_effect(),),
            target_death_return_target_schema(), TARGET_DEATH_RETURN_MECHANICS,
        )
    return None


def target_death_return_activation_gate(
    *, effects, target_schema, mechanics, active_zone, cost_schema, capability_registry, capability_profile,
):
    from ..delayed_object_model import (
        TARGET_DEATH_BATTLEFIELD_CAPABILITY, TARGET_DEATH_RETURN_CAPABILITY,
        is_target_death_battlefield_return, is_target_death_return_effect,
        target_death_return_node_capabilities, target_death_return_price_is_closed,
    )
    from .dependency_gate import DependencyGate, explicit_capabilities_gate

    if not any(is_target_death_return_effect(effect) for effect in effects):
        return None
    required = target_death_return_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
    )
    battlefield = is_target_death_battlefield_return(effects[0])
    if not required or active_zone != "battlefield" or not target_death_return_price_is_closed(cost_schema, battlefield=battlefield):
        capability = TARGET_DEATH_BATTLEFIELD_CAPABILITY if battlefield else TARGET_DEATH_RETURN_CAPABILITY
        return DependencyGate(blockers=(f"capability:{capability}",))
    return explicit_capabilities_gate(
        required, capability_registry=capability_registry, capability_profile=capability_profile,
    )


DELAYED_SOURCE_CONVERT_TEMPLATE = "combat-damage-player-delayed-source-convert-v1"


def _delayed_source_convert_result(binding, body):
    if (body != binding.body or (binding.event.value, binding.variant, binding.active_zone)
            != ("damage.dealt.self", "source_combat_damage_player", "battlefield")
            or binding.public_state_condition is not None
            or " ".join(body.split()).casefold() != "convert it at end of combat."):
        return None
    from ..delayed_object_model import delayed_source_convert_effect

    return (DELAYED_SOURCE_CONVERT_TEMPLATE, (delayed_source_convert_effect(),), None, (DELAYED_OBJECT_MECHANIC,)), False


def delayed_object_result(binding,body,*,card_name):
    from .small_creature_delayed_return_templates import small_creature_delayed_return_template

    if conversion := _delayed_source_convert_result(binding, body):
        return conversion
    if returned := small_creature_delayed_return_template(binding, body, card_name=card_name):
        return returned, False
    if (binding.event.value == "creature.attacks"
            and binding.variant == "public_attack_a_creature_you_control"
            and re.fullmatch(r"it phases out at end of combat\.?", body.strip(), re.I)):
        from ..delayed_object_model import controlled_attacker_delayed_phase_out_effect

        return (("controlled-attacker-delayed-phase-out-v1", (controlled_attacker_delayed_phase_out_effect(),),
                 None, (DELAYED_OBJECT_MECHANIC,)), False)

    counter=_delayed_source_counter_result(binding,body,card_name=card_name)
    if counter is not None:return counter
    match=re.fullmatch(r'(?P<action>Destroy|Sacrifice) (?P<subject>.+?) (?P<time>at end of combat|at the beginning of the next end step)\.',body,re.I)
    if match is None:
        match=re.fullmatch(r"(?P<action>Return) (?P<subject>.+?) to its owner's hand (?P<time>at end of combat|at the beginning of the next end step)\.(?: \(Return it only if it's on the battlefield\.\))?",body,re.I)
    if match is None:return None
    subject=match['subject'].casefold()
    if subject=='that creature' and binding.variant=='source_block_pair_counterpart':reference='$event.block_counterpart'
    elif subject=='that creature' and binding.variant==DAMAGE_PERMANENT_VARIANT:reference=DAMAGE_PERMANENT_OBJECT
    elif (subject in {'this creature','this permanent','this artifact','this enchantment','this land'}
            or SourceReferenceSpec(card_name).matches(match['subject'])):reference='$source.zone_object'
    elif subject in {'it','him','her'} and (binding.event.value.endswith('.self') or binding.variant in {
            'source_attacks','this_creature_blocks','this_creature_becomes_blocked','source_attacks_or_blocks',
            'source_blocks_or_becomes_blocked'}):reference='$source.zone_object'
    else:return None
    action=match['action'].casefold();action='bounce' if action=='return' else action
    effect={'op':SCHEDULE_OBJECT_ACTION,'schema_version':1,'object':reference,'action':action,
        'timing':'end_of_combat' if match['time'].casefold()=='at end of combat' else 'next_end_step'}
    return ('fixed-delayed-object-action-v1',(effect,),None,(DELAYED_OBJECT_MECHANIC,)),False


def _delayed_source_counter_result(binding,body,*,card_name):
    match=re.fullmatch(r'(?P<body>(?:Put|Remove) .+?) (?P<time>at end of combat|at the beginning of the next end step)\.',body,re.I)
    if match is None:return None
    text=match['body']+'.'
    source_pronoun=binding.event.value.endswith('.self') or binding.variant in {
        'source_attacks','this_creature_blocks','this_creature_becomes_blocked','source_attacks_or_blocks','source_blocks_or_becomes_blocked'}
    if source_pronoun:text=re.sub(r'\b(on|from) (?:it|him|her)(?=\.)',r'\1 this permanent',text,flags=re.I)
    from .counter_placement_templates import fixed_counter_placement_effect_template
    from .counter_removal_templates import fixed_source_counter_removal_effect_template
    placement=fixed_counter_placement_effect_template(text,card_name=card_name)
    compiled=placement.compiled() if placement is not None else fixed_source_counter_removal_effect_template(text,card_name=card_name)
    if compiled is None:return None
    template,effects,schema,mechanics=compiled
    if schema is not None or len(effects)!=1 or effects[0].get('card')!='$source.zone_object':return None
    primitive=effects[0]
    effect={'op':SCHEDULE_OBJECT_ACTION,'schema_version':2,'object':'$source.zone_object',
        'action':primitive['op'],'counter':primitive['counter'],'amount':primitive['amount'],'source':'$source',
        'timing':'end_of_combat' if match['time'].casefold()=='at end of combat' else 'next_end_step'}
    return ('fixed-delayed-source-counter-v2',(effect,),None,(DELAYED_OBJECT_MECHANIC,*mechanics)),False
