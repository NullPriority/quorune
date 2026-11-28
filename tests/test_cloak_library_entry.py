"""Native face-down library entry, Ward, and paid turn-up behavior."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.manifest import is_cloaked, is_manifested
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.morph_actions import build_turn_face_up_offers
from quorune.semantic_choices.intent_replacement import semantic_intent_identity
from quorune.semantic_runtime.zone_move_intent import ZoneMoveIntent
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database


class CloakLibraryEntryTests(InheritedNativeFixture, unittest.TestCase):
    creature = "Fixture Hollow Lynx"
    targeted_instant = "Fixture Canopy Fade"
    disguise_creature = "Fixture Dust Crane"
    partition_spell = "Fixture Hidden Passage"
    looked_names = ("Generic Bound Plains", creature, targeted_instant, disguise_creature, "Fixture Frost Talon")

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "cloak-library-entry.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json", fixtures / "cloak-library-entry-cards.json",
        ], database)
        cls.use_database(database, "Generic Bound Commander", "Generic Bound Plains")

    @classmethod
    def use_database(cls, database, commander, basic):
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private library entry", [
            DeckEntry(commander, 1, "commander"), DeckEntry(basic, 30),
        ], [commander])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def cloak(self, game, card):
        intent = ZoneMoveIntent(
            actor="A", object_ref=card.ref, expected_zones=("library",), destination="battlefield",
            reason="Native library entry", new_controller="A",
            expected_zone_change_counter=card.zone_change_counter, cloak_entry=True,
        )
        kind, identity = semantic_intent_identity(intent)
        self.assertEqual(kind, "zone_move")
        self.assertIs(identity["cloak_entry"], True)
        self.assertNotIn("manifest_entry", identity)
        self.assertEqual(game.engine.move_object_intent(intent), card.ref)
        self.assertTrue(is_cloaked(card))
        self.assertFalse(is_manifested(card))

    def test_cloaked_creature_retains_ward_through_save_and_can_turn_up_for_its_printed_mana(self):
        game = self.session(70158001)
        hidden = self.add(game.engine, self.creature, zone="library", ref="hidden-cloak-creature")
        target_spell = self.add(game.engine, self.targeted_instant, seat="B", zone="hand")
        for card in (hidden, target_spell):
            self.assert_closed(game, card)
        self.cloak(game, hidden)
        original_identity = hidden.logical_object_id
        self.prepare(game)
        game.state.players["A"].mana_pool.update({"C": 1, "G": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "U": 1})
        apparent = game.engine._effective_card_data(hidden)
        self.assertEqual((apparent["name"], apparent["type_line"], apparent["mana_value"], apparent["keywords"]),
                         ("", "Creature", 0, ["Ward {2}"]))
        self.assertEqual((game.engine._numeric_stat(hidden.object_id, "power"),
                          game.engine._numeric_stat(hidden.object_id, "toughness")), (2, 2))
        self.assertNotIn(hidden.printed_name, json.dumps(game.packet("pilot:B", full=True)["state"]))
        self.checkpoint(game)
        action_id = f"turn-face-up:{hidden.ref}:cloak"
        state = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": action_id, "pay": "auto"}).ok)
        self.assertEqual(state, authoritative_state_hash(game.state))
        self.assertTrue(game.act("pilot:A", {"action_id": "pass"}).ok)
        result = game.act("pilot:B", {"action_id": f"cast:{target_spell.ref}", "targets": [hidden.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        decision = self.resolve(game)
        self.assertEqual((decision.kind, decision.actors), ("semantic.choice", ["B"]))
        game = self.replay(game, load=True)
        decline = game.act("pilot:B", {"action_id": "choose", "pay": False})
        self.assertTrue(decline.ok, decline.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(game.state.cards[target_spell.object_id].zone, "graveyard")
        self.until(game, lambda state: state.pending_principals() == ["pilot:A"])
        self.assertTrue(is_cloaked(game.state.cards[hidden.object_id]))
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        card = game.state.cards[hidden.object_id]
        from quorune.manifest import cloak_turn_up_spec
        self.assertIn(action_id, {entry["id"] for entry in actions}, {
            "turn": game.state.turn_sequence,
            "phase": game.state.phase,
            "step": game.state.step,
            "mana": dict(game.state.players["A"].mana_pool),
            "spec": cloak_turn_up_spec(game.engine, "A", card),
            "offers": [offer.to_dict() for offer in build_turn_face_up_offers(game.engine, "A", card)],
            "method_actions": [entry["id"] for entry in actions if "face-up" in entry["id"]],
        })
        result = game.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        returned = game.state.cards[hidden.object_id]
        self.assertFalse(returned.face_down)
        self.assertEqual(returned.logical_object_id, original_identity)
        self.assertEqual(game.engine._effective_card_data(returned)["keywords"], [])
        self.assertEqual(sum(game.state.players["A"].mana_pool.values()), 0)
        self.replay(game, load=True)

    def test_cloaked_instant_has_no_paid_turn_up_and_a_face_up_instruction_leaves_it_cloaked(self):
        game = self.session(70158002)
        instant = self.add(game.engine, self.targeted_instant, zone="library")
        self.assert_closed(game, instant)
        self.cloak(game, instant)
        identity = instant.logical_object_id
        game.state.players["A"].mana_pool.update({"C": 20, "R": 5})
        self.assertEqual(build_turn_face_up_offers(game.engine, "A", instant), ())
        self.assertFalse(ZoneTransitionOwner(game.engine).turn_face_up(instant, viewers=tuple(game.engine.seats)))
        self.assertTrue(is_cloaked(instant))
        self.assertEqual(instant.logical_object_id, identity)
        self.assertEqual(game.state.events[-1].code, "card.reveal")
        self.assertEqual(game.engine._effective_card_data(instant)["keywords"], ["Ward {2}"])

    def test_cloaked_card_can_use_its_printed_disguise_method(self):
        game = self.session(70158003)
        hidden = self.add(game.engine, self.disguise_creature, zone="library")
        self.assert_closed(game, hidden)
        self.cloak(game, hidden)
        game.state.players["A"].mana_pool.update({"C": 20, "W": 5})
        offers = build_turn_face_up_offers(game.engine, "A", hidden)
        self.assertEqual({offer.to_dict()["method"] for offer in offers}, {"cloak", "disguise"})
    def test_cloak_partition_hides_the_choices_and_replays_ordered_entries_and_random_bottom(self):
        game = self.session(70158005)
        spell = self.add(game.engine, self.partition_spell, zone="hand", ref="private-cloak-spell")
        self.assert_closed(game, spell)
        looked = [
            self.add(game.engine, name, zone="library", ref=f"private-cloak-look-{index}")
            for index, name in enumerate(self.looked_names)
        ]
        self.prepare(game, mana={"C": 3, "G": 1})
        self.checkpoint(game)
        result = game.act("pilot:A", {"action_id": f"cast:{spell.ref}", "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        decision = self.resolve(game)
        self.assertEqual((decision.kind, decision.actors), ("semantic.choice", ["A"]))
        owner = game.packet("pilot:A", full=True)["decision"]
        field, = owner["legal_actions"][0]["form"]["fields"]
        self.assertEqual(("cards", True, "selection_order"), (field["name"], field["ordered"], field["order"]))
        spectator = game.packet("pilot:B", full=True)
        for card in looked:
            self.assertIn(card.ref, json.dumps(owner))
            self.assertNotIn(card.ref, json.dumps(spectator["state"]))
            self.assertNotIn(card.ref, json.dumps(spectator["decision"]))
        selected = (looked[4], looked[1])
        state_hash = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "cards": [card.ref for card in selected]}).ok)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "cards": [selected[0].ref] * 2}).ok)
        self.assertEqual(authoritative_state_hash(game.state), state_hash)
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "cards": [card.ref for card in selected]})
        self.assertTrue(result.ok, result.summary)
        actual = tuple(game.state.cards[card.object_id] for card in selected)
        self.assertTrue(all(is_cloaked(card) for card in actual))
        self.assertLess(actual[0].zone_timestamp, actual[1].zone_timestamp)
        self.assertEqual(
            set(game.state.players["A"].zones["library"][:3]),
            {card.object_id for card in looked if card not in selected},
        )
        self.assertNotIn(selected[0].printed_name, json.dumps(game.packet("pilot:B", full=True)["state"]))
        self.replay(game, load=True)

    def test_cloak_partition_fails_closed_when_a_required_entry_owner_is_withdrawn(self):
        from dataclasses import replace
        from unittest.mock import patch

        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.cloak_shapes import CLOAK_CAPABILITY

        card = self.db.lookup(self.partition_spell, fuzzy=False)
        compiled = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", compiled.status)
        actual = CapabilityRegistry.closure

        def denied(registry, requested, *, profile):
            closure = actual(registry, requested, profile=profile)
            if CLOAK_CAPABILITY in closure.reachable:
                return replace(closure, trusted=False, blockers=("dependency:cloak-entry-withdrawn",))
            return closure

        with patch.object(CapabilityRegistry, "closure", new=denied):
            blocked = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(any("cloak-entry-withdrawn" in str(value) for value in blocked.material_residuals))

    def test_cloak_partition_detects_reversed_entry_order_mutation(self):
        from unittest.mock import patch

        from quorune.semantic_choices.cloak_library import CloakChoiceHandler

        original = CloakChoiceHandler.complete

        def reversed_choice(handler, continuation, response, query):
            return original(handler, continuation, {**response, "cards": list(reversed(response["cards"]))}, query)

        with patch.object(CloakChoiceHandler, "complete", new=reversed_choice):
            with self.assertRaisesRegex(AssertionError, "not less than"):
                self.test_cloak_partition_hides_the_choices_and_replays_ordered_entries_and_random_bottom()

    def test_compiled_top_cloak_and_printed_snow_and_phyrexian_cost_use_the_canonical_payment(self):
        game = self.session(70158008)
        spell = self.add(game.engine, "Fixture Shrouded Gate", zone="hand")
        other = self.add(game.engine, self.creature, zone="library", ref="lower-library-object")
        cloaked = self.add(game.engine, "Fixture Frost Talon", zone="library", ref="top-library-object")
        snow = self.add(game.engine, "Fixture Frost Point", ref="snow-payment-source")
        for card in (spell, other, cloaked, snow):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)
        self.cast(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        card = game.state.cards[cloaked.object_id]
        self.assertTrue(is_cloaked(card))
        self.assertEqual(game.state.cards[other.object_id].zone, "library")
        self.assertNotIn(card.printed_name, json.dumps(game.packet("pilot:B", full=True)["state"]))
        life = game.state.players["A"].life
        offer, = [row for row in build_turn_face_up_offers(game.engine, "A", card)
                  if row.to_dict().get("printed_phyrexian_life") == 2]
        self.assertEqual(1, offer.to_dict()["snow"])
        result = game.act("pilot:A", {"action_id": offer.to_dict()["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(life - 2, game.state.players["A"].life)
        self.assertTrue(game.state.cards[snow.object_id].tapped)
        self.assertFalse(game.state.cards[cloaked.object_id].face_down)
        self.replay(game, load=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
