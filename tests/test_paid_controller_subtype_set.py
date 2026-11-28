"""Native paid-X layer-four and layer-seven creature changes."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.creature_subtypes import CREATURE_SUBTYPES, canonical_creature_subtype
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.creature_subtype_set_results import PAID_CONTROLLER_SUBTYPE_SET_CAPABILITY, PAID_CONTROLLER_SUBTYPE_SET_FIELD
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database


class PaidControllerSubtypeSetTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "paid-controller-subtype-set.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/paid-controller-subtype-set-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Paid controller types and base stats", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_original_requires_the_exact_mana_only_x_activation(self):
        card = self.db.lookup("Mirror Entity", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertTrue(any(row.kind == "static_ability" for row in ir.faces[0].nodes))
        node, = (row for row in ir.faces[0].nodes if row.event == "activate")
        self.assertIn(PAID_CONTROLLER_SUBTYPE_SET_CAPABILITY, node.capability_dependencies)
        self.assertIsNone(node.target_schema)
        self.assertEqual(1, node.cost["announced_x_symbols"])
        for old, new in (("{X}:", "{X}, {T}:"), ("you control", "target player controls"),
                         ("X/X", "X/1"), ("all creature types", "flying")):
            with self.subTest(changed=old):
                changed = self.compile(replace(card, oracle_text=card.oracle_text.replace(old, new)))
                self.assertFalse(any(PAID_CONTROLLER_SUBTYPE_SET_CAPABILITY in row.capability_dependencies
                                     for row in changed.faces[0].nodes))
        game = self.session(750050000)
        source = self.add(game.engine, card.name)
        self.assert_closed(game, source)
        program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id) if row.event == "activate")
        for changed in (replace(program, cost_schema={**program.cost_schema, "announced_x_symbols": 2}),
                        replace(program, effects=({**program.effects[0], PAID_CONTROLLER_SUBTYPE_SET_FIELD: 1},))):
            with self.subTest(cost=changed.cost_schema, effects=changed.effects):
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                      profile="commander_review")
                self.assertFalse(bound["strict"], bound)

    def prepare_payment(self, game, mana, *, opponent_mana=None):
        self.prepare(game, mana=mana)
        if opponent_mana:
            game.state.players["B"].mana_pool.update(opponent_mana)
        game.state.phase, game.state.step = "postcombat_main", "main"
        game.state.phase_index = TURN_STEPS.index((game.state.phase, game.state.step))
        self.checkpoint(game)

    def activate(self, game, source, amount):
        self.priority(game, "A")
        row, = (row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                if row["id"].startswith("activate:" + source.ref + ":") and row.get("choice_schema", {}).get("x"))
        command = {"action_id": row["id"], "x": amount, "pay": "auto"}
        initial = authoritative_state_hash(game.state)
        for seat, response in (("B", command), ("A", {**command, "x": True}), ("A", {**command, "x": 99})):
            self.assertFalse(game.act("pilot:" + seat, response).ok)
            self.assertEqual(initial, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(amount, game.state.stack[-1].x_value)

    def subtypes(self, game, card):
        types = game.engine._effective_type_parts(game.state.cards[card.object_id])
        return {canonical_creature_subtype(value) for value in types[1]} - {None}

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, key) for key in ("power", "toughness"))

    def test_actual_paid_x_locks_current_creatures_after_source_leaves_and_replays(self):
        game = self.session(750050001)
        source = self.add(game.engine, "Mirror Entity")
        own = self.add(game.engine, "Ornithopter")
        own.counters["+1/+1"] = 1
        rival = self.add(game.engine, "Grizzly Bears", seat="B")
        ring = self.add(game.engine, "Sol Ring")
        flash = self.add(game.engine, "Spectral Sailor", zone="hand")
        late = self.add(game.engine, "Llanowar Elves", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        for card in (source, flash, late, bounce):
            self.assert_closed(game, card)
        self.prepare_payment(game, {"C": 3, "U": 4, "G": 4}, opponent_mana={"U": 1})
        self.activate(game, source, 3)
        self.cast_for(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual(CREATURE_SUBTYPES, self.subtypes(game, source))
        self.cast_for(game, "A", flash)
        self.resolve_top(game)
        self.assertEqual({"spirit", "pirate"}, self.subtypes(game, flash))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual((4, 4), self.stats(game, own))
        self.assertEqual((3, 3), self.stats(game, flash))
        for card in (own, flash):
            self.assertEqual(CREATURE_SUBTYPES, self.subtypes(game, card))
        self.assertEqual({"bear"}, self.subtypes(game, rival))
        self.assertEqual((2, 2), self.stats(game, rival))
        self.assertFalse(self.subtypes(game, ring))
        self.cast_for(game, "A", late)
        self.resolve_top(game)
        self.assertEqual((1, 1), self.stats(game, late))
        self.assertEqual({"elf", "druid"}, self.subtypes(game, late))
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn
                   or current.state.pending_decision.kind == "cleanup.discard")
        if game.state.turn_sequence == turn:
            choice = game.packet("pilot:A", full=True)["decision"]["ctx"]
            result = game.act("pilot:A", {"action_id": "discard",
                                         "cards": [row["id"] for row in choice["hand"][:choice["count"]]]})
            self.assertTrue(result.ok, result.summary)
            self.until(game, lambda current: current.state.turn_sequence > turn)
        self.assertEqual({"thopter"}, self.subtypes(game, own))
        self.assertEqual((1, 3), self.stats(game, own))
        self.assertEqual({"spirit", "pirate"}, self.subtypes(game, flash))
        self.replay(game, load=True)

    def test_actual_zero_x_uses_normal_state_based_actions_and_counter_layers(self):
        game = self.session(750050002)
        source = self.add(game.engine, "Mirror Entity")
        survives = self.add(game.engine, "Grizzly Bears")
        survives.counters["+1/+1"] = 1
        opponent = self.add(game.engine, "Grizzly Bears", seat="B", ref="mirror-zero-opponent")
        self.assert_closed(game, source)
        self.prepare_payment(game, {})
        self.activate(game, source, 0)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[survives.object_id].zone)
        self.assertEqual((1, 1), self.stats(game, survives))
        self.assertEqual(CREATURE_SUBTYPES, self.subtypes(game, survives))
        self.assertEqual((2, 2), self.stats(game, opponent))
        self.assertEqual({"bear"}, self.subtypes(game, opponent))
        self.replay(game, load=True)
