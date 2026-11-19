"""Version-four Enchant ownership inferred from the actual typed fragment."""
from collections.abc import Mapping

from ..enchant_spec import TypedEnchantSpec
from ..enchant_public_state_model import (
    ENCHANT_PUBLIC_STATE_CAPABILITY, EnchantPublicStateKind, EnchantPublicStateSpec,
)


def public_state_enchant_spec_dependencies(spec):
    if not isinstance(spec, TypedEnchantSpec) or spec.schema_version != 4:
        return ()
    # Decode through the existing codec so a partial raw object cannot claim
    # this scope by setting only a version or a coverage annotation.
    spec = TypedEnchantSpec.from_dict(spec.to_dict())
    if not isinstance(spec.public_state, EnchantPublicStateSpec):
        raise ValueError('Public-state Enchant requires its complete typed qualification')
    return (ENCHANT_PUBLIC_STATE_CAPABILITY, 'attachment.aura.typed_restriction',
        'target.revalidate_resolution',
        *(('state_query.permanent.public_state_predicate',)
            if spec.public_state.kind is EnchantPublicStateKind.MODIFIED_CREATURE else ()))


def public_state_enchant_descriptor_dependencies(value):
    if not isinstance(value, Mapping) or value.get('kind') != 'enchant_typed':
        return ()
    from ..ability_fragments import ability_fragment_from_dict
    return public_state_enchant_spec_dependencies(ability_fragment_from_dict(value))
