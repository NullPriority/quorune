"""Native targeted upkeep transfer and source-incarnation replay evidence."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.source_counter_distribution import SOURCE_COUNTER_TRANSFER_CAPABILITY
from scripts.build_test_database import build_fixture_database


class SourceUpkeepTargetedTransferTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-upkeep-targeted-transfer.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/source-upkeep-targeted-transfer-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Targeted upkeep transfer", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_printed_entry_and_targeted_upkeep_bind_exactly(self):
        card = self.db.lookup("Scrounging Bandar", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        entry, transfer = ir.faces[0].nodes
        self.assertEqual("zone.change", entry.event)
        self.assertEqual("step.begin", transfer.event)
        self.assertIn(SOURCE_COUNTER_TRANSFER_CAPABILITY, transfer.capability_dependencies)
        self.assertEqual(2, transfer.effects[0]["schema_version"])
        self.assertEqual("$target.0", transfer.effects[0]["target"])
        self.assertIs(True, transfer.target_schema["source_exclusion"])
        for old, new in (("your upkeep", "your end step"), ("+1/+1 counters from", "charge counters from"),
                         ("another target creature", "another target permanent")):
            with self.subTest(changed=old):
                self.assertNotEqual("exact", self.compile(replace(card, oracle_text=card.oracle_text.replace(old, new))).status)

        game = self.session(74203700)
        source = self.add(game.engine, card.name, zone="hand")
        self.assert_closed(game, source)
        program, = (candidate for candidate in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if candidate.event == "step.begin")
        schema = dict(program.target_schema)
        del schema["source_exclusion"]
        mutants = (replace(program, target_schema=schema), replace(program, event="spell.cast"),
                   replace(program, effects=({**program.effects[0], "schema_version": 1},)))
        for candidate in mutants:
            with self.subTest(candidate=candidate.event):
                bind = bind_semantic_program_runtime(candidate, capability_registry=self.registry, profile="commander_review")
                self.assertFalse(bind["strict"], bind)

        self.prepare(game, mana={"G": 1, "C": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.assertEqual(2, game.state.cards[source.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def upkeep_scene(self, seed, *, response=None):
        game = self.session(seed)
        donor = self.add(game.engine, "Scrounging Bandar")
        donor.counters.update({"+1/+1": 4, "charge": 1})
        target = self.add(game.engine, "Grizzly Bears", seat="C", ref="upkeep-receiver")
        distractor = self.add(game.engine, "Grizzly Bears", ref="other-legal-creature")
        land = self.add(game.engine, "Plains", seat="D", ref="not-a-creature")
        reply = self.add(game.engine, response, seat="B", zone="hand") if response else None
        if response == "Turn Against":
            for index in range(4):
                self.add(game.engine, "Plains", seat="B", ref=f"turn-against-land-{index}")
            self.add(game.engine, "Mountain", seat="B")
        if response == "Unsummon":
            self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, donor)
        if reply is not None:
            self.assert_closed(game, reply)
        previous = game.state.turn_order[game.state.turn_order.index("A") - 1]
        game.state.last_normal_turn_player = previous
        self.prepare(game, actor=previous, active=previous)
        self.checkpoint(game)
        self.until(game, lambda current: current.state.active_player == "A" and current.state.step == "upkeep"
                   and current.state.pending_decision is not None and current.state.pending_decision.kind == "semantic.target")
        return game, donor, target, distractor, land, reply

    def reject(self, game, seat, **choice):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", **choice})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def choose_amount(self, game, allocation):
        result = game.act("pilot:A", {"action_id": "choose", "counter_allocation": allocation})
        self.assertTrue(result.ok, result.summary)

    def test_actual_target_and_variable_quantity_remain_with_trigger_controller_after_theft(self):
        game, donor, target, distractor, land, theft = self.upkeep_scene(74203701, response="Turn Against")
        self.reject(game, "A", targets=[donor.ref])
        self.reject(game, "A", targets=[land.ref])
        self.reject(game, "B", targets=[target.ref])
        game = self.replay(game, load=True)
        self.target(game, "A", [target.ref])
        self.cast_for(game, "B", theft, [donor.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[donor.object_id].controller)
        self.assertEqual("A", game.state.stack[-1].controller)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        decision = game.packet("pilot:A", full=True)["decision"]
        choice, = decision["ctx"]["legal_actions"]
        self.assertEqual([target.ref], choice["choice_schema"]["legal_refs"])
        self.assertEqual(4, choice["choice_schema"]["maximum_total"])
        self.reject(game, "A", counter_allocation={target.ref: 5})
        self.reject(game, "A", counter_allocation={distractor.ref: 1})
        self.reject(game, "B", counter_allocation={target.ref: 2})
        self.choose_amount(game, {target.ref: 3})
        self.assertEqual({"+1/+1": 1, "charge": 1}, game.state.cards[donor.object_id].counters)
        self.assertEqual({"+1/+1": 3}, game.state.cards[target.object_id].counters)
        self.assertFalse(game.state.cards[distractor.object_id].counters)
        self.replay(game, load=True)

    def test_actual_decline_and_illegal_target_move_nothing(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                game, donor, target, _, _, bounce = self.upkeep_scene(
                    74203710 + stale, response="Unsummon" if stale else None,
                )
                self.target(game, "A", [target.ref])
                if stale:
                    self.cast_for(game, "B", bounce, [target.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                pending = self.resolve(game)
                if stale:
                    self.assertIsNone(pending)
                    self.assertEqual("hand", game.state.cards[target.object_id].zone)
                else:
                    self.assertEqual("semantic.choice", pending.kind)
                    self.choose_amount(game, {})
                self.assertEqual(4, game.state.cards[donor.object_id].counters["+1/+1"])
                self.assertFalse(game.state.cards[target.object_id].counters)
                self.replay(game, load=True)
