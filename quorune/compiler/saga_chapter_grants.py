from __future__ import annotations

"""Lower a Saga chapter's fixed, quoted self-grant and its activation."""

from dataclasses import replace

from ..read_ahead import saga_chapter_line
from ..rules.saga_chapter_grants import SAGA_CHAPTER_GRANT_MECHANIC, saga_chapter_grant_effect
from . import regex_working_set as re
from .attached_granted_ability_nodes import attached_granted_ability_plan, compile_early_material_nodes
from .ir_model import SourceSpan
from .saga_chapter_nodes import ordinary_saga_chapter_nodes
from .typed_token_ability_nodes import typed_token_ability_nodes


_SELF_GRANT = re.compile(r'This Saga gains "(?P<activation>[^"\n]+)"\.?', re.IGNORECASE)
_TOKEN_QUOTE = re.compile(r"(?P<activation>.+\btoken with) '(?P<ability>[^'\n]+)'", re.IGNORECASE)


def _saga_token_chapter_plan(chapter, values):
    if len(chapter.chapters) != 1 or not chapter.body.casefold().startswith("create ") or '"' not in chapter.body:
        return None
    nodes = typed_token_ability_nodes(
        record=values["record"], face_id=values["face_id"], node_id=values["node_id"],
        line=values["line"], material_line=chapter.body, span=values["span"],
        source_name=values["source_name"], effect_template=values["effect_template"],
        keywords=values["keywords"], printed_card_types=values["printed_card_types"],
        source_attachment_relation=values["source_attachment_relation"], trusted_mechanics=values["trusted_mechanics"],
        capability_registry=values["capability_registry"], capability_profile=values["capability_profile"],
        compile_inner=values["compile_inner"], trigger_node=values["trigger_node"],
        grant_effect_templates=values["grant_effect_templates"], saga_chapter_body=True,
    )
    if nodes is None:
        return None
    outer, *children = nodes
    return ((outer.template_id, outer.effects, outer.target_schema, outer.mechanics),
            tuple(children), outer.capability_dependencies)


def saga_chapter_grant(
    body, *, record, face_id, node_id, line, span, type_line, printed_card_types,
    compile_inner, trigger_node, grant_effect_templates, trusted_mechanics,
    capability_registry, capability_profile,
):
    match = _SELF_GRANT.fullmatch(body)
    if match is None:
        return None
    printed_activation = match["activation"]
    activation = printed_activation
    token_quote = _TOKEN_QUOTE.fullmatch(activation)
    if token_quote is not None:
        activation = f'{token_quote["activation"]} "{token_quote["ability"]}".'
    if re.fullmatch(r'(?:\{[1-9]\d*\}, )?\{T\}: .+', activation) is None:
        return None

    offset = line.find(printed_activation)
    if offset < 0:
        return None
    child_start = span.start + offset
    child_span = SourceSpan(start=child_start, end=child_start + len(printed_activation), line=span.line)
    child_id = node_id + ":granted"
    effect_template, trigger_effect_template = grant_effect_templates(True, printed_card_types, None)
    if token_quote is None:
        residuals = []
        activation_node = compile_inner(
            node_id=child_id, line=activation, material_line=activation, span=child_span,
            card_name=record.name, type_line=type_line, keywords=(),
            trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
            capability_profile=capability_profile, residuals=residuals,
            effect_template=effect_template, trigger_effect_template=trigger_effect_template,
        )
        if activation_node is None or residuals:
            return None
        token_children = ()
    else:
        nodes = typed_token_ability_nodes(
            record=record, face_id=face_id, node_id=child_id, line=printed_activation,
            material_line=activation, span=child_span, source_name=record.name,
            effect_template=effect_template, keywords=(), printed_card_types=printed_card_types,
            source_attachment_relation=None, trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry, capability_profile=capability_profile,
            compile_inner=compile_inner, trigger_node=trigger_node, grant_effect_templates=grant_effect_templates,
        )
        if nodes is None:
            return None
        activation_node, *token_children = nodes

    plan = attached_granted_ability_plan(
        node=activation_node, quoted_text=activation, oracle_id=record.oracle_id,
        face_id=face_id, source_line=span.line, card_name=record.name, keywords=(),
    )
    if plan is None:
        return None
    fragment = plan.fragment
    if token_quote is not None:
        fragment = {
            **fragment,
            "value": {**fragment["value"], "effect_text": printed_activation.partition(": ")[2]},
        }
    try:
        effect = saga_chapter_grant_effect(fragment)
    except ValueError:
        return None
    child = replace(activation_node, kind=plan.node_kind, text=printed_activation, active_zone="battlefield")
    return (
        ("fixed-saga-chapter-activation-grant-v1", (effect,), None, (SAGA_CHAPTER_GRANT_MECHANIC,)),
        (child, *token_children),
        activation_node.capability_dependencies,
    )


