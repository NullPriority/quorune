"""Validate the explicit source-owned block-as-though fragment discriminator."""
from ..block_as_though_model import (
    BLOCK_AS_THOUGH_CAPABILITY,BLOCK_AS_THOUGH_HANDLER,BLOCK_AS_THOUGH_KIND,BlockAsThoughPermission,
)
from .component_registry import exact_fields
from .context import SemanticNodeError
from dataclasses import dataclass


@dataclass(frozen=True,slots=True)
class BlockAsThoughFragmentHandler:
    handler_id: str = BLOCK_AS_THOUGH_HANDLER
    schema_version: int = 1
    family: str = 'ability.static.bounded_block_as_though'
    event: str = 'characteristics.evaluate'
    rule_references: tuple[str,...] = ('509.1a','509.1b','609.4','609.4a','702.28b','702.9b')
    capability_dependencies: tuple[str,...] = (BLOCK_AS_THOUGH_CAPABILITY,)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','fragment'},field='block as-though')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=1 or descriptor['event']!=self.event):
            raise SemanticNodeError('Block as-though handler identity or schema changed')
        fragment=descriptor['fragment'];exact_fields(fragment,{'kind','value'},field='block as-though fragment')
        if fragment['kind']!=BLOCK_AS_THOUGH_KIND:raise SemanticNodeError('Block as-though fragment kind changed')
        try:return BlockAsThoughPermission.from_dict(fragment['value'])
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):
        del context
        return (self.validate(descriptor),)


def source_owned_blocking_fragment_handlers():
    """Compose the existing and new source-owned blocking rule families."""
    from .landwalk_permissions import LandwalkPermissionFragmentHandler
    return (LandwalkPermissionFragmentHandler(),BlockAsThoughFragmentHandler())
