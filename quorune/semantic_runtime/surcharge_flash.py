"""A face-bound timing permission whose price is enforced by casting costs."""
from dataclasses import dataclass
from ..cast_timing import (
    CAST_PERMISSION_EVENT,CastTimingPermission,ManaSurchargeFlashSpec,
    SURCHARGE_FLASH_HANDLER,SURCHARGE_FLASH_CAPABILITY,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class ManaSurchargeFlashPermissionHandler:
    handler_id: str=SURCHARGE_FLASH_HANDLER
    schema_version: int=1
    family: str='permission.cast.mana_surcharge_flash'
    event: str=CAST_PERMISSION_EVENT
    rule_references: tuple[str,...]=('113.6e','601.2b','601.2f','609.4','702.8a')
    capability_dependencies: tuple[str,...]=(SURCHARGE_FLASH_CAPABILITY,)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','surcharge'},field='Mana-surcharge Flash')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=1 or descriptor['event']!=self.event):
            raise SemanticNodeError('Mana-surcharge Flash identity changed')
        try:return ManaSurchargeFlashSpec.from_dict(descriptor['surcharge'])
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):
        self.validate(descriptor)
        return (CastTimingPermission(),)
