from __future__ import annotations

"""Ordered temporary player-rule productions with independent typed owners."""

from .temporary_cast_restrictions import temporary_cast_restriction_template
from .library_search_rule_templates import temporary_library_search_prohibition_template


def temporary_player_rule_template(text):
    return temporary_cast_restriction_template(text) or temporary_library_search_prohibition_template(text)
