"""Turnabout uses one announced player and two exact resolution choices."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as untap_fixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.public_zone_move_capability_shapes import chosen_type_tap_state_effect
from quorune import public_tap_state_sets
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class ChosenTypeTapStateTests(InheritedNativeFixture, unittest.TestCase):
    activate = untap_fixture.NextPlayerUntapRestrictionTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "chosen-type-tap-state-set.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/chosen-type-tap-state-set-cards.json"], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Tap or untap the chosen type", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_requires_six_closed_branches_and_the_same_target_player(self):
        admitted, semantics = register_card(self.db, self.registry, "Turnabout")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup("Turnabout", fuzzy=False)
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual([chosen_type_tap_state_effect()], program.effects)
        self.assertEqual("any", program.target_schema["player_relation"])
        self.assertIn("target.revalidate_resolution", program.capability_dependencies)
        effect = deepcopy(program.effects[0])
        effect["then_by_choice"]["artifact"][0]["then_by_choice"]["untap"][0]["set"]["target_controller"] = "$controller"
        for bad in (replace(program, effects=[effect]),
                    replace(program, target_schema={**program.target_schema, "player_relation": "opponent"})):
            self.assertFalse(bind_semantic_program_runtime(
                bad, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        changed = replace(card, oracle_text=card.oracle_text.replace("artifact, creature, or land", "artifact or creature"))
        self.assertNotEqual("exact", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile="commander_review",
        ).status)

    def choose(self, game, value, legal, *, accepted=True):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        packet = game.packet("pilot:A", full=True)["decision"]
        schema = packet["legal_actions"][0]["choice_schema"]
        self.assertEqual(legal, set(schema["legal_values"]))
        command = {"action_id": "choose", "choice": value}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_artifact_tap_uses_the_response_changed_set_before_any_occurrence(self):
        game = self.session(60826001)
        spell = self.add(game.engine, "Turnabout", zone="hand")
        coating = self.add(game.engine, "Liquimetal Coating", seat="B")
        island = self.add(game.engine, "Island", seat="B")
        citadel = self.add(game.engine, "Darksteel Citadel", seat="B")
        opponent_land = self.add(game.engine, "Forest", seat="B", ref="turnabout-other-type")
        other_ring = self.add(game.engine, "Sol Ring", seat="C")
        for card in (spell, coating, citadel, other_ring):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["B"])
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        activated, = (row for row in actions if row["id"].startswith("activate:" + coating.ref + ":"))
        result = game.act("pilot:B", {"action_id": activated["id"], "targets": [island.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.choose(game, "enchantment", {"artifact", "creature", "land"}, accepted=False)
        game = self.replay(game, load=True)
        self.choose(game, "artifact", {"artifact", "creature", "land"})
        self.assertFalse(game.state.cards[island.object_id].tapped)
        self.assertFalse(game.state.cards[citadel.object_id].tapped)
        game = self.replay(game, load=True)
        seen = []
        dispatch = public_tap_state_sets.dispatch_tap_state_occurrence

        def observe(host, card, **parameters):
            if card.object_id in {island.object_id, citadel.object_id}:
                seen.append((card.object_id, host.state.cards[island.object_id].tapped,
                             host.state.cards[citadel.object_id].tapped))
            return dispatch(host, card, **parameters)

        self.choose(game, "decline", {"tap", "untap"}, accepted=False)
        with patch.object(public_tap_state_sets, "dispatch_tap_state_occurrence", side_effect=observe):
            self.choose(game, "tap", {"tap", "untap"})
        self.assertIsNone(self.resolve(game))
        self.assertEqual({(island.object_id, True, True), (citadel.object_id, True, True)}, set(seen))
        self.assertEqual(2, len(seen))
        self.assertFalse(game.state.cards[opponent_land.object_id].tapped)
        self.assertFalse(game.state.cards[other_ring.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_untap_keeps_absolute_prohibitions_and_uses_the_original_player(self):
        game = self.session(60826002)
        spell = self.add(game.engine, "Turnabout", zone="hand")
        ice = self.add(game.engine, "Frozen in Ice", zone="hand")
        arbor = self.add(game.engine, "Dryad Arbor", seat="B")
        forest = self.add(game.engine, "Forest", seat="B", ref="turnabout-eligible")
        ring = self.add(game.engine, "Sol Ring", seat="B")
        other = self.add(game.engine, "Forest", seat="C", ref="turnabout-other-seat")
        for card in (spell, ice, arbor, ring):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "U": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", ice, [arbor.ref], reserve={"C": 2, "U": 2})
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "A", spell, ["B"])
        for seat, card in (("B", forest), ("B", ring), ("C", other)):
            self.activate(game, seat, card)
        self.choose(game, "land", {"artifact", "creature", "land"})
        self.choose(game, "untap", {"tap", "untap"})
        self.assertIsNone(self.resolve(game))
        self.assertEqual([True, False, True, True],
                         [game.state.cards[card.object_id].tapped for card in (arbor, forest, ring, other)])
        self.replay(game, load=True)

    def test_actual_turnabout_fizzles_before_either_choice_if_its_target_gains_hexproof(self):
        game = self.session(60826003)
        spell = self.add(game.engine, "Turnabout", zone="hand")
        veil = self.add(game.engine, "Veil of Summer", zone="hand", seat="B")
        land = self.add(game.engine, "Forest", seat="B")
        for card in (spell, veil):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "U": 2})
        game.state.players["B"].mana_pool["G"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["B"])
        self.cast_for(game, "B", veil)
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[land.object_id].tapped)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)
