"""Recognize the closed redirect-to-source result in a printed ability."""

from __future__ import annotations

from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from ..rules.source_directed_redirect_model import SourceDirectedRedirectSpec
from . import regex_working_set as re


def source_directed_redirect_spec(text: str, *, source_name: str):
    match = re.fullmatch(
        r"(?P<optional>You may )?change a target of target spell or ability to (?P<recipient>[^.]+)\.",
        " ".join(text.split()), re.IGNORECASE,
    )
    if match is None:
        return None
    recipient = match["recipient"]
    if source_self_permanent_type(recipient) != "creature" and not SourceReferenceSpec(source_name).matches(recipient):
        return None
    return SourceDirectedRedirectSpec(optional=bool(match["optional"]))


def source_directed_redirect_template(text, *, source_name, source_is_permanent):
    if source_is_permanent is not True:
        return None
    spec = source_directed_redirect_spec(text, source_name=source_name)
    if spec is None:
        return None
    return "source-directed-redirect-v1", spec.effects, spec.target_schema, ("cr-115-targets",)
