"""The same entering incarnation owns its selected opponent and linked rules."""

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
from quorune.entry_designations import ENTRY_DESIGNATION_CAPABILITY, linked_opponent_upkeep_condition
from quorune.deck import DeckDefinition, DeckEntry
from quorune.maximum_hand_size import effective_maximum_hand_size
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


VISE = "Black Vise"
RACK = "The Rack"
CURSED = "Cursed Rack"


class LinkedEntryOpponentHandRulesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-entry-opponent.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/linked-entry-opponent-hand-rules-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Linked entry opponent", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_original_sources_keep_the_nontargeting_choice_and_all_linked_results(self):
        for name, event in ((VISE, "step.begin"), (RACK, "step.begin"), (CURSED, "characteristics.evaluate")):
            with self.subTest(card=name):
                card = self.db.lookup(name, fuzzy=False)
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                programs = semantics.programs_for_oracle(card.oracle_id)
                self.assertEqual({"zone.change", event}, {program.event for program in programs})
                self.assertTrue(all(program.target_schema is None for program in programs))
                ir = compile_oracle_card(card, capability_registry=self.registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                for text in (
                    card.oracle_text.replace("choose an opponent", "choose a player"),
                    card.oracle_text.replace("choose an opponent", "choose target opponent"),
                    card.oracle_text.split("\n", 1)[-1],
                ):
                    denied = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry)
                    self.assertNotEqual("exact", denied.status)

    def test_linked_raw_programs_reject_wrong_scope_or_missing_entry_authority(self):
        _, semantics = register_card(self.db, self.registry, VISE)
        card = self.db.lookup(VISE, fuzzy=False)
        entry, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "zone.change"]
        trigger, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "step.begin"]
        self.assertEqual(linked_opponent_upkeep_condition(), trigger.event_condition)
        caps = [value for value in trigger.capability_dependencies if value != ENTRY_DESIGNATION_CAPABILITY]
        changed = replace(trigger, capability_dependencies=caps,
                          capability_closure=self.registry.closure(caps, profile="commander_review").to_dict())
        decision = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(decision["strict"], decision)
        self.assertTrue(any(ENTRY_DESIGNATION_CAPABILITY in blocker for blocker in decision["blockers"]))
        for malformed in (
            replace(entry, active_zone="battlefield"),
            replace(trigger, event_condition={"all": [trigger.event_condition["all"][0]]}),
        ):
            decision = bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(decision["strict"], decision)

    def choose_opponent(self, game, card, seat):
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        choice = game.packet("pilot:A", full=True)["decision"]
        options = {item["id"].rsplit(":", 1)[-1]: item["id"] for item in choice["ctx"]["options"]}
        self.assertEqual({"B", "C", "D"}, set(options))
        self.assertNotEqual("battlefield", game.state.cards[card.object_id].zone)
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "choices": {"replacement": options[seat]}}
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        invalid = options[seat].rsplit(":", 1)[0] + ":A"
        self.assertFalse(game.act("pilot:A", {**command, "choices": {"replacement": invalid}}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.settle(game)
        self.assertEqual("battlefield", game.state.cards[card.object_id].zone)

    def upkeep(self, game, seat, source):
        self.until(game, lambda current: current.state.active_player == seat and current.state.step == "upkeep"
                   and any(item.source_object_id == source.object_id for item in current.state.stack))
        item, = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(("triggered_ability", [], seat),
                         (item.kind, item.targets, item.context["player"]))

    def test_actual_vise_reads_the_chosen_hand_on_resolution_after_cast_response_and_source_loss(self):
        game = self.session(60720471)
        engine = game.engine
        source = self.add(engine, VISE, zone="hand")
        draw = self.add(engine, "Opportunity", seat="B", zone="hand")
        destroy = self.add(engine, "Disenchant", seat="B", zone="hand")
        secret = self.add(engine, "Counterspell", seat="D", zone="hand", ref="unrelated-linked-hand")
        for index in range(7):
            self.add(engine, "Island", seat="B", ref=f"linked-vise-blue-{index}")
        self.add(engine, "Plains", seat="B", ref="linked-vise-white")
        for card in (source, draw, destroy):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.choose_opponent(game, source, "B")

        self.upkeep(game, "B", source)
        life_before = game.state.players["B"].life
        self.cast_for(game, "B", draw, ["B"])
        self.resolve_top(game)
        self.cast_for(game, "B", destroy, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        count = len(game.state.players["B"].zones["hand"])
        self.assertGreater(count, 4)
        self.resolve_top(game)
        self.assertEqual(life_before - (count - 4), game.state.players["B"].life)
        self.assertEqual((40, 40, 40), tuple(game.state.players[seat].life for seat in "ACD"))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_rack_waits_for_the_selected_seat_and_uses_the_clamped_shortage(self):
        game = self.session(60720472)
        engine = game.engine
        source = self.add(engine, RACK, zone="hand")
        donate = self.add(engine, "Donate", zone="hand", ref="shortage-rack-control")
        player = game.state.players["C"]
        for object_id in tuple(player.zones["hand"])[1:]:
            engine.move_card(object_id, "library", log=False)
        if len(game.state.players["B"].zones["hand"]) > 6:
            engine.move_card(game.state.players["B"].zones["hand"][-1], "library", log=False)
        self.assert_closed(game, source)
        self.assert_closed(game, donate)
        self.prepare(game, mana={"C": 3, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 2, "U": 1})
        self.choose_opponent(game, source, "C")
        self.cast_for(game, "A", donate, ["C", source.ref])
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[source.object_id].controller)
        self.upkeep(game, "C", source)
        self.assertEqual("C", game.state.stack[-1].controller)
        self.assertEqual(1, len(player.zones["hand"]))
        self.resolve_top(game)
        self.assertEqual((40, 40, 38, 40), tuple(game.state.players[seat].life for seat in "ABCD"))
        self.replay(game, load=True)

    def test_actual_cursed_rack_keeps_chosen_cleanup_after_control_transfer_and_overrides_older_limit(self):
        game = self.session(60720473)
        engine = game.engine
        source = self.add(engine, CURSED, zone="hand")
        older = self.add(engine, "Null Profusion", seat="B")
        donate = self.add(engine, "Donate", zone="hand")
        for card in (source, older, donate):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "U": 1})
        self.checkpoint(game)
        self.assertEqual(2, effective_maximum_hand_size(engine, "B"))
        self.cast_for(game, "A", source, reserve={"C": 2, "U": 1})
        self.choose_opponent(game, source, "B")
        self.assertEqual((7, 4, 7, 7), tuple(effective_maximum_hand_size(engine, seat) for seat in "ABCD"),
                         "The chosen seat gets the maximum before a control change")
        self.cast_for(game, "A", donate, ["B", source.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[source.object_id].controller)
        self.assertEqual((7, 4, 7, 7), tuple(effective_maximum_hand_size(engine, seat) for seat in "ABCD"))
        self.until(game, lambda current: current.state.pending_decision.kind == "cleanup.discard")
        choice = game.packet("pilot:B", full=True)["decision"]
        size = len(game.state.players["B"].zones["hand"])
        self.assertEqual(size - 4, choice["ctx"]["count"])
        self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
        refs = [item["id"] for item in choice["ctx"]["hand"][:size - 4]]
        command = {"a": "discard", "cards": refs}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertTrue(game.act("pilot:B", command).ok)
        self.assertEqual(4, len(game.state.players["B"].zones["hand"]))
        self.replay(game, load=True)

    def test_actual_cleanup_witness_kills_using_source_controller_instead_of_linked_seat(self):
        with patch("quorune.maximum_hand_size.current_linked_entry_opponent",
                   side_effect=lambda host, card: card.controller):
            with self.assertRaisesRegex(AssertionError, "chosen seat gets the maximum"):
                self.test_actual_cursed_rack_keeps_chosen_cleanup_after_control_transfer_and_overrides_older_limit()

    def test_actual_flicker_requires_a_new_selection_for_the_new_linked_incarnation(self):
        game = self.session(60720474)
        engine = game.engine
        source = self.add(engine, CURSED, zone="hand")
        flicker = self.add(engine, "Flicker", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, flicker)
        self.prepare(game, mana={"C": 5, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 1, "W": 1})
        self.choose_opponent(game, source, "B")
        old_identity = game.state.cards[source.object_id].logical_object_id
        self.cast_for(game, "A", flicker, [source.ref])
        self.choose_opponent(game, source, "C")
        self.assertNotEqual(old_identity, game.state.cards[source.object_id].logical_object_id)
        self.assertEqual((7, 7, 4, 7), tuple(effective_maximum_hand_size(engine, seat) for seat in "ABCD"))
        self.replay(game, load=True)
