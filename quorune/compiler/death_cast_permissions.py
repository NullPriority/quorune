"""One complete conditional graveyard permission and its correlated entry rider."""
from . import regex_working_set as re
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..death_cast_permission_model import (DeathCastPermissionSpec,DEATH_CAST_PERMISSION_CAPABILITY,
    DEATH_CAST_PERMISSION_HANDLER)
from .public_history_conditions import qualified_creature_death_exclusion


def qualified_death_cast_template(text):
    match=re.fullmatch(r'You may cast this card from your graveyard if (?P<condition>.+?)\. '
        r'If you do, this creature enters with a \+1/\+1 counter on it\.?',' '.join(text.strip().split()),re.I)
    if match is None:return None
    condition=qualified_creature_death_exclusion(match['condition'])
    if condition is None:return None
    query=condition.history_query
    spec=DeathCastPermissionSpec(excluded_name=query.excluded_name,excluded_subtype=query.excluded_subtype,entry_counters=1)
    return ('qualified-death-source-cast-entry-v1',{'handler_id':DEATH_CAST_PERMISSION_HANDLER,
        'schema_version':1,'event':'cast.zone.permission','source_zone':'graveyard',
        'permission':spec.to_dict(),REQUIRES_COMPLETE_CARD_PROGRAM_FIELD:True},
        (DEATH_CAST_PERMISSION_CAPABILITY,*query.capability_dependencies))
