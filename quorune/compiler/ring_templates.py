"""Closed compiler grammar for one mandatory controller Ring temptation."""
from __future__ import annotations

from dataclasses import dataclass
import re

from ..ring_model import RING_MECHANIC, RING_TEMPTATION_OPERATION


@dataclass(frozen=True, slots=True)
class FixedRingTemptationTemplate:
    template_id: str = 'ring-temptation-controller-v1'

    def compiled(self):
        return (self.template_id,
                ({'op': RING_TEMPTATION_OPERATION, 'player': '$controller'},),
                None, (RING_MECHANIC,))


def fixed_ring_temptation_template(text: str) -> FixedRingTemptationTemplate | None:
    if re.fullmatch(r'the ring tempts you\.?', text.strip(), re.IGNORECASE) is None:
        return None
    return FixedRingTemptationTemplate()
