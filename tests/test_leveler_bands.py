from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE,
    STATIC_COMPONENT_SCOPE_FRAGMENT_HANDLER_ID,
    StaticComponentSpec,
    StaticComponentScopeSpec,
    ability_fragment_from_dict,
    ability_fragment_to_dict,
    protection_specs,
)
from quorune.carddb import CardDatabase, CardRecord
from quorune.combat_damage_engine_adapter import EngineCombatDamageQuery
from quorune.combat_damage_projection import project_combat_damage_assignment
from quorune.continuous_effect_state import (
    commit_continuous_effect,
    expire_end_of_turn_continuous_effects,
)
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.deck import DeckLoader
from quorune.leveler_bands import (
    LEVELER_BANDS_CAPABILITY_ID,
    LEVELER_BANDS_HANDLER_ID,
    LevelerBandError,
    LevelerBandSpec,
    LevelerBandsSpec,
    leveler_bands_handler_descriptor,
)
from quorune.model import CardInstance, CombatState
from quorune.oracle_ir import (
    ORACLE_COMPILER_VERSION,
    compile_oracle_card,
    register_generated_programs,
)
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
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.continuous_components import (
    ContinuousEffectSourceContext,
    LevelerBandsHandler,
)
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
LEVELER_FIXTURE = ROOT / "tests" / "fixtures" / "leveler-bands-cards.json"


