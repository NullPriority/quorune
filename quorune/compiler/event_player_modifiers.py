"""One fixed untargeted creature set addressed to a proved attack participant."""
from . import regex_working_set as re
from .event_player_results import _event_player
from ..affected_permanents import defending_player_creature_untap_effect
from ..event_player_modifier_model import EventPlayerModifierSpec,EVENT_PLAYER_SET_MECHANIC,DEFENDING_PLAYER_REFERENCE
from ..rules.event_player_modifiers import event_player_modifier_scope_dependencies


def event_player_modifier_result(binding,body):
    event=getattr(binding.event,'value',binding.event)
    condition=getattr(binding,'event_condition',getattr(binding,'condition',None))
    if not event_player_modifier_scope_dependencies(event,condition):return None
    if _event_player(binding,'defending player')!=DEFENDING_PLAYER_REFERENCE:return None
    material = body.strip()
    if (binding.variant == "source_attacks"
            and material.casefold() == "untap all creatures defending player controls."
            and getattr(binding, "body", "").strip().casefold() == material.casefold()):
        return ("defending-player-creature-untap-v1", (defending_player_creature_untap_effect(),), None,
            ("tap-and-untap", "fixed-public-tap-state-set")), False
    match=re.fullmatch(r'creatures defending player controls get (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) until end of turn\.',material,re.I)
    if match is None:return None
    try:spec=EventPlayerModifierSpec(int(match['power']),int(match['toughness']))
    except ValueError:return None
    return ('event-player-creature-fixed-stats-eot-v1',(spec.to_effect(),),None,
        (EVENT_PLAYER_SET_MECHANIC,'cr-611-continuous-effects')),False
