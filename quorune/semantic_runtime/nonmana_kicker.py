from __future__ import annotations

"""Descriptor validation for optional fixed nonmana Kicker payments."""

from dataclasses import dataclass
from ..nonmana_kicker import FixedNonmanaKickerSpec,NONMANA_KICKER_CAPABILITY,NONMANA_KICKER_HANDLER
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class FixedNonmanaKickerHandler:
    handler_id: str=NONMANA_KICKER_HANDLER
    schema_version: int=1
    family: str='casting.kicker.fixed_nonmana'
    event: str='cast.cost'
    rule_references: tuple[str,...]=('118.8','601.2b','601.2f','601.2h','702.33a','702.33b')
    capability_dependencies: tuple[str,...]=(NONMANA_KICKER_CAPABILITY,)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event',REQUIRES_COMPLETE_CARD_PROGRAM_FIELD,'kicker'},field='nonmana Kicker')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=1 or descriptor['event']!=self.event
                or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Nonmana Kicker identity or admission is malformed')
        try:return FixedNonmanaKickerSpec.from_dict(descriptor['kicker'])
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):return (self.validate(descriptor),)
