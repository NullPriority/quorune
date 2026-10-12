from __future__ import annotations

from dataclasses import dataclass
from ..exert_model import ExertAttackSpec, EXERT_ATTACK_CAPABILITY, EXERT_ATTACK_HANDLER
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class ExertAttackFragmentHandler:
    handler_id: str = EXERT_ATTACK_HANDLER
    schema_version: int = 1
    family: str = 'ability.static.exert_attack'
    event: str = 'characteristics.evaluate'
    rule_references: tuple[str,...] = ('701.43a','701.43b','701.43c','701.43d','508.1g','607.2h')
    capability_dependencies: tuple[str,...] = (EXERT_ATTACK_CAPABILITY,)

    def validate(self,value):
        exact_fields(value,{'handler_id','schema_version','event','fragment'},field='Exert attack')
        if (value['handler_id'] != self.handler_id or type(value['schema_version']) is not int
                or value['schema_version'] != 1 or value['event'] != self.event):
            raise SemanticNodeError('Exert attack identity is malformed')
        from ..ability_fragments import ability_fragment_from_dict
        spec=ability_fragment_from_dict(value['fragment'])
        if not isinstance(spec,ExertAttackSpec):
            raise SemanticNodeError('Exert attack requires its linked typed fragment')
        return spec

    def lower(self,value,context):
        return (self.validate(value),)
