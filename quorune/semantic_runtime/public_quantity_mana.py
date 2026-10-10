from __future__ import annotations

"""Strict public-quantity descriptors in the existing activated mana registry."""

from dataclasses import dataclass
from ..public_quantity_mana_abilities import PublicQuantityActivatedManaAbilitySpec, PUBLIC_QUANTITY_MANA_HANDLER_ID
from ..public_quantity_mana_model import PUBLIC_QUANTITY_MANA_CAPABILITY
from .context import SemanticNodeError
from .component_registry import exact_fields


@dataclass(frozen=True,slots=True)
class PublicQuantityActivatedManaHandler:
    handler_id: str=PUBLIC_QUANTITY_MANA_HANDLER_ID
    schema_version: int=1
    family: str='ability.activated.mana.public-quantity'
    event: str='activate'
    rule_references: tuple[str,...]=('605.1a','605.2','605.3b','107.1b','608.2h')
    capability_dependencies: tuple[str,...]=(PUBLIC_QUANTITY_MANA_CAPABILITY,)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','ability'},field='public quantity mana handler')
        if descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int or descriptor['schema_version']!=1 or descriptor['event']!=self.event:
            raise SemanticNodeError('Public quantity mana handler identity/version mismatch')
        try:
            return PublicQuantityActivatedManaAbilitySpec.from_dict(descriptor['ability'])
        except (TypeError,ValueError,KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):
        del context
        return (self.validate(descriptor),)
