from __future__ import annotations

"""Current trusted printed face keywords, without runtime Oracle interpretation."""

from ..card_program_faces import program_matches_face
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS


def current_printed_face_keywords(host,card,record,base):
    if card.annotations.get('copy_overrides') is not None or card.is_token or card.object_kind in {'card_copy','spell_copy'}:
        return base.get('keywords',[])
    if record is None or not record.faces or (card.zone not in {'stack','battlefield'} and not card.annotations.get('prospective_cast_face')):
        return base.get('keywords',[])
    current=set()
    known={str(value).casefold() for value in FIXED_CHARACTERISTIC_KEYWORDS}
    for program in host.semantics.programs_for_oracle(record.oracle_id):
        if (program.provenance.get('template_id')=='printed-keyword-list-v1'
                and program_matches_face(record,program,card)
                and host.semantic_program_is_current_trusted(program)):
            current.update(str(value).casefold() for value in program.coverage if str(value).casefold() in known)
    return [keyword for keyword in base.get('keywords',()) if str(keyword).casefold() not in known or str(keyword).casefold() in current]
