"""Compile the exact two-target persistent copy instruction."""

from __future__ import annotations

from ..rules.persistent_public_copy import (
    PERSISTENT_PUBLIC_COPY_TEMPLATE, persistent_public_copy_effect, persistent_public_copy_target_schema,
)
from . import regex_working_set as re


def persistent_public_copy_template(text: str):
    if re.fullmatch(
        r"Target artifact or creature becomes a copy of another target artifact or creature\.",
        " ".join(text.split()), re.IGNORECASE,
    ) is None:
        return None
    return (
        PERSISTENT_PUBLIC_COPY_TEMPLATE, (persistent_public_copy_effect(),),
        persistent_public_copy_target_schema(), ("cr-115-targets",),
    )
