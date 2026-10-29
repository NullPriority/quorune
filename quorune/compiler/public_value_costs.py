from __future__ import annotations

"""Closed current-provider power grammar shared by cast and activation costs."""

import re

from ..rules.source_references import SourceReferenceSpec
from ..self_cast_reductions import CastReductionMetric, CastReductionMetricKind


def source_power_cost_metric(text, *, source_name):
    match = re.fullmatch(r"(?P<source>.+?)['’]s power", text, re.I)
    if match is None:
        return None
    subject = match['source']
    if subject.casefold() not in {'this creature', 'this permanent'} and not SourceReferenceSpec(source_name).matches(subject):
        return None
    return CastReductionMetric(CastReductionMetricKind.SOURCE_POWER, schema_version=4)
