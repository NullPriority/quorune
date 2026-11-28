"""Actual monarch tenure changes feed an ordinary triggered sacrifice."""

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
from quorune.rules.monarch_transition import MONARCH_BECAME_CAPABILITY, monarch_became_runtime_dependencies
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class MonarchTransitionSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "monarch-transition-sacrifice.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/monarch-transition-sacrifice-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Monarch transition", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def sacrifice(self, game, seat, card):
        choice = self.resolve(game)
        self.assertEqual(("choice.apnap", [seat]), (choice.kind, choice.actors))
        context = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertIn(card.ref, context["options"])
        command = {"action_id": "choose", "cards": [card.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def test_actual_lich_entry_changes_monarch_then_chooses_the_ordinary_player_target(self):
        game = self.session(76281201)
        lich = self.add(game.engine, "Custodi Lich", zone="hand")
        bear = self.add(game.engine, "Darksteel Myr", seat="B")
        self.start(game, lich, mana={"B": 2, "C": 3})
        self.assertIsNone(game.state.monarch)
        self.cast(game, "A", lich)
        decision = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (decision.kind, decision.actors))
        self.assertEqual("A", game.state.monarch)
        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
        game = self.replay(game, load=True)
        self.target(game, "A", ["B"])
        self.sacrifice(game, "B", bear)

    def test_actual_lich_entry_while_already_monarch_does_not_repeat_the_occurrence(self):
        game = self.session(76281202)
        sentinels = self.add(game.engine, "Palace Sentinels", zone="hand")
        lich = self.add(game.engine, "Custodi Lich", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        for index in (1, 2):
            self.add(game.engine, "Swamp", ref=f"lich-reserved-swamp-{index}")
        self.start(game, sentinels, lich, mana={"W": 1, "C": 6})
        self.cast(game, "A", sentinels)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", game.state.monarch)
        self.cast(game, "A", lich)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, sum(event.code == "monarch.change" for event in game.state.events))
        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
        self.replay(game, load=True)

    def test_actual_combat_and_departure_transfers_feed_the_same_non_former_player_target(self):
        for reason in ("combat", "departure"):
            with self.subTest(reason=reason):
                game = self.session(76281210 + len(reason))
                lich = self.add(game.engine, "Custodi Lich")
                bear = self.add(game.engine, "Grizzly Bears")
                chosen = self.add(game.engine, "Darksteel Myr", seat="C")
                bear.acquired_control_turn_count = -1
                game.state.monarch = "B"
                self.start(game, lich)
                if reason == "combat":
                    self.attack(game, [(bear, "B")])
                else:
                    self.priority(game, "B")
                    result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(result.ok, result.summary)
                for _ in range(144):
                    decision = game.state.pending_decision
                    if decision is not None and decision.kind == "semantic.target":
                        break
                    self.assertIsNotNone(decision)
                    if decision.kind == "combat.blockers":
                        command = {"action_id": "block", "blocks": []}
                    else:
                        self.assertEqual("priority", decision.kind)
                        command = {"action_id": "pass"}
                    result = game.act(game.pending_principals()[0], command)
                    self.assertTrue(result.ok, result.summary)
                else:
                    self.fail("The real title transfer did not produce its ordinary target")
                self.assertEqual("A", game.state.monarch)
                self.assertEqual(reason == "combat", game.state.players["B"].in_game)
                self.assertEqual(["A"], game.state.pending_decision.actors)
                self.target(game, "A", ["C"])
                self.sacrifice(game, "C", chosen)

    def test_complete_source_preserves_separate_entry_and_new_title_authority(self):
        card = self.db.lookup("Custodi Lich", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Custodi Lich")
        self.assertTrue(report["strict_capability_ready"], report)
        self.assertEqual("capability_closed", report["trust_basis"])
        all_programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(2, len(all_programs))
        program, = (p for p in all_programs if MONARCH_BECAME_CAPABILITY in p.capability_dependencies)
        self.assertIn(MONARCH_BECAME_CAPABILITY, monarch_became_runtime_dependencies(program))
        deps = tuple(cap for cap in program.capability_dependencies if cap != MONARCH_BECAME_CAPABILITY)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{MONARCH_BECAME_CAPABILITY}",
                      binding["blockers"])
        for old, new in (("you become the monarch, target", "a player becomes the monarch, target"),
                         ("Target player", "Target opponent"),
                         ("of their choice.", "of their choice. Rewrite history.")):
            if old not in card.oracle_text:
                old, new = old.casefold(), new.casefold()
            altered = replace(card, oracle_text=card.oracle_text.replace(old, new))
            with self.subTest(new=new):
                self.assertNotEqual("exact", compile_oracle_card(
                    altered, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        with patch("quorune.compiler.tap_state_event_bindings.monarch_became_binding_spec", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
