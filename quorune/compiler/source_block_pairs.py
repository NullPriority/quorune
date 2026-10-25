from __future__ import annotations

"""A target blocker and the represented permanent source, with distinct roles."""

from dataclasses import dataclass
import re

from ..source_block_pair_model import SourceBlockPairSpec
from .direct_target import direct_permanent_target_spec


@dataclass(frozen=True, slots=True)
class SourceBlockPairTemplate:
    def compiled(self):
        target = direct_permanent_target_spec('target creature')
        return ('temporary-target-cannot-block-source-eot-v1',
            ({'op': 'grant_declaration_restriction_until_end_of_turn', 'card': '$target.0',
                'restriction': SourceBlockPairSpec().to_dict()},), target.to_target_schema(),
            ('cr-115-targets', 'cr-509-declare-blockers-step', 'cr-611-continuous-effects'))


def source_block_pair_template(text, *, allow_source):
    if allow_source and re.fullmatch(r"Target creature can't block this creature this turn\.", text.strip(), re.I):
        return SourceBlockPairTemplate()
    return None
