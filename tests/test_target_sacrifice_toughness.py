"""The chosen creature's last public toughness supplies the printed life result."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.target_sacrifice_toughness import (
    TARGET_SACRIFICE_TOUGHNESS_CAPABILITY, TARGET_SACRIFICE_TOUGHNESS_FIELD,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class TargetSacrificeToughnessTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-sacrifice-toughness.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/target-selected-sacrifice-toughness-life-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Selected sacrifice toughness", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def reject(self, game, seat, action):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, action).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def choose(self, game, seat, card):
        decision = self.resolve(game)
        self.assertEqual(("choice.apnap", [seat]), (decision.kind, decision.actors))
        context = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertIn(card.ref, context["options"])
        command = {"action_id": "choose", "cards": [card.ref]}
        self.reject(game, "A", command)
        game = self.replay(game, load=True)
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        return game

    def test_actual_devour_flesh_uses_latest_toughness_and_gives_life_only_to_the_target(self):
        game = self.session(76251401)
        spell = self.add(game.engine, "Devour Flesh", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        spare = self.add(game.engine, "Llanowar Elves", seat="B")
        growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
        self.add(game.engine, "Forest", seat="B")
        life = {seat: game.state.players[seat].life for seat in "AB"}
        self.start(game, spell, growth, mana={"B": 1, "C": 1})
        self.cast(game, "A", spell, ["B"])
        self.cast(game, "B", growth, [bear.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(bear.object_id, "toughness"))
        game = self.choose(game, "B", bear)
        self.assertEqual(life["A"], game.state.players["A"].life)
        self.assertEqual(life["B"] + 5, game.state.players["B"].life)
        self.assertEqual(("graveyard", "battlefield"),
                         tuple(game.state.cards[card.object_id].zone for card in (bear, spare)))
        self.replay(game, load=True)

    def test_actual_tribute_life_uses_the_controller_and_counts_a_redirected_sacrifice(self):
        game = self.session(76251402)
        spell = self.add(game.engine, "Tribute to Hunger", zone="hand")
        redirect = self.add(game.engine, "Rest in Peace", seat="D")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        life = {seat: game.state.players[seat].life for seat in "AB"}
        self.start(game, spell, redirect, mana={"B": 1, "C": 2})
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in rows if row["id"] == "cast:" + spell.ref)
        self.reject(game, "A", {"action_id": offer["id"], "targets": ["A"], "pay": "auto"})
        self.cast(game, "A", spell, ["B"])
        game = self.choose(game, "B", bear)
        self.assertEqual("exile", game.state.cards[bear.object_id].zone)
        self.assertEqual(life["A"] + 2, game.state.players["A"].life)
        self.assertEqual(life["B"], game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_vapors_no_sacrifice_then_free_rebound_changes_player_and_gains_life(self):
        game = self.session(76251403)
        spell = self.add(game.engine, "Consuming Vapors", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", seat="C")
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"][5:]):
                if object_id != spell.object_id:
                    game.engine.move_card(object_id, "library", reason="Prepare a small starting hand")
        life = game.state.players["A"].life
        self.start(game, spell, mana={"B": 1, "C": 3})
        self.cast(game, "A", spell, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life, game.state.players["A"].life)
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.assertEqual(1, len(game.state.delayed_triggers))
        for _ in range(384):
            decision = game.state.pending_decision
            if decision is not None and decision.kind == "selection.exile_cast":
                break
            self.assertIsNotNone(decision)
            if decision.kind == "combat.attackers":
                response = {"action_id": "attack", "attackers": []}
            else:
                self.assertEqual("priority", decision.kind)
                response = {"action_id": "pass"}
            result = game.act(game.pending_principals()[0], response)
            self.assertTrue(result.ok, result.summary)
        else:
            self.fail("The real delayed Rebound trigger did not offer its cast")
        self.assertEqual(("A", "upkeep"), (game.state.active_player, game.state.step))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        command = {"action_id": "cast", "targets": ["C"]}
        self.reject(game, "B", command)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        game = self.choose(game, "C", bear)
        self.assertEqual(("graveyard", "graveyard"),
                         tuple(game.state.cards[card.object_id].zone for card in (spell, bear)))
        self.assertEqual(life + 2, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_complete_sources_preserve_the_distinct_recipient_and_target_authority(self):
        cases = (
            ("target", "any", register_card(self.db, self.registry, "Devour Flesh")),
            ("controller", "any", register_card(self.db, self.registry, "Consuming Vapors")),
            ("controller", "opponent", register_card(self.db, self.registry, "Tribute to Hunger")),
        )
        for recipient, relation, (report, semantics) in cases:
            with self.subTest(oracle_id=report["oracle_id"]):
                self.assertTrue(report["strict_capability_ready"], report)
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (p for p in semantics.programs_for_oracle(report["oracle_id"])
                            if TARGET_SACRIFICE_TOUGHNESS_CAPABILITY in p.capability_dependencies)
                self.assertEqual(recipient, program.effects[0][TARGET_SACRIFICE_TOUGHNESS_FIELD]["recipient"])
                self.assertEqual(relation, program.target_schema["player_relation"])
                deps = tuple(cap for cap in program.capability_dependencies if cap != TARGET_SACRIFICE_TOUGHNESS_CAPABILITY)
                absent = replace(program, capability_dependencies=deps,
                                 capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                result = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{TARGET_SACRIFICE_TOUGHNESS_CAPABILITY}",
                              result["blockers"])
        card = self.db.lookup("Devour Flesh", fuzzy=False)
        for old, new in (
            ("toughness", "power"), ("Target player", "Each player"),
            ("gains life", "draws cards"), ("toughness.", "toughness. Rewrite history."),
        ):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(
                changed, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        with patch("quorune.compiler.resolution_effect_templates.target_sacrifice_toughness_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
