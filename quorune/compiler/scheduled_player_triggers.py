from __future__ import annotations

"""Fixed scheduled-player predicates and nontargeted result binding."""

from copy import deepcopy
from . import regex_working_set as re

from ..additional_beginning_model import (
    ADDITIONAL_BEGINNING_CAPABILITY,
    ADDITIONAL_BEGINNING_CONTROLLER_VARIANT,
    ADDITIONAL_BEGINNING_ENCHANTED_VARIANT,
    ADDITIONAL_BEGINNING_VARIANTS,
    additional_beginning_condition,
    additional_beginning_effect,
)
from ..entry_designations import (
    ENTRY_DESIGNATION_CAPABILITY, LINKED_OPPONENT_UPKEEP_TEMPLATE, LINKED_OPPONENT_UPKEEP_VARIANT,
    linked_opponent_upkeep_condition,
)
from ..power_sacrifice_model import CONTROLLER_SACRIFICE_LIFE_TEMPLATE, controller_sacrifice_toughness_life_effect
from ..rules.affected_player_sacrifice_capability_shapes import upkeep_sacrifice_life_condition
from ..rules.scheduled_player_shapes import SCHEDULED_PLAYER_CAPABILITY, SCHEDULED_PLAYER_MECHANIC, upkeep_source_counter_draw_effect
from .affected_player_sacrifice_templates import FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC
from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
from .fixed_controller_effect_sequences import _clauses
from .event_result_value_templates import scheduled_fractional_life_instruction
from .affected_player_discard_templates import (
    FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC,
    FIXED_CONTROLLER_DISCARD_MECHANIC,
)

SCHEDULED_PLAYER_VARIANTS = frozenset({'each_player_upkeep','each_opponent_upkeep','enchanted_object_controller_upkeep',
                                     'each_player_draw','each_opponent_draw','each_other_player_draw'})


_UPKEEP_SACRIFICE_LIFE_VARIANT = "controller_upkeep_selected_sacrifice_life"
_UPKEEP_SACRIFICE_LIFE = re.compile(
    r"At the beginning of your upkeep, (?P<body>sacrifice a creature\. You gain life equal to that creature['’]s toughness\.)",
    re.I,
)


_ADDITIONAL_BEGINNING = re.compile(
    r"At the beginning of (?:(?P<own>your postcombat main phase|each of your postcombat main phases)|"
    r"enchanted player['’]s postcombat main phase|each of enchanted player['’]s postcombat main phases), "
    r"(?P<body>there is an additional beginning phase after this phase[.]?)",
    re.I,
)


_OPPONENT_MAIN_MANA = re.compile(
    r"At the beginning of each of your main phases, if you haven['’]t added mana with this ability this turn, "
    r"you may add X mana of any one color, where X is the number of "
    r"(?P<type>Plains|Islands|Swamps|Mountains|Forests) target opponent controls\.?",
    re.I,
)


def main_phase_opponent_mana_node(*, node_id, line, material_line, span, card_name,
                                  capability_registry, capability_profile, residuals):
    match = _OPPONENT_MAIN_MANA.fullmatch(material_line.strip())
    if match is None:
        return None
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..rules.target_player_query_amounts import (
        MAIN_PHASE_OPPONENT_MANA_LIMIT_VERSION, MAIN_PHASE_OPPONENT_MANA_TEMPLATE,
        main_phase_opponent_mana_condition, main_phase_opponent_mana_effect,
        main_phase_opponent_mana_requirements, main_phase_opponent_mana_target_schema,
    )
    from ..rules.trigger_limits import OPTIONAL_RESULT_LIMIT_KIND, TriggerLimitSpec
    from .dependency_gate import explicit_capabilities_gate
    from .ir_model import OracleNode, append_residual

    subtype = match["type"].casefold()
    subtype = subtype if subtype == "plains" else subtype.removesuffix("s")
    effects = (main_phase_opponent_mana_effect(subtype),)
    targets = main_phase_opponent_mana_target_schema()
    required = main_phase_opponent_mana_requirements(effects, targets)
    gate = explicit_capabilities_gate(required, capability_registry=capability_registry, capability_profile=capability_profile)
    pending = ()
    if gate.blockers:
        pending = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
            reason="Optional main-phase mana requires its target, public count and successful-result authority",
            blockers=gate.blockers),)
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span,
        active_zone="battlefield", event="step.begin", event_condition=main_phase_opponent_mana_condition(),
        trigger_limit=TriggerLimitSpec(MAIN_PHASE_OPPONENT_MANA_LIMIT_VERSION, OPTIONAL_RESULT_LIMIT_KIND).to_dict(),
        lowerable=True, exact=not pending, template_id=MAIN_PHASE_OPPONENT_MANA_TEMPLATE,
        effects=effects, target_schema=targets,
        mechanics=("cr-603-handling-triggered-abilities", "cr-115-targets"),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE, "fixed_typed_event_effect_trigger"),
        residual_ids=pending, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (), capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )


