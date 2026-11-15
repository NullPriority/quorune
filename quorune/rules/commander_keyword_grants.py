"""Capability boundary for keyword grants to designated battlefield objects."""

COMMANDER_PERMANENT_QUERY_CAPABILITY = 'state_query.commander.permanent_designation'
COMMANDER_KEYWORD_GRANT_HANDLER = 'continuous.ability.commander-keyword-grant.v1'


def commander_permanent_query_dependencies(value):
    if (value.get('commander') is True and not value.get('types_all')
            and value.get('zones') in (['battlefield'], ('battlefield',))):
        return (COMMANDER_PERMANENT_QUERY_CAPABILITY,)
    return ()
