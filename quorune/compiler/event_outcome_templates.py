"""Closed event-bound countering and player game-loss instructions."""
from . import regex_working_set as re
from .event_player_results import _event_player


def fixed_event_outcome_template(binding, body, *, card_name, effect_template):
    del card_name, effect_template
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
    if (re.fullmatch(r'That player loses the game\.', body.strip(), re.I)
            and _event_player(binding, 'that player') == '$context.target'):
        return ('fixed-damage-player-game-loss-v1', ({'op':'lose_game','player':'$context.target'},), None, ()), False
    return None
