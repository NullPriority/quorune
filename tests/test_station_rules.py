from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.compiler import station_nodes as station_nodes_module
from quorune.compiler.ir_model import SourceSpan
from quorune.counter_state import (
    CounterChange,
    commit_counter_changes,
    plan_counter_changes,
)
from quorune.deck import DeckLoader
from quorune.errors import StateInvariantError
from quorune.haste import is_summoning_sick
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.preflight import card_semantic_status
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.semantic_runtime.station_abilities import (
    ordinary_station_specs_from_descriptors,
)
from quorune.semantics import SemanticRegistry
from quorune.station import (
    OrdinaryStationAbilitySpec,
    StationAbilityError,
    StationPowerReference,
    commit_station_cost,
    compile_ordinary_station_ability,
    ordinary_station_handler_descriptor,
    prepare_station_cost,
    station_candidates,
    station_cost_choice,
    station_resolution_power,
)
from quorune.tap_state import set_permanent_tapped
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "station-rules-cards.json"
STATION_CAPABILITY = "counter.producer.station"
QUANTITY_CAPABILITY = "counter.placement.quantity_replacement"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "station-rules.sqlite3"
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


class StationModelTests(unittest.TestCase):
    def test_ordinary_station_descriptor_is_strict_and_round_trips(self):
        spec = compile_ordinary_station_ability(
            material_line="Station",
            oracle_line=(
                "Station (Tap another creature you control: Put charge "
                "counters equal to its power on this Spacecraft. Station "
                "only as a sorcery. It's an artifact creature at 9+.)"
            ),
            line_index=1,
        )
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(
            spec,
            OrdinaryStationAbilitySpec.from_dict(spec.to_dict()),
        )
        ability = spec.to_activated_ability()
        choice = station_cost_choice(ability)
        self.assertIsNotNone(choice)
        assert choice is not None
        self.assertEqual("station", choice.kind)
        self.assertEqual("battlefield", choice.zone)
        self.assertEqual("creature", choice.card_type)
        self.assertTrue(choice.another)
        self.assertTrue(ability.sorcery_speed)

        payload = spec.to_dict()
        payload["line_index"] = 99
        self.assertEqual(1, spec.line_index)
        malformed = spec.to_dict()
        malformed["unknown"] = True
        with self.assertRaisesRegex(StationAbilityError, "unknown"):
            OrdinaryStationAbilitySpec.from_dict(malformed)
        malformed = spec.to_dict()
        malformed["line_index"] = True
        with self.assertRaisesRegex(StationAbilityError, "nonnegative"):
            OrdinaryStationAbilitySpec.from_dict(malformed)

        descriptor = ordinary_station_handler_descriptor(spec)
        self.assertEqual(
            (spec,),
            ordinary_station_specs_from_descriptors([descriptor]),
        )
        descriptor["unknown"] = True
        with self.assertRaisesRegex(ValueError, "unknown"):
            ordinary_station_specs_from_descriptors([descriptor])

        reference = StationPowerReference(
            object_id="object:1",
            logical_object_id="logical:1",
            reference="P1",
            last_known_power=-2,
        )
        self.assertEqual(
            reference,
            StationPowerReference.from_dict(reference.to_dict()),
        )


class StationCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.record = cls.db.lookup("Wedgelight Rammer")
        cls.registry_value = json.loads(
            REGISTRY_PATH.read_text(encoding="utf-8")
        )
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, record=None, *, capabilities=None):
        return compile_oracle_card(
            record or self.record,
            capability_registry=capabilities or self.capabilities,
            capability_profile="commander_review",
        )

    def station_node(self, record=None, *, capabilities=None):
        ir = self.compile(record, capabilities=capabilities)
        return ir, next(
            node
            for node in ir.faces[0].nodes
            if "station" in node.mechanics
        )

    def test_ordinary_station_compiles_source_spanned_activation(self):
        ir, node = self.station_node()

        self.assertTrue(node.exact, ir.material_residuals)
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual("ordinary-station-activation-v1", node.template_id)
        self.assertEqual("battlefield", node.active_zone)
        self.assertEqual("activate", node.event)
        self.assertEqual((STATION_CAPABILITY,), node.capability_dependencies)
        self.assertEqual(
            {
                "k": "station",
                "n": 1,
                "z": "battlefield",
                "t": "creature",
                "other": 1,
            },
            node.cost["choices"][0],
        )
        self.assertEqual(
            {
                "op": "station",
                "card": "$source.zone_object",
                "amount": "$station.power",
                "source": "$source",
            },
            node.effects[0],
        )
        self.assertEqual(
            self.record.oracle_text[node.span.start : node.span.end],
            node.text,
        )
        self.assertEqual(
            "ability.activated.station.v1",
            node.handlers[0]["handler_id"],
        )

    def test_generated_station_program_is_capability_closed(self):
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (self.record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        program = registry.get(
            f"{self.record.oracle_id}:ability:ab2"
        )
        self.assertIsNotNone(program)
        assert program is not None
        self.assertEqual("trusted", program.trust_level)
        self.assertEqual([STATION_CAPABILITY], program.capability_dependencies)
        self.assertIn("station", program.coverage)
        self.assertGreaterEqual(result["runtime_handlers_promoted"], 1)
        status = card_semantic_status(
            self.record,
            registry,
            db=self.db,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertNotIn("keyword:station", status["unresolved"])

    def test_unsupported_station_families_remain_precise_residuals(self):
        residuals = []
        node = station_nodes_module.ordinary_station_keyword_node(
            node_id="fixture:modified-station:line:1",
            line="Station 2",
            material_line="Station 2",
            span=SourceSpan(start=0, end=9, line=1),
            mechanics=("station",),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            residuals=residuals,
        )
        self.assertIsNotNone(node)
        assert node is not None
        self.assertFalse(node.exact)
        self.assertTrue(node.residual_ids)
        residual = next(
            row
            for row in residuals
            if row.residual_id in node.residual_ids
        )
        self.assertEqual("unsupported_station_ability", residual.kind)
        self.assertIn("ordinary printed keyword grammar", residual.reason)

        real_ir, real_node = self.station_node()
        self.assertTrue(real_node.exact)
        self.assertTrue(real_ir.material_residuals)
        capability = self.capabilities.capability(STATION_CAPABILITY)
        assert capability is not None
        exclusions = " ".join(capability["applicability"]["exclusions"])
        self.assertIn("CR 721", exclusions)
        self.assertIn("Tapestry Warden", exclusions)
        self.assertIn("type-changing", exclusions)

    def test_station_dependency_mutation_fails_closed(self):
        for blocked in (STATION_CAPABILITY, QUANTITY_CAPABILITY):
            with self.subTest(blocked=blocked):
                value = deepcopy(self.registry_value)
                row = next(
                    item
                    for item in value["capabilities"]
                    if item["id"] == blocked
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused Station dependency mutation"]
                ir, node = self.station_node(
                    capabilities=CapabilityRegistry(value)
                )
                self.assertFalse(node.exact, ir.material_residuals)
                self.assertTrue(node.residual_ids)

    def test_station_compiler_mutant_is_killed(self):
        def assert_exact() -> None:
            _ir, node = self.station_node()
            self.assertTrue(node.exact)
            self.assertEqual("$station.power", node.effects[0]["amount"])

        assert_exact()
        with mock.patch.object(
            station_nodes_module,
            "compile_ordinary_station_ability",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class StationRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
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
        zone: str = "battlefield",
        register: bool = True,
        controller: str | None = None,
    ):
        engine = session.engine
        record = self.db.lookup(name)
        public = zone not in {"hand", "library"}
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=controller or seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=list(engine.seats) if public else [seat],
            revealed_to=list(engine.seats) if public else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
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
    def creature(engine, seat: str, name: str, power: int | str):
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": "Token Creature — Robot",
                "power": str(power),
                "toughness": "4",
            },
            reason="Station rules fixture",
        )[0]
        return engine._resolve_object(seat, ref)

    @staticmethod
    def prepare_priority(
        session,
        *,
        seat: str = "A",
        phase: str = "precombat_main",
        step: str = "main",
    ):
        engine = session.engine
        engine.state.active_player = seat
        engine.state.started = True
        engine.state.phase = phase
        engine.state.step = step
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority(seat)
        engine.pump()

    def prepare(
        self,
        session,
        *,
        power: int | str = 2,
        candidate_card: str | None = None,
        phase: str = "precombat_main",
        step: str = "main",
    ):
        source = self.add_card(
            session,
            seat="A",
            name="Wedgelight Rammer",
            ref=f"A-station-{session.engine.state.config.seed}",
        )
        creature = (
            self.add_card(
                session,
                seat="A",
                name=candidate_card,
                ref=f"A-station-worker-{session.engine.state.config.seed}",
                register=False,
            )
            if candidate_card is not None
            else self.creature(
                session.engine,
                "A",
                f"Station Worker {session.engine.state.config.seed}",
                power,
            )
        )
        self.prepare_priority(session, phase=phase, step=step)
        abilities = [
            ability
            for ability in session.engine._activated_abilities(source)
            if station_cost_choice(ability) is not None
        ]
        self.assertEqual(1, len(abilities))
        ability = abilities[0]
        self.assertEqual("ab2", ability.ability_id)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        return source, creature, ability, action_id

    @staticmethod
    def pass_until(session, predicate, *, limit: int = 48):
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Station resolution stopped without a decision")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Station resolution did not converge")

    @staticmethod
    def choose_replacements(session, *, limit: int = 8):
        for _ in range(limit):
            decision = session.state.pending_decision
            if decision is None or decision.kind != "replacement.order":
                return
            seat = decision.actors[0]
            packet = StateProjector(
                session.engine.card_db,
                session.state,
            )._decision(f"pilot:{seat}")
            assert packet is not None
            selected = packet["ctx"]["options"][0]["id"]
            result = session.act(
                f"pilot:{seat}",
                {"a": "choose", "replacement": selected},
            )
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Station replacement sequence did not converge")

    def test_station_taps_exact_creature_and_places_current_power(self):
        session = self.session(70218401)
        engine = session.engine
        source, creature, _ability, action_id = self.prepare(session, power=2)
        self.assertTrue(is_summoning_sick(engine, creature))
        action = next(
            row
            for row in session.packet("pilot:A", full=True)["decision"]["ctx"][
                "legal"
            ]["actions"]
            if row["id"] == action_id
        )
        self.assertEqual(
            [creature.ref],
            action["cost_summary"]["choose_cost"][0]["legal_refs"],
        )

        result = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [creature.ref]},
        )

        self.assertTrue(result.ok, result.summary)
        self.assertTrue(creature.tapped)
        item = engine.state.stack[-1]
        self.assertEqual([creature.ref], item.context["cost_objects"])
        self.assertEqual(
            creature.logical_object_id,
            item.context["station"]["logical_object_id"],
        )
        self.pass_until(session, lambda: not engine.state.stack)
        self.assertEqual(2, source.counters.get("charge", 0))

    def test_station_is_available_only_at_sorcery_timing(self):
        session = self.session(70218402)
        source, creature, _ability, action_id = self.prepare(
            session,
            phase="combat",
            step="declare_attackers",
        )
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"][
            "legal"
        ]["actions"]
        self.assertNotIn(action_id, {row["id"] for row in actions})
        self.assertFalse(source.tapped)
        self.assertFalse(creature.tapped)

    def test_station_rejects_invalid_cost_objects_and_rolls_back(self):
        session = self.session(70218403, players=4)
        engine = session.engine
        source, creature, _ability, action_id = self.prepare(session, power=2)
        opposing = self.creature(engine, "B", "Opposing Worker", 4)

        for response in (
            {"cost_cards": [source.ref]},
            {"cost_cards": [opposing.ref]},
            {
                "cost_cards": [creature.ref],
                "cost_objects": [creature.ref],
            },
        ):
            with self.subTest(response=response):
                with self.assertRaises(StationAbilityError):
                    prepare_station_cost(
                        engine,
                        seat="A",
                        source=source,
                        response=response,
                    )

        before = authoritative_state_hash(engine.state)
        result = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [opposing.ref]},
        )
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertFalse(creature.tapped)
        self.assertFalse(opposing.tapped)
        self.assertFalse(engine.state.stack)

        plan = prepare_station_cost(
            engine,
            seat="A",
            source=source,
            response={"cost_cards": [creature.ref]},
        )
        set_permanent_tapped(
            engine,
            creature.ref,
            actor="A",
            tapped=True,
            reason="Station stale-cost fixture",
            logical_object_id=creature.logical_object_id,
            log=False,
        )
        with self.assertRaisesRegex(StationAbilityError, "changed"):
            commit_station_cost(engine, plan)

    def test_unresolved_station_power_fails_closed(self):
        session = self.session(70218404)
        engine = session.engine
        source = self.add_card(
            session,
            seat="A",
            name="Wedgelight Rammer",
            ref="A-station-unresolved",
        )
        self.creature(engine, "A", "Unresolved Worker", "*")
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(StationAbilityError, "unresolved"):
            station_candidates(engine, "A", source)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        changed = self.session(70218405)
        source, creature, _ability, action_id = self.prepare(changed, power=3)
        result = changed.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [creature.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        creature.annotations.setdefault("copy_overrides", {})[
            "type_line"
        ] = "Artifact"
        with self.assertRaisesRegex(StationAbilityError, "type changes"):
            station_resolution_power(changed.engine, changed.engine.state.stack[-1])
        self.assertEqual(0, source.counters.get("charge", 0))

    def test_station_uses_resolution_and_last_known_power(self):
        current = self.session(70218406)
        engine = current.engine
        source, creature, _ability, action_id = self.prepare(current, power=2)
        result = current.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [creature.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        commit_counter_changes(
            engine,
            plan_counter_changes(
                engine,
                (
                    CounterChange(
                        "permanent",
                        creature.object_id,
                        "+1/+1",
                        1,
                        expected_zone="battlefield",
                        expected_logical_object_id=creature.logical_object_id,
                    ),
                ),
            ),
        )
        self.pass_until(current, lambda: not engine.state.stack)
        self.assertEqual(3, source.counters.get("charge", 0))

        departed = self.session(70218407)
        engine = departed.engine
        source, creature, _ability, action_id = self.prepare(
            departed,
            candidate_card="Beastbreaker of Bala Ged",
        )
        result = departed.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [creature.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        original_logical = creature.logical_object_id
        engine.move_card(creature.object_id, "graveyard", log=False)
        self.assertEqual(
            2,
            engine.state.stack[-1].context["station"]["last_known_power"],
        )
        engine.move_card(
            creature.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertNotEqual(original_logical, creature.logical_object_id)
        commit_counter_changes(
            engine,
            plan_counter_changes(
                engine,
                (
                    CounterChange(
                        "permanent",
                        creature.object_id,
                        "+1/+1",
                        4,
                        expected_zone="battlefield",
                        expected_logical_object_id=creature.logical_object_id,
                    ),
                ),
            ),
        )
        self.pass_until(departed, lambda: not engine.state.stack)
        self.assertEqual(2, source.counters.get("charge", 0))

        negative = self.session(70218408)
        engine = negative.engine
        source, creature, _ability, action_id = self.prepare(negative, power=-2)
        result = negative.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [creature.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        self.pass_until(negative, lambda: not engine.state.stack)
        self.assertEqual(0, source.counters.get("charge", 0))

    def test_station_source_departure_skips_returned_object(self):
        session = self.session(70218409)
        engine = session.engine
        source, creature, _ability, action_id = self.prepare(session, power=3)
        result = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [creature.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        original_logical = source.logical_object_id
        engine.move_card(source.object_id, "graveyard", log=False)
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertNotEqual(original_logical, source.logical_object_id)

        self.pass_until(session, lambda: not engine.state.stack)

        self.assertEqual(0, source.counters.get("charge", 0))

    def test_current_effective_station_ability_query_hides_removed_activation(self):
        session = self.session(70218410)
        engine = session.engine
        source, _creature, _ability, _action_id = self.prepare(session, power=2)
        original_data = engine._effective_card_data

        def removed(card, **kwargs):
            data = dict(original_data(card, **kwargs))
            if getattr(card, "object_id", card) == source.object_id:
                data["activated_abilities"] = []
            return data

        with mock.patch.object(
            engine,
            "_effective_card_data",
            side_effect=removed,
        ), mock.patch(
            "quorune.compiler.activated_ability_catalog.parse_activated_abilities",
            side_effect=AssertionError("runtime reparsed Station Oracle text"),
        ):
            abilities = engine._activated_abilities(source)

        self.assertFalse(
            any(station_cost_choice(ability) is not None for ability in abilities)
        )

    def test_station_lki_capture_rolls_back_before_zone_mutation(self):
        session = self.session(70218411)
        engine = session.engine
        source, creature, _ability, action_id = self.prepare(session, power=2)
        result = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [creature.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        item = engine.state.stack[-1]
        item.context["station"] = {
            **item.context["station"],
            "unknown": True,
        }
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(StateInvariantError, "unknown"):
            engine.move_card(creature.object_id, "graveyard", log=False)

        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("battlefield", creature.zone)

    def test_station_quantity_replacement_suspends_and_replays(self):
        session = self.session(70218412, players=4)
        engine = session.engine
        source, creature, _ability, action_id = self.prepare(session, power=2)
        self.add_card(
            session,
            seat="A",
            name="Doubling Season",
            ref="A-station-doubling",
        )
        self.add_card(
            session,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref="A-station-doc",
        )
        self.prepare_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [creature.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        self.pass_until(
            session,
            lambda: session.state.pending_decision is not None
            and session.state.pending_decision.kind == "replacement.order",
        )
        projector = StateProjector(self.db, engine.state)
        self.assertIsNotNone(projector._decision("pilot:A"))
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        self.choose_replacements(session)
        self.pass_until(session, lambda: not engine.state.stack)
        self.assertGreater(source.counters.get("charge", 0), 2)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "station-counter-replacement"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)

        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
