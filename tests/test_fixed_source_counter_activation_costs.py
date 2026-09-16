from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.abilities import ActivatedAbility, parse_activated_abilities
from quorune import abilities as abilities_module
from quorune.rules.activation_counter_cost import (
    SourceCounterRemovalCost,
    SourceCounterRemovalCostError,
)
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.activated_ability_catalog import (
    compile_activated_ability_catalog,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = (
    ROOT
    / "tests"
    / "fixtures"
    / "fixed-source-counter-activation-costs.json"
)
CAPABILITY = "activation.source_counter_removal.fixed"


def trusted_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(REGISTRY_PATH)
    registry.mark_evidence_verified("0" * 64)
    return registry


def fixture_card(name: str, oracle_text: str) -> CardRecord:
    return CardRecord(
        oracle_id=f"fixture:{name.casefold().replace(' ', '-')}",
        name=name,
        mana_cost="{2}",
        mana_value=2.0,
        type_line="Artifact Creature — Construct",
        oracle_text=oracle_text,
        power="2",
        toughness="2",
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=("G", "U"),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "source-counter-costs.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-counter-keyword-activations.json",
            FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class FixedSourceCounterCostCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        cls.capabilities = trusted_registry()

    def compile(self, record: CardRecord, *, registry=None):
        return compile_oracle_card(
            record,
            capability_registry=registry or self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_source_counter_cost_compiler_runtime_contract(self):
        examples = (
            (
                "{1}, {T}, Remove two charge counters from this artifact: "
                "Draw a card.",
                "charge",
                2,
                False,
            ),
            (
                "Remove a +1/+1 counter from this creature: "
                "This creature gets +1/+1 until end of turn.",
                "+1/+1",
                1,
                False,
            ),
            (
                "Remove three spore counters from Named Source: "
                "Create a 1/1 green Saproling creature token.",
                "spore",
                3,
                False,
            ),
            (
                "Remove a charge counter from Named Source: Add {G}.",
                "charge",
                1,
                True,
            ),
        )
        for index, (text, counter_name, amount, mana_ability) in enumerate(examples):
            with self.subTest(text=text):
                record = fixture_card("Named Source", text)
                ir = self.compile(record)
                node = ir.faces[0].nodes[0]
                self.assertTrue(node.exact, ir.to_dict())
                self.assertEqual(mana_ability, node.kind == "mana_ability")
                self.assertEqual(
                    {
                        "schema_version": 1,
                        "counter_name": counter_name,
                        "amount": amount,
                    },
                    node.cost["source_counter_removal_cost"],
                )
                self.assertIn(CAPABILITY, node.capability_dependencies)
                ability = compile_activated_ability_catalog(record)["front"][0]
                self.assertEqual(
                    SourceCounterRemovalCost(counter_name, amount),
                    ability.source_counter_removal_cost,
                )
                self.assertEqual(
                    ability,
                    ActivatedAbility.from_dict(ability.to_dict()),
                )

    def test_open_and_non_source_counter_costs_remain_residual(self):
        unsupported = (
            "Remove X charge counters from this artifact",
            "Remove all charge counters from this artifact",
            "Remove any number of charge counters from this artifact",
            "Remove a counter from this artifact",
            "Remove a counter of your choice from this artifact",
            "Remove a charge counter from target artifact",
            "Remove a charge counter from an artifact you control",
            "Remove a charge counter from each artifact you control",
            "Remove a charge counter from this card",
        )
        for index, cost in enumerate(unsupported):
            with self.subTest(cost=cost):
                record = fixture_card(
                    f"Unsupported Counter Cost {index}",
                    f"{cost}: Draw a card.",
                )
                ir = self.compile(record)
                node = ir.faces[0].nodes[0]
                self.assertFalse(node.exact)
                self.assertTrue(node.cost["uncompiled_costs"])
                self.assertIsNone(node.cost["source_counter_removal_cost"])

        multiple = self.compile(
            fixture_card(
                "Multiple Counter Costs",
                "Remove a charge counter from this artifact, Remove a luck "
                "counter from this artifact: Draw a card.",
            )
        ).faces[0].nodes[0]
        self.assertFalse(multiple.exact)
        self.assertEqual("charge", multiple.cost["source_counter_removal_cost"]["counter_name"])
        self.assertEqual(
            ["Remove a luck counter from this artifact"],
            multiple.cost["uncompiled_costs"],
        )

        with self.assertRaises(SourceCounterRemovalCostError):
            SourceCounterRemovalCost("charge", 0)
        with self.assertRaises(SourceCounterRemovalCostError):
            SourceCounterRemovalCost.from_dict(
                {"schema_version": 1, "counter_name": "charge", "amount": 1, "x": 2}
            )

    def test_source_counter_cost_capability_fails_closed(self):
        value = deepcopy(self.registry_value)
        capability = next(row for row in value["capabilities"] if row["id"] == CAPABILITY)
        capability["status"] = "blocked"
        capability["blockers"] = ["focused mutation witness"]
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        ir = self.compile(
            fixture_card(
                "Counter Capability Fixture",
                "Remove a charge counter from this artifact: Draw a card.",
            ),
            registry=registry,
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_source_counter_cost_compiler_mutant_is_killed(self):
        record = fixture_card(
            "Counter Parser Fixture",
            "Remove a charge counter from this artifact: Draw a card.",
        )

        def assert_exact() -> None:
            ir = self.compile(record)
            node = ir.faces[0].nodes[0]
            self.assertTrue(node.exact, ir.to_dict())
            self.assertEqual(
                "charge",
                node.cost["source_counter_removal_cost"]["counter_name"],
            )

        assert_exact()
        with mock.patch.object(
            abilities_module,
            "_source_counter_removal_cost",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class FixedSourceCounterCostRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )
        cls.capabilities = trusted_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = None
        session.engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_source(self, session, *, name: str, ref: str) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["A"].zones["battlefield"].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        return card

    @staticmethod
    def prepare_priority(session, *, players_mana: int = 8) -> None:
        engine = session.engine
        for symbol in ("B", "C", "G", "R", "U", "W"):
            engine.state.players["A"].mana_pool[symbol] = players_mana
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine.pump()

    @staticmethod
    def pass_until(session, predicate, *, limit: int = 48) -> None:
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Resolution stopped without a decision")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Resolution did not converge")

    @staticmethod
    def action(session, action_id: str, *, seat: str = "A"):
        return next(
            row
            for row in session.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
            if row["id"] == action_id
        )

    def test_source_counter_cost_activation_pays_before_resolution(self):
        session = self.session(19701)
        source = self.add_source(session, name="Counter Cost Reservoir", ref="A-reservoir")
        source.counters["charge"] = 3
        self.prepare_priority(session)
        ability = session.engine._activated_abilities(source)[0]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        action = self.action(session, action_id)
        self.assertEqual(2, action["cost_summary"]["remove_source_counter"]["amount"])
        hand_before = len(session.state.players["A"].zones["hand"])
        mana_before = sum(session.state.players["A"].mana_pool.values())
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action_id})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, source.counters["charge"])
        self.assertTrue(source.tapped)
        self.assertEqual(
            mana_before - 1,
            sum(session.state.players["A"].mana_pool.values()),
        )
        self.assertEqual(1, len(session.state.stack))
        self.pass_until(session, lambda: not session.state.stack)
        self.assertEqual(hand_before + 1, len(session.state.players["A"].zones["hand"]))

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "source-counter-cost-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_insufficient_and_stale_counter_costs_roll_back(self):
        session = self.session(19702)
        source = self.add_source(session, name="Counter Cost Reservoir", ref="A-reservoir")
        source.counters["charge"] = 2
        self.prepare_priority(session)
        ability = session.engine._activated_abilities(source)[0]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        self.action(session, action_id)
        before_pool = dict(session.state.players["A"].mana_pool)
        source.counters["charge"] = 1

        result = session.act("pilot:A", {"action_id": action_id})

        self.assertFalse(result.ok)
        self.assertEqual(1, source.counters["charge"])
        self.assertFalse(source.tapped)
        self.assertEqual(before_pool, dict(session.state.players["A"].mana_pool))
        self.assertFalse(session.state.stack)
        self.prepare_priority(session)
        self.assertNotIn(
            action_id,
            {
                row["id"]
                for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            },
        )

    def test_current_ability_removal_rejects_counter_cost_activation(self):
        session = self.session(19703)
        source = self.add_source(session, name="Counter Cost Reservoir", ref="A-reservoir")
        source.counters["charge"] = 2
        self.prepare_priority(session)
        ability = session.engine._activated_abilities(source)[0]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        self.action(session, action_id)
        before_pool = dict(session.state.players["A"].mana_pool)

        with mock.patch.object(session.engine, "_activated_abilities", return_value=()):
            result = session.act("pilot:A", {"action_id": action_id})

        self.assertFalse(result.ok)
        self.assertEqual(2, source.counters["charge"])
        self.assertFalse(source.tapped)
        self.assertEqual(before_pool, dict(session.state.players["A"].mana_pool))

    def test_source_counter_cost_mana_ability_is_nonreversible(self):
        session = self.session(19704)
        source = self.add_source(session, name="Counter Cost Mana Relic", ref="A-relic")
        source.counters["charge"] = 2
        self.prepare_priority(session, players_mana=0)
        ability = session.engine._activated_abilities(source)[0]
        result = session.act(
            "pilot:A",
            {"action_id": f"activate:{source.ref}:{ability.ability_id}"},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, source.counters["charge"])
        self.assertEqual(1, session.state.players["A"].mana_pool["G"])
        self.assertFalse(session.state.stack)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row.get("action") == "undo_mana" for row in actions))

    def test_source_counter_cost_commit_mutant_is_killed(self):
        def assert_paid(seed: int) -> None:
            session = self.session(seed)
            source = self.add_source(
                session,
                name="Counter Cost Mana Relic",
                ref="A-relic",
            )
            source.counters["charge"] = 1
            self.prepare_priority(session, players_mana=0)
            ability = session.engine._activated_abilities(source)[0]
            result = session.act(
                "pilot:A",
                {"action_id": f"activate:{source.ref}:{ability.ability_id}"},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertNotIn("charge", source.counters)

        assert_paid(19706)
        with mock.patch(
            "quorune.rules.activation.counter_costs.commit_counter_removals",
            return_value=(),
        ):
            with self.assertRaises(AssertionError):
                assert_paid(19707)

    def test_counter_cost_projection_is_public_and_seat_scoped(self):
        session = self.session(19705, players=4)
        source = self.add_source(session, name="Counter Cost Reservoir", ref="A-reservoir")
        source.counters["charge"] = 2
        self.prepare_priority(session)
        ability = session.engine._activated_abilities(source)[0]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        packet_a = session.packet("pilot:A", full=True)
        packet_b = session.packet("pilot:B", full=True)
        self.assertIn(
            action_id,
            {row["id"] for row in packet_a["decision"]["ctx"]["legal"]["actions"]},
        )
        self.assertNotIn(action_id, json.dumps(packet_b, sort_keys=True))
        self.assertIn('"charge": 2', json.dumps(packet_b, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
