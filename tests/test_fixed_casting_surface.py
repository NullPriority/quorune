from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.cast_lifecycles import (
    FixedCastLifecycleKind,
    FixedCastLifecycleSpec,
    FIXED_CAST_LIFECYCLE_CAPABILITY_ID,
    FIXED_CAST_LIFECYCLE_CONTEXT_FIELD,
    FIXED_CAST_LIFECYCLE_HANDLER_ID,
)
from quorune.compiler.cast_cost_modifier_templates import (
    static_fixed_spell_cost_reduction_handler,
)
from quorune.continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.haste import has_effective_haste
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.semantic_runtime.cast_lifecycles import (
    FixedCastLifecycleHandler,
)
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database


FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "fixed-casting-surface-cards.json"
)


def _record(
    text: str,
    suffix: int,
    *,
    keywords: tuple[str, ...] = (),
    type_line: str = "Artifact",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=f"Fixed Casting Surface Compiler Fixture {suffix}",
        mana_cost="{3}",
        mana_value=3.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=keywords,
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedCastingSurfaceCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()

    def compile(self, record: CardRecord):
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_public_static_modifiers_compile_closed_schema(self):
        cases = (
            (
                "Artifact, instant, and sorcery spells your opponents cast cost {1} more to cast.",
                "source_opponents",
                1,
                "any",
                "any",
                [],
            ),
            (
                "During your turn, creature spells you cast cost {1} less to cast.",
                "source_controller",
                -1,
                "source_controller_turn",
                "any",
                [],
            ),
            (
                "The first creature spell you cast each turn costs {2} less to cast.",
                "source_controller",
                -2,
                "any",
                "first",
                [],
            ),
            (
                "Spells you cast from your graveyard or from exile cost {1} less to cast.",
                "source_controller",
                -1,
                "any",
                "any",
                ["graveyard", "exile"],
            ),
            (
                "Historic spells you cast cost {1} less to cast.",
                "source_controller",
                -1,
                "any",
                "any",
                [],
            ),
        )
        for index, (
            text,
            controller,
            adjustment,
            turn,
            ordinal,
            origins,
        ) in enumerate(cases, start=1):
            with self.subTest(text=text):
                ir = self.compile(_record(text, 160_010_000 + index))
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(
                    "public-fixed-spell-cost-modifier-v1",
                    node.template_id,
                )
                self.assertIn(
                    "current_ability_fragment_required",
                    node.runtime_coverage,
                )
                modifier = node.handlers[0]["modifier"]
                self.assertEqual(controller, modifier["affected_controller"])
                self.assertEqual(adjustment, modifier["generic_adjustment"])
                self.assertEqual(turn, modifier["turn_relation"])
                self.assertEqual(ordinal, modifier["ordinal"])
                self.assertEqual(origins, modifier["cast_origin_zones"])

    def test_public_static_modifier_near_misses_remain_residual(self):
        cases = (
            "Creature spells you cast cost {X} less to cast.",
            "Spells you cast cost {W} less to cast.",
            "The next spell you cast this turn costs {1} less to cast.",
            "The first historic spell you cast each turn costs {1} less to cast.",
            "Spells of the chosen type cost {1} more to cast.",
            "Creature spells cost {1} less to cast for each creature you control.",
            "This spell costs {2} less to cast if it targets a creature.",
        )
        for index, text in enumerate(cases, start=1):
            with self.subTest(text=text):
                ir = self.compile(_record(text, 160_011_000 + index))
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_cast_lifecycles_compile_source_spanned_typed_programs(self):
        cases = (
            ("Buyback {2}", "Buyback", FixedCastLifecycleKind.BUYBACK),
            ("Dash {1}{R}", "Dash", FixedCastLifecycleKind.DASH),
            ("Warp {1}{U}", "Warp", FixedCastLifecycleKind.WARP),
            ("Retrace", "Retrace", FixedCastLifecycleKind.RETRACE),
        )
        for index, (text, keyword, kind) in enumerate(cases, start=1):
            with self.subTest(text=text):
                record = _record(
                    text,
                    160_012_000 + index,
                    keywords=(keyword,),
                    type_line=(
                        "Sorcery"
                        if kind in {
                            FixedCastLifecycleKind.BUYBACK,
                            FixedCastLifecycleKind.RETRACE,
                        }
                        else "Creature — Wizard"
                    ),
                )
                ir = self.compile(record)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(
                    "fixed-public-cast-lifecycle-v1",
                    node.template_id,
                )
                self.assertEqual(
                    (FIXED_CAST_LIFECYCLE_CAPABILITY_ID,),
                    tuple(
                        dependency
                        for dependency in node.capability_dependencies
                        if dependency == FIXED_CAST_LIFECYCLE_CAPABILITY_ID
                    ),
                )
                descriptor = node.handlers[0]
                self.assertEqual(
                    FIXED_CAST_LIFECYCLE_HANDLER_ID,
                    descriptor["handler_id"],
                )
                spec = FixedCastLifecycleHandler().validate(descriptor)
                self.assertEqual(kind, spec.kind)
                self.assertEqual(text, spec.oracle_line)

    def test_cast_lifecycle_open_costs_and_other_families_remain_residual(self):
        cases = (
            ("Dash {X}{R}", "Dash"),
            ("Warp {W/U}", "Warp"),
            ("Buyback—Sacrifice a land.", "Buyback"),
            ("Blitz {1}{R}", "Blitz"),
            ("Retrace—Discard two lands.", "Retrace"),
        )
        for index, (text, keyword) in enumerate(cases, start=1):
            with self.subTest(text=text):
                ir = self.compile(
                    _record(
                        text,
                        160_013_000 + index,
                        keywords=(keyword,),
                        type_line="Creature — Wizard",
                    )
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertFalse(
                    any(
                        descriptor.get("handler_id")
                        == FIXED_CAST_LIFECYCLE_HANDLER_ID
                        for face in ir.faces
                        for node in face.nodes
                        for descriptor in node.handlers
                    )
                )

    def test_lifecycle_descriptors_are_closed_and_dependency_gated(self):
        record = _record(
            "Dash {1}{R}",
            160_014_001,
            keywords=("Dash",),
            type_line="Creature — Warrior",
        )
        ir = self.compile(record)
        descriptor = dict(ir.faces[0].nodes[0].handlers[0])
        mutation = {
            **descriptor,
            "lifecycle": {
                **descriptor["lifecycle"],
                "cost_text": "{2}{R}",
            },
        }
        with self.assertRaises(SemanticNodeError):
            FixedCastLifecycleHandler().validate(mutation)
        registry_value = json.loads(
            (
                ROOT / "quorune" / "rules" / "capability-registry.json"
            ).read_text(encoding="utf-8")
        )
        row = next(
            item
            for item in registry_value["capabilities"]
            if item["id"] == FIXED_CAST_LIFECYCLE_CAPABILITY_ID
        )
        row["status"] = "blocked"
        row["blockers"] = ["focused mutation"]
        blocked = CapabilityRegistry(registry_value)
        blocked_ir = compile_oracle_card(
            record,
            capability_registry=blocked,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", blocked_ir.status)
        self.assertTrue(blocked_ir.material_residuals)


class FixedCastingSurfaceRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-casting-surface.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                FIXTURE_PATH,
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
    ) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name, fuzzy=False)
        card = CardInstance(
            object_id=f"fixed-casting-surface:{ref}",
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
    def cast_action(engine, card: CardInstance, seat: str = "B"):
        return next(
            action
            for action in engine._priority_action_hints(seat)["actions"]
            if action.get("action") == "cast" and action.get("card") == card.ref
        )

    @staticmethod
    def option(engine, card: CardInstance, option_id: str, seat: str = "B"):
        return next(
            option
            for option in FixedCastingSurfaceRuntimeTests.cast_action(
                engine, card, seat
            )["cost_options"]
            if option["id"] == option_id
        )

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    @staticmethod
    def resolve_stack_with_passes(session) -> None:
        for _ in range(16):
            if not session.engine.state.stack:
                return
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Fixed casting-surface stack did not resolve")

    @staticmethod
    def resolve_delayed_end_step(engine) -> None:
        triggers = engine._matching_delayed_triggers(
            "step.begin",
            {
                "phase": "ending",
                "step": "end_step",
                "player": engine.state.active_player,
            },
        )
        if not triggers:
            raise AssertionError("Fixed cast-lifecycle delayed trigger is missing")
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._start_trigger_batch(triggers, after="grant_priority")
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    @staticmethod
    def remove_all_abilities(engine, card: CardInstance) -> None:
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id=f"test:remove:{card.ref}",
                source_id="test:fixed-casting-surface-removal",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=card.object_id,
                        logical_object_id=card.logical_object_id,
                    ),
                ),
            ),
        )

    def test_public_static_modifiers_scope_opponents_and_replay(self):
        session = self.session(160_020_001, players=4)
        engine = session.engine
        source = self.add_card(
            session,
            name="Public Opponent Tax Fixture",
            ref="TAX-SOURCE",
            seat="A",
            zone="battlefield",
        )
        taxed = self.add_card(
            session,
            name="Generic Artifact Spell Fixture",
            ref="TAXED-SPELL",
            seat="B",
        )
        untaxed = self.add_card(
            session,
            name="Generic Artifact Spell Fixture",
            ref="UNTAXED-SPELL",
            seat="A",
        )
        engine.state.players["B"].mana_pool["C"] = 4
        engine.state.players["A"].mana_pool["C"] = 3
        self.prepare_main(session, "B")
        self.assertEqual(4, self.option(engine, taxed, "normal")["requirements"]["GENERIC"])
        self.prepare_main(session, "A")
        self.assertEqual(3, self.option(engine, untaxed, "normal", "A")["requirements"]["GENERIC"])

        self.prepare_main(session, "B")
        action = self.cast_action(engine, taxed)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:B", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", taxed.zone)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            self.assertNotIn(
                taxed.object_id,
                json.dumps(session.packet(principal, full=True), sort_keys=True),
            )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "public-cost-modifier-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])
        self.assertEqual("battlefield", source.zone)

    def test_public_static_modifiers_share_offer_commit_and_current_ability(self):
        session = self.session(160_020_002)
        engine = session.engine
        source = self.add_card(
            session,
            name="Public Flying Reducer Fixture",
            ref="FLYING-REDUCER",
            zone="battlefield",
        )
        flying = self.add_card(
            session,
            name="Flying Creature Spell Fixture",
            ref="FLYING-SPELL",
        )
        ordinary = self.add_card(
            session,
            name="Generic Creature Spell Fixture",
            ref="NONFLYING-SPELL",
        )
        engine.state.players["B"].mana_pool.update({"C": 8, "U": 1, "G": 1})
        self.prepare_main(session)
        self.assertEqual(1, self.option(engine, flying, "normal")["requirements"]["GENERIC"])
        self.assertEqual(3, self.option(engine, ordinary, "normal")["requirements"]["GENERIC"])
        self.remove_all_abilities(engine, source)
        self.prepare_main(session)
        self.assertEqual(2, self.option(engine, flying, "normal")["requirements"]["GENERIC"])
        action = self.cast_action(engine, flying)
        result = session.act("pilot:B", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", flying.zone)
        self.assertEqual(6, engine.state.players["B"].mana_pool["C"])

    def test_public_static_modifier_turn_origin_and_ordinal_predicates(self):
        session = self.session(160_020_003)
        engine = session.engine
        self.add_card(
            session,
            name="Public Turn Reducer Fixture",
            ref="TURN-REDUCER",
            zone="battlefield",
        )
        self.add_card(
            session,
            name="Public Ordinal Reducer Fixture",
            ref="ORDINAL-REDUCER",
            zone="battlefield",
        )
        hand_spell = self.add_card(
            session,
            name="Generic Creature Spell Fixture",
            ref="TURN-SPELL",
        )
        engine.state.players["B"].mana_pool.update({"C": 12, "G": 1})
        self.prepare_main(session)
        self.assertEqual(0, self.option(engine, hand_spell, "normal")["requirements"]["GENERIC"])
        engine._record_turn_history("spell_cast", actor="B", types=("artifact",))
        self.assertEqual(0, self.option(engine, hand_spell, "normal")["requirements"]["GENERIC"])
        engine._record_turn_history("spell_cast", actor="B", types=("creature",))
        self.assertEqual(2, self.option(engine, hand_spell, "normal")["requirements"]["GENERIC"])
        engine.state.active_player = "A"
        program = engine.semantics.get(f"{hand_spell.oracle_id}:spell:front")
        off_turn = engine._cast_cost_options(
            "B",
            hand_spell,
            program,
            response={},
            hint=True,
        )
        normal = next(value for value in off_turn if value["id"] == "normal")
        self.assertEqual(3, normal["requirements"]["GENERIC"])

        origin = self.add_card(
            session,
            name="Public Origin Reducer Fixture",
            ref="ORIGIN-REDUCER",
            zone="battlefield",
        )
        graveyard_spell = self.add_card(
            session,
            name="Retrace Spell Fixture",
            ref="ORIGIN-SPELL",
            zone="graveyard",
        )
        land = self.add_card(
            session,
            name="Forest",
            ref="ORIGIN-LAND",
        )
        engine.state.players["B"].mana_pool["U"] = 1
        self.prepare_main(session)
        option = self.option(engine, graveyard_spell, "retrace")
        self.assertEqual(1, option["requirements"]["GENERIC"])
        self.assertIn(land.ref, option["choice_schema"]["discard_cards"]["legal_refs"])
        self.assertEqual("battlefield", origin.zone)

    def test_buyback_resolves_to_hand_but_countered_spell_does_not(self):
        session = self.session(160_020_004)
        engine = session.engine
        card = self.add_card(
            session,
            name="Buyback Spell Fixture",
            ref="BUYBACK-ONE",
        )
        engine.state.players["B"].mana_pool.update({"C": 6, "U": 2})
        self.prepare_main(session)
        option = self.option(engine, card, "buyback")
        self.assertEqual(3, option["requirements"]["GENERIC"])
        engine.permissions.invalidate_current()
        engine._cast(
            "B",
            {"card": card.ref, "cost_option": "buyback", "pay": "auto"},
        )
        self.resolve_top(engine)
        self.assertEqual("hand", card.zone)

        engine.state.players["B"].mana_pool.update({"C": 3, "U": 1})
        self.prepare_main(session)
        engine.permissions.invalidate_current()
        engine._cast(
            "B",
            {"card": card.ref, "cost_option": "buyback", "pay": "auto"},
        )
        engine._counter_stack_item(
            engine.state.stack[-1].ref,
            reason="Buyback counter regression",
            countered_by="A",
        )
        self.assertEqual("graveyard", card.zone)

    def test_dash_and_warp_are_identity_pinned_through_delayed_resolution(self):
        for index, (name, option_id, destination) in enumerate(
            (
                ("Dash Creature Fixture", "dash", "hand"),
                ("Warp Creature Fixture", "warp", "exile"),
            ),
            start=1,
        ):
            with self.subTest(name=name, mode="ordinary"):
                session = self.session(160_021_000 + index)
                engine = session.engine
                card = self.add_card(session, name=name, ref=f"LIFE-{index}")
                engine.state.players["B"].mana_pool.update({"C": 1, "R": 1, "U": 1})
                self.prepare_main(session)
                engine.permissions.invalidate_current()
                engine._cast(
                    "B",
                    {"card": card.ref, "cost_option": option_id, "pay": "auto"},
                )
                self.resolve_top(engine)
                self.assertEqual("battlefield", card.zone)
                if option_id == "dash":
                    self.assertTrue(has_effective_haste(engine, card))
                self.resolve_delayed_end_step(engine)
                self.assertEqual(destination, card.zone)
                if option_id == "warp":
                    permission = card.annotations.get("temporary_play_permission")
                    self.assertEqual("until_used", permission["duration"])
                    self.assertFalse(
                        any(
                            action.get("card") == card.ref
                            for action in engine._priority_action_hints("B")["actions"]
                        )
                    )
                    engine.state.turn_sequence += 1
                    engine.state.players["B"].mana_pool.update(
                        {"C": 3, "U": 1}
                    )
                    self.prepare_main(session)
                    self.assertTrue(
                        any(
                            action.get("card") == card.ref
                            for action in engine._priority_action_hints("B")["actions"]
                        )
                    )

            with self.subTest(name=name, mode="new-incarnation"):
                session = self.session(160_022_000 + index)
                engine = session.engine
                card = self.add_card(session, name=name, ref=f"STALE-{index}")
                engine.state.players["B"].mana_pool.update({"C": 1, "R": 1, "U": 1})
                self.prepare_main(session)
                engine.permissions.invalidate_current()
                engine._cast(
                    "B",
                    {"card": card.ref, "cost_option": option_id, "pay": "auto"},
                )
                self.resolve_top(engine)
                old_identity = card.logical_object_id
                if option_id == "warp":
                    engine.move_card(card.object_id, "exile", log=False)
                else:
                    engine.move_card(card.object_id, "hand", log=False)
                    engine.move_card(
                        card.object_id,
                        "battlefield",
                        controller="B",
                        log=False,
                    )
                self.assertNotEqual(old_identity, card.logical_object_id)
                self.resolve_delayed_end_step(engine)
                self.assertEqual(
                    "exile" if option_id == "warp" else "battlefield",
                    card.zone,
                )
                self.assertNotIn("temporary_play_permission", card.annotations)

            if option_id == "warp":
                with self.subTest(name=name, mode="phased-out"):
                    session = self.session(160_023_000 + index)
                    engine = session.engine
                    card = self.add_card(
                        session,
                        name=name,
                        ref=f"PHASED-{index}",
                    )
                    engine.state.players["B"].mana_pool.update(
                        {"C": 1, "U": 1}
                    )
                    self.prepare_main(session)
                    engine.permissions.invalidate_current()
                    engine._cast(
                        "B",
                        {
                            "card": card.ref,
                            "cost_option": option_id,
                            "pay": "auto",
                        },
                    )
                    self.resolve_top(engine)
                    card.phased_out = True
                    self.resolve_delayed_end_step(engine)
                    self.assertEqual("battlefield", card.zone)
                    self.assertNotIn(
                        "temporary_play_permission",
                        card.annotations,
                    )

    def test_retrace_uses_private_typed_land_discard_and_revalidates(self):
        session = self.session(160_020_005, players=4)
        engine = session.engine
        spell = self.add_card(
            session,
            name="Retrace Spell Fixture",
            ref="RETRACE-SPELL",
            zone="graveyard",
        )
        land = self.add_card(session, name="Forest", ref="RETRACE-LAND")
        nonland = self.add_card(
            session,
            name="Generic Artifact Spell Fixture",
            ref="RETRACE-NONLAND",
        )
        engine.state.players["B"].mana_pool.update({"C": 2, "U": 1})
        self.prepare_main(session)
        action = self.cast_action(engine, spell)
        self.assertEqual({"retrace"}, {row["id"] for row in action["cost_options"]})
        schema = action["cost_options"][0]["choice_schema"]["discard_cards"]
        self.assertIn(land.ref, schema["legal_refs"])
        self.assertNotIn(nonland.ref, schema["legal_refs"])
        self.assertFalse(
            any(
                row.get("card") == spell.ref
                for row in engine._priority_action_hints("A")["actions"]
            )
        )
        self.assertNotIn(
            land.ref,
            json.dumps(session.packet("pilot:A", full=True), sort_keys=True),
        )
        engine.move_card(land.object_id, "graveyard", log=False)
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine._cast(
                "B",
                {
                    "card": spell.ref,
                    "from": "graveyard",
                    "cost_option": "retrace",
                    "discard_cards": [land.ref],
                    "pay": "auto",
                },
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("graveyard", spell.zone)

        engine.move_card(land.object_id, "hand", log=False)
        engine.permissions.invalidate_current()
        engine._cast(
            "B",
            {
                "card": spell.ref,
                "from": "graveyard",
                "cost_option": "retrace",
                "discard_cards": [land.ref],
                "pay": "auto",
            },
        )
        self.assertEqual("graveyard", land.zone)
        self.resolve_top(engine)
        self.assertEqual("graveyard", spell.zone)

    def test_partial_card_and_stale_lifecycle_contract_fail_closed(self):
        session = self.session(160_020_006)
        engine = session.engine
        partial = self.add_card(
            session,
            name="Partial Retrace Fixture",
            ref="PARTIAL-RETRACE",
            zone="graveyard",
        )
        dash = self.add_card(
            session,
            name="Dash Creature Fixture",
            ref="STALE-DASH",
        )
        engine.state.players["B"].mana_pool.update({"C": 3, "R": 1})
        self.prepare_main(session)
        self.assertFalse(
            any(
                action.get("card") == partial.ref
                for action in engine._priority_action_hints("B")["actions"]
            )
        )
        before = authoritative_state_hash(engine.state)
        with mock.patch(
            "quorune.rules.casting.commit.compiled_fixed_cast_lifecycle_spec",
            return_value=None,
        ):
            with self.assertRaisesRegex(GameRuleError, "contract changed"):
                engine._cast(
                    "B",
                    {"card": dash.ref, "cost_option": "dash", "pay": "auto"},
                )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("hand", dash.zone)

    def test_public_modifiers_apply_to_lifecycle_total_costs(self):
        session = self.session(160_020_007)
        engine = session.engine
        self.add_card(
            session,
            name="Public Turn Reducer Fixture",
            ref="LIFECYCLE-REDUCER",
            zone="battlefield",
        )
        dash = self.add_card(
            session,
            name="Dash Creature Fixture",
            ref="REDUCED-DASH",
        )
        engine.state.players["B"].mana_pool.update({"R": 1})
        self.prepare_main(session)
        option = self.option(engine, dash, "dash")
        self.assertEqual(0, option["requirements"]["GENERIC"])
        self.assertEqual(1, option["requirements"]["R"])

    def test_lifecycle_replay_and_projection_are_deterministic(self):
        session = self.session(160_020_008, players=4)
        engine = session.engine
        card = self.add_card(
            session,
            name="Warp Creature Fixture",
            ref="REPLAY-WARP",
        )
        engine.state.players["B"].mana_pool.update({"C": 1, "U": 1})
        self.prepare_main(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        action = self.cast_action(engine, card)
        result = session.act(
            "pilot:B",
            {"action_id": action["id"], "cost_option": "warp"},
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_stack_with_passes(session)
        self.assertEqual("battlefield", card.zone)
        self.assertTrue(
            any(
                trigger.active and trigger.source_object_id == card.object_id
                for trigger in engine.state.delayed_triggers
            )
        )
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            self.assertNotIn(
                card.object_id,
                json.dumps(session.packet(principal, full=True), sort_keys=True),
            )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "fixed-lifecycle-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
