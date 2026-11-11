from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_ability_source_counter_targets as native


class CompiledReviewedSpellConsumerTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.AbilitySourceCounterRuntimeTests.session
    add = native.AbilitySourceCounterRuntimeTests.add
    prepare = native.AbilitySourceCounterRuntimeTests.prepare
    checkpoint = native.AbilitySourceCounterRuntimeTests.checkpoint
    priority = native.AbilitySourceCounterRuntimeTests.priority
    actions = native.AbilitySourceCounterRuntimeTests.actions
    binding = native.AbilitySourceCounterRuntimeTests.binding
    reject = native.AbilitySourceCounterRuntimeTests.reject
    settle = native.AbilitySourceCounterRuntimeTests.settle
    replay = native.AbilitySourceCounterRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "compiled-reviewed-spells.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/compiled-reviewed-spell-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Compiled reviewed spells", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def cast(self, session, source, targets=(), *, seat="A", **choices):
        self.priority(session, seat)
        row = next(row for row in self.actions(session, seat) if row.get("card") == source.ref)
        self.assertTrue(self.binding(session, source)["strict_capability_ready"])
        command = {"action_id": row["id"], "targets": list(targets), "pay": "auto", **choices}
        self.reject(session, command, seat="D" if seat != "D" else "C")
        result = session.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def test_compiled_modal_spells_enforce_each_announced_target_group(self):
        for index, (name, mode) in enumerate((
            ("Red Elemental Blast", "mode_1"), ("Red Elemental Blast", "mode_2"),
            ("Abrade", "mode_1"), ("Abrade", "mode_2"),
        )):
            with self.subTest(card=name, mode=mode):
                session = self.session(964_000 + index)
                source = self.add(session.engine, name, zone="hand")
                artifact = self.add(session.engine, "Sol Ring", seat="B")
                creature = self.add(session.engine, "Centaur Courser", seat="B")
                blue = self.add(session.engine, "Merfolk of the Pearl Trident", seat="B")
                victim = self.add(session.engine, "Opt", seat="B", zone="hand")
                self.prepare(session, mana={"C": 1, "R": 1})
                session.state.players["B"].mana_pool.update(C=1, U=1)
                self.checkpoint(session)
                if name == "Red Elemental Blast" and mode == "mode_1":
                    self.cast(session, victim, seat="B")
                    target = session.state.stack[-1].ref
                    resolved = victim
                else:
                    resolved = blue if name == "Red Elemental Blast" else creature if mode == "mode_1" else artifact
                    target = resolved.ref
                self.priority(session, "A")
                row = next(row for row in self.actions(session) if row.get("card") == source.ref)
                wrong = creature if mode == "mode_2" else artifact
                command = {"action_id": row["id"], "modes": [mode], "targets": [target], "pay": "auto"}
                self.reject(session, {**command, "targets": [wrong.ref]})
                self.reject(session, {**command, "modes": ["mode_1", "mode_2"]})
                self.cast(session, source, (target,), modes=[mode])
                session = self.replay(session, load=True)
                self.assertIsNone(self.settle(session))
                self.assertEqual("graveyard", session.state.cards[resolved.object_id].zone)
                untouched = artifact if resolved.object_id != artifact.object_id else creature
                self.assertEqual("battlefield", session.state.cards[untouched.object_id].zone)
                self.replay(session, load=True)

    def test_compiled_feed_the_swarm_loses_life_for_a_legal_indestructible_target(self):
        for index, name in enumerate(("Centaur Courser", "Darksteel Myr")):
            with self.subTest(target=name):
                session = self.session(964_010 + index)
                source = self.add(session.engine, "Feed the Swarm", zone="hand")
                target = self.add(session.engine, name, seat="B", ref="feed-enemy")
                own = self.add(session.engine, "Centaur Courser", ref="feed-own")
                self.prepare(session, mana={"C": 1, "B": 1})
                self.checkpoint(session)
                self.reject(session, {"action_id": "cast:" + source.ref, "targets": [own.ref], "pay": "auto"})
                self.cast(session, source, (target.ref,))
                session = self.replay(session, load=True)
                self.assertIsNone(self.settle(session))
                self.assertEqual(37, session.state.players["A"].life)
                self.assertEqual("battlefield" if name == "Darksteel Myr" else "graveyard",
                                 session.state.cards[target.object_id].zone)
                self.replay(session, load=True)

    def test_compiled_force_of_vigor_pitches_off_turn_and_revalidates_each_target(self):
        session = self.session(964_020)
        source = self.add(session.engine, "Force of Vigor", zone="hand")
        green = self.add(session.engine, "Grizzly Bears", zone="hand")
        wrong = self.add(session.engine, "Revitalize", zone="hand")
        artifact = self.add(session.engine, "Sol Ring", seat="B")
        returned = self.add(session.engine, "Ornithopter", seat="C")
        bounce = self.add(session.engine, "Unsummon", seat="C", zone="hand")
        self.prepare(session, mana={})
        session.state.active_player = "B"
        session.state.players["C"].mana_pool["U"] = 1
        session.engine.permissions.invalidate_current()
        session.state.pending_decision = None
        session.engine._grant_priority("A")
        session.engine.pump()
        self.checkpoint(session)
        row = next(row for row in self.actions(session) if row.get("card") == source.ref)
        pitch = next(option for option in row["cost_options"] if option["choice_schema"].get("exile_cards"))
        refs = pitch["choice_schema"]["exile_cards"]["legal_refs"]
        self.assertIn(green.ref, refs)
        self.assertNotIn(wrong.ref, refs)
        command = {"action_id": row["id"], "cost_option": pitch["id"], "exile_cards": [green.ref],
                   "targets": [artifact.ref, returned.ref], "pay": "auto"}
        self.reject(session, {**command, "exile_cards": [wrong.ref]})
        self.reject(session, {**command, "targets": [artifact.ref, artifact.ref]})
        self.cast(session, source, (artifact.ref, returned.ref), cost_option=pitch["id"], exile_cards=[green.ref])
        self.assertEqual("exile", session.state.cards[green.object_id].zone)
        self.cast(session, bounce, (returned.ref,), seat="C")
        session = self.replay(session, load=True)
        self.assertIsNone(self.settle(session))
        self.assertEqual("hand", session.state.cards[returned.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[artifact.object_id].zone)
        self.replay(session, load=True)

        own_turn = self.session(964_021)
        source = self.add(own_turn.engine, "Force of Vigor", zone="hand")
        green = self.add(own_turn.engine, "Grizzly Bears", zone="hand")
        self.prepare(own_turn, mana={})
        self.checkpoint(own_turn)
        self.assertFalse(any(row.get("card") == source.ref for row in self.actions(own_turn)))
        self.reject(own_turn, {"action_id": "cast:" + source.ref, "cost_option": pitch["id"],
                               "exile_cards": [green.ref], "targets": [], "pay": "auto"})

    def test_compiled_toxic_deluge_pays_life_before_resolution_without_a_refund(self):
        for countered in (False, True):
            with self.subTest(countered=countered):
                session = self.session(964_030 + countered)
                source = self.add(session.engine, "Toxic Deluge", zone="hand")
                small = self.add(session.engine, "Grizzly Bears")
                large = self.add(session.engine, "Centaur Courser", seat="B")
                response = self.add(session.engine, "Counterspell", seat="B", zone="hand")
                session.state.players["A"].life = 5
                session.state.players["B"].mana_pool["U"] = 2
                self.prepare(session, mana={"C": 2, "B": 1})
                self.checkpoint(session)
                for invalid in (-1, True, 6):
                    self.reject(session, {"action_id": "cast:" + source.ref, "x": invalid, "pay": "auto"})
                self.cast(session, source, x=2)
                self.assertEqual(3, session.state.players["A"].life)
                if countered:
                    self.cast(session, response, (session.state.stack[-1].ref,), seat="B")
                session = self.replay(session, load=True)
                self.assertIsNone(self.settle(session))
                self.assertEqual(3, session.state.players["A"].life)
                self.assertEqual("battlefield" if countered else "graveyard", session.state.cards[small.object_id].zone)
                self.assertEqual(3 if countered else 1, session.engine._numeric_stat(large.object_id, "toughness"))
                self.replay(session, load=True)

    def test_compiled_life_from_the_loam_returns_own_lands_then_replaces_a_real_draw(self):
        session = self.session(964_040)
        source = self.add(session.engine, "Life from the Loam", zone="hand")
        lands = [self.add(session.engine, name, zone="graveyard") for name in ("Forest", "Mountain", "Island")]
        foreign = self.add(session.engine, "Forest", seat="B", zone="graveyard", ref="loam-foreign-land")
        nonland = self.add(session.engine, "Sol Ring", zone="graveyard")
        draw = self.add(session.engine, "Revitalize", zone="hand")
        self.prepare(session, mana={"C": 2, "G": 1, "W": 1})
        self.checkpoint(session)
        for wrong in (foreign, nonland):
            self.reject(session, {"action_id": "cast:" + source.ref, "targets": [wrong.ref], "pay": "auto"})
        self.cast(session, source, [card.ref for card in lands])
        self.assertIsNone(self.settle(session))
        self.assertTrue(all(session.state.cards[card.object_id].zone == "hand" for card in lands))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        before_library = len(session.state.players["A"].zones["library"])
        before_draws = len(session.state.players["A"].draw_history)
        self.cast(session, draw)
        self.assertEqual("draw.replacement", self.settle(session).kind)
        for seat in "BCD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        command = {"action_id": "choose", "choice": source.ref}
        self.reject(session, command, seat="D")
        session = self.replay(session, load=True)
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.settle(session))
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.assertEqual(before_library - 3, len(session.state.players["A"].zones["library"]))
        self.assertEqual(before_draws, len(session.state.players["A"].draw_history))
        self.replay(session, load=True)
