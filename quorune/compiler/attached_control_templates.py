from __future__ import annotations

import re

from ..rules.attached_control import AttachedControlSpec, ATTACHED_CONTROL_CAPABILITY


def attached_control_handler(text: str):
    if re.fullmatch(r'You control enchanted (?:artifact|creature|enchantment|land|permanent)\.?',text.strip(),re.I) is None:
        return None
    return 'static-attached-control-v1',AttachedControlSpec().to_descriptor(),ATTACHED_CONTROL_CAPABILITY
