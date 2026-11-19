from __future__ import annotations

"""Current trusted printed face keywords, without runtime Oracle interpretation."""

from ..card_program_faces import program_matches_face
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from ..landwalk import QUALIFIED_LANDWALK_KEYWORDS
from ..qualified_hexproof import QUALIFIED_HEXPROOF_KEYWORDS

_KNOWN_PRINTED_KEYWORDS = frozenset(str(value).casefold() for value in FIXED_CHARACTERISTIC_KEYWORDS) | {"protection"}
_QUALIFIED_PRINTED_KEYWORDS = frozenset(QUALIFIED_LANDWALK_KEYWORDS) | QUALIFIED_HEXPROOF_KEYWORDS


def current_printed_face_keywords(host,card,record,base):
    if card.annotations.get('copy_overrides') is not None or card.is_token or card.object_kind in {'card_copy','spell_copy'}:
        return base.get('keywords',[])
    if record is None or (record.faces and card.zone not in {'stack','battlefield'} and not card.annotations.get('prospective_cast_face')):
        return base.get('keywords',[])
    current=set()
    qualified=set()
    # The record keyword index combines faces; qualified Protection also needs
    # the current face's trusted printed declaration before retaining its label.
    for program in host.semantics.programs_for_oracle(record.oracle_id):
        if (program.provenance.get('template_id')=='printed-keyword-list-v1'
                and program_matches_face(record,program,card)
                and host.semantic_program_is_current_trusted(program)):
            for value in program.coverage:
                keyword = str(value).casefold()
                if keyword in _KNOWN_PRINTED_KEYWORDS:
                    current.add(keyword)
                if keyword in _QUALIFIED_PRINTED_KEYWORDS:
                    qualified.add(keyword)
    result = ([keyword for keyword in base.get('keywords',())
        if str(keyword).casefold() not in _KNOWN_PRINTED_KEYWORDS or str(keyword).casefold() in current]
        if record.faces else list(base.get('keywords',())))
    present={str(value).casefold() for value in result}
    if qualified.intersection(QUALIFIED_HEXPROOF_KEYWORDS) and 'hexproof' not in current:
        result=[value for value in result if str(value).casefold()!='hexproof']
    if 'flash' in present:
        from ..compiled_cast_timing import conditional_flash_authorship
        conditional,printed=conditional_flash_authorship(host,card)
        if conditional and not printed:
            result=[value for value in result if str(value).casefold()!='flash']
    # Pinned indexes can omit Landwalk variants or collapse qualified Hexproof.
    # Exact trusted declarations retain the actual printed keyword values.
    return result + [keyword.title() for keyword in sorted(qualified) if keyword not in present]
