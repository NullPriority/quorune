from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
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
from quorune.model import CardInstance
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card, generated_programs
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
from quorune.rules.spell_cast_events import SpellCastEvent, SpellCastEventError
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "prowess.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "prowess-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class ProwessCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.record = cls.db.lookup("Monastery Swiftspear")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_spell_cast_event_is_strict_canonical_and_round_trips(self):
        event = SpellCastEvent(
            card_ref="A17",
            object_id="object:A17",
            logical_object_id="logical:A17:4",
            controller="A",
            origin="hand",
            stack_ref="S9",
            types=("Artifact", "artifact"),
            subtypes=("Vehicle", "vehicle"),
            supertypes=("Legendary",),
            colors=("u", "W"),
        )
        equivalent = SpellCastEvent(
            card_ref="A17",
            object_id="object:A17",
            logical_object_id="logical:A17:4",
            controller="A",
            origin="hand",
            stack_ref="S9",
            types=("artifact",),
            subtypes=("vehicle",),
            supertypes=("legendary",),
            colors=("W", "U"),
        )

        self.assertEqual(("artifact",), event.types)
        self.assertEqual(("vehicle",), event.subtypes)
        self.assertEqual(("legendary",), event.supertypes)
        self.assertEqual(("W", "U"), event.colors)
        self.assertEqual(event, SpellCastEvent.from_context(event.to_context()))
        self.assertEqual(event.fingerprint, equivalent.fingerprint)
        self.assertEqual(
            ["artifact"],
            event.to_context()["types"],
        )

    def test_spell_cast_event_replays_legacy_v1_shape_exactly(self):
        legacy = {
            "schema_version": 1,
            "card": "A17",
            "object_id": "object:A17",
            "logical_object_id": "logical:A17:4",
            "controller": "A",
            "player": "A",
            "from": "hand",
            "to": "stack",
            "types": ["artifact"],
            "stack": "S9",
        }

        event = SpellCastEvent.from_context(legacy)

        self.assertEqual(1, event.schema_version)
        self.assertEqual((), event.subtypes)
        self.assertEqual((), event.supertypes)
        self.assertEqual((), event.colors)
        self.assertEqual(legacy, event.to_context())

    def test_spell_cast_event_rejects_malformed_context_without_mutation(self):
        context = SpellCastEvent(
            card_ref="A17",
            object_id="object:A17",
            logical_object_id="logical:A17:4",
            controller="A",
            origin="hand",
            stack_ref="S9",
            types=("artifact",),
        ).to_context()
        context["player"] = "B"
        before = deepcopy(context)

        with self.assertRaises(SpellCastEventError):
            SpellCastEvent.from_context(context)

        self.assertEqual(before, context)

        malformed_colors = SpellCastEvent(
            card_ref="A17",
            object_id="object:A17",
            logical_object_id="logical:A17:4",
            controller="A",
            origin="hand",
            stack_ref="S9",
            types=("artifact",),
        ).to_context()
        malformed_colors["colors"] = ["C"]
        with self.assertRaises(SpellCastEventError):
            SpellCastEvent.from_context(malformed_colors)

    def test_prowess_keyword_lowers_each_instance_with_precise_spans(self):
        text = "Haste, prowess, prowess"
        record = replace(
            self.record,
            oracle_text=text,
            keywords=("Haste", "Prowess"),
        )
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        nodes = [
            node
            for node in ir.faces[0].nodes
            if node.template_id == "prowess-noncreature-spell-trigger-v1"
        ]

        self.assertEqual("exact", ir.status)
        self.assertEqual(2, len(nodes))
        self.assertEqual(2, len({node.node_id for node in nodes}))
        self.assertEqual(
            2,
            len({(node.span.start, node.span.end) for node in nodes}),
        )
        for node in nodes:
            self.assertTrue(node.exact)
            self.assertEqual("spell.cast", node.event)
            self.assertEqual(
                ("current_ability_fragment_required",),
                node.runtime_coverage,
            )
            self.assertEqual(
                ("trigger.keyword.prowess",),
                node.capability_dependencies,
            )
            self.assertEqual(
                "prowess",
                text[node.span.start : node.span.end].casefold(),
            )
            self.assertEqual(
                "$source.zone_object",
                node.effects[0]["card"],
            )
            self.assertEqual(
                "ability.trigger.prowess.v1",
                node.handlers[0]["handler_id"],
            )

        programs = [
            program
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "prowess-noncreature-spell-trigger-v1"
        ]
        self.assertEqual(2, len(programs))
        self.assertEqual(2, len({program.key for program in programs}))
        self.assertTrue(all(program.capability_closure["trusted"] for program in programs))

    def test_unsupported_prowess_wording_remains_material_residual(self):
        for text in (
            "Prowess 2",
            "Prowess — Whenever you cast your second spell each turn",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(
                        self.record,
                        oracle_text=text,
                        keywords=("Prowess",),
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertTrue(
                    any(
                        "prowess-unsupported-wording" in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    )
                )

    def test_prowess_dependency_and_compiler_mutations_fail_closed(self):
        for dependency_id in (
            "continuous.resolution.fixed_characteristics_until_end_of_turn",
            "trigger.event.normalized_spell_cast",
            "trigger.placement.apnap",
        ):
            with self.subTest(dependency=dependency_id):
                value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                dependency = next(
                    row
                    for row in value["capabilities"]
                    if row["id"] == dependency_id
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = ["test mutation"]
                ir = compile_oracle_card(
                    self.record,
                    capability_registry=CapabilityRegistry(value),
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(
                    any(
                        dependency_id in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    )
                )

        with patch("quorune.oracle_ir.prowess_keyword_node", return_value=None):
            ir = compile_oracle_card(
                self.record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(
                node.template_id == "prowess-noncreature-spell-trigger-v1"
                for node in ir.faces[0].nodes
            )
        )


class ProwessRuntimeTests(unittest.TestCase):
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
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
    ) -> CardInstance:
        record = self.db.lookup(name)
        visible = list(engine.seats) if zone == "battlefield" else [seat]
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=visible,
            revealed_to=visible if zone == "battlefield" else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def deck_card(engine, seat: str, name: str) -> CardInstance:
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def register_prowess(
        self,
        engine,
        source: CardInstance,
        *,
        expected_instances: int = 1,
    ):
        record = self.db.by_oracle_id(source.oracle_id)
        for program in tuple(engine.semantics.programs_for_oracle(source.oracle_id)):
            if program.event == "spell.cast":
                engine.semantics.remove(program.key)
        programs = [
            program
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "prowess-noncreature-spell-trigger-v1"
        ]
        self.assertEqual(expected_instances, len(programs))
        for program in programs:
            engine.semantics.put(program)
        return programs

    @staticmethod
    def resolve_top(engine):
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def prepare_noncreature_cast(self, engine, *, seat: str = "A") -> CardInstance:
        engine.state.active_player = seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = seat
        engine.state.priority_passes = []
        card = self.deck_card(engine, seat, "Sol Ring")
        if card.zone != "hand":
            engine.move_card(card.object_id, "hand", log=False)
        engine.state.players[seat].mana_pool["C"] += 1
        engine._cast(seat, {"card": card.ref, "pay": "auto"})
        return card

    @staticmethod
    def cast_event(*, controller: str, types: tuple[str, ...]) -> dict:
        return SpellCastEvent(
            card_ref=f"{controller}-spell",
            object_id=f"object:{controller}-spell",
            logical_object_id=f"logical:{controller}-spell:1",
            controller=controller,
            origin="hand",
            stack_ref=f"stack:{controller}-spell",
            types=types,
        ).to_context()

    def test_noncreature_spell_creates_one_prowess_trigger(self):
        session = self.session(70210801)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Monastery Swiftspear",
            ref="prowess-source",
            zone="battlefield",
        )
        program = self.register_prowess(engine, source)[0]

        spell = self.prepare_noncreature_cast(engine)
        engine._stabilize()

        self.assertEqual("stack", spell.zone)
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual("A", engine.state.stack[-1].controller)
        self.assertEqual(
            source.logical_object_id,
            engine.state.stack[-1].context["source_logical_object_id"],
        )
        self.resolve_top(engine)
        self.assertEqual(2, engine._numeric_stat(source.object_id, "power"))
        self.assertEqual(3, engine._numeric_stat(source.object_id, "toughness"))

    def test_spell_cast_event_dispatch_mutant_is_killed(self):
        def assert_typed_dispatch(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            source = self.add_card(
                engine,
                seat="A",
                name="Monastery Swiftspear",
                ref=f"typed-event-source-{seed}",
                zone="battlefield",
            )
            self.register_prowess(engine, source)
            self.prepare_noncreature_cast(engine)
            engine._stabilize()
            context = engine.state.stack[-1].context
            self.assertEqual(1, context["schema_version"])
            self.assertTrue(context["object_id"])
            self.assertTrue(context["logical_object_id"])

        assert_typed_dispatch(70210803)

        class LegacySpellCastEvent:
            def __init__(self, **values):
                self.values = values

            def to_context(self):
                return {
                    "card": self.values["card_ref"],
                    "controller": self.values["controller"],
                    "player": self.values["controller"],
                    "from": self.values["origin"],
                    "to": "stack",
                    "types": sorted(self.values["types"]),
                    "stack": self.values["stack_ref"],
                }

        with patch(
            "quorune.rules.casting.commit.SpellCastEvent",
            LegacySpellCastEvent,
        ):
            with self.assertRaises((AssertionError, KeyError)):
                assert_typed_dispatch(70210804)

    def test_multiple_prowess_instances_trigger_separately(self):
        session = self.session(70210802)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Double Prowess Fixture",
            ref="double-prowess-source",
            zone="battlefield",
        )
        programs = self.register_prowess(
            engine,
            source,
            expected_instances=2,
        )

        self.prepare_noncreature_cast(engine)
        self.assertEqual("trigger.order", engine.state.pending_decision.kind)
        trigger_refs = [
            item.ref for item in engine.state.pending_trigger_batches[0].items
        ]
        result = session.act(
            "pilot:A",
            {"action_id": "order", "triggers": trigger_refs},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            {program.key for program in programs},
            {item.semantic_key for item in engine.state.stack[-2:]},
        )

        self.resolve_top(engine)
        self.resolve_top(engine)
        self.assertEqual(3, engine._numeric_stat(source.object_id, "power"))
        self.assertEqual(4, engine._numeric_stat(source.object_id, "toughness"))

    def test_creature_and_opponent_spells_do_not_trigger_prowess(self):
        for controller, types, label in (
            ("A", ("creature",), "creature"),
            ("B", ("artifact",), "opponent"),
        ):
            with self.subTest(label=label):
                session = self.session(70210810 + len(label))
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Monastery Swiftspear",
                    ref=f"{label}-prowess-source",
                    zone="battlefield",
                )
                self.register_prowess(engine, source)

                triggered = engine._dispatch_semantic_event(
                    "spell.cast",
                    self.cast_event(controller=controller, types=types),
                    sources=[source],
                )

                self.assertEqual([], triggered)
                self.assertFalse(engine.state.stack)
                self.assertFalse(engine.state.pending_trigger_batches)

    def test_current_layer_six_ability_fragment_is_required(self):
        session = self.session(70210820)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Monastery Swiftspear",
            ref="ability-removed-source",
            zone="battlefield",
        )
        self.register_prowess(engine, source)
        identity = ContinuousObjectIdentity(
            object_id=source.object_id,
            logical_object_id=source.logical_object_id,
        )
        commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-prowess",
                source_id="fixture:remove-abilities-source",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=("battlefield",)),
                locked_objects=(identity,),
            ),
        )

        self.assertEqual(
            [],
            engine._effective_card_data(source)["ability_fragments"],
        )
        triggered = engine._dispatch_semantic_event(
            "spell.cast",
            self.cast_event(controller="A", types=("artifact",)),
            sources=[source],
        )
        self.assertEqual([], triggered)
        self.assertFalse(engine.state.pending_trigger_batches)
        with patch(
            "quorune.trigger_discovery."
            "program_has_current_ability_fragments",
            return_value=True,
        ):
            with self.assertRaises(AssertionError):
                self.assertEqual(
                    [],
                    engine._dispatch_semantic_event(
                        "spell.cast",
                        self.cast_event(
                            controller="A",
                            types=("artifact",),
                        ),
                        sources=[source],
                    ),
                )

    def test_prowess_source_identity_and_control_are_pinned(self):
        session = self.session(70210830)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Monastery Swiftspear",
            ref="control-change-source",
            zone="battlefield",
        )
        self.register_prowess(engine, source)
        self.prepare_noncreature_cast(engine)
        engine._stabilize()
        self.assertEqual("A", engine.state.stack[-1].controller)
        engine.change_control(
            source.object_id,
            "B",
            reason="Prowess control-change fixture",
        )

        self.resolve_top(engine)

        self.assertEqual("B", source.controller)
        self.assertEqual(2, engine._numeric_stat(source.object_id, "power"))

        session = self.session(70210831)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Monastery Swiftspear",
            ref="incarnation-change-source",
            zone="battlefield",
        )
        self.register_prowess(engine, source)
        self.prepare_noncreature_cast(engine)
        engine._stabilize()
        engine.move_card(source.object_id, "exile", log=False)
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )

        self.resolve_top(engine)

        self.assertEqual(1, engine._numeric_stat(source.object_id, "power"))

    def test_prowess_and_opponent_cast_trigger_share_one_apnap_batch(self):
        session = self.session(70210840)
        engine = session.engine
        engine.state.active_player = "A"
        source = self.add_card(
            engine,
            seat="A",
            name="Monastery Swiftspear",
            ref="interaction-prowess-source",
            zone="battlefield",
        )
        self.register_prowess(engine, source)
        remora = self.deck_card(engine, "B", "Mystic Remora")
        engine.move_card(remora.object_id, "battlefield", controller="B", log=False)

        self.prepare_noncreature_cast(engine)

        self.assertEqual(
            ["A", "B"],
            [item.controller for item in engine.state.stack[-2:]],
        )
        self.assertEqual(
            {source.object_id, remora.object_id},
            {item.source_object_id for item in engine.state.stack[-2:]},
        )

    def test_four_player_prowess_batch_is_public_and_apnap_ordered(self):
        session = self.session(70210850, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        source = self.add_card(
            engine,
            seat="A",
            name="Monastery Swiftspear",
            ref="four-player-prowess-source",
            zone="battlefield",
        )
        self.register_prowess(engine, source)
        remora = self.deck_card(engine, "B", "Mystic Remora")
        engine.move_card(remora.object_id, "battlefield", controller="B", log=False)
        private_a = self.deck_card(engine, "A", "Lightning Greaves")
        private_b = self.deck_card(engine, "B", "Scute Swarm")
        engine.move_card(private_a.object_id, "hand", log=False)
        engine.move_card(private_b.object_id, "hand", log=False)

        spell = self.prepare_noncreature_cast(engine)

        self.assertEqual(
            ["A", "B"],
            [item.controller for item in engine.state.stack[-2:]],
        )
        projector = StateProjector(self.db, engine.state)
        snapshot_a = projector._snapshot("pilot:A")
        snapshot_b = projector._snapshot("pilot:B")
        projected_a = json.dumps(snapshot_a, sort_keys=True)
        projected_b = json.dumps(snapshot_b, sort_keys=True)
        self.assertNotIn(private_b.ref, projected_a)
        self.assertNotIn(private_a.ref, projected_b)
        self.assertIn(source.ref, projected_b)
        self.assertIn(remora.ref, projected_a)
        self.assertTrue(
            any(
                row.get("n") == spell.printed_name
                and row.get("cid") == spell.oracle_id[:8]
                for row in snapshot_b["stack"]
            )
        )

    def test_prowess_trigger_and_resolution_replay_exactly(self):
        session = self.session(70210860, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Monastery Swiftspear",
            ref="replay-prowess-source",
            zone="battlefield",
        )
        self.register_prowess(engine, source)
        self.prepare_noncreature_cast(engine)
        engine._stabilize()
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for seat in engine.seats:
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)

        self.assertEqual(2, engine._numeric_stat(source.object_id, "power"))
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "prowess-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
