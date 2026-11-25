"""Exercise the original targeted land tap and loss of unspent mana."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.mana_provenance import add_mana, mana_provenance_lots
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Mana Short"
VERSION_FIELD = "target_unspent_mana_version"


class TargetControllerLandsAndUnspentManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "unspent-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/target-controller-lands-and-unspent-mana-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Targeted unspent mana loss", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "resolve"]
        return card, program

    def test_original_registers_the_whole_targeted_result_and_rejects_unrepresented_text(self):
        card, program = self.original_program()
        self.assertEqual("any", program.target_schema["player_relation"])
        effect, = program.effects
        self.assertEqual(("set_public_tap_state", "$source", True, 1),
                         (effect["op"], effect["source"], effect["tapped"], effect[VERSION_FIELD]))
        self.assertEqual("$target.0", effect["set"]["target_controller"])
        for text in (
            card.oracle_text.replace("lands", "creatures"),
            card.oracle_text.replace("target player", "each opponent"),
            card.oracle_text.replace("loses all unspent mana", "loses all unspent mana at end of turn"),
            card.oracle_text + " Rewrite the game history.",
        ):
            changed = replace(card, oracle_text=text)
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status, text)

    def test_raw_combined_shape_keeps_the_original_target_lands_and_mana_origin(self):
        _, program = self.original_program()
        effect, = program.effects
        for changed in (
            replace(program, event="activate"),
            replace(program, target_schema={**program.target_schema, "player_relation": "opponent"}),
            replace(program, effects=({**effect, VERSION_FIELD: True},)),
            replace(program, effects=({**effect, "tapped": False},)),
            replace(program, effects=({**effect, "set": {**effect["set"], "target_controller": "C"}},)),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def activate_mana(self, game, seat, card, **extra):
        self.priority(game, seat)
        decision = game.packet("pilot:" + seat, full=True)["decision"]
        action, = [row for row in decision["ctx"]["legal"]["actions"]
                   if row["id"].startswith("activate:" + card.ref + ":")]
        result = game.act("pilot:" + seat, {"action_id": action["id"], **extra})
        self.assertTrue(result.ok, result.summary)

    def scene(self, seed, *, veil=False, combat_mana=False):
        game = self.session(seed)
        engine = game.engine
        spell = self.add(engine, ORIGINAL, zone="hand")
        free_land = self.add(engine, "Plains", seat="C", ref="mana-short-other-seat")
        ring = self.add(engine, "Sol Ring", seat="B", ref="mana-short-ring")
        lands = {}
        response = None
        if not combat_mana:
            lands["spend"] = self.add(engine, "Forest" if veil else "Island", seat="B", ref="mana-short-spend")
            lands["untapped"] = self.add(engine, "Island", seat="B", ref="mana-short-untapped")
            lands["nonproducer"] = self.add(engine, "Maze of Ith", seat="B")
            lands["restricted"] = self.add(engine, "Ancient Ziggurat", seat="B")
            response = self.add(engine, "Veil of Summer" if veil else "Reach Through Mists", seat="B", zone="hand")
        for card in [spell, ring, *lands.values()] + ([response] if response else []):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "U": 1})
        add_mana(game.state.players["C"], {"R": 2})
        if combat_mana:
            engine.permissions.invalidate_current()
            game.state.pending_decision = None
            game.state.phase = "combat"
            game.state.step = "declare_attackers"
            add_mana(game.state.players["B"], {"G": 3}, retention="end_of_combat")
            engine._grant_priority("A")
            engine.pump()
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["B"])
        return game, spell, lands, ring, response, free_land

    def test_actual_responses_spend_mana_then_tap_the_target_lands_and_clear_remaining_lots(self):
        game, spell, lands, ring, response, free_land = self.scene(61342201)
        player = game.state.players["B"]
        self.activate_mana(game, "B", lands["spend"])
        self.activate_mana(game, "B", lands["restricted"], mana_choice="G")
        self.activate_mana(game, "B", ring)
        self.assertEqual(1, game.engine._restricted_mana("B")["creature_spell_only"]["G"])
        before_draw = len(player.draw_history)
        self.cast_for(game, "B", response, [])
        self.resolve_top(game)
        self.assertEqual(before_draw + 1, len(player.draw_history))
        self.assertEqual("graveyard", game.state.cards[response.object_id].zone)
        self.assertEqual((0, 1, 2), tuple(player.mana_pool[color] for color in ("U", "G", "C")))
        self.assertFalse(game.state.cards[lands["untapped"].object_id].tapped)
        game = self.replay(game, load=True)
        before = game.state.event_sequence
        self.assertIsNone(self.resolve(game))
        player = game.state.players["B"]
        self.assertFalse(any(player.mana_pool.values()), "The target loses every remaining lot")
        self.assertEqual((), mana_provenance_lots(player))
        self.assertEqual({}, game.engine._restricted_mana("B"))
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in lands.values()))
        self.assertFalse(game.state.cards[free_land.object_id].tapped)
        self.assertEqual(2, game.state.players["C"].mana_pool["R"])
        self.assertEqual(before_draw + 1, len(player.draw_history))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        new_events = [event for event in game.state.events if event.event_id > before]
        tapped = next(event for event in new_events if event.code == "effect.permanent.tap_state_set")
        lost = next(event for event in new_events if event.code == "mana.empty" and event.actor == "B")
        self.assertLess(tapped.event_id, lost.event_id)
        self.assertEqual({"C": 2, "G": 1}, lost.details["lost"])
        self.replay(game, load=True)

    def test_actual_veiled_player_is_illegal_for_both_parts(self):
        game, _, lands, ring, response, _ = self.scene(61342202, veil=True)
        self.activate_mana(game, "B", ring)
        self.activate_mana(game, "B", lands["spend"])
        self.cast_for(game, "B", response, [])
        self.resolve_top(game)
        before = game.state.event_sequence
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[lands["untapped"].object_id].tapped)
        self.assertFalse(game.state.cards[lands["nonproducer"].object_id].tapped)
        self.assertEqual(2, game.state.players["B"].mana_pool["C"])
        self.assertFalse(any(event.code in {"mana.empty", "effect.permanent.tap_state_set"}
                             for event in game.state.events if event.event_id > before))
        self.replay(game, load=True)

    def test_landless_target_loses_combat_retained_mana_without_using_a_step_boundary(self):
        game, _, lands, ring, _, free_land = self.scene(61342203, combat_mana=True)
        self.assertEqual({}, lands)
        self.assertEqual("end_of_combat", mana_provenance_lots(game.state.players["B"])[0].retention)
        self.assertEqual("declare_attackers", game.state.step)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("declare_attackers", game.state.step)
        self.assertEqual((), mana_provenance_lots(game.state.players["B"]))
        self.assertFalse(any(game.state.players["B"].mana_pool.values()))
        self.assertFalse(game.state.cards[ring.object_id].tapped)
        self.assertFalse(game.state.cards[free_land.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_resolution_kills_the_omitted_unspent_mana_loss(self):
        with patch("quorune.public_tap_state_sets.lose_all_unspent_mana", return_value={}):
            with self.assertRaisesRegex(AssertionError, "The target loses every remaining lot"):
                self.test_actual_responses_spend_mana_then_tap_the_target_lands_and_clear_remaining_lots()
