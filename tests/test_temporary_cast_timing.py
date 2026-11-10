"""Turn-bound flash timing uses the native cast and Suspend boundaries."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
import test_event_spell_copy as native
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_cast_timing import (
    TEMPORARY_CAST_TIMING_CAPABILITY, TEMPORARY_CAST_TIMING_FIELD, TemporaryCastTimingScope,
    temporary_cast_timing_effect_scope,
)
from scripts.build_test_database import build_fixture_database


class TemporaryCastTimingTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.EventSpellCopyNativeTests.session
    add = native.EventSpellCopyNativeTests.add
    prepare = native.EventSpellCopyNativeTests.prepare
    cast_for = native.EventSpellCopyNativeTests.cast_for
    priority = native.EventSpellCopyNativeTests.priority
    checkpoint = native.EventSpellCopyNativeTests.checkpoint
    resolve = native.EventSpellCopyNativeTests.resolve
    replay = native.EventSpellCopyNativeTests.replay
    assert_closed = native.EventSpellCopyNativeTests.assert_closed
    until = native.EventSpellCopyNativeTests.until
    action = native.EventSpellCopyNativeTests.action
    finish = native.EventSpellCopyNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixtures = ROOT / "tests/fixtures"
        path = Path(cls.temporary.name) / "temporary-cast-timing.sqlite3"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "event-spell-copy-cards.json", "temporary-cast-timing-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Temporary cast timing", [DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Isamaru, Hound of Konda"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def offer(self, session, seat, card, action):
        self.priority(session, seat)
        rows = session.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in rows if row.get("card") == card.ref and row.get("action") == action), None)

    def test_temporary_timing_shape_and_runtime_dependency_are_closed(self):
        session = self.session(70208001)
        source = self.add(session.engine, "Quicken", zone="hand")
        self.assert_closed(session, source)
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                    if TEMPORARY_CAST_TIMING_CAPABILITY in p.capability_dependencies]
        deps = tuple(cap for cap in program.capability_dependencies if cap != TEMPORARY_CAST_TIMING_CAPABILITY)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(bound["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + TEMPORARY_CAST_TIMING_CAPABILITY, bound["blockers"])
        effect = {"op": "grant_cast_permission", "schema_version": 1, "player": "$controller",
                  TEMPORARY_CAST_TIMING_FIELD: "next_sorcery"}
        self.assertIs(TemporaryCastTimingScope.NEXT_SORCERY, temporary_cast_timing_effect_scope(effect))
        for fields in ({"schema_version": True}, {"player": "B"}, {"card": "another-zone-card"},
                       {TEMPORARY_CAST_TIMING_FIELD: "arbitrary"}):
            with self.assertRaises(ValueError):
                temporary_cast_timing_effect_scope({**effect, **fields})
        self.assertTrue(TemporaryCastTimingScope.CREATURE_SPELLS.matches("Creature — Bear"))
        self.assertFalse(TemporaryCastTimingScope.CREATURE_SPELLS.matches("Land Creature — Forest Dryad"))

    def test_removing_timing_producer_leaves_an_unfinished_card(self):
        record = self.db.lookup("Quicken")
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        self.assertEqual("exact", compile_oracle_card(record, **options).status)
        with patch("quorune.compiler.temporary_player_rule_templates._temporary_cast_timing_template", return_value=None):
            changed = replace(record, oracle_id="temporary-cast-timing-mutation-probe")
            self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)

    def test_quicken_suspends_without_consumption_and_normal_cast_exhausts_both_grants(self):
        session = self.session(70208002)
        engine = session.engine
        first = self.add(engine, "Quicken", zone="hand", ref="first-quicken")
        second = self.add(engine, "Quicken", zone="hand", ref="second-quicken")
        suspend = self.add(engine, "Rift Bolt", zone="hand")
        sorcery = self.add(engine, "Divination", zone="hand", ref="first-divination")
        later = self.add(engine, "Divination", zone="hand", ref="later-divination")
        blaze = self.add(engine, "Blaze", zone="hand")
        creature = self.add(engine, "Grizzly Bears", zone="hand")
        bolt = self.add(engine, "Lightning Bolt", seat="B", zone="hand", ref="first-bolt")
        follow = self.add(engine, "Lightning Bolt", seat="B", zone="hand", ref="later-bolt")
        self.assert_closed(session, first)
        self.assert_closed(session, suspend)
        session.state.players["B"].mana_pool["R"] = 2
        self.prepare(session, mana={"U": 5, "C": 10, "R": 3, "G": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", first)
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, "A", second)
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, "B", bolt, ["D"])
        self.assertIsNone(self.offer(session, "A", creature, "cast"))
        action = self.offer(session, "A", suspend, "suspend")
        self.assertIsNotNone(action)
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", session.state.cards[suspend.object_id].zone)
        action = self.offer(session, "A", blaze, "cast")
        self.assertIsNotNone(action)
        before = authoritative_state_hash(session.state)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": ["missing"], "x": 1})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertIn(TEMPORARY_CAST_TIMING_FIELD, session.state.players["A"].stats)
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, "A", sorcery, reserve={"R": 1})
        self.assertNotIn(TEMPORARY_CAST_TIMING_FIELD, session.state.players["A"].stats)
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, "B", follow, ["C"])
        self.assertIsNone(self.offer(session, "A", later, "cast"))
        self.assertIsNone(self.resolve(session))
        self.replay(session, load=True)

    def test_complete_the_circuit_copies_once_and_keeps_repeat_sorcery_timing(self):
        session = self.session(70208003)
        engine = session.engine
        circuit = self.add(engine, "Complete the Circuit", zone="hand")
        first = self.add(engine, "Divination", zone="hand", ref="circuit-first")
        later = self.add(engine, "Divination", zone="hand", ref="circuit-second")
        creature = self.add(engine, "Grizzly Bears", zone="hand")
        opposing = self.add(engine, "Divination", seat="C", zone="hand", ref="opposing-divination")
        bolt = self.add(engine, "Lightning Bolt", seat="B", zone="hand")
        self.assert_closed(session, circuit)
        session.state.players["B"].mana_pool["R"] = 1
        session.state.players["C"].mana_pool.update({"U": 1, "C": 4})
        self.prepare(session, active="B", mana={"U": 5, "C": 14, "G": 1})
        self.checkpoint(session)
        self.assertIsNone(self.offer(session, "A", first, "cast"))
        self.cast_for(session, "B", bolt, ["D"])
        self.cast_for(session, "A", circuit, reserve={"U": 2, "G": 1})
        self.until(session, lambda game: game.state.cards[circuit.object_id].zone == "graveyard")
        self.assertIsNone(self.offer(session, "C", opposing, "cast"))
        self.assertNotIn(first.ref, str(session.packet("pilot:C", full=True)))
        self.assertIsNone(self.offer(session, "A", creature, "cast"))
        self.assertTrue(any(item.card_object_id == bolt.object_id for item in session.state.stack))
        hand = len(session.state.players["A"].zones["hand"])
        self.cast_for(session, "A", first, reserve={"U": 1, "G": 1})
        self.finish(session)
        self.assertEqual(hand + 5, len(session.state.players["A"].zones["hand"]))
        self.assertIn(TEMPORARY_CAST_TIMING_FIELD, session.state.players["A"].stats)
        self.assertNotIn(later.ref, str(session.packet("pilot:C", full=True)))
        self.cast_for(session, "A", later)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand + 6, len(session.state.players["A"].zones["hand"]))
        self.replay(session)

    def test_emergence_zone_grant_survives_sacrifice_and_expires_at_cleanup(self):
        session = self.session(70208004)
        engine = session.engine
        zone = self.add(engine, "Emergence Zone")
        first = self.add(engine, "Grizzly Bears", zone="hand", ref="first-bear")
        second = self.add(engine, "Grizzly Bears", zone="hand", ref="second-bear")
        self.assert_closed(session, zone)
        self.prepare(session, active="B", mana={"G": 2, "C": 5})
        self.checkpoint(session)
        self.assertIsNone(self.offer(session, "A", first, "cast"))
        rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation = next(row for row in rows if row.get("source") == zone.ref and not row.get("mana_ability"))
        result = session.act("pilot:A", {"action_id": activation["id"], "pay": "auto", "reserve": {"G": 2}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[zone.object_id].zone)
        self.cast_for(session, "A", first, reserve={"G": 1})
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, "A", second)
        self.assertIsNone(self.resolve(session))
        turn = session.state.turn_sequence
        self.until(session, lambda game: game.state.turn_sequence > turn and game.state.pending_decision is not None)
        self.assertNotIn(TEMPORARY_CAST_TIMING_FIELD, session.state.players["A"].stats)
        self.replay(session, load=True)

    def test_split_second_blocks_cast_and_suspend_through_the_grant(self):
        session = self.session(70208005)
        engine = session.engine
        source = self.add(engine, "Quicken", zone="hand")
        suspended = self.add(engine, "Rift Bolt", zone="hand")
        sorcery = self.add(engine, "Divination", zone="hand")
        shock = self.add(engine, "Sudden Shock", seat="B", zone="hand")
        session.state.players["B"].mana_pool.update({"R": 2, "C": 2})
        self.prepare(session, active="B", mana={"U": 4, "R": 2, "C": 6})
        self.checkpoint(session)
        self.cast_for(session, "A", source, reserve={"R": 1, "U": 1})
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, "B", shock, ["D"])
        self.assertIsNone(self.offer(session, "A", suspended, "suspend"))
        self.assertIsNone(self.offer(session, "A", sorcery, "cast"))
        self.assertIsNone(self.resolve(session))
        self.assertIsNotNone(self.offer(session, "A", suspended, "suspend"))
        self.cast_for(session, "A", sorcery)
        self.assertIsNone(self.resolve(session))
        self.replay(session)

    def test_scouts_warning_requires_land_play_timing(self):
        record = self.db.lookup("Scout's Warning")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)
        self.assertEqual([record.oracle_text.splitlines()[0]], [row.text for row in ir.material_residuals])

    def test_borne_upon_a_wind_reuses_flashback_without_granting_zone_access(self):
        session = self.session(70208007)
        engine = session.engine
        source = self.add(engine, "Borne Upon a Wind", zone="hand")
        flashback = self.add(engine, "Call of the Herd", zone="graveyard")
        ordinary = self.add(engine, "Divination", zone="graveyard")
        self.assert_closed(session, source)
        self.assert_closed(session, flashback)
        self.prepare(session, active="B", mana={"U": 2, "G": 1, "C": 8})
        self.checkpoint(session)
        self.assertIsNone(self.offer(session, "A", flashback, "cast"))
        self.cast_for(session, "A", source, reserve={"G": 1, "U": 1})
        self.assertIsNone(self.resolve(session))
        self.assertIsNone(self.offer(session, "A", ordinary, "cast"))
        self.cast_for(session, "A", flashback)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", session.state.cards[flashback.object_id].zone)
        token, = [card for card in session.state.cards.values()
                  if card.zone == "battlefield" and card.controller == "A" and card.object_kind == "token"]
        self.assertIn("elephant", engine._type_parts(engine._effective_card_data(token)["type_line"])[1])
        self.assertEqual((3, 3), tuple(engine._numeric_stat(token.object_id, stat) for stat in ("power", "toughness")))
        self.replay(session)
