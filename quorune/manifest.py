from __future__ import annotations

"""Typed Manifest state and ordinary printed-mana turn-up authority."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .morph import MORPH_FACE_DOWN_ANNOTATION, MORPH_FACE_DOWN_VALUES
from .util import mana_cost_to_vector

MANIFEST_CAPABILITY = 'keyword_action.manifest.card'
MANIFEST_DREAD_CAPABILITY = 'keyword_action.manifest_dread'
MANIFEST_MARKER = 'manifested_card'


@dataclass(frozen=True, slots=True)
class ManifestTurnUpSpec:
    cost_text: str
    requirements_dict: Mapping[str, int]
    method: str = 'manifest'


def manifest_characteristics():
    return dict(MORPH_FACE_DOWN_VALUES)


def prepare_manifest_entry_replacement(host, card, *, controller, characteristics, selections):
    from copy import deepcopy
    from .semantic_runtime.zone_replacements import capture_zone_change_replacement_snapshot, prepare_zone_change_replacement_snapshot
    from .zone_trigger_events import ZoneTransitionKind
    prospective = deepcopy(card)
    prospective.face_down = True
    prospective.annotations[MORPH_FACE_DOWN_ANNOTATION] = manifest_characteristics()
    prospective.annotations[MANIFEST_MARKER] = {'schema_version': 1}
    snapshot = capture_zone_change_replacement_snapshot(host, ((card.object_id, 'battlefield'),),
        destination_controllers={card.object_id: controller}, entry_characteristics={card.object_id: characteristics},
        face_down_entry_objects={card.object_id: prospective}, requested_tapped={card.object_id: False},
        entry_pay_life={card.object_id: False}, transition_kinds={card.object_id: ZoneTransitionKind.ORDINARY})
    return prepare_zone_change_replacement_snapshot(snapshot, selections=selections)[card.object_id]


def is_manifested(card: Any) -> bool:
    return (card.face_down and card.zone == 'battlefield'
        and card.annotations.get(MANIFEST_MARKER) == {'schema_version': 1}
        and card.annotations.get(MORPH_FACE_DOWN_ANNOTATION) == manifest_characteristics())


def manifest_turn_up_spec(host: Any, seat: str, card: Any):
    if not is_manifested(card) or card.controller != seat or card.phased_out or card.object_kind != 'card':
        return None
    record = host.card_record(card)
    if record is None or record.faces or record.layout != 'normal':
        return None
    effective = host._effective_card_data(card, ignore_face_down=True)
    if 'creature' not in host._type_parts(str(effective.get('type_line') or ''))[0]:
        return None
    cost = str(effective.get('mana_cost') or '')
    if not cost or re.fullmatch(r'(?:\{(?:[0-9]+|[WUBRGCX])\})+', cost, re.I) is None:
        return None
    # X in a permanent mana cost is zero outside the stack (CR 107.3g).
    ordinary = cost.replace('{X}', '{0}')
    requirements, complex_symbols = mana_cost_to_vector(ordinary)
    if complex_symbols:
        return None
    from .card_programs import bind_card_program_runtime
    from .rules.capabilities import load_default_capability_registry
    registry = load_default_capability_registry()
    program = host.semantics.card_program_for_oracle(record.oracle_id)
    if program is None:
        return None
    if not bind_card_program_runtime(program, capability_registry=registry,
            profile=host.state.config.review_profile)['strict_capability_ready']:
        return None
    return ManifestTurnUpSpec(cost_text=ordinary, requirements_dict=requirements)


__all__ = ['MANIFEST_CAPABILITY', 'MANIFEST_DREAD_CAPABILITY', 'MANIFEST_MARKER',
    'manifest_characteristics', 'is_manifested', 'manifest_turn_up_spec']
