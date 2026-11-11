"""CR 608.2h: a sealed recipient and the ability controller's live count."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.event_player_result_values import event_result_value_capabilities
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import PUBLIC_QUERY_AMOUNT_CAPABILITY
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_compiled_reviewed_trigger_consumers as native


TRIGGER = (
    "Whenever this creature deals combat damage to a player, "
    "that player loses life equal to the number of creature cards in your graveyard."
)


class EventPlayerPublicCountCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, registry=None):
        return compile_oracle_card(replace(query_record(text), type_line="Creature — Human Rogue"),
            capability_registry=registry or self.registry, capability_profile="commander_review")

    def test_result_recipient_does_not_rebind_the_quantity_controller(self):
        for instruction, operation, field in (
            ("loses life equal to", "lose_life", "amount"),
            ("gains life equal to", "life", "delta"),
            ("draws cards equal to", "draw", "count"),
        ):
            with self.subTest(instruction=instruction):
                ir = self.compile(TRIGGER.replace("loses life equal to", instruction))
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertIsNone(node.target_schema)
                effect, = node.effects
                self.assertEqual((operation, "$context.target"), (effect["op"], effect["player"]))
                amount = effect[field]
                self.assertEqual(("public_query_effect_amount", 1, 1),
                    (amount["kind"], amount["schema_version"], amount["coefficient"]))
                self.assertEqual("controller_zone", amount["quantity"]["scope"])
                self.assertEqual(["graveyard"], list(amount["quantity"]["query"]["zones"]))
                self.assertEqual(["creature"], list(amount["quantity"]["query"]["types_all"]))
                self.assertIn("resolution.event_player.fixed_result", node.capability_dependencies)
                self.assertIn(PUBLIC_QUERY_AMOUNT_CAPABILITY, node.capability_dependencies)

    def test_unbound_recipients_hidden_face_filters_and_open_counts_remain_residual(self):
        for text in (
            TRIGGER.replace("to a player", "to a planeswalker"),
            TRIGGER.replace("your graveyard", "their graveyard"),
            TRIGGER.replace("your graveyard", "your hand"),
            TRIGGER.replace("creature cards in your graveyard", "secret cards named Fixture"),
            TRIGGER.replace("your graveyard.", "your graveyard that were discarded this turn."),
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_amount_codec_preserves_independent_positive_results_and_received_damage_boundary(self):
        ir = self.compile(TRIGGER)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        amount = deepcopy(ir.faces[0].nodes[0].effects[0]["amount"])
        self.assertEqual((PUBLIC_QUERY_AMOUNT_CAPABILITY,),
            event_result_value_capabilities("lose_life", "amount", amount))
        for changes in (
            {"coefficient": 0}, {"coefficient": -1}, {"coefficient": True},
            {"schema_version": 2, "binding_id": "fixture:unbound-declaration"},
            {"quantity": {"schema_version": 1, "scope": "$context.target"}},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(event_result_value_capabilities("lose_life", "amount", {**amount, **changes}))
        self.assertIsNone(event_result_value_capabilities("draw", "amount", amount))
        self.assertIsNone(event_result_value_capabilities("damage", "amount", amount, committed_damage=True))

    def test_withdrawing_either_composed_owner_prevents_exact_admission(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        for owner in ("resolution.event_player.fixed_result", PUBLIC_QUERY_AMOUNT_CAPABILITY):
            with self.subTest(owner=owner):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == owner).update(
                    status="blocked", blockers=["event-player quantity owner withdrawn"])
                ir = self.compile(TRIGGER, CapabilityRegistry(changed))
                self.assertNotEqual("exact", ir.status)


class EventPlayerPublicCountRuntimeTests(unittest.TestCase):
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
    advance = native.CompiledReviewedTriggerConsumerTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "event-player-public-count.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/source-event-player-damage-cards.json",
            ROOT / "tests/fixtures/event-player-public-count-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Event-player public counts", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def attack_fixture(self, seed, *, planeswalker=False):
        session = self.session(seed)
        source = self.add(session.engine, "Graveblade Marauder", zone="hand")
        fervor = self.add(session.engine, "Fervor", zone="hand")
        crypt = self.add(session.engine, "Tormod's Crypt", zone="hand")
        removal = self.add(session.engine, "Murder", zone="hand")
        swamps = [self.add(session.engine, "Swamp", ref=f"quantity-swamp-{i}") for i in range(3)]
        for seat, count in (("A", 2), ("B", 5), ("C", 1)):
            for i in range(count):
                self.add(session.engine, "Generic Bound Body", seat=seat, zone="graveyard", ref=f"quantity-grave-{seat}-{i}")
        self.add(session.engine, "Generic Bound Plains", zone="graveyard")
        self.add(session.engine, "Generic Bound Growth", zone="graveyard")
        self.add(session.engine, "Leyline of Sanctity", seat="B")
        walker = self.add(session.engine, "Jace Beleren", seat="B") if planeswalker else None
        if walker is not None:
            walker.counters["loyalty"] = 3
        self.ready(session, {"C": 10, "B": 2, "R": 2})
        for card in (fervor, source, crypt):
            self.cast(session, card)
            self.assertIsNone(self.settle(session))
        self.advance(session, lambda s: getattr(s.state.pending_decision, "kind", None) == "combat.attackers")
        command = {"action_id": "attack", "atk": {source.ref: walker.ref if walker else "B"}}
        self.reject(session, command, seat="D")
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        return session, source, crypt, removal, swamps, walker

    def test_resolution_counts_its_departed_source_or_zero_after_a_legal_graveyard_exile(self):
        for index, interrupt in enumerate(("source_dies", "graveyard_emptied")):
            with self.subTest(interrupt=interrupt):
                session, source, crypt, removal, swamps, _ = self.attack_fixture(973_000 + index)
                self.advance(session, lambda s: any(item.source_object_id == source.object_id for item in s.state.stack))
                self.assertEqual([40, 39, 40, 40], [session.state.players[seat].life for seat in "ABCD"])
                self.assertEqual(1, len(session.state.stack))
                self.assertFalse(session.state.stack[-1].targets)
                session = self.replay(session, load=True)
                self.priority(session, "A")
                if interrupt == "source_dies":
                    for swamp in swamps:
                        row = next(row for row in self.actions(session) if row["id"].startswith("activate:" + swamp.ref + ":"))
                        result = session.act("pilot:A", {"action_id": row["id"], "pay": "auto"})
                        self.assertTrue(result.ok, result.summary)
                    self.cast(session, removal, [source.ref])
                else:
                    row = next(row for row in self.actions(session) if row["id"].startswith("activate:" + crypt.ref + ":"))
                    command = {"action_id": row["id"], "targets": ["A"], "pay": "auto"}
                    self.reject(session, command, seat="D")
                    result = session.act("pilot:A", command)
                    self.assertTrue(result.ok, result.summary)
                session = self.replay(session, load=True)
                self.assertIsNone(self.settle(session))
                self.assertEqual("priority", session.state.pending_decision.kind)
                self.assertEqual([40, 36 if interrupt == "source_dies" else 39, 40, 40],
                    [session.state.players[seat].life for seat in "ABCD"])
                if interrupt == "source_dies":
                    self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
                else:
                    self.assertEqual([], session.state.players["A"].zones["graveyard"])
                self.replay(session, load=True)

    def test_planeswalker_combat_damage_does_not_trigger_player_life_loss(self):
        session, source, _, _, _, walker = self.attack_fixture(973_010, planeswalker=True)
        self.advance(session, lambda s: s.state.phase == "postcombat_main")
        self.assertEqual([40] * 4, [session.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(2, session.state.cards[walker.object_id].counters["loyalty"])
        self.assertFalse(any(item.source_object_id == source.object_id for item in session.state.stack))
        self.replay(session, load=True)
