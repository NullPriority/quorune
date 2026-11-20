"""Bind fixed self combat prevention only to the proved original block source."""
from . import regex_working_set as re
from ..source_prevention import SOURCE_SUBJECT_VERSION, SOURCE_COMBAT_DIRECTION


def bound_source_combat_prevention(binding,body):
    events={'this_creature_blocks':'creature.blocks','this_creature_becomes_blocked':'creature.becomes_blocked'}
    if (binding.variant not in events or binding.event.value!=events[binding.variant]
            or binding.active_zone!='battlefield'
            or binding.event_condition!={'field':'card','op':'eq','value':'$source.ref'}):return None
    match=re.fullmatch(r'Prevent all combat damage that would be dealt (?P<both>to and dealt )?by it this turn\.',body,re.I)
    if match is None:return None
    directions=('to','from') if match['both'] else ('from',)
    effects=tuple({'op':'create_damage_prevention_shield','source':'$source','subject':'$source.zone_object',
        'mode':'all','duration':'until_end_of_turn','damage_kind':'combat','application_group_id':'$stack',
        SOURCE_SUBJECT_VERSION:3,SOURCE_COMBAT_DIRECTION:direction} for direction in directions)
    return ('bound-source-block-combat-prevention-v1',effects,None,('cr-615-prevention-effects',))
