from dataclasses import dataclass

from ..replacement.immutable import thaw_value
from ..rules.boast_allowance import CAPABILITY, EVENT, HANDLER_ID, descriptor
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class BoastAllowanceHandler:
    handler_id: str = HANDLER_ID
    schema_version: int = 1
    family: str = "permission.activation.boast_twice"
    event: str = EVENT
    rule_references: tuple[str, ...] = ("602.2", "602.5", "702.142a")
    capability_dependencies: tuple[str, ...] = (CAPABILITY,)

    def validate(self, value):
        if thaw_value(value) != descriptor() or type(value.get("schema_version")) is not int:
            raise SemanticNodeError("Boast allowance has a closed exact descriptor")

    def lower(self, value, context):
        self.validate(value)
        return ()
