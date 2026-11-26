"""The spell creates one next-own-upkeep payment after its primary instruction."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.delayed_triggers import FIXED_NEXT_UPKEEP_PAYMENT_CAPABILITY, fixed_next_upkeep_payment_cost
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = {
    "Pact of Negation": ("counter_stack_target", {"GENERIC": 3, "U": 2}),
    "Pact of the Titan": ("create_token", {"GENERIC": 4, "R": 1}),
    "Slaughter Pact": ("destroy", {"GENERIC": 2, "B": 1}),
    "Summoner's Pact": ("search", {"GENERIC": 2, "G": 2}),
}


class FixedNextUpkeepPaymentPactTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-upkeep-pacts.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-next-upkeep-payment-pacts-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Delayed upkeep payment witnesses", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_keep_one_source_span_and_their_independent_primary_instructions(self):
        for name, (operation, cost) in ORIGINALS.items():
            with self.subTest(card=name):
                card = self.db.lookup(name, fuzzy=False)
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                program, = semantics.programs_for_oracle(card.oracle_id)
                first, delayed = program.effects
                self.assertEqual(operation, first["op"])
                self.assertEqual(cost, fixed_next_upkeep_payment_cost(delayed))
                self.assertIn(FIXED_NEXT_UPKEEP_PAYMENT_CAPABILITY, program.capability_dependencies)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
                node, = ir.faces[0].nodes
                self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
                with patch("quorune.compiler.fixed_effect_clause_sequences._fixed_upkeep_payment_clauses", return_value=None):
                    omitted = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
                self.assertTrue(omitted.material_residuals)
        incomplete = self.db.lookup("Intervention Pact", fuzzy=False)
        retained = compile_oracle_card(incomplete, capability_registry=self.registry, capability_profile="commander_review")
        self.assertTrue(retained.material_residuals)

    def to_next_own_upkeep(self, game):
        for seat in ("B", "C", "D"):
            self.until(game, lambda item: item.state.active_player == seat and item.state.step == "upkeep")
            self.assertEqual([], game.state.stack)
            self.assertEqual(1, len(game.state.delayed_triggers))
        self.until(game, lambda item: item.state.active_player == "A" and item.state.step == "upkeep"
                   and bool(item.state.stack))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        return game.packet("pilot:A", full=True)["decision"]["ctx"]

    def test_actual_negation_schedules_after_a_protected_target_and_pays_on_the_next_own_upkeep(self):
        game = self.playing(61127231)
        spell = self.add(game.engine, "Pact of Negation", zone="hand")
        fallout = self.add(game.engine, "Volcanic Fallout", seat="B", zone="hand")
        for card in (spell, fallout):
            self.assert_closed(game, card)
        lands = [self.add(game.engine, name, ref=f"pact-mana-{index}")
                 for index, name in enumerate(("Island", "Island", "Plains", "Plains", "Plains"))]
        self.prepare(game, actor="A", active="A")
        game.state.players["B"].mana_pool["R"] = 3
        self.checkpoint(game)
        self.cast_for(game, "B", fallout)
        self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["A"].life)
        self.assertEqual("graveyard", game.state.cards[fallout.object_id].zone)
        ctx = self.to_next_own_upkeep(game)
        self.assertTrue(ctx["payable"])
        self.assertEqual((3, 2), (ctx["cost"]["GENERIC"], ctx["cost"]["U"]))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "pay": True}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        paid = game.act("pilot:A", {"action_id": "choose", "pay": True})
        self.assertTrue(paid.ok, paid.summary)
        self.assertTrue(game.state.players["A"].in_game)
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in lands))
        retained, = game.state.delayed_triggers
        self.assertFalse(retained.active)
        self.replay(game, load=True)

    def test_actual_titan_owes_its_fixed_payment_after_the_token_departs(self):
        game = self.playing(61127232)
        spell = self.add(game.engine, "Pact of the Titan", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="C", zone="hand")
        for card in (spell, bounce):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A")
        game.state.players["C"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        giant, = (card for card in game.state.cards.values()
                  if card.zone == "battlefield" and card.is_token and card.controller == "A")
        self.assertEqual((4, 4), (game.engine._numeric_stat(giant.object_id, "power"),
                                  game.engine._numeric_stat(giant.object_id, "toughness")))
        self.cast_for(game, "C", bounce, [giant.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual("battlefield", game.state.cards[giant.object_id].zone)
        ctx = self.to_next_own_upkeep(game)
        self.assertFalse(ctx["payable"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "pay": True}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": "choose", "pay": False})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.players["A"].in_game)
        self.assertTrue(all(game.state.players[seat].in_game for seat in "BCD"))
        self.replay(game, load=True)

    def test_actual_slaughter_distinguishes_a_legal_failed_destruction_from_an_illegal_target(self):
        for invalid in (False, True):
            with self.subTest(invalid=invalid):
                game = self.playing(61127233 + invalid)
                spell = self.add(game.engine, "Slaughter Pact", zone="hand")
                target = self.add(game.engine, "Darksteel Myr", seat="B")
                bounce = self.add(game.engine, "Unsummon", seat="C", zone="hand")
                for card in (spell, target, bounce):
                    self.assert_closed(game, card)
                self.prepare(game, actor="A", active="A")
                game.state.players["C"].mana_pool["U"] = 1
                self.checkpoint(game)
                self.cast_for(game, "A", spell, [target.ref])
                if invalid:
                    self.cast_for(game, "C", bounce, [target.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("hand" if invalid else "battlefield", game.state.cards[target.object_id].zone)
                self.assertEqual(0 if invalid else 1, len(game.state.delayed_triggers))
                self.replay(game, load=True)

    def test_actual_summoner_finishes_the_private_search_before_creating_the_payment(self):
        game = self.playing(61127235)
        spell = self.add(game.engine, "Summoner's Pact", zone="hand")
        selected = self.add(game.engine, "Colossal Dreadmaw", zone="library")
        for card in (spell, selected):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A")
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        pending = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (pending.kind, pending.actors))
        self.assertFalse(game.state.delayed_triggers)
        self.assertNotIn(selected.ref, str(game.packet("pilot:C", full=True)))
        command = {"action_id": "choose", "search_cards": [selected.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        chosen = game.act("pilot:A", command)
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[selected.object_id].zone)
        delayed, = game.state.delayed_triggers
        self.assertEqual("A", delayed.controller)
        self.replay(game, load=True)

    def test_the_original_reviewed_counter_and_payment_payload_still_replays(self):
        game = self.playing(61127236)
        spell = self.add(game.engine, "Pact of Negation", zone="hand")
        target = self.add(game.engine, "Ornithopter", seat="B", zone="hand")
        reviewed, = SemanticRegistry().programs_for_oracle(spell.oracle_id)
        game.engine.semantics.put(reviewed)
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        self.cast_for(game, "B", target)
        self.cast_for(game, "A", spell, [game.state.stack[-1].ref])
        self.assertIsNone(self.resolve(game))
        delayed, = game.state.delayed_triggers
        self.assertEqual(reviewed.effects[1]["label"], delayed.label)
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        loaded = self.replay(game, load=True)
        resident, = loaded.engine.semantics.programs_for_oracle(spell.oracle_id)
        self.assertEqual(reviewed.effects, resident.effects)
