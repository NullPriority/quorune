"""Intrinsic Mutate metadata in the existing ability-fragment registry."""
from dataclasses import dataclass
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..mutate_model import MutateSpec,MUTATE_CAPABILITY,MUTATE_HANDLER
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class MutateAbilityHandler:
    handler_id:str=MUTATE_HANDLER
    schema_version:int=1
    family:str='ability.static.mutate'
    event:str='characteristics.evaluate'
    rule_references:tuple[str,...]=('112.2','601.2b','601.2f','702.140a','702.140b','702.140c','702.140d','702.140e','702.140f','730.2')
    capability_dependencies:tuple[str,...]=(MUTATE_CAPABILITY,)

    def validate(self,value):
        exact_fields(value,{'handler_id','schema_version','event','fragment',REQUIRES_COMPLETE_CARD_PROGRAM_FIELD},field='Mutate handler')
        if (value['handler_id']!=self.handler_id or type(value['schema_version']) is not int
                or value['schema_version']!=1 or value['event']!=self.event
                or value[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Mutate handler requires its current complete-card contract')
        from ..ability_fragments import ability_fragment_from_dict
        try:spec=ability_fragment_from_dict(value['fragment'])
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc
        if not isinstance(spec,MutateSpec):raise SemanticNodeError('Mutate requires its own typed cost fragment')
        return spec

    def lower(self,value,context):return (self.validate(value),)
