from __future__ import annotations

"""Live opponent Venture restrictions through effective static components."""

from dataclasses import dataclass
from typing import Mapping

from ..turn_history import ventured_this_turn
from .component_registry import exact_fields
from .context import SemanticNodeError


VENTURE_LIMIT_HANDLER = "ability.static.opponent-venture-limit.v1"
VENTURE_LIMIT_CAPABILITY = "variant.dungeon.venture_restriction"


@dataclass(frozen=True, slots=True)
class VentureLimitHandler:
    handler_id: str = VENTURE_LIMIT_HANDLER
    schema_version: int = 1
    family: str = "ability.static.venture_limit"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("101.2", "611.3b", "701.49a", "701.49d")
    capability_dependencies: tuple[str, ...] = (VENTURE_LIMIT_CAPABILITY,)

    def validate(self, descriptor):
        if not isinstance(descriptor, Mapping):
            raise SemanticNodeError("The Venture limit requires a typed descriptor")
        exact_fields(descriptor, {"handler_id", "schema_version", "event"}, field="Venture limit")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event):
            raise SemanticNodeError("The Venture limit descriptor is malformed")

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()


def current_venture_allowed(host, player: str) -> bool:
    """The first successful Venture in either mode uses this turn's allowance."""
    previous = ventured_this_turn(host.state, player)
    if previous is False:
        return True
    opponents = set(host.active_seats) - {player}
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if source.controller not in opponents or source.phased_out:
            continue
        for key in host._effective_static_component_keys(source):
            program = host.semantics.get(key)
            if (program is not None and any(descriptor.get("handler_id") == VENTURE_LIMIT_HANDLER
                                            for descriptor in program.handlers)
                    and host.semantic_program_is_current_trusted(program)):
                if previous is None:
                    raise SemanticNodeError("The Venture limit requires complete current-turn history")
                return False
    return True
