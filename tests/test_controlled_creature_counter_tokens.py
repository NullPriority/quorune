"""Controller-at-placement history and native Hornbeetle count replay."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.turn_step_owner import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.controlled_creature_counter_tokens import CAPABILITY, COUNT_FIELD
from quorune.turn_history_model import CONTROLLED_CREATURE_COUNTER_HISTORY_EVENT, PLUS_ONE_COUNTERS_PLACED_EVENT, TurnHistory
from scripts.build_test_database import build_fixture_database


class ControlledCreatureCounterTokensTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-creature-counter-tokens.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controlled-creature-counter-token-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Controlled creature placements", [DeckEntry(commander, 1, "commander"),
                                  DeckEntry("Plains", 30)], [commander])

    def compile(self, record):
        return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

    def test_printed_end_step_and_versioned_history_shape_fail_closed(self):
        card = self.db.lookup("Iridescent Hornbeetle", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(CAPABILITY, node.capability_dependencies)
        self.assertEqual("step.begin", node.event)
        self.assertEqual(1, node.effects[0][COUNT_FIELD])
        for old, new in (("your end step", "each end step"), ("you've put", "put on"),
                         ("creatures under your control", "creatures your opponents control")):
            with self.subTest(changed=old):
                changed = self.compile(replace(card, oracle_text=card.oracle_text.replace(old, new)))
                self.assertFalse(any(CAPABILITY in node.capability_dependencies for node in changed.faces[0].nodes))
        game = self.session(748046000)
        source = self.add(game.engine, card.name)
        self.assert_closed(game, source)
        program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id) if row.event == "step.begin")
        for mutant in (replace(program, event_condition=None),
                       replace(program, effects=({**program.effects[0], COUNT_FIELD: True},))):
            with self.subTest(condition=mutant.event_condition):
                bind = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile="commander_review")
                self.assertFalse(bind["strict"], bind)
        initial = game.state.turn_history.to_dict()
        key = "controlled_creature_counter_history_version"
        self.assertEqual(1, initial[key])
        old = {name: value for name, value in initial.items() if name != key}
        self.assertEqual(old, TurnHistory.from_dict(old).to_dict())
        for value in (None, True, 2):
            with self.subTest(version=value), self.assertRaises(ValueError):
                TurnHistory.from_dict({**old, key: value})

    def history(self, game, kind):
        return [(event.actor, event.object_incarnation, event.amount)
                for event in game.state.turn_history.events if event.kind == kind]

    def insects(self, game):
        return [card for card in game.state.cards.values()
                if card.is_token and card.owner == "A" and card.zone == "battlefield"
                and "insect" in game.engine._type_parts(game.engine._effective_card_data(card)["type_line"])[1]]

    def test_actual_finalized_amount_placement_control_actor_and_turn_replay(self):
        game = self.session(748046001)
        source = self.add(game.engine, "Iridescent Hornbeetle", zone="hand")
        mine = self.add(game.engine, "Grizzly Bears", ref="countered-ours")
        rival = self.add(game.engine, "Grizzly Bears", seat="C", ref="countered-foreign")
        scales = self.add(game.engine, "Hardened Scales")
        bond = self.add(game.engine, "Common Bond", zone="hand")
        growth = self.add(game.engine, "Battlegrowth", seat="B", zone="hand")
        theft = self.add(game.engine, "Turn Against", seat="B", zone="hand")
        for card in (source, scales, bond, growth, theft):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "G": 2, "W": 1})
        game.state.players["B"].mana_pool.update({"C": 4, "G": 1, "R": 1})
        game.state.phase, game.state.step = TURN_STEPS[TURN_STEPS.index(("ending", "end_step")) - 1]
        game.state.phase_index = TURN_STEPS.index((game.state.phase, game.state.step))
        self.checkpoint(game)
        own_id = game.state.cards[mine.object_id].logical_object_id
        foreign_id = game.state.cards[rival.object_id].logical_object_id
        self.cast_for(game, "A", bond, [mine.ref, rival.ref])
        self.resolve_top(game)
        self.assertEqual([("A", own_id, 2), ("A", foreign_id, 1)], self.history(game, PLUS_ONE_COUNTERS_PLACED_EVENT))
        self.assertEqual([("A", own_id, 2)], self.history(game, CONTROLLED_CREATURE_COUNTER_HISTORY_EVENT))
        self.cast_for(game, "B", growth, [mine.ref])
        self.resolve_top(game)
        self.assertEqual(("B", own_id, 2), self.history(game, PLUS_ONE_COUNTERS_PLACED_EVENT)[-1])
        self.assertEqual([("A", own_id, 2)], self.history(game, CONTROLLED_CREATURE_COUNTER_HISTORY_EVENT))
        self.cast_for(game, "B", theft, [mine.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[mine.object_id].controller)
        self.cast_for(game, "A", source, [])
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.until(game, lambda current: current.state.step == "end_step"
                   and self.trigger_count(current, source) == 1)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(2, len(self.insects(game)))
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn)
        self.assertFalse(self.history(game, CONTROLLED_CREATURE_COUNTER_HISTORY_EVENT))
        self.assertEqual(2, len(self.insects(game)))
        self.replay(game, load=True)
