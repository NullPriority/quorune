"""Native evidence for separate shields over the current controlled creature set."""

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
from quorune.regeneration import CONTROLLED_CREATURE_REGENERATION_CAPABILITY, controlled_creature_regeneration_effect
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


WRAP = "Wrap in Vigor"
HIERARCH = "Loxodon Hierarch"
PROFILE = "commander_review"


class ControlledCreatureRegenerationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-creature-regeneration.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controlled-creature-regeneration-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Controlled regeneration", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, **kwargs):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, **kwargs)
        self.checkpoint(game)

    def shields(self, game, cards):
        return [game.state.cards[card.object_id].regeneration_shields for card in cards]

    def zones(self, game, cards):
        return [game.state.cards[card.object_id].zone for card in cards]

    def test_actual_wrap_shields_current_shrouded_and_ordinary_creatures_before_late_arrival(self):
        game = self.session(61280501)
        spell = self.add(game.engine, WRAP, zone="hand")
        wrath = self.add(game.engine, "Day of Judgment", zone="hand", seat="B")
        late = self.add(game.engine, "Ambush Viper", zone="hand")
        own = (self.add(game.engine, "Grizzly Bears"), self.add(game.engine, "Deft Duelist"))
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="regeneration-foreign-bear")
        enchantment = self.add(game.engine, "Fervor")
        for i in range(4):
            self.add(game.engine, "Plains", seat="B", ref=f"regeneration-b-white-{i}")
        game.state.players["A"].mana_pool.update({"G": 2, "C": 2})
        self.start(game, spell, wrath, late, *own, actor="B", active="B")
        self.cast(game, "B", wrath)
        self.cast(game, "A", spell)
        self.assertEqual([], list(game.state.stack[-1].targets))
        self.resolve_top(game)
        self.assertEqual([1, 1], self.shields(game, own))
        self.assertEqual([0, 0], self.shields(game, (foreign, enchantment)))
        self.cast(game, "A", late)
        self.resolve_top(game)
        self.assertEqual([0], self.shields(game, (late,)))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["battlefield", "battlefield", "graveyard", "graveyard"], self.zones(game, (*own, late, foreign)))
        self.assertEqual([0, 0], self.shields(game, own))
        self.assertTrue(all(game.state.cards[card.object_id].tapped for card in own))
        self.replay(game, load=True)

    def test_actual_hierarch_keeps_life_trigger_and_pays_sacrifice_before_group_resolution(self):
        game = self.session(61280502)
        source = self.add(game.engine, HIERARCH, zone="hand")
        ally = self.add(game.engine, "Grizzly Bears")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="hierarch-foreign-bear")
        remove = self.add(game.engine, "Murder", seat="B", zone="hand")
        for i in range(3):
            self.add(game.engine, "Swamp", seat="B", ref=f"regeneration-b-black-{i}")
        life = game.state.players["A"].life
        self.start(game, source, remove, mana={"G": 4, "W": 4, "C": 2})
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life + 4, game.state.players["A"].life)
        self.priority(game, "A")
        offered, = (row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    if row["id"].startswith("activate:" + source.ref + ":"))
        command = {"action_id": offered["id"], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:B", command)
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        paid = game.act("pilot:A", command)
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(["graveyard"], self.zones(game, (source,)))
        self.assertEqual([0, 0, 0], self.shields(game, (source, ally, foreign)))
        self.assertIsNone(self.resolve(game))
        self.assertEqual([0, 1, 0], self.shields(game, (source, ally, foreign)))
        self.cast(game, "B", remove, [ally.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["graveyard", "battlefield", "battlefield"], self.zones(game, (source, ally, foreign)))
        self.assertTrue(game.state.cards[ally.object_id].tapped)
        self.assertEqual([0], self.shields(game, (ally,)))
        self.replay(game, load=True)

    def cast_modes(self, game, spell, modes, targets=()):
        self.priority(game, "A")
        offered, = (entry for entry in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    if entry["id"] == "cast:" + spell.ref)
        outcome = game.act("pilot:A", {"action_id": offered["id"], "modes": list(modes),
                                      "targets": list(targets), "pay": "auto"})
        self.assertTrue(outcome.ok, outcome.summary)

    def test_actual_golgari_charm_keeps_all_three_original_modes(self):
        for seed, mode in ((61280503, "mode_1"), (61280504, "mode_2"), (61280505, "mode_3")):
            with self.subTest(mode=mode):
                game = self.session(seed)
                spell = self.add(game.engine, "Golgari Charm", zone="hand")
                own = self.add(game.engine, "Grizzly Bears")
                opponent = self.add(game.engine, "Grizzly Bears", seat="B", ref="charm-foreign-bear")
                enchantment = self.add(game.engine, "Fervor", seat="B")
                self.start(game, spell, mana={"B": 1, "G": 1})
                targets = [enchantment.ref] if mode == "mode_2" else []
                self.cast_modes(game, spell, [mode], targets)
                self.assertIsNone(self.resolve(game))
                if mode == "mode_1":
                    self.assertEqual([1, 1], [game.engine._numeric_stat(card.object_id, "power")
                                              for card in (own, opponent)])
                if mode == "mode_2":
                    self.assertEqual(["graveyard"], self.zones(game, (enchantment,)))
                self.assertEqual([1 if mode == "mode_3" else 0, 0], self.shields(game, (own, opponent)))
                self.replay(game, load=True)

    def test_actual_entwined_wail_grants_shields_before_its_shared_lethal_damage(self):
        game = self.session(61280506)
        spell = self.add(game.engine, "Wail of the Nim", zone="hand")
        own = self.add(game.engine, "Deft Duelist")
        opponent = self.add(game.engine, "Ambush Viper", seat="B")
        life = {seat: player.life for seat, player in game.state.players.items()}
        self.start(game, spell, mana={"B": 2, "C": 2})
        self.cast_modes(game, spell, ["mode_1", "mode_2"])
        self.assertFalse(game.state.stack[-1].targets)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["battlefield", "graveyard"], self.zones(game, (own, opponent)))
        self.assertTrue(game.state.cards[own.object_id].tapped)
        self.assertEqual([0], self.shields(game, (own,)))
        self.assertEqual({seat: total - 1 for seat, total in life.items()},
                         {seat: player.life for seat, player in game.state.players.items()})
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_whole_originals_preserve_the_exact_targetless_group_and_runtime_authority(self):
        cases = (
            (WRAP, register_card(self.db, self.registry, "Wrap in Vigor")),
            (HIERARCH, register_card(self.db, self.registry, "Loxodon Hierarch")),
        )
        for name, (report, semantics) in cases:
            with self.subTest(name=name):
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                card = self.db.lookup(name, fuzzy=False)
                program, = (p for p in semantics.programs_for_oracle(card.oracle_id)
                            if CONTROLLED_CREATURE_REGENERATION_CAPABILITY in p.capability_dependencies)
                self.assertIsNone(program.target_schema)
                self.assertEqual((controlled_creature_regeneration_effect(),), tuple(program.effects))
                deps = tuple(cap for cap in program.capability_dependencies
                             if cap != CONTROLLED_CREATURE_REGENERATION_CAPABILITY)
                unbound = replace(program, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                runtime = bind_semantic_program_runtime(unbound, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(runtime["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{CONTROLLED_CREATURE_REGENERATION_CAPABILITY}",
                              runtime["blockers"])
        original = self.db.lookup(WRAP, fuzzy=False)
        for signature in (
            "Regenerate each creature an opponent controls.",
            "Regenerate each other creature you control.",
            "Regenerate each creature you control. Change the name of each card in your hand.",
        ):
            with self.subTest(signature=signature):
                changed = replace(original, oracle_text=signature)
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        with patch("quorune.compiler.regeneration_templates.controlled_creature_regeneration_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                original, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
