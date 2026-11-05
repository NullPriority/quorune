"""Same-spell-owner qualification over authoritative ephemeral source facts."""

from collections.abc import Mapping
from ..targets import target_plan

TARGET_SPELL_OWNER_CAPABILITY='target.object.same_spell_owner'


def target_has_spell_owner(source,owner,category):
    """A source controller never substitutes for unavailable ownership."""
    return (category in {'permanent','card','spell'} and source is not None
        and source.kind=='spell' and source.spell_owner is not None and owner==source.spell_owner)


def spell_owner_target_dependencies(schema):
    def uses_owner(value):
        if isinstance(value,Mapping):
            return (value.get('owner',value.get('owner_relation'))=='spell_owner'
                or any(uses_owner(child) for child in value.values()))
        if isinstance(value,(tuple,list)):return any(uses_owner(child) for child in value)
        return False
    if not uses_owner(schema):return ()
    plan=target_plan(schema)
    return (TARGET_SPELL_OWNER_CAPABILITY,) if any(group.owner_relation=='spell_owner' for group in plan.groups) else ()
