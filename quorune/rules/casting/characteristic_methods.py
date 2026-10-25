from __future__ import annotations

"""Shared prospective and committed characteristics for typed casting methods."""

from ..prototype import prototype_cast_view, commit_prototype_cast
from ..fuse import fuse_cast_view, commit_fuse_cast
from ..fuse_model import FuseSpec, FUSE_ANNOTATION


def characteristic_cast_view(host, card, record, method):
    card, record = prototype_cast_view(host, card, record, method)
    return fuse_cast_view(host, card, record, method)


def characteristic_program_key(record, card, method, ordinary_key):
    if method == 'fuse':
        return f'{record.oracle_id}:{FuseSpec.from_dict(card.annotations[FUSE_ANNOTATION]).ability_id}'
    return ordinary_key


def characteristic_offer_fields(record, method):
    return {'cost': record.mana_cost, 'cast_method': method} if method in {'prototype', 'fuse'} else {}


def commit_cast_characteristics(card, details):
    commit_prototype_cast(card, details.get('prototype'))
    commit_fuse_cast(card, details.get('fuse'))


def characteristic_stack_context(details):
    method = details.get('cast_method')
    return {'cast_method': method, method: details[method]} if method in {'prototype', 'fuse'} else {}
