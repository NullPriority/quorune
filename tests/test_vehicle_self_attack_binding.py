from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_compiled_reviewed_trigger_consumers as native


class VehicleSelfAttackCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def test_vehicle_source_binding_keeps_its_identity_predicate_and_damage_body(self):
        for alone in ("", " alone"):
            text = f"Whenever this Vehicle attacks{alone}, it deals 1 damage to any target."
            binding = fixed_counter_trigger_binding(text, card_name="Fixture")
            source = {"field": "card", "op": "eq", "value": "$source.ref"}
            if alone:
                self.assertEqual({"all": [source, {"field": "attacking_alone", "op": "truthy", "value": True}]}, binding.event_condition)
            else:
                self.assertEqual(source, binding.event_condition)
            ir = compile_oracle_card(replace(query_record(text), type_line="Artifact — Vehicle"),
                capability_registry=self.registry, capability_profile="commander_review")
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(({"op": "damage", "source": "$source", "target": "$target.0", "amount": 1},), tuple(node.effects))
            self.assertEqual(1, node.target_schema["count"])
            self.assertEqual(node.text, text[node.span.start:node.span.end])

    def test_other_creature_and_unrepresented_riders_do_not_borrow_the_source(self):
        for text in (
            "Whenever a creature attacks, it deals 1 damage to any target.",
            "Whenever this Vehicle attacks, it deals 1 damage to any target chosen at random.",
            "Whenever this Vehicle attacks and becomes blocked, it deals 1 damage to any target.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(query_record(text), type_line="Artifact — Vehicle"),
                    capability_registry=self.registry, capability_profile="commander_review")
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)


class VehicleSelfAttackRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.CompiledReviewedTriggerConsumerTests.session
    add = native.CompiledReviewedTriggerConsumerTests.add
    ready = native.CompiledReviewedTriggerConsumerTests.ready
    prepare = native.CompiledReviewedTriggerConsumerTests.prepare
    checkpoint = native.CompiledReviewedTriggerConsumerTests.checkpoint
    priority = native.CompiledReviewedTriggerConsumerTests.priority
    actions = native.CompiledReviewedTriggerConsumerTests.actions
    binding = native.CompiledReviewedTriggerConsumerTests.binding
    reject = native.CompiledReviewedTriggerConsumerTests.reject
    settle = native.CompiledReviewedTriggerConsumerTests.settle
    replay = native.CompiledReviewedTriggerConsumerTests.replay
    cast = native.CompiledReviewedTriggerConsumerTests.cast
    choose = native.CompiledReviewedTriggerConsumerTests.choose
    advance = native.CompiledReviewedTriggerConsumerTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "vehicle-self-attack.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/vehicle-self-attack-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Vehicle self attack", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def crew(self, session, source, pilots):
        self.priority(session, "A")
        row, = [row for row in self.actions(session)
                if row["id"].startswith("activate:" + source.ref + ":")
                and any(cost["k"] == "crew" for cost in row.get("cost_summary", {}).get("choose_cost", []))]
        command = {"action_id": row["id"], "cost_cards": [card.ref for card in pilots]}
        self.reject(session, command, seat="D")
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.settle(session))

    def test_only_the_crewed_source_triggers_and_departure_retains_its_lifelink(self):
        for source_attacks in (False, True):
            with self.subTest(source_attacks=source_attacks):
                session = self.session(969_000 + int(source_attacks))
                source = self.add(session.engine, "Ballista Charger", zone="hand")
                other = self.add(session.engine, "Consulate Dreadnought", zone="hand")
                fervor = self.add(session.engine, "Fervor", zone="hand")
                lifelink = self.add(session.engine, "Lifelink", zone="hand")
                pilots = [self.add(session.engine, "Grizzly Bears", ref=f"crew-pilot-{index}") for index in range(5)]
                foreign = self.add(session.engine, "Grizzly Bears", seat="B", ref="foreign-pilot")
                island = self.add(session.engine, "Island", seat="B")
                bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
                self.ready(session, {"C": 30, "R": 5, "W": 5})
                for card in (fervor, source, other):
                    self.cast(session, card)
                    self.assertIsNone(self.settle(session))
                self.crew(session, other, pilots[:3])
                if source_attacks:
                    row, = [row for row in self.actions(session) if row["id"].startswith("activate:" + source.ref + ":")]
                    for refs in ([pilots[3].ref], [foreign.ref, pilots[3].ref]):
                        self.reject(session, {"action_id": row["id"], "cost_cards": refs})
                    self.crew(session, source, pilots[3:])
                    self.cast(session, lifelink, [source.ref])
                    self.assertIsNone(self.settle(session))
                self.advance(session, lambda s: getattr(s.state.pending_decision, "kind", None) == "combat.attackers")
                if not source_attacks:
                    self.reject(session, {"action_id": "attack", "atk": {source.ref: "B"}})
                attackers = {other.ref: "B", **({source.ref: "B"} if source_attacks else {})}
                command = {"action_id": "attack", "atk": attackers}
                self.reject(session, command, seat="D")
                result = session.act("pilot:A", command)
                self.assertTrue(result.ok, result.summary)
                decision = self.settle(session)
                if source_attacks:
                    self.assertEqual("semantic.target", decision.kind)
                    session = self.replay(session, load=True)
                    self.choose(session, targets=["B"])
                    self.priority(session, "B")
                    self.assertEqual(1, len(session.state.stack))
                    row = next(row for row in self.actions(session, "B") if row["id"].startswith("activate:" + island.ref + ":"))
                    result = session.act("pilot:B", {"action_id": row["id"], "pay": "auto"})
                    self.assertTrue(result.ok, result.summary)
                    self.cast(session, bounce, [source.ref], seat="B")
                    self.assertIsNone(self.settle(session))
                    self.assertEqual("priority", session.state.pending_decision.kind)
                    self.assertEqual([41, 39, 40, 40], [session.state.players[seat].life for seat in "ABCD"])
                    self.assertEqual("hand", session.state.cards[source.object_id].zone)
                    self.assertEqual("graveyard", session.state.cards[lifelink.object_id].zone)
                else:
                    self.assertIsNone(decision)
                    self.assertEqual("priority", session.state.pending_decision.kind)
                    self.assertFalse(session.state.stack)
                    self.assertEqual([40, 40, 40, 40], [session.state.players[seat].life for seat in "ABCD"])
                self.replay(session, load=True)
