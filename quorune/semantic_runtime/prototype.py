from __future__ import annotations

"""Prototype marker in the existing typed ability component registry."""

from dataclasses import dataclass
from ..prototype_model import PrototypeSpec,PROTOTYPE_HANDLER,PROTOTYPE_CAPABILITY
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class PrototypeAbilityHandler:
    handler_id: str=PROTOTYPE_HANDLER
    schema_version: int=1
    family: str='ability.static.prototype'
    event: str='characteristics.evaluate'
    rule_references: tuple[str,...]=('702.160a','718.2','718.3','718.4','718.5')
    capability_dependencies: tuple[str,...]=(PROTOTYPE_CAPABILITY,)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','prototype',REQUIRES_COMPLETE_CARD_PROGRAM_FIELD},field='Prototype handler')
        if descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int or descriptor['schema_version']!=1 or descriptor['event']!=self.event or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True:
            raise SemanticNodeError('Prototype handler identity is malformed')
        try:return PrototypeSpec.from_dict(descriptor['prototype'])
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):
        self.validate(descriptor)
        return ()
