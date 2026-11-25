"""Resolve Tangle's full original combat prevention and attacking-object restrictions."""

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
from quorune.tap_state import NEXT_UNTAP_PROHIBITION_ANNOTATION
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Tangle"
VERSION_FIELD = "attacking_creatures_next_untap_version"


class AttackingCreatureNextUntapTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "attacking-next-untap.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/attacking-creature-next-untap-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Tangle witness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "resolve"]
        return card, program

    def test_full_original_composes_global_fog_and_an_untargeted_current_attacker_result(self):
        card, program = self.original_program()
        self.assertIsNone(program.target_schema)
        fog, untap = program.effects
        self.assertEqual(("create_damage_prevention_shield", "until_end_of_turn", "combat"),
                         (fog["op"], fog["duration"], fog["damage_kind"]))
        self.assertEqual({"op": "prohibit_next_untap", VERSION_FIELD: 1}, untap)
        for text in (
            card.oracle_text.replace("attacking creature", "blocking creature"),
            card.oracle_text.replace("controller's next untap", "owner's next untap"),
            card.oracle_text.replace("all combat damage", "all damage"),
            "\n".join(reversed(card.oracle_text.splitlines())),
            card.oracle_text + " Rewrite the game history.",
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                capability_registry=self.registry).status, text)

    def test_raw_scope_rejects_alternate_result_order_and_an_unowned_snapshot_source(self):
        _, program = self.original_program()
        fog, untap = program.effects
        for changed in (
            replace(program, event="activate"),
            replace(program, effects=(untap, fog)),
            replace(program, effects=(untap,)),
            replace(program, effects=({**fog, "damage_kind": "any"}, untap)),
            replace(program, effects=(fog, {**untap, VERSION_FIELD: True})),
            replace(program, effects=(fog, {**untap, "card": "$target.0"})),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def annotation(self, game, card):
        return game.state.cards[card.object_id].annotations.get(NEXT_UNTAP_PROHIBITION_ANNOTATION, False)

    def scene(self, seed, *, tokens=False, blink=False, bolt=False):
        game = self.session(seed)
        engine = game.engine
        spell = self.add(engine, ORIGINAL, seat="B", zone="hand")
        recon = self.add(engine, "Reconnaissance")
        warp = self.add(engine, "Time Warp", zone="hand")
        kept = self.add(engine, "Grizzly Bears", ref="tangle-current-attacker")
        removed = self.add(engine, "Savannah Lions", ref="tangle-removed-attacker")
        waiting = self.add(engine, "Goblin Piker", ref="tangle-old-tapped")
        waiting.tapped = True
        controls = []
        cloudshift = self.add(engine, "Cloudshift", zone="hand") if blink else None
        burn = self.add(engine, "Lightning Bolt", seat="B", zone="hand") if bolt else None
        if tokens:
            controls.append(self.add(engine, "Adeline, Resplendent Cathar"))
        for index in range(2):
            self.add(engine, "Forest", seat="B", ref=f"tangle-forest-{index}")
        if burn:
            for index in range(2):
                self.add(engine, "Mountain", seat="B", ref=f"tangle-mountain-{index}")
        if cloudshift:
            self.add(engine, "Plains", ref="tangle-blink-plains")
        for card in (spell, recon, warp, *controls, *(card for card in (cloudshift, burn) if card)):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", warp, ["A"])
        self.resolve_top(game)
        turn, = game.state.extra_turns
        self.attack(game, [(kept, "B"), (removed, "C")])
        self.cast_for(game, "B", spell, [])
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith("activate:" + recon.ref + ":")]
        command = {"action_id": action, "targets": [removed.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertIn(kept.object_id, game.state.combat.attackers)
        self.assertNotIn(removed.object_id, game.state.combat.attackers)
        return game, kept, removed, waiting, cloudshift, burn, turn

    def test_actual_spell_locks_its_current_attacker_and_keeps_global_fog_for_later_attacking_tokens(self):
        game, kept, removed, waiting, _, burn, turn = self.scene(61342401, tokens=True, bolt=True)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertTrue(self.annotation(game, kept), "The current attacking creature must get its own untap restriction")
        self.assertFalse(self.annotation(game, removed))
        self.assertFalse(self.annotation(game, waiting))
        self.assertIsNone(self.resolve(game))
        added = [game.state.cards[object_id] for object_id in game.state.combat.attackers if object_id != kept.object_id]
        self.assertEqual(3, len(added))
        self.assertTrue(all(card.is_token and not self.annotation(game, card) for card in added))
        life_before = {seat: game.state.players[seat].life for seat in "BCD"}
        own_life = game.state.players["A"].life
        self.cast_for(game, "B", burn, ["A"])
        self.resolve_top(game)
        self.assertEqual(own_life - 3, game.state.players["A"].life)
        self.until(game, lambda trial: trial.state.current_turn is not None
                   and trial.state.current_turn.turn_id == turn.turn_id and trial.state.step == "upkeep")
        self.assertEqual(life_before, {seat: game.state.players[seat].life for seat in "BCD"})
        self.assertTrue(game.state.cards[kept.object_id].tapped)
        self.assertFalse(game.state.cards[waiting.object_id].tapped)
        self.assertFalse(self.annotation(game, kept), "The first physical controller untap consumes the restriction")
        self.replay(game, load=True)

    def test_actual_blink_after_resolution_leaves_the_new_incarnation_without_the_old_restriction(self):
        game, kept, removed, _, cloudshift, _, _ = self.scene(61342402, blink=True)
        self.resolve_top(game)
        self.assertTrue(self.annotation(game, kept))
        original = game.state.cards[kept.object_id].logical_object_id
        self.cast_for(game, "A", cloudshift, [kept.ref])
        self.resolve_top(game)
        returned = game.state.cards[kept.object_id]
        self.assertNotEqual(original, returned.logical_object_id)
        self.assertEqual("battlefield", returned.zone)
        self.assertFalse(self.annotation(game, returned))
        self.assertFalse(self.annotation(game, removed))
        self.replay(game, load=True)

    def test_omitting_the_object_local_restriction_fails_the_real_tangle_result(self):
        with patch("quorune.tap_state.prohibit_next_untap", return_value=""):
            with self.assertRaisesRegex(AssertionError, "The current attacking creature must get its own untap restriction"):
                self.test_actual_spell_locks_its_current_attacker_and_keeps_global_fog_for_later_attacking_tokens()
