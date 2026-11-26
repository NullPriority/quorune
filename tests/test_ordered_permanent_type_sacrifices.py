"""Actual ordered type selection and three ordinary APNAP sacrifice stages."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.ordered_type_sacrifice import (
    ORDERED_TYPE_SACRIFICE_CAPABILITY, SACRIFICE_ORDER_CHOICES, ordered_type_sacrifice_effect,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class OrderedPermanentTypeSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "ordered-permanent-type-sacrifices.sqlite3"
        fixture = ROOT / "tests/fixtures/ordered-permanent-type-sacrifices-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Three ordered sacrifices", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def ready(self, game, *cards, active="A"):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active=active, mana={"C": 5, "R": 2})
        self.checkpoint(game)

    def add_many(self, game, seat, name, count):
        return tuple(self.add(game.engine, name, seat=seat, ref=f"rite-{seat}-{name}-{index}")
                     for index in range(count))

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def order(self, game, order):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        packet = json.dumps(game.packet("pilot:A", full=True))
        for available in SACRIFICE_ORDER_CHOICES:
            self.assertIn(available, packet)
        command = {"action_id": "choose", "order": order}
        self.reject(game, "B", command)
        self.reject(game, "A", {**command, "order": "artifact, artifact, land"})
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        return game

    def stage(self, game, seat, count):
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", [seat]), (pending.kind, pending.actors))
        context = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual(count, context["count"])
        return context

    def choose(self, game, seat, cards):
        result = game.act("pilot:" + seat, {"action_id": "choose", "cards": [card.ref for card in cards]})
        self.assertTrue(result.ok, result.summary)

    def zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def test_paid_off_turn_order_uses_active_player_first_and_commits_each_round_before_the_next(self):
        game = self.session(76354001)
        spell = self.add(game.engine, "Rite of Ruin", zone="hand")
        flash = self.add(game.engine, "Vedalken Orrery")
        dual = self.add(game.engine, "Leaden Myr")
        ring = self.add(game.engine, "Sol Ring", seat="B")
        death = self.add(game.engine, "Perilous Myr", seat="B")
        a_bears = self.add_many(game, "A", "Grizzly Bears", 3)
        b_bears = self.add_many(game, "B", "Grizzly Bears", 2)
        a_lands = self.add_many(game, "A", "Plains", 4)
        b_lands = self.add_many(game, "B", "Plains", 4)
        private = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="rite-unrelated-private")
        self.ready(game, spell, flash, dual, death, active="B")
        self.cast(game, "A", spell)
        self.assertEqual("B", game.state.active_player)
        game = self.order(game, "artifact, creature, land")
        context = self.stage(game, "B", 1)
        self.assertEqual({ring.ref, death.ref}, set(context["options"]))
        self.choose(game, "B", (ring,))
        context = self.stage(game, "A", 1)
        self.assertEqual([ring.ref], context["prior_public_choices"]["B"])
        self.assertEqual("battlefield", self.zone(game, ring))
        self.assertEqual("battlefield", self.zone(game, dual))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        game = self.replay(game, load=True)
        self.choose(game, "A", (dual,))
        context = self.stage(game, "B", 2)
        self.assertEqual({death.ref, *(card.ref for card in b_bears)}, set(context["options"]))
        self.assertEqual("graveyard", self.zone(game, ring))
        self.assertEqual("graveyard", self.zone(game, dual))
        self.choose(game, "B", (death, b_bears[0]))
        context = self.stage(game, "A", 2)
        self.assertNotIn(dual.ref, context["options"])
        self.assertEqual("battlefield", self.zone(game, death))
        self.reject(game, "A", {"action_id": "choose", "cards": [dual.ref, a_bears[0].ref]})
        self.choose(game, "A", a_bears[:2])
        self.stage(game, "B", 3)
        self.assertEqual("graveyard", self.zone(game, death))
        self.assertEqual("graveyard", self.zone(game, a_bears[0]))
        self.assertFalse(any(item.source_object_id == death.object_id for item in game.state.stack))
        self.choose(game, "B", b_lands[:3])
        self.stage(game, "A", 3)
        self.assertTrue(all(self.zone(game, card) == "battlefield" for card in b_lands))
        self.choose(game, "A", a_lands[:3])
        self.assertEqual("graveyard", self.zone(game, spell))
        for lands in (a_lands, b_lands):
            self.assertTrue(all(self.zone(game, card) == "graveyard" for card in lands[:3]))
            self.assertEqual("battlefield", self.zone(game, lands[3]))
        self.target(game, "B", ["D"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_paid_different_order_uses_the_available_quota_and_skips_empty_seats(self):
        game = self.session(76354002)
        spell = self.add(game.engine, "Rite of Ruin", zone="hand")
        land = self.add(game.engine, "Plains")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        creature = self.add(game.engine, "Grizzly Bears", seat="C")
        self.ready(game, spell)
        self.cast(game, "A", spell)
        game = self.order(game, "land, artifact, creature")
        for seat, card in (("A", land), ("B", artifact), ("C", creature)):
            context = self.stage(game, seat, 1)
            self.assertEqual([card.ref], context["options"])
            self.choose(game, seat, (card,))
        self.assertIsNone(self.resolve(game))
        self.assertTrue(all(self.zone(game, card) == "graveyard" for card in (spell, land, artifact, creature)))
        self.replay(game, load=True)

    def test_complete_original_requires_the_exact_order_and_independent_sacrifice_authority(self):
        cap = ORDERED_TYPE_SACRIFICE_CAPABILITY
        report, programs = register_card(self.db, self.registry, "Rite of Ruin")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                    if cap in row.capability_dependencies)
        self.assertEqual((ordered_type_sacrifice_effect(),), tuple(dict(value) for value in program.effects))
        self.assertIsNone(program.target_schema)
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        original = self.db.lookup("Rite of Ruin", fuzzy=False)
        for before, after in (("artifacts, creatures, and lands", "artifacts, creatures, and enchantments"),
                              ("Each player", "Each opponent"), ("sacrifices two", "sacrifices four"),
                              ("the third type.", "the third type. Rewrite history.")):
            with self.subTest(unsupported=after):
                candidate = replace(original, oracle_text=original.oracle_text.replace(before, after))
                lowered = compile_oracle_card(candidate, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", lowered.status)
