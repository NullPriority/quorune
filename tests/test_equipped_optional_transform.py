"""The equipped damage trigger transforms only its original Equipment incarnation."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.transform_capability_shapes import OPTIONAL_SOURCE_TRANSFORM_VERSION
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Dowsing Dagger // Lost Vale"


class EquippedOptionalTransformTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "equipped-optional-transform.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/equipped-optional-transform-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped optional transform", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_both_faces_and_the_original_transform_snapshot(self):
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = semantics.programs_for_oracle(record.oracle_id)
        entry, = (program for program in programs if program.event == "permanent.enter.self")
        transform, = (program for program in programs if program.event == "damage.dealt")
        self.assertEqual("create_token", entry.effects[0]["op"])
        self.assertIsNone(transform.target_schema)
        self.assertEqual([{
            "op": "offer_optional_effect", "player": "$controller",
            "schema_version": OPTIONAL_SOURCE_TRANSFORM_VERSION, "effects": [{
                "op": "transform", "card": "$source.zone_object",
                "expected_transform_count": "$source.transform_count",
            }],
        }], transform.effects)
        self.assertLessEqual({"permanent.transform.face_change", "effect.choice.optional_fixed",
                              "attachment.reference.current_or_lki"}, set(transform.capability_dependencies))
        weaker = replace(transform, effects=[{
            **transform.effects[0], "effects": [{"op": "transform", "card": "$source.zone_object"}],
        }])
        self.assertFalse(bind_semantic_program_runtime(
            weaker, capability_registry=self.registry, profile="commander_review",
        )["strict"])

    def scene(self, seed, *, flicker=False):
        game = self.session(seed)
        dagger = self.add(game.engine, ORIGINAL, zone="hand")
        carrier = self.add(game.engine, "Runeclaw Bear")
        carrier.acquired_control_turn_count = -1
        removal, island = None, None
        if flicker:
            removal = self.add(game.engine, "Ghostly Flicker", zone="hand")
            island = self.add(game.engine, "Island", ref="dagger-flicker-island")
            self.add(game.engine, "Island", ref="dagger-cost-island")
            self.add(game.engine, "Wastes")
            self.assert_closed(game, removal)
        self.assert_closed(game, dagger)
        self.prepare(game, mana={"C": 4})
        self.checkpoint(game)
        self.cast(game, "A", dagger)
        self.target(game, "A", ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, len(self.plants(game)))
        self.priority(game, "A")
        equip = self.activation(game, dagger)
        action = game.act("pilot:A", {"action_id": equip["id"], "targets": [carrier.ref], "pay": "auto"})
        self.assertTrue(action.ok, action.summary)
        self.resolve_top(game)
        self.assertEqual(carrier.object_id, game.state.cards[dagger.object_id].attached_to)
        self.assertEqual((4, 3), self.stats(game, carrier))
        self.attack(game, [(carrier, "C")])
        self.until(game, lambda current: self.trigger_count(current, dagger) == 1)
        self.assertEqual(36, game.state.players["C"].life)
        trigger = game.state.stack[-1]
        self.assertFalse(trigger.targets)
        self.assertEqual((dagger.logical_object_id, 0),
                         (trigger.context["source_logical_object_id"], trigger.context["source_transform_count"]))
        return game, dagger, carrier, removal, island

    def activation(self, game, source):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def plants(self, game):
        tokens = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token]
        for token in tokens:
            data = game.engine._effective_card_data(token)
            self.assertIn("plant", game.engine._type_parts(data["type_line"])[1])
            self.assertEqual((0, 2), self.stats(game, token))
            self.assertEqual(("B", "B", {"G"}), (token.owner, token.controller, set(data["colors"])))
            self.assertIn("defender", {keyword.casefold() for keyword in data["keywords"]})
        return tokens

    def respond(self, game, choice):
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
        command = {"action_id": "choose", "choice": choice}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        return game

    def test_actual_transform_detaches_and_the_same_untapped_land_makes_three_mana(self):
        game, dagger, carrier, _, _ = self.scene(70127081)
        previous = dagger.logical_object_id
        game = self.respond(game, "apply")
        land = game.state.cards[dagger.object_id]
        self.assertEqual(("Lost Vale", previous, 1, None, False),
                         (land.active_face, land.logical_object_id, land.transform_count, land.attached_to, land.tapped))
        self.assertEqual((2, 2), self.stats(game, carrier))
        self.priority(game, "A")
        action = self.activation(game, land)
        options = action["choice_schema"]["mana_output"]["options"]
        self.assertCountEqual([{color: 3} for color in "WUBRG"], [option["value"] for option in options])
        before = authoritative_state_hash(game.state)
        for invalid in ({"C": 3}, {"R": 1, "G": 2}):
            self.assertFalse(game.act("pilot:A", {"action_id": action["id"], "mana_output": invalid}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": action["id"], "mana_output": {"G": 3}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual({"G": 3}, {k: v for k, v in game.state.players["A"].mana_pool.items() if v})
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_actual_decline_keeps_the_original_front_and_equipped_modifier(self):
        game, dagger, carrier, _, _ = self.scene(70127082)
        game = self.respond(game, "decline")
        current = game.state.cards[dagger.object_id]
        self.assertEqual(("Dowsing Dagger", dagger.logical_object_id, 0, carrier.object_id),
                         (current.active_face, current.logical_object_id, current.transform_count, current.attached_to))
        self.assertEqual((4, 3), self.stats(game, carrier))
        self.replay(game, load=True)

    def test_actual_blink_resolves_new_entry_before_the_stale_transform_cannot_apply(self):
        game, dagger, _, flicker, island = self.scene(70127083, flicker=True)
        old = dagger.logical_object_id
        self.cast_for(game, "A", flicker, [dagger.ref, island.ref])
        self.resolve_top(game)
        self.target(game, "A", ["B"])
        self.resolve_top(game)
        current = game.state.cards[dagger.object_id]
        self.assertNotEqual(old, current.logical_object_id)
        self.assertEqual(4, len(self.plants(game)))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("Dowsing Dagger", current.logical_object_id, 0, None),
                         (game.state.cards[dagger.object_id].active_face,
                          game.state.cards[dagger.object_id].logical_object_id,
                          game.state.cards[dagger.object_id].transform_count,
                          game.state.cards[dagger.object_id].attached_to))
        self.replay(game, load=True)