_LIBRARY_EXILE_CHAPTERS = (
    r"Search your library for a card, exile it face down, then shuffle[.]",
    r"Turn the exiled card face up[.] If it['’]s a creature card, you lose life equal to its mana value[.]",
    r"You may put the exiled card onto the battlefield if it['’]s a creature card[.] "
    r"If you don['’]t put it onto the battlefield, put it into its owner['’]s hand[.]",
)


def _linked_library_exile_chapter_plan(record, chapter):
    from ..linked_library_imprint_model import LINKED_SAGA_LIBRARY_MECHANIC, linked_saga_library_chapter_effects

    if record.layout not in {"normal", "saga"} or record.faces or len(chapter.chapters) != 1:
        return None
    rows = tuple(parsed for line in record.oracle_text.splitlines() if (parsed := saga_chapter_line(line)) is not None)
    if len(rows) != 3 or any(
        row.chapters != (number,) or re.fullmatch(pattern, row.body, re.I) is None
        for number, (pattern, row) in enumerate(zip(_LIBRARY_EXILE_CHAPTERS, rows), 1)
    ):
        return None
    number, = chapter.chapters
    return (("linked-saga-library-chapter-v1", linked_saga_library_chapter_effects(number), None,
             (LINKED_SAGA_LIBRARY_MECHANIC,)), (), ())


def compile_early_material_nodes_with_saga_grants(*, type_line: str, **values):
    """Route printed Saga self-grants through their bounded source compiler."""
    chapter = saga_chapter_line(values["material_line"]) if values["saga_chapters"] else None
    if (
        chapter is None
        or "saga" not in values["printed_subtypes"]
        or not set(chapter.chapters).issubset(values["saga_chapters"])
    ):
        return compile_early_material_nodes(**values)
    grant = _linked_library_exile_chapter_plan(values["record"], chapter) or saga_chapter_grant(
        chapter.body, record=values["record"], face_id=values["face_id"],
        node_id=values["node_id"], line=values["line"], span=values["span"], type_line=type_line,
        printed_card_types=values["printed_card_types"], compile_inner=values["compile_inner"],
        trigger_node=values["trigger_node"], grant_effect_templates=values["grant_effect_templates"],
        trusted_mechanics=values["trusted_mechanics"], capability_registry=values["capability_registry"],
        capability_profile=values["capability_profile"],
    )
    if grant is None:
        grant = _saga_token_chapter_plan(chapter, values)
    if grant is None:
        return compile_early_material_nodes(**values)
    return ordinary_saga_chapter_nodes(
        node_id=values["node_id"], line=values["line"], material_line=values["material_line"],
        span=values["span"], card_name=values["source_name"], printed_subtypes=values["printed_subtypes"],
        declared_chapters=values["saga_chapters"], trusted_mechanics=values["trusted_mechanics"],
        capability_registry=values["capability_registry"], capability_profile=values["capability_profile"],
        residuals=values["residuals"], effect_template=values["effect_template"], grant=grant,
    )
