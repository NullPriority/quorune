"""A real paid-creature cast seals its private upper bound through replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.paid_creature_library_search import CAPABILITY, paid_creature_search_effect
from quorune.scalar_effect_amount_model import PAID_SACRIFICE_MANA_VALUE_CONTEXT
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index


class PaidCreatureLibrarySearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "paid-creature-search.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/paid-creature-search-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid creature search", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_exact_source_and_dependency_withdrawal(self):
        original = self.db.lookup("Eldritch Evolution")
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        compiled = compile_oracle_card(original, **options)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        node, = compiled.faces[0].nodes
        self.assertEqual((paid_creature_search_effect(),), tuple(node.effects))
        for old, new in (("sacrifice a creature", "sacrifice an artifact"), ("2 plus", "3 plus"),
                         ("onto the battlefield", "into your hand"), ("Exile Eldritch Evolution.", "")):
            with self.subTest(old=old):
                changed = replace(original, oracle_text=original.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)

        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        current = read_evidence_document(ROOT / "quorune/rules/capability-evidence.json")
        known_tests = {row["test_id"] for row in current["declarations"]}
        raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == CAPABILITY)
        row.update(status="blocked", blockers=["Test-only result authority withdrawal"])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known_tests)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        rejected = compile_oracle_card(original, capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", rejected.status)
        self.assertTrue(any(CAPABILITY in blocker for residual in rejected.material_residuals
                            for blocker in residual.blockers))

    def scene(self, seed, *, replacement=False, enter_replacement=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        spell = self.add(game.engine, "Eldritch Evolution", zone="hand")
        paid = self.add(game.engine, "Isamaru, Hound of Konda")
        foreign = self.add(game.engine, "Grizzly Bears", seat="C")
        paid.annotations["copy_overrides"] = dict(game.engine._copyable_characteristics(foreign))
        good = self.add(game.engine, "Ravenous Chupacabra", zone="library")
        high = self.add(game.engine, "Kalonian Behemoth", zone="library")
        if replacement:
            self.add(game.engine, "Rest in Peace", seat="B")
        if enter_replacement:
            self.add(game.engine, "Orb of Dreams", ref="evolution-entry-orb")
            self.add(game.engine, "Archelos, Lagoon Mystic", seat="C", ref="evolution-entry-archelos")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 1, "G": 2})
        self.checkpoint(game)
        return game, spell, paid, foreign, good, high

    def cast_with_paid_creature(self, game, spell, paid, foreign, *, displaced):
        action = self.action(game, "A", spell)
        before = authoritative_state_hash(game.state)
        wrong = {"action_id": action["id"], "sacrifice_cards": [foreign.ref], "pay": "auto"}
        self.assertFalse(game.act("pilot:A", wrong).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        logical = paid.logical_object_id
        result = game.act("pilot:A", {**wrong, "sacrifice_cards": [paid.ref]})
        self.assertTrue(result.ok, result.summary)
        item = game.state.stack[-1]
        receipt = item.context[PAID_SACRIFICE_MANA_VALUE_CONTEXT]
        self.assertEqual({"schema_version": 1, "logical_object_id": logical, "mana_value": 2}, receipt)
        self.assertEqual(displaced, game.state.cards[paid.object_id].zone)
        return item

    def assert_private_search(self, game, good, high):
        self.assertEqual("semantic.search", self.resolve(game).kind)
        packet = game.packet("pilot:A", full=True)
        self.assertEqual({good.ref}, {row["id"] for row in packet["decision"]["ctx"]["search_cards"]})
        for other in "BCD":
            observed = json.dumps(game.packet(f"pilot:{other}", full=True))
            self.assertNotIn(good.ref, observed)
            self.assertNotIn(high.ref, observed)

    def test_native_exact_bound_survives_replacement_and_private_failure_to_find(self):
        for replaced, found in ((False, True), (True, True), (False, False)):
            with self.subTest(sacrifice_replaced=replaced, found=found):
                game, spell, paid, foreign, good, high = self.scene(620201 + replaced, replacement=replaced)
                self.cast_with_paid_creature(game, spell, paid, foreign, displaced="exile" if replaced else "graveyard")
                self.assert_private_search(game, good, high)
                game = self.replay(game, load=True)
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:A", {"action_id": "choose", "search_cards": [high.ref]}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                shuffles = sum(event.code == "library.shuffle" for event in game.state.events)
                selected = [good.ref] if found else []
                result = game.act("pilot:A", {"action_id": "choose", "search_cards": selected})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("battlefield" if found else "library", game.state.cards[good.object_id].zone)
                self.assertEqual("exile", game.state.cards[spell.object_id].zone)
                self.assertEqual(shuffles + 1, sum(event.code == "library.shuffle" for event in game.state.events))
                self.replay(game, load=True)

        game, spell, paid, foreign, good, high = self.scene(620203)
        self.cast_with_paid_creature(game, spell, paid, foreign, displaced="graveyard")
        with patch("quorune.rules.paid_creature_library_search.resolve_scalar_effect_amount", return_value=1):
            with self.assertRaises(AssertionError):
                self.assert_private_search(game, good, high)

    def test_private_entry_replacement_resumes_and_exiles_after_shuffle(self):
        game, spell, paid, foreign, good, high = self.scene(620204, enter_replacement=True)
        self.cast_with_paid_creature(game, spell, paid, foreign, displaced="graveyard")
        self.assert_private_search(game, good, high)
        shuffles = sum(event.code == "library.shuffle" for event in game.state.events)
        result = game.act("pilot:A", {"action_id": "choose", "search_cards": [good.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(("replacement.order", ["A"]),
                         (game.state.pending_decision.kind, game.state.pending_decision.actors))
        self.assertEqual("stack", game.state.cards[spell.object_id].zone)
        for other in "BCD":
            self.assertNotIn(good.ref, json.dumps(game.packet(f"pilot:{other}", full=True)))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        orb = next(option["id"] for option in options if "evolution-entry-orb" in option["id"])
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "replacement": orb})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[good.object_id].zone)
        self.assertEqual(shuffles + 1, sum(event.code == "library.shuffle" for event in game.state.events))
        self.replay(game, load=True)
