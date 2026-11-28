"""Unviewed own-library exile and one incarnation-bound next-end-step trigger."""

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
from quorune.linked_exile_return_model import CONTROLLER_LIBRARY_TOP_HAND_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import authoritative_state_hash
from quorune.replacement_effects import ReplacementClass, ReplacementEffect
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import zone_replacements
from quorune.zone_object_state import reveal_exiled_card
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


_SOURCE = "Generic Sealed Library Source"
_PROFILE = "commander_review"
_LABEL = "Return linked exiled objects"


class ControllerLibraryTopDelayedHandTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-library-top-hand.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database(
            [fixtures / "linked-library-imprint-cards.json",
             fixtures / "controller-library-top-delayed-hand-cards.json",
             fixtures / "life-total-prohibition-cards.json"], path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Sealed library", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander],
        )

    def activate(self, game, source, *, seat="A"):
        self.priority(game, seat)
        legal = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = (
            row for row in legal if row.get("source") == source.ref and row["id"].startswith("activate:")
        )
        before = game.state.players[seat].life
        result = game.act("pilot:" + seat, {"action_id": offered["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(before - 1, game.state.players[seat].life)
        return offered

    def active_delays(self, game):
        return [entry for entry in game.state.delayed_triggers if entry.label == _LABEL and entry.active]

    def ready(self, game, *, reserve_b=None):
        self.prepare(game, mana={})
        game.state.config.semantic_policy = "trusted_only"
        if reserve_b:
            game.state.players["B"].mana_pool.update(reserve_b)
        self.checkpoint(game)

    def assert_hidden(self, game, card, *, in_exile):
        current = game.state.cards[card.object_id]
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = game.packet(principal, full=True)
            if in_exile:
                exile = packet["state"]["players"]["A"]["ex"]
                masked, = (row for row in exile if row["id"] == current.ref)
                self.assertEqual({"id": current.ref, "n": "?", "fd": 1}, masked)
            rendered = json.dumps(packet)
            if in_exile or principal != "pilot:A":
                self.assertNotIn(current.printed_name, rendered, principal)
                self.assertNotIn(current.oracle_id, rendered, principal)
                self.assertNotIn(current.object_id, rendered, principal)
            else:
                self.assertIn(current.printed_name, rendered)

    def test_compiler_binds_the_complete_life_price_and_paired_result(self):
        record = self.db.lookup(_SOURCE, fuzzy=False)
        admitted, semantics = register_card(self.db, self.registry, _SOURCE)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual(1, program.cost_schema["life_payment"])
        self.assertIn(CONTROLLER_LIBRARY_TOP_HAND_CAPABILITY, program.capability_dependencies)
        self.assertIsNone(program.target_schema)
        start, finish = program.effects
        self.assertEqual(("$controller", "exile", "return"), (start["cards"], start["phase"], finish["phase"]))
        self.assertEqual(start["binding_id"], finish["binding_id"])
        self.assertEqual("controller_next_end_step", start["spec"]["timing"])
        reduced = tuple(cap for cap in program.capability_dependencies if cap != CONTROLLER_LIBRARY_TOP_HAND_CAPABILITY)
        for altered in (
            replace(program, effects=({**start, "cards": "$active"}, finish)),
            replace(program, effects=(start,)),
            replace(program, cost_schema={**program.cost_schema, "life_payment": 0}),
            replace(program, capability_dependencies=reduced,
                    capability_closure=self.registry.closure(reduced, profile=_PROFILE).to_dict()),
        ):
            with self.subTest(altered=altered):
                self.assertFalse(bind_semantic_program_runtime(
                    altered, capability_registry=self.registry, profile=_PROFILE,
                )["strict"])
        for needle, substitute in (
            ("your next end step", "the next end step"),
            ("face down", "face up"),
            ("Pay 1 life", "{1}"),
        ):
            changed = replace(record, oracle_text=record.oracle_text.replace(needle, substitute))
            self.assertNotEqual("exact", compile_oracle_card(
                changed, capability_registry=self.registry, capability_profile=_PROFILE,
            ).status)

    def test_repeated_activation_hides_both_objects_and_survives_source_departure(self):
        game = self.session(40660371)
        source = self.add(game.engine, _SOURCE)
        second = self.add(game.engine, "Grizzly Bears", zone="library")
        first = self.add(game.engine, "Sol Ring", zone="library")
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        original = source.logical_object_id
        self.assert_closed(game, source)
        self.ready(game, reserve_b={"C": 1, "B": 2})

        self.activate(game, source)
        self.activate(game, source)
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[first.object_id].zone)
        self.assertEqual("library", game.state.cards[second.object_id].zone)
        self.cast_for(game, "B", murder, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.resolve_top(game)
        self.assertEqual(
            [[first.object_id], [second.object_id]],
            [entry.referred_object_ids for entry in self.active_delays(game)],
        )
        self.assertTrue(all(entry.source_logical_object_id == original for entry in self.active_delays(game)))
        for card in (first, second):
            current = game.state.cards[card.object_id]
            self.assertEqual(("exile", True, [], []), (current.zone, current.face_down, current.known_to, current.revealed_to))
            self.assert_hidden(game, card, in_exile=True)

        game = self.replay(game, load=True)
        self.until(game, lambda state: all(state.state.cards[c.object_id].zone == "hand" for c in (first, second)))
        self.assertEqual([], self.active_delays(game))
        for card in (first, second):
            current = game.state.cards[card.object_id]
            self.assertEqual(("hand", False, ["A"]), (current.zone, current.face_down, current.known_to))
            self.assert_hidden(game, card, in_exile=False)
        self.replay(game, load=True)

    def test_owner_visibility_mutation_exposes_the_unviewed_exile(self):
        game = self.session(40660376)
        source = self.add(game.engine, _SOURCE)
        card = self.add(game.engine, "Sol Ring", zone="library")
        self.ready(game)
        self.activate(game, source)
        self.resolve_top(game)
        self.assert_hidden(game, card, in_exile=True)

        visible = StateProjector._card_visible

        def formerly_visible(projector, candidate, principal):
            return (
                candidate.zone == "exile" and candidate.face_down
                and principal == "pilot:" + candidate.controller
            ) or visible(projector, candidate, principal)

        with patch.object(StateProjector, "_card_visible", formerly_visible):
            with self.assertRaises(AssertionError):
                self.assert_hidden(game, card, in_exile=True)

    def test_late_activation_waits_for_the_next_own_end_step(self):
        game = self.session(40660372)
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"][3:]):
                game.engine.move_card(object_id, "library", log=False)
        source = self.add(game.engine, _SOURCE)
        card = self.add(game.engine, "Sol Ring", zone="library")
        self.ready(game)
        self.until(game, lambda state: state.state.active_player == "A" and state.state.step == "end_step")
        self.activate(game, source)
        self.resolve_top(game)
        delayed, = self.active_delays(game)
        self.assertEqual({"step": "end_step", "player": "A"}, delayed.condition)
        self.assertEqual("exile", game.state.cards[card.object_id].zone)
        game = self.replay(game, load=True)

        for seat in ("B", "C", "D"):
            self.until(game, lambda state: state.state.active_player == seat and state.state.step == "end_step")
            self.assertEqual("exile", game.state.cards[card.object_id].zone)
            self.assertEqual([delayed.ref], [entry.ref for entry in self.active_delays(game)])
        self.until(game, lambda state: state.state.active_player == "A" and state.state.step == "end_step")
        trigger, = (item for item in game.state.stack if item.label == _LABEL)
        self.assertEqual(("A", [card.object_id]), (trigger.controller, trigger.referred_object_ids))
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def test_delay_never_substitutes_a_new_exile_incarnation(self):
        game = self.session(40660373)
        source = self.add(game.engine, _SOURCE)
        missing = self.add(game.engine, "Grizzly Bears", zone="library")
        face_up = self.add(game.engine, "Sol Ring", zone="library")
        self.ready(game)
        for _ in range(2):
            self.activate(game, source)
            self.resolve_top(game)
        detached = game.state.cards[missing.object_id]
        original = detached.logical_object_id
        game.engine.move_card(detached.object_id, "graveyard")
        game.engine.move_card(detached.object_id, "exile")
        self.assertNotEqual(original, detached.logical_object_id)
        reveal_exiled_card(game.state.cards[face_up.object_id], viewers=tuple("ABCD"))
        self.checkpoint(game)
        self.until(game, lambda state: state.state.cards[face_up.object_id].zone == "hand")
        self.settle(game)
        self.assertEqual("exile", game.state.cards[missing.object_id].zone)
        self.replay(game, load=True)

    def test_empty_library_creates_one_inert_delay(self):
        game = self.session(40660374)
        source = self.add(game.engine, _SOURCE)
        for object_id in tuple(game.state.players["A"].zones["library"]):
            game.engine.move_card(object_id, "graveyard", log=False)
        self.ready(game)
        self.activate(game, source)
        self.resolve_top(game)
        delayed, = self.active_delays(game)
        self.assertEqual([], delayed.referred_object_ids)
        self.until(game, lambda state: any(item.label == _LABEL for item in state.state.stack))
        self.resolve_top(game)
        self.assertEqual([], self.active_delays(game))
        self.assertEqual([], game.state.players["A"].zones["exile"])
        self.replay(game, load=True)

    def test_redirected_library_exile_creates_an_inert_delay(self):
        game = self.session(40660377)
        source = self.add(game.engine, _SOURCE)
        top = self.add(game.engine, "Sol Ring", zone="library")
        self.ready(game)
        collect = zone_replacements.collect_zone_change_replacement_effects
        redirect = ReplacementEffect(
            "synthetic:redirect-library-exile", "synthetic:test", "zone.change", ReplacementClass.OTHER,
            conditions={"origin": {"eq": "library"}, "destination": {"eq": "exile"}},
            operations=({"op": "set", "field": "destination", "value": "graveyard"},),
        )

        def collect_with_redirect(host, **kwargs):
            return (*collect(host, **kwargs), redirect)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", collect_with_redirect):
            self.activate(game, source)
            self.resolve_top(game)
        current = game.state.cards[top.object_id]
        self.assertEqual(("graveyard", False), (current.zone, current.face_down))
        redirected_incarnation = current.logical_object_id
        delay, = self.active_delays(game)
        self.assertEqual([], delay.referred_object_ids)
        self.until(game, lambda state: any(item.label == _LABEL for item in state.state.stack))
        self.resolve_top(game)
        self.assertEqual(("graveyard", redirected_incarnation), (current.zone, current.logical_object_id))
        self.assertEqual([], self.active_delays(game))

    def test_life_prohibition_removes_the_offer_and_failed_payment_rolls_back(self):
        game = self.session(40660378)
        source = self.add(game.engine, _SOURCE)
        self.add(game.engine, "Sol Ring", zone="library")
        self.ready(game)
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = (row for row in legal if row.get("source") == source.ref and row["id"].startswith("activate:"))

        prohibition = self.add(game.engine, "Platinum Emperion")
        self.assert_closed(game, prohibition)
        self.ready(game)
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn(offered["id"], [row["id"] for row in legal])
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id": offered["id"], "pay": "auto"})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual([], self.active_delays(game))

    def test_wrong_principal_and_invalid_choices_roll_back(self):
        game = self.session(40660375)
        source = self.add(game.engine, _SOURCE)
        self.add(game.engine, "Sol Ring", zone="library")
        self.ready(game)
        self.priority(game, "A")
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = (row for row in legal if row.get("source") == source.ref and row["id"].startswith("activate:"))
        before = authoritative_state_hash(game.state)
        command = {"action_id": offered["id"], "pay": "auto"}
        for actor, response in (("B", command), ("A", {**command, "targets": ["forged-target"]})):
            with self.subTest(actor=actor):
                self.assertFalse(game.act("pilot:" + actor, response).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
        self.activate(game, source)
        self.resolve_top(game)
        self.replay(game, load=True)
