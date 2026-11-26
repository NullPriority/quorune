"""Vilis draws for the retained controller from actual committed life loss."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.life_change import LIFE_LOSS_EVENT
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_outcome_shapes import controller_life_loss_draw_condition, controller_life_loss_draw_effect
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Vilis, Broker of Blood"


class ControllerLifeLossDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-life-loss-draw.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controller-life-loss-draw-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Draw only from committed own loss", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_preserves_flying_paid_activation_and_exact_draw_scope(self):
        admitted, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        rows = semantics.programs_for_oracle(card.oracle_id)
        trigger, = (row for row in rows if row.event == LIFE_LOSS_EVENT)
        self.assertIsNone(trigger.target_schema)
        self.assertEqual(controller_life_loss_draw_condition(), trigger.event_condition)
        self.assertEqual([controller_life_loss_draw_effect()], trigger.effects)
        self.assertTrue(any(row.event == "continuous" for row in rows))
        activation, = (row for row in rows if row.event == "activate")
        self.assertEqual(2, activation.cost_schema["life_payment"])
        self.assertEqual("modify_stats_until_end_of_turn", activation.effects[0]["op"])
        for changed in (replace(trigger, event_condition={"field": "player", "op": "ne", "value": "$source.controller"}),
                        replace(trigger, effects=[{**trigger.effects[0], "private": False}])):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])

    def test_private_draws_bound_to_damage_do_not_claim_life_loss(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
        from quorune.oracle_ir import compile_oracle_card
        from quorune.semantics import SemanticRegistry
        from test_bound_effect_programs import record

        texts = (
            "Whenever this creature deals combat damage to a player, draw that many cards.",
            "Whenever this creature is dealt damage, draw that many cards.",
        )
        for index, text in enumerate(texts, 1):
            with self.subTest(text=text):
                card = replace(record(text, "Creature — Nightmare"),
                               oracle_id=f"00000000-0000-4000-8000-00000000dd0{index}")
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status)
                program = _compile_best_available_card_program_from_ir(
                    self.db, card, ir, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile="commander_review",
                )
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])

    def life_triggers(self, game, source):
        return [item for item in game.state.stack
                if item.source_object_id == source.object_id and item.context.get("event") == LIFE_LOSS_EVENT]

    def test_actual_paid_activation_stacks_before_draw_and_keeps_the_loss_after_source_exile_and_gain(self):
        game = self.session(11904081)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        target = self.add(game.engine, "Runeclaw Bear", seat="B")
        response = self.add(game.engine, "Swords to Plowshares", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B")
        self.add(game.engine, "Forest", zone="library", ref="vilis-private-draw-identity")
        for card in (source, response):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "B": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"B": 1})
        self.resolve_top(game)
        source_data = game.engine._effective_card_data(game.state.cards[source.object_id])
        self.assertIn("flying", {word.casefold() for word in source_data["keywords"]})
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activate, = (row["id"] for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        command = {"action_id": activate, "targets": [target.ref], "pay": "auto"}
        life = game.state.players["A"].life
        hand = len(game.state.players["A"].zones["hand"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual((life - 2, hand), (game.state.players["A"].life, len(game.state.players["A"].zones["hand"])))
        self.assertEqual(["activated_ability", "triggered_ability"], [item.kind for item in game.state.stack])
        self.assertEqual([target.ref], game.state.stack[0].targets)
        trigger, = self.life_triggers(game, source)
        self.assertEqual(("A", 2), (trigger.context["player"], trigger.context["amount"]))
        self.assertEqual(2, game.engine._numeric_stat(target.object_id, "power"))
        self.cast_for(game, "B", response, [source.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.assertEqual(life - 2 + 8, game.state.players["A"].life)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(hand + 2, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(2, game.engine._numeric_stat(target.object_id, "power"))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.engine._numeric_stat(target.object_id, "power"))
        self.assertNotIn("vilis-private-draw-identity", json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_actual_damage_to_the_controller_draws_three_and_damage_to_another_player_does_not(self):
        game = self.session(12003081)
        source = self.add(game.engine, ORIGINAL)
        bolts = [self.add(game.engine, "Lightning Bolt", zone="hand", seat="B", ref=f"vilis-bolt-{index}")
                 for index in range(2)]
        for card in (source, *bolts):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"R": 2})
        self.checkpoint(game)
        life_a, life_c = (game.state.players[seat].life for seat in ("A", "C"))
        hand = len(game.state.players["A"].zones["hand"])
        self.cast_for(game, "B", bolts[0], ["C"], reserve={"R": 1})
        self.resolve_top(game)
        self.assertEqual(life_c - 3, game.state.players["C"].life)
        self.assertFalse(self.life_triggers(game, source))
        self.assertEqual(hand, len(game.state.players["A"].zones["hand"]))
        self.cast_for(game, "B", bolts[1], ["A"])
        self.resolve_top(game)
        trigger, = self.life_triggers(game, source)
        self.assertEqual(("A", 3), (trigger.context["player"], trigger.context["amount"]))
        self.assertEqual(life_a - 3, game.state.players["A"].life)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand + 3, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def test_actual_explicit_spell_loss_triggers_a_separate_draw_after_the_original_draw(self):
        game = self.session(11903081)
        source = self.add(game.engine, ORIGINAL)
        spell = self.add(game.engine, "Night's Whisper", zone="hand")
        for card in (source, spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        hand = len(game.state.players["A"].zones["hand"])
        life = game.state.players["A"].life
        self.resolve_top(game)
        self.assertEqual((life - 2, hand + 2), (game.state.players["A"].life, len(game.state.players["A"].zones["hand"])))
        trigger, = self.life_triggers(game, source)
        self.assertEqual(2, trigger.context["amount"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand + 4, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)
