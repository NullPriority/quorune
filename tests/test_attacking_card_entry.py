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


    def test_attacking_graveyard_source_requires_its_closed_attack_and_payment(self):
        from quorune.rules.attacking_card_entry import SOURCE_GRAVEYARD_IDENTITY, validate_attacking_entry_choice

        registry = load_default_capability_registry()
        text = ("Whenever you attack with three or more creatures, you may pay {R}{R}. If you do, return "
            "this card from your graveyard to the battlefield tapped and attacking with a +1/+1 counter on it.")
        ir = compile_oracle_card(record(text, "Creature"), capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("graveyard", node.active_zone)
        self.assertEqual("creature.attacks", node.event)
        self.assertIsNone(node.target_schema)
        self.assertIn("counter.producer.effect_entry", node.capability_dependencies)
        payment, = node.effects
        choice, = payment["effects"]
        move = validate_attacking_entry_choice(choice, actor="$controller", compiled=True)
        self.assertEqual("$source", move["card"])
        self.assertEqual(SOURCE_GRAVEYARD_IDENTITY, move["source_identity"])
        self.assertEqual("+1/+1", move["entry_counter"])
        for changes in ({"source_identity": "$target.0"}, {"card": "$target.0"}, {"entry_counter": "finality"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_attacking_entry_choice({**choice, "attacking_card_entry": {**move, **changes}},
                    actor="$controller", compiled=True)
        for unsupported in (
            text.replace("from your graveyard", "from your hand"),
            text.replace("you may pay {R}{R}. If you do, return", "return"),
            text.replace("Whenever you attack with three or more creatures", "At the beginning of your upkeep"),
            text.replace("Whenever you attack with three or more creatures", "Whenever this creature attacks"),
            text.replace("+1/+1", "finality"),
        ):
            with self.subTest(unsupported=unsupported):
                self.assertNotEqual("exact", compile_oracle_card(record(unsupported, "Creature"),
                    capability_registry=registry).status)


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

    cast = hand.HandEntryFollowupNativeTests.cast

    def attack_player(self, session, card, defender):
        self.declare_attackers(session, {card.ref: defender})

    def declare_attackers(self, session, assignments):
        self.until(session, lambda game: game.state.pending_decision.kind == "combat.attackers")
        result = session.act("pilot:A", {"action_id": "attack", "attackers": assignments})
        self.assertTrue(result.ok, result.summary)

    def attack_with(self, session, *names):
        attackers = [self.add(session.engine, name, ref=f"declared-{index}") for index, name in enumerate(names)]
        for attacker in attackers:
            attacker.acquired_control_turn_count = -1
        return attackers

    def choose(self, session, option):
        result = session.act("pilot:A", {"action_id": "choose", "choice": option})
        self.assertTrue(result.ok, result.summary)

    def pay_for_return(self, session, *, pay):
        result = session.act("pilot:A", {"action_id": "choose", "pay": pay})
        self.assertTrue(result.ok, result.summary)

    def resolve_top(self, session):
        stack_id = session.state.stack[-1].stack_id
        self.until(session, lambda game: all(item.stack_id != stack_id for item in game.state.stack))

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


    def test_graveyard_phoenix_counts_declarations_and_enters_with_its_printed_counter(self):
        for seed, name, count, counters in (
            (508403, "Warcry Phoenix", 2, None),
            (508404, "Warcry Phoenix", 3, 0),
            (508405, "Phoenix Chick", 3, 1),
        ):
            with self.subTest(name=name, count=count):
                session = self.session(seed)
                source = self.add(session.engine, name, zone="graveyard")
                off_zone = self.add(session.engine, name, zone="hand", ref="off-zone-source")
                attackers = self.attack_with(session, *("Generic Bound Body",) * count)
                lands = [self.add(session.engine, "Mountain", ref=f"payment-land-{i}")
                    for i in range(2 if counters == 1 else 3)]
                self.assert_closed(session, source)
                self.prepare(session)
                self.checkpoint(session)
                self.declare_attackers(session, {attacker.ref: "B" for attacker in attackers})
                self.assertFalse(any(item.source_object_id == off_zone.object_id for item in session.state.stack))
                if counters is None:
                    self.assertFalse(session.state.stack)
                    self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
                    continue
                item, = session.state.stack
                self.assertEqual(source.object_id, item.source_object_id)
                self.assertEqual("graveyard", item.context["source_zone"])
                self.assertFalse(item.targets)
                self.assertEqual("semantic.choice", self.resolve(session).kind)
                payment = session.packet("pilot:A", full=True)["decision"]["ctx"]
                self.assertTrue(payment["payable"])
                self.assertEqual(2 if counters == 1 else 1, payment["cost"]["R"])
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:D", {"action_id": "choose", "pay": True}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                self.pay_for_return(session, pay=True)
                self.assertTrue(all(session.state.cards[land.object_id].tapped for land in lands))
                self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
                session = self.replay(session, load=True)
                observed = []
                original = type(session.engine)._dispatch_zone_change_events

                def observe_entry(host, card, **kwargs):
                    if card.object_id == source.object_id and kwargs["destination"] == "battlefield":
                        observed.append((host.state.combat.attackers.get(card.object_id),
                            card.counters.get("+1/+1", 0)))
                    return original(host, card, **kwargs)

                with patch.object(type(session.engine), "_dispatch_zone_change_events", new=observe_entry):
                    self.choose(session, "C")
                current = session.state.cards[source.object_id]
                self.assertEqual("battlefield", current.zone)
                self.assertTrue(current.tapped)
                self.assertEqual([("C", counters)], observed)
                self.assertEqual({attacker.object_id for attacker in attackers}, {
                    object_id for object_id, recipient in session.state.combat.attackers.items() if recipient == "B"})
                self.assertFalse(session.state.stack)
                self.replay(session, load=True)

    def test_interceptor_qualifies_across_defenders_once_and_can_decline(self):
        for qualified in (False, True):
            with self.subTest(qualified=qualified):
                session = self.session(508406 + qualified)
                source = self.add(session.engine, "Interceptor, Shadow's Hound", zone="graveyard")
                names = ("Generic Bound Commander", "Isamaru, Hound of Konda") if qualified else (
                    "Generic Bound Body", "Generic Bound Body")
                attackers = self.attack_with(session, *names)
                lands = [self.add(session.engine, "Swamp", ref=f"interceptor-land-{i}") for i in range(3)]
                self.assert_closed(session, source)
                self.prepare(session)
                self.checkpoint(session)
                self.declare_attackers(session, {card.ref: seat for card, seat in zip(attackers, "BC")})
                if qualified:
                    trigger, = session.state.stack
                    self.assertEqual(source.object_id, trigger.source_object_id)
                    self.assertEqual("semantic.choice", self.resolve(session).kind)
                    self.pay_for_return(session, pay=False)
                self.assertFalse(session.state.stack)
                self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
                self.assertTrue(all(not session.state.cards[land.object_id].tapped for land in lands))
                if qualified:
                    self.replay(session, load=True)

    def test_marshstalker_checks_threshold_at_declaration_and_before_payment(self):
        for count, disrupt in ((6, False), (7, True), (7, False)):
            with self.subTest(count=count, disrupt=disrupt):
                session = self.session(508410 + count + disrupt)
                source = self.add(session.engine, "Persistent Marshstalker", zone="graveyard")
                fillers = [self.add(session.engine, "Generic Bound Body", zone="graveyard", ref=f"grave-{i}")
                    for i in range(count - 1)]
                rat, = self.attack_with(session, "Typhoid Rats")
                lands = [self.add(session.engine, "Swamp", ref=f"threshold-land-{i}") for i in range(3)]
                exile = self.add(session.engine, "Cremate", seat="B", zone="hand")
                self.add(session.engine, "Swamp", seat="B", ref="response-black")
                self.assert_closed(session, source)
                self.prepare(session)
                self.checkpoint(session)
                self.attack_player(session, rat, "B")
                if count == 6:
                    self.assertFalse(session.state.stack)
                else:
                    trigger, = session.state.stack
                    self.assertEqual(source.object_id, trigger.source_object_id)
                    if disrupt:
                        self.cast(session, "B", exile, [fillers[0].ref])
                        self.assertIsNone(self.resolve(session))
                        self.assertEqual("exile", session.state.cards[fillers[0].object_id].zone)
                        self.assertIsNone(self.resolve(session))
                    else:
                        self.assertEqual("semantic.choice", self.resolve(session).kind)
                        self.pay_for_return(session, pay=True)
                        self.choose(session, "D")
                        self.assertEqual("D", session.state.combat.attackers[source.object_id])
                enters = count == 7 and not disrupt
                self.assertEqual("battlefield" if enters else "graveyard", session.state.cards[source.object_id].zone)
                self.assertEqual([enters] * len(lands), [session.state.cards[land.object_id].tapped for land in lands])
                if count == 7:
                    self.replay(session, load=True)

    def test_paid_graveyard_return_cannot_move_its_new_incarnation(self):
        session = self.session(508420)
        source = self.add(session.engine, "Warcry Phoenix", zone="graveyard")
        attackers = self.attack_with(session, *("Generic Bound Body",) * 3)
        for index in range(3):
            self.add(session.engine, "Mountain", ref=f"return-land-{index}")
        exile = self.add(session.engine, "Cremate", seat="B", zone="hand")
        restore = self.add(session.engine, "Pull from Eternity", seat="B", zone="hand")
        self.add(session.engine, "Swamp", seat="B")
        self.add(session.engine, "Generic Bound Plains", seat="B")
        self.assert_closed(session, source)
        initial_identity = source.logical_object_id
        self.prepare(session)
        self.checkpoint(session)
        self.declare_attackers(session, {attacker.ref: "B" for attacker in attackers})
        item, = session.state.stack
        self.assertEqual(initial_identity, item.context["source_logical_object_id"])
        self.cast(session, "B", exile, [source.ref])
        self.resolve_top(session)
        self.assertEqual("exile", session.state.cards[source.object_id].zone)
        self.cast(session, "B", restore, [source.ref])
        self.resolve_top(session)
        current = session.state.cards[source.object_id]
        self.assertEqual("graveyard", current.zone)
        self.assertNotEqual(initial_identity, current.logical_object_id)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.pay_for_return(session, pay=True)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertNotIn(source.object_id, session.state.combat.attackers)
        self.assertFalse(session.state.stack)
        self.replay(session, load=True)