def scheduled_player_binding_spec(text, *, card_name=None):
    if extra := _ADDITIONAL_BEGINNING.fullmatch(text):
        enchanted = extra["own"] is None
        return FixedPublicEventBindingSpec(
            event="step.begin",
            variant=ADDITIONAL_BEGINNING_ENCHANTED_VARIANT if enchanted else ADDITIONAL_BEGINNING_CONTROLLER_VARIANT,
            body=extra["body"],
            template_id="postcombat-additional-beginning-trigger-v1",
            mechanic="cr-603-handling-triggered-abilities",
            condition=additional_beginning_condition(enchanted=enchanted),
            capabilities=(ADDITIONAL_BEGINNING_CAPABILITY, "trigger.placement.apnap"),
        )
    from .target_hand_amount_templates import upkeep_target_hand_life_binding_spec

    if hand_life := upkeep_target_hand_life_binding_spec(text):
        return hand_life
    if sacrifice := _UPKEEP_SACRIFICE_LIFE.fullmatch(text):
        return FixedPublicEventBindingSpec(
            event="step.begin", variant=_UPKEEP_SACRIFICE_LIFE_VARIANT, body=sacrifice["body"],
            template_id=CONTROLLER_SACRIFICE_LIFE_TEMPLATE, mechanic="cr-603-handling-triggered-abilities",
            condition=upkeep_sacrifice_life_condition(),
        )
    linked = re.fullmatch(r"At the beginning of the chosen player['’]s upkeep, (?P<body>.+)", text, re.I)
    if linked is not None:
        return FixedPublicEventBindingSpec(
            event="step.begin", variant=LINKED_OPPONENT_UPKEEP_VARIANT,
            body=linked["body"], template_id=LINKED_OPPONENT_UPKEEP_TEMPLATE,
            mechanic="cr-603-handling-triggered-abilities", condition=linked_opponent_upkeep_condition(),
            capabilities=(SCHEDULED_PLAYER_CAPABILITY, ENTRY_DESIGNATION_CAPABILITY),
        )
    match=re.fullmatch(r"At the beginning of each (?P<who>player|opponent|other player)['’]s (?P<step>upkeep|draw step), (?P<body>.+)",text,re.I)
    attached=re.fullmatch(r"At the beginning of the upkeep of enchanted (?P<type>creature|permanent|artifact|enchantment|land)['’]s controller, (?P<body>.+)",text,re.I)
    if match is None and attached is None:return None
    step='draw' if match is not None and match['step'].casefold()=='draw step' else 'upkeep'
    conditions=[{'field':'step','op':'eq','value':step}]
    if attached is not None:
        conditions.append({'field':'source_attachment_controller','op':'eq','value':'$context.player','required_type':attached['type'].casefold()})
        return FixedPublicEventBindingSpec(event='step.begin',variant='enchanted_object_controller_upkeep',body=attached['body'],
            template_id='fixed-counter-step-trigger-v1',mechanic='cr-603-handling-triggered-abilities',condition={'all':conditions},
            capabilities=(SCHEDULED_PLAYER_CAPABILITY,'attachment.reference.current_or_lki'))
    who=match['who'].casefold().replace(' ','_')
    if who=='other_player' and step!='draw':return None
    if who in {'opponent','other_player'}:conditions.append({'field':'player','op':'ne','value':'$source.controller'})
    return FixedPublicEventBindingSpec(event='step.begin',variant='each_'+who+'_'+step,body=match['body'],
        template_id='fixed-counter-step-trigger-v1',mechanic='cr-603-handling-triggered-abilities',
        condition={'all':conditions},capabilities=(SCHEDULED_PLAYER_CAPABILITY,))


