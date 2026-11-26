"""Closed public choices addressed by the established occurrence-player binder."""
from __future__ import annotations

from copy import deepcopy
from . import regex_working_set as re

from .affected_player_sacrifice_templates import fixed_affected_player_sacrifice_effect_template
from .event_player_results import _event_player, _occurrence_player
from .fixed_entry_return_requirements import FIXED_ENTRY_RETURN_MECHANIC, FixedEntryReturnRequirementSpec
from .spell_cast_predicates import FixedSpellCastController, FixedSpellCastQuality, FixedSpellCastSubject
from ..object_predicate import ObjectQuerySpec
from ..spell_caster_sacrifice_payment import (
    SPELL_CASTER_SACRIFICE_PAYMENT_MECHANICS, spell_caster_sacrifice_cast_condition_is_closed,
    spell_caster_sacrifice_payment_effect,
)


EVENT_PLAYER_CHOICE_CAPABILITY = 'resolution.event_player.fixed_sacrifice'
EVENT_PLAYER_CHOICE_MECHANIC = 'fixed-event-player-sacrifice'

SPELL_CASTER_LAND_RETURN_MECHANIC = "spell-caster-land-return"
_SPELL_CASTER_LAND_RETURN = FixedEntryReturnRequirementSpec(
    count=1, predicate=ObjectQuerySpec(zones=("battlefield",), types_all=("land",)),
    excludes_source=False, sacrifice_source_unless_paid=False,
)
_UNQUALIFIED_CAST = FixedSpellCastSubject(FixedSpellCastController.ANY, FixedSpellCastQuality.ANY)


def spell_caster_land_return_effect():
    return {**_SPELL_CASTER_LAND_RETURN.effects[0], "players": ["$context.player"]}


def _spell_caster_land_return_result(binding, body):
    if (
        (binding.event.value, binding.active_zone) != ("spell.cast", "battlefield")
        or binding.spell_subject != _UNQUALIFIED_CAST or binding.event_condition is not None
        or body != binding.body
        or re.fullmatch(r"That player returns a land they control to its owner['’]s hand[.]?", body, re.I) is None
    ):
        return None
    return (
        "fixed-spell-caster-land-return-v1", (spell_caster_land_return_effect(),), None,
        (FIXED_ENTRY_RETURN_MECHANIC, SPELL_CASTER_LAND_RETURN_MECHANIC),
    )

def _spell_caster_sacrifice_payment_result(binding, body):
    if (
        (binding.event.value, binding.active_zone) != ("spell.cast", "battlefield")
        or binding.spell_subject is None
        or not spell_caster_sacrifice_cast_condition_is_closed(binding.event_condition)
        or body != binding.body
        or re.fullmatch(r"That player sacrifices a permanent of their choice unless they pay \{1\}[.]?", body, re.I) is None
    ):
        return None
    return (
        "fixed-spell-caster-sacrifice-payment-v14", (spell_caster_sacrifice_payment_effect(),),
        None, SPELL_CASTER_SACRIFICE_PAYMENT_MECHANICS,
    )


def _sacrifice_clause(body):
    return re.fullmatch(r"(?P<subject>that player|they|defending player|that creature['’]s controller) "
                        r"sacrifices? (?P<body>.+)", body.strip(), re.I)


def _compiled_sacrifice(match, player):
    if player is None or player == '$controller':
        return None
    template = fixed_affected_player_sacrifice_effect_template('Target player sacrifices ' + match['body'])
    if template is None:
        return None
    effect = deepcopy(dict(template.effects[0]))
    if effect.get('players') != ['$target.0'] or effect.pop('target', None) != '$target.0':
        return None
    effect['players'] = [player]
    return ('fixed-event-player-sacrifice-v1', (effect,), None,
            tuple(dict.fromkeys((*(mechanic for mechanic in template.mechanics if mechanic != 'cr-115-targets'),
                                 EVENT_PLAYER_CHOICE_MECHANIC))))


