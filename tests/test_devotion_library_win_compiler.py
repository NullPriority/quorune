"""Closed source, descriptor, and scope checks for a devotion library result."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
from quorune.compiler.variable_library_selection_templates import devotion_library_win_template
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.library_selection_capability_shapes import (
    fixed_top_random_bottom_selection,
    validate_fixed_top_random_bottom_selection,
)
from quorune.variable_library_selection_model import (
    DEVOTION_LIBRARY_WIN_CAPABILITY,
    DEVOTION_LIBRARY_WIN_TEMPLATE,
    devotion_library_win_effect,
    fixed_selection_for_resolved_count,
    validate_variable_library_selection,
)

BODY = (
    "Look at the top X cards of your library, where X is your devotion to blue. "
    "Put up to one of them on top of your library and the rest on the bottom of your library in a random order. "
    "If X is greater than or equal to the number of cards in your library, you win the game."
)
TEXT = "When this creature enters, " + BODY[0].lower() + BODY[1:]


class DevotionLibraryWinCompilerTests(unittest.TestCase):
    def compile(self, text, registry=None):
        return compile_oracle_card(
            query_record(text),
            capability_registry=load_default_capability_registry() if registry is None else registry,
            capability_profile="commander_review",
        )

    def test_printed_source_has_one_closed_spanned_devotion_program(self):
        result = self.compile(TEXT)
        self.assertEqual("exact", result.status, result.material_residuals)
        (node,) = result.faces[0].nodes
        self.assertEqual(DEVOTION_LIBRARY_WIN_TEMPLATE, node.template_id)
        self.assertEqual(("permanent.enter.self", "battlefield"), (node.event, node.active_zone))
        self.assertEqual(TEXT, TEXT[node.span.start:node.span.end])
        self.assertEqual((devotion_library_win_effect(),), tuple(node.effects))
        self.assertTrue({
            DEVOTION_LIBRARY_WIN_CAPABILITY, "state_query.devotion.current", "library.select.fixed_controller",
            "quantity_expression.public_query_effect_amount", "effect.player.game_win",
        }.issubset(node.capability_dependencies))

    def test_scope_and_changed_clause_keep_material_residuals(self):
        for text in (
            TEXT.replace("this creature enters", "this creature dies"),
            TEXT.replace("this creature enters, ", "this creature enters, if you cast it, "),
            TEXT.replace("your devotion to blue", "your devotion to green"),
            TEXT.replace("greater than or equal to", "greater than"),
            TEXT.replace("up to one", "one"),
            TEXT.replace("in a random order", "in any order"),
            TEXT + " Draw a card.",
        ):
            with self.subTest(text=text):
                self.assertTrue(self.compile(text).material_residuals)
        self.assertIsNotNone(devotion_library_win_template(BODY))

    def test_closed_count_does_not_relax_the_historical_domain_limit(self):
        effect = devotion_library_win_effect(8, player="A")
        fixed = fixed_selection_for_resolved_count(effect)
        self.assertEqual(8, fixed["look_count"])
        validate_fixed_top_random_bottom_selection(fixed, player="A")
        with self.assertRaises(ValueError):
            validate_fixed_top_random_bottom_selection(fixed_top_random_bottom_selection(8, player="A"), player="A")
        self.assertIsNone(fixed_selection_for_resolved_count(devotion_library_win_effect(0, player="A")))
        for other in (
            {**effect, "look_count": True},
            {**effect, "stage": "continuing"},
            {**effect, "player": "$target.0"},
            {**effect, "maximum": 2},
            {**effect, "remainder_order": "chosen"},
            {**devotion_library_win_effect(), "stage": "compare"},
        ):
            with self.subTest(effect=other), self.assertRaises(ValueError):
                validate_variable_library_selection(other, resolved=type(other["look_count"]) is int)

    def test_capability_withdrawal_and_compiler_binding_omission_fail_closed(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        raw["capabilities"] = [row for row in raw["capabilities"] if row["id"] != DEVOTION_LIBRARY_WIN_CAPABILITY]
        denied = self.compile(TEXT, CapabilityRegistry(raw))
        self.assertTrue(denied.material_residuals)
        self.assertTrue(any(DEVOTION_LIBRARY_WIN_CAPABILITY in blocker for row in denied.material_residuals
                            for blocker in row.blockers))
        with patch("quorune.compiler.variable_library_selection_templates.devotion_library_win_template",
                   return_value=None) as omitted:
            self.assertTrue(self.compile(TEXT).material_residuals)
        omitted.assert_called_once()


if __name__ == "__main__":
    unittest.main()
