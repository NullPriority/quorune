from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
import test_bound_effect_programs as bound
import test_ordinary_crew as crew_actions
from test_qualified_zone_event_queries import query_record
import test_ring_native_actions as turns
import test_self_public_zone_casting as actions
from quorune.abilities import ActivatedAbility
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.saddle import (
    SADDLE_CAPABILITY, SADDLED_ATTACK_CAPABILITY, SADDLE_OPERATION,
    is_saddled, saddle_activation_runtime_dependencies, saddled_attack_condition,
    saddled_attack_runtime_dependencies,
)
from quorune.semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database


ATTACK = "Whenever this creature attacks while saddled, it gets +1/+2 and gains flying until end of turn."


class SaddleCompilerTests(unittest.TestCase):
    def compile(self, text, *, keywords=(), registry=None):
        record = replace(query_record(text), type_line="Creature — Bird Mount", keywords=keywords)
        return compile_oracle_card(record, capability_registry=registry or load_default_capability_registry(),
            capability_profile="commander_review")

    def test_fixed_saddle_compiles_closed_catalog_and_activation(self):
        text = "Saddle 3"
        ir = self.compile(text, keywords=("Saddle",))
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((text, 0, len(text)), (node.text, node.span.start, node.span.end))
        self.assertEqual(3, node.cost["saddle"])
        self.assertEqual((SADDLE_CAPABILITY,), node.capability_dependencies)
        descriptor, = node.handlers
        handler = ActivatedAbilityCatalogHandler()
        ability = handler.validate(descriptor)
        self.assertTrue(ability.sorcery_speed)
        self.assertEqual(3, ability.saddle_threshold)
        self.assertEqual(ability, ActivatedAbility.from_dict(ability.to_dict()))
        with self.assertRaises(SemanticNodeError):
            handler.validate({key: value for key, value in descriptor.items()
                if key != "requires_complete_card_program"})
        for change in ({"saddle_threshold": True}, {"sorcery_speed": False}, {"tap_source": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ActivatedAbility.from_dict({**ability.to_dict(), **change})
        program = SimpleNamespace(event="activate", active_zone="battlefield", target_schema=None,
            handlers=node.handlers, effects=node.effects, cost_schema=node.cost)
        self.assertEqual((SADDLE_CAPABILITY,), saddle_activation_runtime_dependencies(program))
        program.effects = ({"op": SADDLE_OPERATION, "card": "$target"},)
        with self.assertRaises(ValueError):
            saddle_activation_runtime_dependencies(program)
        program.handlers = ()
        program.effects = ({"op": "sequence", "effects": list(program.effects)},)
        with self.assertRaises(ValueError):
            saddle_activation_runtime_dependencies(program)
        self.assertNotEqual("exact", self.compile("Saddle X", keywords=("Saddle",)).status)

    def test_attacks_while_saddled_binds_declared_self(self):
        ir = self.compile(ATTACK)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("creature.attacks", node.event)
        self.assertEqual(saddled_attack_condition(), node.event_condition)
        self.assertIn(SADDLED_ATTACK_CAPABILITY, node.capability_dependencies)
        self.assertEqual((SADDLED_ATTACK_CAPABILITY,), saddled_attack_runtime_dependencies(node))
        with self.assertRaises(ValueError):
            saddled_attack_runtime_dependencies(replace(node, event_condition=node.event_condition["all"][1]))
        self.assertNotEqual("exact", self.compile(ATTACK.replace("this creature", "a creature you control")).status)

    def test_saddle_owner_withdrawal_rejects_attack(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        owner = next(row for row in raw["capabilities"] if row["id"] == SADDLE_CAPABILITY)
        owner.update(status="blocked", blockers=["withdrawn for the dependency witness"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", self.compile(ATTACK, registry=registry).status)
        self.assertEqual("exact", self.compile("Flying", registry=registry).status)

    def test_saddle_and_attack_lowering_omissions_are_killed(self):
        with patch("quorune.compiler.keyword_nodes.ordinary_saddle_keyword_node", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_fixed_saddle_compiles_closed_catalog_and_activation()
        with patch("quorune.compiler.fixed_public_event_trigger_bindings._public_attack_spec", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_attacks_while_saddled_binds_declared_self()


class SaddleNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = bound.BoundEffectProgramRuntimeTests.session
    add = bound.BoundEffectProgramRuntimeTests.add
    checkpoint = bound.BoundEffectProgramRuntimeTests.checkpoint
    resolve = bound.BoundEffectProgramRuntimeTests.resolve
    replay = bound.BoundEffectProgramRuntimeTests.replay
    prepare = actions.SelfPublicZoneRuntimeTests.prepare
    cast = actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = actions.SelfPublicZoneRuntimeTests.assert_closed
    until = turns.RingNativeActionTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "saddle.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/crew-power-bonus-cards.json",
            ROOT / "tests/fixtures/ordinary-saddle-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Saddle", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def offers(self, session, source):
        return [row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            if row["id"].startswith(f"activate:{source.ref}:")]

    def fixture(self, seed, *, name="Brightfield Glider"):
        session = self.session(seed)
        engine = session.engine
        mount = self.add(engine, name)
        mount.acquired_control_turn_count = -1
        pilot = self.add(engine, "Hotshot Mechanic")
        helpers = [pilot, *(self.add(engine, "Generic Bound Body", ref=f"saddle-helper-{index}") for index in range(3))]
        self.assert_closed(session, mount)
        return session, mount, helpers

    def test_actual_saddle_pays_signed_power_and_seals_attack_before_cleanup(self):
        session, mount, helpers = self.fixture(70217101)
        engine = session.engine
        unsaddled = self.add(engine, "Dracosaur Auxiliary")
        unsaddled.acquired_control_turn_count = -1
        negative = crew_actions.OrdinaryCrewRuntimeTests.creature(engine, "A", "Negative saddle helper", -2)
        self.assert_closed(session, unsaddled)
        self.prepare(session)
        self.checkpoint(session)
        action, = self.offers(session, mount)
        self.assertEqual({"k": "saddle", "z": "battlefield", "minimum": 1, "minimum_total_power": 3,
            "legal_refs": [card.ref for card in (*helpers, unsaddled, negative)]}, action["cost_summary"]["choose_cost"][0])
        chosen = [card.ref for card in helpers[:2]]
        before = authoritative_state_hash(session.state)
        for seat, refs in (("D", chosen), ("A", chosen[:1]), ("A", [chosen[0], chosen[0]]),
                           ("A", [mount.ref, chosen[0]]), ("A", [*chosen, negative.ref])):
            rejected = session.act(f"pilot:{seat}", {"action_id": action["id"], "cost_cards": refs})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))

        accepted = session.act("pilot:A", {"action_id": action["id"], "cost_cards": chosen})
        self.assertTrue(accepted.ok, accepted.summary)
        cost = session.state.stack[-1].context["saddle"]
        self.assertEqual((3, 4, chosen), (cost["threshold"], cost["total_power"],
            [row["object_ref"] for row in cost["saddled_by"]]))
        self.assertTrue(all(engine.state.cards[card.object_id].tapped for card in helpers[:2]))
        self.assertFalse(is_saddled(engine.state.cards[mount.object_id]))
        self.assertFalse(self.offers(session, mount))
        session = self.replay(session, load=True)
        engine = session.engine
        self.assertIsNone(self.resolve(session))
        self.assertTrue(is_saddled(engine.state.cards[mount.object_id]))
        self.assertIn('"saddled": true', json.dumps(session.packet("pilot:D", full=True)))
        self.assertEqual(1, len(self.offers(session, mount)))  # Another payment is legal.
        self.until(session, lambda game: game.state.pending_decision.kind == "combat.attackers")
        result = session.act("pilot:A", {"action_id": "attack", "attackers": {mount.ref: "B", unsaddled.ref: "C"}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        glider = engine.state.cards[mount.object_id]
        printed = self.db.lookup("Brightfield Glider", fuzzy=False)
        self.assertEqual((int(printed.power) + 1, int(printed.toughness) + 2),
            (engine._numeric_stat(glider.object_id, "power"), engine._numeric_stat(glider.object_id, "toughness")))
        self.assertIn("flying", engine._combat_keywords(glider))
        self.until(session, lambda game: game.state.active_player == "B" and game.state.step == "upkeep")
        self.assertFalse(is_saddled(engine.state.cards[mount.object_id]))
        self.assertEqual(int(printed.power), engine._numeric_stat(glider.object_id, "power"))
        self.replay(session, load=True)

    def test_saddle_result_leaves_the_returned_incarnation_unsaddled(self):
        session, mount, helpers = self.fixture(70217102)
        blink = self.add(session.engine, "Cloudshift", zone="hand")
        self.assert_closed(session, blink)
        self.prepare(session, mana={"W": 1})
        self.checkpoint(session)
        action, = self.offers(session, mount)
        result = session.act("pilot:A", {"action_id": action["id"], "cost_cards": [card.ref for card in helpers[:2]]})
        self.assertTrue(result.ok, result.summary)
        original = mount.logical_object_id
        self.cast(session, "A", blink, [mount.ref])
        self.assertIsNone(self.resolve(session))
        returned = session.state.cards[mount.object_id]
        self.assertEqual("battlefield", returned.zone)
        self.assertNotEqual(original, returned.logical_object_id)
        self.assertFalse(is_saddled(returned))
        self.assertTrue(all(session.state.cards[card.object_id].tapped for card in helpers[:2]))
        self.replay(session, load=True)

    def test_saddled_attack_trigger_uses_declared_fact_after_source_returns(self):
        session, mount, helpers = self.fixture(70217103, name="Dracosaur Auxiliary")
        blink = self.add(session.engine, "Cloudshift", zone="hand")
        self.add(session.engine, "Generic Bound Plains", ref="combat-blink-mana")
        self.prepare(session)
        self.checkpoint(session)
        action, = self.offers(session, mount)
        result = session.act("pilot:A", {"action_id": action["id"], "cost_cards": [card.ref for card in helpers[:2]]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.until(session, lambda game: game.state.pending_decision.kind == "combat.attackers")
        result = session.act("pilot:A", {"action_id": "attack", "attackers": {mount.ref: "B"}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        self.assertIsNone(session.packet("pilot:D", full=True)["decision"])
        result = session.act("pilot:A", {"action_id": "choose", "targets": ["D"]})
        self.assertTrue(result.ok, result.summary)
        original = mount.logical_object_id
        life = session.state.players["D"].life
        self.cast(session, "A", blink, [mount.ref])
        self.assertIsNone(self.resolve(session))
        returned = session.state.cards[mount.object_id]
        self.assertNotEqual(original, returned.logical_object_id)
        self.assertFalse(is_saddled(returned))
        self.assertEqual(life - 2, session.state.players["D"].life)
        self.replay(session, load=True)
