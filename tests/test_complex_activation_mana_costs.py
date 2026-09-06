from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session, pass_current
from quorune.activation_mana_cost import (
    ActivationManaCostOption,
    fixed_complex_activation_mana_options,
)
from quorune.card_programs import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.choice_forms import build_action_form, delegated_choice_fields
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.deck import DeckLoader
from quorune.mana import ManaPlanError, auto_plan_payment
from quorune.mana_provenance import (
    add_mana,
    mana_provenance_lots,
    MANA_PROVENANCE_KEY,
    ManaProvenanceError,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.activation.commit import commit_activation
from quorune.rules.activation.model import (
    ActivationProposalError,
    ActivationProposalRequest,
)
from quorune.rules.activation.proposal import build_activation_proposal
from quorune.rules.capabilities import CapabilityRegistry
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "complex-activation-mana-cards.json"


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def trusted_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(REGISTRY_PATH)
    registry.mark_evidence_verified("0" * 64)
    return registry


def mana_record(text: str, *, suffix: int) -> CardRecord:
    return CardRecord(
        oracle_id=f"17400000-0000-4000-8000-{suffix:012d}",
        name="Generic Complex Mana Channel",
        mana_cost="{2}",
        mana_value=2.0,
        type_line="Artifact",
        oracle_text=text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-09-06",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "complex-activation-mana.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class ComplexActivationManaCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = trusted_registry()

    def compile(self, text: str, *, suffix: int):
        record = mana_record(text, suffix=suffix)
        oracle = compile_oracle_card(
            record,
            capability_registry=self.registry,
            capability_profile="commander_review",
        )
        program = compile_card_program(
            _NoRulingsDatabase(),
            record,
            capability_registry=self.registry,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        return oracle, program

    def test_closed_complex_symbols_compile_to_typed_options(self):
        cases = (
            ("{W/U}, {T}: You gain 2 life.", 2, 0, {"U", "W"}),
            ("{2/W}, {T}: You gain 2 life.", 2, 0, {"GENERIC", "W"}),
            ("{R/P}, {T}: You gain 3 life.", 2, 0, {"R"}),
            ("{S}, {T}: You gain 1 life.", 1, 1, set()),
            ("{W/U}{R/P}{S}, {T}: You gain 4 life.", 4, 1, {"U", "W", "R"}),
        )
        for index, (text, count, snow, symbols) in enumerate(cases):
            with self.subTest(text=text):
                oracle, program = self.compile(
                    text,
                    suffix=174_001_000 + index,
                )
                self.assertEqual("exact", oracle.status, oracle.material_residuals)
                self.assertEqual((), program.residuals)
                self.assertTrue(program.trust_closure["strict_capability_ready"])
                descriptor = next(
                    handler["ability"]
                    for ability in program.abilities
                    for handler in ability.handlers
                    if handler.get("handler_id")
                    == "activation.catalog.pinned.v1"
                )
                options = tuple(
                    ActivationManaCostOption.from_dict(value)
                    for value in descriptor["mana_cost_options"]
                )
                self.assertEqual(count, len(options))
                self.assertEqual(
                    snow,
                    max(option.snow_payment for option in options),
                )
                represented = {
                    key
                    for option in options
                    for key, amount in option.requirements.items()
                    if amount
                }
                self.assertEqual(symbols, represented)
                self.assertTrue(
                    all(
                        "activation.mana_cost.fixed_complex"
                        in node.capability_dependencies
                        for node in oracle.faces[0].nodes
                        if node.kind == "activated_ability"
                    )
                )

    def test_option_model_and_unsupported_symbols_fail_closed(self):
        mixed = fixed_complex_activation_mana_options(
            {"GENERIC": 1},
            ("W/U", "R/P", "S"),
        )
        self.assertEqual(4, len(mixed))
        self.assertEqual({0, 2}, {option.life_payment for option in mixed})
        self.assertTrue(all(option.snow_payment == 1 for option in mixed))
        self.assertEqual(
            [option.to_dict() for option in mixed],
            [
                ActivationManaCostOption.from_dict(option.to_dict()).to_dict()
                for option in mixed
            ],
        )
        malformed = mixed[0].to_dict()
        malformed["unknown"] = True
        with self.assertRaises(ValueError):
            ActivationManaCostOption.from_dict(malformed)
        malformed = mixed[0].to_dict()
        malformed["id"] = 1
        with self.assertRaises(ValueError):
            ActivationManaCostOption.from_dict(malformed)
        malformed = mixed[0].to_dict()
        malformed["requirements"]["GENERIC"] = "1"
        with self.assertRaises(ValueError):
            ActivationManaCostOption.from_dict(malformed)

        excluded = (
            "{X}, {T}: You gain 1 life.",
            "Pay {E}, {T}: You gain 1 life.",
            "{W/P}, Discard two cards: You gain 1 life.",
        )
        for index, text in enumerate(excluded):
            with self.subTest(text=text):
                oracle = compile_oracle_card(
                    mana_record(text, suffix=174_002_000 + index),
                    capability_registry=self.registry,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", oracle.status)
                self.assertTrue(oracle.material_residuals)

    def test_complex_activation_mana_dependency_fails_closed(self):
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        capability = next(
            row
            for row in value["capabilities"]
            if row["id"] == "activation.mana_cost.fixed_complex"
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["focused dependency mutation"]
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        oracle = compile_oracle_card(
            mana_record(
                "{W/U}, {T}: You gain 2 life.",
                suffix=174_003_001,
            ),
            capability_registry=registry,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", oracle.status)
        self.assertTrue(oracle.material_residuals)

    def test_snow_allocation_does_not_double_spend_colored_mana(self):
        with self.assertRaises(ManaPlanError):
            auto_plan_payment({"R": 1}, (), starting_pool={})
        ordinary = auto_plan_payment(
            {"R": 1},
            (),
            starting_pool={"R": 1},
        )
        self.assertEqual(1, ordinary.payment["R"])

        with self.assertRaises(ManaPlanError):
            auto_plan_payment(
                {"R": 1},
                (),
                starting_pool={"R": 1, "C": 1},
                starting_snow_pool={"R": 1},
                snow_required=1,
            )

        plan = auto_plan_payment(
            {"R": 1},
            (),
            reserve={"U": 1},
            starting_pool={"R": 1, "U": 1, "C": 1},
            starting_snow_pool={"C": 1},
            snow_required=1,
        )
        self.assertEqual(1, plan.payment["R"])
        self.assertEqual(1, plan.snow_payment["C"])
        self.assertEqual(0, plan.payment["U"])

    def test_provenance_journal_rejects_a_stale_restriction_summary(self):
        player = SimpleNamespace(
            mana_pool={key: 0 for key in "WUBRGC"},
            stats={},
        )
        add_mana(
            player,
            {"C": 1},
            snow=True,
            restriction="artifact_spell_only",
        )
        self.assertEqual(1, len(mana_provenance_lots(player)))
        player.stats["restricted_mana"]["artifact_spell_only"]["C"] = 2
        with self.assertRaises(ManaProvenanceError):
            mana_provenance_lots(player)


class ComplexActivationManaRuntimeTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, *card_names: str, seed: int, players: int = 2):
        deck = copy.deepcopy(self.mishra)
        entries = [entry for entry in deck.entries if entry.board == "mainboard"]
        for entry, card_name in zip(
            entries[: len(card_names)],
            card_names,
            strict=True,
        ):
            entry.name = card_name
        session = make_session(
            self.db,
            deck,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def permanent(engine, name: str):
        card = next(
            value
            for value in engine.state.cards.values()
            if value.owner == "A" and value.printed_name == name
        )
        engine.move_card(
            card.object_id,
            "battlefield",
            controller="A",
            tapped=False,
            log=False,
        )
        return card

    @staticmethod
    def set_priority(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")

    @staticmethod
    def offers(session) -> list[dict]:
        session.engine.pump()
        packet = session.packet("pilot:A", full=True)
        return packet["decision"]["ctx"]["legal"]["actions"]

    def offer(self, session, source, ability) -> dict:
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        return next(action for action in self.offers(session) if action["id"] == action_id)

    @staticmethod
    def resolve(session) -> None:
        for _ in range(16):
            if not session.engine.state.stack:
                return
            pass_current(session)

    def test_hybrid_offer_commit_and_stale_option_are_atomic(self):
        session = self.session("Hybrid Channel", seed=174_010_001)
        engine = session.engine
        source = self.permanent(engine, "Hybrid Channel")
        engine.state.players["A"].mana_pool.update({"U": 1, "W": 1})
        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        options = {
            next(
                key
                for key, amount in option.requirements.items()
                if amount
            ): option
            for option in ability.mana_cost_options
        }
        offer = self.offer(session, source, ability)
        advertised = offer["cost_options"]
        self.assertEqual(2, len(advertised))
        self.assertEqual(
            {"Pay {U}", "Pay {W}"},
            {value["label"] for value in advertised},
        )
        form = build_action_form(
            offer,
            decision_kind="priority",
            context={},
        )
        self.assertIsNotNone(form)
        self.assertEqual("cost_option", form["variants"]["field"])
        self.assertEqual(
            {"cost_option"},
            delegated_choice_fields(
                offer,
                decision_kind="priority",
                context={},
            ),
        )
        proposal = build_activation_proposal(
            engine,
            ActivationProposalRequest.from_submission(
                "A",
                {
                    "source": source.ref,
                    "from": "battlefield",
                    "ability": ability.ability_id,
                    "cost_option": options["U"].option_id,
                },
            ),
        )
        engine.state.players["A"].mana_pool["U"] = 0
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(ActivationProposalError):
            commit_activation(
                engine,
                proposal,
                {
                    "pay": "manual",
                    "payment": {"U": 1},
                },
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertFalse(source.tapped)

        engine.state.players["A"].mana_pool["U"] = 1
        self.set_priority(engine)
        offer = self.offer(session, source, ability)
        life_before = engine.state.players["A"].life
        accepted = session.act(
            "pilot:A",
            {
                "action_id": offer["id"],
                "cost_option": options["U"].option_id,
                "pay": "manual",
                "payment": {"U": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(life_before + 2, engine.state.players["A"].life)
        self.assertEqual(1, engine.state.players["A"].mana_pool["W"])
        self.assertEqual(0, engine.state.players["A"].mana_pool["U"])

    def test_hybrid_locket_output_pays_sibling_complex_activation(self):
        session = self.session(
            "Hybrid Locket",
            "Hybrid Locket",
            seed=174_010_009,
        )
        engine = session.engine
        lockets = [
            value
            for value in engine.state.cards.values()
            if value.owner == "A" and value.printed_name == "Hybrid Locket"
        ][:2]
        for locket in lockets:
            engine.move_card(
                locket.object_id,
                "battlefield",
                controller="A",
                tapped=False,
                log=False,
            )
        producer, spender = lockets
        self.set_priority(engine)
        producer_ability = engine._activated_abilities(producer)[0]
        produced = session.act(
            "pilot:A",
            {
                "action_id": self.offer(
                    session,
                    producer,
                    producer_ability,
                )["id"],
                "mana_output": {"U": 1},
            },
        )
        self.assertTrue(produced.ok, produced.summary)
        self.assertTrue(producer.tapped)
        self.assertEqual(1, engine.state.players["A"].mana_pool["U"])

        self.set_priority(engine)
        spender_ability = engine._activated_abilities(spender)[1]
        offer = self.offer(session, spender, spender_ability)
        life_before = engine.state.players["A"].life
        accepted = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertTrue(spender.tapped)
        self.assertEqual(life_before + 2, engine.state.players["A"].life)
        self.assertEqual(0, engine.state.players["A"].mana_pool["U"])

    def test_two_brid_offer_and_commit_preserve_the_unspent_color(self):
        session = self.session("Two-Brid Channel", seed=174_010_012)
        engine = session.engine
        source = self.permanent(engine, "Two-Brid Channel")
        engine.state.players["A"].mana_pool.update(
            {"W": 1, "C": 2}
        )
        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        generic_option = next(
            option
            for option in ability.mana_cost_options
            if option.requirements["GENERIC"] == 2
        )
        offer = self.offer(session, source, ability)
        self.assertEqual(2, len(offer["cost_options"]))
        self.assertEqual(
            {"Pay {2}", "Pay {W}"},
            {value["label"] for value in offer["cost_options"]},
        )
        life_before = engine.state.players["A"].life
        accepted = session.act(
            "pilot:A",
            {
                "action_id": offer["id"],
                "cost_option": generic_option.option_id,
                "pay": "manual",
                "payment": {"C": 2},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(life_before + 2, engine.state.players["A"].life)
        self.assertEqual(1, engine.state.players["A"].mana_pool["W"])
        self.assertEqual(0, engine.state.players["A"].mana_pool["C"])

    def test_hybrid_payment_and_source_sacrifice_share_one_transaction(self):
        session = self.session(
            "Hybrid Sacrifice Channel",
            seed=174_010_013,
        )
        engine = session.engine
        source = self.permanent(engine, "Hybrid Sacrifice Channel")
        engine.state.players["A"].mana_pool["U"] = 1
        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        option = next(
            value
            for value in ability.mana_cost_options
            if value.requirements["U"] == 1
        )
        hand_before = len(engine.state.players["A"].zones["hand"])
        accepted = session.act(
            "pilot:A",
            {
                "action_id": self.offer(session, source, ability)["id"],
                "cost_option": option.option_id,
                "pay": "manual",
                "payment": {"U": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", source.zone)
        self.assertEqual(0, engine.state.players["A"].mana_pool["U"])
        self.resolve(session)
        self.assertEqual(
            hand_before + 2,
            len(engine.state.players["A"].zones["hand"]),
        )

    def test_hybrid_payment_and_untap_symbol_commit_atomically(self):
        session = self.session("Hybrid Untap Channel", seed=174_010_014)
        engine = session.engine
        source = self.permanent(engine, "Hybrid Untap Channel")
        source.tapped = True
        engine.state.players["A"].mana_pool.update({"U": 1, "C": 1})
        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        option = next(
            value
            for value in ability.mana_cost_options
            if value.requirements["U"] == 1
        )
        hand_before = len(engine.state.players["A"].zones["hand"])
        accepted = session.act(
            "pilot:A",
            {
                "action_id": self.offer(session, source, ability)["id"],
                "cost_option": option.option_id,
                "pay": "manual",
                "payment": {"U": 1, "C": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(source.tapped)
        self.assertFalse(any(engine.state.players["A"].mana_pool.values()))
        self.resolve(session)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )

    def test_hybrid_target_revalidates_after_departure(self):
        session = self.session("Hybrid Bolt", seed=174_010_010)
        engine = session.engine
        source = self.permanent(engine, "Hybrid Bolt")
        target_ref = engine.create_token(
            "B",
            name="Complex Mana Target",
            characteristics={
                "type_line": "Token Creature — Citizen",
                "power": "3",
                "toughness": "3",
            },
            reason="complex activation mana target fixture",
        )[0]
        target = engine._resolve_object(
            "B",
            target_ref,
            zones={"battlefield"},
        )
        engine.state.players["A"].mana_pool["U"] = 1
        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        option = next(
            value
            for value in ability.mana_cost_options
            if value.requirements["U"] == 1
        )
        offer = self.offer(session, source, ability)
        accepted = session.act(
            "pilot:A",
            {
                "action_id": offer["id"],
                "cost_option": option.option_id,
                "targets": [target.ref],
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        engine.move_card(target.object_id, "graveyard", log=False)
        self.resolve(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual(0, target.marked_damage)

    def test_phyrexian_options_share_mana_and_life_owners(self):
        life_session = self.session("Phyrexian Channel", seed=174_010_002)
        engine = life_session.engine
        source = self.permanent(engine, "Phyrexian Channel")
        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        life_option = next(
            option for option in ability.mana_cost_options if option.life_payment
        )
        offer = self.offer(life_session, source, ability)
        life_before = engine.state.players["A"].life
        accepted = life_session.act(
            "pilot:A",
            {
                "action_id": offer["id"],
                "cost_option": life_option.option_id,
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(life_session)
        self.assertEqual(life_before + 1, engine.state.players["A"].life)

        mana_session = self.session("Phyrexian Channel", seed=174_010_003)
        engine = mana_session.engine
        source = self.permanent(engine, "Phyrexian Channel")
        engine.state.players["A"].mana_pool["R"] = 1
        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        mana_option = next(
            option
            for option in ability.mana_cost_options
            if option.requirements["R"] == 1
        )
        offer = self.offer(mana_session, source, ability)
        life_before = engine.state.players["A"].life
        accepted = mana_session.act(
            "pilot:A",
            {
                "action_id": offer["id"],
                "cost_option": mana_option.option_id,
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(mana_session)
        self.assertEqual(life_before + 3, engine.state.players["A"].life)
        self.assertEqual(0, engine.state.players["A"].mana_pool["R"])

        blocked = self.session("Phyrexian Channel", seed=174_010_004)
        engine = blocked.engine
        source = self.permanent(engine, "Phyrexian Channel")
        engine.state.players["A"].life = 1
        self.set_priority(engine)
        action_id = f"activate:{source.ref}:{engine._activated_abilities(source)[0].ability_id}"
        self.assertNotIn(action_id, {action["id"] for action in self.offers(blocked)})

    def test_snow_payment_uses_current_source_provenance_and_replays(self):
        session = self.session(
            "Snow Channel",
            "Ordinary Grove",
            "Snow Grove",
            seed=174_010_005,
        )
        engine = session.engine
        source = self.permanent(engine, "Snow Channel")
        ordinary = self.permanent(engine, "Ordinary Grove")
        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        self.assertNotIn(action_id, {action["id"] for action in self.offers(session)})

        snow = self.permanent(engine, "Snow Grove")
        self.set_priority(engine)
        offer = self.offer(session, source, ability)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        life_before = engine.state.players["A"].life
        accepted = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(source.tapped)
        self.assertTrue(snow.tapped)
        self.assertFalse(ordinary.tapped)
        self.resolve(session)
        self.assertEqual(life_before + 1, engine.state.players["A"].life)
        self.assertFalse(any(engine.state.players["A"].mana_pool.values()))
        self.assertNotIn(MANA_PROVENANCE_KEY, engine.state.players["A"].stats)
        expected = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "complex-snow-activation"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_stale_snow_provenance_rolls_back_before_tap(self):
        session = self.session("Snow Channel", seed=174_010_011)
        engine = session.engine
        source = self.permanent(engine, "Snow Channel")
        engine._add_mana_to_pool("A", {"U": 1}, snow_source=True)
        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        proposal = build_activation_proposal(
            engine,
            ActivationProposalRequest.from_submission(
                "A",
                {
                    "source": source.ref,
                    "from": "battlefield",
                    "ability": ability.ability_id,
                },
            ),
        )
        engine.state.players["A"].stats.pop(MANA_PROVENANCE_KEY)
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(ActivationProposalError):
            commit_activation(engine, proposal, {})
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertFalse(source.tapped)
        self.assertEqual(1, engine.state.players["A"].mana_pool["U"])

    def test_mixed_option_commits_hybrid_life_and_snow_as_one_cost(self):
        session = self.session(
            "Mixed Channel",
            "Snow Grove",
            seed=174_010_008,
        )
        engine = session.engine
        source = self.permanent(engine, "Mixed Channel")
        snow = self.permanent(engine, "Snow Grove")
        engine.state.players["A"].mana_pool["U"] = 1
        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        option = next(
            value
            for value in ability.mana_cost_options
            if value.requirements["U"] == 1
            and value.life_payment == 2
            and value.snow_payment == 1
        )
        offer = self.offer(session, source, ability)
        life_before = engine.state.players["A"].life
        accepted = session.act(
            "pilot:A",
            {
                "action_id": offer["id"],
                "cost_option": option.option_id,
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(source.tapped)
        self.assertTrue(snow.tapped)
        self.resolve(session)
        self.assertEqual(life_before + 2, engine.state.players["A"].life)
        self.assertFalse(any(engine.state.players["A"].mana_pool.values()))
        self.assertNotIn(MANA_PROVENANCE_KEY, engine.state.players["A"].stats)

    def test_restricted_snow_overlap_and_projection_preserve_only_aggregates(self):
        session = self.session(
            "Snow Channel",
            "Snow Restricted Rock",
            "Snow Grove",
            seed=174_010_006,
            players=4,
        )
        engine = session.engine
        source = self.permanent(engine, "Snow Channel")
        restricted = self.permanent(engine, "Snow Restricted Rock")
        self.set_priority(engine)
        restricted_ability = engine._activated_abilities(restricted)[0]
        restricted_offer = self.offer(session, restricted, restricted_ability)
        produced = session.act(
            "pilot:A",
            {"action_id": restricted_offer["id"]},
        )
        self.assertTrue(produced.ok, produced.summary)
        self.assertEqual(1, engine.state.players["A"].mana_pool["C"])

        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        self.assertNotIn(action_id, {action["id"] for action in self.offers(session)})
        snow = self.permanent(engine, "Snow Grove")
        self.set_priority(engine)
        snow_ability = engine._activated_abilities(snow)[0]
        snow_offer = self.offer(session, snow, snow_ability)
        produced = session.act("pilot:A", {"action_id": snow_offer["id"]})
        self.assertTrue(produced.ok, produced.summary)
        self.set_priority(engine)
        offer = self.offer(session, source, ability)
        projected = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:B")
        )
        self.assertNotIn(MANA_PROVENANCE_KEY, projected)
        self.assertNotIn(source.object_id, projected)
        accepted = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(1, engine.state.players["A"].mana_pool["C"])
        self.assertEqual(
            {"C": 1},
            {
                key: value
                for key, value in engine.state.players["A"].stats[
                    "restricted_mana"
                ]["artifact_spell_only"].items()
                if value
            },
        )

    def test_current_ability_removal_rejects_option_before_mutation(self):
        session = self.session("Hybrid Channel", seed=174_010_007)
        engine = session.engine
        source = self.permanent(engine, "Hybrid Channel")
        engine.state.players["A"].mana_pool["U"] = 1
        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        option = next(
            value
            for value in ability.mana_cost_options
            if value.requirements["U"] == 1
        )
        proposal = build_activation_proposal(
            engine,
            ActivationProposalRequest.from_submission(
                "A",
                {
                    "source": source.ref,
                    "from": "battlefield",
                    "ability": ability.ability_id,
                    "cost_option": option.option_id,
                },
            ),
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-complex-mana-ability",
                source_id="fixture:remove-complex-mana-source",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=source.object_id,
                        logical_object_id=source.logical_object_id,
                    ),
                ),
            ),
        )
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(ActivationProposalError):
            commit_activation(engine, proposal, {"payment": {"U": 1}})
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertFalse(source.tapped)
        self.assertEqual(1, engine.state.players["A"].mana_pool["U"])

    def test_compiler_and_snow_provenance_mutants_are_killed(self):
        record = mana_record(
            "{W/U}, {T}: You gain 2 life.",
            suffix=174_020_001,
        )

        def assert_exact() -> None:
            oracle = compile_oracle_card(
                record,
                capability_registry=trusted_registry(),
                capability_profile="commander_review",
            )
            self.assertEqual("exact", oracle.status)

        assert_exact()
        with mock.patch(
            "quorune.compiler.activation_mana_costs.fixed_complex_activation_mana_options",
            return_value=(),
        ):
            with self.assertRaises(AssertionError):
                assert_exact()

        session = self.session("Snow Channel", seed=174_020_002)
        engine = session.engine
        source = self.permanent(engine, "Snow Channel")
        engine._add_mana_to_pool("A", {"U": 1}, snow_source=True)
        self.set_priority(engine)
        ability = engine._activated_abilities(source)[0]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        self.assertIn(
            action_id,
            {action["id"] for action in self.offers(session)},
        )
        with mock.patch(
            "quorune.mana_provenance.snow_mana_pool",
            return_value={key: 0 for key in "WUBRGC"},
        ):
            self.assertNotEqual(
                "payable",
                engine._ability_availability("A", source, ability)[0],
            )


if __name__ == "__main__":
    unittest.main()
