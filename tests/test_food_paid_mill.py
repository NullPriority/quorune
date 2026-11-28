"""Native Food payment and recovery from exact committed Mill arrivals."""

from dataclasses import replace
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
from quorune.rules.milled_permanent_recovery import CAPABILITY, RECOVERY_FIELD
from scripts.build_test_database import build_fixture_database


class FoodPaidMillTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "food-paid-mill.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/food-paid-mill-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Food payment and public Mill", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_attack_requires_the_complete_food_mill_and_optional_hand_move(self):
        card = self.db.lookup("Bristlebud Farmer", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertTrue(any(node.event == "permanent.enter.self" for node in ir.faces[0].nodes))
        attack, = (node for node in ir.faces[0].nodes if node.event == "creature.attacks")
        self.assertIn(CAPABILITY, attack.capability_dependencies)
        effect, = attack.effects
        self.assertEqual(3, effect["effects"][0]["count"])
        self.assertEqual(1, effect["effects"][0]["followup"][RECOVERY_FIELD])
        for old, new in (("sacrifice a Food", "sacrifice an artifact"), ("mill three cards", "mill two cards"),
                         ("permanent card from among them", "card from among them")):
            with self.subTest(changed=old):
                updated = self.compile(replace(card, oracle_text=card.oracle_text.replace(old, new)))
                self.assertFalse(any(CAPABILITY in node.capability_dependencies for node in updated.faces[0].nodes))
        game = self.session(749049000)
        source = self.add(game.engine, card.name)
        self.assert_closed(game, source)
        program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if row.event == "creature.attacks")
        for changed in (replace(program, event_condition=None),
                        replace(program, effects=({**effect, "effects": [{**effect["effects"][0], "count": 4}]},))):
            with self.subTest(condition=changed.event_condition):
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                       profile="commander_review")
                self.assertFalse(result["strict"], result)

    def begin_attack(self, seed, *, replacement):
        game = self.session(seed)
        source = self.add(game.engine, "Bristlebud Farmer", zone="hand")
        nontoken_food = self.add(game.engine, "Gingerbrute")
        other_food = self.add(game.engine, "Gingerbrute", seat="B", ref="opponent-food")
        self.add(game.engine, "Fervor")
        unrelated = self.add(game.engine, "Grizzly Bears", zone="exile" if replacement else "graveyard",
                             ref="preexisting-permanent")
        if replacement:
            peace = self.add(game.engine, "Rest in Peace", seat="B")
            self.assert_closed(game, peace)
        targets = [self.add(game.engine, name, zone="library", ref=f"farmer-top-{index}")
                   for index, name in enumerate(("Sol Ring", "Murder", "Forest"))]
        library = game.state.players["A"].zones["library"]
        target_ids = {card.object_id for card in targets}
        library[:] = [oid for oid in library if oid not in target_ids] + [card.object_id for card in reversed(targets)]
        for card in targets:
            card.known_to = []
            card.revealed_to = []
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "G": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.assertIsNone(self.resolve(game))
        food_tokens = tuple(card for card in game.state.cards.values()
                            if card.is_token and card.controller == "A" and card.zone == "battlefield"
                            and "food" in game.engine._effective_type_parts(card)[1])
        self.assertEqual(2, len(food_tokens))
        self.attack(game, [(source, "B")])
        choice = self.resolve(game)
        self.assertEqual(("semantic.choice", ("A",)), (choice.kind, tuple(choice.actors)))
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("sacrifice", ctx["payment_kind"])
        schema = ctx["legal_actions"][0]["choice_schema"]
        self.assertEqual({nontoken_food.ref, *(card.ref for card in food_tokens)}, set(schema["legal_refs"]))
        self.assertNotIn(other_food.ref, schema["legal_refs"])
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in targets))
        return game, nontoken_food, other_food, unrelated, targets

    def command(self, game, seat, response, *, accepted=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, response)
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_nontoken_food_mills_then_recovers_only_same_public_replaced_arrival(self):
        game, food, opposing, unrelated, (ring, spell, land) = self.begin_attack(749049001, replacement=True)
        self.command(game, "B", {"action_id": "choose", "cards": [food.ref]}, accepted=False)
        self.command(game, "A", {"action_id": "choose", "cards": [opposing.ref]}, accepted=False)
        self.command(game, "A", {"action_id": "choose", "cards": [food.ref]})
        self.assertEqual("exile", game.state.cards[food.object_id].zone)
        self.assertEqual(("exile",) * 3, tuple(game.state.cards[card.object_id].zone for card in (ring, spell, land)))
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        selection = ctx["legal_actions"][0]["choice_schema"]
        self.assertEqual({ring.ref, land.ref}, set(selection["legal_refs"]))
        self.assertEqual((0, 1), (selection["minimum"], selection["maximum"]))
        for card in (spell, unrelated):
            self.command(game, "A", {"action_id": "choose", "objects": [card.ref]}, accepted=False)
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        game = self.replay(game, load=True)
        self.command(game, "A", {"action_id": "choose", "objects": [ring.ref]})
        self.assertEqual("hand", game.state.cards[ring.object_id].zone)
        self.assertEqual("exile", game.state.cards[land.object_id].zone)
        self.assertEqual("exile", game.state.cards[unrelated.object_id].zone)
        self.replay(game, load=True)

    def test_payment_and_recovery_can_be_declined_independently(self):
        for paid in (False, True):
            with self.subTest(paid=paid):
                game, food, _, unrelated, targets = self.begin_attack(749049002 + paid, replacement=False)
                self.command(game, "A", {"action_id": "choose", "cards": [food.ref] if paid else []})
                if paid:
                    self.command(game, "A", {"action_id": "choose", "objects": []})
                expected = "graveyard" if paid else "library"
                self.assertTrue(all(game.state.cards[card.object_id].zone == expected for card in targets))
                self.assertEqual("graveyard" if paid else "battlefield", game.state.cards[food.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[unrelated.object_id].zone)
                self.replay(game, load=True)
