"""Exercise a fixed life alternative through original native payment and hand owners."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.targeted_life_decline import targeted_life_decline_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.targeted_life_decline import (
    TARGETED_LIFE_DECLINE_CAPABILITY, targeted_life_decline_node_capabilities,
)
from quorune.semantic_choices.context import ChoiceStackView, SemanticChoiceContext, SnapshotSemanticChoiceQuery
from quorune.semantic_choices.fixed_effect_payment import prepare_fixed_effect_payment
from quorune.semantic_choices.model import SemanticChoiceError
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class TargetedFixedLifeDeclineTests(InheritedNativeFixture, unittest.TestCase):
    fixture_source_name = "Generic Targeted Life Alternative"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "targeted-life.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "targeted-fixed-life-decline-cards.json",
        ], database)
        cls.configure_game_assets(database)

    @classmethod
    def configure_game_assets(cls, database):
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        ruler = "Generic Bound Commander"
        cls.deck = DeckDefinition("Fixed life alternative", [
            DeckEntry(ruler, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [ruler])

    def offer(self, seed, *, life):
        game = self.session(seed)
        source = self.add(game.engine, self.fixture_source_name, zone="hand", ref="PAYMENT-SPELL")
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        game.state.players["B"].life = life
        hand = tuple(game.state.players["B"].zones["hand"])
        self.assertTrue(hand)
        self.prepare(game, mana={"C": 5, "B": 2, "R": 2})
        self.checkpoint(game)
        self.priority(game, "A")
        offer = self.action(game, "A", game.state.cards[source.object_id])
        option = offer["cost_options"][0]["id"]
        result = game.act("pilot:A", {
            "action_id": offer["id"], "targets": ["B"], "cost_option": option, "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual("semantic.choice", decision.kind)
        self.assertEqual(["B"], decision.actors)
        return game, hand

    def choose(self, game, *, pay):
        result = game.act("pilot:B", {"action_id": "choose", "pay": pay})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.stack)

    def test_paid_target_keeps_hand_and_replays_after_loading(self):
        game, hand = self.offer(11812001, life=12)
        packet = game.packet("pilot:B", full=True)["decision"]
        self.assertTrue(packet["ctx"]["payable"])
        self.assertEqual(7, packet["ctx"]["life_cost"])
        for observer in ("A", "C", "D"):
            view = game.packet(f"pilot:{observer}", full=True)
            self.assertIsNone(view["decision"])
            self.assertTrue(all(game.state.cards[card].ref not in json.dumps(view) for card in hand))
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "pay": True}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.choose(game, pay=True)
        self.assertEqual(5, game.state.players["B"].life)
        self.assertEqual(hand, tuple(game.state.players["B"].zones["hand"]))
        self.replay(game, load=True)

    def test_unaffordable_pay_rejects_and_only_target_discards_on_decline(self):
        game, hand = self.offer(11812002, life=6)
        self.assertFalse(game.packet("pilot:B", full=True)["decision"]["ctx"]["payable"])
        untouched = tuple(game.state.players["C"].zones["hand"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "pay": True}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.choose(game, pay=False)
        self.assertEqual(6, game.state.players["B"].life)
        self.assertEqual([], game.state.players["B"].zones["hand"])
        self.assertTrue(set(hand) <= set(game.state.players["B"].zones["graveyard"]))
        self.assertEqual(untouched, tuple(game.state.players["C"].zones["hand"]))
        self.replay(game, load=True)

    def test_affordable_player_can_decline_without_life_payment(self):
        game, hand = self.offer(11812003, life=12)
        self.choose(game, pay=False)
        self.assertEqual(12, game.state.players["B"].life)
        self.assertTrue(set(hand) <= set(game.state.players["B"].zones["graveyard"]))
        self.replay(game, load=True)

    def test_wrong_choice_target_and_result_controller_reject(self):
        record = self.db.lookup(self.fixture_source_name, fuzzy=False)
        _, effects, _, _ = targeted_life_decline_template(record.oracle_text)
        resolved = {**effects[0], "player": "B", "declined_effects": [
            {**effects[0]["declined_effects"][0], "actor": "A", "players": ["B"]},
        ]}
        item = ChoiceStackView(ref="R", controller="A", label="Fixed life", semantic_key="program",
                               targets=("B",), modes=())
        view = SnapshotSemanticChoiceQuery(
            seat_order=("A", "B", "C"), active_order=("A", "B", "C"), life_by_seat={"B": 12},
            stack_rows=(item,),
        )
        context = SemanticChoiceContext(
            actor="B", stack_ref="R", stack_controller="A", stack_label="Fixed life", source_ref=None, card_ref=None,
            semantic_program_id="program", semantic_program_version=1, query=view,
        )
        self.assertEqual((True, False), prepare_fixed_effect_payment(resolved, context).request.choice.legal_values)
        wrong_target = replace(context, query=replace(view, stack_rows=(replace(item, targets=("C",)),)))
        with self.assertRaisesRegex(SemanticChoiceError, "resolving authority"):
            prepare_fixed_effect_payment(resolved, wrong_target)
        other_source = {**resolved, "declined_effects": [{**resolved["declined_effects"][0], "actor": "C"}]}
        with self.assertRaisesRegex(SemanticChoiceError, "resolving authority"):
            prepare_fixed_effect_payment(other_source, context)
        with self.assertRaisesRegex(SemanticChoiceError, "paying player's"):
            prepare_fixed_effect_payment({**resolved, "declined_effects": [
                {**resolved["declined_effects"][0], "players": ["D"]},
            ]}, context)

    def test_compiler_requires_typed_target_and_reviewed_runtime_capability(self):
        card = self.db.lookup(self.fixture_source_name, fuzzy=False)

        def compile_card(record, registry=self.registry):
            return compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")

        compiled = compile_card(card)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        self.assertTrue(any(TARGETED_LIFE_DECLINE_CAPABILITY in node.capability_dependencies
                            for face in compiled.faces for node in face.nodes))
        _, effects, target_schema, mechanics = targeted_life_decline_template(card.oracle_text)
        self.assertIn(TARGETED_LIFE_DECLINE_CAPABILITY,
                      targeted_life_decline_node_capabilities(effects[0], target_schema, mechanics))
        alternate = {**effects[0], "player": "$controller"}
        self.assertEqual((), targeted_life_decline_node_capabilities(alternate, target_schema, mechanics))
        self.assertEqual((), targeted_life_decline_node_capabilities(effects[0], {**target_schema, "count": True}, mechanics))
        for old, new in (("their hand", "two cards"), ("they pay", "you pay"),
                         ("Target player", "Each opponent"), ("7 life", "X life")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertIsNone(targeted_life_decline_template(changed.oracle_text))
        with patch("quorune.compiler.targeted_life_decline.targeted_life_decline_template", return_value=None):
            self.assertNotEqual("exact", compile_card(card).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        entry = next(row for row in raw["capabilities"] if row["id"] == TARGETED_LIFE_DECLINE_CAPABILITY)
        entry.update(status="blocked", blockers=["Withdraw the targeted fixed life alternative."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_card(card, registry).status)
