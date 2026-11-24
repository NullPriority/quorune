"""Compile optional draws bound to represented public occurrences."""
from . import regex_working_set as re

EVENT_OPTIONAL_DRAW_FIELD = 'event_optional_draw_version'
EVENT_OPTIONAL_DRAW_CAPABILITY = 'resolution.event_player.optional_draw'
EVENT_OPTIONAL_DRAW_MECHANIC = 'fixed-event-player-optional-draw'
EVENT_OPTIONAL_DRAW_TEMPLATE = 'fixed-defending-player-optional-draw-v1'


def fixed_event_optional_draw_result(binding, body, *, effect_template, card_name):
    from .event_creature_controller_results import dead_creature_controller_optional_draw_result
    if death := dead_creature_controller_optional_draw_result(binding, body):return death
    from ..rules.event_optional_draw_shapes import (
        DAMAGE_DEALER_DRAW_VARIANT, DAMAGE_DEALER_DRAW_VERSION, damage_dealer_draw_condition,
        cast_opponent_optional_draw_effect,
    )

    if binding.event.value == "spell.cast" and binding.active_zone == "battlefield":
        from ..rules.event_outcome_shapes import noncreature_cast_condition
        if binding.event_condition == noncreature_cast_condition() and re.fullmatch(
            r"each of that player['’]s opponents may draw a card\.?", " ".join(body.split()), re.I,
        ):
            return (("cast-opponents-optional-draw-v1", (cast_opponent_optional_draw_effect(),),
                     None, (EVENT_OPTIONAL_DRAW_MECHANIC,)), False)

    if binding.variant == DAMAGE_DEALER_DRAW_VARIANT:
        if (binding.event.value != "damage.dealt" or binding.active_zone != "battlefield"
                or binding.event_condition != damage_dealer_draw_condition()
                or re.fullmatch(r"its controller may draw a card\.?", body.strip(), re.I) is None):
            return None
        from ..target_controller_information import damage_dealer_controller_reference
        from .event_player_results import EVENT_PLAYER_RESULT_MECHANIC
        from .draw_templates import fixed_draw_effect_template

        _, (effect,), _, mechanics = fixed_draw_effect_template("You may draw a card.")
        controller = damage_dealer_controller_reference()
        effect = {**effect, "player": controller, "drawer": controller, EVENT_OPTIONAL_DRAW_FIELD: DAMAGE_DEALER_DRAW_VERSION}
        return (("damage-dealer-controller-optional-draw-v3", (effect,), None,
                 (*mechanics, EVENT_PLAYER_RESULT_MECHANIC, EVENT_OPTIONAL_DRAW_MECHANIC)), False)
    if binding.event.value != 'creature.attacks' or binding.variant != 'source_attacks':return None
    if re.fullmatch(
        r"Defending player may have you draw a card\. If they do, untap this creature and remove it from combat\.?",
        " ".join(body.split()), re.I,
    ):
        from ..rules.event_optional_draw_shapes import defending_controller_draw_effect

        return (("defender-controller-draw-combat-removal-v1", (defending_controller_draw_effect(),),
                 None, (EVENT_OPTIONAL_DRAW_MECHANIC,)), False)
    match = re.fullmatch(r'defending player may draw (?P<body>.+)', body.strip(), re.I)
    if match is None:return None
    from .event_player_results import _event_player, EVENT_PLAYER_RESULT_MECHANIC
    player = _event_player(binding, 'defending player')
    if player != '$context.defending_player':return None
    from .draw_templates import fixed_draw_effect_template
    compiled = fixed_draw_effect_template('You may draw ' + match['body'])
    if compiled is None:return None
    name, effects, schema, mechanics = compiled
    if name != 'optional-draw-controller-v1' or len(effects) != 1 or schema is not None:return None
    effect, = effects
    if (set(effect) != {'op', 'player', 'drawer', 'count', 'private'}
            or effect.get('op') != 'offer_draw' or effect.get('player') != '$controller'
            or effect.get('drawer') != '$controller' or type(effect.get('count')) is not int
            or effect['count'] < 1 or effect.get('private') is not True):return None
    result = {**effect, 'player': player, 'drawer': player, EVENT_OPTIONAL_DRAW_FIELD: 1}
    return ((EVENT_OPTIONAL_DRAW_TEMPLATE, (result,), None,
        (*mechanics, EVENT_PLAYER_RESULT_MECHANIC, EVENT_OPTIONAL_DRAW_MECHANIC)), False)
