"""Native APNAP draw-result attribution and whole-card admission."""

from dataclasses import replace
from hashlib import file_digest
import json
import os
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
from quorune.record import authoritative_state_hash, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.opponent_draw_followup import DISTINCT_DRAW_CAPABILITY, DISTINCT_DRAW_PHASE
from scripts.build_test_database import build_fixture_database


ORIGINAL = "Cut a Deal"
PROFILE = "commander_review"


class CutADealTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "cut-a-deal.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database(
            [fixtures / name for name in (
                "bound-effect-program-cards.json", "faction-entry-trigger-cards.json",
                "paired-opponent-draw-cards.json", "redirected-draw-cards.json", "cut-a-deal-cards.json",
            )],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Opponent draw follow-up",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30)], [commander])

    @staticmethod
    def _draw_order(game, start):
        return [event.actor for event in game.state.events
                if event.event_id > start and event.code == "card.draw"]

    def _record(self, game, name):
        target = os.environ.get("QUORUNE_DISTINCT_DRAW_RECORD_PARENT")
        if target is None:
            self.replay(game, load=True)
            return
        destination = Path(target) / name
        game.save(destination)
        result = replay_record(destination, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(authoritative_state_hash(game.state), result["final_state_hash"])
        with (destination / "manifest.json").open("rb") as handle:
            digest = file_digest(handle, "sha256").hexdigest()
        receipt = {
            "manifest_sha256": digest,
            "commands": len(game.commands),
            "final_state_sha256": authoritative_state_hash(game.state),
        }
        destination.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    def _replacement_scene(self, seed):
        game = self.session(seed)
        spell = self.add(game.engine, ORIGINAL, seat="C", zone="hand")
        dredger = self.add(game.engine, "Life from the Loam", seat="B", zone="graveyard")
        doubler = self.add(game.engine, "Thought Reflection", seat="D")
        timing = self.add(game.engine, "Vedalken Orrery", seat="C")
        for card in (spell, doubler, timing):
            self.assert_closed(game, card)
        self.prepare(game)
        self.assertEqual("A", game.state.active_player)
        game.state.players["C"].mana_pool.update({"W": 1, "C": 2})
        self.checkpoint(game)
        start = game.state.events[-1].event_id
        self.cast_for(game, "C", spell)
        return game, spell, dredger, start

    def test_off_turn_apnap_dredge_accept_or_decline_and_double_counts_one(self):
        for dredge in (False, True):
            with self.subTest(dredge=dredge):
                game, spell, dredger, start = self._replacement_scene(61126081 + dredge)
                pending = self.resolve(game)
                self.assertEqual(("draw.replacement", ["B"]), (pending.kind, pending.actors))
                committed_before_pause = self._draw_order(game, start)
                self.assertEqual(["A"], committed_before_pause)
                resume_after = game.state.events[-1].event_id
                command = {"action_id": "choose", "choice": dredger.ref if dredge else "draw"}
                self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:C", command).ok)
                self.assertFalse(game.act("pilot:B", {"action_id": "choose", "choice": "forged"}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                result = game.act("pilot:B", command)
                self.assertTrue(result.ok, result.summary)
                self.settle(game)
                expected = ["A", "D", "D", "C", "C"] if dredge else ["A", "B", "D", "D", "C", "C", "C"]
                self.assertEqual(expected, committed_before_pause + self._draw_order(game, resume_after))
                self.assertEqual("hand" if dredge else "graveyard", game.state.cards[dredger.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                hidden_ref = game.state.players["D"].draw_history[-1]["object"]
                for seat in "ABC":
                    self.assertNotIn(hidden_ref, json.dumps(game.packet("pilot:" + seat, full=True)))
                self.assertIn(hidden_ref, json.dumps(game.packet("pilot:D", full=True)))
                self._record(game, "cut-a-deal-dredge-" + ("accept" if dredge else "decline"))

    def test_draw_limits_exclude_prior_drawers_and_cap_the_controller_reward(self):
        game = self.session(61126083)
        spell = self.add(game.engine, ORIGINAL, zone="hand")
        prior = self.add(game.engine, "Reach Through Mists", seat="B", zone="hand")
        limit = self.add(game.engine, "Spirit of the Labyrinth", seat="D")
        for card in (spell, prior, limit):
            self.assert_closed(game, card)
        self.prepare(game, mana={"W": 1, "C": 2})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        baseline = len(game.state.players["B"].draw_history)
        self.cast_for(game, "B", prior)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(baseline + 1, len(game.state.players["B"].draw_history))
        start = game.state.events[-1].event_id
        self.cast_for(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["C", "D", "A"], self._draw_order(game, start))
        denied = [event.actor for event in game.state.events
                  if event.event_id > start and event.code == "card.draw.prohibited"]
        self.assertEqual(["B", "A"], denied)
        self.assertEqual(baseline + 1, len(game.state.players["B"].draw_history))
        self._record(game, "cut-a-deal-draw-limits")

    def test_redirected_draws_count_their_actual_opponent_and_exclude_the_controller(self):
        for thief, expected in (("A", ["A"] * 3), ("C", ["C"] * 4)):
            with self.subTest(thief=thief):
                game = self.session(61126084 + (thief == "C"))
                spell = self.add(game.engine, ORIGINAL, zone="hand")
                redirect = self.add(game.engine, "Notion Thief", seat=thief)
                for card in (spell, redirect):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"W": 1, "C": 2})
                self.checkpoint(game)
                start = game.state.events[-1].event_id
                self.cast_for(game, "A", spell)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(expected, self._draw_order(game, start))
                self._record(game, "cut-a-deal-redirect-" + thief)

    def test_exact_spell_binding_and_runtime_authority_for_both_ordered_phases(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        game = self.session(61126087)
        spell = self.add(game.engine, ORIGINAL, zone="hand")
        self.assert_closed(game, spell)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(("opponents", "controller"), tuple(effect[DISTINCT_DRAW_PHASE] for effect in program.effects))
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(card.oracle_text)), (node.span.start, node.span.end))
        dependencies = tuple(dep for dep in program.capability_dependencies if dep != DISTINCT_DRAW_CAPABILITY)
        missing = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        wrong_order = replace(program, effects=tuple(reversed(program.effects)))
        wrong_schema = replace(program, effects=({**program.effects[0], "schema_version": True}, program.effects[1]))
        for changed in (missing, wrong_order, wrong_schema):
            check = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(check["strict"], check)
        neighboring_oracle = card.oracle_text.replace("each opponent who drew a card", "each player who drew a card")
        other = compile_oracle_card(replace(card, oracle_text=neighboring_oracle),
                                    capability_registry=self.registry, capability_profile=PROFILE)
        self.assertTrue(other.material_residuals)

    def test_dropped_replacement_observer_mutant_fails_the_existing_native_result(self):
        game, _, _, start = self._replacement_scene(61126086)
        self.assertIsNotNone(self.resolve(game))
        with patch("quorune.drawing.coordinator._retain_committed_draw_observers",
                   side_effect=lambda draw, original: draw):
            result = game.act("pilot:B", {"action_id": "choose", "choice": "draw"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(["A", "B", "D", "D", "C", "C"], self._draw_order(game, start))
