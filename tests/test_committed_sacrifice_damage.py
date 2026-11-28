"""The original legal target and actual committed sacrifices determine actual damage."""

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
from quorune.rules.any_number_sacrifice import ANY_NUMBER_SACRIFICE_FIELD, any_number_sacrifice_parts
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.committed_sacrifice_damage import COMMITTED_SACRIFICE_DAMAGE_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class CommittedSacrificeDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "committed-sacrifice-damage.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/committed-sacrifice-count-damage-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Committed sacrifice damage", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def select(self, game, options, chosen):
        decision = self.resolve(game)
        self.assertEqual(("choice.apnap", ["A"]), (decision.kind, decision.actors))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual(set(options), set(context["options"]))
        self.assertEqual((0, len(options)), (context["minimum"], context["count"]))
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "cards": list(chosen)}
        self.reject(game, "B", command)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        return game

    def zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def test_actual_landslide_uses_completed_redirected_mountains_and_its_announced_player(self):
        game = self.session(76291401)
        spell = self.add(game.engine, "Landslide", zone="hand")
        redirect = self.add(game.engine, "Rest in Peace", seat="D")
        mountains = tuple(self.add(game.engine, "Mountain", ref=f"landslide-mountain-{i}") for i in range(3))
        forest = self.add(game.engine, "Forest")
        foreign = self.add(game.engine, "Mountain", seat="B", ref="foreign-mountain")
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        life = game.state.players["B"].life
        self.start(game, spell, redirect)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in actions if row["id"] == "cast:" + spell.ref)
        self.reject(game, "A", {"action_id": offer["id"], "targets": [creature.ref], "pay": "auto"})
        self.cast(game, "A", spell, ["B"])
        game = self.select(game, [card.ref for card in mountains], [mountains[0].ref, mountains[1].ref])
        self.assertEqual(life - 2, game.state.players["B"].life)
        self.assertEqual(("exile", "exile", "battlefield"),
                         tuple(self.zone(game, card) for card in mountains))
        self.assertEqual(("battlefield", "battlefield"), (self.zone(game, forest), self.zone(game, foreign)))
        self.replay(game, load=True)

    def test_actual_last_ditch_keeps_the_sacrifice_if_its_valid_target_is_one_of_the_chosen_creatures(self):
        for target in ("player", "departing", "zero"):
            with self.subTest(target=target):
                game = self.session(76291410 + len(target))
                spell = self.add(game.engine, "Last-Ditch Effort", zone="hand")
                bear = self.add(game.engine, "Grizzly Bears")
                elf = self.add(game.engine, "Llanowar Elves")
                life = game.state.players["B"].life
                self.start(game, spell)
                self.cast(game, "A", spell, [bear.ref if target == "departing" else "B"])
                refs = [bear.ref, elf.ref]
                game = self.select(game, refs, [] if target == "zero" else refs)
                self.assertEqual(life - (2 if target == "player" else 0), game.state.players["B"].life)
                expected = "battlefield" if target == "zero" else "graveyard"
                self.assertEqual((expected, expected), (self.zone(game, bear), self.zone(game, elf)))
                damage = [event for event in game.state.events if event.code == "effect.damage"]
                self.assertEqual(int(target == "player"), len(damage))
                self.replay(game, load=True)

    def test_actual_illegal_only_target_never_offers_or_commits_the_sacrifice(self):
        game = self.session(76291416)
        spell = self.add(game.engine, "Last-Ditch Effort", zone="hand")
        own = self.add(game.engine, "Llanowar Elves")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.start(game, spell, bounce)
        self.cast(game, "A", spell, [target.ref])
        self.cast(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        self.assertEqual("hand", self.zone(game, target))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "battlefield"), (self.zone(game, spell), self.zone(game, own)))
        self.assertFalse(any(event.code == "choice.sacrifice" for event in game.state.events))
        self.replay(game, load=True)

    def test_complete_originals_require_the_printed_domains_targets_and_damage_count_authority(self):
        for domain, recipient, (report, semantics) in (
            ("mountains", "player_or_planeswalker", register_card(self.db, self.registry, "Landslide")),
            ("creatures", "damageable", register_card(self.db, self.registry, "Last-Ditch Effort")),
        ):
            with self.subTest(domain=domain):
                self.assertTrue(report["strict_capability_ready"], report)
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (p for p in semantics.programs_for_oracle(report["oracle_id"])
                            if COMMITTED_SACRIFICE_DAMAGE_CAPABILITY in p.capability_dependencies)
                effect, = program.effects
                selected_domain, result = any_number_sacrifice_parts(effect)
                self.assertEqual((domain, recipient), (selected_domain, result["recipient"]))
                self.assertEqual(recipient, program.target_schema["predicate"])
                self.assertEqual(3, effect[ANY_NUMBER_SACRIFICE_FIELD]["schema_version"])
                deps = tuple(cap for cap in program.capability_dependencies if cap != COMMITTED_SACRIFICE_DAMAGE_CAPABILITY)
                absent = replace(program, capability_dependencies=deps,
                                 capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{COMMITTED_SACRIFICE_DAMAGE_CAPABILITY}",
                              binding["blockers"])
        card = self.db.lookup("Landslide", fuzzy=False)
        for old, new in (("Mountains", "lands"), ("Landslide deals", "A creature deals"),
                         ("that much damage", "twice that much damage"),
                         ("planeswalker.", "planeswalker. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(
                changed, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        with patch("quorune.compiler.resolution_effect_templates.counted_sacrifice_damage_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
