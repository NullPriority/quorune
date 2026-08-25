from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler import activated_mana_nodes
from quorune.compiler.activated_ability_catalog import (
    compile_activated_ability_catalog,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.activation_costs import (
    FixedTapActivationCostError,
    commit_fixed_tap_cost,
    prepare_fixed_tap_cost,
)
from quorune.rules.capabilities import CapabilityRegistry
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fixed-activation-tap-costs.json"
TAP_COST_CAPABILITY = "activation.selected_tap.fixed"


def trusted_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(REGISTRY_PATH)
    registry.mark_evidence_verified("0" * 64)
    return registry


def fixture_card(name: str, oracle_text: str) -> CardRecord:
    return CardRecord(
        oracle_id=f"fixture:{name.casefold().replace(' ', '-')}",
        name=name,
        mana_cost="{0}",
        mana_value=0.0,
        type_line="Creature — Wizard",
        oracle_text=oracle_text,
        power="2",
        toughness="2",
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=("W", "U", "B", "R", "G"),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-08-25",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-activation-tap-costs.sqlite3"
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


class FixedActivationTapCostCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        cls.capabilities = trusted_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, record: CardRecord):
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_type_and_subtype_tap_costs_compile_source_spanned(self):
        examples = (
            (
                self.db.lookup("Azami, Lady of Scrolls"),
                1,
                "subtypes_all",
                ["wizard"],
                False,
            ),
            (
                fixture_card(
                    "Fixed Artifact Tap Fixture",
                    "Tap two untapped artifacts you control: Draw a card.",
                ),
                2,
                "types_all",
                ["artifact"],
                False,
            ),
            (
                fixture_card(
                    "Another Creature Tap Fixture",
                    "Tap another untapped creature you control: Draw a card.",
                ),
                1,
                "types_all",
                ["creature"],
                True,
            ),
            (
                fixture_card(
                    "Fixed Merfolk Tap Fixture",
                    "Tap seven untapped Merfolk you control: Draw a card.",
                ),
                7,
                "subtypes_all",
                ["merfolk"],
                False,
            ),
        )
        for record, count, field, value, another in examples:
            with self.subTest(record=record.name):
                ir = self.compile(record)
                node = next(
                    item
                    for item in ir.faces[0].nodes
                    if item.kind == "activated_ability"
                )
                self.assertTrue(node.exact, ir.to_dict())
                self.assertEqual(
                    node.text,
                    record.oracle_text[node.span.start : node.span.end],
                )
                self.assertEqual([], node.cost["uncompiled_costs"])
                self.assertEqual(1, len(node.cost["choices"]))
                choice = node.cost["choices"][0]
                self.assertEqual("tap", choice["k"])
                self.assertEqual(count, choice["n"])
                self.assertEqual(value, choice["q"][field])
                self.assertEqual(
                    "$source" if another else None,
                    choice["q"]["exclude_ref"],
                )
                self.assertIn(TAP_COST_CAPABILITY, node.capability_dependencies)
                catalog = compile_activated_ability_catalog(record)["front"]
                lowered = next(
                    ability
                    for ability in catalog
                    if ability.line_index == node.span.line - 1
                )
                self.assertIsNotNone(lowered.choices[0].fixed_tap_cost())
                self.assertFalse(lowered.uncompiled_costs)

    def test_tap_cost_grammar_rejects_qualified_and_open_variants(self):
        unsupported = (
            "Tap any number of untapped creatures you control",
            "Tap two untapped artifacts and/or creatures you control",
            "Tap an untapped white creature you control",
            "Tap an untapped legendary permanent you control",
            "Tap an untapped snow land you control",
            "Tap an untapped nontoken artifact you control",
            "Tap an untapped creature you control with flying",
            "Tap target untapped creature you control",
        )
        for index, cost in enumerate(unsupported):
            with self.subTest(cost=cost):
                record = fixture_card(
                    f"Unsupported Tap Cost {index}",
                    f"{cost}: Draw a card.",
                )
                node = self.compile(record).faces[0].nodes[0]
                self.assertFalse(node.exact)
                self.assertEqual([cost], node.cost["uncompiled_costs"])
                self.assertNotIn(
                    TAP_COST_CAPABILITY, node.capability_dependencies
                )

        mana = self.compile(
            fixture_card(
                "Tap Cost Mana Fixture",
                "Tap an untapped artifact you control: Add {U}.",
            )
        ).faces[0].nodes[0]
        self.assertFalse(mana.exact)
        self.assertEqual(
            ["Tap an untapped artifact you control"],
            mana.cost["uncompiled_costs"],
        )

    def test_selected_tap_cost_dependencies_fail_closed(self):
        record = self.db.lookup("Azami, Lady of Scrolls")
        value = deepcopy(self.registry_value)
        capability = next(
            row
            for row in value["capabilities"]
            if row["id"] == TAP_COST_CAPABILITY
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["focused mutation witness"]
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)

        ir = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )

        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_selected_tap_cost_compiler_mutant_is_killed(self):
        record = self.db.lookup("Azami, Lady of Scrolls")

        def assert_exact() -> None:
            ir = self.compile(record)
            self.assertEqual("exact", ir.status)
            self.assertEqual("tap", ir.faces[0].nodes[0].cost["choices"][0]["k"])

        assert_exact()
        with mock.patch.object(
            activated_mana_nodes,
            "fixed_activated_tap_cost",
            side_effect=lambda ability: ability,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class FixedActivationTapCostRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
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

    def add_card(
        self,
        session,
        *,
        seat: str,
        name: str,
        ref: str,
        controller: str | None = None,
        register: bool = False,
    ) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=controller or seat,
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=engine.state.players[seat].turns_begun,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        if register:
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
    def prepare_priority(session, *, seat: str = "A") -> None:
        engine = session.engine
        engine.state.active_player = seat
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority(seat)
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
        packet = session.packet(f"pilot:{seat}", full=True)
        context = packet["decision"]["ctx"]
        if "legal" not in context:
            raise AssertionError(json.dumps(packet, sort_keys=True))
        return next(
            row
            for row in context["legal"]["actions"]
            if row["id"] == action_id
        )

    @staticmethod
    def tap_ability(engine, source: CardInstance):
        return next(
            ability
            for ability in engine._activated_abilities(source)
            if any(
                choice.fixed_tap_cost() is not None
                for choice in ability.choices
            )
        )

    def test_offer_commit_and_replay_share_current_untapped_wizard_query(self):
        session = self.session(7082501, players=4)
        engine = session.engine
        source = self.add_card(
            session,
            seat="A",
            name="Azami, Lady of Scrolls",
            ref="A-azami",
            register=True,
        )
        wizard = self.add_card(
            session,
            seat="A",
            name="Vedalken Aethermage",
            ref="A-wizard",
        )
        tapped = self.add_card(
            session,
            seat="A",
            name="Vedalken Aethermage",
            ref="A-tapped-wizard",
        )
        tapped.tapped = True
        phased = self.add_card(
            session,
            seat="A",
            name="Vedalken Aethermage",
            ref="A-phased-wizard",
        )
        phased.phased_out = True
        opposing = self.add_card(
            session,
            seat="B",
            name="Vedalken Aethermage",
            ref="B-wizard",
            controller="B",
        )
        self.prepare_priority(session)
        ability = self.tap_ability(engine, source)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        action = self.action(session, action_id)
        self.assertEqual(
            {source.ref, wizard.ref},
            set(action["cost_summary"]["choose_cost"][0]["legal_refs"]),
        )
        before = authoritative_state_hash(engine.state)

        rejected = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [opposing.ref]},
        )

        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        source = engine.state.cards[source.object_id]
        wizard = engine.state.cards[wizard.object_id]
        hand_before = len(engine.state.players["A"].zones["hand"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        accepted = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [source.ref]},
        )

        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(source.tapped)
        self.assertFalse(wizard.tapped)
        self.assertTrue(engine.state.stack)
        self.pass_until(session, lambda: not engine.state.stack)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "fixed-tap-cost-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_multiple_tap_cost_rejects_duplicate_and_stale_selection_atomically(self):
        session = self.session(7082502)
        engine = session.engine
        source = self.add_card(
            session,
            seat="A",
            name="Cryptbreaker",
            ref="A-cryptbreaker",
            register=True,
        )
        fodder = tuple(
            self.add_card(
                session,
                seat="A",
                name="Cryptbreaker",
                ref=f"A-zombie-{index}",
            )
            for index in range(1, 4)
        )
        self.prepare_priority(session)
        ability = self.tap_ability(engine, source)
        choice = ability.choices[0]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        before = authoritative_state_hash(engine.state)

        rejected = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "cost_cards": [fodder[0].ref, fodder[0].ref, fodder[1].ref],
            },
        )

        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        source = engine.state.cards[source.object_id]
        fodder = tuple(engine.state.cards[card.object_id] for card in fodder)
        plan = prepare_fixed_tap_cost(
            engine,
            seat="A",
            source=source,
            choice=choice,
            response={"cost_cards": [card.ref for card in fodder]},
        )
        engine.state.cards[fodder[2].object_id].tapped = True
        with self.assertRaises(FixedTapActivationCostError):
            commit_fixed_tap_cost(engine, plan)
        self.assertFalse(engine.state.cards[fodder[0].object_id].tapped)
        self.assertFalse(engine.state.cards[fodder[1].object_id].tapped)
        engine.state.cards[fodder[2].object_id].tapped = False
        self.prepare_priority(session)

        accepted = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "cost_cards": [card.ref for card in fodder],
            },
        )

        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(source.tapped)
        self.assertTrue(all(card.tapped for card in fodder))
        self.assertTrue(engine.state.stack)


if __name__ == "__main__":
    unittest.main()
