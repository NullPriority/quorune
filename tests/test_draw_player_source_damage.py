"""Original damage triggers follow individual committed draws and their owner."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


VISIONS = "Spiteful Visions"
PARASITE = "Kederekt Parasite"


class DrawPlayerSourceDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "draw-player-source-damage.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/draw-player-source-damage-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Observe actual draws", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_own_the_independent_draw_and_exact_optional_condition(self):
        for name in (VISIONS, PARASITE):
            with self.subTest(card=name):
                admitted, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                rows = semantics.programs_for_oracle(card.oracle_id)
                damage, = (row for row in rows if row.event == "card.drawn")
                self.assertIsNone(damage.target_schema)
                leaf = {"op": "damage", "source": "$source", "target": "$context.player", "amount": 1}
                if name == VISIONS:
                    self.assertIsNone(damage.event_condition)
                    self.assertEqual([leaf], damage.effects)
                    additional, = (row for row in rows if row.event == "step.begin")
                    self.assertEqual([{"op": "draw", "player": "$context.player", "count": 1, "private": True}],
                                     additional.effects)
                else:
                    self.assertEqual([{"op": "offer_optional_effect", "player": "$controller", "effects": [leaf]}],
                                     damage.effects)
                    self.assertIn("intervening_condition", damage.coverage)
                    changed = replace(damage, event_condition=damage.event_condition["all"][0])
                    self.assertFalse(bind_semantic_program_runtime(
                        changed, capability_registry=self.registry, profile="commander_review",
                    )["strict"])

    def damage_triggers(self, game, source):
        return [item for item in game.state.stack
                if item.source_object_id == source.object_id and item.context.get("event") == "card.drawn"]

    def advance_until(self, game, predicate):
        for _ in range(len(game.state.players) + 1):
            self.until(game, lambda current: predicate(current)
                       or current.state.pending_decision.kind == "cleanup.discard")
            if predicate(game):
                return
            principal = game.pending_principals()[0]
            ctx = game.packet(principal, full=True)["decision"]["ctx"]
            result = game.act(principal, {
                "action_id": "discard", "cards": [row["id"] for row in ctx["hand"][:ctx["count"]]],
            })
            self.assertTrue(result.ok, result.summary)
        self.fail("Normal turn actions never reached the expected draw boundary")

    def test_actual_draw_step_has_one_committed_draw_before_its_independent_additional_draw(self):
        game = self.session(50401061)
        source = self.add(game.engine, VISIONS)
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        hand = len(game.state.players["B"].zones["hand"])
        life = game.state.players["B"].life
        self.advance_until(game, lambda state: state.state.active_player == "B" and state.state.step == "draw"
                           and self.trigger_count(state, source) == 2)
        self.assertEqual(hand + 1, len(game.state.players["B"].zones["hand"]))
        self.assertEqual(life, game.state.players["B"].life)
        self.assertEqual({"step.begin", "card.drawn"},
                         {item.context["event"] for item in game.state.stack if item.source_object_id == source.object_id})
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((hand + 2, life - 2), (len(game.state.players["B"].zones["hand"]), game.state.players["B"].life))
        self.replay(game, load=True)

    def test_actual_redirected_draws_damage_the_actual_drawing_controller_twice(self):
        game = self.session(12102061)
        source = self.add(game.engine, VISIONS)
        thief = self.add(game.engine, "Notion Thief")
        spell = self.add(game.engine, "Divination", seat="B", zone="hand")
        top = self.add(game.engine, "Forest", zone="library", ref="redirected-controller-draw")
        for card in (source, thief, spell):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"C": 2, "U": 1})
        self.checkpoint(game)
        hand = len(game.state.players["A"].zones["hand"])
        life_a, life_b = (game.state.players[seat].life for seat in "AB")
        self.cast_for(game, "B", spell)
        self.resolve_top(game)
        self.until(game, lambda current: len(self.damage_triggers(current, source)) == 2)
        self.assertEqual({"A"}, {item.context["player"] for item in self.damage_triggers(game, source)})
        self.assertEqual(hand + 2, len(game.state.players["A"].zones["hand"]))
        self.assertIn(top.object_id, game.state.players["A"].zones["hand"])
        self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((life_a - 2, life_b), (game.state.players["A"].life, game.state.players["B"].life))
        self.replay(game, load=True)

    def test_actual_lethal_draw_damage_leaves_no_second_damage_recipient(self):
        game = self.session(12102062)
        source = self.add(game.engine, VISIONS)
        spell = self.add(game.engine, "Divination", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, spell)
        game.state.players["B"].life = 1
        self.prepare(game, actor="B", active="B", mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", spell)
        self.resolve_top(game)
        self.until(game, lambda current: len(self.damage_triggers(current, source)) == 2)
        self.resolve_top(game)
        self.assertFalse(game.state.players["B"].in_game)
        self.assertEqual(1, len(self.damage_triggers(game, source)))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.players["B"].life)
        self.replay(game, load=True)

    def parasite_scene(self, seed, *, has_red=True, response=False):
        game = self.session(seed)
        source = self.add(game.engine, PARASITE)
        red = self.add(game.engine, "Goblin Piker") if has_red else None
        draw_spell = self.add(game.engine, "Divination", zone="hand", seat="B")
        hidden = self.add(game.engine, "Mountain", zone="library", seat="B", ref="opponent-private-draw")
        removal = self.add(game.engine, "Doom Blade", zone="hand", seat="B") if response else None
        for card in (source, draw_spell, *([removal] if removal is not None else [])):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"C": 3, "U": 1, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", draw_spell, reserve={"C": 1, "B": 1})
        self.resolve_top(game)
        self.assertIn(hidden.object_id, game.state.players["B"].zones["hand"])
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        if has_red:
            self.until(game, lambda current: len(self.damage_triggers(current, source)) == 2)
        return game, source, red, removal

    def test_actual_optional_damage_offers_each_draw_to_the_retained_controller(self):
        game, _, _, _ = self.parasite_scene(12102063)
        life = game.state.players["B"].life
        for choice in ("apply", "decline"):
            decision = self.resolve(game)
            self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
            self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
            command = {"action_id": "choose", "choice": choice}
            before = authoritative_state_hash(game.state)
            self.assertFalse(game.act("pilot:B", command).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
            game = self.replay(game, load=True)
            accepted = game.act("pilot:A", command)
            self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 1, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_red_permanent_is_required_at_detection_and_recheck(self):
        game, source, _, _ = self.parasite_scene(12102064, has_red=False)
        self.assertFalse(self.damage_triggers(game, source))
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)
        game, _, red, removal = self.parasite_scene(12102065, response=True)
        life = game.state.players["B"].life
        self.cast_for(game, "B", removal, [red.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[red.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life, game.state.players["B"].life)
        self.replay(game, load=True)