def _scheduled_creature_return_result(binding, body):
    if (binding.variant != "each_player_upkeep" or body != binding.body
            or re.fullmatch(r"That player returns a creature they control to its owner['’]s hand[.]?", body, re.I) is None):
        return None
    from ..rules.event_player_choice_shapes import UPKEEP_CREATURE_RETURN_MECHANIC, upkeep_creature_return_effect

    return (
        ("scheduled-player-fixed-creature-return-v1", (upkeep_creature_return_effect(),), None,
         (UPKEEP_CREATURE_RETURN_MECHANIC, SCHEDULED_PLAYER_MECHANIC)),
        True,
    )


def scheduled_player_result(binding, body, *, effect_template, card_name):
    if binding.variant in ADDITIONAL_BEGINNING_VARIANTS and body == binding.body:
        return ("postcombat-additional-beginning-v1", (additional_beginning_effect(),), None, ()), True
    if binding.variant == _UPKEEP_SACRIFICE_LIFE_VARIANT and body == binding.body:
        return (
            (CONTROLLER_SACRIFICE_LIFE_TEMPLATE, (controller_sacrifice_toughness_life_effect(),), None,
             (FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, "sacrifice", "cr-119-life")),
            True,
        )
    if binding.event.value == "step.begin" and binding.variant == "your upkeep" and body == binding.body:
        from .upkeep_power_counter_results import upkeep_power_counter_result
        if power_balance := upkeep_power_counter_result(body, card_name=card_name):
            return power_balance, True

        from .damage_templates import upkeep_other_controlled_creatures_damage_template
        from .upkeep_hand_difference_templates import controller_upkeep_hand_surplus_result

        if damage := upkeep_other_controlled_creatures_damage_template(body):
            return damage, True
        if life := controller_upkeep_hand_surplus_result(binding, body, effect_template=effect_template, card_name=card_name):
            return life
    from .target_hand_amount_templates import upkeep_target_hand_life_result

    if hand_life := upkeep_target_hand_life_result(binding, body):
        return hand_life
    if binding.variant == LINKED_OPPONENT_UPKEEP_VARIANT:
        from .upkeep_hand_difference_templates import upkeep_hand_difference_result
        return upkeep_hand_difference_result(binding, body, effect_template=effect_template, card_name=card_name)
    if binding.variant not in SCHEDULED_PLAYER_VARIANTS:return None
    if creature_return := _scheduled_creature_return_result(binding, body):
        return creature_return
    if (binding.variant == "each_player_upkeep" and body == binding.body and re.fullmatch(
            r"That player draws a card for each \+1/\+1 counter on this creature\.", body, re.I)):
        return ("scheduled-upkeep-source-counter-draw-v1", (upkeep_source_counter_draw_effect(),), None,
                (SCHEDULED_PLAYER_MECHANIC,)), True
    if (binding.variant == "each_player_upkeep" and body == binding.body
            and re.fullmatch(r"That player sacrifices a permanent of their choice for each soot counter on this artifact\.", body, re.I)):
        from ..rules.upkeep_source_counter_sacrifice import upkeep_source_counter_sacrifice_effect

        return ("upkeep-source-counter-sacrifice-v1", (upkeep_source_counter_sacrifice_effect(),), None,
                (SCHEDULED_PLAYER_MECHANIC, FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, "sacrifice")), True
    if (binding.variant == "each_player_upkeep" and body == binding.body
            and body.casefold() == "that player untaps a land they control."):
        from ..untargeted_untap_model import UNTARGETED_UNTAP_MECHANIC, scheduled_land_untap_effect

        return ("scheduled-player-land-untap-v2", (scheduled_land_untap_effect(),), None,
                (SCHEDULED_PLAYER_MECHANIC, UNTARGETED_UNTAP_MECHANIC, "tap-and-untap")), True
    from .scheduled_life_return import scheduled_life_return_result
    if life_return := scheduled_life_return_result(binding, body):
        return life_return
    if binding.variant in {'each_player_upkeep', 'each_opponent_upkeep'}:
        from .event_player_choice_templates import _sacrifice_clause
        from .affected_player_sacrifice_templates import fixed_affected_player_sacrifice_effect_template
        match = _sacrifice_clause(body)
        if match is not None and match['subject'].casefold() in {'that player', 'they'}:
            choice = fixed_affected_player_sacrifice_effect_template('Target player sacrifices ' + match['body'])
            if choice is not None:
                effect = deepcopy(dict(choice.effects[0]))
                if effect.pop('target', None) != '$target.0' or effect.get('players') != ['$target.0']:
                    return None
                effect['players'] = ['$context.player']
                return ('scheduled-player-fixed-sacrifice-v1', (effect,), None,
                    tuple(m for m in (*choice.mechanics, SCHEDULED_PLAYER_MECHANIC) if m != 'cr-115-targets')), True
    from .upkeep_hand_difference_templates import upkeep_hand_difference_result
    difference=upkeep_hand_difference_result(binding,body,effect_template=effect_template,card_name=card_name)
    if difference is not None:return difference
    from .hand_library_redraw_templates import hand_library_redraw_template
    if binding.variant == 'each_player_draw' and (redraw := hand_library_redraw_template(body)) is not None:
        from ..hand_library_redraw_model import HandLibraryScope
        if redraw.redraw.scope is HandLibraryScope.EVENT_PLAYER:
            template,effects,schema,mechanics = redraw.compiled()
            return (template,effects,schema,(*mechanics,SCHEDULED_PLAYER_MECHANIC)),True
    if re.search(r'\b(?:that player|them)\b',body,re.I) and re.search(r'\bdeals? [1-9]\d* damage to (?:that player|them)\.',body,re.I):
        fixed=re.sub(r'\b(?:that player|them)\b','target player',body,flags=re.I)
        template,effects,schema,mechanics=effect_template(fixed,card_name=card_name)
        if template is None or len(effects)!=1 or schema is None or schema.get('zones')!=['player'] or schema.get('count')!=1:return None
        effect=deepcopy(dict(effects[0]))
        if effect.get('op')!='damage' or effect.get('target')!='$target.0':return None
        effect['target']='$context.player'
        return ('scheduled-player-fixed-damage-v1',(effect,),None,tuple(dict.fromkeys((
            *(m for m in mechanics if m!='cr-115-targets'),SCHEDULED_PLAYER_MECHANIC)))),True
    if not re.match(r'(?:that player|they)\b',body,re.I):return None
    normalized=scheduled_fractional_life_instruction(body) if 'half' in body.casefold() else None
    if 'half' in body.casefold() and normalized is None:return None
    if normalized is None:
        normalized=re.sub(r'\b(?:that player|they)\b','you',body,flags=re.I)
        normalized=re.sub(r'\b(loses|gains|draws|mills|discards)\b',lambda m:m[0][:-1],normalized,flags=re.I)
    template,effects,schema,mechanics=effect_template(normalized,card_name=card_name)
    if template is None or schema is not None or not 1<=len(effects)<=2:return None
    clauses = _clauses(body) if len(effects) == 2 else (body,)
    if clauses is None or len(clauses) != len(effects):return None
    results=[]
    result_mechanics=[]
    player = '$context.player'
    for clause, effect in zip(clauses, effects):
        discard = effect.get('op') == 'choose_cards_apnap'
        if discard:
            if effect.get('actor') != '$controller' or effect.get('players') != ['$controller']:
                return None
        elif effect.get('op') not in {'life','lose_life','draw','mill'} or effect.get('player')!='$controller':return None
        subject = re.match(r'(that player|they|you)\b', clause, re.I)
        if subject is not None:
            player = '$controller' if subject[1].casefold() == 'you' else '$context.player'
        elif not re.match(r'(?:loses?|gains?|draws?|mills?|discards?)\b', clause, re.I):
            return None
        result=deepcopy(dict(effect))
        if discard:
            if player == '$context.player' and effect.get('count') not in {1, 2, 3}:
                return None
            result['players']=[player]
            result_mechanics.append(FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC if player == '$context.player' else FIXED_CONTROLLER_DISCARD_MECHANIC)
        else:
            result['player']=player
        results.append(result)
    return ('scheduled-player-fixed-result-v1',tuple(results),None,tuple(dict.fromkeys((
        *(m for m in mechanics if m not in {'fixed-controller-effect-sequence', 'closed-effect-program', 'bound-effect-program', FIXED_CONTROLLER_DISCARD_MECHANIC}),
        *result_mechanics,SCHEDULED_PLAYER_MECHANIC)))),True
