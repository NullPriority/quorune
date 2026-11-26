from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.impulse_access_model import ImpulseAccessDuration
from quorune.mana_restrictions import (
    exile_play_mana_mode, exile_play_mana_spend_context, mana_restriction_allows, spell_mana_spend_context,
)
from quorune.mana_spending_model import ManaSpendingMode, ManaSpendingPolicy
from quorune.opponent_library_exile_model import each_opponent_top_exile_effect, validate_each_opponent_top_exile
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class OpposingExileIncarnationNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opposing-exile-incarnation.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json",
            "graveyard-mana-value-reanimation-cards.json",
            "activation-paid-creature-subtype-cards.json",
            "cast-target-copy-cards.json",
            "fixed-resolution-mana-cards.json",
            "defending-player-top-library-exile-cards.json",
            "controller-life-loss-draw-cards.json",
            "madcap-jester-cards.json",
            "opposing-exile-incarnation-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        Deck = DeckDefinition
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Opposing exile witness", [
            DeckEntry(commander, 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], [commander])
        cls.registry = load_default_capability_registry()

    def top(self, game, name, seat, ref):
        card = self.add(game.engine, name, zone="library", seat=seat, ref=ref)
        card.known_to.clear()
        card.revealed_to.clear()
        self.assertEqual(card.object_id, game.state.players[seat].zones["library"][-1])
        return card

    def actions(self, game, seat):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def action_ids(self, game, seat):
        return {row["id"] for row in self.actions(game, seat)}

    def command(self, game, seat, response):
        result = game.act("pilot:" + seat, response)
        self.assertTrue(result.ok, result.summary)
        return result

    def tap(self, game, card, seat="A"):
        prefix = "activate:" + card.ref + ":"
        action, = [key for key in self.action_ids(game, seat) if key.startswith(prefix)]
        self.command(game, seat, {"action_id": action})

    def order_pending_triggers(self, game, seat):
        self.assertEqual("trigger.order", game.state.pending_decision.kind)
        refs = [item.ref for batch in game.state.pending_trigger_batches for item in batch.items]
        self.command(game, seat, {"action_id": "order", "triggers": refs})

    def next_main(self, game, turn, seat):
        def main_reached(current):
            decision = current.state.pending_decision
            return (decision is not None and decision.kind == "priority" and current.state.turn_sequence > turn
                    and current.state.active_player == seat and current.state.phase == "precombat_main")

        def discard_reached(current):
            decision = current.state.pending_decision
            return decision is not None and decision.kind == "cleanup.discard"

        for _ in range(12):
            if main_reached(game):
                return
            current_turn = game.state.turn_sequence
            self.until(game, lambda current: main_reached(current) or discard_reached(current)
                       or current.state.turn_sequence > current_turn)
            if main_reached(game):
                return
            if not discard_reached(game):
                continue
            principal = game.pending_principals()[0]
            context = game.packet(principal, full=True)["decision"]["ctx"]
            cards = [row["id"] for row in context["hand"][:context["count"]]]
            result = game.act(principal, {"action_id": "discard", "cards": cards})
            self.assertTrue(result.ok, result.summary)
        self.fail("The next main phase was not reached")

    def test_printed_scopes_and_malformed_play_instruction(self):
        original = self.db.lookup("Brainstealer Dragon")
        registry = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        accepted = compile_oracle_card(original, **registry)
        self.assertEqual("exact", accepted.status, accepted.material_residuals)
        for current, drift in (
            ("your end step", "each end step"),
            ("any color to cast", "any type to cast"),
            ("nonland permanent", "creature"),
            ("under your control", "under its owner's control"),
            ("they lose life", "you lose life"),
        ):
            with self.subTest(drift=drift):
                self.assertIn(current, original.oracle_text)
                card = replace(original, oracle_text=original.oracle_text.replace(current, drift))
                result = compile_oracle_card(card, **registry)
                if drift == "you lose life":
                    self.assertEqual("exact", result.status)
                    entry, = [node for face in result.faces for node in face.nodes if node.event == "permanent.enter"]
                    self.assertEqual("$controller", entry.effects[0]["player"])
                    self.assertNotIn("life.loss.opposing_owner_entry_mana_value", entry.capability_dependencies)
                else:
                    self.assertNotEqual("exact", result.status)
        descriptor = each_opponent_top_exile_effect(grant_incarnation_play=True)
        self.assertTrue(validate_each_opponent_top_exile(descriptor))
        for drift in ({"schema_version": True}, {"schema_version": 3}, {"controller": "B"}, {"exile_face_down": True}):
            with self.subTest(drift=drift), self.assertRaises(ValueError):
                validate_each_opponent_top_exile({**descriptor, **drift})

    def test_color_policy_is_caster_scoped_and_preserves_restrictions(self):
        spell = spell_mana_spend_context("Instant")
        color_context = exile_play_mana_spend_context(spell, "A", color_only=True)
        type_context = exile_play_mana_spend_context(spell, "A")
        self.assertIsNone(exile_play_mana_mode(spell, "A"))
        self.assertIsNone(exile_play_mana_mode(color_context, "B"))
        self.assertIs(exile_play_mana_mode(color_context, "A"), ManaSpendingMode.ANY_COLOR)
        self.assertIs(exile_play_mana_mode(type_context, "A"), ManaSpendingMode.ANY_TYPE)
        self.assertIsNone(exile_play_mana_mode(color_context + ";exile-play-caster=42", "A"))
        self.assertFalse(mana_restriction_allows("creature_spell_only", color_context))
        creature = exile_play_mana_spend_context(spell_mana_spend_context("Creature — Cat"), "A", color_only=True)
        self.assertTrue(mana_restriction_allows("creature_spell_only", creature))
        policy = ManaSpendingPolicy(frozenset({exile_play_mana_mode(color_context, "A")}))
        self.assertIsNone(policy.allocate({"W": 2}, {"C": 1, "GENERIC": 1}))
        self.assertEqual(2, policy.allocate({"W": 2}, {"U": 1, "GENERIC": 1})["W"])

    def test_actual_end_step_grant_outlives_source_and_finite_any_type_grant(self):
        game = self.session(607222101)
        dragon = self.add(game.engine, "Brainstealer Dragon")
        jester = self.add(game.engine, "The Madcap Jester")
        plains = [self.add(game.engine, "Plains", ref=f"exile-scope-plains-{i}") for i in range(2)]
        spare_plains = self.add(game.engine, "Plains", ref="exile-scope-spare-plains")
        swords = self.add(game.engine, "Swords to Plowshares", zone="hand")
        island = self.add(game.engine, "Island", seat="B")
        bounce = self.add(game.engine, "Unsummon", zone="hand", seat="B")
        bear = self.top(game, "Grizzly Bears", "B", "exile-scope-bear")
        colorless = self.top(game, "Spatial Contortion", "D", "exile-scope-colorless")
        for object_id in tuple(game.state.players["C"].zones["library"]):
            game.engine.move_card(object_id, "graveyard", reason="Prepare empty opposing library")
        for card in (dragon, jester, bounce, bear, colorless, swords):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)

        self.until(game, lambda s: s.state.step == "end_step" and self.trigger_count(s, dragon) == 1)
        self.assertEqual("A", game.state.stack[-1].controller)
        self.tap(game, island, "B")
        self.cast_for(game, "B", bounce, [dragon.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[dragon.object_id].zone)
        for seat in "ABCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True), sort_keys=True)
            for card in (bear, colorless):
                self.assertNotIn(card.ref, packet)
                self.assertNotIn(card.object_id, packet)

        self.resolve_top(game)
        current_bear = game.state.cards[bear.object_id]
        original_exile_incarnation = current_bear.logical_object_id
        current_colorless = game.state.cards[colorless.object_id]
        self.assertEqual([("exile", "B", False), ("exile", "D", False)], [
            (card.zone, card.owner, card.face_down) for card in (current_bear, current_colorless)
        ])
        for seat in "ABCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True), sort_keys=True)
            self.assertIn(bear.ref, packet)
            self.assertIn(colorless.ref, packet)
        self.assertFalse(game.state.players["C"].zones["exile"])
        permission = game.engine._temporary_play_permission("A", current_bear)
        self.assertEqual("while_exiled", permission["duration"])
        self.assertIsNone(game.engine._temporary_play_permission("B", current_bear))

        self.order_pending_triggers(game, "A")
        self.assertIsNone(self.resolve(game))
        game = self.replay(game, load=True)
        initial = game.state.turn_sequence
        self.next_main(game, initial, "A")
        for land in plains:
            self.tap(game, land)
        self.assertEqual(2, game.state.players["A"].mana_pool["W"])
        self.assertIn("cast:" + colorless.ref, self.action_ids(game, "A"))
        grant = game.engine._temporary_play_permission("A", game.state.cards[colorless.object_id])
        self.assertEqual(ImpulseAccessDuration.END_OF_NEXT_TURN.value, grant["duration"])

        self.next_main(game, game.state.turn_sequence, "A")
        for land in plains:
            self.tap(game, land)
        self.assertEqual(2, game.state.players["A"].mana_pool["W"])
        current_colorless = game.state.cards[colorless.object_id]
        grant = game.engine._temporary_play_permission("A", current_colorless)
        self.assertEqual("while_exiled", grant["duration"])
        self.assertNotIn("cast:" + colorless.ref, self.action_ids(game, "A"))
        rejected = {
            "action_id": "cast:" + colorless.ref, "targets": [jester.ref],
            "pay": "manual", "payment": {"W": 2},
        }
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", rejected).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertIn("cast:" + bear.ref, self.action_ids(game, "A"))
        self.command(game, "A", {"action_id": "cast:" + bear.ref, "pay": "manual", "payment": {"W": 2}})
        item = game.state.stack[-1]
        self.assertEqual(("W",), tuple(item.mana_colors_spent))
        self.assertIsNone(self.resolve(game))
        current_bear = game.state.cards[bear.object_id]
        self.assertEqual(("battlefield", "B", "A"), (current_bear.zone, current_bear.owner, current_bear.controller))
        self.assertIsNone(game.engine._temporary_play_permission("A", current_bear))
        self.tap(game, spare_plains)
        self.cast_for(game, "A", swords, [current_bear.ref])
        self.resolve_top(game)
        later_exile = game.state.cards[bear.object_id]
        self.assertEqual("exile", later_exile.zone)
        self.assertNotEqual(original_exile_incarnation, later_exile.logical_object_id)
        self.assertIsNone(game.engine._temporary_play_permission("A", later_exile))
        self.assertNotIn("cast:" + bear.ref, self.action_ids(game, "A"))
        self.assertNotIn("cast:" + colorless.ref, self.action_ids(game, "D"))
        self.replay(game, load=True)

    def test_actual_opposing_entry_retains_owner_and_latest_departing_copy_mana_value(self):
        game = self.session(607222102)
        dragon = self.add(game.engine, "Brainstealer Dragon")
        owner_bear = self.add(game.engine, "Grizzly Bears", zone="graveyard", seat="B")
        own_bear = self.add(game.engine, "Grizzly Bears", zone="hand", ref="our-entry")
        hill = self.add(game.engine, "Hill Giant", seat="C")
        land = self.add(game.engine, "Dryad Arbor", zone="graveyard", seat="D")
        land_raise = self.add(game.engine, "Reanimate", zone="hand", ref="reanimate-land")
        raise_bear = self.add(game.engine, "Reanimate", zone="hand", ref="reanimate-foreign-bear")
        copy = self.add(game.engine, "Cytoshape", zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        blink_mana = self.add(game.engine, "Plains", ref="blink-plain")
        opponent_blink = self.add(game.engine, "Cloudshift", zone="hand", seat="B", ref="their-own-blink")
        opponent_creature = self.add(game.engine, "Hill Giant", seat="B", ref="their-own-creature")
        for card in (dragon, owner_bear, own_bear, hill, land, land_raise, raise_bear, copy, blink, opponent_blink):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        game.state.players["B"].mana_pool["W"] = 1
        self.prepare(game, mana={"B": 2, "W": 1, "G": 2, "U": 1, "C": 2})
        self.checkpoint(game)

        self.cast_for(game, "B", opponent_blink, [opponent_creature.ref])
        self.resolve_top(game)
        self.assertEqual(0, self.trigger_count(game, dragon))

        self.cast_for(game, "A", own_bear)
        self.resolve_top(game)
        self.assertEqual(0, self.trigger_count(game, dragon))
        self.cast_for(game, "A", land_raise, [land.ref])
        self.resolve_top(game)
        current_land = game.state.cards[land.object_id]
        self.assertEqual(("battlefield", "D", "A"), (current_land.zone, current_land.owner, current_land.controller))
        self.assertEqual(0, self.trigger_count(game, dragon))

        self.cast_for(game, "A", raise_bear, [owner_bear.ref])
        self.resolve_top(game)
        entrant = game.state.cards[owner_bear.object_id]
        self.assertEqual(("battlefield", "B", "A"), (entrant.zone, entrant.owner, entrant.controller))
        self.assertEqual(1, self.trigger_count(game, dragon))
        first = game.state.stack[-1]
        self.assertEqual(("A", "B", 2), (first.controller, first.context["owner"], first.context["mana_value"]))

        self.cast_for(game, "A", copy, [entrant.ref])
        decision = self.resolve(game)
        self.assertEqual(["A"], decision.actors)
        self.command(game, "A", {"action_id": "choose", "card": hill.ref})
        self.assertEqual(4, game.engine._effective_card_data(entrant)["mana_value"])
        self.tap(game, blink_mana)
        self.cast_for(game, "A", blink, [entrant.ref])
        self.resolve_top(game)
        returned = game.state.cards[entrant.object_id]
        self.assertNotEqual(first.context["card_object_identity"], returned.logical_object_id)
        self.assertEqual(2, game.engine._effective_card_data(returned)["mana_value"])
        self.assertEqual(2, self.trigger_count(game, dragon))
        self.assertEqual(40, game.state.players["B"].life)
        game = self.replay(game, load=True)

        self.resolve_top(game)
        self.assertEqual(38, game.state.players["B"].life)
        self.resolve_top(game)
        self.assertEqual({"A": 38, "B": 34, "C": 40, "D": 40},
                         {seat: player.life for seat, player in game.state.players.items()})
        self.replay(game, load=True)


if __name__ == "__main__":
    unittest.main()
