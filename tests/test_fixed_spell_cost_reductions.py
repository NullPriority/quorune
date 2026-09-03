from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.cast_cost_modifier_templates import (
    self_spell_cost_reduction_handler,
    static_fixed_spell_cost_reduction_handler,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.self_cast_reductions import CastReductionQueryScope
from quorune.semantic_runtime.cast_costs import (
    FIXED_SPELL_COST_REDUCTION_CAPABILITY_ID,
    FIXED_SPELL_COST_REDUCTION_EVENT,
    FIXED_SPELL_COST_REDUCTION_HANDLER_ID,
    FixedSpellCostReductionHandler,
    SELF_SPELL_COST_REDUCTION_HANDLER_ID,
    SelfSpellCostReductionHandler,
)
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database


FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "fixed-spell-cost-reduction-cards.json"
)
SELF_REDUCTION_FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "self-spell-cost-reduction-cards.json"
)


def _record(text: str, suffix: int) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name="Fixed Spell Cost Fixture",
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
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedSpellCostReductionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()

    def compile(self, text: str, suffix: int):
        return compile_oracle_card(
            _record(text, suffix),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_spell_reducers_compile_typed_query_descriptors(self):
        cases = (
            (
                "Instant and sorcery spells you cast cost {1} less to cast.",
                {"types_any": ["instant", "sorcery"]},
            ),
            (
                "White spells and black spells you cast cost {1} less to cast.",
                {"colors_any": ["B", "W"]},
            ),
            (
                "Cleric, Rogue, Warrior, and Wizard spells you cast cost {1} less to cast.",
                {"subtypes_any": ["cleric", "rogue", "warrior", "wizard"]},
            ),
            (
                "Colorless Eldrazi spells you cast cost {2} less to cast.",
                {"colorless": True, "subtypes_all": ["eldrazi"]},
            ),
            (
                "Noncreature spells you cast cost {1} less to cast.",
                {"excluded_types": ["creature"]},
            ),
            (
                "Spells you cast cost {1} less to cast.",
                {},
            ),
        )
        for index, (text, expected) in enumerate(cases, 1):
            with self.subTest(text=text):
                ir = self.compile(text, 601_500_000 + index)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(
                    "fixed-query-spell-cost-reduction-v1",
                    node.template_id,
                )
                self.assertEqual(FIXED_SPELL_COST_REDUCTION_EVENT, node.event)
                self.assertEqual(
                    (FIXED_SPELL_COST_REDUCTION_CAPABILITY_ID,),
                    node.capability_dependencies,
                )
                self.assertEqual(("static_ability",), node.runtime_coverage)
                descriptor = node.handlers[0]
                self.assertEqual(
                    FIXED_SPELL_COST_REDUCTION_HANDLER_ID,
                    descriptor["handler_id"],
                )
                for field, value in expected.items():
                    self.assertEqual(value, descriptor["predicate"][field])

    def test_unsupported_spell_reduction_grammar_remains_residual(self):
        unsupported = (
            "This spell costs {2} less to cast if it targets a tapped creature.",
            "This spell costs {1} less to cast for each card you've drawn this turn.",
            "This spell costs {X} less to cast, where X is the total power of creatures you control.",
            "This spell costs {1} less to cast for each card with an Adventure in your graveyard.",
            "The first creature spell you cast each turn costs {2} less to cast.",
            "Spells you cast of the chosen type cost {1} less to cast.",
            "Creature spells with flying you cast cost {1} less to cast.",
            "Spells you cast from your graveyard cost {1} less to cast.",
            "Spells your opponents cast cost {1} less to cast.",
            "Spells you cast cost {W} less to cast.",
            "Historic spells you cast cost {1} less to cast.",
        )
        for index, text in enumerate(unsupported, 1):
            with self.subTest(text=text):
                ir = self.compile(text, 601_501_000 + index)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertFalse(
                    any(
                        node.event == FIXED_SPELL_COST_REDUCTION_EVENT
                        for node in ir.faces[0].nodes
                    )
                )

    def test_self_reducers_compile_closed_public_metrics(self):
        cases = (
            (
                "This spell costs {2} less to cast if you control a Wizard.",
                "fixed_public_threshold",
                {"GENERIC": 2},
            ),
            (
                "This spell costs {1} less to cast for each creature card in your graveyard.",
                "object_count",
                {"GENERIC": 1},
            ),
            (
                "This spell costs {X} less to cast, where X is the total mana value of noncreature artifacts you control.",
                "total_mana_value",
                {"GENERIC": 1},
            ),
            (
                "This spell costs {3} less to cast if a creature died this turn.",
                "turn_fact",
                {"GENERIC": 3},
            ),
            (
                "This spell costs {X} less to cast, where X is your devotion to blue. (Each {U} in the mana costs of permanents you control counts toward your devotion to blue.)",
                "devotion",
                {"GENERIC": 1},
            ),
            (
                "This spell costs {G} less to cast for each green creature you control.",
                "object_count",
                {"G": 1},
            ),
        )
        for index, (text, metric, reduction) in enumerate(cases, 1):
            with self.subTest(text=text):
                compiled = self_spell_cost_reduction_handler(text)
                self.assertIsNotNone(compiled)
                ir = self.compile(text, 601_502_000 + index)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual("self-spell-cost-public-reduction-v1", node.template_id)
                self.assertEqual("all", node.active_zone)
                descriptor = node.handlers[0]
                term = descriptor["reduction"]["terms"][0]
                self.assertEqual(metric, term["metric"]["kind"])
                self.assertEqual(reduction, term["reduction"])

    def test_self_reduction_compiler_preserves_opponent_quantification(self):
        cases = (
            (
                "This spell costs {2} less to cast if an opponent has three or more creature cards in their graveyard.",
                CastReductionQueryScope.ANY_OPPONENT,
            ),
            (
                "This spell costs {2} less to cast if an opponent controls no basic lands.",
                CastReductionQueryScope.ANY_OPPONENT,
            ),
            (
                "This spell costs {2} less to cast if your opponents control three or more creatures.",
                CastReductionQueryScope.OPPONENTS_COMBINED,
            ),
        )
        for index, (text, expected_scope) in enumerate(cases, 1):
            with self.subTest(text=text):
                compiled = self_spell_cost_reduction_handler(text)
                self.assertIsNotNone(compiled)
                assert compiled is not None
                descriptor = compiled[1]
                scope = descriptor["reduction"]["terms"][0]["metric"][
                    "queries"
                ][0]["scope"]
                self.assertEqual(expected_scope.value, scope)
                ir = self.compile(text, 601_503_000 + index)
                self.assertEqual("exact", ir.status, ir.material_residuals)

        legacy_compiled = self_spell_cost_reduction_handler(
            "This spell costs {2} less to cast if your opponents control "
            "three or more creatures."
        )
        self.assertIsNotNone(legacy_compiled)
        assert legacy_compiled is not None
        legacy = json.loads(json.dumps(legacy_compiled[1]))
        legacy["reduction"]["terms"][0]["metric"]["queries"][0][
            "scope"
        ] = CastReductionQueryScope.OPPONENT_ZONES.value
        specification = SelfSpellCostReductionHandler().validate(legacy)
        self.assertEqual(
            CastReductionQueryScope.OPPONENT_ZONES,
            specification.terms[0].metric.queries[0].scope,
        )

    def test_self_reduction_descriptor_fails_closed(self):
        compiled = self_spell_cost_reduction_handler(
            "This spell costs {2} less to cast if you control a Wizard."
        )
        self.assertIsNotNone(compiled)
        assert compiled is not None
        descriptor = dict(compiled[1])
        self.assertEqual(
            SELF_SPELL_COST_REDUCTION_HANDLER_ID,
            descriptor["handler_id"],
        )
        SelfSpellCostReductionHandler().validate(descriptor)
        malformed = (
            {**descriptor, "schema_version": True},
            {**descriptor, "event": "resolve"},
            {**descriptor, "unknown": True},
            {
                **descriptor,
                "reduction": {
                    **descriptor["reduction"],
                    "terms": [],
                },
            },
            {
                **descriptor,
                "reduction": {
                    **descriptor["reduction"],
                    "terms": [
                        {
                            **descriptor["reduction"]["terms"][0],
                            "reduction": {"GENERIC": -1},
                        }
                    ],
                },
            },
            {
                **descriptor,
                "reduction": {
                    **descriptor["reduction"],
                    "terms": [
                        {
                            **descriptor["reduction"]["terms"][0],
                            "metric": {
                                **descriptor["reduction"]["terms"][0][
                                    "metric"
                                ],
                                "queries": [
                                    {
                                        **descriptor["reduction"]["terms"][0][
                                            "metric"
                                        ]["queries"][0],
                                        "scope": "everybody",
                                    }
                                ],
                            },
                        }
                    ],
                },
            },
            {
                **descriptor,
                "reduction": {
                    **descriptor["reduction"],
                    "terms": [
                        {
                            **descriptor["reduction"]["terms"][0],
                            "metric": {
                                **descriptor["reduction"]["terms"][0][
                                    "metric"
                                ],
                                "kind": "object_count",
                                "minimum": None,
                                "queries": [
                                    {
                                        **descriptor["reduction"]["terms"][0][
                                            "metric"
                                        ]["queries"][0],
                                        "scope": (
                                            CastReductionQueryScope.ANY_OPPONENT.value
                                        ),
                                    }
                                ],
                            },
                        }
                    ],
                },
            },
        )
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(SemanticNodeError):
                    SelfSpellCostReductionHandler().validate(value)

    def test_fixed_spell_cost_descriptor_is_strict(self):
        compiled = static_fixed_spell_cost_reduction_handler(
            "Creature spells you cast cost {2} less to cast."
        )
        self.assertIsNotNone(compiled)
        assert compiled is not None
        descriptor = dict(compiled[1])
        spec = FixedSpellCostReductionHandler().validate(descriptor)
        self.assertEqual(2, spec.generic_reduction)
        self.assertEqual(("creature",), spec.predicate.types_all)

        malformed = (
            {**descriptor, "generic_reduction": True},
            {**descriptor, "schema_version": True},
            {**descriptor, "affected_controller": "opponent"},
            {**descriptor, "unknown": True},
            {
                **descriptor,
                "predicate": {
                    **descriptor["predicate"],
                    "keywords_all": ["flying"],
                },
            },
        )
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(SemanticNodeError):
                    FixedSpellCostReductionHandler().validate(value)


class FixedSpellCostReductionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-spell-costs.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT / "tests" / "fixtures" / "bestow-cards.json",
                ROOT / "tests" / "fixtures" / "kicker-rules-cards.json",
                FIXTURE_PATH,
                SELF_REDUCTION_FIXTURE_PATH,
            ],
            database,
        )
        cls.db = CardDatabase(database)
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
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        session,
        *,
        name: str,
        ref: str,
        seat: str = "B",
        zone: str = "hand",
        controller: str | None = None,
    ):
        engine = session.engine
        record = self.db.lookup(name)
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
            known_to=list(engine.seats) if zone != "hand" else [seat],
            revealed_to=list(engine.seats) if zone != "hand" else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        return card

    @staticmethod
    def prepare_main(session, seat: str = "B") -> None:
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
    def cast_action(engine, card, seat: str = "B"):
        return next(
            action
            for action in engine._priority_action_hints(seat)["actions"]
            if action.get("card") == card.ref and action.get("action") == "cast"
        )

    @classmethod
    def cost_option(cls, engine, card, option_id: str = "normal"):
        action = cls.cast_action(engine, card)
        return next(
            option
            for option in action["cost_options"]
            if option["id"] == option_id
        )

    @staticmethod
    def resolve_stack_with_passes(session) -> None:
        for _ in range(12):
            if not session.engine.state.stack:
                return
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Reduced spell did not resolve")

    def test_runtime_cost_reductions_stack_and_floor_generic(self):
        session = self.session(601_510_001)
        engine = session.engine
        for index in range(3):
            self.add_card(
                session,
                name="Goblin Electromancer",
                ref=f"REDUCE-{index}",
                zone="battlefield",
            )
        spell = self.add_card(session, name="Divination", ref="DIVINATION")
        engine.state.players["B"].mana_pool["U"] = 1
        self.prepare_main(session)

        option = self.cost_option(engine, spell)
        self.assertEqual(0, option["requirements"]["GENERIC"])
        self.assertEqual(1, option["requirements"]["U"])

    def test_self_reduction_offer_and_commit_share_public_query(self):
        session = self.session(601_520_001)
        engine = session.engine
        counted = self.add_card(
            session,
            name="Birds of Paradise",
            ref="COUNTED-CREATURE",
            zone="graveyard",
        )
        spell = self.add_card(
            session,
            name="Public Census Spell Fixture",
            ref="CENSUS-SPELL",
        )
        engine.state.players["B"].mana_pool.update({"C": 5, "U": 1})
        self.prepare_main(session)

        action = self.cast_action(engine, spell)
        option = next(value for value in action["cost_options"] if value["id"] == "normal")
        self.assertEqual(5, option["requirements"]["GENERIC"])
        result = session.act("pilot:B", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", spell.zone)
        self.assertEqual("graveyard", counted.zone)

    def test_any_opponent_graveyard_threshold_is_not_combined_across_opponents(
        self,
    ):
        session = self.session(601_520_007, players=4)
        engine = session.engine
        for seat in ("A", "C", "D"):
            self.add_card(
                session,
                name="Birds of Paradise",
                ref=f"SEPARATE-GRAVEYARD-{seat}",
                seat=seat,
                zone="graveyard",
            )
        spell = self.add_card(
            session,
            name="Any Opponent Graveyard Threshold Spell Fixture",
            ref="ANY-OPPONENT-GRAVEYARD-SPELL",
        )
        engine.state.players["B"].mana_pool.update({"C": 4, "U": 1})
        self.prepare_main(session)

        action = self.cast_action(engine, spell)
        option = next(
            value for value in action["cost_options"] if value["id"] == "normal"
        )
        self.assertEqual(4, option["requirements"]["GENERIC"])
        result = session.act("pilot:B", {"action_id": action["id"]})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", spell.zone)
        self.assertEqual(0, engine.state.players["B"].mana_pool.get("C", 0))

    def test_any_opponent_zero_basic_land_condition_is_evaluated_per_opponent(
        self,
    ):
        session = self.session(601_520_008, players=4)
        engine = session.engine
        for seat in ("A", "C"):
            self.add_card(
                session,
                name="Forest",
                ref=f"BASIC-LAND-{seat}",
                seat=seat,
                zone="battlefield",
            )
        spell = self.add_card(
            session,
            name="Any Opponent Basic Land Absence Spell Fixture",
            ref="ANY-OPPONENT-NO-BASIC-SPELL",
        )
        engine.state.players["B"].mana_pool.update({"C": 4, "U": 1})
        self.prepare_main(session)

        action = self.cast_action(engine, spell)
        option = next(
            value for value in action["cost_options"] if value["id"] == "normal"
        )
        self.assertEqual(2, option["requirements"]["GENERIC"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:B", {"action_id": action["id"]})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", spell.zone)
        self.assertEqual(2, engine.state.players["B"].mana_pool.get("C", 0))
        self.resolve_stack_with_passes(session)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "any-opponent-zero-basic-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_combined_opponents_threshold_still_aggregates_when_oracle_wording_requires_it(
        self,
    ):
        session = self.session(601_520_009, players=4)
        engine = session.engine
        for seat in ("A", "C", "D"):
            self.add_card(
                session,
                name="Birds of Paradise",
                ref=f"COMBINED-BATTLEFIELD-{seat}",
                seat=seat,
                zone="battlefield",
            )
        spell = self.add_card(
            session,
            name="Combined Opponents Threshold Spell Fixture",
            ref="COMBINED-OPPONENTS-SPELL",
        )
        engine.state.players["B"].mana_pool.update({"C": 4, "U": 1})
        self.prepare_main(session)

        action = self.cast_action(engine, spell)
        option = next(
            value for value in action["cost_options"] if value["id"] == "normal"
        )
        self.assertEqual(2, option["requirements"]["GENERIC"])
        result = session.act("pilot:B", {"action_id": action["id"]})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", spell.zone)
        self.assertEqual(2, engine.state.players["B"].mana_pool.get("C", 0))

    def test_multiple_qualifying_opponents_apply_one_fixed_reduction(self):
        session = self.session(601_520_010, players=4)
        engine = session.engine
        for seat in ("A", "C"):
            for index in range(3):
                self.add_card(
                    session,
                    name="Birds of Paradise",
                    ref=f"QUALIFYING-GRAVEYARD-{seat}-{index}",
                    seat=seat,
                    zone="graveyard",
                )
        spell = self.add_card(
            session,
            name="Any Opponent Graveyard Threshold Spell Fixture",
            ref="MULTIPLE-QUALIFYING-OPPONENTS-SPELL",
        )
        engine.state.players["B"].mana_pool.update({"C": 4, "U": 1})
        self.prepare_main(session)

        action = self.cast_action(engine, spell)
        option = next(
            value for value in action["cost_options"] if value["id"] == "normal"
        )
        self.assertEqual(2, option["requirements"]["GENERIC"])
        result = session.act("pilot:B", {"action_id": action["id"]})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", spell.zone)
        self.assertEqual(2, engine.state.players["B"].mana_pool.get("C", 0))

    def test_self_reduction_recomputes_current_control_and_rejects_stale_offer(self):
        session = self.session(601_520_002, players=4)
        engine = session.engine
        wizard = self.add_card(
            session,
            name="Public Wizard Fixture",
            ref="THRESHOLD-WIZARD",
            zone="battlefield",
        )
        spell = self.add_card(
            session,
            name="Public Threshold Spell Fixture",
            ref="THRESHOLD-SPELL",
        )
        engine.state.players["B"].mana_pool.update({"C": 2, "U": 1})
        self.prepare_main(session)

        action = self.cast_action(engine, spell)
        option = next(value for value in action["cost_options"] if value["id"] == "normal")
        self.assertEqual(2, option["requirements"]["GENERIC"])
        engine.change_control(
            wizard.object_id,
            "A",
            reason="self-reduction stale-offer fixture",
        )
        before = authoritative_state_hash(engine.state)
        result = session.act("pilot:B", {"action_id": action["id"]})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("hand", spell.zone)

    def test_self_reduction_uses_mana_value_devotion_history_and_colored_floor(self):
        session = self.session(601_520_003)
        engine = session.engine
        artifact = self.add_card(
            session,
            name="Arcane Signet",
            ref="MANA-VALUE-ARTIFACT",
            zone="battlefield",
        )
        artifact.annotations["copy_overrides"] = {
            "type_line": "Artifact",
            "mana_value": 4,
            "mana_cost": "{2}{U}{U}",
        }
        green_one = self.add_card(
            session,
            name="Birds of Paradise",
            ref="GREEN-ONE",
            zone="battlefield",
        )
        green_two = self.add_card(
            session,
            name="Birds of Paradise",
            ref="GREEN-TWO",
            zone="battlefield",
        )
        mana_value_spell = self.add_card(
            session,
            name="Public Mana Value Spell Fixture",
            ref="MANA-VALUE-SPELL",
        )
        devotion_spell = self.add_card(
            session,
            name="Public Devotion Spell Fixture",
            ref="DEVOTION-SPELL",
        )
        history_spell = self.add_card(
            session,
            name="Public History Spell Fixture",
            ref="HISTORY-SPELL",
        )
        colored_spell = self.add_card(
            session,
            name="Colored Census Spell Fixture",
            ref="COLORED-SPELL",
        )
        engine._record_turn_history("creature_died", actor="A")
        engine.state.players["B"].mana_pool.update({"C": 30, "G": 2, "U": 4})
        self.prepare_main(session)

        self.assertEqual(4, self.cost_option(engine, mana_value_spell)["requirements"]["GENERIC"])
        self.assertEqual(5, self.cost_option(engine, devotion_spell)["requirements"]["GENERIC"])
        self.assertEqual(2, self.cost_option(engine, history_spell)["requirements"]["GENERIC"])
        colored = self.cost_option(engine, colored_spell)
        self.assertEqual(0, colored["requirements"]["G"])
        self.assertEqual(3, colored["requirements"]["GENERIC"])
        self.assertEqual("B", green_one.controller)
        self.assertEqual("B", green_two.controller)

    def test_self_and_source_reductions_share_total_cost_pipeline(self):
        session = self.session(601_520_006)
        engine = session.engine
        self.add_card(
            session,
            name="Goblin Electromancer",
            ref="EXTERNAL-REDUCER",
            zone="battlefield",
        )
        self.add_card(
            session,
            name="Birds of Paradise",
            ref="SELF-COUNTED-CREATURE",
            zone="graveyard",
        )
        spell = self.add_card(
            session,
            name="Public Census Spell Fixture",
            ref="STACKED-REDUCTION-SPELL",
        )
        engine.state.players["B"].mana_pool.update({"C": 4, "U": 1})
        self.prepare_main(session)

        option = self.cost_option(engine, spell)

        self.assertEqual(4, option["requirements"]["GENERIC"])

    def test_self_reduction_is_private_multiplayer_and_replays(self):
        session = self.session(601_520_004, players=4)
        engine = session.engine
        self.add_card(
            session,
            name="Birds of Paradise",
            ref="PRIVATE-COUNTED-CREATURE",
            zone="graveyard",
        )
        spell = self.add_card(
            session,
            name="Public Census Spell Fixture",
            ref="PRIVATE-CENSUS-SPELL",
        )
        engine.state.players["B"].mana_pool.update({"C": 5, "U": 1})
        self.prepare_main(session)
        action = self.cast_action(engine, spell)
        self.assertFalse(
            any(
                row.get("card") == spell.ref
                for row in engine._priority_action_hints("A")["actions"]
            )
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:B", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.resolve_stack_with_passes(session)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "self-cost-reduction-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_self_reduction_compiler_and_runtime_mutants_are_killed(self):
        record = self.db.lookup("Public Threshold Spell Fixture", fuzzy=False)
        with mock.patch(
            "quorune.compiler.runtime_templates.self_spell_cost_reduction_handler",
            return_value=None,
        ):
            ir = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertTrue(ir.material_residuals)

        session = self.session(601_520_005)
        engine = session.engine
        self.add_card(
            session,
            name="Public Wizard Fixture",
            ref="MUTANT-WIZARD",
            zone="battlefield",
        )
        spell = self.add_card(
            session,
            name="Public Threshold Spell Fixture",
            ref="MUTANT-SELF-SPELL",
        )
        engine.state.players["B"].mana_pool.update({"C": 4, "U": 1})
        self.prepare_main(session)
        self.assertEqual(2, self.cost_option(engine, spell)["requirements"]["GENERIC"])
        with mock.patch(
            "quorune.rules.casting.costs.compiled_self_spell_cost_reduction_specs",
            return_value=(),
        ):
            self.assertEqual(4, self.cost_option(engine, spell)["requirements"]["GENERIC"])

    def test_reduction_applies_to_kicker_total_before_payment(self):
        session = self.session(601_510_002)
        engine = session.engine
        self.add_card(
            session,
            name="Emerald Medallion",
            ref="EMERALD",
            zone="battlefield",
        )
        spell = self.add_card(session, name="Kavu Titan", ref="KAVU")
        engine.state.players["B"].mana_pool.update({"C": 2, "G": 2})
        self.prepare_main(session)

        normal = self.cost_option(engine, spell)
        kicked = self.cost_option(engine, spell, "kicked")
        self.assertEqual(0, normal["requirements"]["GENERIC"])
        self.assertEqual(2, kicked["requirements"]["GENERIC"])
        self.assertEqual(2, kicked["requirements"]["G"])

    def test_fixed_query_reduction_applies_before_convoke_payment(self):
        session = self.session(601_510_008)
        engine = session.engine
        source = self.add_card(
            session,
            name="Goblin Electromancer",
            ref="CONVOKE-REDUCER",
            zone="battlefield",
        )
        spell = self.add_card(
            session,
            name="Chord of Calling",
            ref="CONVOKE-SPELL",
        )
        engine.state.players["B"].mana_pool["G"] = 3
        self.prepare_main(session)

        options = engine._cast_cost_options(
            "B",
            spell,
            engine.semantics.get(f"{spell.oracle_id}:spell:front"),
            response={"x": 2, "convoke_cards": [source.ref]},
            hint=False,
        )

        self.assertEqual(1, len(options))
        self.assertEqual(0, options[0]["requirements"]["GENERIC"])
        self.assertEqual(3, options[0]["requirements"]["G"])
        self.assertEqual(1, options[0]["cost_reductions"][0]["count"])
        self.assertEqual(
            source.ref,
            options[0]["convoke_payment"]["contributions"][0]["candidate"][
                "ref"
            ],
        )

    def test_aura_reducer_uses_bestow_spell_characteristics(self):
        session = self.session(601_510_003)
        engine = session.engine
        self.add_card(
            session,
            name="Transcendent Envoy",
            ref="ENVOY",
            zone="battlefield",
        )
        self.add_card(
            session,
            name="Birds of Paradise",
            ref="TARGET",
            zone="battlefield",
        )
        spell = self.add_card(session, name="Leafcrown Dryad", ref="DRYAD")
        engine.state.players["B"].mana_pool.update({"C": 3, "G": 1})
        self.prepare_main(session)

        normal = self.cost_option(engine, spell)
        bestow = self.cost_option(engine, spell, "bestow")
        self.assertEqual(1, normal["requirements"]["GENERIC"])
        self.assertEqual(2, bestow["requirements"]["GENERIC"])

    def test_colorless_reducer_uses_devoid_spell_characteristics(self):
        session = self.session(601_510_004)
        engine = session.engine
        self.add_card(
            session,
            name="Herald of Kozilek",
            ref="HERALD-SOURCE",
            zone="battlefield",
        )
        spell = self.add_card(
            session,
            name="Herald of Kozilek",
            ref="HERALD-SPELL",
        )
        engine.state.players["B"].mana_pool.update({"U": 1, "R": 1})
        self.prepare_main(session)

        option = self.cost_option(engine, spell)
        self.assertEqual(0, option["requirements"]["GENERIC"])
        self.assertEqual(1, option["requirements"]["U"])
        self.assertEqual(1, option["requirements"]["R"])

    def test_stale_reducer_program_is_ignored(self):
        session = self.session(601_510_005)
        engine = session.engine
        self.add_card(
            session,
            name="Goblin Electromancer",
            ref="STALE-SOURCE",
            zone="battlefield",
        )
        spell = self.add_card(session, name="Divination", ref="STALE-SPELL")
        engine.state.players["B"].mana_pool.update({"C": 2, "U": 1})
        self.prepare_main(session)

        with mock.patch.object(
            type(engine),
            "semantic_program_is_current_trusted",
            return_value=False,
        ):
            option = self.cost_option(engine, spell)
        self.assertEqual(2, option["requirements"]["GENERIC"])
        self.assertNotIn("cost_reductions", option)

    def test_reduced_cast_is_seat_scoped_and_replays(self):
        session = self.session(601_510_006, players=4)
        engine = session.engine
        self.add_card(
            session,
            name="Goblin Electromancer",
            ref="REPLAY-SOURCE",
            zone="battlefield",
        )
        spell = self.add_card(session, name="Divination", ref="REPLAY-SPELL")
        engine.state.players["B"].mana_pool.update({"C": 1, "U": 1})
        self.prepare_main(session)
        action = self.cast_action(engine, spell)
        self.assertEqual(
            1,
            next(
                value
                for value in action["cost_options"]
                if value["id"] == "normal"
            )["requirements"]["GENERIC"],
        )
        self.assertFalse(
            any(
                row.get("card") == spell.ref
                for row in engine._priority_action_hints("A")["actions"]
            )
        )

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:B", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.resolve_stack_with_passes(session)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            self.assertNotIn(
                spell.object_id,
                json.dumps(session.packet(principal, full=True), sort_keys=True),
            )

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "fixed-spell-cost-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_compiler_and_runtime_reduction_mutants_are_killed(self):
        record = self.db.lookup("Goblin Electromancer")
        with mock.patch(
            "quorune.compiler.runtime_templates.static_fixed_spell_cost_reduction_handler",
            return_value=None,
        ):
            ir = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertTrue(ir.material_residuals)

        session = self.session(601_510_007)
        engine = session.engine
        self.add_card(
            session,
            name="Goblin Electromancer",
            ref="MUTANT-SOURCE",
            zone="battlefield",
        )
        spell = self.add_card(session, name="Divination", ref="MUTANT-SPELL")
        engine.state.players["B"].mana_pool.update({"C": 2, "U": 1})
        self.prepare_main(session)
        with mock.patch(
            "quorune.rules.casting.costs.active_fixed_spell_cost_reductions",
            return_value=(),
        ):
            option = self.cost_option(engine, spell)
        self.assertEqual(2, option["requirements"]["GENERIC"])


if __name__ == "__main__":
    unittest.main()
