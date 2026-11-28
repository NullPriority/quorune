"""Copy a controlled enchantment with a copied upkeep return and replay both entries."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_fixed_copy_type_exceptions as copy_fixture


NAME = "Estrid's Invocation"
PROFILE = "commander_review"


class EstridsInvocationTests(InheritedNativeFixture, unittest.TestCase):
    choose_copy = copy_fixture.FixedCopyTypeExceptionRuntimeTests.choose_copy

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "estrid-invocation.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "estrids-invocation-copy-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Estrid's Invocation", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_actual_copy_keeps_the_controlled_query_and_exact_upkeep_child(self):
        card = self.db.lookup(NAME, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        entry, = (program for program in programs if program.event == "zone.change")
        descriptor, = entry.handlers
        self.assertEqual(("enchantment",), tuple(descriptor["copy"]["selection"]["types_all"]))
        self.assertEqual("$controller", descriptor["copy"]["selection"]["controller"])
        self.assertEqual(1, len(descriptor["copy"]["exception"]["add_ability_fragments"]))
        upkeep, = (program for program in programs if program.event == "step.begin")
        self.assertEqual("offer_optional_effect", upkeep.effects[0]["op"])
        for before, after in (("an enchantment you control", "an enchantment"),
                              ("your upkeep", "each upkeep"),
                              ("its owner's control", "your control")):
            with self.subTest(after=after):
                changed = replace(card, oracle_text=card.oracle_text.replace(before,after))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)

    def wait_for_copy(self, game):
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "replacement.order")

    def test_actual_upkeep_can_leave_and_reenter_as_a_different_controlled_enchantment(self):
        game = self.session(47840201)
        source = self.add(game.engine, NAME, zone="hand")
        first = self.add(game.engine, "Enchantress's Presence", ref="owned-first-copy")
        second = self.add(game.engine, "Dueling Grounds", ref="owned-second-copy")
        foreign = self.add(game.engine, "Dueling Grounds", seat="C", ref="foreign-enchantment")
        for item in (source, first, second, foreign):
            self.assert_closed(game, item)
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"]):
                if object_id != source.object_id:
                    game.engine.move_card(object_id, "library", semantic_events=False, log=False)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="A", active="A", mana={"C":2,"U":1})
        self.checkpoint(game)
        self.cast(game,"A",source)
        self.wait_for_copy(game)
        options = game.packet("pilot:A",full=True)["decision"]["ctx"]["options"]
        self.assertTrue(any(first.ref in row["id"] for row in options))
        self.assertFalse(any(foreign.ref in row["id"] for row in options))
        game = self.choose_copy(game,first)
        initial = game.state.cards[source.object_id]
        self.assertEqual("Enchantress's Presence", game.engine._effective_card_data(initial)["name"])
        first_incarnation = initial.logical_object_id
        for seat in ("B","C","D"):
            self.until(game,lambda current:current.state.active_player==seat and current.state.step=="upkeep")
        self.until(game,lambda current: current.state.active_player=="A" and current.state.step=="upkeep"
                   and current.state.pending_decision is not None and current.state.pending_decision.kind=="semantic.choice")
        game = self.replay(game,load=True)
        command = {"action_id":"choose","choice":"apply"}
        old_state = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C",command).ok)
        self.assertEqual(old_state,authoritative_state_hash(game.state))
        result = game.act("pilot:A",command)
        self.assertTrue(result.ok,result.summary)
        self.wait_for_copy(game)
        self.assertEqual("exile",game.state.cards[source.object_id].zone)
        game = self.choose_copy(game,second)
        returned = game.state.cards[source.object_id]
        self.assertEqual(("A","battlefield"),(returned.controller,returned.zone))
        self.assertNotEqual(first_incarnation,returned.logical_object_id)
        self.assertEqual("Dueling Grounds",game.engine._effective_card_data(returned)["name"])
        self.replay(game,load=True)
