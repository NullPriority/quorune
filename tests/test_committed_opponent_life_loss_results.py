"""Use committed opponent loss for fully registered life and mill triggers."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


EXQUISITE = "Exquisite Blood"
CONQUEROR = "Bloodthirsty Conqueror"
MINDCRANK = "Mindcrank"


class CommittedOpponentLifeLossResultTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "committed-opponent-loss.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/committed-opponent-life-loss-results-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Committed opponent life loss", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_keep_the_exact_opponent_occurrence_and_other_keywords(self):
        for name in (EXQUISITE, CONQUEROR, MINDCRANK):
            with self.subTest(card=name):
                card = self.db.lookup(name, fuzzy=False)
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                trigger, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "life.lost"]
                self.assertEqual(("battlefield", None, {"field": "player", "op": "ne", "value": "$source.controller"}),
                                 (trigger.active_zone, trigger.target_schema, trigger.event_condition))
                effect, = trigger.effects
                amount = ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT).to_dict()
                if name == MINDCRANK:
                    self.assertEqual(("mill", "$context.player", amount),
                                     (effect["op"], effect["player"], effect["count"]))
                else:
                    self.assertEqual(("life", "$controller", amount),
                                     (effect["op"], effect["player"], effect["delta"]))
                if name == CONQUEROR:
                    lines = tuple(node.text.casefold() for face in compile_oracle_card(
                        card, capability_registry=self.registry,
                    ).faces for node in face.nodes)
                    self.assertTrue(any("flying" in line and "deathtouch" in line for line in lines), lines)
                for text in (card.oracle_text.replace("an opponent loses", "a player loses"),
                             card.oracle_text.replace("loses life", "loses life during your turn"),
                             card.oracle_text + " Exile that player's hand."):
                    self.assertNotEqual("exact", compile_oracle_card(
                        replace(card, oracle_text=text), capability_registry=self.registry,
                    ).status, text)

    def test_runtime_rejects_a_substituted_opponent_amount_or_subscription(self):
        card = self.db.lookup(MINDCRANK, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, MINDCRANK)
        trigger, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "life.lost"]
        effect, = trigger.effects
        dependencies = tuple(key for key in trigger.capability_dependencies
                             if key != "trigger.event.normalized_life_loss")
        for modified in (
            replace(trigger, coverage=(), capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict()),
            replace(trigger, target_schema={"categories": ["player"], "count": 1}),
            replace(trigger, event_condition=None),
            replace(trigger, active_zone="graveyard"),
            replace(trigger, effects=({**effect, "player": "$controller"},)),
            replace(trigger, effects=({**effect, "count": 2},)),
        ):
            with self.subTest(program=modified.key, effects=modified.effects):
                binding = bind_semantic_program_runtime(modified, capability_registry=self.registry,
                                                        profile="commander_review")
                self.assertFalse(binding["strict"], binding)

    def enter(self, game, source, mana, *controls):
        for card in (source, *controls):
            self.assert_closed(game, card)
        self.prepare(game, mana=mana)
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)

    def test_actual_mindcrank_follows_paid_life_and_explicit_loss_without_milling_other_seats(self):
        game = self.session(61341101)
        engine = game.engine
        source = self.add(engine, MINDCRANK, zone="hand")
        confluence = self.add(engine, "Mana Confluence", seat="B")
        bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
        creature = self.add(engine, "Grizzly Bears", seat="C")
        bite = self.add(engine, "Sovereign's Bite", zone="hand")
        hidden = self.add(engine, "Counterspell", seat="D", zone="hand", ref="life-loss-private-hand")
        top = self.add(engine, "Child of Night", seat="B", zone="library", ref="life-loss-milled-top")
        self.enter(game, source, {"C": 3, "B": 1}, confluence, bounce, bite)
        before = {seat: len(player.zones["library"]) for seat, player in game.state.players.items()}
        life = game.state.players["B"].life
        self.cast_for(game, "B", bounce, [creature.ref])
        self.assertTrue(game.state.cards[confluence.object_id].tapped)
        self.assertEqual(life - 1, game.state.players["B"].life)
        self.assertEqual(1, self.trigger_count(game, source), "Actual life payment must occur once")
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[top.object_id].zone)
        self.assertEqual(before["B"] - 1, len(game.state.players["B"].zones["library"]))
        self.resolve_top(game)
        self.cast_for(game, "A", bite, ["B"])
        self.resolve_top(game)
        self.assertEqual(life - 4, game.state.players["B"].life)
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual({seat: count - (4 if seat == "B" else 0) for seat, count in before.items()},
                         {seat: len(player.zones["library"]) for seat, player in game.state.players.items()})
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_exquisite_blood_uses_loss_even_when_simultaneous_lifelink_offsets_it(self):
        game = self.session(61341102)
        engine = game.engine
        source = self.add(engine, EXQUISITE, zone="hand")
        blocked = self.add(engine, "Giant Spider")
        unblocked = self.add(engine, "Grizzly Bears")
        blocker = self.add(engine, "Child of Night", seat="B")
        haste = self.add(engine, "Fervor")
        self.enter(game, source, {"C": 4, "B": 1}, blocked, unblocked, blocker, haste)
        before_a, before_b = (game.state.players[seat].life for seat in ("A", "B"))
        self.attack(game, [(blocked, "B"), (unblocked, "B")])
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        result = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: blocked.ref}})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda current: self.trigger_count(current, source) == 1)
        self.assertEqual(before_b, game.state.players["B"].life)
        self.assertEqual(before_a, game.state.players["A"].life)
        self.assertEqual("graveyard", game.state.cards[blocker.object_id].zone)
        self.resolve_top(game)
        self.assertEqual(before_a + 2, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_bloodthirsty_conqueror_queues_each_replacement_resolved_opponent_loss(self):
        game = self.session(61341103)
        engine = game.engine
        source = self.add(engine, CONQUEROR, zone="hand")
        damage = self.add(engine, "Sizzle", zone="hand")
        replacement = self.add(engine, "Furnace of Rath")
        self.enter(game, source, {"C": 5, "B": 2, "R": 1}, damage, replacement)
        self.assertIn("flying", engine._combat_keywords(game.state.cards[source.object_id]))
        before = {seat: player.life for seat, player in game.state.players.items()}
        self.cast_for(game, "A", damage)
        decision = self.resolve(game)
        self.assertEqual(("trigger.order", ["A"]), (decision.kind, decision.actors))
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
        self.assertEqual(3, len(rows))
        command = {"action_id": "order", "triggers": [row["id"] for row in rows]}
        state = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(state, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual({seat: amount - (6 if seat != "A" else 0) for seat, amount in before.items()},
                         {seat: player.life for seat, player in game.state.players.items()})
        self.assertEqual(3, self.trigger_count(game, source))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before["A"] + 18, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_omitting_committed_life_payment_is_killed_by_the_actual_mindcrank_witness(self):
        with patch("quorune.life_state.dispatch_life_payment_loss", return_value=None):
            with self.assertRaisesRegex(AssertionError, "Actual life payment must occur once"):
                self.test_actual_mindcrank_follows_paid_life_and_explicit_loss_without_milling_other_seats()
