from __future__ import annotations

from contextvars import ContextVar
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import DB_PATH, keep_all, packet_without_decision_capability
from test_fixed_mana_abilities import record
from quorune import CardDatabase, CommanderSession, GameConfig
from quorune.card_programs.adapters import compile_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.cost_payments import cost_is_affordable
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.fixed_mana_abilities import HAND_EXILE_MANA_CAPABILITY, HAND_EXILE_MANA_HANDLER_ID
from quorune.mana_source_discovery import capture_hand_mana_window
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.mana_abilities import HandExileActivatedManaHandler, FixedActivatedManaAbilityHandler


GUIDES = {"Elvish Spirit Guide": "G", "Simian Spirit Guide": "R"}
COMMANDER = "Ruric Thar, the Unbowed"
ONE_MANA_SPELL = "Llanowar Elves"


class HandExileManaCompilerTests(unittest.TestCase):
    def test_complete_hand_source_syntax_and_version_boundary(self):
        registry = load_default_capability_registry()
        for source, color in (("this creature", "G"), ("this card", "R")):
            with self.subTest(source=source):
                material = f"Exile {source} from your hand: Add {{{color}}}."
                ir = compile_oracle_card(record(material), capability_registry=registry)
                self.assertEqual((), ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual("hand", node.active_zone)
                descriptor, = node.handlers
                self.assertEqual(HAND_EXILE_MANA_HANDLER_ID, descriptor["handler_id"])
                ability = HandExileActivatedManaHandler().validate(descriptor).to_activated_ability()
                self.assertEqual(("hand",), ability.zones)
                self.assertTrue(ability.exile_source)
                self.assertEqual(1, ability.fixed_mana_outputs[0].bundle[color])
                with self.assertRaises(SemanticNodeError):
                    FixedActivatedManaAbilityHandler().validate({
                        **descriptor, "handler_id": FixedActivatedManaAbilityHandler().handler_id,
                        "schema_version": 1,
                    })
                with self.assertRaises(SemanticNodeError):
                    HandExileActivatedManaHandler().validate({
                        **descriptor, "ability": {**descriptor["ability"], "exile_source_from_hand": False},
                    })

    def test_hand_source_dependencies_fail_closed(self):
        for key in (HAND_EXILE_MANA_CAPABILITY, "mana.activated.fixed_output"):
            with self.subTest(key=key):
                data = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_text(encoding="utf-8"))
                capability = next(row for row in data["capabilities"] if row["id"] == key)
                capability.update(status="blocked", blockers=["withdrawn hand-exile mana authority"])
                registry = CapabilityRegistry(data)
                registry.mark_evidence_verified("0" * 64)
                ir = compile_oracle_card(record("Exile this card from your hand: Add {R}."), capability_registry=registry)
                self.assertTrue(ir.material_residuals)
                self.assertNotEqual("exact", ir.status)

    def test_nearby_exile_printings_stay_outside_fixed_hand_mana(self):
        for material in (
            "Exile another creature card from your hand: Add {G}.",
            "Exile this creature from your hand: Add {G}. Draw a card.",
            "Exile this card from your graveyard: Add {G}.",
            "{1}, Exile this creature from your hand: Add {G}.",
        ):
            with self.subTest(material=material):
                ir = compile_oracle_card(record(material), capability_registry=load_default_capability_registry())
                self.assertTrue(ir.material_residuals)
                self.assertNotIn(HAND_EXILE_MANA_HANDLER_ID, {
                    d["handler_id"] for face in ir.faces for node in face.nodes for d in node.handlers
                })


class HandExileManaNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed, *, active="A"):
        common = [DeckEntry(COMMANDER, board="commander"), DeckEntry(ONE_MANA_SPELL),
                  DeckEntry("Bonesplitter"), DeckEntry("Sol Ring"), DeckEntry("Forest", 46), DeckEntry("Mountain", 49)]
        deck_a = DeckDefinition("Green guide", [*common, DeckEntry("Elvish Spirit Guide")], [COMMANDER])
        deck_b = DeckDefinition("Red guide", [*common, DeckEntry("Simian Spirit Guide")], [COMMANDER])
        session = CommanderSession.create(
            self.db, {"A": deck_a, "B": deck_b, "C": deck_a, "D": deck_b},
            first_player="A", seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=False),
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.active_player = active
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        for seat, name in (("A", "Elvish Spirit Guide"), ("B", "Simian Spirit Guide")):
            guide = self.card(session, seat, name)
            if guide.zone != "hand":
                engine.move_card(guide.object_id, "hand", log=False)
        engine._grant_priority(active)
        engine.pump()
        return session

    @staticmethod
    def card(session, seat, name):
        return next(card for card in session.state.cards.values() if card.owner == seat and card.printed_name == name)

    @staticmethod
    def offer(session, seat, *, source=None, card=None):
        actions = session.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(
            row for row in actions
            if (row["action"] == "activate" and row.get("source") == source)
            or (row["action"] == "cast" and row.get("card") == card)
        )

    def replay_after(self, session, action):
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        action()
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "hand-exile-mana"
            session.save(destination)
            replay = replay_record(destination, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_originals_bind_strictly_in_all_profiles(self):
        registry = load_default_capability_registry()
        for profile in ("traditional", "commander_duel", "commander_review"):
            for name in GUIDES:
                with self.subTest(profile=profile, name=name):
                    original = self.db.lookup(name, fuzzy=False)
                    self.assertEqual("legal", original.legalities["commander"])
                    ir = compile_oracle_card(original, capability_registry=registry, capability_profile=profile)
                    self.assertEqual((), ir.material_residuals)
                    program = compile_card_program(
                        self.db, original, capability_registry=registry, capability_profile=profile,
                        trust_level="trusted",
                    )
                    result = bind_card_program_runtime(program, capability_registry=registry, profile=profile)
                    self.assertTrue(result["strict_capability_ready"], result)

    def test_private_manual_activation_exiles_once_and_replays(self):
        for seat, name, color in (("A", "Elvish Spirit Guide", "G"), ("B", "Simian Spirit Guide", "R")):
            with self.subTest(seat=seat):
                session = self.session(60561, active=seat)
                guide = self.card(session, seat, name)
                foreign = self.card(session, "B" if seat == "A" else "A",
                                    "Simian Spirit Guide" if seat == "A" else "Elvish Spirit Guide")
                observer = "D" if seat == "A" else "C"
                before = json.dumps(packet_without_decision_capability(session.packet(f"pilot:{observer}", full=True)))
                self.assertNotIn(guide.object_id, before)
                self.assertNotIn(name, before)
                offer = self.offer(session, seat, source=guide.ref)

                def action():
                    original = authoritative_state_hash(session.state)
                    rejected = session.act(f"pilot:{seat}", {"a": "activate", "source": foreign.ref, "ability": "ab1"})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(original, authoritative_state_hash(session.state))
                    success = session.act(f"pilot:{seat}", {"action_id": offer["id"]})
                    self.assertTrue(success.ok, success.summary)
                    exiled = session.state.cards[guide.object_id]
                    self.assertEqual("exile", exiled.zone)
                    self.assertFalse(exiled.face_down)
                    self.assertEqual(1, session.state.players[seat].mana_pool[color])
                    self.assertEqual([], session.state.stack)
                    self.assertEqual(seat, session.state.priority_player)
                    after = json.dumps(packet_without_decision_capability(session.packet(f"pilot:{observer}", full=True)))
                    self.assertIn(name, after)
                    current = authoritative_state_hash(session.state)
                    stale = session.act(f"pilot:{seat}", {"action_id": offer["id"]})
                    self.assertFalse(stale.ok)
                    self.assertEqual(current, authoritative_state_hash(session.state))

                self.replay_after(session, action)

    def test_automatic_mana_uses_current_hand_without_using_the_cast_spell_and_replays(self):
        session = self.session(60562)
        guide = self.card(session, "A", "Elvish Spirit Guide")
        elf = self.card(session, "A", ONE_MANA_SPELL)
        engine = session.engine
        engine.move_card(elf.object_id, "hand", log=False)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority("A")
        engine.pump()
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row["action"] == "cast" and row.get("card") == guide.ref for row in actions))
        offer = self.offer(session, "A", card=elf.ref)

        def action():
            success = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
            self.assertTrue(success.ok, success.summary)
            self.assertEqual("exile", guide.zone)
            self.assertEqual("stack", elf.zone)
            self.assertEqual(0, session.state.players["A"].mana_pool["G"])
            item, = session.state.stack
            self.assertEqual(1, item.mana_spent_counts.count_for(elf, "G"))
            self.assertEqual(1, item.mana_spent_counts.total_for(elf))

        self.replay_after(session, action)

    def test_automatic_mana_funds_an_activated_ability_and_replays(self):
        session = self.session(60567)
        engine = session.engine
        source = self.card(session, "A", "Bonesplitter")
        target = self.card(session, "A", ONE_MANA_SPELL)
        guide = self.card(session, "A", "Elvish Spirit Guide")
        for card in (source, target):
            engine.move_card(card.object_id, "battlefield", log=False)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority("A")
        engine.pump()
        offer = self.offer(session, "A", source=source.ref)

        def action():
            accepted = session.act("pilot:A", {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"})
            self.assertTrue(accepted.ok, accepted.summary)
            self.assertEqual("exile", session.state.cards[guide.object_id].zone)
            self.assertEqual(0, session.state.players["A"].mana_pool["G"])
            item, = session.state.stack
            self.assertEqual([target.ref], item.targets)

        self.replay_after(session, action)

    def test_a_card_returned_as_cost_is_not_a_mana_source_in_that_window(self):
        session = self.session(60563)
        engine = session.engine
        guide = self.card(session, "A", "Elvish Spirit Guide")
        engine.move_card(guide.object_id, "battlefield", log=False)
        with capture_hand_mana_window(engine, "A"):
            engine.move_card(guide.object_id, "hand", log=False)
            self.assertFalse(any(row.ref == guide.ref for row in engine.available_mana_sources("A")))
            before = authoritative_state_hash(session.state)
            with self.assertRaisesRegex(GameRuleError, "no compiler-pinned mana mode"):
                engine._activate_mana_plan("A", [{"source": guide.ref, "tap_source": False, "bundle": {"G": 1}}])
            self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(any(row.ref == guide.ref for row in engine.available_mana_sources("A")))
        original_identity = guide.logical_object_id
        with capture_hand_mana_window(engine, "A"):
            engine.move_card(guide.object_id, "battlefield", log=False)
            engine.move_card(guide.object_id, "hand", log=False)
            self.assertNotEqual(original_identity, guide.logical_object_id)
            self.assertFalse(any(row.ref == guide.ref for row in engine.available_mana_sources("A")))

    def test_assist_invitation_does_not_inspect_private_hand_sources(self):
        session = self.session(60565)
        engine = session.engine
        source = self.card(session, "A", "Elvish Spirit Guide")
        self.assertTrue(cost_is_affordable(engine, "A", {"G": 1}))
        original_size = len(session.state.players["A"].zones["hand"])
        threshold = {"GENERIC": 1}
        original = cost_is_affordable(engine, "A", threshold, public_hand_mana_bound=True)
        replacement = next(
            card for card in session.state.cards.values()
            if card.owner == "A" and card.zone == "library" and card.printed_name == "Forest"
        )
        engine.move_card(source.object_id, "library", log=False)
        engine.move_card(replacement.object_id, "hand", log=False)
        self.assertEqual(original_size, len(session.state.players["A"].zones["hand"]))
        self.assertFalse(cost_is_affordable(engine, "A", {"G": 1}))
        self.assertFalse(original)
        self.assertFalse(cost_is_affordable(engine, "A", threshold, public_hand_mana_bound=True))

    def test_authenticated_assist_helper_can_spend_the_private_guide_and_replay(self):
        import test_assist_payment

        helper = test_assist_payment.AssistRuntimeTests()
        helper.db = self.db
        helper.registry = load_default_capability_registry()
        session = self.session(60566)
        engine = session.engine
        own_guide = self.card(session, "A", "Elvish Spirit Guide")
        engine.move_card(own_guide.object_id, "library", log=False)
        spell = helper.add(engine, "Bring Down", zone="hand")
        plains = helper.add(engine, "Plains")
        reef = helper.add(engine, "Shivan Reef", seat="B")
        target = helper.add(engine, "Ancient Brontodon", seat="C")
        guide = self.card(session, "B", "Simian Spirit Guide")
        helper.prepare(session, mana={"W": 1, "C": 1})
        offer = helper._cast_offer(session, spell)
        self.assertEqual(["B"], offer["cost_options"][0]["choice_schema"]["assist_helper"]["legal_values"])

        def action():
            helper._act(session, "A", offer["id"], targets=[target.ref], assist_helper="B", pay="auto")
            helper._frame(session, "helper_mana")
            self.assertNotIn("Simian Spirit Guide", json.dumps(session.packet("pilot:C", full=True)))
            helper._plan_mana(session, "B", guide, "R")
            helper._act(session, "B", "assist:finish-mana")
            helper._plan_mana(session, "A", plains, "W")
            helper._act(session, "A", "assist:finish-mana")
            helper._act(session, "B", "assist:contribute", assist_amount=1)
            helper._act(session, "A", "assist:pay")
            self.assertFalse(session.state.cards[reef.object_id].tapped)
            self.assertEqual("exile", session.state.cards[guide.object_id].zone)
            self.assertEqual(0, session.state.players["B"].mana_pool["R"])
            item, = session.state.stack
            self.assertEqual(1, item.mana_spent_counts.count_for(session.state.cards[spell.object_id], "R"))

        self.replay_after(session, action)

    def test_hand_plan_scope_mutation_is_detected(self):
        session = self.session(60564)
        engine = session.engine
        guide = self.card(session, "A", "Elvish Spirit Guide")
        engine.move_card(guide.object_id, "battlefield", log=False)
        with capture_hand_mana_window(engine, "A"):
            engine.move_card(guide.object_id, "hand", log=False)
            self.assertFalse(any(row.ref == guide.ref for row in engine.available_mana_sources("A")))
            with patch("quorune.mana_source_discovery._hand_mana_window", new=ContextVar("mutant", default=None)):
                with self.assertRaises(AssertionError):
                    self.assertFalse(any(row.ref == guide.ref for row in engine.available_mana_sources("A")))
