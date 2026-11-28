"""Closed event-bound countering, damage and player outcomes."""
from . import regex_working_set as re
from .event_player_results import _event_player


def fixed_event_outcome_template(binding, body, *, card_name, effect_template):
    from ..saga_lifecycle import (
        FINAL_SAGA_CHAPTER_RESOLVED_EVENT, FINAL_SAGA_LIFE_AMOUNT_MECHANIC, final_saga_life_drain_effect,
    )
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC

    if binding.event.value == FINAL_SAGA_CHAPTER_RESOLVED_EVENT and re.fullmatch(
        r"Each opponent loses X life and you gain X life, where X is that Saga['’]s mana value[.]?",
        body, re.I,
    ):
        return (
            "saga-mana-value-drain-each-opponent-v1", (final_saga_life_drain_effect(),), None,
            ("cr-119-life", "cr-101-the-magic-golden-rules", SCALAR_AMOUNT_MECHANIC, FINAL_SAGA_LIFE_AMOUNT_MECHANIC),
        ), True

    del card_name, effect_template

    from ..rules.postcombat_life_loss_mana import TEMPLATE as POSTCOMBAT_MANA_TEMPLATE, VARIANT as POSTCOMBAT_MANA_VARIANT
    from ..rules.postcombat_life_loss_mana import effect as postcombat_mana

    if binding.variant == POSTCOMBAT_MANA_VARIANT and body == binding.body:
        from .fixed_resolution_mana import FIXED_RESOLUTION_MANA_MECHANIC

        color = re.search(r"\{([WUBRGC])\}", body, re.I)
        if color is not None:
            return (POSTCOMBAT_MANA_TEMPLATE, (postcombat_mana(color[1].upper()),), None,
                    (FIXED_RESOLUTION_MANA_MECHANIC,)), False
    from ..rules.exile_origin_creature_counters import BODY, TEMPLATE, exile_origin_counter_condition, exile_origin_counter_effect

    if (body.casefold() == BODY and binding.event.value == "spell.cast"
            and binding.active_zone == "battlefield" and binding.event_condition == exile_origin_counter_condition()):
        return (TEMPLATE, (exile_origin_counter_effect(),), None, ("cr-122-counters",)), True
    from ..rules.source_power_opponent_damage import (
        SOURCE_POWER_OPPONENT_DAMAGE_BODY, SOURCE_POWER_OPPONENT_DAMAGE_MECHANIC,
        SOURCE_POWER_OPPONENT_DAMAGE_TEMPLATE, SOURCE_POWER_OPPONENT_DAMAGE_VARIANT, source_power_opponent_damage_effect,
    )
    if (binding.variant == SOURCE_POWER_OPPONENT_DAMAGE_VARIANT and body == binding.body
            and body.casefold() == SOURCE_POWER_OPPONENT_DAMAGE_BODY):
        return (SOURCE_POWER_OPPONENT_DAMAGE_TEMPLATE, (source_power_opponent_damage_effect(),), None,
                (SOURCE_POWER_OPPONENT_DAMAGE_MECHANIC, "cr-120-damage")), True
    if (binding.event.value == "spell.cast" and binding.active_zone == "battlefield"
            and binding.event_condition == {"field": "controller", "op": "ne", "value": "$source.controller"}
            and " ".join(body.split()).casefold() == (
                "you may reveal the top card of your library. if you do, "
                "counter that spell if it has the same mana value as the revealed card."
            )):
        from ..rules.event_outcome_shapes import cast_reveal_counter_effect
        from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_MECHANIC

        return ("optional-reveal-top-and-compare-cast-v1", (cast_reveal_counter_effect(),), None,
                (FIXED_OPTIONAL_EFFECT_MECHANIC,)), False
    if (binding.event.value == "step.begin" and binding.active_zone == "battlefield"
            and re.fullmatch(r"all lands you control phase out\.?", body.strip(), re.I)):
        from ..phasing_model import (
            PHASE_OUT_MECHANIC, controlled_land_set_phase_out_condition, controlled_land_set_phase_out_effect,
        )

        if binding.event_condition == controlled_land_set_phase_out_condition():
            return (("controlled-land-set-phase-out-v1", (controlled_land_set_phase_out_effect(),),
                     None, (PHASE_OUT_MECHANIC,)), False)
    from ..life_change import LIFE_LOSS_EVENT
    loss_body = body.strip().casefold().removesuffix(".")
    if binding.event.value == LIFE_LOSS_EVENT and loss_body == "draw that many cards":
        from ..rules.event_outcome_shapes import (
            CONTROLLER_LIFE_LOSS_DRAW_MECHANIC, controller_life_loss_draw_condition, controller_life_loss_draw_effect,
        )

        if binding.event_condition == controller_life_loss_draw_condition():
            return (
                "controller-life-loss-draw-v1", (controller_life_loss_draw_effect(),), None,
                (CONTROLLER_LIFE_LOSS_DRAW_MECHANIC,),
            ), True
    if binding.event.value == LIFE_LOSS_EVENT and loss_body in (
        "you gain that much life", "that player mills that many cards",
    ):
        from ..rules.event_outcome_shapes import OPPONENT_LIFE_LOSS_MECHANIC, opponent_life_loss_effect

        effect = opponent_life_loss_effect(mill=loss_body == "that player mills that many cards")
        return ("opponent-life-loss-result-v1", (effect,), None, (OPPONENT_LIFE_LOSS_MECHANIC,)), False
    if (
        binding.event.value == "spell.cast"
        and binding.active_zone == "battlefield"
        and binding.event_condition is None
        and re.fullmatch(
            r"Sacrifice this enchantment\. If you do, each of that player['’]s opponents draws three cards\.?",
            " ".join(body.split()), re.I,
        )
    ):
        from ..source_sacrifice_model import SOURCE_SACRIFICE_MECHANIC, cast_participant_sacrifice_draw_effect

        return (
            "cast-participant-source-sacrifice-draw-v1", (cast_participant_sacrifice_draw_effect(),),
            None, (SOURCE_SACRIFICE_MECHANIC, "sacrifice"),
        ), False
    if (
        binding.event.value == "spell.cast"
        and binding.active_zone == "battlefield"
        and binding.event_condition == {"field": "controller", "op": "ne", "value": "$source.controller"}
        and re.fullmatch(
            r"Counter that spell and put a depletion counter on this enchantment\. "
            r"If there are three or more depletion counters on this enchantment, sacrifice it\.?",
            " ".join(body.strip().split()), re.I,
        )
    ):
        from ..rules.event_counter_threshold import source_depletion_counter_effects

        return (
            "opponent-cast-counter-and-source-depletion-v1", source_depletion_counter_effects(),
            None, ("cr-122-counters", "sacrifice"),
        ), False
    if (
        binding.event.value == "spell.cast"
        and binding.active_zone == "battlefield"
        and binding.event_condition in (None, {"field": "controller", "op": "ne", "value": "$source.controller"})
        and re.fullmatch(r"Sacrifice this enchantment and counter that spell\.?", body.strip(), re.I)
    ):
        from ..source_sacrifice_model import SOURCE_SACRIFICE_MECHANIC, fixed_source_sacrifice_effect

        return (
            "cast-event-source-sacrifice-and-counter-v1",
            (fixed_source_sacrifice_effect(), {"op": "counter_stack_target", "stack": "$context.stack"}),
            None,
            (SOURCE_SACRIFICE_MECHANIC, "sacrifice"),
        ), False
    if binding.event.value == "spell.cast" and binding.active_zone == "battlefield":
        from ..rules.event_outcome_shapes import cast_event_counter_birds_effects, noncreature_cast_condition

        if binding.event_condition == noncreature_cast_condition() and re.fullmatch(
            r"Counter that spell\. That player creates X 1/1 white and blue Bird creature tokens with flying, "
            r"where X is the spell['’]s mana value\.?", " ".join(body.strip().split()), re.I,
        ):
            from ..cast_event_amounts import CAST_MANA_VALUE_MECHANIC
            from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC

            return (
                "cast-event-counter-and-mana-value-birds-v1", cast_event_counter_birds_effects(), None,
                (CAST_MANA_VALUE_MECHANIC, SCALAR_AMOUNT_MECHANIC, "cr-111-tokens", "flying"),
            ), False
    source = re.fullmatch(r'Counter it unless you sacrifice (?:a|an) (?P<type>artifact|battle|creature|enchantment|land|planeswalker)\.',body.strip(),re.I)
    if (source is not None and binding.event.value == 'spell.cast'
            and getattr(getattr(binding,'spell_subject',None),'source_spell',False)):
        from ..cast_source_sacrifice_payment import CastSourceSacrificePaymentSpec
        from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
        spec = CastSourceSacrificePaymentSpec(source['type'].casefold())
        return ('fixed-cast-source-sacrifice-or-counter-v1',(spec.effect(),),None,(FIXED_EFFECT_PAYMENT_MECHANIC,)),False
    if binding.event.value == 'spell.cast' and re.fullmatch(r'Counter that spell\.', body.strip(), re.I):
        return ('fixed-cast-event-counter-v1', ({'op':'counter_stack_target','stack':'$context.stack'},), None, ()), False
    if re.fullmatch(
        r"That player loses the game if the Ring has tempted you four or more times this game\. "
        r"Otherwise, the Ring tempts you\.", " ".join(body.split()), re.I,
    ):
        from ..rules.event_player_targets import is_source_combat_damage_player_binding
        from ..rules.source_subtype_progression import RING_DAMAGE_OUTCOME_TEMPLATE, ring_damage_outcome_effect

        if is_source_combat_damage_player_binding(binding.event.value, binding.event_condition):
            return (RING_DAMAGE_OUTCOME_TEMPLATE, (ring_damage_outcome_effect(),), None, ()), False
    if (re.fullmatch(r'That player loses the game\.', body.strip(), re.I)
            and _event_player(binding, 'that player') == '$context.target'):
        return ('fixed-damage-player-game-loss-v1', ({'op':'lose_game','player':'$context.target'},), None, ()), False
    return None
