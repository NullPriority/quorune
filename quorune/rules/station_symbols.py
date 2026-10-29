"""Read intrinsic compiled Station-symbol information without Oracle parsing."""
from ..entry_copy import copiable_oracle_id
from ..card_program_faces import program_matches_face
from ..station_symbol_model import STATION_SYMBOL_HANDLER, StationSymbolsSpec


def compiled_station_symbols(host, card):
    if card.face_down:
        return None
    record = host.card_record(card)
    # Read the intrinsic battlefield declaration even while the card is in
    # another zone; this does not execute its continuous effect there.
    for program in host.semantics.runtime_handler_programs_for_oracle(copiable_oracle_id(card), active_zone='battlefield', event='characteristics.evaluate'):
        if (not host.semantic_program_is_current_trusted(program)
                or not program_matches_face(record, program, card)):
            continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') == STATION_SYMBOL_HANDLER:
                return StationSymbolsSpec.from_dict({'symbols': descriptor['symbols']})
    return None
