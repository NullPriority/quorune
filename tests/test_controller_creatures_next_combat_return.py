"""The original creature exile set returns at the next combat with temporary haste."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as untap_fixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_exile_return_model import LINKED_PUBLIC_SET_NEXT_COMBAT_VERSION
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class ControllerCreaturesNextCombatReturnTests(InheritedNativeFixture, unittest.TestCase):
    playing = untap_fixture.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-creatures-next-combat-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controller-creatures-next-combat-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Next actual combat", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_both_anthems_and_the_paid_linked_activation(self):
        admitted, semantics = register_card(self.db, self.registry, "Legion's Initiative")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup("Legion's Initiative", fuzzy=False)
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(2, sum(program.event == "characteristics.evaluate" for program in programs))
        ability, = (program for program in programs if program.event == "activate")
        self.assertTrue(ability.cost_schema["exile_source"])
        self.assertEqual((1, 1), (ability.cost_schema["mana"]["R"], ability.cost_schema["mana"]["W"]))
        exile, returned = ability.effects
        self.assertEqual("$controller", exile["cards"])
        self.assertIsNone(ability.target_schema)
        self.assertEqual(exile["spec"], returned["spec"])
        self.assertEqual(exile["binding_id"], returned["binding_id"])
        self.assertEqual((LINKED_PUBLIC_SET_NEXT_COMBAT_VERSION, "next_combat", ["Haste"]),
                         (returned["spec"]["schema_version"], returned["spec"]["timing"], returned["spec"]["keywords"]))
        invalid = {**exile, "cards": "$active"}
        self.assertFalse(bind_semantic_program_runtime(
            replace(ability, effects=[invalid, returned]), capability_registry=self.registry, profile="commander_review",
        )["strict"])
        altered = replace(card, oracle_text=card.oracle_text.replace("the next combat", "your next combat"))
        self.assertNotEqual("exact", compile_oracle_card(
            altered, capability_registry=self.registry, capability_profile="commander_review",
        ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def activate(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = (row for row in actions if row["id"].startswith("activate:" + source.ref + ":"))
        result = game.act("pilot:A", {"action_id": offered["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", self.current(game, source).zone)

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, name) for name in ("power", "toughness"))

    def beginning_combat_return(self, game, seat):
        self.until(game, lambda current: current.state.active_player == seat and current.state.step == "beginning_combat"
                   and any(item.label == "Return linked exiled objects" for item in current.state.stack))
        item, = (item for item in game.state.stack if item.label == "Return linked exiled objects")
        self.assertEqual("A", item.controller)
        self.assertIsNone(self.resolve(game))

    def test_actual_cost_and_anthems_then_owner_return_lets_the_opponent_attack_with_haste(self):
        game = self.playing(50726001)
        source = self.add(game.engine, "Legion's Initiative")
        donate = self.add(game.engine, "Donate", seat="B", zone="hand")
        swiftblade = self.add(game.engine, "Boros Swiftblade")
        borrowed = self.add(game.engine, "Grizzly Bears", seat="B")
        for card in (source, donate, swiftblade):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"C": 2, "U": 1})
        game.state.players["A"].mana_pool.update({"R": 1, "W": 1})
        self.checkpoint(game)
        self.assertEqual((2, 3), self.stats(game, swiftblade))
        self.cast_for(game, "B", donate, ["A", borrowed.ref])
        self.resolve_top(game)
        self.assertEqual(("A", "B"), (self.current(game, borrowed).controller, self.current(game, borrowed).owner))
        self.activate(game, source)
        self.assertEqual((1, 2), self.stats(game, swiftblade))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["exile", "exile"], [self.current(game, card).zone for card in (swiftblade, borrowed)])
        game = self.replay(game, load=True)
        self.beginning_combat_return(game, "B")
        self.assertEqual([("battlefield", "A"), ("battlefield", "B")],
                         [(self.current(game, card).zone, self.current(game, card).controller)
                          for card in (swiftblade, borrowed)])
        for card in (swiftblade, borrowed):
            self.assertIn("haste", game.engine._combat_keywords(self.current(game, card)))
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers")
        attack = game.act("pilot:B", {"action_id": "attack", "attackers": {self.current(game, borrowed).ref: "A"}})
        self.assertTrue(attack.ok, attack.summary)
        self.assertIn(borrowed.object_id, game.state.combat.attackers)
        self.until(game, lambda current: current.state.active_player == "C")
        for card in (swiftblade, borrowed):
            self.assertNotIn("haste", game.engine._combat_keywords(self.current(game, card)))
        self.replay(game, load=True)

    def test_actual_activation_after_combat_waits_past_end_step_even_when_next_player_has_no_attackers(self):
        game = self.playing(50726002)
        source = self.add(game.engine, "Legion's Initiative")
        creature = self.add(game.engine, "Grizzly Bears")
        self.add(game.engine, "Mountain")
        self.add(game.engine, "Plains")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.activate(game, source)
        self.assertIsNone(self.resolve(game))
        self.until(game, lambda current: current.state.active_player == "A" and current.state.step == "end_step")
        self.assertEqual("exile", self.current(game, creature).zone)
        self.assertFalse(any(item.label == "Return linked exiled objects" for item in game.state.stack))
        game = self.replay(game, load=True)
        self.beginning_combat_return(game, "B")
        self.assertEqual("battlefield", self.current(game, creature).zone)
        self.assertIn("haste", game.engine._combat_keywords(self.current(game, creature)))
        self.assertFalse(any(entry.active for entry in game.state.delayed_triggers))
        self.replay(game, load=True)
