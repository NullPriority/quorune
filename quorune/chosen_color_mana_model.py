from __future__ import annotations

"""A fixed mana amount whose color is the source's retained designation."""

from dataclasses import dataclass
from typing import Any, Mapping

from .fixed_mana_abilities import FixedManaMode
from .mana_restrictions import valid_mana_spend_restriction


CHOSEN_COLOR_MANA_KIND = 'source_chosen_color_mana'
CHOSEN_COLOR_MANA_CAPABILITY = 'mana.production.source_chosen_color'
CHOSEN_COLOR_MANA_HANDLER_ID = 'ability.activated.mana.source-chosen-color.v1'


@dataclass(frozen=True, slots=True)
class ChosenColorManaOutput:
    amount: int
    fixed_alternative: FixedManaMode | None = None
    restriction: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported chosen-color mana version')
        if type(self.amount) is not int or self.amount < 1:
            raise ValueError('Chosen-color mana requires a positive fixed amount')
        if self.fixed_alternative is not None and not isinstance(self.fixed_alternative, FixedManaMode):
            raise ValueError('Chosen-color alternative must be a typed fixed mode')
        if self.restriction is not None and not valid_mana_spend_restriction(self.restriction):
            raise ValueError('Chosen-color mana restriction is unsupported')

    def to_dict(self) -> dict[str, Any]:
        return {'kind': CHOSEN_COLOR_MANA_KIND, 'schema_version': self.schema_version,
                'amount': self.amount, 'fixed_alternative': None if self.fixed_alternative is None else self.fixed_alternative.bundle,
                'restriction': self.restriction}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'amount', 'fixed_alternative', 'restriction'}
                or value['kind'] != CHOSEN_COLOR_MANA_KIND):
            raise ValueError('Chosen-color mana fields are missing or unknown')
        alternative = value['fixed_alternative']
        return cls(value['amount'], None if alternative is None else FixedManaMode.from_bundle(alternative),
                   value['restriction'], value['schema_version'])
