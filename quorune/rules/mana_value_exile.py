"""Closed new versions of total-mana-value exile instruction and progress."""
from collections.abc import Mapping
from ..mana_value_exile_model import TOTAL_MANA_EXILE_SCOPE, ManaValueExileProgress
from .library_selection import LibrarySelectionObjectIdentity

TOTAL_MANA_INSTRUCTION_VERSION=4
TOTAL_MANA_STEP_VERSION=5


def validate_total_mana_instruction(effect, *, resolved=False):
    from ..fixed_top_exile import FIXED_TOP_EXILE_OPERATION, _seat
    if (not isinstance(effect,Mapping)
            or set(effect)!={'op','schema_version','player','scope','minimum_total'}
            or effect['op']!=FIXED_TOP_EXILE_OPERATION
            or type(effect['schema_version']) is not int or effect['schema_version']!=TOTAL_MANA_INSTRUCTION_VERSION
            or effect['scope']!=TOTAL_MANA_EXILE_SCOPE
            or not (_seat(effect['player']) if resolved else effect['player']=='$controller')
            or type(effect['minimum_total']) is not int or effect['minimum_total']<=0):
        raise ValueError('Total mana-value exile requires its explicit closed version-four instruction')
    return TOTAL_MANA_EXILE_SCOPE


def validate_total_mana_step(effect):
    from ..fixed_top_exile import FIXED_TOP_EXILE_OPERATION
    from ..discover_model import discover_zone_counter
    if (not isinstance(effect,Mapping)
            or set(effect)!={'op','schema_version','player','scope','instruction','progress','pending'}
            or effect['op']!=FIXED_TOP_EXILE_OPERATION
            or type(effect['schema_version']) is not int or effect['schema_version']!=TOTAL_MANA_STEP_VERSION
            or effect['scope']!=TOTAL_MANA_EXILE_SCOPE):
        raise ValueError('Total mana-value exile step requires its closed version-five continuation')
    validate_total_mana_instruction(effect['instruction'],resolved=True)
    progress=ManaValueExileProgress.from_dict(effect['progress'])
    pending=LibrarySelectionObjectIdentity.from_dict(effect['pending'])
    discover_zone_counter(pending)
    if (effect['player']!=effect['instruction']['player']
            or progress.minimum_total!=effect['instruction']['minimum_total']
            or progress.complete or any(c.identity.object_id==pending.object_id for c in progress.cards)):
        raise ValueError('Total mana-value exile step lost its original controller, threshold or prefix')
    return progress,pending