def leveler_record(
    oracle_text: str,
    *,
    name: str = "Leveler Fixture",
    oracle_id: str = "00000000-0000-4000-8000-000071100001",
    keywords: tuple[str, ...] = ("Level Up",),
    layout: str = "leveler",
    type_line: str = "Creature — Human Wizard",
) -> CardRecord:
    return CardRecord(
        oracle_id=oracle_id,
        name=name,
        mana_cost="{1}{U}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power="2",
        toughness="2",
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=keywords,
        produced_mana=(),
        layout=layout,
        released_at="2010-04-23",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


BEASTBREAKER_TEXT = (
    "Level up {2}{G} ({2}{G}: Put a level counter on this. Level up only "
    "as a sorcery.)\nLEVEL 1-3\n4/4\nLEVEL 4+\n6/6\nTrample"
)
BRIMSTONE_TEXT = (
    "Level up {3}{R} ({3}{R}: Put a level counter on this. Level up only "
    "as a sorcery.)\nLEVEL 1-2\n2/3\n{T}: This creature deals 1 damage "
    "to any target.\nLEVEL 3+\n2/4\n{T}: This creature deals 3 damage "
    "to any target."
)
CORALHELM_TEXT = (
    "Level up {1} ({1}: Put a level counter on this. Level up only as a "
    "sorcery.)\nLEVEL 2-3\n3/3\nFlying\nLEVEL 4+\n4/4\nFlying\nOther "
    "Merfolk creatures you control get +1/+1."
)


class LevelerBandModelAndCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.registry_value = json.loads(
            REGISTRY_PATH.read_text(encoding="utf-8")
        )

    def compile(self, record: CardRecord):
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_leveler_descriptor_is_strict_and_uses_typed_layer_boundaries(self):
        spec = LevelerBandsSpec(
            (
                LevelerBandSpec(
                    minimum_level=1,
                    maximum_level=2,
                    power=2,
                    toughness=3,
                    semantic_keys=("fixture:lower",),
                ),
                LevelerBandSpec(
                    minimum_level=3,
                    maximum_level=None,
                    power=2,
                    toughness=4,
                    keywords=("Flying",),
                    semantic_keys=("fixture:upper",),
                ),
            )
        )
        self.assertEqual(spec, LevelerBandsSpec.from_dict(spec.to_dict()))
        descriptor = leveler_bands_handler_descriptor(spec)
        self.assertEqual(LEVELER_BANDS_HANDLER_ID, descriptor["handler_id"])
        handler = LevelerBandsHandler()
        self.assertEqual(spec, handler.validate(descriptor))

        effects = handler.lower(
            descriptor,
            ContinuousEffectSourceContext(
                source_object_id="fixture:leveler",
                source_logical_object_id="logical:leveler",
                source_ref="L1",
                source_controller="A",
                source_timestamp=7,
                component_id="fixture:bands",
                source_counters={"level": 3},
            ),
        )
        self.assertEqual([Layer.ABILITY, Layer.POWER_TOUGHNESS], [
            effect.layer for effect in effects
        ])
        self.assertEqual("7b", effects[1].sublayer)
        self.assertEqual(
            (2, 4),
            effects[1].operations[0].value,
        )
        removed = [
            ability_fragment_from_dict(operation.value)
            for operation in effects[0].operations
            if operation.op == "remove_ability_fragment"
        ]
        self.assertIn(StaticComponentSpec("fixture:lower"), removed)
        self.assertNotIn(StaticComponentSpec("fixture:upper"), removed)
        self.assertTrue(
            any(
                operation.op == "add_ability"
                and operation.value == "Flying"
                for operation in effects[0].operations
            )
        )

        malformed = deepcopy(descriptor)
        malformed["bands"][1]["minimum_level"] = 4
        with self.assertRaisesRegex(
            SemanticNodeError, "finite range followed by its open range"
        ):
            handler.validate(malformed)
        malformed = deepcopy(descriptor)
        malformed["bands"][1]["keywords"] = "ab"
        with self.assertRaisesRegex(SemanticNodeError, "must be arrays"):
            handler.validate(malformed)
        scope = StaticComponentScopeSpec(
            parent_semantic_key="fixture:parent",
            child_semantic_keys=("fixture:child",),
            keywords=("Flying",),
        )
        self.assertEqual(
            scope,
            ability_fragment_from_dict(ability_fragment_to_dict(scope)),
        )
        with self.assertRaisesRegex(
            ValueError, "unique canonical keywords"
        ):
            StaticComponentScopeSpec(
                parent_semantic_key="fixture:parent",
                child_semantic_keys=("fixture:child",),
                keywords=("Flying", "flying"),
            )
        with self.assertRaisesRegex(ValueError, "canonical child keys"):
            StaticComponentScopeSpec(
                parent_semantic_key="fixture:parent",
                child_semantic_keys=("fixture:parent",),
                keywords=(),
            )
        with self.assertRaises(LevelerBandError):
            LevelerBandSpec(
                minimum_level=True,
                maximum_level=2,
                power=2,
                toughness=2,
            )

    def test_leveler_rows_compile_typed_bands_and_preserve_child_membership(self):
        records = (
            leveler_record(
                BEASTBREAKER_TEXT,
                name="Beastbreaker Fixture",
                oracle_id="00000000-0000-4000-8000-000071100011",
                keywords=("Level Up", "Trample"),
            ),
            leveler_record(
                BRIMSTONE_TEXT,
                name="Brimstone Fixture",
                oracle_id="00000000-0000-4000-8000-000071100012",
            ),
            leveler_record(
                CORALHELM_TEXT,
                name="Coralhelm Fixture",
                oracle_id="00000000-0000-4000-8000-000071100013",
                keywords=("Flying", "Level Up"),
                type_line="Creature — Merfolk Soldier",
            ),
        )
        expected_memberships = (1, 2, 3)
        expected_scopes = (("Trample",), (), ("Flying",))
        for record, expected_membership_count, expected_scope in zip(
            records, expected_memberships, expected_scopes, strict=True
        ):
            with self.subTest(name=record.name):
                ir = self.compile(record)
                self.assertEqual("exact", ir.status, ir.to_dict())
                self.assertEqual(ORACLE_COMPILER_VERSION, ir.compiler_version)
                band_nodes = [
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id
                    in {"leveler-bands-v1", "leveler-band-range-v1"}
                ]
                self.assertEqual(2, len(band_nodes))
                self.assertTrue(all(node.exact for node in band_nodes))
                self.assertTrue(
                    all(
                        record.oracle_text[
                            node.span.start : node.span.end
                        ]
                        == node.text
                        for node in band_nodes
                    )
                )
                descriptor = band_nodes[0].handlers[0]
                self.assertEqual(
                    LEVELER_BANDS_HANDLER_ID,
                    descriptor["handler_id"],
                )
                scope_descriptors = [
                    handler
                    for handler in band_nodes[0].handlers
                    if handler.get("handler_id")
                    == STATIC_COMPONENT_SCOPE_FRAGMENT_HANDLER_ID
                ]
                self.assertEqual(1, len(scope_descriptors))
                scope = scope_descriptors[0]["fragment"]["value"]
                self.assertEqual(list(expected_scope), scope["keywords"])
                band_keys = {
                    key
                    for band in descriptor["bands"]
                    for key in band["semantic_keys"]
                }
                member_nodes = [
                    node
                    for node in ir.faces[0].nodes
                    if CURRENT_ABILITY_FRAGMENT_COVERAGE
                    in node.runtime_coverage
                ]
                self.assertEqual(
                    expected_membership_count,
                    len(member_nodes),
                )
                self.assertEqual(
                    expected_membership_count,
                    len(band_keys),
                )
                self.assertEqual(
                    band_keys,
                    set(scope["child_semantic_keys"]),
                )
                self.assertTrue(
                    scope["parent_semantic_key"].startswith(
                        f"{record.oracle_id}:"
                    )
                )
                self.assertNotIn(scope["parent_semantic_key"], band_keys)
                self.assertTrue(
                    all(
                        key.startswith(f"{record.oracle_id}:")
                        for key in band_keys
                    )
                )
                for node in member_nodes:
                    self.assertIn(
                        LEVELER_BANDS_CAPABILITY_ID,
                        node.capability_dependencies,
                    )
                    self.assertIn(
                        CURRENT_ABILITY_FRAGMENT_COVERAGE,
                        node.runtime_coverage,
                    )
                level_up = ir.faces[0].nodes[0]
                self.assertIn("level up", level_up.mechanics)
                self.assertNotIn(
                    CURRENT_ABILITY_FRAGMENT_COVERAGE,
                    level_up.runtime_coverage,
                )

    def test_leveler_parser_rejects_malformed_ranges_and_open_children(self):
        malformed_texts = (
            BEASTBREAKER_TEXT.replace("Level up {2}{G}", "Level up {X}"),
            BEASTBREAKER_TEXT.replace("LEVEL 4+", "LEVEL 5+"),
            BEASTBREAKER_TEXT.replace("LEVEL 4+", "LEVEL 3+"),
            BEASTBREAKER_TEXT.replace("4/4", "*/4"),
            BEASTBREAKER_TEXT.replace(
                "LEVEL 4+\n6/6",
                "LEVEL 4-5\n6/6\nLEVEL 6+\n7/7",
            ),
            BEASTBREAKER_TEXT.replace(
                "\nLEVEL 1-3",
                "\nFlying\nLEVEL 1-3",
            ),
        )
        for index, text in enumerate(malformed_texts):
            with self.subTest(index=index):
                ir = self.compile(
                    leveler_record(
                        text,
                        oracle_id=(
                            "00000000-0000-4000-8000-0000711001"
                            f"{index:02d}"
                        ),
                        keywords=("Level Up", "Trample"),
                    )
                )
                self.assertNotEqual("exact", ir.status)
                self.assertFalse(
                    any(
                        node.template_id == "leveler-bands-v1"
                        for node in ir.faces[0].nodes
                    )
                )

        for layout, type_line in (
            ("class", "Enchantment — Class"),
            ("room", "Enchantment — Room"),
            ("prototype", "Artifact Creature — Construct"),
        ):
            with self.subTest(layout=layout):
                ir = self.compile(
                    leveler_record(
                        BEASTBREAKER_TEXT,
                        layout=layout,
                        type_line=type_line,
                        keywords=("Level Up", "Trample"),
                    )
                )
                self.assertFalse(
                    any(
                        node.template_id == "leveler-bands-v1"
                        for node in ir.faces[0].nodes
                    )
                )

        open_child = leveler_record(
            BEASTBREAKER_TEXT.replace(
                "6/6\nTrample",
                "6/6\nAt the beginning of each end step, take an extra turn.",
            ),
            keywords=("Level Up",),
        )
        ir = self.compile(open_child)
        self.assertEqual("partial", ir.status)
        self.assertEqual(2, sum(
            node.template_id
            in {"leveler-bands-v1", "leveler-band-range-v1"}
            and node.exact
            for node in ir.faces[0].nodes
        ))
        unsupported = next(
            node for node in ir.faces[0].nodes if not node.exact
        )
        self.assertNotIn(
            CURRENT_ABILITY_FRAGMENT_COVERAGE,
            unsupported.runtime_coverage,
        )

        unsupported_keyword = leveler_record(
            BEASTBREAKER_TEXT.replace("6/6\nTrample", "6/6\nWard—Pay 3 life."),
            keywords=("Level Up", "Ward"),
        )
        ir = self.compile(unsupported_keyword)
        self.assertEqual("partial", ir.status)
        scope = next(
            handler["fragment"]["value"]
            for node in ir.faces[0].nodes
            for handler in node.handlers
            if handler.get("handler_id")
            == STATIC_COMPONENT_SCOPE_FRAGMENT_HANDLER_ID
        )
        self.assertEqual(["Ward"], scope["keywords"])
        self.assertEqual([], scope["child_semantic_keys"])

        dynamic_child = leveler_record(
            BEASTBREAKER_TEXT.replace(
                "6/6\nTrample",
                "6/6\nThis creature gets +1/+1 for each artifact you control.",
            ),
            keywords=("Level Up",),
        )
        ir = self.compile(dynamic_child)
        self.assertEqual("partial", ir.status)
        dynamic = next(
            node
            for node in ir.faces[0].nodes
            if "for each artifact" in node.text
        )
        self.assertFalse(dynamic.exact)
        self.assertEqual((), dynamic.handlers)
        self.assertTrue(
            any(
                residual.kind == "unsupported_leveler_dynamic_child"
                for residual in ir.material_residuals
            )
        )

    def test_leveler_dependency_and_compiler_mutants_fail_closed(self):
        record = leveler_record(
            BEASTBREAKER_TEXT,
            keywords=("Level Up", "Trample"),
        )
        value = deepcopy(self.registry_value)
        capability = next(
            row
            for row in value["capabilities"]
            if row["id"] == LEVELER_BANDS_CAPABILITY_ID
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["focused mutation witness"]
        blocked = compile_oracle_card(
            record,
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertEqual("partial", blocked.status)
        self.assertTrue(
            all(
                not node.exact
                for node in blocked.faces[0].nodes
                if node.template_id
                in {"leveler-bands-v1", "leveler-band-range-v1"}
            )
        )

        def assert_band_compiles() -> None:
            compiled = self.compile(record)
            self.assertTrue(
                any(
                    node.template_id == "leveler-bands-v1"
                    for node in compiled.faces[0].nodes
                )
            )

        assert_band_compiles()
        with mock.patch(
            "quorune.compiler.leveler_context_nodes.parse_leveler_context",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_band_compiles()


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "leveler-bands.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-counter-keyword-activations.json",
            LEVELER_FIXTURE,
        ],
        database,
    )
    return CardDatabase(database)


class LevelerBandRuntimeTests(unittest.TestCase):
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
        name: str,
        ref: str,
        seat: str = "A",
        register: bool = True,
    ) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            zone_timestamp=engine._next_zone_timestamp(),
            acquired_control_turn_count=-1,
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
    def prepare_priority(session, *, seat: str = "A", mana: int = 8) -> None:
        engine = session.engine
        for symbol in ("B", "C", "G", "R", "U", "W"):
            engine.state.players[seat].mana_pool[symbol] = mana
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
    def pass_until(session, predicate, *, limit: int = 64) -> None:
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
    def choose_replacements(session, *, limit: int = 8) -> None:
        for _ in range(limit):
            decision = session.state.pending_decision
            if decision is None or decision.kind != "replacement.order":
                return
            seat = decision.actors[0]
            projected = StateProjector(
                session.engine.card_db, session.state
            )._decision(f"pilot:{seat}")
            assert projected is not None
            result = session.act(
                f"pilot:{seat}",
                {
                    "a": "choose",
                    "replacement": projected["ctx"]["options"][0]["id"],
                },
            )
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Replacement sequence did not converge")

    @staticmethod
    def damage_abilities(engine, card: CardInstance) -> set[int]:
        return {
            int(ability.effect_text.split(" deals ", 1)[1].split(" ", 1)[0])
            for ability in engine._activated_abilities(card)
            if " deals " in ability.effect_text
        }

    def test_level_counters_gate_power_keywords_static_and_activated_abilities(self):
        session = self.session(71101)
        engine = session.engine
        beast = self.add_card(
            session,
            name="Beastbreaker of Bala Ged",
            ref="A-leveler-beast",
        )
        mage = self.add_card(
            session,
            name="Brimstone Mage",
            ref="A-leveler-mage",
        )
        drinker = self.add_card(
            session,
            name="Hexdrinker",
            ref="A-leveler-protection",
        )

        for level, power, toughness, trample in (
            (0, 2, 2, False),
            (1, 4, 4, False),
            (3, 4, 4, False),
            (4, 6, 6, True),
        ):
            with self.subTest(level=level):
                beast.counters["level"] = level
                effective = engine._effective_card_data(beast)
                self.assertEqual(power, engine._numeric_stat(beast.object_id, "power"))
                self.assertEqual(
                    toughness,
                    engine._numeric_stat(beast.object_id, "toughness"),
                )
                self.assertEqual(trample, "Trample" in effective["keywords"])
                self.assertTrue(
                    any(
                        "level counter" in ability.effect_text
                        for ability in engine._activated_abilities(beast)
                    )
                )

        self.assertEqual(set(), self.damage_abilities(engine, mage))
        mage.counters["level"] = 1
        self.assertEqual({1}, self.damage_abilities(engine, mage))
        lower = next(
            ability
            for ability in engine._activated_abilities(mage)
            if "deals 1 damage" in ability.effect_text
        )
        self.prepare_priority(session)
        result = session.act(
            "pilot:A",
            {
                "action_id": f"activate:{mage.ref}:{lower.ability_id}",
                "targets": ["B"],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.pass_until(session, lambda: not engine.state.stack)
        self.assertEqual(39, engine.state.players["B"].life)
        mage.tapped = False
        mage.counters["level"] = 3
        self.assertEqual({3}, self.damage_abilities(engine, mage))
        self.prepare_priority(session)
        before = authoritative_state_hash(engine.state)
        result = session.act(
            "pilot:A",
            {
                "action_id": f"activate:{mage.ref}:{lower.ability_id}",
                "targets": ["B"],
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        leveler_program = next(
            program
            for program in engine.semantics.runtime_handler_programs_for_oracle(
                mage.oracle_id,
                active_zone="battlefield",
                event="characteristics.evaluate",
            )
            if any(
                handler.get("handler_id") == LEVELER_BANDS_HANDLER_ID
                for handler in program.handlers
            )
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="test:mage:remove-level-symbol",
                source_id="test:mage:remove-level-symbol",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(
                    ContinuousOperation(
                        "remove_ability_fragment",
                        {
                            "kind": "static_component",
                            "value": StaticComponentSpec(
                                leveler_program.key
                            ).to_dict(),
                        },
                    ),
                ),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=mage.object_id,
                        logical_object_id=mage.logical_object_id,
                    ),
                ),
            ),
        )
        self.assertEqual(set(), self.damage_abilities(engine, mage))
        self.assertTrue(
            any(
                "level counter" in ability.effect_text
                for ability in engine._activated_abilities(mage)
            )
        )
        self.assertEqual(1, expire_end_of_turn_continuous_effects(engine.state))
        self.assertEqual({3}, self.damage_abilities(engine, mage))

        self.assertEqual(
            (),
            protection_specs(engine._effective_ability_fragments(drinker)),
        )
        drinker.counters["level"] = 3
        self.assertEqual(
            ["card_type"],
            [
                spec.quality_kind.value
                for spec in protection_specs(
                    engine._effective_ability_fragments(drinker)
                )
            ],
        )
        drinker_program = next(
            program
            for program in engine.semantics.runtime_handler_programs_for_oracle(
                drinker.oracle_id,
                active_zone="battlefield",
                event="characteristics.evaluate",
            )
            if any(
                handler.get("handler_id") == LEVELER_BANDS_HANDLER_ID
                for handler in program.handlers
            )
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="test:drinker:remove-level-symbol",
                source_id="test:drinker:remove-level-symbol",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(
                    ContinuousOperation(
                        "remove_ability_fragment",
                        {
                            "kind": "static_component",
                            "value": StaticComponentSpec(
                                drinker_program.key
                            ).to_dict(),
                        },
                    ),
                ),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=drinker.object_id,
                        logical_object_id=drinker.logical_object_id,
                    ),
                ),
            ),
        )
        self.assertEqual(
            (),
            protection_specs(engine._effective_ability_fragments(drinker)),
        )
        self.assertEqual(1, expire_end_of_turn_continuous_effects(engine.state))
        self.assertEqual(
            ["card_type"],
            [
                spec.quality_kind.value
                for spec in protection_specs(
                    engine._effective_ability_fragments(drinker)
                )
            ],
        )

    def test_leveler_copy_ability_removal_and_layer_ordering(self):
        session = self.session(71102)
        engine = session.engine
        beast = self.add_card(
            session,
            name="Beastbreaker of Bala Ged",
            ref="A-layered-leveler",
        )
        beast.counters.update({"level": 4, "+1/+1": 1})
        self.assertEqual(7, engine._numeric_stat(beast.object_id, "power"))

        identity = ContinuousObjectIdentity(
            object_id=beast.object_id,
            logical_object_id=beast.logical_object_id,
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="test:leveler:modify",
                source_id="test:leveler:modify",
                layer=Layer.POWER_TOUGHNESS,
                sublayer="7c",
                timestamp=engine._next_zone_timestamp(),
                operations=(
                    ContinuousOperation("modify_power_toughness", [2, 2]),
                ),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(identity,),
            ),
        )
        self.assertEqual(9, engine._numeric_stat(beast.object_id, "power"))
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="test:leveler:set",
                source_id="test:leveler:set",
                layer=Layer.POWER_TOUGHNESS,
                sublayer="7b",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("set_power_toughness", [1, 1]),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(identity,),
            ),
        )
        self.assertEqual(4, engine._numeric_stat(beast.object_id, "power"))

        copied = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Goblin Engineer"
        )
        engine.move_card(
            copied.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        copied.annotations["copy_overrides"] = (
            engine._copyable_characteristics(beast)
        )
        self.assertEqual(2, engine._numeric_stat(copied.object_id, "power"))
        self.assertNotIn(
            "Trample", engine._effective_card_data(copied)["keywords"]
        )
        copied.counters["level"] = 4
        self.assertEqual(6, engine._numeric_stat(copied.object_id, "power"))
        self.assertIn(
            "Trample", engine._effective_card_data(copied)["keywords"]
        )
        engine.move_card(beast.object_id, "graveyard", log=False)
        beast.counters["level"] = 4
        self.assertEqual(2, engine._numeric_stat(beast.object_id, "power"))
        self.assertNotIn(
            "Trample", engine._effective_card_data(beast)["keywords"]
        )

        coralhelm = self.add_card(
            session,
            name="Coralhelm Commander",
            ref="A-coralhelm-source",
        )
        other = self.add_card(
            session,
            name="Coralhelm Commander",
            ref="A-coralhelm-other",
            register=False,
        )
        coralhelm.counters["level"] = 4
        self.assertIn(
            "Flying", engine._effective_card_data(coralhelm)["keywords"]
        )
        self.assertNotIn(
            "Flying", engine._effective_card_data(other)["keywords"]
        )
        self.assertEqual(3, engine._numeric_stat(other.object_id, "power"))

        older_keyword_loss = ContinuousEffect(
            effect_id="test:leveler:older-keyword-loss",
            source_id="test:leveler:older-keyword-loss",
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=max(0, coralhelm.zone_timestamp - 1),
            operations=(ContinuousOperation("remove_ability", "Flying"),),
            origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            locked_objects=(
                ContinuousObjectIdentity(
                    object_id=coralhelm.object_id,
                    logical_object_id=coralhelm.logical_object_id,
                ),
            ),
        )
        commit_continuous_effect(engine.state, older_keyword_loss)
        self.assertIn(
            "Flying", engine._effective_card_data(coralhelm)["keywords"]
        )
        newer_keyword_loss = replace(
            older_keyword_loss,
            effect_id="test:leveler:newer-keyword-loss",
            source_id="test:leveler:newer-keyword-loss",
            timestamp=engine._next_zone_timestamp(),
        )
        commit_continuous_effect(engine.state, newer_keyword_loss)
        self.assertNotIn(
            "Flying", engine._effective_card_data(coralhelm)["keywords"]
        )
        self.assertEqual(4, expire_end_of_turn_continuous_effects(engine.state))
        self.assertIn(
            "Flying", engine._effective_card_data(coralhelm)["keywords"]
        )

        leveler_program = next(
            program
            for program in engine.semantics.runtime_handler_programs_for_oracle(
                coralhelm.oracle_id,
                active_zone="battlefield",
                event="characteristics.evaluate",
            )
            if any(
                handler.get("handler_id") == LEVELER_BANDS_HANDLER_ID
                for handler in program.handlers
            )
        )
        removal = ContinuousEffect(
            effect_id="test:leveler:remove-component",
            source_id="test:leveler:remove-component",
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=engine._next_zone_timestamp(),
            operations=(
                ContinuousOperation(
                    "remove_ability_fragment",
                    {
                        "kind": "static_component",
                        "value": StaticComponentSpec(
                            leveler_program.key
                        ).to_dict(),
                    },
                ),
            ),
            origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            locked_objects=(
                ContinuousObjectIdentity(
                    object_id=coralhelm.object_id,
                    logical_object_id=coralhelm.logical_object_id,
                ),
            ),
        )
        commit_continuous_effect(engine.state, removal)
        self.assertNotIn(
            "Flying", engine._effective_card_data(coralhelm)["keywords"]
        )
        self.assertEqual(2, engine._numeric_stat(coralhelm.object_id, "power"))
        self.assertEqual(2, engine._numeric_stat(other.object_id, "power"))
        self.assertTrue(
            any(
                "level counter" in ability.effect_text
                for ability in engine._activated_abilities(coralhelm)
            )
        )
        self.assertEqual(1, expire_end_of_turn_continuous_effects(engine.state))
        self.assertEqual(4, engine._numeric_stat(coralhelm.object_id, "power"))
        self.assertEqual(3, engine._numeric_stat(other.object_id, "power"))

    def test_leveler_trample_uses_current_band_in_damage_assignment(self):
        session = self.session(71104)
        engine = session.engine
        attacker = self.add_card(
            session,
            name="Beastbreaker of Bala Ged",
            ref="A-leveler-trampler",
        )
        blocker_ref = engine.create_token(
            "B",
            name="Leveler Blocker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "2",
            },
        )[0]
        blocker = engine._resolve_object(
            "B", blocker_ref, zones={"battlefield"}
        )
        attacker.attacking = "B"
        blocker.blocking = attacker.object_id
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            defending_players=["B"],
            blockers={attacker.object_id: [blocker.object_id]},
        )

        attacker.counters["level"] = 3
        lower = project_combat_damage_assignment(
            EngineCombatDamageQuery(engine), "A"
        )
        self.assertEqual(
            [blocker.ref], lower.projected_options()[attacker.ref]["targets"]
        )
        attacker.counters["level"] = 4
        upper = project_combat_damage_assignment(
            EngineCombatDamageQuery(engine), "A"
        )
        self.assertEqual(
            {blocker.ref, "B"},
            set(upper.projected_options()[attacker.ref]["targets"]),
        )

        engine.state.phase_index = 7
        engine.state.phase = "combat"
        engine.state.step = "combat_damage"
        engine._begin_combat_damage()
        result = session.act(
            "pilot:A",
            {
                "a": "dmg",
                "assignments": [
                    {"source": attacker.ref, "target": blocker.ref, "amount": 2},
                    {"source": attacker.ref, "target": "B", "amount": 4},
                ],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(36, engine.state.players["B"].life)

    def test_leveler_four_player_replacement_replay_and_privacy(self):
        session = self.session(71103, players=4)
        engine = session.engine
        leveler = self.add_card(
            session,
            name="Beastbreaker of Bala Ged",
            ref="A-replay-leveler",
        )
        self.add_card(
            session,
            name="Doubling Season",
            ref="A-level-doubling-one",
        )
        self.add_card(
            session,
            name="Doubling Season",
            ref="A-level-doubling-two",
            register=False,
        )
        self.prepare_priority(session)
        level_up = next(
            ability
            for ability in engine._activated_abilities(leveler)
            if "level counter" in ability.effect_text
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {"action_id": f"activate:{leveler.ref}:{level_up.ability_id}"},
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

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "leveler-bands-record"
            session.save(record_dir)
            resumed = CommanderSession.load(self.db, record_dir)
            self.choose_replacements(resumed)
            self.pass_until(resumed, lambda: not resumed.state.stack)
            resumed_leveler = resumed.state.cards[leveler.object_id]
            self.assertEqual(4, resumed_leveler.counters.get("level"))
            self.assertEqual(
                6,
                resumed.engine._numeric_stat(leveler.object_id, "power"),
            )
            self.assertIn(
                "Trample",
                resumed.engine._effective_card_data(resumed_leveler)["keywords"],
            )
            expected_hash = authoritative_state_hash(resumed.state)
            resumed.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
