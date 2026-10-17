from __future__ import annotations

"""Closed vocabulary for a current source's own graveyard redirection."""

from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD


SELF_GRAVEYARD_EXILE_CAPABILITY = 'zone.change.self_graveyard_exile'
SELF_GRAVEYARD_EXILE_HANDLER = 'replacement.zone.self-graveyard-exile.v1'


def self_graveyard_exile_descriptor():
    return {'handler_id': SELF_GRAVEYARD_EXILE_HANDLER, 'schema_version': 1,
        'event': 'zone.change', REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True}
