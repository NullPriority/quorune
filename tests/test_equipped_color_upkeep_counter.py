"""Controller upkeep checks the current equipped creature's color at resolution."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = (
    ("Ring of Kalonia", "Runeclaw Bear", "G", "trample"),
    ("Ring of Thune", "Savannah Lions", "W", "vigilance"),
    ("Ring of Valkas", "Goblin Piker", "R", "haste"),
    ("Ring of Xathrid", "Walking Corpse", "B", None),
)


class EquippedColorUpkeepCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "equipped-color-upkeep-counter.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/equipped-color-upkeep-counter-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped upkeep color", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_four_complete_originals_share_the_same_color_checked_attachment_reference(self):
        for name, _, color, _ in ORIGINALS:
            with self.subTest(name=name):
                result, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(result["strict_capability_ready"], result["blockers"])
                record = self.db.lookup(name, fuzzy=False)
                upkeep, = (row for row in semantics.programs_for_oracle(record.oracle_id) if row.event == "step.begin")
                self.assertIsNone(upkeep.target_schema)
                effect, = upkeep.effects
                self.assertEqual(("place_counters", "+1/+1", 1), (effect["op"], effect["counter"], effect["amount"]))
                self.assertEqual({
                    "kind": "source_attachment", "relation": "equipped_object",
                    "required_card_type": "creature", "required_color": color, "schema_version": 3,
                }, effect["card"])
                self.assertIn("counter.producer.fixed_attached_effect", upkeep.capability_dependencies)
                self.assertIn("object.reference.source_attachment_information", upkeep.capability_dependencies)

    def start(self, seed):
        game = self.session(seed)
        self.assert_closed(game, self.add(game.engine, "Sphinx of the Second Sun"))
        return game

    def equip(self, game, ring, creature):
        self.assert_closed(game, ring)
        self.cast_for(game, "A", ring)
        self.resolve_top(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [action for action in actions if action["id"].startswith(f"activate:{ring.ref}:") and "Equip" in action["label"]]
        result = game.act("pilot:A", {"action_id": offer["id"], "targets": [creature.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(creature.object_id, game.state.cards[ring.object_id].attached_to)

    def wait_for_upkeep(self, game, rings):
        self.until(game, lambda state: all(self.trigger_count(state, ring) == 1 for ring in rings))
        self.assertEqual(("A", "upkeep"), (game.state.active_player, game.state.step))
        self.assertTrue(all(not item.targets for item in game.state.stack))

    def counters(self, game, creature):
        return game.state.cards[creature.object_id].counters.get("+1/+1", 0)

    def test_actual_four_printed_rings_equip_and_each_matching_color_gets_one_counter(self):
        game = self.start(60304061)
        pairs = [(self.add(game.engine, name, zone="hand"), self.add(game.engine, creature), keyword)
                 for name, creature, _, keyword in ORIGINALS]
        self.prepare(game, mana={"C": 12})
        self.checkpoint(game)
        for ring, creature, keyword in pairs:
            self.equip(game, ring, creature)
            if keyword is not None:
                data = game.engine._effective_card_data(game.state.cards[creature.object_id])
                self.assertIn(keyword, {item.casefold() for item in data["keywords"]})
        self.wait_for_upkeep(game, [ring for ring, _, _ in pairs])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([1, 1, 1, 1], [self.counters(game, creature) for _, creature, _ in pairs])
        self.replay(game, load=True)

    def test_actual_color_change_after_announcement_controls_the_untargeted_result(self):
        for index, (creature_name, instant_name, land_name, expected) in enumerate((
            ("Goblin Piker", "Viridescent Wisps", "Forest", 1),
            ("Runeclaw Bear", "Crimson Wisps", "Mountain", 0),
        )):
            with self.subTest(instant=instant_name):
                game = self.start(60304062 + index)
                ring = self.add(game.engine, "Ring of Kalonia", zone="hand")
                creature = self.add(game.engine, creature_name)
                response = self.add(game.engine, instant_name, seat="B", zone="hand")
                self.add(game.engine, land_name, seat="B")
                self.assert_closed(game, response)
                self.prepare(game, mana={"C": 3})
                self.checkpoint(game)
                self.equip(game, ring, creature)
                self.wait_for_upkeep(game, [ring])
                self.cast_for(game, "B", response, [creature.ref])
                self.resolve_top(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(expected, self.counters(game, creature))
                self.replay(game, load=True)

    def test_actual_reattachment_then_source_departure_uses_the_last_equipped_creature(self):
        game = self.start(60304064)
        ring = self.add(game.engine, "Ring of Kalonia", zone="hand")
        first = self.add(game.engine, "Runeclaw Bear", ref="ring-initial-carrier")
        last = self.add(game.engine, "Runeclaw Bear", seat="B", ref="ring-last-carrier")
        theft = self.add(game.engine, "Magnetic Theft", seat="B", zone="hand")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        for land in ("Mountain", "Plains", "Wastes"):
            self.add(game.engine, land, seat="B")
        for card in (theft, removal):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3})
        self.checkpoint(game)
        self.equip(game, ring, first)
        self.wait_for_upkeep(game, [ring])
        self.priority(game, "B")
        result = game.act("pilot:B", {
            "action_id": f"cast:{theft.ref}", "pay": "auto",
            "targets": {"target_0": [ring.ref], "target_1": [last.ref]},
        })
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(last.object_id, game.state.cards[ring.object_id].attached_to)
        self.cast_for(game, "B", removal, [ring.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[ring.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((0, 1), (self.counters(game, first), self.counters(game, last)))
        self.replay(game, load=True)
