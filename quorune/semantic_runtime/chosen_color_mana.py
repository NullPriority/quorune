from __future__ import annotations

"""Strict chosen-color descriptors in the shared activated mana registry."""

from dataclasses import dataclass

from ..chosen_color_mana_abilities import ChosenColorActivatedManaAbilitySpec
from ..chosen_color_mana_model import CHOSEN_COLOR_MANA_HANDLER_ID, CHOSEN_COLOR_MANA_CAPABILITY
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class ChosenColorActivatedManaHandler:
    handler_id: str = CHOSEN_COLOR_MANA_HANDLER_ID
    schema_version: int = 1
    family: str = 'ability.activated.mana.source-chosen-color'
    event: str = 'activate'
    rule_references: tuple[str, ...] = ('105.1', '106.1', '109.5', '302.6', '602.2b', '605.1a', '605.3a', '605.3b', '614.12a', '707.2')
    capability_dependencies: tuple[str, ...] = (CHOSEN_COLOR_MANA_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'ability'}, field='chosen-color mana handler')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != self.schema_version or descriptor['event'] != self.event):
            raise SemanticNodeError('Chosen-color mana handler identity changed')
        try:
            return ChosenColorActivatedManaAbilitySpec.from_dict(descriptor['ability'])
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)
