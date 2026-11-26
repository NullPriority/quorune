from dataclasses import replace
import json
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
from quorune.rules.opponent_library_exile_triggers import DRAW_THEN_OPPONENT_EXILE_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

CARD_NAME = "Fathom Feeder"
PROFILE = "commander_review"


class FathomFeederTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fathom-feeder.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fathom-feeder-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Ordered draw and opposing tops", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def top(self, host, name, seat, ref):
        card = self.add(host, name, seat=seat, zone="library", ref=ref)
        host.move_card(card.object_id, "library", position="top", log=False)
        card.known_to.clear()
        card.revealed_to.clear()
        return card

    def test_paid_activation_draws_before_opponent_top_exile_and_independent_ingest_replays(self):
        game = self.session(12170114)
        host = game.engine
        source = self.add(host, CARD_NAME)
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        own = self.top(host, "Plains", "A", "fathom-private-draw")
        b_next = self.top(host, "Mountain", "B", "fathom-b-next")
        b_top = self.top(host, "Plains", "B", "fathom-b-first")
        d_next = self.top(host, "Mountain", "D", "fathom-d-next")
        d_top = self.top(host, "Plains", "D", "fathom-d-first")
        for object_id in tuple(game.state.players["C"].zones["library"]):
            host.move_card(object_id, "outside", log=False)
        self.prepare(game, mana={"C": 3, "U": 1, "B": 1})
        self.checkpoint(game)
        grant, = [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                  if row["id"].startswith(f"activate:{source.ref}:")]
        command = {"action_id": grant["id"], "pay": "manual", "payment": {"C": 3, "U": 1, "B": 1}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.assertFalse(game.state.cards[source.object_id].tapped)
        game = self.replay(game, load=True)
        draw_at_exile = []
        logger = game.engine._log

        def watch(actor, code, *args, **kwargs):
            if code == "library.opponents_exile_top":
                draw_at_exile.append(game.state.cards[own.object_id].zone)
            return logger(actor, code, *args, **kwargs)

        with patch.object(game.engine, "_log", side_effect=watch):
            self.resolve_top(game)
        self.assertEqual(["hand"], draw_at_exile)
        exile_event, = [event for event in game.state.events if event.code == "library.opponents_exile_top"]
        self.assertEqual([b_top.ref, d_top.ref], exile_event.details["objects"])
        for card in (b_top, d_top):
            current = game.state.cards[card.object_id]
            self.assertEqual(("exile", False), (current.zone, current.face_down))
            self.assertNotIn("temporary_play_permission", current.annotations)
        for card in (b_next, d_next):
            self.assertEqual("library", game.state.cards[card.object_id].zone)
        self.assertTrue(game.state.players["C"].in_game)
        self.assertEqual([], game.state.players["C"].zones["library"])
        for seat in "BCD":
            projected = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(own.ref, projected)
            self.assertIn(b_top.ref, projected)
            self.assertIn(d_top.ref, projected)

        self.attack(game, [(game.state.cards[source.object_id], "B")])
        self.until(game, lambda current: self.trigger_count(current, source) == 1)
        self.assertEqual(39, game.state.players["B"].life)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[b_next.object_id].zone)
        self.assertEqual("library", game.state.cards[d_next.object_id].zone)
        self.replay(game, load=True)

    def test_activated_sequence_is_closed_separately_from_ingest(self):
        original = self.db.lookup(CARD_NAME, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(original, **options).status)
        report, semantics = register_card(self.db, self.registry, CARD_NAME)
        self.assertTrue(report["strict_capability_ready"], report)
        programs = semantics.programs_for_oracle(original.oracle_id)
        program, = (p for p in programs if p.event == "activate")
        self.assertTrue(any("trigger.keyword.ingest.fixed" in p.capability_dependencies for p in programs if p is not program))
        for altered in (
            replace(program, effects=tuple(reversed(program.effects))),
            replace(program, effects=({**program.effects[0], "private": False}, program.effects[1])),
            replace(program, effects=(program.effects[0], {**program.effects[1], "schema_version": 2})),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                altered, capability_registry=self.registry, profile=PROFILE)["strict"])
        deps = tuple(cap for cap in program.capability_dependencies if cap != DRAW_THEN_OPPONENT_EXILE_CAPABILITY)
        omitted = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        report = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + DRAW_THEN_OPPONENT_EXILE_CAPABILITY,
                      report["blockers"])
        with patch("quorune.compiler.opponent_library_exile_templates.draw_then_opponent_top_exile_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(original, **options).status)