def _entry_player_counter_payment_result(binding, body):
    if binding.variant not in {"putting_player_forest", "putting_player_swamp"}:
        return None
    if re.fullmatch(
        r"This enchantment deals 3 damage to that player unless "
        r"(?:the player puts|they put) a -1/-1 counter on a creature they control\.",
        body, re.I,
    ) is None:
        return (None, (), None, ()), False
    from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
    from ..rules.entry_player_counter_payment import (
        ENTRY_PLAYER_COUNTER_PAYMENT_MECHANIC, entry_player_counter_payment_effect,
    )

    return (
        "entry-player-counter-payment-or-damage-v1", (entry_player_counter_payment_effect(),), None,
        (FIXED_EFFECT_PAYMENT_MECHANIC, ENTRY_PLAYER_COUNTER_PAYMENT_MECHANIC),
    ), True


def _opponent_caster_discard_payment_result(binding, body):
    from ..rules.opponent_caster_discard_payment import (
        CASTER_DISCARD_PAYMENT_MECHANICS, caster_discard_payment_effect, opponent_cast_discard_condition,
    )

    if ((binding.event.value, binding.active_zone) != ("spell.cast", "battlefield")
            or body != binding.body or not opponent_cast_discard_condition(binding.event_condition)):
        return None
    match = re.fullmatch(r"That player loses ([1-9][0-9]*) life unless they discard a card[.]", body, re.I)
    if match is None:
        return None
    return ("opponent-caster-discard-or-life-v1", (caster_discard_payment_effect(int(match[1])),),
            None, CASTER_DISCARD_PAYMENT_MECHANICS)


def fixed_event_player_choice_result(binding, body):
    from ..rules.opponent_damage_source_sacrifice import (
        OPPONENT_DAMAGE_SOURCE_SACRIFICE_MECHANICS, OPPONENT_DAMAGE_SOURCE_SACRIFICE_VARIANT,
        opponent_damage_source_sacrifice_effect,
    )

    if binding.variant == OPPONENT_DAMAGE_SOURCE_SACRIFICE_VARIANT and body == binding.body:
        return ("fixed-opponent-damage-source-sacrifice-v1", (opponent_damage_source_sacrifice_effect(),),
                None, OPPONENT_DAMAGE_SOURCE_SACRIFICE_MECHANICS), False
    entry_payment = _entry_player_counter_payment_result(binding, body)
    if entry_payment is not None:
        return entry_payment
    caster_choice = (
        _opponent_caster_discard_payment_result(binding, body)
        or _spell_caster_land_return_result(binding, body)
        or _spell_caster_sacrifice_payment_result(binding, body)
    )
    if caster_choice is not None:
        return caster_choice, False
    match = _sacrifice_clause(body)
    if match is None:
        return None
    compiled = _compiled_sacrifice(match, _event_player(binding, match['subject'].casefold()))
    return (compiled, False) if compiled is not None else None


def source_death_player_sacrifice_result(body, *, event_phrase):
    """Adapt the existing source-self death owner to the same LKI player table."""
    match = _sacrifice_clause(body)
    if event_phrase != 'dies' or match is None:
        return None
    return _compiled_sacrifice(match, _occurrence_player(
        'creature.dies', None, 'source_dies', match['subject'].casefold()))


def attacked_defender_exile_result(binding, body):
    if (
        (binding.event.value, binding.variant, binding.active_zone)
        != ("creature.attacks", "source_attacks", "battlefield")
        or binding.event_condition != {"field": "card", "op": "eq", "value": "$source.ref"}
        or body != binding.body
        or re.fullmatch(r"Defending player exiles two permanents they control[.]?", body, re.I) is None
    ):
        return None
    from ..rules.event_player_choice_shapes import attacked_defender_exile_effect

    return (
        "fixed-attacked-defender-battlefield-exile-v1", (attacked_defender_exile_effect(),), None,
        (),
    )
