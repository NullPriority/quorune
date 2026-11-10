from __future__ import annotations

"""Read-only prospective face views shared by cast offers and cost legality."""

from dataclasses import replace
from functools import partial
from typing import Any, Mapping, TYPE_CHECKING

from ..action_proposals import CastCostOption

if TYPE_CHECKING:
    from .costs import CastCostHost


def prospective_cast_card(host,card,face_name=None):
    if card.annotations.get('prospective_cast_fuse') is True:return card
    record=host.card_record(card)
    if record is None or not record.faces:return card
    from ...preparation import preparation_cast_face
    face_name=preparation_cast_face(card,record,face_name)
    face=host._select_cast_face(record,face_name)
    return replace(card,active_face=str(face['name']),annotations={**card.annotations,'prospective_cast_face':True})


TYPED_MANA_SPEND_TYPE_LINE_FIELD = "typed_mana_spend_type_line"


def typed_mana_spend_details(host, cast_view, type_line, *, use_selected_type):
    """Carry the funding query's effective spell type to typed payment commit."""
    if use_selected_type:
        return {}
    effective = str(host._effective_card_data(cast_view).get("type_line") or "")
    if not effective or effective == type_line:
        return {}
    from ...mana_spend_rules import MANA_SPEND_RULE_CAPABILITY

    if any(MANA_SPEND_RULE_CAPABILITY in program.capability_dependencies
           for program in host.semantics.programs_for_event("activate")):
        return {TYPED_MANA_SPEND_TYPE_LINE_FIELD: effective}
    return {}


def prospective_cast_record(record,face):
    return replace(record,type_line=str(face.get('type_line') or ''),mana_cost=str(face.get('mana_cost') or '')) if face else record


def current_printed_cast_mana_cost(host,card,record):
    from ..fuse import fuse_characteristic_values
    fused=fuse_characteristic_values(card)
    if fused:return fused['mana_cost']
    from ..prototype import prototype_characteristic_values
    values=prototype_characteristic_values(card)
    if values:return values['mana_cost']
    face=host._select_cast_face(record,card.active_face)
    return str(face.get('mana_cost') or '') if face is not None else record.mana_cost


def printed_phyrexian_cast_options(mana_cost: str, *, x_value: int, commander_tax: int) -> list[dict[str, Any]]:
    """Price printed symbols while retaining the chosen mana and life components."""
    from ...activation_mana_cost import fixed_complex_activation_mana_options
    from ...printed_mana_cost_model import PRINTED_PHYREXIAN_LIFE_FIELD
    from ...util import mana_cost_to_vector

    if type(x_value) is not int or x_value < 0 or type(commander_tax) is not int or commander_tax < 0:
        raise ValueError("Printed Phyrexian cast price requires nonnegative integer X and tax")
    base, symbols = mana_cost_to_vector(mana_cost)
    if not any(symbol.endswith("/P") for symbol in symbols):
        return []
    base["GENERIC"] += x_value * symbols.count("X") + commander_tax
    variants = fixed_complex_activation_mana_options(base, tuple(symbol for symbol in symbols if symbol != "X"))
    if any(variant.snow_payment for variant in variants):
        return []
    return [
        {"id": f"printed-phyrexian-{index}", "kind": "hybrid", "requirements": dict(variant.requirements),
         **({PRINTED_PHYREXIAN_LIFE_FIELD: variant.life_payment} if variant.life_payment else {})}
        for index, variant in enumerate(variants, start=1)
    ]


def cast_resolution_is_trusted(host,record,face,program):
    return host.semantic_program_is_current_trusted(program) if program is not None else host._trusted_generic_spell(prospective_cast_record(record,face))


def selected_cast_program(host,request,record,face,method_spec,card):
    from .proposal import _spell_semantic_key
    from .model import CastProposalError
    key=f'builtin:{request.cast_method}-face-down' if method_spec is not None else _spell_semantic_key(record,face)
    from .characteristic_methods import characteristic_program_key
    key=characteristic_program_key(record,card,request.cast_method,key)
    program=None if method_spec is not None else host.semantics.get(key)
    from ..fuse import fuse_source_cast_is_complete
    if not fuse_source_cast_is_complete(host,card,program):
        raise CastProposalError('This Fuse source has incomplete executable composition',status='unresolved',reason='fuse_source_incomplete')
    if method_spec is None and host.state.config.semantic_policy=='trusted_only' and not cast_resolution_is_trusted(host,record,face,program):
        raise CastProposalError('This selected face lacks a trusted resolution contract',status='unresolved',reason='semantic_policy_requires_trusted')
    return key,program


