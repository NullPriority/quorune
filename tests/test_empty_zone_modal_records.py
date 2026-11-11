from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.record import authoritative_state_hash, semantics_fingerprint
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.zone_replacement_model import ZoneChangeReplacementSnapshot, ZoneReplacementError
from quorune.semantics import SemanticProgram, SemanticRegistry
from quorune.targets import available_modes, target_group_mappings
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
import test_ability_source_counter_targets as native


class OrderedRegistryStorageTests(unittest.TestCase):
    def test_storage_retains_printed_mode_order_in_both_registry_views(self):
        registry = SemanticRegistry(include_builtin_packs=False)
        modes = ("z_printed_first", "a_printed_second")
        program = SemanticProgram(
            key="test:ordered-storage", label="Ordered storage fixture",
            oracle_id="test:ordered-storage", ability_id="spell:front", effects=[],
            target_schema={"mode_count": 2, "modes": {
                name: {"groups": [], "effects": [{"op": "life", "player": "$controller", "delta": index + 1}]}
                for index, name in enumerate(modes)
            }},
        )
        registry.put(program)
        before = semantics_fingerprint(registry)
        with tempfile.TemporaryDirectory() as directory:
            registry.path = Path(directory) / "semantics.json"
            registry.save()
            loaded = SemanticRegistry(registry.path, include_builtin_packs=False)
        self.assertEqual(before, semantics_fingerprint(loaded))
        restored = loaded.get(program.key)
        self.assertEqual(modes, available_modes(restored.target_schema))
        target_group_mappings(restored.target_schema, modes)
        for card_program in loaded.card_programs():
            for ability in card_program.abilities:
                self.assertEqual(modes, available_modes(ability.target_schema))


class EmptyZoneModalRecordTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.AbilitySourceCounterRuntimeTests.session
    add = native.AbilitySourceCounterRuntimeTests.add
    prepare = native.AbilitySourceCounterRuntimeTests.prepare
    checkpoint = native.AbilitySourceCounterRuntimeTests.checkpoint
    actions = native.AbilitySourceCounterRuntimeTests.actions
    reject = native.AbilitySourceCounterRuntimeTests.reject
    cast = native.AbilitySourceCounterRuntimeTests.cast
    priority = native.AbilitySourceCounterRuntimeTests.priority
    settle = native.AbilitySourceCounterRuntimeTests.settle
    replay = native.AbilitySourceCounterRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "empty-zone-modal-records.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/scryfall-exact-lists.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Empty zone and modal record fixtures", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_empty_graveyard_exile_resolves_for_self_or_opponent_and_replays(self):
        for index, affected in enumerate("AB"):
            with self.subTest(affected=affected):
                session = self.session(965_910 + index)
                source = self.add(session.engine, "Bojuka Bog", zone="hand")
                session.state.players["A"].land_plays_remaining = 1
                self.prepare(session)
                self.checkpoint(session)
                row = next(row for row in self.actions(session) if row.get("card") == source.ref)
                self.reject(session, {"action_id": row["id"]}, seat="D")
                result = session.act("pilot:A", {"action_id": row["id"]})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("semantic.target", self.settle(session).kind)
                command = {"action_id": "choose", "targets": [affected]}
                self.reject(session, command, seat="D")
                result = session.act("pilot:A", command)
                self.assertTrue(result.ok, result.summary)
                before = {oid: (card.zone, card.zone_change_counter) for oid, card in session.state.cards.items()}
                self.assertIsNone(self.settle(session))
                self.assertEqual(before, {oid: (card.zone, card.zone_change_counter) for oid, card in session.state.cards.items()})
                self.assertFalse(session.state.players[affected].zones["graveyard"])
                self.replay(session, load=True)

    def test_empty_zone_batch_keeps_damage_clear_and_rejects_invalid_metadata(self):
        session = self.session(965_912)
        body = self.add(session.engine, "Generic Bound Body")
        body.marked_damage = 1
        self.prepare(session)
        owner = ZoneTransitionOwner(session.engine)
        self.assertEqual([], owner.move_cards_simultaneously(
            [], reason="Empty transaction with destruction aftermath",
            damage_clears=[(body.object_id, body.logical_object_id)],
        ))
        self.assertEqual(0, session.state.cards[body.object_id].marked_damage)
        before = authoritative_state_hash(session.state)
        for fields in ({"replacement_selections": ["unused"]}, {"effect_entry_counters": {"ghost": ()}},
                       {"prepared_replacements": {"ghost": None}}):
            with self.subTest(fields=fields), self.assertRaises(GameRuleError):
                owner.move_cards_simultaneously([], reason="Malformed empty transaction", **fields)
            self.assertEqual(before, authoritative_state_hash(session.state))
        with self.assertRaises(GameRuleError):
            owner.move_cards_simultaneously([(body.object_id, "hand")] * 2, reason="Duplicate objects")
        self.assertEqual(before, authoritative_state_hash(session.state))
        with self.assertRaises(ZoneReplacementError):
            ZoneChangeReplacementSnapshot(0, 0, ("A",), (), (), ())

    def test_modal_trigger_pending_choice_and_resolved_record_replay(self):
        session = self.session(965_913)
        source = self.add(session.engine, "Retreat to Coralhelm", zone="hand")
        land = self.add(session.engine, "Forest", zone="hand")
        body = self.add(session.engine, "Generic Bound Body", seat="B")
        body.tapped = True
        session.state.players["A"].land_plays_remaining = 1
        self.prepare(session, mana={"C": 2, "U": 1})
        self.checkpoint(session)
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        row = next(row for row in self.actions(session) if row.get("card") == land.ref)
        result = session.act("pilot:A", {"action_id": row["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.target", self.settle(session).kind)
        session = self.replay(session, load=True)
        command = {"action_id": "choose", "modes": ["untap"], "targets": [body.ref]}
        self.reject(session, command, seat="D")
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.settle(session))
        self.assertFalse(session.state.cards[body.object_id].tapped)
        self.replay(session, load=True)
