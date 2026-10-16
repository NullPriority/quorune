from __future__ import annotations

"""Compose existing typed token nodes into closed modal effect branches."""

import re

from .modal_templates import _mode_body
from .typed_token_ability_nodes import typed_token_ability_nodes


class ModalTokenBranchCompiler:
    def __init__(self, *, record, face_id, source_name, material_rows, effect_template,
                 printed_card_types, keywords, trusted_mechanics, capability_registry,
                 capability_profile, compile_inner, trigger_node, grant_effect_templates):
        self.effect_template = effect_template
        self.source_name = source_name
        self.arguments = dict(record=record, face_id=face_id, source_name=source_name,
            effect_template=effect_template, keywords=keywords, printed_card_types=printed_card_types,
            source_attachment_relation=None, trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry, capability_profile=capability_profile,
            compile_inner=compile_inner, trigger_node=trigger_node, grant_effect_templates=grant_effect_templates)
        self.rows = {}
        for row in material_rows[1:]:
            body = _mode_body(row[1])
            if body is None:
                priced = re.fullmatch(r'\+ (?:\{[^}]+\})+ \u2014 (?P<body>.+)', row[1])
                body = priced['body'] if priced is not None else None
            if body is not None:
                # Identical bodies may share the same immutable child definition.
                self.rows.setdefault(body, row)
        self.cache = {}
        self._children = {}

    @property
    def children(self):
        return tuple(sorted(self._children.values(), key=lambda node: (node.span.line, node.node_id)))

    def __call__(self, body):
        if body in self.cache:
            return self.cache[body]
        ordinary = self.effect_template(body, card_name=self.source_name)
        if '"' not in body or 'create' not in body.casefold() or body not in self.rows:
            return ordinary
        row = self.rows[body]
        nodes = typed_token_ability_nodes(node_id=self.arguments['face_id'] + ':modal:' + str(row[2].line),
            line=body, material_line=body, span=row[2], **self.arguments)
        if (nodes is None or nodes[0].kind != 'spell_ability' or nodes[0].event != 'resolve'
                or any(not node.exact or node.residual_ids for node in nodes)):
            self.cache[body] = ordinary
            return ordinary
        parent = nodes[0]
        for child in nodes[1:]:
            self._children[child.node_id] = child
        result = parent.template_id, parent.effects, parent.target_schema, parent.mechanics
        self.cache[body] = result
        return result
