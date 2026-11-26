"""Whole-face admission for an Aura with a linked graveyard return."""

from dataclasses import replace
import json
import unittest

from common import ROOT
from test_qualified_zone_event_queries import query_record
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.linked_graveyard_aura import (
    LINKED_GRAVEYARD_AURA_CAPABILITY, LINKED_GRAVEYARD_AURA_ENTRY_TEMPLATE, LINKED_GRAVEYARD_AURA_SPELL_TEMPLATE,
    linked_graveyard_aura_entry_effect, linked_graveyard_aura_prepare_effect,
    linked_graveyard_aura_spell_handler, linked_graveyard_aura_spell_target,
)
from scripts.update_capability_evidence import build_index, discover_tests


TEXT = (
    "Enchant creature card in a graveyard\n"
    "When this Aura enters, if it's on the battlefield, it loses "
    '"enchant creature card in a graveyard" and gains "enchant creature put onto the battlefield with this Aura." '
    "Return enchanted creature card to the battlefield under your control and attach this Aura to it. "
    "When this Aura leaves the battlefield, that creature's controller sacrifices it.\n"
    "Enchanted creature gets -1/-0."
)
PROFILE = "commander_review"


class LinkedGraveyardAuraCompilerTests(unittest.TestCase):
    def card(self, text=TEXT):
        return replace(query_record(text), type_line="Enchantment — Aura", keywords=("Enchant",),
                       mana_cost="{1}{B}", power=None, toughness=None)

    def compile(self, card, registry=None):
        return compile_oracle_card(card, capability_registry=registry or load_default_capability_registry(),
                                   capability_profile=PROFILE)

    def test_source_spans_keep_the_original_static_properties_and_linked_carriers(self):
        ir = self.compile(self.card())
        self.assertEqual("exact", ir.status, ir.material_residuals)
        ordinary_enchant, cast, enters, modifier = ir.faces[0].nodes
        self.assertEqual(("continuous", 1), (ordinary_enchant.event, ordinary_enchant.span.line))
        self.assertEqual(("characteristics.evaluate", 3), (modifier.event, modifier.span.line))
        self.assertEqual((LINKED_GRAVEYARD_AURA_SPELL_TEMPLATE, 1), (cast.template_id, cast.span.line))
        self.assertEqual((LINKED_GRAVEYARD_AURA_ENTRY_TEMPLATE, 2), (enters.template_id, enters.span.line))
        self.assertEqual(linked_graveyard_aura_spell_target(), cast.target_schema)
        self.assertEqual((linked_graveyard_aura_prepare_effect(),), tuple(cast.effects))
        self.assertEqual((linked_graveyard_aura_spell_handler(),), tuple(cast.handlers))
        self.assertEqual((linked_graveyard_aura_entry_effect(),), tuple(enters.effects))
        self.assertIsNone(enters.target_schema)
        for changed in (
            TEXT.replace("Enchant creature card in a graveyard\n", "Enchant creature you control\n"),
            TEXT.replace("under your control", "under its owner's control"),
            TEXT.replace("if it's on the battlefield, ", ""),
            TEXT.replace("controller sacrifices it", "controller exiles it"),
            TEXT + "\nThis Aura has flying.",
        ):
            with self.subTest(text=changed):
                rejected = self.compile(self.card(changed))
                self.assertFalse(any(node.template_id == LINKED_GRAVEYARD_AURA_SPELL_TEMPLATE for node in rejected.faces[0].nodes))
                self.assertNotEqual("exact", rejected.status)

    def test_withdrawing_linked_authority_blocks_the_whole_card(self):
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())
        for capability in (LINKED_GRAVEYARD_AURA_CAPABILITY, "attachment.aura.typed_restriction"):
            raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
            affected = next(row for row in raw["capabilities"] if row["id"] == capability)
            affected.update(status="blocked", blockers=["Test-only withdrawal"])
            registry = CapabilityRegistry(raw)
            index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            with self.subTest(capability=capability):
                changed = self.compile(self.card(), registry)
                self.assertNotEqual("exact", changed.status)
                self.assertTrue(any(capability in item for r in changed.material_residuals for item in r.blockers))

    def test_mutating_the_exact_body_recognizer_leaves_the_original_unowned(self):
        from unittest.mock import patch
        from quorune.compiler import linked_graveyard_aura_nodes as owner

        def witness():
            found = self.compile(self.card())
            self.assertEqual("exact", found.status, "The linked Aura source split was omitted")

        witness()
        with patch.object(owner, "_ENTER", "unrepresented body"), self.assertRaisesRegex(
            AssertionError, "The linked Aura source split was omitted",
        ):
            witness()
