"""Exact public printed type choices precede a simultaneous sacrifice."""

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
from quorune.rules.keep_permanents_by_type import KEEP_PERMANENTS_BY_TYPE_CAPABILITY, KeepPermanentsByType
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class KeepPermanentsByTypeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "keep-permanents-by-type.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/keep-permanents-by-type-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Keep by permanent type", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, source, *, mana):
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)
        self.cast(game, "A", source)

    def zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def reject(self, game, seat, refs):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, {"action_id": "choose", "cards": list(refs)}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def choice(self, game, chooser, affected, category, options):
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", [chooser]), (decision.kind, decision.actors))
        self.assertFalse(game.state.stack[-1].targets)
        packet = game.packet("pilot:" + chooser, full=True)["decision"]
        context = packet["ctx"]
        self.assertEqual((affected, category), (context["affected"], context["category"]))
        choose, = (row for row in packet["legal_actions"] if row["id"] == "choose")
        self.assertEqual(set(options), set(choose["choice_schema"]["legal_refs"]))
        return context["prior_public_choices"]

    def keep(self, game, chooser, ref):
        result = game.act("pilot:" + chooser, {"action_id": "choose", "cards": [ref]})
        self.assertTrue(result.ok, result.summary)

    def test_actual_gearhulk_same_permanent_fills_two_slots_and_lands_never_enter_the_domains(self):
        game = self.session(76301501)
        source = self.add(game.engine, "Cataclysmic Gearhulk", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        elf = self.add(game.engine, "Llanowar Elves", seat="B")
        spared = self.add(game.engine, "Grizzly Bears", seat="B", ref="gearhulk-keep-bear")
        land = self.add(game.engine, "Darksteel Citadel", seat="B")
        enchantment = self.add(game.engine, "Omen of the Sea", seat="C")
        self.start(game, source, mana={"C": 3, "W": 2})
        self.resolve_top(game)
        gearhulk = game.state.cards[source.object_id].ref
        self.choice(game, "A", "A", "artifact", [gearhulk])
        self.keep(game, "A", gearhulk)
        self.choice(game, "A", "A", "creature", [gearhulk, bear.ref])
        game = self.replay(game, load=True)
        self.keep(game, "A", gearhulk)
        choices = self.choice(game, "B", "B", "creature", [elf.ref, spared.ref])
        self.assertEqual([gearhulk, gearhulk], [entry["permanent"] for entry in choices])
        self.reject(game, "A", [spared.ref])
        self.reject(game, "B", [land.ref])
        self.keep(game, "B", spared.ref)
        self.choice(game, "C", "C", "enchantment", [enchantment.ref])
        self.assertEqual(("battlefield", "battlefield"), (self.zone(game, bear), self.zone(game, elf)))
        self.keep(game, "C", enchantment.ref)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "graveyard"), (self.zone(game, bear), self.zone(game, elf)))
        self.assertEqual(("battlefield",) * 4, tuple(self.zone(game, c) for c in (source, spared, land, enchantment)))
        self.assertIn("vigilance", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.replay(game, load=True)

    def test_actual_blinked_gearhulk_starts_at_the_active_player_even_when_the_controller_is_different(self):
        game = self.session(76301504)
        source = self.add(game.engine, "Cataclysmic Gearhulk")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        self.assert_closed(game, source)
        self.assert_closed(game, blink)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="A", active="B", mana={"W": 1})
        self.checkpoint(game)
        self.cast(game, "A", blink, [source.ref])
        self.resolve_top(game)
        self.assertEqual([], self.choice(game, "B", "B", "creature", [creature.ref]))
        self.keep(game, "B", creature.ref)
        gearhulk = game.state.cards[source.object_id].ref
        prior = self.choice(game, "A", "A", "artifact", [gearhulk])
        self.assertEqual([creature.ref], [row["permanent"] for row in prior])
        self.keep(game, "A", gearhulk)
        self.choice(game, "A", "A", "creature", [gearhulk])
        self.keep(game, "A", gearhulk)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "battlefield"), (self.zone(game, source), self.zone(game, creature)))
        self.replay(game, load=True)

    def test_actual_tragic_controller_can_keep_foreign_artifact_land_but_other_nonland_is_sacrificed(self):
        game = self.session(76301502)
        spell = self.add(game.engine, "Tragic Arrogance", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        land = self.add(game.engine, "Darksteel Citadel", seat="B")
        relic = self.add(game.engine, "Darksteel Relic", seat="B")
        self.start(game, spell, mana={"C": 3, "W": 2})
        self.choice(game, "A", "A", "creature", [bear.ref])
        self.keep(game, "A", bear.ref)
        self.choice(game, "A", "B", "artifact", [land.ref, relic.ref])
        self.reject(game, "B", [land.ref])
        game = self.replay(game, load=True)
        self.keep(game, "A", land.ref)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "battlefield", "graveyard"),
                         tuple(self.zone(game, c) for c in (bear, land, relic)))
        self.replay(game, load=True)

    def test_actual_cataclysm_includes_one_land_and_folds_the_same_artifact_creature_into_one_kept_object(self):
        game = self.session(76301503)
        spell = self.add(game.engine, "Cataclysm", zone="hand")
        plain = self.add(game.engine, "Plains")
        forest = self.add(game.engine, "Forest")
        myr = self.add(game.engine, "Darksteel Myr", seat="B")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        remote_land = self.add(game.engine, "Forest", seat="B", ref="cataclysm-foreign-forest")
        self.start(game, spell, mana={"C": 2, "W": 2})
        self.choice(game, "A", "A", "land", [plain.ref, forest.ref])
        self.keep(game, "A", plain.ref)
        self.choice(game, "B", "B", "artifact", [myr.ref])
        self.keep(game, "B", myr.ref)
        self.choice(game, "B", "B", "creature", [myr.ref, bear.ref])
        self.keep(game, "B", myr.ref)
        self.choice(game, "B", "B", "land", [remote_land.ref])
        self.assertEqual(("battlefield", "battlefield"), (self.zone(game, forest), self.zone(game, bear)))
        self.keep(game, "B", remote_land.ref)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "graveyard"), (self.zone(game, forest), self.zone(game, bear)))
        self.assertEqual(("battlefield",) * 3, tuple(self.zone(game, c) for c in (plain, myr, remote_land)))
        self.replay(game, load=True)

    def test_full_originals_keep_distinct_roles_domains_and_independent_authority(self):
        for mode, (report, semantics) in (
            ("nonland_self_choice", register_card(self.db, self.registry, "Cataclysmic Gearhulk")),
            ("nonland_controller_choice", register_card(self.db, self.registry, "Tragic Arrogance")),
            ("land_self_choice", register_card(self.db, self.registry, "Cataclysm")),
        ):
            with self.subTest(mode=mode):
                self.assertTrue(report["strict_capability_ready"], report)
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (p for p in semantics.programs_for_oracle(report["oracle_id"])
                            if KEEP_PERMANENTS_BY_TYPE_CAPABILITY in p.capability_dependencies)
                self.assertEqual(mode, KeepPermanentsByType.from_effect(program.effects[0]).mode)
                self.assertIsNone(program.target_schema)
                deps = tuple(cap for cap in program.capability_dependencies if cap != KEEP_PERMANENTS_BY_TYPE_CAPABILITY)
                absent = replace(program, capability_dependencies=deps,
                                 capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{KEEP_PERMANENTS_BY_TYPE_CAPABILITY}",
                              binding["blockers"])
        card = self.db.lookup("Cataclysm", fuzzy=False)
        for old, new in (("a land", "a battle"), ("Each player chooses", "Each opponent chooses"),
                         ("sacrifices the rest.", "sacrifices the rest. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(
                changed, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        with patch("quorune.compiler.resolution_effect_templates.keep_by_type_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
