"""The current graveyard incarnation must have arrived from the battlefield this turn."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_zone_moves import SAME_TURN_GRAVEYARD_RETURN_VERSION
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class SameTurnGraveyardReturnSetTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "same-turn-graveyard-return-sets.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/same-turn-graveyard-return-sets-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("The original graveyard arrival", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_keep_the_two_printed_owner_and_type_scopes(self):
        four_types = {"artifact", "creature", "enchantment", "land"}
        for name, relation, types in (("Faith's Reward", "actor", four_types | {"battle", "planeswalker"}),
                                      ("Second Sunrise", "any", four_types)):
            with self.subTest(name=name):
                admitted, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                program, = semantics.programs_for_oracle(card.oracle_id)
                effect, = program.effects
                spec = effect["set"]
                self.assertEqual(SAME_TURN_GRAVEYARD_RETURN_VERSION, spec["schema_version"])
                self.assertEqual(("owner", relation, "owner"),
                                 (spec["relation_axis"], spec["seat_relation"], spec["battlefield_controller"]))
                self.assertEqual(types, set(spec["query"]["types_any"]))
                self.assertIsNone(program.target_schema)
                self.assertIn("condition.history.fixed_public_query", program.capability_dependencies)
                invalid = {**effect, "set": {**spec, "battlefield_controller": "actor"}}
                self.assertFalse(bind_semantic_program_runtime(
                    replace(program, effects=[invalid]), capability_registry=self.registry, profile="commander_review",
                )["strict"])
                altered = replace(card, oracle_text=card.oracle_text.replace("this turn", "this game"))
                self.assertNotEqual("exact", compile_oracle_card(
                    altered, capability_registry=self.registry, capability_profile="commander_review",
                ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def test_actual_faiths_reward_uses_the_latest_arrival_incarnation_and_its_owner(self):
        game = self.session(40071101)
        reward = self.add(game.engine, "Faith's Reward", zone="hand")
        theft = self.add(game.engine, "Act of Treason", zone="hand")
        wrath = self.add(game.engine, "Wrath of God", zone="hand")
        recover = self.add(game.engine, "Regrowth", zone="hand")
        discard = self.add(game.engine, "Thrill of Possibility", zone="hand")
        survivor = self.add(game.engine, "Grizzly Bears", ref="reward-original-current")
        stale = self.add(game.engine, "Grizzly Bears", ref="reward-later-discard")
        borrowed = self.add(game.engine, "Grizzly Bears", seat="B", ref="reward-borrowed")
        old = self.add(game.engine, "Sol Ring", zone="graveyard", ref="reward-prior-grave")
        for card in (reward, theft, wrath, recover, discard):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 9, "W": 3, "R": 2, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", theft, [borrowed.ref], reserve={"C": 7, "W": 3, "R": 1, "G": 1})
        self.resolve_top(game)
        self.assertEqual(("A", "B"), (self.current(game, borrowed).controller, self.current(game, borrowed).owner))
        self.cast_for(game, "A", wrath, reserve={"C": 5, "W": 1, "R": 1, "G": 1})
        self.resolve_top(game)
        original = self.current(game, stale).logical_object_id
        self.assertIn(borrowed.object_id, game.state.players["B"].zones["graveyard"])
        self.cast_for(game, "A", recover, [self.current(game, stale).ref], reserve={"C": 4, "W": 1, "R": 1})
        self.resolve_top(game)
        self.priority(game, "A")
        offer = self.action(game, "A", discard)
        cast = game.act("pilot:A", {
            "action_id": offer["id"], "discard_cards": [self.current(game, stale).ref],
            "pay": "auto", "reserve": {"C": 3, "W": 1},
        })
        self.assertTrue(cast.ok, cast.summary)
        self.resolve_top(game)
        self.assertNotEqual(original, self.current(game, stale).logical_object_id)
        self.assertEqual("graveyard", self.current(game, stale).zone)
        self.cast_for(game, "A", reward)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "A"), (self.current(game, survivor).zone, self.current(game, survivor).controller))
        self.assertEqual(["graveyard"] * 3, [self.current(game, card).zone for card in (stale, borrowed, old)])
        self.replay(game, load=True)

    def test_actual_second_sunrise_returns_the_printed_types_across_owners_and_leaves_a_planeswalker(self):
        game = self.session(40071102)
        sunrise = self.add(game.engine, "Second Sunrise", zone="hand")
        within = self.add(game.engine, "Beast Within", zone="hand")
        wrath = self.add(game.engine, "Wrath of God", zone="hand")
        disenchant = self.add(game.engine, "Disenchant", zone="hand")
        rain = self.add(game.engine, "Stone Rain", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", ref="sunrise-a-creature")
        second = self.add(game.engine, "Grizzly Bears", seat="B", ref="sunrise-b-creature")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        land = self.add(game.engine, "Forest", seat="C")
        walker = self.add(game.engine, "Jace Beleren", seat="B")
        walker.counters["loyalty"] = 3
        stale = self.add(game.engine, "Forest", seat="D", zone="graveyard", ref="sunrise-old-land")
        for card in (sunrise, within, wrath, disenchant, rain, walker):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 8, "W": 5, "G": 1, "R": 1})
        self.checkpoint(game)
        for cast, targets, reserve in (
            (within, [walker.ref], {"C": 6, "W": 5, "R": 1}),
            (wrath, [], {"C": 4, "W": 3, "R": 1}),
            (disenchant, [artifact.ref], {"C": 3, "W": 2, "R": 1}),
            (rain, [land.ref], {"C": 1, "W": 2}),
        ):
            self.cast_for(game, "A", cast, targets, reserve=reserve)
            self.resolve_top(game)
        returning = (first, second, artifact, land)
        self.assertEqual(["graveyard"] * 5, [self.current(game, card).zone for card in (*returning, walker)])
        incarnations = {card.object_id: self.current(game, card).logical_object_id for card in returning}
        self.cast_for(game, "A", sunrise)
        self.assertIsNone(self.resolve(game))
        for card in returning:
            result = self.current(game, card)
            self.assertEqual(("battlefield", card.owner), (result.zone, result.controller))
            self.assertNotEqual(incarnations[card.object_id], result.logical_object_id)
        self.assertEqual(["graveyard", "graveyard"], [self.current(game, card).zone for card in (walker, stale)])
        self.replay(game, load=True)

    def test_actual_faiths_reward_aura_chooses_an_existing_subject_before_the_batch_returns(self):
        game = self.session(40071103)
        reward = self.add(game.engine, "Faith's Reward", zone="hand")
        wrath = self.add(game.engine, "Wrath of God", zone="hand")
        army = self.add(game.engine, "Raise the Alarm", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears")
        aura = self.add(game.engine, "Pacifism")
        attach_objects(game.state.cards, aura, creature, source_timestamp=aura.zone_timestamp, players=game.state.players)
        for card in (reward, wrath, army, aura):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "W": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", wrath, reserve={"C": 4, "W": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["graveyard"] * 2, [self.current(game, card).zone for card in (aura, creature)])
        self.cast_for(game, "A", army, reserve={"C": 3, "W": 1})
        self.resolve_top(game)
        existing = next(card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield")
        self.cast_for(game, "A", reward)
        pending = self.resolve(game)
        self.assertEqual(("aura.entry", ["A"]), (pending.kind, pending.actors))
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:A", {"action_id": "choose", "aura_target": self.current(game, creature).ref})
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(["graveyard"] * 2, [self.current(game, card).zone for card in (aura, creature)])
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", {"action_id": "choose", "aura_target": existing.ref})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["battlefield"] * 2, [self.current(game, card).zone for card in (aura, creature)])
        self.assertEqual(existing.object_id, self.current(game, aura).attached_to)
        self.replay(game, load=True)
