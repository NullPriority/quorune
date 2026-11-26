"""Actual activation payments retain the union of their battlefield card types."""

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
from quorune.paid_activation_card_types import CAPABILITY, CONTEXT_FIELD
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index


SOURCE = "Baba Lysaga, Night Witch"


class BoundedSacrificeCardTypesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "bounded-sacrifice.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/bounded-sacrifice-card-types-cards.json",
            ROOT / "tests/fixtures/fixed-qualified-stack-copy-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Bounded sacrifice witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def game(self, seed, *, replacement=False, copy=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        baba = self.add(game.engine, SOURCE, ref="baba")
        artifact = self.add(game.engine, "Darksteel Citadel", ref="artifact-land")
        bears = [self.add(game.engine, "Grizzly Bears", ref=f"bears-{index}") for index in range(3)]
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign")
        if replacement:
            peace = self.add(game.engine, "Rest in Peace", seat="B", ref="peace")
            self.assert_closed(game, peace)
        if copy:
            copier = self.add(game.engine, "Lithoform Engine", ref="copier")
            self.assert_closed(game, copier)
        self.assert_closed(game, baba)
        self.prepare(game, mana={"C": 2} if copy else {})
        self.checkpoint(game)
        self.priority(game, "A")
        available, = [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row["id"].startswith(f"activate:{baba.ref}:")]
        choice = available["choice_schema"]["cost_cards"]
        self.assertEqual((0, 3, [0, 1, 2, 3]), (choice["minimum"], choice["maximum"], choice["allowed_counts"]))
        self.assertIn(baba.ref, choice["legal_refs"])
        self.assertNotIn(foreign.ref, choice["legal_refs"])
        return game, baba, artifact, bears, foreign, available

    def submit(self, game, action, cards):
        result = game.act("pilot:A", {"action_id": action["id"], "cost_cards": [card.ref for card in cards]})
        self.assertTrue(result.ok, result.summary)
        return next(item for item in game.state.stack if item.kind == "activated_ability")

    def resolve_successful_replaced_cost(self, seed):
        game, baba, artifact, _bears, _foreign, action = self.game(seed, replacement=True)
        initial_life = {seat: player.life for seat, player in game.state.players.items()}
        hand_before = len(game.state.players["A"].zones["hand"])
        item = self.submit(game, action, [baba, artifact])
        self.assertEqual(["artifact", "creature", "land"], item.context[CONTEXT_FIELD]["types"])
        for card in (baba, artifact):
            self.assertEqual("exile", game.state.cards[card.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        for seat, previous in initial_life.items():
            self.assertEqual(previous + (3 if seat == "A" else -3), game.state.players[seat].life)
        hand = game.state.players["A"].zones["hand"]
        self.assertEqual(hand_before + 3, len(hand))
        opponent_view = json.dumps(game.packet("pilot:B", full=True))
        for object_id in hand[-3:]:
            self.assertNotIn(game.state.cards[object_id].ref, opponent_view)
        self.replay(game, load=True)

    def test_actual_two_permanents_count_three_types_even_when_replaced_and_replay(self):
        self.resolve_successful_replaced_cost(40072801)

    def test_ordinary_activation_copy_keeps_paid_types_without_repaying(self):
        game, baba, artifact, _bears, _foreign, action = self.game(40072843, copy=True)
        before = len(game.state.players["A"].zones["hand"])
        original = self.submit(game, action, [baba, artifact])
        self.priority(game, "A")
        packet = game.packet("pilot:A", full=True)["decision"]["ctx"]
        copier, = [row for row in packet["legal"]["actions"] if row["id"].startswith("activate:copier:")
                   and original.ref in json.dumps(row.get("target_schema") or {})]
        accepted = game.act("pilot:A", {"action_id": copier["id"], "targets": [original.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)
        copied, = [item for item in game.state.stack if item.context.get("copied_from_stack") == original.ref]
        self.assertEqual(original.context[CONTEXT_FIELD], copied.context[CONTEXT_FIELD])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual([46, 34, 34, 34], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(before + 6, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def test_zero_and_three_same_type_are_legal_and_have_no_result(self):
        for index in (0, 3):
            with self.subTest(count=index):
                game, baba, _artifact, bears, _foreign, action = self.game(40072802 + index)
                before = [(player.life, len(player.zones["hand"])) for player in game.state.players.values()]
                item = self.submit(game, action, bears[:index])
                self.assertTrue(game.state.cards[baba.object_id].tapped)
                self.assertEqual(["creature"] if index else [], item.context[CONTEXT_FIELD]["types"])
                self.resolve_top(game)
                self.assertEqual(before, [(player.life, len(player.zones["hand"])) for player in game.state.players.values()])
                self.replay(game, load=True)

    def test_foreign_duplicate_and_excess_costs_roll_back_before_a_legal_payment(self):
        game, baba, artifact, bears, foreign, action = self.game(40072818)
        before = authoritative_state_hash(game.state)
        for cards in ([foreign], [artifact, artifact], [artifact, *bears]):
            with self.subTest(refs=[card.ref for card in cards]):
                result = game.act("pilot:A", {"action_id": action["id"], "cost_cards": [card.ref for card in cards]})
                self.assertFalse(result.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                self.assertFalse(game.state.cards[baba.object_id].tapped)
        self.submit(game, action, [])

    def test_whole_card_rejects_a_different_cost_result_or_withdrawn_authority(self):
        source = self.db.lookup(SOURCE, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        original = compile_oracle_card(source, **options)
        self.assertEqual("exact", original.status, original.material_residuals)
        for before, after in (("up to three permanents", "up to four permanents"),
                              ("you gain 3 life", "you gain 4 life")):
            changed = replace(source, oracle_text=source.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)
        registry_raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
        row = next(row for row in registry_raw["capabilities"] if row["id"] == CAPABILITY)
        row.update(status="blocked", blockers=["Test-only withdrawal of the committed paid card types"])
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        current = read_evidence_document(ROOT / "quorune/rules/capability-evidence.json")
        registry = CapabilityRegistry(registry_raw)
        index = build_index(registry_value=registry_raw, declaration_source=declarations,
                            discovered_test_ids={row["test_id"] for row in current["declarations"]})
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(source, **{**options, "capability_registry": registry}).status)

    def test_mutating_the_retained_type_result_breaks_the_native_witness(self):
        with patch("quorune.paid_activation_card_types.resolve_paid_type_condition", return_value=False):
            with self.assertRaises(AssertionError):
                self.resolve_successful_replaced_cost(40072831)
