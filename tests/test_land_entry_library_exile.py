"""Actual commands and exact replay for a land entry's independent exile and draw."""

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
from quorune.land_entry_library_exile_model import LAND_ENTRY_TOP_EXILE_CAPABILITY, is_land_entry_top_exile
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.impulse_access_handlers import FixedImpulseAccessHandler
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
SIRE = "Sire of Stagnation"


class LandEntryLibraryExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "land-entry-library-exile.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/land-entry-library-exile-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Opposing land entry", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def prepared_game(self, seed, *, count=None, theft=False, counter=False, second_observer=False, active="A"):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, SIRE, seat="B", ref="observer-B")
        self.assert_closed(game, source)
        if second_observer:
            other = self.add(game.engine, SIRE, seat="C", ref="observer-C")
            self.assert_closed(game, other)
        if theft:
            permission = self.add(game.engine, "Vedalken Orrery", ref="instant-aura-permission")
            self.assert_closed(game, permission)
            response = self.add(game.engine, "Control Magic", zone="hand", ref="control-response")
            self.assert_closed(game, response)
        elif counter:
            response = self.add(game.engine, "Stifle", seat="C", zone="hand", ref="ability-counter")
            self.assert_closed(game, response)
            self.add(game.engine, "Island", seat="C", ref="counter-mana")
        else:
            response = None
        land = self.add(game.engine, "Plains", seat=active, zone="hand", ref="land-play")
        if count is not None:
            library = tuple(game.state.players[active].zones["library"])
            for object_id in library[:len(library) - count]:
                game.engine.move_card(object_id, "graveyard", reason="Prepare the short library")
        self.prepare(game, actor=active, active=active, mana={"C": 2, "U": 2} if theft else {})
        self.checkpoint(game)
        return game, source, land, response

    def play_land(self, game, seat, land):
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offered = next(row for row in actions if row["id"].startswith("play-land:") and row["card"] == land.ref)
        command = {"action_id": offered["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        self.assertEqual("priority", game.state.pending_decision.kind)

    def libraries(self, game):
        return {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ABCD"}

    def assert_result(self, game, before, from_event, *, supplier, recipient, count):
        expected = dict(before)
        top = tuple(reversed(before[supplier][-count:])) if count else ()
        drawn = tuple(reversed(before[recipient][-2:]))
        if count:
            expected[supplier] = before[supplier][:-count]
        expected[recipient] = before[recipient][:-2]
        self.assertEqual(expected, self.libraries(game))
        self.assertTrue(game.state.players[supplier].in_game)
        for object_id in top:
            card = game.state.cards[object_id]
            self.assertEqual((supplier, "exile", False), (card.owner, card.zone, card.face_down))
            self.assertNotIn("temporary_play_permission", card.annotations)
        for object_id in drawn:
            card = game.state.cards[object_id]
            self.assertEqual((recipient, "hand"), (card.owner, card.zone))
        events = [event for event in game.state.events[from_event:]
                  if event.code in {"library.impulse_access", "card.draw", "card.draw.empty"}]
        self.assertEqual(["library.impulse_access", "card.draw", "card.draw"], [event.code for event in events])
        self.assertEqual([recipient] * 3, [event.actor for event in events])
        self.assertEqual((supplier, count, [game.state.cards[oid].ref for oid in top]),
                         tuple(events[0].details[key] for key in ("player", "count", "objects")))
        self.assertEqual([1, 1], [event.details["count"] for event in events[1:]])
        for seat in "ABCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for object_id in top:
                self.assertIn(game.state.cards[object_id].ref, packet)
            if seat != recipient:
                for object_id in drawn:
                    self.assertNotIn(game.state.cards[object_id].ref, packet)

    def test_actual_opposing_land_exiles_current_top_then_draws_twice_even_when_short(self):
        for available in (3, 1, 0):
            with self.subTest(available=available):
                game, source, land, _ = self.prepared_game(78143100 + available, count=available)
                before = self.libraries(game)
                for seat in "ABCD":
                    packet = json.dumps(game.packet("pilot:" + seat, full=True))
                    for object_id in before["A"][-2:]:
                        self.assertNotIn(game.state.cards[object_id].ref, packet)
                self.play_land(game, "A", land)
                trigger, = game.state.stack
                self.assertEqual((source.object_id, "B", "A", []),
                                 (trigger.source_object_id, trigger.controller, trigger.context["controller"], trigger.targets))
                self.assertEqual(before, self.libraries(game))
                game = self.replay(game, load=True)
                mark = len(game.state.events)
                self.resolve_top(game)
                self.assert_result(game, before, mark, supplier="A", recipient="B", count=min(2, available))
                self.replay(game, load=True)

    def test_actual_own_land_does_not_trigger_and_stifle_counters_both_instructions(self):
        game, source, land, _ = self.prepared_game(78143120, active="B")
        before = self.libraries(game)
        self.play_land(game, "B", land)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertEqual(before, self.libraries(game))
        self.replay(game, load=True)
        game, _, land, stifle = self.prepared_game(78143121, counter=True)
        before = self.libraries(game)
        self.play_land(game, "A", land)
        trigger, = game.state.stack
        self.cast(game, "C", stifle, [trigger.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, self.libraries(game))
        self.assertEqual("graveyard", game.state.cards[stifle.object_id].zone)
        self.replay(game, load=True)

    def test_actual_source_control_change_retains_trigger_and_apnap_orders_two_observers(self):
        game, source, land, control = self.prepared_game(78143130, theft=True, second_observer=True)
        self.play_land(game, "A", land)
        self.assertEqual([("B", "A", []), ("C", "A", [])],
                         [(item.controller, item.context["controller"], item.targets) for item in game.state.stack])
        self.cast(game, "A", control, [source.ref])
        self.resolve_top(game)
        self.assertEqual("A", game.state.cards[source.object_id].controller)
        game = self.replay(game, load=True)
        for recipient in ("C", "B"):
            before = self.libraries(game)
            mark = len(game.state.events)
            self.resolve_top(game)
            self.assert_result(game, before, mark, supplier="A", recipient=recipient, count=2)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_actual_foreign_owned_land_uses_its_committed_controller_as_supplier(self):
        game = self.session(78143133)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, SIRE, seat="B", ref="observer-B")
        land = self.add(game.engine, "Dryad Arbor", seat="D", zone="graveyard")
        spell = self.add(game.engine, "Reanimate", zone="hand")
        for card in (source, land, spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={"B": 1})
        self.checkpoint(game)
        before = self.libraries(game)
        self.cast(game, "A", spell, [land.ref])
        self.resolve_top(game)
        current = game.state.cards[land.object_id]
        self.assertEqual(("D", "A", "battlefield"), (current.owner, current.controller, current.zone))
        trigger, = game.state.stack
        self.assertEqual(("B", "A", []), (trigger.controller, trigger.context["controller"], trigger.targets))
        self.assertEqual(before, self.libraries(game))
        game = self.replay(game, load=True)
        mark = len(game.state.events)
        self.resolve_top(game)
        self.assert_result(game, before, mark, supplier="A", recipient="B", count=2)
        self.replay(game, load=True)

    def test_actual_fault_after_exile_and_private_draw_restores_pending_trigger(self):
        from quorune.drawing import transaction as draw_owner
        from quorune.errors import GameRuleError

        game, _, land, _ = self.prepared_game(78143136)
        self.play_land(game, "A", land)
        self.priority(game, "D")
        before = authoritative_state_hash(game.state)
        libraries = self.libraries(game)
        expected_exile = libraries["A"][-2:]
        expected_draw = libraries["B"][-1]
        original = draw_owner._commit_ordinary_draw
        seen = []

        def fault(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertEqual("draw", result.kind)
            self.assertTrue(all(game.state.cards[oid].zone == "exile" for oid in expected_exile))
            self.assertEqual("hand", game.state.cards[expected_draw].zone)
            seen.append(result.player)
            raise GameRuleError("Injected fault after the first private draw")

        with patch.object(draw_owner, "_commit_ordinary_draw", side_effect=fault):
            rejected = game.act("pilot:D", {"action_id": "pass"})
        self.assertFalse(rejected.ok)
        self.assertIn("Injected fault", rejected.summary)
        self.assertEqual(["B"], seen)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(libraries, self.libraries(game))
        self.assertEqual(1, len(game.state.stack))
        game = self.replay(game, load=True)
        mark = len(game.state.events)
        self.resolve_top(game)
        self.assert_result(game, libraries, mark, supplier="A", recipient="B", count=2)
        self.replay(game, load=True)

    def test_original_requires_ordered_untargeted_instructions_and_independent_owners(self):
        card = self.db.lookup(SIRE, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (value for value in semantics.programs_for_oracle(card.oracle_id) if value.event == "land.enter")
        self.assertIsNone(program.target_schema)
        for capability in (LAND_ENTRY_TOP_EXILE_CAPABILITY, "zone.draw.library_to_hand",
                           "trigger.event.normalized_zone_change", "zone.change.destination_replacement"):
            with self.subTest(capability=capability):
                self.assertIn(capability, program.capability_dependencies)
                deps = [key for key in program.capability_dependencies if key != capability]
                changed = replace(program, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                blocked = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(blocked["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{capability}", blocked["blockers"])
        for changed in (replace(program, effects=list(reversed(program.effects))),
                        replace(program, event="creature.enter"), replace(program, event_condition=None)):
            scope = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(scope["strict"])
            self.assertTrue(any("runtime_land_entry_top_exile" in value for value in scope["blockers"]), scope)
        for old, new in (("an opponent controls", "you control"), ("top two", "top three"),
                         ("draw two", "draw three"), ("exiles", "may exile"), ("that player", "target player"),
                         ("draw two cards.", "draw two cards. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            compiled = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", compiled.status, changed.oracle_text)

    def test_actual_exile_supplier_substitution_mutant_is_killed(self):
        def run(seed):
            game, _, land, _ = self.prepared_game(seed)
            self.play_land(game, "A", land)
            before = self.libraries(game)
            mark = len(game.state.events)
            self.resolve_top(game)
            self.assert_result(game, before, mark, supplier="A", recipient="B", count=2)

        run(78143140)
        original = FixedImpulseAccessHandler.lower

        def substitute(handler, effect, context):
            if is_land_entry_top_exile(effect):
                effect = {**effect, "player": "C"}
            return original(handler, effect, context)

        with patch.object(FixedImpulseAccessHandler, "lower", substitute):
            with self.assertRaises(AssertionError):
                run(78143141)
