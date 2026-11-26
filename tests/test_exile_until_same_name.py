"""Real Tainted Pact commands exercise ordered exile, private choice and replay."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_match_outcome_rules as outcomes
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.exile_until_same_name import EXILE_UNTIL_SAME_NAME_CAPABILITY
from scripts.update_capability_evidence import build_index, discover_tests
from scripts.build_test_database import build_fixture_database


class RepeatedLibraryExileTests(InheritedNativeFixture, unittest.TestCase):
    witnessed_match = outcomes.MatchOutcomeRulesTests.witnessed_match

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "repeated-library-exile.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/repeated-library-exile-cards.json", path)
        outcomes.MatchOutcomeRulesTests.use_database.__func__(cls, path)

    def offer(self, game, pact):
        self.assert_closed(game, pact)
        self.prepare(game, mana={"C": 1, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", pact)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind != "priority")

    def test_repeat_exiles_both_named_cards_and_never_offers_the_second(self):
        game = self.witnessed_match(6082501)
        pact = self.add(game.engine, "Tainted Pact", zone="hand")
        later = self.add(game.engine, "Island", zone="library", ref="repeated-exile-later")
        duplicate = self.add(game.engine, "Swamp", zone="library", ref="repeated-exile-duplicate")
        first = self.add(game.engine, "Swamp", zone="library", ref="repeated-exile-first")
        self.offer(game, pact)

        self.assertEqual("exile", game.state.cards[first.object_id].zone)
        self.assertEqual("library", game.state.cards[duplicate.object_id].zone)
        self.assertNotIn(later.ref, json.dumps(game.packet("pilot:B", full=True)))
        before = authoritative_state_hash(game.state)
        for principal, selected in (("pilot:B", [first.ref]), ("pilot:A", [duplicate.ref])):
            result = game.act(principal, {"action_id": "choose", "objects": selected})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))

        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "objects": []})
        self.assertTrue(result.ok, result.summary)
        self.settle(game)
        self.assertEqual("exile", game.state.cards[first.object_id].zone)
        self.assertEqual("exile", game.state.cards[duplicate.object_id].zone)
        self.assertEqual("library", game.state.cards[later.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[pact.object_id].zone)
        self.replay(game, load=True)

    def test_take_moves_the_captured_card_and_stops_before_the_next_top(self):
        game = self.witnessed_match(6082502)
        pact = self.add(game.engine, "Tainted Pact", zone="hand")
        later = self.add(game.engine, "Island", zone="library", ref="take-exile-later")
        first = self.add(game.engine, "Fire // Ice", zone="library", ref="take-exile-first")
        self.offer(game, pact)
        self.assertEqual("exile", game.state.cards[first.object_id].zone)

        result = game.act("pilot:A", {"action_id": "choose", "objects": [first.ref]})
        self.assertTrue(result.ok, result.summary)
        self.settle(game)
        self.assertEqual("hand", game.state.cards[first.object_id].zone)
        self.assertEqual("library", game.state.cards[later.object_id].zone)
        self.replay(game, load=True)

    def test_incomplete_authority_does_not_admit_the_spell(self):
        source = self.db.lookup("Tainted Pact", fuzzy=False)
        arguments = {"capability_profile": "commander_review"}
        self.assertEqual("exact", compile_oracle_card(source, capability_registry=self.registry, **arguments).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == EXILE_UNTIL_SAME_NAME_CAPABILITY)
        row.update(status="blocked", blockers=["Withhold the repeated library exile authority"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(source, capability_registry=registry, **arguments).status)
        with patch("quorune.compiler.exile_until_same_name_templates.exile_until_same_name_template", return_value=None):
            result = compile_oracle_card(source, capability_registry=self.registry, **arguments)
            self.assertNotEqual("exact", result.status)