def offered_cast_methods(host,card):
    from ...morph import FACE_DOWN_CAST_METHODS
    record=host.card_record(card)
    from ...preparation_model import PREPARE_COPY_FIELD
    if PREPARE_COPY_FIELD in card.annotations:
        from ...preparation import preparation_cast_face
        return ((None,preparation_cast_face(card,record)),)
    from ...cast_face_offers import complete_cast_face_offers
    face_layouts={'split','modal_dfc'}|({'adventure'} if complete_cast_face_offers(host.state) else set())
    faces=tuple(str(f['name']) for f in record.faces) if record is not None and record.layout in face_layouts else (None,)
    from .disturb_costs import compiled_disturb_spec
    disturb=compiled_disturb_spec(host,card) if card.zone=='graveyard' else None
    if disturb is not None:faces=(*faces,disturb.back_face)
    from ..prototype import compiled_prototype_spec
    prototype=(('prototype',None),) if compiled_prototype_spec(host,card) is not None else ()
    from ..fuse import compiled_fuse_spec
    fuse=(('fuse',None),) if card.zone=='hand' and compiled_fuse_spec(host,card) is not None else ()
    return tuple((None,name) for name in faces)+prototype+fuse+tuple((method,None) for method in FACE_DOWN_CAST_METHODS)


def candidate_zone_cast_permission(host,seat,card):
    from .disturb_costs import cast_view_zone_permission
    if cast_view_zone_permission(host,seat,card):return True
    return any(cast_view_zone_permission(host,seat,selected_cast_card_view(host,card,name,method)) for method,name in offered_cast_methods(host,card) if method=='prototype' or method is None and name is not None)


def selected_cast_card_view(host,card,face_name=None,cast_method=None):
    from .characteristic_methods import characteristic_cast_view
    view=prospective_cast_card(host,card,face_name)
    return characteristic_cast_view(host,view,host.card_record(view),cast_method)[0]


def _static_library_top_minimum_x(host, seat, card, *, face_down):
    if card.zone != 'library':
        return 0
    permission = host._temporary_play_permission(seat, card)
    if permission is not None and permission.get('allow_spell', True):
        return 0
    from ...semantic_runtime.action_permissions import controller_library_top_spell_minimum_x

    return controller_library_top_spell_minimum_x(host, seat, card, face_down=face_down)


def build_cast_cost_options(
    host: CastCostHost,
    seat: str,
    card: Any,
    program: Any,
    *,
    response: Mapping[str, Any] | None = None,
    hint: bool,
    force_without_mana_cost: bool = False,
    alternative_base: Mapping[str, Any] | None = None,
    cast_type_line: str | None = None,
    suppress_source_costs: bool = False,
    resolving_exile_authority: bool = False,
) -> tuple[CastCostOption, ...]:
    """Offer the selected face's payable choices under its current zone permission."""
    from .costs import _cast_cost_options_for_submission
    from .source_placement import casting_affordability_host

    host = casting_affordability_host(host, card)
    submission = dict(response or {})
    build = partial(_cast_cost_options_for_submission, host, seat, card, program,
        hint=hint, force_without_mana_cost=force_without_mana_cost, alternative_base=alternative_base,
        cast_type_line=cast_type_line, suppress_source_costs=suppress_source_costs,
        resolving_exile_authority=resolving_exile_authority)
    minimum = _static_library_top_minimum_x(host, seat, card, face_down=suppress_source_costs)
    if not (hint and minimum and submission.get('x') is None):
        return build(submission, minimum_library_x=minimum)
    static = build({**submission, 'x': minimum}, minimum_library_x=minimum, source_grant_only=False)
    from .turn_cast_grants import current_turn_cast_grants

    if not current_turn_cast_grants(host, seat, card, cast_type_line=cast_type_line):
        return static
    grants = build(submission, minimum_library_x=minimum, source_grant_only=True)
    return (*static, *grants)


def bind_library_top_cost_option(option, submission, *, has_x, minimum, source_grant_only):
    if minimum == 0:
        return option
    from ..turn_cast_grant_model import TURN_CAST_GRANT_FIELD

    source_grant = TURN_CAST_GRANT_FIELD in option
    if source_grant_only is not None and source_grant != source_grant_only:
        return None
    if source_grant:
        return option
    announced = submission.get('x')
    if (minimum is None or not has_x or option.get('x_value_policy') is not None
            or type(announced) is not int or announced < minimum):
        return None
    return {**option, 'library_top_minimum_x': minimum, 'library_top_announced_x': announced}


def validate_library_top_cast_commit(host, seat, card, option, response, *, face_down=False):
    from ..turn_cast_grant_model import TURN_CAST_GRANT_FIELD

    if card.zone != 'library' or TURN_CAST_GRANT_FIELD in option:
        return
    minimum = _static_library_top_minimum_x(host, seat, card, face_down=face_down)
    if minimum is None:
        raise ValueError('The current library-top permission is unavailable')
    recorded = option.get('library_top_minimum_x', 0)
    if type(recorded) is not int or recorded != minimum:
        raise ValueError('The library-top mana-value requirement changed')
    if minimum:
        announced = response.get('x')
        if type(announced) is not int or announced < minimum or announced != option.get('library_top_announced_x'):
            raise ValueError('The announced X cannot satisfy the library-top permission')


def cast_offer_id(card,record,face,cast_method=None):
    if cast_method:return f'cast-{cast_method}:{card.ref}'
    if face is None or not record.faces or str(face.get('name'))==str(record.faces[0].get('name')):return f'cast:{card.ref}'
    return f"cast:{card.ref}:face:{face['name']}"
