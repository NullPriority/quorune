"""Exercise Reins of Power through the two public creature sets and cleanup."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Reins of Power"
VERSION_FIELD = "two_controller_creature_transfer_version"


class TwoControllerCreatureControlAndHasteTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "two-controller-creatures.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/two-controller-creature-control-and-haste-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Reins of Power witness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def source_program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "resolve"]
        return card, program

    def test_complete_original_has_only_the_legal_opponent_and_exact_ordered_result(self):
        card, program = self.source_program()
        self.assertEqual({"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1},
                         program.target_schema)
        self.assertEqual(({"op": "gain_control_set", "opponent": "$target.0", "source": "$source", VERSION_FIELD: 1},),
                         tuple(program.effects))
        clauses = " ".join(card.oracle_text.split()).removesuffix(".").split(". ")
        for text in (
            card.oracle_text.replace("target opponent", "target player"),
            card.oracle_text.replace("all creatures you control", "all permanents you control"),
            card.oracle_text.replace("gain haste", "gain flying"),
            ". ".join(reversed(clauses)) + ".",
            card.oracle_text + " Rewrite the game history.",
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                capability_registry=self.registry).status, text)

    def test_raw_transfer_scope_cannot_change_target_recipient_or_the_application_context(self):
        _, program = self.source_program()
        effect, = program.effects
        for changed in (
            replace(program, event="activate"),
            replace(program, target_schema={**program.target_schema, "player_relation": "any"}),
            replace(program, effects=({**effect, "opponent": "$target.1"},)),
            replace(program, effects=({**effect, "opponent": "$controller"},)),
            replace(program, effects=({**effect, "duration": "zone_object"},)),
            replace(program, effects=({**effect, VERSION_FIELD: True},)),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def scene(self, seed, *, one_sided=False, control_source=False, veil=False):
        game = self.session(seed)
        engine = game.engine
        spell = self.add(engine, ORIGINAL, zone="hand")
        own = None if one_sided else self.add(engine, "Grizzly Bears", ref="reins-own")
        opposite = self.add(engine, "Savannah Lions", seat="B", ref="reins-opponent")
        bystander = self.add(engine, "Goblin Piker", seat="C", ref="reins-third-player")
        control = self.add(engine, "Rubinia Soulsinger") if control_source else None
        response = self.add(engine, "Veil of Summer", seat="B", zone="hand") if veil else None
        if response is not None:
            self.add(engine, "Forest", seat="B")
        for card in (own, opposite, bystander):
            if card is not None:
                card.tapped = True
        for card in (spell, control, response):
            if card is not None:
                self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "U": 2})
        self.checkpoint(game)
        return game, spell, own, opposite, bystander, control, response

    def live(self, game, card):
        return game.state.cards[card.object_id]

    def haste(self, game, card):
        values = game.engine._effective_card_data(self.live(game, card)).get("keywords", ())
        return "haste" in {value.casefold() for value in values}

    def test_real_untap_swap_and_haste_are_limited_to_the_two_players_then_expire_together(self):
        game, spell, own, opposite, bystander, _, _ = self.scene(61342501)
        self.cast_for(game, "A", spell, ["B"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("B", "A"), (self.live(game, own).controller, self.live(game, opposite).controller),
                         "Both original participant groups must get the opposing controller")
        self.assertEqual(("A", "B"), (self.live(game, own).owner, self.live(game, opposite).owner))
        for card in (own, opposite):
            self.assertFalse(self.live(game, card).tapped)
            self.assertTrue(self.haste(game, card))
        self.assertEqual(("C", True), (self.live(game, bystander).controller, self.live(game, bystander).tapped))
        self.assertFalse(self.haste(game, bystander))
        self.attack(game, [(self.live(game, opposite), "B")])
        self.assertIn(opposite.object_id, game.state.combat.attackers)
        self.until(game, lambda trial: trial.state.active_player == "B" and trial.state.step == "upkeep")
        self.assertEqual(("A", "B"), (self.live(game, own).controller, self.live(game, opposite).controller))
        self.assertFalse(self.haste(game, own))
        self.assertFalse(self.haste(game, opposite))
        self.replay(game, load=True)

    def test_empty_caster_group_does_not_prevent_transfer_or_haste_after_a_stun_replacement(self):
        game, spell, _, opposite, bystander, _, _ = self.scene(61342502, one_sided=True)
        self.live(game, opposite).counters["stun"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", spell, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("A", True, 0), (self.live(game, opposite).controller, self.live(game, opposite).tapped,
                                         self.live(game, opposite).counters.get("stun", 0)))
        self.assertTrue(self.haste(game, opposite))
        self.assertEqual("C", self.live(game, bystander).controller)
        self.replay(game, load=True)

    def test_untapping_a_control_source_returns_its_creature_before_the_two_transfer_sets_are_read(self):
        game, spell, own, opposite, bystander, control, _ = self.scene(61342503, control_source=True)
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        ability, = [row["id"] for row in rows if row["id"].startswith("activate:" + control.ref + ":")]
        result = game.act("pilot:A", {"action_id": ability, "targets": [bystander.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual("A", self.live(game, bystander).controller)
        self.assertTrue(self.live(game, bystander).tapped)
        self.cast_for(game, "A", spell, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("C", False), (self.live(game, bystander).controller, self.live(game, bystander).tapped))
        self.assertFalse(self.haste(game, bystander))
        self.assertEqual(("B", "B", "A"), (self.live(game, control).controller, self.live(game, own).controller,
                                            self.live(game, opposite).controller))
        self.replay(game, load=True)

    def test_actual_player_hexproof_invalidates_the_entire_untap_transfer_and_haste_result(self):
        game, spell, own, opposite, bystander, _, veil = self.scene(61342504, veil=True)
        self.cast_for(game, "A", spell, ["B"])
        self.cast_for(game, "B", veil, [])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("A", "B", "C"), tuple(self.live(game, card).controller for card in (own, opposite, bystander)))
        self.assertTrue(all(self.live(game, card).tapped for card in (own, opposite, bystander)))
        self.assertFalse(any(self.haste(game, card) for card in (own, opposite, bystander)))
        self.replay(game, load=True)

    def test_omitting_the_control_batch_fails_the_real_two_sided_transfer(self):
        with patch("quorune.control_effects.gain_control_of_assignments", return_value=()):
            with self.assertRaisesRegex(AssertionError, "Both original participant groups must get the opposing controller"):
                self.test_real_untap_swap_and_haste_are_limited_to_the_two_players_then_expire_together()
