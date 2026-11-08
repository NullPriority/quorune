"""Resolution counts use only committed, current-turn departure facts."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_public_history_queries as history_fixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import TurnHistory, TurnHistoryEvent
from quorune.public_history_query_model import (
    PERMANENT_DEATH_HISTORY_CAPABILITY, PublicHistoryQuerySpec, public_history_query_value,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec
from scripts.build_test_database import build_fixture_database


class PermanentDeathFactTests(unittest.TestCase):
    def test_qualified_facts_require_exact_mode_shape_and_current_turn(self):
        fact = TurnHistoryEvent(
            "permanent_died", actor="B", object_incarnation="kindred@1",
            types=("enchantment", "kindred"), creature_subtypes=("zubera",), token=False,
        )
        query = PublicHistoryQuerySpec(
            "permanent_died", relation="any_player", creature_subtype="zubera", schema_version=3,
        )
        amount = ScalarEffectAmountSpec(ScalarAmountOrigin.HISTORY_COUNT, schema_version=10, history_count=query)
        history = TurnHistory(turn_sequence=5, events=[fact], permanent_death_history_version=1)
        self.assertEqual(history, TurnHistory.from_dict(history.to_dict()))
        self.assertEqual(amount, ScalarEffectAmountSpec.from_dict(amount.to_dict()))
        for change in ({"token": 0}, {"creature_subtypes": ["Zubera"]}, {"origin": "battlefield"},
                       {"types": [False]}, {"types": None}):
            with self.assertRaises(ValueError):
                TurnHistoryEvent.from_dict({**fact.to_dict(), **change})
        for change in ({"schema_version": 2}, {"token": 0}, {"creature_subtype": "Zubera"}):
            with self.assertRaises(ValueError):
                PublicHistoryQuerySpec.from_dict({**query.to_dict(), **change})
        with self.assertRaises(ValueError):
            TurnHistory.from_dict({**history.to_dict(), "permanent_death_history_version": None})
        state = SimpleNamespace(
            turn_sequence=5, turn_history=history,
            players={"A": SimpleNamespace(in_game=True), "B": SimpleNamespace(in_game=True)},
        )
        self.assertEqual(1, public_history_query_value(state, "A", query))
        self.assertEqual(0, public_history_query_value(state, "A", replace(query, relation="controller")))
        self.assertEqual(0, public_history_query_value(state, "A", replace(query, types_all=("creature",))))
        state.turn_sequence += 1
        self.assertIsNone(public_history_query_value(state, "A", query))


class PermanentDeathAmountNativeTests(InheritedNativeFixture, unittest.TestCase):
    end_step = history_fixture.PublicHistoryQueryNativeTests.end_step

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "permanent-death-amounts.sqlite3"
        fixtures = ("bound-effect-program-cards", "activated-self-transform-cards", "permanent-death-amount-cards")
        build_fixture_database([ROOT / "tests/fixtures" / f"{name}.json" for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Permanent death amounts",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"],
        )

    def sacrifice(self, session, seat, altar, creature):
        self.priority(session, seat)
        action, = [row for row in session.packet("pilot:" + seat, full=True)["decision"]["legal_actions"]
                   if row["id"].startswith(f"activate:{altar.ref}:")]
        command = {"action_id": action["id"], "cost_cards": [creature.ref]}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_zuberas_count_later_foreign_token_death_and_exclude_exile(self):
        session = self.session(70046081)
        engine = session.engine
        dream = self.add(engine, "Floating-Dream Zubera")
        chant = self.add(engine, "Silent-Chant Zubera", seat="B")
        ordinary = self.add(engine, "Generic Bound Body")
        altar_a = self.add(engine, "Ashnod's Altar")
        altar_b = self.add(engine, "Ashnod's Altar", seat="B", ref="foreign-altar")
        exile = self.add(engine, "Final Reward", seat="B", zone="hand")
        for card in (dream, chant, exile):
            self.assert_closed(session, card)
        token_ref, = engine.create_token("B", name="", copy_of=chant.ref, reason="Prepare the copied-Zubera witness")
        token = engine._resolve_object("B", token_ref, zones={"battlefield"})
        self.prepare(session)
        session.state.players["B"].mana_pool.update({"C": 4, "B": 1})
        hand = len(session.state.players["A"].zones["hand"])
        life = session.state.players["B"].life
        hidden = session.state.cards[session.state.players["D"].zones["hand"][0]].ref
        self.checkpoint(session)
        self.sacrifice(session, "A", altar_a, dream)
        self.sacrifice(session, "A", altar_a, ordinary)
        self.sacrifice(session, "B", altar_b, token)
        self.cast(session, "B", exile, [chant.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand + 2, len(session.state.players["A"].zones["hand"]))
        self.assertEqual(life + 4, session.state.players["B"].life)
        self.assertEqual("exile", session.state.cards[chant.object_id].zone)
        for seat in "ABC":
            self.assertNotIn(hidden, json.dumps(session.packet("pilot:" + seat, full=True)))
        self.replay(session, load=True)

    def test_actual_gadrak_counts_deaths_before_entry_and_excludes_spirit_token(self):
        session = self.session(70046082)
        engine = session.engine
        gadrak = self.add(engine, "Gadrak, the Crown-Scourge", zone="hand")
        zubera = self.add(engine, "Dripping-Tongue Zubera")
        altar = self.add(engine, "Ashnod's Altar")
        for card in (gadrak, zubera):
            self.assert_closed(session, card)
        self.prepare(session, mana={"R": 1})
        self.checkpoint(session)
        self.sacrifice(session, "A", altar, zubera)
        self.assertIsNone(self.resolve(session))
        spirit, = [card for card in session.state.cards.values() if card.zone == "battlefield" and card.is_token]
        self.sacrifice(session, "A", altar, spirit)
        self.cast(session, "A", gadrak)
        self.assertIsNone(self.resolve(session))
        self.end_step(session)
        self.assertTrue(any(item.source_object_id == gadrak.object_id for item in session.state.stack))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        treasures = [card for card in session.state.cards.values() if card.zone == "battlefield" and card.is_token]
        self.assertEqual(1, len(treasures))
        self.assertIn("treasure", session.engine._type_parts(session.engine._effective_card_data(treasures[0])["type_line"])[1])
        self.replay(session, load=True)

    def test_actual_ember_fist_chooses_a_target_before_the_count_is_known(self):
        session = self.session(70046086)
        engine = session.engine
        ember = self.add(engine, "Ember-Fist Zubera")
        dream = self.add(engine, "Floating-Dream Zubera")
        altar = self.add(engine, "Ashnod's Altar")
        self.assert_closed(session, ember)
        self.prepare(session)
        self.checkpoint(session)
        life = session.state.players["B"].life
        self.sacrifice(session, "A", altar, ember)
        self.target(session, "A", ["B"])
        self.sacrifice(session, "A", altar, dream)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(life - 2, session.state.players["B"].life)
        self.replay(session, load=True)

    def test_actual_standard_bearer_uses_departure_controller_and_its_own_later_death(self):
        session = self.session(70046083)
        engine = session.engine
        bearer = self.add(engine, "Liliana's Standard Bearer", zone="hand")
        theft = self.add(engine, "Act of Treason", zone="hand")
        stolen = self.add(engine, "Generic Bound Body", seat="B", ref="borrowed-body")
        foreign = self.add(engine, "Generic Bound Body", seat="B", ref="foreign-body")
        altar_a = self.add(engine, "Ashnod's Altar")
        altar_b = self.add(engine, "Ashnod's Altar", seat="B", ref="foreign-altar")
        for card in (bearer, theft):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 2, "R": 1, "B": 1})
        self.checkpoint(session)
        self.cast(session, "A", theft, [stolen.ref])
        self.assertIsNone(self.resolve(session))
        self.sacrifice(session, "A", altar_a, stolen)
        self.sacrifice(session, "B", altar_b, foreign)
        self.cast(session, "A", bearer)
        self.resolve_top(session)
        self.assertTrue(any(item.source_object_id == bearer.object_id for item in session.state.stack))
        self.sacrifice(session, "A", altar_a, bearer)
        hand = len(session.state.players["A"].zones["hand"])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand + 2, len(session.state.players["A"].zones["hand"]))
        self.replay(session, load=True)

    def test_permanent_death_dependency_is_inferred_and_subtype_mutation_is_killed(self):
        session = self.session(70046084)
        source = self.add(session.engine, "Gadrak, the Crown-Scourge")
        self.assert_closed(session, source)
        program, = [row for row in session.engine.semantics.programs_for_oracle(source.oracle_id)
                    if PERMANENT_DEATH_HISTORY_CAPABILITY in row.capability_dependencies]
        deps = tuple(dep for dep in program.capability_dependencies if dep != PERMANENT_DEATH_HISTORY_CAPABILITY)
        incomplete = replace(program, capability_dependencies=deps,
                             capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(incomplete, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + PERMANENT_DEATH_HISTORY_CAPABILITY, binding["blockers"])
        with patch("quorune.zone_trigger_processing.canonical_creature_types", return_value=()):
            with self.assertRaises(AssertionError):
                self.test_actual_zuberas_count_later_foreign_token_death_and_exclude_exile()

    def test_legacy_missing_permanent_death_mode_replays_without_new_facts(self):
        session = self.session(70046085)
        engine = session.engine
        body = self.add(engine, "Generic Bound Body")
        altar = self.add(engine, "Ashnod's Altar")
        session.state.turn_history.permanent_death_history_version = None
        self.prepare(session)
        self.checkpoint(session)
        self.sacrifice(session, "A", altar, body)
        session = self.replay(session, load=True)
        history = session.state.turn_history.to_dict()
        self.assertNotIn("permanent_death_history_version", history)
        events = [event for event in history["events"] if event["kind"] in {"creature_died", "permanent_died"}]
        death, = events
        self.assertEqual("creature_died", death["kind"])
        self.assertNotIn("creature_subtypes", death)
        self.assertNotIn("token", death)
