"""The resolving monarch condition keeps original target and private choice authority."""

from dataclasses import replace
import json
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
from quorune.rules.monarch_movement_replacements import MONARCH_MOVEMENT_CAPABILITY, monarch_movement_mode
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class MonarchMovementReplacementTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "monarch-movement.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/monarch-movement-replacements-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Monarch movement", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def throne(self, game):
        throne = self.add(game.engine, "Throne of the High City", seat="B")
        for index in range(4):
            self.add(game.engine, "Plains", seat="B", ref=f"throne-price-land-{index}")
        return throne

    def upkeep(self, game, source, throne):
        self.assert_closed(game, source)
        if throne is not None:
            self.assert_closed(game, throne)
        order = game.state.turn_order
        previous = order[order.index("A") - 1]
        for object_id in tuple(game.state.players[previous].zones["hand"][7:]):
            game.engine.move_card(object_id, "library", reason="Prepare the prior player's starting hand")
        game.state.last_normal_turn_player = previous
        game.state.monarch = "A"
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor=previous, active=previous)
        self.checkpoint(game)
        self.until(game, lambda session: session.state.active_player == "A" and session.state.step == "upkeep")
        self.assertEqual("A", game.state.monarch)

    def transfer(self, game, throne):
        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in rows if row["id"] == f"activate:{throne.ref}:ab2")
        result = game.act("pilot:B", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[throne.object_id].zone)
        self.resolve_top(game)
        self.assertEqual("B", game.state.monarch)

    def reject(self, game, seat, command):
        initial = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(initial, authoritative_state_hash(game.state))

    def test_actual_bounty_uses_the_current_title_for_its_private_land_or_creature_options(self):
        for changed in (True, False):
            with self.subTest(changed=changed):
                game = self.session(76311601 + changed)
                source = self.add(game.engine, "Court of Bounty")
                for object_id in tuple(game.state.players["A"].zones["hand"]):
                    game.engine.move_card(object_id, "library", reason="Prepare a private hand fixture")
                forest = self.add(game.engine, "Forest", zone="hand")
                creature = self.add(game.engine, "Grizzly Bears", zone="hand")
                other = self.add(game.engine, "Darksteel Relic", zone="hand")
                throne = self.throne(game) if changed else None
                self.upkeep(game, source, throne)
                self.assertEqual(1, self.trigger_count(game, source))
                if changed:
                    self.transfer(game, throne)
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
                options = game.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]["legal_refs"]
                self.assertEqual({forest.ref} if changed else {forest.ref, creature.ref}, set(options))
                for seat in "BCD":
                    view = game.packet("pilot:" + seat, full=True)
                    self.assertIsNone(view["decision"])
                    self.assertNotIn(other.ref, json.dumps(view))
                    self.assertNotIn(creature.ref, json.dumps(view))
                self.reject(game, "B", {"action_id": "choose", "card": forest.ref})
                forbidden = creature if changed else other
                self.reject(game, "A", {"action_id": "choose", "card": forbidden.ref})
                game = self.replay(game, load=True)
                chosen = forest if changed else creature
                result = game.act("pilot:A", {"action_id": "choose", "card": chosen.ref})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield", game.state.cards[chosen.object_id].zone)
                self.assertFalse(game.state.cards[chosen.object_id].tapped)
                self.assertFalse(any(event.code == "land.play" for event in game.state.events))
                self.replay(game, load=True)

    def test_actual_ardenvale_keeps_its_one_original_target_through_a_title_change(self):
        for changed in (True, False):
            with self.subTest(changed=changed):
                game = self.session(76311610 + changed)
                source = self.add(game.engine, "Court of Ardenvale")
                own = self.add(game.engine, "Darksteel Relic", zone="graveyard")
                costly = self.add(game.engine, "Shattergang Brothers", zone="graveyard")
                foreign = self.add(game.engine, "Darksteel Relic", seat="C", zone="graveyard", ref="foreign-ardenvale")
                throne = self.throne(game) if changed else None
                self.upkeep(game, source, throne)
                pending = self.resolve(game)
                self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
                for illegal in (costly, foreign):
                    self.reject(game, "A", {"action_id": "choose", "targets": [illegal.ref]})
                self.target(game, "A", [own.ref])
                self.assertEqual([own.ref], game.state.stack[-1].targets)
                if changed:
                    self.transfer(game, throne)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("hand" if changed else "battlefield", game.state.cards[own.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[costly.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
                self.replay(game, load=True)

    def test_complete_originals_require_the_same_target_or_private_choice_and_independent_authority(self):
        for mode, (report, semantics) in (
            ("hand_entry", register_card(self.db, self.registry, "Court of Bounty")),
            ("graveyard_destination", register_card(self.db, self.registry, "Court of Ardenvale")),
        ):
            with self.subTest(mode=mode):
                self.assertTrue(report["strict_capability_ready"], report)
                self.assertEqual("capability_closed", report["trust_basis"])
                programs = semantics.programs_for_oracle(report["oracle_id"])
                self.assertEqual(2, len(programs))
                program, = (p for p in programs if MONARCH_MOVEMENT_CAPABILITY in p.capability_dependencies)
                self.assertEqual(mode, monarch_movement_mode(program.effects[0]))
                deps = tuple(cap for cap in program.capability_dependencies if cap != MONARCH_MOVEMENT_CAPABILITY)
                absent = replace(program, capability_dependencies=deps,
                                 capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{MONARCH_MOVEMENT_CAPABILITY}", binding["blockers"])
        card = self.db.lookup("Court of Bounty", fuzzy=False)
        for old, new in (("creature or land", "enchantment or land"),
                         ("If you're the monarch", "If you're the initiative"),
                         ("battlefield.", "battlefield. Rewrite history.")):
            altered = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(
                altered, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        with patch("quorune.compiler.trigger_result_templates.monarch_movement_replacement_result", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
