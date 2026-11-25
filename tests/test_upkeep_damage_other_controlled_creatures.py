"""Native evidence for an upkeep damage batch on other current controlled creatures."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_damage_set_model import FixedDamageSetError, FixedDamageSetSpec, PermanentControllerRelation
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_scheduled_player_triggers as scheduled_witnesses


ORIGINAL = "Cinder Giant"


class UpkeepDamageOtherControlledCreaturesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "upkeep-damage-other-controlled-creatures.sqlite3"
        fixture = ROOT / "tests/fixtures/upkeep-damage-other-controlled-creatures-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Upkeep grouped creature damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "step.begin"]
        return card, program

    def test_original_admits_one_untargeted_group_and_exact_source_exclusion(self):
        card, program = self.program()
        self.assertEqual("battlefield", program.active_zone)
        self.assertIsNone(program.target_schema)
        effect, = program.effects
        self.assertEqual(("damage_fixed_set", "$source", 2),
                         (effect["op"], effect["source"], effect["amount"]))
        spec = FixedDamageSetSpec.from_dict({"groups": effect["groups"]})
        group, = spec.groups
        self.assertEqual(PermanentControllerRelation.ACTOR, group.controller_relation)
        self.assertTrue(group.exclude_source)
        self.assertEqual(("creature",), group.query.types_all)
        with self.assertRaises(FixedDamageSetError):
            FixedDamageSetSpec.from_dict({"groups": [{**group.to_dict(), "exclude_source": False}]})
        with self.assertRaises(FixedDamageSetError):
            FixedDamageSetSpec.from_dict({"groups": [group.to_dict(), {"kind": "players", "relation": "all"}]})
        for altered in (
            card.oracle_text.replace("each other creature", "each other player you own"),
            card.oracle_text.replace("2 damage", "as much damage as you choose"),
            card.oracle_text + " Rewrite history.",
        ):
            ir = compile_oracle_card(replace(card, oracle_text=altered), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, altered)

    def upkeep(self, game, seat):
        scheduled_witnesses.ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap(self, game, seat)

    def native_group(self, game, *, mode):
        source = self.add(game.engine, ORIGINAL)
        game.engine.change_control(source.object_id, "B", reason="Prepare a borrowed trigger source")
        bear = self.add(game.engine, "Grizzly Bears", seat="B", ref="cinder-owned-bear")
        angel = self.add(game.engine, "Serra Angel", seat="B")
        knight = self.add(game.engine, "Silver Knight", seat="B")
        outsider = self.add(game.engine, "Grizzly Bears", seat="A", ref="cinder-other-controller")
        walker = self.add(game.engine, "Jace Beleren", seat="B")
        walker.counters["loyalty"] = 3
        response = None
        if mode == "destroy":
            response = self.add(game.engine, "Murder", seat="C", zone="hand")
            self.add(game.engine, "Swamp", seat="C", ref="cinder-funding-black-first")
            self.add(game.engine, "Swamp", seat="C", ref="cinder-funding-black-second")
            self.add(game.engine, "Plains", seat="C")
        elif mode == "blink":
            response = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
            self.add(game.engine, "Plains", seat="B")
        for card in (source, bear, angel, knight, outsider, walker, response):
            if card is not None:
                self.assert_closed(game, card)
        return source, bear, angel, knight, outsider, walker, response

    def assert_group(self, game, bear, angel, knight, outsider, walker):
        self.assertEqual("graveyard", game.state.cards[bear.object_id].zone)
        self.assertEqual(2, game.state.cards[angel.object_id].marked_damage)
        self.assertEqual(0, game.state.cards[knight.object_id].marked_damage)
        self.assertEqual(0, game.state.cards[outsider.object_id].marked_damage)
        self.assertEqual(3, game.state.cards[walker.object_id].counters["loyalty"])
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])

    def test_actual_borrowed_source_damages_only_other_controlled_creatures_and_respects_prevention(self):
        game = self.session(12040320)
        source, bear, angel, knight, outsider, walker, _ = self.native_group(game, mode="present")
        self.upkeep(game, "B")
        trigger, = game.state.stack
        self.assertEqual((source.object_id, "B", []), (trigger.source_object_id, trigger.controller, list(trigger.targets)))
        self.resolve_top(game)
        self.assert_group(game, bear, angel, knight, outsider, walker)
        self.assertEqual(0, game.state.cards[source.object_id].marked_damage)
        self.replay(game, load=True)

    def test_actual_departed_source_keeps_its_damage_and_blinked_new_creature_joins_the_group(self):
        for mode in ("destroy", "blink"):
            with self.subTest(mode=mode):
                game = self.session(12040330 + len(mode))
                source, bear, angel, knight, outsider, walker, response = self.native_group(game, mode=mode)
                original = source.logical_object_id
                self.upkeep(game, "B")
                self.cast_for(game, "C" if mode == "destroy" else "B", response, [source.ref])
                self.resolve_top(game)
                current = game.state.cards[source.object_id]
                self.assertNotEqual(original, current.logical_object_id)
                self.assertEqual("graveyard" if mode == "destroy" else "battlefield", current.zone)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assert_group(game, bear, angel, knight, outsider, walker)
                if mode == "blink":
                    self.assertEqual(2, game.state.cards[source.object_id].marked_damage)
                self.replay(game, load=True)

    def test_actual_battlefield_ability_removal_prevents_the_upkeep_trigger(self):
        game = self.session(12040350)
        source = self.add(game.engine, ORIGINAL, seat="B")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        lignify = self.add(game.engine, "Lignify", zone="hand")
        for card in (source, lignify):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", lignify, [source.ref])
        self.resolve_top(game)
        self.assertIn("Treefolk", game.engine._effective_card_data(source)["type_line"])
        self.until(game, lambda current: current.state.active_player == "B" and current.state.step == "upkeep")
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
        self.assertEqual(0, game.state.cards[bear.object_id].marked_damage)
        self.replay(game, load=True)
