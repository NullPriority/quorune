"""Typed Forecast marker and incarnation-bound upkeep disclosure lifetime."""
from dataclasses import dataclass
from collections.abc import Mapping
from .continuous_effect_model import ContinuousObjectIdentity

FORECAST_CAPABILITY = 'activation.forecast.hand'
FORECAST_REVEAL_FIELD = 'forecast_reveal_v1'
FORECAST_UNCOMPILED = 'forecast-source-hand-reveal-unrepresented'


@dataclass(frozen=True, slots=True)
class ForecastRevealFrame:
    subject: ContinuousObjectIdentity
    owner: str
    turn_sequence: int
    prior_revealed_to: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self):
        if (not isinstance(self.subject, ContinuousObjectIdentity) or type(self.owner) is not str or not self.owner
                or type(self.turn_sequence) is not int or self.turn_sequence < 0 or type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.prior_revealed_to, (list, tuple)) or any(type(v) is not str or not v for v in self.prior_revealed_to)
                or len(set(self.prior_revealed_to)) != len(self.prior_revealed_to)):
            raise ValueError('Forecast reveal lifetime is malformed')
        object.__setattr__(self, 'prior_revealed_to', tuple(self.prior_revealed_to))

    def to_dict(self):
        return {'schema_version': self.schema_version, 'subject': self.subject.to_dict(), 'owner': self.owner,
            'turn_sequence': self.turn_sequence, 'prior_revealed_to': list(self.prior_revealed_to)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'subject', 'owner', 'turn_sequence', 'prior_revealed_to'}:
            raise ValueError('Forecast reveal frame has a closed schema')
        return cls(ContinuousObjectIdentity.from_dict(value['subject']), value['owner'], value['turn_sequence'], value['prior_revealed_to'], value['schema_version'])
