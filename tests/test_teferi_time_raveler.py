"""The printed Teferi timing restrictions and loyalty results use native actions."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from common import ROOT
import test_qualified_public_history as turn_helpers
import test_source_attack_alone as native_helpers
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.static_cast_rule_templates import static_cast_rule_handler
from quorune.continuous_effect_state import expire_next_turn_continuous_effects
from quorune.deck import DeckDefinition, DeckEntry
from quorune.declaration_rule_effects import continuous_journal_effect_from_dict
from quorune.engine import CommanderEngine
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_cast_timing import (
    NEXT_TURN_SORCERY_TIMING_CAPABILITY,
    TemporaryCastTimingScope,
    temporary_cast_timing_effect_scope,
    temporary_cast_timing_payload_dependencies,
    temporary_cast_timing_permissions,
)
from quorune.semantic_runtime import static_cast_rules
from quorune.temporary_cast_rule_model import ResolvedNextTurnSorceryTiming
from scripts.build_test_database import build_fixture_database


TEFERI = "Teferi, Time Raveler"
ORACLE_ID = "ae7604bb-4818-45a3-960c-cf3d83f15964"
RESTRICTION = "Each opponent can cast spells only any time they could cast a sorcery."


class TeferiTimingContractTests(unittest.TestCase):
    def test_teferi_opponent_timing_requires_priority_own_main_and_empty_stack(self):
        _, descriptor, _ = static_cast_rule_handler(RESTRICTION, source_name=TEFERI)
        self.assertEqual(
            static_cast_rules.OPPONENT_SORCERY_TIMING_DEPENDENCIES,
            static_cast_rules.opponent_sorcery_timing_descriptor_dependencies(descriptor),
        )
        for change in (
            {"player_scope": "all_players"},
            {"origin_zones": ["graveyard"]},
            {"spell_queries": [{**descriptor["spell_queries"][0], "types_all": ["instant"]}]},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                static_cast_rules.opponent_sorcery_timing_descriptor_dependencies({**descriptor, **change})
        rule = SimpleNamespace(condition=static_cast_rules.StaticCastCondition.OUTSIDE_SORCERY_TIMING)
        main = {"active_player": "B", "priority_player": "B", "phase": "precombat_main", "step": "main", "stack": []}
        host = SimpleNamespace()
        host._is_main_phase = lambda: CommanderEngine._is_main_phase(host)
        host._sorcery_timing = lambda seat: CommanderEngine._sorcery_timing(host, seat)
        host.state = SimpleNamespace(**main)
        self.assertFalse(static_cast_rules._condition_applies(host, rule, player="B"))

        def cannot_cast(**change):
            host.state = SimpleNamespace(**{**main, **change})
            self.assertTrue(static_cast_rules._condition_applies(host, rule, player="B"))

        for change in ({"priority_player": None}, {"priority_player": "A"}, {"active_player": "A"},
                       {"phase": "beginning", "step": "upkeep"}, {"stack": [object()]}):
            with self.subTest(change=change):
                cannot_cast(**change)
        with patch.object(static_cast_rules, "_has_sorcery_timing", return_value=True), self.assertRaises(AssertionError):
            cannot_cast(stack=[object()])

    def test_teferi_next_turn_timing_is_actor_bound_and_expires_only_on_that_seat(self):
        payload = {"op": "grant_cast_permission", "schema_version": 2,
                   "player": "$controller", "temporary_cast_timing": "sorcery_spells"}
        self.assertIs(TemporaryCastTimingScope.SORCERY_SPELLS, temporary_cast_timing_effect_scope(payload))
        self.assertIn(NEXT_TURN_SORCERY_TIMING_CAPABILITY, temporary_cast_timing_payload_dependencies(payload))
        for change in ({"schema_version": True}, {"player": "B"},
                       {"temporary_cast_timing": "next_sorcery"}, {"temporary_cast_timing": "spells"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                temporary_cast_timing_effect_scope({**payload, **change})
        timing = ResolvedNextTurnSorceryTiming("CT1", "departed-source", 3, "A")
        self.assertEqual(timing, continuous_journal_effect_from_dict(timing.to_dict()))
        state = SimpleNamespace(continuous_effects=[timing], turn_sequence=4,
                                players={seat: SimpleNamespace(stats={}) for seat in "AB"})
        host = SimpleNamespace(state=state)
        spell = SimpleNamespace(object_kind="card")
        sorcery = {"type_line": "Sorcery"}
        self.assertTrue(temporary_cast_timing_permissions(host, "A", spell, face=sorcery))
        self.assertFalse(temporary_cast_timing_permissions(host, "B", spell, face=sorcery))
        self.assertFalse(temporary_cast_timing_permissions(host, "A", spell, face={"type_line": "Artifact"}))
        def expires_on_its_controller():
            state.continuous_effects = [timing]
            expire_next_turn_continuous_effects(state, "B")
            self.assertTrue(temporary_cast_timing_permissions(host, "A", spell, face=sorcery))
            expire_next_turn_continuous_effects(state, "A")
            self.assertFalse(temporary_cast_timing_permissions(host, "A", spell, face=sorcery))

        expires_on_its_controller()
        with patch.object(ResolvedNextTurnSorceryTiming, "expires_on_next_turn_of", property(lambda _effect: "B")):
            with self.assertRaises(AssertionError):
                expires_on_its_controller()


class TeferiTimeRavelerNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native_helpers.SourceAttackAloneNativeTests.session
    add = native_helpers.SourceAttackAloneNativeTests.add
    prepare = native_helpers.SourceAttackAloneNativeTests.prepare
    cast_for = native_helpers.SourceAttackAloneNativeTests.cast_for
    action = native_helpers.SourceAttackAloneNativeTests.action
    priority = native_helpers.SourceAttackAloneNativeTests.priority
    checkpoint = native_helpers.SourceAttackAloneNativeTests.checkpoint
    resolve = native_helpers.SourceAttackAloneNativeTests.resolve
    resolve_top = native_helpers.SourceAttackAloneNativeTests.resolve_top
    replay = native_helpers.SourceAttackAloneNativeTests.replay
    assert_closed = native_helpers.SourceAttackAloneNativeTests.assert_closed
    until = turn_helpers.QualifiedPublicHistoryNativeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "teferi.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "event-spell-copy-cards.json", "bounded-hand-cast-cards.json",
            "ability-source-counter-cards.json", "teferi-time-raveler-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Teferi timing", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def planeswalker(self, session, seat="A"):
        source = self.add(session.engine, TEFERI, seat=seat, ref=f"teferi-{seat.lower()}")
        self.assertEqual(ORACLE_ID, source.oracle_id)
        source.counters["loyalty"] = int(self.db.lookup(TEFERI).loyalty)
        return source

    def rows(self, session, seat):
        self.priority(session, seat)
        return session.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]

    def cast_offer(self, session, seat, card):
        ref = session.state.cards[card.object_id].ref
        return next((row for row in self.rows(session, seat) if row["id"] == f"cast:{ref}"), None)

    def submit(self, session, seat, command):
        result = session.act(f"pilot:{seat}", command)
        self.assertTrue(result.ok, result.summary)

    def activate_loyalty(self, session, seat, source, delta, targets=()):
        current = session.state.cards[source.object_id]
        ability, = (entry for entry in session.engine._activated_abilities(current) if entry.loyalty_delta == delta)
        action_id = f"activate:{current.ref}:{ability.ability_id}"
        self.assertTrue(any(row["id"] == action_id for row in self.rows(session, seat)))
        before = current.counters["loyalty"]
        self.submit(session, seat, {"action_id": action_id, "targets": list(targets)})
        self.assertEqual(before + delta, session.state.cards[source.object_id].counters["loyalty"])
        return action_id

    def timing_effects(self, session):
        return tuple(effect for effect in session.state.continuous_effects or ()
                     if isinstance(effect, ResolvedNextTurnSorceryTiming))

    def test_teferi_timing_descriptors_require_independent_capabilities(self):
        session = self.session(8374100)
        source = self.planeswalker(session)
        self.assert_closed(session, source)
        programs = session.engine.semantics.programs_for_oracle(ORACLE_ID)
        for capability in (static_cast_rules.OPPONENT_SORCERY_TIMING_CAPABILITY, NEXT_TURN_SORCERY_TIMING_CAPABILITY):
            with self.subTest(capability=capability):
                program, = (row for row in programs if capability in row.capability_dependencies)
                dependencies = tuple(dep for dep in program.capability_dependencies if dep != capability)
                changed = replace(
                    program, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict(),
                )
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                       profile="commander_review")
                self.assertFalse(result["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{capability}", result["blockers"])

    def test_actual_teferi_opponent_timing_precedes_flash_and_resolving_free_cast(self):
        session = self.session(8374101)
        engine = session.engine
        restriction = self.planeswalker(session)
        opposing = self.planeswalker(session, "B")
        blocked = self.add(engine, "Sram's Expertise", seat="B", zone="hand", ref="blocked-expertise")
        control = self.add(engine, "Sram's Expertise", seat="B", zone="hand", ref="control-expertise")
        free = self.add(engine, "Ancestral Vision", seat="B", zone="hand")
        sorcery = self.add(engine, "Divination", seat="B", zone="hand")
        bounce = self.add(engine, "Boomerang", seat="B", zone="hand")
        self.assert_closed(session, restriction)
        self.assert_closed(session, blocked)
        self.prepare(session, actor="B", active="B", mana={"W": 4, "U": 3, "C": 8})
        self.checkpoint(session)

        self.assertIsNotNone(self.cast_offer(session, "B", bounce))
        self.activate_loyalty(session, "B", opposing, 1)
        self.assertIsNone(self.cast_offer(session, "B", bounce))
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:B", {"action_id": f"cast:{bounce.ref}", "targets": [restriction.ref],
                                           "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(["B"], [effect.controller for effect in self.timing_effects(session)])

        self.cast_for(session, "B", blocked, reserve={"W": 2, "U": 3, "C": 4})
        self.assertIsNone(self.cast_offer(session, "B", sorcery))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[blocked.object_id].zone)
        self.assertEqual("hand", session.state.cards[free.object_id].zone)
        tokens = [card for card in session.state.cards.values()
                  if card.zone == "battlefield" and card.controller == "B" and card.object_kind == "token"]
        self.assertEqual(3, len(tokens))

        self.cast_for(session, "B", bounce, [restriction.ref], reserve={"W": 2, "C": 2})
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[restriction.object_id].zone)
        self.cast_for(session, "B", control)
        self.assertIsNotNone(self.cast_offer(session, "B", sorcery))
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        options = session.packet("pilot:B", full=True)["decision"]["ctx"]["cast_options"]
        option, = (price for price in options if price["card"] == free.ref)
        for other in "ACD":
            self.assertNotIn(free.ref, json.dumps(session.packet(f"pilot:{other}", full=True)))
        session = self.replay(session, load=True)
        self.submit(session, "B", {"action_id": "choose", "choice": "cast",
                                   "cast": {"card": free.ref, "cost_option": option["id"], "targets": ["B"]}})
        self.assertEqual("stack", session.state.cards[free.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[free.object_id].zone)
        self.replay(session)

    def test_actual_teferi_plus_one_survives_departure_and_expires_on_its_controllers_turn(self):
        session = self.session(8374102)
        engine = session.engine
        source = self.planeswalker(session, "B")
        engine.change_control(source.object_id, "A", reason="Borrowed Teferi fixture")
        bounce = self.add(engine, "Boomerang", zone="hand")
        sorceries = [self.add(engine, "Divination", zone="hand", ref=f"timed-divination-{n}") for n in range(3)]
        artifact = self.add(engine, "Sol Ring", zone="hand")
        for n in range(6):
            self.add(engine, "Island", ref=f"teferi-island-{n}")
        self.assert_closed(session, source)
        self.prepare(session, mana={"U": 2})
        self.checkpoint(session)

        action_id = self.activate_loyalty(session, "A", source, 1)
        self.cast_for(session, "A", bounce, [source.ref])
        self.resolve_top(session)
        self.assertTrue(session.state.stack)
        self.assertIn(source.object_id, session.state.players["B"].zones["hand"])
        self.assertFalse(self.timing_effects(session))
        self.assertIsNone(self.resolve(session))
        effect, = self.timing_effects(session)
        self.assertEqual("A", effect.controller)
        self.assertFalse(any(row["id"] == action_id for row in self.rows(session, "A")))
        session = self.replay(session, load=True)

        for active, spell in zip("BC", sorceries):
            self.until(session, lambda current: current.state.active_player == active and current.state.step == "main")
            self.assertEqual((effect,), self.timing_effects(session))
            self.assertIsNone(self.cast_offer(session, "A", artifact))
            for other in "BCD":
                self.assertNotIn(spell.ref, json.dumps(session.packet(f"pilot:{other}", full=True)))
            self.cast_for(session, "A", spell)
            self.assertEqual("stack", session.state.cards[spell.object_id].zone)
            self.assertIsNone(self.resolve(session))
            self.assertEqual("graveyard", session.state.cards[spell.object_id].zone)

        self.until(session, lambda current: current.state.active_player == "A" and current.state.step == "upkeep")
        self.assertFalse(self.timing_effects(session))
        self.assertIsNone(self.cast_offer(session, "A", sorceries[-1]))
        self.until(session, lambda current: current.state.active_player == "A" and current.state.step == "main")
        self.assertIsNotNone(self.cast_offer(session, "A", sorceries[-1]))
        self.replay(session, load=True)

    def test_actual_teferi_minus_three_draws_without_targets_and_returns_to_owner(self):
        for selection in ("zero", "borrowed", "invalidated"):
            with self.subTest(selection=selection):
                session = self.session(8374103 + ("zero", "borrowed", "invalidated").index(selection))
                engine = session.engine
                source = (self.add(engine, TEFERI, zone="hand", ref="teferi-a") if selection == "zero"
                          else self.planeswalker(session))
                bounce = self.add(engine, "Boomerang", zone="hand")
                target = self.add(engine, "Sol Ring", seat="B")
                engine.change_control(target.object_id, "A", reason="Borrowed artifact fixture")
                self.assert_closed(session, source)
                self.prepare(session, mana={"U": 3, "W": 1, "C": 1})
                self.checkpoint(session)
                if selection == "zero":
                    self.cast_for(session, "A", source)
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual(4, session.state.cards[source.object_id].counters["loyalty"])
                hand_draws = len(session.state.players["A"].draw_history)
                self.activate_loyalty(session, "A", source, -3, [] if selection == "zero" else [target.ref])
                if selection == "invalidated":
                    self.cast_for(session, "A", bounce, [target.ref])
                    self.resolve_top(session)
                    self.assertTrue(session.state.stack)
                    session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(hand_draws + (selection != "invalidated"), len(session.state.players["A"].draw_history))
                self.assertEqual(selection != "zero", target.object_id in session.state.players["B"].zones["hand"])
                self.assertEqual("battlefield" if selection == "zero" else "hand",
                                 session.state.cards[target.object_id].zone)
                prefix = f"activate:{session.state.cards[source.object_id].ref}:"
                self.assertFalse(any(row["id"].startswith(prefix) for row in self.rows(session, "A")))
                self.replay(session, load=True)
