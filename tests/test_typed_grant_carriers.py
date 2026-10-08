from __future__ import annotations

"""Printed grants: CR 113.7a, 604.1, 611.3 and 613.1f establish expectations."""

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session, pass_current
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.conditional_continuous import (
    FixedPublicStateCharacteristicsHandler, FixedPublicStateGrantedAbilityHandler,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


CAPABILITY = "continuous.ability.fixed_public_state_grant"
FIXTURE = ROOT / "tests/fixtures/typed-grant-carrier-cards.json"


class TypedGrantCarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "typed-grants.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/scryfall-exact-lists.json", FIXTURE], path)
        cls.db = CardDatabase(path)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(ROOT / "examples/mishra-eminent-one.txt", commander="Mishra, Eminent One")
        cls.zimone = loader.load(ROOT / "examples/zimone-and-dina.txt", commander="Zimone and Dina")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed):
        session = make_session(self.db, self.mishra, self.zimone, players=4, seed=seed, auto_pass_empty=False)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        for seat in engine.seats:
            engine.state.players[seat].mana_pool.update(C=20, W=20, U=20, B=20, R=20, G=20)
        return session

    def card(self, session, name, zone="hand", seat="A"):
        engine = session.engine
        record = self.db.lookup(name, fuzzy=False)
        ref = engine._next_ref("P")
        card = CardInstance(
            object_id=f"typed-grant:{ref}", ref=ref, oracle_id=record.oracle_id,
            printed_name=record.name, owner=seat, controller=seat, zone=zone,
            zone_timestamp=engine._next_zone_timestamp(),
            known_to=list(engine.seats) if zone in {"battlefield", "graveyard"} else [seat],
            revealed_to=list(engine.seats) if zone == "battlefield" else [],
            entered_battlefield_turn_sequence=engine.state.turn_sequence - 1,
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db, engine.semantics, (record,), capability_registry=load_default_capability_registry(),
            capability_profile="commander_review", promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        return card

    def priority(self, session, seat):
        engine = session.engine
        if engine.state.pending_decision is None:
            engine._grant_priority(engine.state.priority_player or engine.state.active_player)
            engine.pump()
        for _ in range(12):
            decision = engine.state.pending_decision
            if decision is not None and seat in decision.actors:
                break
            pass_current(session)
        else:
            self.fail(f"No supported priority window reached {seat}")
        return session.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]

    def cast(self, session, card, *, targets=()):
        actions = self.priority(session, card.controller)
        action_id = f"cast:{card.ref}"
        self.assertTrue(any(a["id"] == action_id for a in actions), actions)
        result = session.act(f"pilot:{card.controller}", {"action_id": action_id, "targets": list(targets), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def resolve(self, session):
        for _ in range(40):
            if not session.state.stack and not session.state.pending_trigger_batches:
                return
            if session.state.pending_decision and session.state.pending_decision.role == "arbiter":
                top = session.state.stack[-1]
                program = session.engine.semantics.get(top.semantic_key) if top.semantic_key else None
                self.fail(str({
                    "untrusted_stack_key": top.semantic_key,
                    "program_trust": program.trust_level if program else None,
                    "current_trusted": session.engine.semantic_program_is_current_trusted(program),
                    "capabilities": program.capability_dependencies if program else None,
                }))
            pass_current(session)
        self.fail("The offered action did not finish resolving")

    @staticmethod
    def checkpoint(session):
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

    def assert_replay(self, session):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            result = replay_record(directory, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(expected, result["final_state_hash"])

    def test_original_grant_carriers_are_capability_bound(self):
        registry = load_default_capability_registry()
        for name in ("Squirrel Nest", "Orochi Merge-Keeper", "Flaring Flame-Kin", "Relic Vial", "Jaheira, Friend of the Forest", "Underworld Connections", "Hunting Grounds"):
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(), capability_registry=registry, capability_profile="commander_review")
                binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding)
                if name in {"Orochi Merge-Keeper", "Flaring Flame-Kin", "Relic Vial", "Hunting Grounds"}:
                    self.assertTrue(any(CAPABILITY in n.capability_dependencies for f in ir.faces for n in f.nodes))

    def test_conditional_grant_schema_and_dependencies_fail_closed(self):
        registry = load_default_capability_registry()
        record = self.db.lookup("Flaring Flame-Kin")
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        descriptor = next(h for f in ir.faces for n in f.nodes for h in n.handlers if h.get("schema_version") == 2)
        handler = FixedPublicStateGrantedAbilityHandler()
        handler.validate(descriptor)
        with self.assertRaises(SemanticNodeError):
            FixedPublicStateCharacteristicsHandler().validate(descriptor)
        old_header = copy.deepcopy(descriptor)
        old_header.update(handler_id="continuous.characteristics.fixed-public-state.v1", schema_version=1)
        with self.assertRaises(SemanticNodeError):
            FixedPublicStateCharacteristicsHandler().validate(old_header)
        for field, value in (("schema_version", True), ("event", "arbitrary"), ("unknown", True)):
            bad = copy.deepcopy(descriptor)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(SemanticNodeError):
                handler.validate(bad)
        for fragments in ([], [descriptor["modifier"]["add_ability_fragments"][0]] * 2, [dict(kind="arbitrary")]):
            bad = copy.deepcopy(descriptor)
            bad["modifier"]["add_ability_fragments"] = fragments
            with self.assertRaises(SemanticNodeError):
                handler.validate(bad)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        row = next(r for r in raw["capabilities"] if r["id"] == CAPABILITY)
        row.update(status="blocked", blockers=["Deliberately unavailable owner"])
        blocked = compile_oracle_card(record, capability_registry=CapabilityRegistry(raw), capability_profile="commander_review")
        self.assertNotEqual("exact", blocked.status)
        for text in (
            'As long as this creature is enchanted, it has "{X}: Draw X cards."',
            'As long as a creature has flying, this creature has "{T}: Draw a card."',
            'As long as this creature is enchanted, it has "{T}: Draw a card." and "{T}: You gain 1 life."',
        ):
            rejected = compile_oracle_card(replace(record, oracle_text=text), capability_registry=registry, capability_profile="commander_review")
            self.assertNotEqual("exact", rejected.status, text)
        rune = compile_oracle_card(self.db.lookup("Rune of Might"), capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", rune.status)

    def test_printed_land_grant_uses_recipient_and_replays(self):
        session = self.session(61130001)
        land = self.card(session, "Forest", "battlefield")
        aura = self.card(session, "Squirrel Nest")
        destroy = self.card(session, "Disenchant", seat="B")
        self.priority(session, "A")
        self.checkpoint(session)
        self.cast(session, aura, targets=(land.ref,))
        self.resolve(session)
        self.assertEqual(land.object_id, aura.attached_to)
        ability = next(a for a in session.engine._activated_abilities(land) if a.builtin_semantic_key and "ability:granted" in a.builtin_semantic_key)
        actions = self.priority(session, "A")
        action_id = f"activate:{land.ref}:{ability.ability_id}"
        self.assertTrue(any(a["id"] == action_id for a in actions))
        before = authoritative_state_hash(session.state)
        wrong = session.act("pilot:B", {"action_id": action_id})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        activated = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(activated.ok, activated.summary)
        self.assertTrue(land.tapped)
        self.assertFalse(aura.tapped)
        self.cast(session, destroy, targets=(aura.ref,))
        self.resolve(session)
        self.assertEqual("graveyard", aura.zone)
        self.assertFalse(any(a.builtin_semantic_key == ability.builtin_semantic_key for a in session.engine._activated_abilities(land)))
        squirrels = [c for c in session.state.cards.values() if c.zone == "battlefield" and c.printed_name == "Squirrel"]
        self.assertEqual(1, len(squirrels))
        self.assertEqual("A", squirrels[0].controller)
        private = session.state.players["A"].zones["hand"]
        projected = json.dumps(session.packet("pilot:B", full=True))
        for object_id in private:
            self.assertNotIn(object_id, projected)
        self.assert_replay(session)

    def test_conditional_grant_compiler_mutation_is_killed(self):
        record = self.db.lookup("Flaring Flame-Kin")
        registry = load_default_capability_registry()
        with mock.patch(
            "quorune.compiler.attached_granted_ability_nodes.conditional_quoted_ability_text",
            return_value=None,
        ):
            result = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", result.status)

    def test_printed_conditional_activation_ends_without_erasing_stack(self):
        session = self.session(61130002)
        source = self.card(session, "Flaring Flame-Kin", "battlefield")
        aura = self.card(session, "Holy Strength")
        destroy = self.card(session, "Disenchant", seat="B")
        self.assertFalse(any(a.builtin_semantic_key and "ability:granted" in a.builtin_semantic_key for a in session.engine._activated_abilities(source)))
        self.priority(session, "A")
        self.checkpoint(session)
        self.cast(session, aura, targets=(source.ref,))
        self.resolve(session)
        data = session.engine._effective_card_data(source)
        self.assertEqual((5, 6), (int(data["power"]), int(data["toughness"])))
        self.assertIn("Trample", data["keywords"])
        ability = next(a for a in session.engine._activated_abilities(source) if a.builtin_semantic_key and "ability:granted" in a.builtin_semantic_key)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        self.assertTrue(any(a["id"] == action_id for a in self.priority(session, "A")))
        result = session.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.cast(session, destroy, targets=(aura.ref,))
        self.resolve(session)
        self.assertFalse(any(a.builtin_semantic_key == ability.builtin_semantic_key for a in session.engine._activated_abilities(source)))
        data = session.engine._effective_card_data(source)
        self.assertEqual((3, 2), (int(data["power"]), int(data["toughness"])))
        self.assertNotIn("Trample", data["keywords"])
        self.assert_replay(session)

    def test_printed_conditional_granted_death_trigger_uses_event_time_state(self):
        session = self.session(61130003)
        vial = self.card(session, "Relic Vial", "battlefield")
        cleric = self.card(session, "Soul Warden", "battlefield")
        other = self.card(session, "Runeclaw Bear", "battlefield")
        murder = self.card(session, "Murder", seat="B")
        second_murder = self.card(session, "Murder", seat="B")
        life = {seat: session.state.players[seat].life for seat in session.engine.seats}
        self.priority(session, "B")
        self.checkpoint(session)
        self.cast(session, murder, targets=(cleric.ref,))
        self.resolve(session)
        self.assertEqual("graveyard", cleric.zone)
        self.assertEqual(life["A"] + 1, session.state.players["A"].life)
        for seat in ("B", "C", "D"):
            self.assertEqual(life[seat] - 1, session.state.players[seat].life)
        self.assertFalse(any(f.get("kind") == "granted_triggered_ability" for f in session.engine._effective_card_data(vial)["ability_fragments"]))
        after_last_cleric = {seat: session.state.players[seat].life for seat in session.engine.seats}
        self.cast(session, second_murder, targets=(other.ref,))
        self.resolve(session)
        self.assertEqual("graveyard", other.zone)
        self.assertEqual(after_last_cleric, {seat: session.state.players[seat].life for seat in session.engine.seats})
        self.assert_replay(session)

    def test_printed_conditional_granted_hand_choice_is_private_and_persists(self):
        session = self.session(61130004)
        self.card(session, "Hunting Grounds", "battlefield")
        victim = self.card(session, "Runeclaw Bear", "battlefield")
        candidate = self.card(session, "Runeclaw Bear")
        spell = self.card(session, "Murder", seat="B")
        for _ in range(7):
            self.card(session, "Forest", "graveyard")
        self.priority(session, "B")
        self.checkpoint(session)
        self.cast(session, spell, targets=(victim.ref,))
        for _ in range(28):
            pending = session.state.pending_decision
            if pending is not None and pending.kind == "semantic.choice":
                break
            pass_current(session)
        else:
            self.fail("Printed Hunting Grounds did not offer its hand choice")
        decision = session.packet("pilot:A", full=True)["decision"]
        self.assertIn(candidate.ref, json.dumps(decision))
        for seat in ("B", "C", "D"):
            self.assertNotIn(candidate.ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        before = authoritative_state_hash(session.state)
        wrong = session.act("pilot:B", {"action_id": "choose", "card": candidate.ref})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            session = CommanderSession.load(self.db, directory)
        accepted = session.act("pilot:A", {"action_id": "choose", "card": candidate.ref})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        moved = session.state.cards[candidate.object_id]
        self.assertEqual("battlefield", moved.zone)
        self.assertEqual("A", moved.controller)
        self.assertFalse(moved.tapped)
        self.assert_replay(session)
