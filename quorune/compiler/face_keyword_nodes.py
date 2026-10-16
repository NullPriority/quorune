from __future__ import annotations

"""Keywords whose complete production depends on the printed current face."""

from .ascend_nodes import ascend_keyword_node
from .day_night_nodes import day_night_keyword_node


def face_keyword_node(**values):
    if values['mechanics']==('ascend',):return ascend_keyword_node(**values)
    return day_night_keyword_node(**values)
