"""Native cast-only revival counters and one-shot next-end-step returns."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_counter_model import CAST_REVIVAL_ENTRY_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.revival_return import CAPABILITY, COUNT_FIELD, VERSION_FIELD
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database


class RevivalReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "revival-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/revival-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Revival return", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_original_revival_capabilities_require_exact_condition_and_return_timing(self):
        card = self.db.lookup("Nine-Lives Familiar", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual({CAST_REVIVAL_ENTRY_CAPABILITY, CAPABILITY} & {
            cap for node in ir.faces[0].nodes for cap in node.capability_dependencies
        }, {CAST_REVIVAL_ENTRY_CAPABILITY, CAPABILITY})
        for cap, text, variant in ((CAST_REVIVAL_ENTRY_CAPABILITY, "eight revival", "seven revival"),
                                   (CAPABILITY, "beginning of the next end step", "beginning of your upkeep")):
            with self.subTest(changed=text):
                updated = self.compile(replace(card, oracle_text=card.oracle_text.replace(text, variant)))
                self.assertFalse(any(cap in node.capability_dependencies for node in updated.faces[0].nodes))
        game = self.session(748048000)
        source = self.add(game.engine, card.name)
        self.assert_closed(game, source)
        program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if row.event == "creature.dies.self")
        for changed in (replace(program, event_condition=None),
                        replace(program, effects=({**program.effects[0], VERSION_FIELD: True},))):
            with self.subTest(condition=changed.event_condition):
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                      profile="commander_review")
                self.assertFalse(bound["strict"], bound)

    def prepare_end(self, game, *, own, rival):
        self.prepare(game, mana=own)
        game.state.players["B"].mana_pool.update(rival)
        game.state.phase, game.state.step = "postcombat_main", "main"
        game.state.phase_index = TURN_STEPS.index((game.state.phase, game.state.step))
        self.checkpoint(game)

    def kill(self, game, source, answer):
        self.cast_for(game, "B", answer, [game.state.cards[source.object_id].ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)

    def end_step(self, game, source):
        self.until(game, lambda session: session.state.step == "end_step" and self.trigger_count(session, source) == 1)
        self.resolve_top(game)

    def test_actual_cast_doubled_death_count_and_delayed_entry_replacement_replay(self):
        game = self.session(748048001)
        source = self.add(game.engine, "Nine-Lives Familiar", zone="hand")
        season = self.add(game.engine, "Doubling Season")
        kill = self.add(game.engine, "Murder", seat="B", zone="hand")
        for card in (source, season, kill):
            self.assert_closed(game, card)
        self.prepare_end(game, own={"C": 1, "B": 2}, rival={"C": 1, "B": 2})
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual(16, current.counters["revival"])
        old_incarnation = current.logical_object_id
        self.kill(game, source, kill)
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        delayed, = game.state.delayed_triggers
        self.assertEqual(16, delayed.stack_template["context"]["dynamic_effects"][0][COUNT_FIELD])
        game = self.replay(game, load=True)
        self.end_step(game, source)
        current = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", "A", 30), (current.zone, current.controller, current.counters["revival"]))
        self.assertNotEqual(old_incarnation, current.logical_object_id)
        self.assertFalse(any(row.active for row in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_reanimated_entry_gets_no_cast_counters_or_death_return(self):
        game = self.session(748048002)
        source = self.add(game.engine, "Nine-Lives Familiar", zone="graveyard")
        raise_dead = self.add(game.engine, "Reanimate", zone="hand")
        kill = self.add(game.engine, "Murder", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, raise_dead)
        self.prepare_end(game, own={"B": 1}, rival={"C": 1, "B": 2})
        self.cast_for(game, "A", raise_dead, [source.ref])
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertNotIn("revival", current.counters)
        self.kill(game, source, kill)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertFalse(any(row.active for row in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_last_counter_returns_with_zero_and_exiled_arrival_cannot_return(self):
        for exiled in (False, True):
            with self.subTest(exiled=exiled):
                game = self.session(748048003 + exiled)
                source = self.add(game.engine, "Nine-Lives Familiar", seat="A" if exiled else "B")
                source.counters["revival"] = 1
                if not exiled:
                    # This public checkpoint begins after A took control of B's permanent.
                    source.controller = "A"
                    game.state.players["B"].zones["battlefield"].remove(source.object_id)
                    game.state.players["A"].zones["battlefield"].append(source.object_id)
                kill = self.add(game.engine, "Murder", seat="B", zone="hand")
                answer = self.add(game.engine, "Cremate", seat="B", zone="hand") if exiled else None
                self.prepare_end(game, own={}, rival={"C": 1, "B": 3})
                self.kill(game, source, kill)
                self.assertEqual(1, self.trigger_count(game, source))
                self.resolve_top(game)
                if answer is not None:
                    arrival = game.state.cards[source.object_id]
                    self.cast_for(game, "B", answer, [arrival.ref])
                    self.resolve_top(game)
                    self.assertEqual("exile", game.state.cards[source.object_id].zone)
                self.end_step(game, source)
                current = game.state.cards[source.object_id]
                self.assertEqual("exile" if exiled else "battlefield", current.zone)
                if not exiled:
                    self.assertEqual(("B", "A"), (current.owner, current.controller))
                self.assertNotIn("revival", current.counters)
                self.assertFalse(any(row.active for row in game.state.delayed_triggers))
                self.replay(game, load=True)
