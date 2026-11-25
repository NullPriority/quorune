"""Exercise actual printed choices across multiple opponents."""

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
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.collective_source_damage import (
    COLLECTIVE_DECLINE_DESTRUCTION_CAPABILITY,
    COLLECTIVE_SOURCE_DAMAGE_CAPABILITY,
)
from scripts.build_test_database import build_fixture_database


class CollectiveOptionalSourceDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "collective-damage.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "greatest-power-affected-sacrifice-cards.json",
            fixtures / "shared-type-library-cards.json",
            fixtures / "ability-source-counter-cards.json",
            fixtures / "collective-optional-source-damage-cards.json",
            fixtures / "collective-decline-destruction-cards.json",
            fixtures / "regeneration-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        ruler = "Generic Bound Commander"
        cls.deck = DeckDefinition("Collective optional damage", [
            DeckEntry(ruler, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [ruler])

    def scene(self, seed, name):
        game = self.session(seed)
        source = self.add(game.engine, name, zone="hand", ref="COLLECTIVE-DAMAGE-SOURCE")
        self.assert_closed(game, source)
        hidden = self.add(game.engine, "Generic Bound Plains", seat="B", zone="hand", ref="UNRELATED-PRIVATE-HAND")
        game.state.config.semantic_policy = "trusted_only"
        return game, source, hidden

    def check_actor(self, game, seat, hidden):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", [seat]), (pending.kind, pending.actors))
        schema = pending.payload_by_actor[seat]["legal_actions"][0]["choice_schema"]
        self.assertEqual(["accept_damage", "decline_damage"], schema["legal_values"])
        for other in set("ABCD") - {seat}:
            packet = game.packet("pilot:" + other, full=True)
            self.assertIsNone(packet["decision"])
            if other != "B":
                self.assertNotIn(hidden.ref, json.dumps(packet))

    def choose_damage(self, game, seat, answer, hidden):
        self.check_actor(game, seat, hidden)
        invalid = next(other for other in "ABCD" if other != seat)
        previous = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "choice": answer}
        self.assertFalse(game.act("pilot:" + invalid, command).ok)
        self.assertEqual(previous, authoritative_state_hash(game.state))
        choice = game.act("pilot:" + seat, command)
        self.assertTrue(choice.ok, choice.summary)

    def test_browbeat_collects_each_acceptance_before_one_damage_batch(self):
        game, spell, hidden = self.scene(2072981, "Browbeat")
        timing = self.add(game.engine, "Vedalken Orrery", ref="OFF-TURN-CHOICES")
        self.assert_closed(game, timing)
        self.prepare(game, actor="A", active="C", mana={"C": 2, "R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["D"])
        self.assertIsNotNone(self.resolve(game))
        life = {seat: game.state.players[seat].life for seat in "ABCD"}
        draw = tuple(game.state.players["D"].zones["hand"])
        self.choose_damage(game, "C", "accept_damage", hidden)
        self.choose_damage(game, "D", "decline_damage", hidden)
        self.assertEqual(life, {seat: game.state.players[seat].life for seat in "ABCD"})
        game = self.replay(game, load=True)
        self.choose_damage(game, "A", "accept_damage", hidden)
        self.assertEqual(life, {seat: game.state.players[seat].life for seat in "ABCD"})
        self.choose_damage(game, "B", "decline_damage", hidden)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({seat: life[seat] - (5 if seat in "AC" else 0) for seat in "ABCD"},
                         {seat: game.state.players[seat].life for seat in "ABCD"})
        self.assertEqual(draw, tuple(game.state.players["D"].zones["hand"]))
        self.replay(game, load=True)

    def test_book_burning_follows_the_original_target_when_all_players_decline(self):
        game, spell, hidden = self.scene(2072982, "Book Burning")
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["C"])
        self.assertIsNotNone(self.resolve(game))
        piles = {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        for seat in "ABCD":
            self.choose_damage(game, seat, "decline_damage", hidden)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(piles["C"][:-6], tuple(game.state.players["C"].zones["library"]))
        self.assertTrue(set(piles["C"][-6:]) <= set(game.state.players["C"].zones["graveyard"]))
        for seat in "ABD":
            self.assertEqual(piles[seat], tuple(game.state.players[seat].zones["library"]))
        self.replay(game, load=True)

    def test_vexing_devil_sacrifices_after_a_fully_prevented_accepted_choice(self):
        game, source, hidden = self.scene(2072983, "Vexing Devil")
        prevention = self.add(game.engine, "The Wanderer", seat="B", ref="OPTIONAL-DAMAGE-PREVENTION")
        self.assert_closed(game, prevention)
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.assertIsNotNone(self.resolve(game))
        before = game.state.players["B"].life
        self.choose_damage(game, "B", "accept_damage", hidden)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.choose_damage(game, "C", "decline_damage", hidden)
        self.choose_damage(game, "D", "decline_damage", hidden)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, game.state.players["B"].life)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_longhorn_firebeast_damage_uses_the_original_source_after_it_leaves(self):
        game, source, hidden = self.scene(2072984, "Longhorn Firebeast")
        bounce = self.add(game.engine, "Unsummon", zone="hand", ref="REMOVE-THE-ORIGINAL")
        self.assert_closed(game, bounce)
        self.prepare(game, mana={"C": 2, "R": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"U": 1})
        self.resolve_top(game)
        self.cast_for(game, "A", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertIsNotNone(self.resolve(game))
        before = game.state.players["B"].life
        self.choose_damage(game, "B", "accept_damage", hidden)
        self.choose_damage(game, "C", "decline_damage", hidden)
        self.choose_damage(game, "D", "decline_damage", hidden)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before - 5, game.state.players["B"].life)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_collective_gate_rejects_changed_audience_source_or_followup(self):
        records = [self.db.lookup(name, fuzzy=False) for name in
                   ("Browbeat", "Book Burning", "Vexing Devil", "Longhorn Firebeast")]
        def bind(card):
            return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        for card in records:
            ir = bind(card)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            self.assertTrue(any(COLLECTIVE_SOURCE_DAMAGE_CAPABILITY in node.capability_dependencies
                                for face in ir.faces for node in face.nodes))
        for card, old, new in (
            (records[0], "Browbeat deal", "an unrelated object deal"),
            (records[0], "If no one does", "If all players do"),
            (records[1], "target player mills", "you mill"),
            (records[2], "any opponent", "each opponent must"),
            (records[3], "sacrifice this creature", "draw two cards"),
        ):
            altered = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", bind(altered).status)
        with patch("quorune.compiler.collective_source_damage_templates.collective_source_damage_instruction", return_value={"op":"noop"}):
            self.assertTrue(all(bind(card).status!="exact" for card in records))

    def test_all_decline_destroys_the_current_set_with_regeneration_prohibited(self):
        game, spell, hidden = self.scene(608020531, "Generic Decline Destruction")
        shield = self.add(game.engine, "Death Ward", zone="hand", ref="SHIELD-CAST")
        shielded = self.add(game.engine, "Drudge Skeletons", seat="B", ref="SHIELDED-CREATURE")
        unshielded = self.add(game.engine, "Drudge Skeletons", seat="D", ref="OTHER-CREATURE")
        indestructible = self.add(game.engine, "Generic Indestructible Sentinel", seat="C", ref="INDESTRUCTIBLE-CREATURE")
        land = self.add(game.engine, "Generic Bound Plains", seat="B", ref="NONCREATURE-LAND")
        for card in (shield, shielded, unshielded, indestructible, land):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", mana={"C": 3, "R": 3, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", shield, [shielded.ref])
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[shielded.object_id].regeneration_shields)
        self.cast_for(game, "A", spell)
        self.assertIsNotNone(self.resolve(game))
        self.choose_damage(game, "A", "decline_damage", hidden)
        packet = game.packet("pilot:B", full=True)
        self.assertIn("A chose decline_damage", str(packet))
        self.choose_damage(game, "B", "decline_damage", hidden)
        game = self.replay(game, load=True)
        self.choose_damage(game, "C", "decline_damage", hidden)
        self.assertEqual("battlefield", game.state.cards[shielded.object_id].zone)
        self.choose_damage(game, "D", "decline_damage", hidden)
        self.assertIsNone(self.resolve(game))
        for card in (shielded, unshielded):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        for card in (indestructible, land):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def test_prevented_accepted_damage_skips_the_destruction(self):
        game, spell, hidden = self.scene(608020532, "Generic Decline Destruction")
        prevention = self.add(game.engine, "The Wanderer", seat="B", ref="PREVENT-DAMAGE")
        creature = self.add(game.engine, "Drudge Skeletons", seat="C", ref="UNDAMAGED-CREATURE")
        self.assert_closed(game, prevention)
        self.assert_closed(game, creature)
        self.prepare(game, actor="A", mana={"C": 2, "R": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.assertIsNotNone(self.resolve(game))
        life = {seat: player.life for seat,player in game.state.players.items()}
        for seat in "ABCD":
            self.choose_damage(game, seat, "accept_damage" if seat=="B" else "decline_damage", hidden)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life, {seat: player.life for seat,player in game.state.players.items()})
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.replay(game, load=True)

    def test_destroy_decline_requires_the_closed_result_owner(self):
        card = self.db.lookup("Generic Decline Destruction", fuzzy=False)

        def compile_card(record):
            return compile_oracle_card(
                record, capability_registry=self.registry, capability_profile="commander_review",
            )

        admitted = compile_card(card)
        self.assertEqual("exact", admitted.status, admitted.material_residuals)
        node, = [node for face in admitted.faces for node in face.nodes
                 if COLLECTIVE_DECLINE_DESTRUCTION_CAPABILITY in node.capability_dependencies]
        self.assertIsNone(node.target_schema)
        self.assertIn("permanent.destroy.fixed_set", node.capability_dependencies)
        self.assertIn("permanent.destroy.regeneration_prohibition", node.capability_dependencies)
        for old, new in (
            ("all creatures", "all artifacts"),
            ("Those creatures cannot be regenerated.", ""),
            ("If no one does", "If any player does"),
        ):
            altered = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_card(altered).status)
        with patch("quorune.compiler.collective_source_damage_templates.mass_destruction_effect_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_card(card).status)
