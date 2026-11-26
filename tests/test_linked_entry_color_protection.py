"""Native evidence that an entry color belongs to its current linked ability."""

from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_designations import ENTRY_DESIGNATION_CAPABILITY, LINKED_COLOR_PROTECTION_CAPABILITY, LINKED_COLOR_PROTECTION_TEMPLATE
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.semantic_runtime.chosen_characteristics import LinkedColorProtectionHandler, linked_color_protection_descriptor
from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


VOICE = "Voice of All"
ORDER = "Order of the Stars"
CAPABILITY = LINKED_COLOR_PROTECTION_CAPABILITY


class LinkedEntryColorProtectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Linked entry color protection", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-entry-color-protection.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/linked-entry-color-protection-cards.json", path)
        cls.use_database(path)

    def choose_color(self, game, seat, color):
        self.assertEqual("replacement.order", self.resolve(game).kind)
        decision = game.packet("pilot:" + seat, full=True)["decision"]
        options = decision["ctx"]["options"]
        matches = [row["id"] for row in options
                   if row["label"].casefold().endswith("choose color " + color.casefold())]
        self.assertEqual(1, len(matches), options)
        choice, = matches
        command = {"action_id": "choose", "choices": {"replacement": choice}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def test_already_present_color_choice_cannot_feed_a_copied_definition(self):
        game = self.session(607020701)
        game.state.config.semantic_policy = "trusted_only"
        spirit = self.add(game.engine, "Chameleon Spirit", zone="hand")
        knight = self.add(game.engine, "Diamond Knight", zone="hand")
        weave = self.add(game.engine, "Mirrorweave", zone="hand")
        self.add(game.engine, "Fervor", seat="B")
        self.add(game.engine, "Glorious Anthem", seat="C")
        for card in (spirit, knight, weave):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 10, "W": 3, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", knight, reserve={"C": 7, "W": 3, "U": 1})
        self.choose_color(game, "A", "R")
        self.cast_for(game, "A", spirit, reserve={"C": 4, "W": 3})
        self.choose_color(game, "A", "W")
        self.assertEqual("R", game.state.cards[knight.object_id].annotations["chosen_color"])
        self.assertEqual(1, game.engine._numeric_stat(spirit.object_id, "toughness"))
        self.cast_for(game, "A", weave, [spirit.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[knight.object_id].zone,
                         "A new copy with no linked choice is 0/0")
        self.assertEqual("battlefield", game.state.cards[spirit.object_id].zone)
        self.replay(game, load=True)

    def test_native_protectors_keep_separate_choices_for_targets_and_global_damage(self):
        game = self.session(607020702)
        game.state.config.semantic_policy = "trusted_only"
        voice = self.add(game.engine, VOICE, zone="hand")
        order = self.add(game.engine, ORDER, zone="hand")
        clone = self.add(game.engine, "Clone", zone="hand")
        lightning = self.add(game.engine, "Lightning Bolt", zone="hand", seat="B")
        sweeper = self.add(game.engine, "Pyroclasm", zone="hand", seat="B")
        bounce = self.add(game.engine, "Unsummon", zone="hand", seat="B")
        self.add(game.engine, "Vedalken Orrery", seat="B")
        for card in (voice, order, clone, lightning, sweeper, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "W": 3, "U": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "R": 2, "U": 1})
        self.checkpoint(game)
        self.assertNotIn(voice.ref, json.dumps(game.packet("pilot:C", full=True)))
        self.assertNotIn(order.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.cast_for(game, "A", voice, reserve={"C": 3, "W": 1, "U": 1})
        self.choose_color(game, "A", "R")
        self.cast_for(game, "A", order, reserve={"C": 3, "U": 1})
        self.choose_color(game, "A", "U")
        self.cast_for(game, "A", clone)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        copy, = (item["id"] for item in options if item["id"].endswith(":" + voice.ref)
                 and not item.get("decline"))
        result = game.act("pilot:A", {"action_id": "choose", "replacement": copy})
        self.assertTrue(result.ok, result.summary)
        self.choose_color(game, "A", "U")
        game = self.replay(game, load=True)
        self.assertEqual(VOICE, game.engine.display_name(clone.object_id))

        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        red, = (row for row in rows if row["id"] == "cast:" + lightning.ref)
        blue, = (row for row in rows if row["id"] == "cast:" + bounce.ref)
        self.assertNotIn(voice.ref, red["target_schema"]["legal_refs"])
        self.assertIn(order.ref, red["target_schema"]["legal_refs"])
        self.assertIn(clone.ref, red["target_schema"]["legal_refs"])
        self.assertIn(voice.ref, blue["target_schema"]["legal_refs"])
        self.assertNotIn(order.ref, blue["target_schema"]["legal_refs"])
        self.assertNotIn(clone.ref, blue["target_schema"]["legal_refs"])
        unchanged = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": red["id"], "targets": [voice.ref], "pay": "auto"}).ok)
        self.assertEqual(unchanged, authoritative_state_hash(game.state))
        self.cast_for(game, "B", sweeper, reserve={"R": 1, "U": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[voice.object_id].zone)
        self.assertEqual(0, game.state.cards[voice.object_id].marked_damage)
        self.assertEqual("graveyard", game.state.cards[order.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[clone.object_id].zone)
        self.replay(game, load=True)

    def test_color_protection_needs_a_unique_same_face_entry_and_exact_runtime_binding(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Withhold linked color protection"],
        )
        withdrawn = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        game = self.session(607020703)
        for name in (VOICE, ORDER):
            original = self.db.lookup(name)
            valid = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
            self.assertEqual("exact", valid.status, valid.material_residuals)
            protection, = (node for face in valid.faces for node in face.nodes
                           if node.template_id == LINKED_COLOR_PROTECTION_TEMPLATE)
            self.assertTrue({CAPABILITY, ENTRY_DESIGNATION_CAPABILITY}.issubset(protection.capability_dependencies))
            self.assertNotEqual("exact", compile_oracle_card(original, capability_registry=withdrawn,
                                                             capability_profile="commander_review").status)
            for text in (original.oracle_text.replace("As this creature enters, choose a color.\n", ""),
                         original.oracle_text + "\nAs this creature enters, choose a color.",
                         original.oracle_text.replace("This creature has protection", "Other creatures have protection")):
                self.assertNotEqual("exact", compile_oracle_card(
                    replace(original, oracle_text=text), capability_registry=self.registry, capability_profile="commander_review",
                ).status)
            card = self.add(game.engine, name)
            self.assert_closed(game, card)
            program, = (p for p in game.engine.semantics.programs_for_oracle(card.oracle_id)
                        if p.provenance.get("template_id") == LINKED_COLOR_PROTECTION_TEMPLATE)
            forged = replace(program, active_zone="hand")
            self.assertFalse(bind_semantic_program_runtime(
                forged, capability_registry=self.registry, profile="commander_review",
            )["strict"])

    def test_component_witness_detects_omitted_typed_protection(self):
        handler = LinkedColorProtectionHandler()
        descriptor = linked_color_protection_descriptor()
        context = ContinuousEffectSourceContext("object", "source", "A", 1, "component",
                                                source_logical_object_id="incarnation", linked_color="R")

        def witness():
            effects = handler.lower(descriptor, context)
            self.assertEqual(1, len(effects))
            self.assertEqual(("add_ability", "add_ability_fragment"), tuple(op.op for op in effects[0].operations))

        witness()
        self.assertEqual((), handler.lower(descriptor, replace(context, linked_color=None)))
        with patch("quorune.semantic_runtime.chosen_characteristics._linked_self_protection", return_value=()):
            with self.assertRaises(AssertionError):
                witness()
