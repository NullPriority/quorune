"""Native evidence for next-personal-turn protection and controlled phasing."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError
from quorune.deck import DeckDefinition, DeckEntry
from quorune.life_gain_permission import ResolvedLifeGainProhibition
from quorune.life_state import available_life_for_payment, prepare_life_payment
from quorune.oracle_ir import compile_oracle_card
from quorune.phasing_model import CONTROLLED_PERMANENT_PHASE_OUT_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.next_personal_turn_protection import NEXT_PERSONAL_TURN_PROTECTION_CAPABILITY
from quorune.semantic_runtime.life_prohibitions import life_payment_prohibited
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


ORIGINAL = "Teferi's Protection"


class NextPersonalTurnProtectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Next personal turn protection", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "next-personal-turn-protection.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/next-personal-turn-protection-cards.json", path)
        cls.use_database(path)

    def actions(self, game, seat):
        self.priority(game, seat)
        return {row["id"]: row for row in game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]}

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def until_following_turn(self, game, after, seat):
        def reached(current):
            return current.state.turn_sequence > after and current.state.active_player == seat

        for _ in range(len(game.state.players) + 1):
            self.until(game, lambda current: reached(current) or (
                current.state.pending_decision is not None
                and current.state.pending_decision.kind == "cleanup.discard"
            ))
            if reached(game):
                return
            principal = game.pending_principals()[0]
            context = game.packet(principal, full=True)["decision"]["ctx"]
            cards = [row["id"] for row in context["hand"][:context["count"]]]
            result = game.act(principal, {"action_id": "discard", "cards": cards})
            self.assertTrue(result.ok, result.summary)
        self.fail(f"The following turn for {seat} was not reached")

    def test_actual_protection_exiles_source_and_restores_permanents_on_the_resolvers_untap(self):
        game = self.session(70216101)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, ORIGINAL, zone="hand")
        life = self.add(game.engine, "Revitalize", zone="hand")
        wall = self.add(game.engine, "Wall of Blood", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        bear.counters["+1/+1"] = 1
        ring = self.add(game.engine, "Sol Ring")
        ring.tapped = True
        equipment = self.add(game.engine, "Bonesplitter", seat="B")
        attach_objects(game.state.cards, equipment, bear, source_timestamp=equipment.zone_timestamp,
                       players=game.state.players)
        outsider = self.add(game.engine, "Sol Ring", seat="C", ref="outside-controlled-set")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        second = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="protected-player-bolt")
        for card in (source, life, wall, bolt):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "W": 2, "B": 1})
        game.state.players["B"].mana_pool["R"] = 2
        self.checkpoint(game)
        for seat in "BCD":
            self.assertNotIn(source.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.cast(game, "B", bolt, [bear.ref])
        self.cast_for(game, "A", source, reserve={"C": 3, "W": 1, "B": 1})
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        for permanent in (bear, ring, equipment):
            current = game.state.cards[permanent.object_id]
            self.assertTrue(current.phased_out)
            self.assertEqual(permanent.logical_object_id, current.logical_object_id)
            self.assertEqual("battlefield", current.zone)
        for permanent in (bear, ring):
            self.assertEqual("A", game.state.cards[permanent.object_id].phasing_record.return_seat)
        equipment_record = game.state.cards[equipment.object_id].phasing_record
        self.assertTrue(equipment_record.indirect)
        self.assertEqual(bear.logical_object_id, equipment_record.attached_object.logical_object_id)
        self.assertFalse(game.state.cards[outsider.object_id].phased_out)
        self.assertEqual(0, available_life_for_payment(game.engine, "A"))
        self.assertEqual(0, prepare_life_payment(game.engine, "A", 0).amount)
        self.assertFalse(life_payment_prohibited(game.engine, "B"))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[bolt.object_id].zone)
        self.assertEqual(0, game.state.cards[bear.object_id].marked_damage)

        game = self.replay(game, load=True)
        life_before = game.state.players["A"].life
        hand_before = len(game.state.players["A"].zones["hand"])
        self.cast_for(game, "A", life, reserve={"C": 2, "B": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand_before, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(life_before, game.state.players["A"].life)
        self.cast(game, "A", wall)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[wall.object_id].phased_out)
        self.assertFalse(any(key.startswith(f"activate:{wall.ref}:") for key in self.actions(game, "A")))
        self.reject(game, "A", {"action_id": f"activate:{wall.ref}:0"})
        offered = self.actions(game, "B")["cast:" + second.ref]
        self.assertIn(wall.ref, offered["target_schema"]["legal_refs"])
        for ref in ("A", bear.ref):
            self.assertNotIn(ref, offered["target_schema"]["legal_refs"])
            self.reject(game, "B", {"action_id": offered["id"], "targets": [ref], "pay": "auto"})
        turn = game.state.turn_sequence
        self.until_following_turn(game, turn, "B")
        self.assertTrue(life_payment_prohibited(game.engine, "A"))
        self.assertTrue(game.state.cards[bear.object_id].phased_out)
        self.until_following_turn(game, turn, "A")
        self.assertFalse(life_payment_prohibited(game.engine, "A"))
        self.assertNotIn("protection_from_everything_until_next_turn", game.state.players["A"].stats)
        for permanent in (bear, ring, equipment):
            current = game.state.cards[permanent.object_id]
            self.assertFalse(current.phased_out)
            self.assertEqual(permanent.logical_object_id, current.logical_object_id)
            self.assertIsNone(current.phasing_record)
        self.assertEqual({"+1/+1": 1}, game.state.cards[bear.object_id].counters)
        self.assertFalse(game.state.cards[ring.object_id].tapped)
        self.assertEqual(bear.object_id, game.state.cards[equipment.object_id].attached_to)
        self.assertTrue(any(key.startswith(f"activate:{wall.ref}:") for key in self.actions(game, "A")))
        self.replay(game, load=True)

    def test_existing_phasing_response_excludes_a_pending_target_and_replays(self):
        game = self.session(70216103)
        game.state.config.semantic_policy = "trusted_only"
        concealment = self.add(game.engine, "Clever Concealment", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        for spell in (concealment, bolt):
            self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 2, "W": 2})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast(game, "B", bolt, [creature.ref])
        self.cast(game, "A", concealment, [creature.ref])
        self.resolve_top(game)
        self.assertTrue(game.state.cards[creature.object_id].phased_out)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[bolt.object_id].zone)
        self.assertFalse(game.state.stack)
        self.assertEqual(0, game.state.cards[creature.object_id].marked_damage)
        self.assertTrue(any(event.code == "target.illegal" and event.details.get("target") == creature.ref
                            for event in game.state.events))
        self.replay(game, load=True)

    def test_actual_unpreventable_damage_keeps_its_event_without_changing_protected_life(self):
        game = self.session(70216102)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, ORIGINAL, zone="hand")
        pain = self.add(game.engine, "Flaring Pain", seat="B", zone="hand")
        rift = self.add(game.engine, "Flame Rift", seat="B", zone="hand")
        for card in (source, pain, rift):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"C": 2, "R": 2})
        game.state.players["A"].mana_pool.update({"C": 2, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", pain, reserve={"C": 1, "R": 1})
        self.resolve_top(game)
        self.cast(game, "B", rift)
        self.cast(game, "A", source)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual([36, 36, 36], [game.state.players[key].life for key in "BCD"])
        self.assertEqual([4], [event.amount for event in game.engine._current_turn_history("player_damaged")
                              if event.target == "A"])
        self.replay(game, load=True)

    def test_full_source_withdrawal_and_nearby_scope_reject_before_native_admission(self):
        card = self.db.lookup(ORIGINAL)
        for cap in (NEXT_PERSONAL_TURN_PROTECTION_CAPABILITY, CONTROLLED_PERMANENT_PHASE_OUT_CAPABILITY):
            raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
            next(row for row in raw["capabilities"] if row["id"] == cap).update(
                status="blocked", blockers=["Exact next-turn authority withdrawn"],
            )
            registry = CapabilityRegistry(raw)
            index = build_index(registry_value=raw,
                                declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
                                discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry,
                                                            capability_profile="commander_review").status)
        for text in (card.oracle_text.replace("permanents you control", "creatures you control"),
                     card.oracle_text.replace("your next turn", "the next turn"),
                     card.oracle_text.replace("Exile " + ORIGINAL, "Exile that card")):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                                            capability_registry=self.registry,
                                                            capability_profile="commander_review").status)

    def test_next_turn_life_journal_preserves_legacy_wire_shapes(self):
        model = ResolvedLifeGainProhibition
        end = ContinuousEffectDuration.UNTIL_END_OF_TURN
        following = ContinuousEffectDuration.UNTIL_NEXT_TURN
        effects = (
            model("LG1", "S1", 1, "A", ("B",)),
            model("LG2", "S1", 2, "A", ("A", "B"), end, "any"),
            model("LG3", "S1", 3, "A", ("A",), end, "controller"),
            model("LG4", "S1", 4, "A", ("A",), following, "controller"),
        )
        for version, effect in enumerate(effects, 1):
            wire = effect.to_dict()
            self.assertEqual(version, wire["schema_version"])
            self.assertEqual(wire, model.from_dict(wire).to_dict())
            self.assertEqual("A" if version == 4 else None, effect.expires_on_next_turn_of)
        with self.assertRaises(ContinuousEffectError):
            model.from_dict({**effects[-1].to_dict(), "schema_version": 3})

    def test_omitting_life_or_phasing_breaks_the_named_native_witnesses(self):
        with patch("quorune.semantic_runtime.life_prohibitions.apply_temporary_life_gain_prohibition"):
            with self.assertRaises(AssertionError):
                self.test_actual_unpreventable_damage_keeps_its_event_without_changing_protected_life()
        with patch("quorune.phasing.phase_out_permanents", return_value=()):
            with self.assertRaises(AssertionError):
                self.test_actual_protection_exiles_source_and_restores_permanents_on_the_resolvers_untap()
