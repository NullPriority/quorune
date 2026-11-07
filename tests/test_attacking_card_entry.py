from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from test_granted_skulk import record
import test_hand_entry_followup as hand

from quorune.banding_model import AttackBand
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CombatState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.attacking_card_entry import (
    ATTACKING_CARD_ENTRY_CAPABILITY, AttackingEntryRecipient, commit_attacking_entry,
)
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database


HAND = "You may put a Soldier creature card from your hand onto the battlefield tapped and attacking."
GRAVEYARD = "Return target creature card from your graveyard to the battlefield tapped and attacking."


class AttackingCardEntryCompilerTests(unittest.TestCase):
    def test_attacking_card_entry_closes_fixed_hand_and_graveyard_selectors(self):
        registry = load_default_capability_registry()
        for text, expected in ((HAND, "put_card_from_hand"), (GRAVEYARD, "choose_option")):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, "Instant"), capability_registry=registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(text, node.text)
                effect, = node.effects
                self.assertEqual(expected, effect["op"])
                self.assertIn(ATTACKING_CARD_ENTRY_CAPABILITY, node.capability_dependencies)
                if expected == "put_card_from_hand":
                    self.assertIsNone(node.target_schema)
                    self.assertEqual(["soldier"], effect["query"]["subtypes_all"])
                else:
                    self.assertEqual(["graveyard"], node.target_schema["zones"])
                    self.assertEqual("$target.0", effect["attacking_card_entry"]["card"])
        for text in (HAND.replace("Soldier creature", "artifact"), HAND.replace("attacking.", "attacking that opponent."),
            GRAVEYARD.replace("attacking.", "attacking with a finality counter on it.")):
            with self.subTest(unsupported=text):
                self.assertNotEqual("exact", compile_oracle_card(record(text, "Instant"), capability_registry=registry).status)

    def test_withdrawing_defender_authority_preserves_ordinary_graveyard_entry(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == "combat.attack.defender")
        row.update(status="blocked", blockers=["withdrawn for the dependency witness"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(record(GRAVEYARD, "Instant"), capability_registry=registry).status)
        plain = GRAVEYARD.replace(" and attacking", "")
        self.assertEqual("exact", compile_oracle_card(record(plain, "Instant"), capability_registry=registry).status)

    def test_omitting_the_attacking_entry_producer_leaves_the_full_instruction_residual(self):
        registry = load_default_capability_registry()
        card = record(HAND, "Instant")
        self.assertEqual("exact", compile_oracle_card(card, capability_registry=registry).status)
        with patch("quorune.compiler.resolution_effect_templates.fixed_attacking_card_entry_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry).status)


class AttackingCardEntryOwnerTests(unittest.TestCase):
    def test_entry_preserves_attack_bands_and_skips_a_stale_defender_incarnation(self):
        existing = AttackBand((("band-1", "band-1@0"), ("band-2", "band-2@0")))
        combat = CombatState(attackers={"band-1": "B", "band-2": "B"}, attack_bands=(existing,))
        card = SimpleNamespace(object_id="entrant", controller="A", zone="battlefield", phased_out=False, attacking=None)
        state = SimpleNamespace(phase="combat", active_player="A", combat=combat, cards={card.object_id: card})
        host = SimpleNamespace(state=state, active_seats=("A", "B", "C"),
            _effective_card_data=lambda _card: {"type_line": "Creature — Soldier"},
            _type_parts=lambda _line: ({"creature"}, {"soldier"}, set()),
            _attack_target_details=lambda _actor, ref: {"target": ref, "kind": "planeswalker",
                "defending_player": "C", "logical_object_id": "defender@2"})
        commit_attacking_entry(host, card, AttackingEntryRecipient("defender", "defender@1"))
        self.assertNotIn(card.object_id, combat.attackers)
        commit_attacking_entry(host, card, AttackingEntryRecipient("defender", "defender@2"))
        self.assertEqual("defender", combat.attackers[card.object_id])
        self.assertEqual((existing,), combat.attack_bands)


class AttackingCardEntryNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = hand.HandEntryFollowupNativeTests.session
    add = hand.HandEntryFollowupNativeTests.add
    checkpoint = hand.HandEntryFollowupNativeTests.checkpoint
    resolve = hand.HandEntryFollowupNativeTests.resolve
    replay = hand.HandEntryFollowupNativeTests.replay
    prepare = hand.HandEntryFollowupNativeTests.prepare
    assert_closed = hand.HandEntryFollowupNativeTests.assert_closed
    native_until = hand.HandEntryFollowupNativeTests.native_until
    until = hand.HandEntryFollowupNativeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "attacking-entry.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/attacking-card-entry-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attacking entry witness", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def attack_player(self, session, card, defender):
        self.until(session, lambda game: game.state.pending_decision.kind == "combat.attackers")
        result = session.act("pilot:A", {"action_id": "attack", "attackers": {card.ref: defender}})
        self.assertTrue(result.ok, result.summary)

    def test_captain_keeps_its_hand_private_and_enters_attacking_without_declaring_again(self):
        session = self.session(508401)
        engine = session.engine
        captain = self.add(engine, "Preeminent Captain")
        captain.acquired_control_turn_count = -1
        soldier = self.add(engine, "Vicious Conquistador", zone="hand", ref="private-entering-soldier")
        human = self.add(engine, "Generic Bound Body", zone="hand", ref="private-nonsoldier")
        self.assert_closed(session, captain)
        self.assert_closed(session, soldier)
        self.prepare(session)
        self.checkpoint(session)
        self.attack_player(session, captain, "B")
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        offers = session.packet("pilot:A", full=True)["decision"]["ctx"]["objects"]
        self.assertEqual({soldier.ref}, {row["id"] for row in offers})
        self.assertNotIn(human.ref, str(offers))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D", {"action_id": "choose", "card": soldier.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "card": soldier.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", session.state.cards[soldier.object_id].zone)
        self.assertNotIn(soldier.ref, json.dumps(session.packet("pilot:D", full=True)))
        session = self.replay(session, load=True)

        observed = []
        original = type(session.engine)._dispatch_zone_change_events

        def observe_move(host, card, **kwargs):
            if card.object_id == soldier.object_id and kwargs["destination"] == "battlefield":
                observed.append(host.state.combat.attackers.get(card.object_id))
            return original(host, card, **kwargs)

        with patch.object(type(session.engine), "_dispatch_zone_change_events", new=observe_move):
            result = session.act("pilot:A", {"action_id": "choose", "choice": "C"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(["C"], observed)
        self.assertEqual("B", session.state.combat.attackers[captain.object_id])
        self.assertEqual("C", session.state.combat.attackers[soldier.object_id])
        self.assertTrue(session.state.cards[soldier.object_id].tapped)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.players[seat].life == 40 for seat in "BCD"))
        self.replay(session, load=True)

    def test_yore_tiller_resumes_entry_replacement_before_choosing_a_planeswalker(self):
        session = self.session(508402)
        engine = session.engine
        source = self.add(engine, "Yore-Tiller Nephilim")
        source.acquired_control_turn_count = -1
        clone = self.add(engine, "Clone", zone="graveyard")
        other_grave = self.add(engine, "Generic Bound Body", seat="B", zone="graveyard", ref="foreign-graveyard")
        copy_source = self.add(engine, "Generic Bound Body", seat="D", ref="copy-source")
        defender = self.add(engine, "Jace Beleren", seat="C")
        defender.counters["loyalty"] = 3
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.attack_player(session, source, "B")
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": "choose", "targets": [other_grave.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "targets": [clone.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", self.resolve(session).kind)
        self.assertEqual("graveyard", session.state.cards[clone.object_id].zone)
        session = self.replay(session, load=True)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        copy_choice = next(option["id"] for option in options if copy_source.ref in option["id"])
        result = session.act("pilot:A", {"action_id": "choose", "replacement": copy_choice})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", session.state.pending_decision.kind)
        self.assertEqual("graveyard", session.state.cards[clone.object_id].zone)
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "choice": defender.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[clone.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertTrue(current.tapped)
        self.assertEqual("Generic Bound Body", session.engine.display_name(current.object_id))
        self.assertEqual({"target": defender.ref, "kind": "planeswalker", "defending_player": "C",
            "logical_object_id": defender.logical_object_id}, session.state.combat.attack_target_context[current.object_id])
        self.replay(session, load=True)
