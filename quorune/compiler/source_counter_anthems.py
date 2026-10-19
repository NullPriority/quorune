from __future__ import annotations

"""Named source-counter scaling over an independently closed live anthem."""

import re
from typing import Any, Callable

from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantityReduction
from ..counter_snapshot import COUNTER_TOTAL_CAPABILITY
from .query_characteristic_templates import query_characteristic_quantity


def source_counter_anthem_handler(
    text: str, *, source_name: str, compile_fixed: Callable[[str], Any]
) -> Any:
    scaled = re.fullmatch(
        r"(?P<subject>.+?) gets? (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) "
        r"for each (?P<quantity>.+?)\.?", text, re.IGNORECASE,
    )
    if scaled is None or re.search(
        r" counters? on (?:it|him|her|them)\.?$", scaled['quantity'], re.IGNORECASE
    ):
        return None
    quantity = query_characteristic_quantity(scaled['quantity'], source_name=source_name)
    if quantity is None or quantity.scope is not CharacteristicQuantityScope.SOURCE_COUNTER:
        return None
    base = compile_fixed(f"{scaled['subject']} get {scaled['power']}/{scaled['toughness']}.")
    if base is None:
        return None
    template, raw, capabilities = base
    if quantity.reduction is CharacteristicQuantityReduction.TOTAL_COUNTERS:
        handler = {**raw, 'handler_id': 'continuous.anthem.source-counter-total-query.v4', 'schema_version': 4,
            'modifier': {**raw['modifier'], 'source_counter_total': True}}
        return template + '-source-counter-total-v4', handler, tuple(sorted({
            *((capabilities,) if isinstance(capabilities, str) else capabilities), COUNTER_TOTAL_CAPABILITY}))

    handler = {**raw, 'handler_id': 'continuous.anthem.source-counter-query.v3', 'schema_version': 3,
               'modifier': {**raw['modifier'], 'source_counter': quantity.counter_name}}
    return template + '-source-counter-v3', handler, capabilities
