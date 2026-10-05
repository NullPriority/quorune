from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.deck import DeckLoader
from quorune.model import CardInstance, StackItem
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
from quorune.semantic_runtime import runtime_component_inventory, zone_replacements
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.sunburst import (
    SUNBURST_HANDLER_ID,
    SunburstEntryCounterHandler,
    SunburstError,
    SunburstNode,
    SunburstSpec,
)
from quorune.semantic_runtime.zone_replacement_model import (
    ZoneChangeReplacementSnapshot,
    ZoneChangeSubjectSnapshot,
    ZoneReplacementError,
)
from quorune.semantic_runtime.zone_replacements import (
    capture_zone_change_replacement_snapshot,
    prepare_zone_change_replacement_snapshot,
)
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "sunburst.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "sunburst-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class SunburstCompilerAndModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        cls.creature = cls.db.lookup("Skyreach Manta")
        cls.noncreature = cls.db.lookup("Pentad Prism")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, card):
        return compile_oracle_card(
            card,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_sunburst_compiles_printed_counter_kind_with_precise_spans(self):
        expected = (
            (self.creature, "+1/+1"),
            (self.noncreature, "charge"),
        )
        for card, counter_name in expected:
            with self.subTest(card=card.name):
                ir = self.compile(card)
                node = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id == "sunburst-cast-entry-counter-v1"
                )
                self.assertTrue(node.exact)
                self.assertEqual(
                    ("counter.producer.sunburst",),
                    node.capability_dependencies,
                )
                self.assertEqual(
                    "Sunburst",
                    card.oracle_text[node.span.start:node.span.end],
                )
                self.assertEqual(counter_name, node.handlers[0]["counter_name"])

        repeated = replace(
            self.creature,
            oracle_text="Sunburst, Sunburst",
            keywords=("Sunburst",),
        )
        nodes = [
            node
            for node in self.compile(repeated).faces[0].nodes
            if node.template_id == "sunburst-cast-entry-counter-v1"
        ]
        self.assertEqual(2, len(nodes))
        self.assertEqual(
            ["Sunburst", "Sunburst"],
            [
                repeated.oracle_text[node.span.start:node.span.end]
                for node in nodes
            ],
        )
        programs = [
            program
            for program in generated_programs(
                self.db,
                repeated,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "sunburst-cast-entry-counter-v1"
        ]
        self.assertEqual(2, len(programs))
        self.assertEqual(2, len({program.key for program in programs}))

    def test_sunburst_models_snapshots_and_descriptors_reject_malformed_values(self):
        descriptor = SunburstSpec("charge").handler_descriptor()
        handler = SunburstEntryCounterHandler()
        self.assertEqual(
            SunburstNode("charge", "702.44a"),
            handler.validate(descriptor),
        )
        subject = ZoneChangeSubjectSnapshot(
            object_id="object:P1",
            object_ref="P1",
            logical_object_id="logical:P1",
            owner="A",
            controller="A",
            origin="stack",
            destination="battlefield",
            destination_controller="A",
            entry_face_id="front",
            object_types=("artifact",),
            is_card_object=True,
            mana_colors_spent=("U", "W"),
        )
        self.assertEqual(("W", "U"), subject.mana_colors_spent)
        effect = handler.subject_replacement_effect(
            descriptor,
            subject=subject,
            component_id="program:sunburst:0",
        )
        self.assertEqual(2, effect.operations[0].amount)
        self.assertEqual("charge", effect.operations[0].counter_name)

        serialized = StackItem(
            stack_id="stack:1",
            ref="S1",
            kind="spell",
            controller="A",
            label="Sunburst fixture",
            mana_colors_spent=("U", "W"),
        ).to_dict()
        self.assertEqual(("W", "U"), serialized["mana_colors_spent"])
        self.assertEqual(
            ("W", "U"),
            StackItem.from_dict(
                {**serialized, "mana_colors_spent": ["W", "U"]}
            ).mana_colors_spent,
        )
        self.assertNotIn(
            "mana_colors_spent",
            StackItem(
                stack_id="stack:legacy",
                ref="S0",
                kind="spell_copy",
                controller="A",
                label="Historical fixture",
            ).to_dict(),
        )

        for constructor, args, error in (
            (SunburstSpec, ("lore",), SunburstError),
            (SunburstNode, ("charge", ""), SemanticNodeError),
            (
                StackItem,
                ("stack:bad", "S2", "spell", "A", "Bad"),
                ValueError,
            ),
        ):
            with self.subTest(constructor=constructor.__name__):
                with self.assertRaises(error):
                    if constructor is StackItem:
                        constructor(*args, mana_colors_spent=("W", "W"))
                    else:
                        constructor(*args)
        with self.assertRaises(SemanticNodeError):
            handler.validate({**descriptor, "unknown": True})
        with self.assertRaises(ZoneReplacementError):
            replace(subject, mana_colors_spent=("C",))

    def test_unsupported_sunburst_wording_remains_material_residual(self):
        for text in ("Sunburst 2", "Sunburst — charge counters"):
            with self.subTest(text=text):
                ir = self.compile(
                    replace(
                        self.noncreature,
                        oracle_text=text,
                        keywords=("Sunburst",),
                    )
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(
                    any(
                        "sunburst-unsupported-wording" in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    ),
                    ir.material_residuals,
                )

    def test_sunburst_dependencies_and_compiler_runtime_mutations_fail_closed(self):
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            self.creature,
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

        with patch("quorune.oracle_ir.sunburst_keyword_node", return_value=None):
            mutant = self.compile(self.creature)
        self.assertFalse(
            any(
                node.template_id == "sunburst-cast-entry-counter-v1"
                for node in mutant.faces[0].nodes
            )
        )

    def test_sunburst_runtime_component_is_registered_once(self):
        rows = [
            row
            for row in runtime_component_inventory()
            if row["handler_id"] == SUNBURST_HANDLER_ID
        ]
        self.assertEqual(1, len(rows))
        self.assertEqual("replacement.zone.sunburst", rows[0]["family"])
        self.assertEqual(
            ["counter.producer.sunburst"],
            rows[0]["capability_dependencies"],
        )

    def test_multiple_sunburst_instances_apply_independently(self):
        subject = ZoneChangeSubjectSnapshot(
            object_id="object:M1",
            object_ref="M1",
            logical_object_id="logical:M1",
            owner="A",
            controller="A",
            origin="stack",
            destination="battlefield",
            destination_controller="A",
            entry_face_id="front",
            object_types=("creature",),
            is_card_object=True,
            mana_colors_spent=("W", "U"),
        )
        handler = SunburstEntryCounterHandler()
        effects = tuple(
            handler.subject_replacement_effect(
                SunburstSpec("+1/+1").handler_descriptor(),
                subject=subject,
                component_id=f"program:sunburst:{index}",
            )
            for index in range(2)
        )
        snapshot = ZoneChangeReplacementSnapshot(
            revision=0,
            event_sequence=0,
            apnap_order=("A", "B"),
            source_refs=(),
            subjects=(subject,),
            effects=effects,
        )
        prepared = prepare_zone_change_replacement_snapshot(
            snapshot,
            selections=(effects[0].effect_id,),
        )[subject.object_id]
        self.assertEqual(
            [2, 2],
            [int(event.payload["amount"]) for event in prepared.counter_events],
        )


class SunburstRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        controller: str | None = None,
    ) -> CardInstance:
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
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        if zone in engine.state.players[seat].zones:
            engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def register_sunburst(self, engine, card: CardInstance):
        programs = [
            program
            for program in generated_programs(
                self.db,
                self.db.by_oracle_id(card.oracle_id),
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "sunburst-cast-entry-counter-v1"
        ]
        self.assertEqual(1, len(programs))
        engine.semantics.put(programs[0])
        return programs[0]

    def cast_with_pool(
        self,
        session,
        card: CardInstance,
        mana: dict[str, int],
    ) -> StackItem:
        engine = session.engine
        seat = card.controller
        pool = engine.state.players[seat].mana_pool
        for color in tuple(pool):
            pool[color] = 0
        for color, amount in mana.items():
            pool[color] = amount
        engine.state.active_player = seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = seat
        engine.state.priority_passes = []
        engine._cast(seat, {"card": card.ref, "pay": "auto"})
        return engine.state.stack[-1]

    @staticmethod
    def resolve_top(session, item: StackItem) -> None:
        session.engine._continue_resolution(
            stack_ref=item.ref,
            effects=[],
            destination=None,
            note="Sunburst entry fixture",
        )

    @staticmethod
    def replacement_options(session, seat: str):
        decision = StateProjector(session.engine.card_db, session.state)._decision(
            f"pilot:{seat}"
        )
        return [option["id"] for option in decision["ctx"]["options"]]

    def choose(self, session, seat: str, selection: str) -> None:
        result = session.act(
            f"pilot:{seat}",
            {"action_id": "choose", "choices": {"replacement": selection}},
        )
        self.assertTrue(result.ok, result.summary)

    def test_cast_payment_colors_create_entry_counters(self):
        cases = (
            ("Skyreach Manta", {color: 1 for color in "WUBRG"}, ("W", "U", "B", "R", "G"), "+1/+1", 5),
            ("Pentad Prism", {"W": 1, "U": 1}, ("W", "U"), "charge", 2),
            ("Pentad Prism", {"W": 2}, ("W",), "charge", 1),
        )
        for index, (name, mana, colors, counter, amount) in enumerate(cases):
            with self.subTest(name=name, mana=mana):
                session = self.session(7024400 + index)
                card = self.add_card(
                    session.engine,
                    seat="A",
                    name=name,
                    ref=f"cast-{index}",
                    zone="hand",
                )
                self.register_sunburst(session.engine, card)
                item = self.cast_with_pool(session, card, mana)
                self.assertEqual(colors, item.mana_colors_spent)
                self.resolve_top(session, item)
                self.assertEqual("battlefield", card.zone)
                self.assertEqual(amount, card.counters[counter])

    def test_printed_type_boundary_ignores_entry_type_changes(self):
        session = self.session(7024410)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Pentad Prism",
            ref="type-changed",
            zone="stack",
        )
        self.register_sunburst(engine, card)
        snapshot = capture_zone_change_replacement_snapshot(
            engine,
            ((card.object_id, "battlefield"),),
            destination_controllers={card.object_id: "A"},
            entry_characteristics={
                card.object_id: {
                    "name": "Pentad Prism",
                    "type_line": "Artifact Creature",
                    "keywords": ("Sunburst",),
                }
            },
            mana_colors_spent={card.object_id: ("W", "U")},
        )
        prepared = prepare_zone_change_replacement_snapshot(snapshot)[card.object_id]
        self.assertEqual(1, len(prepared.counter_events))
        self.assertEqual("charge", prepared.counter_events[0].payload["counter_name"])
        self.assertEqual(2, prepared.counter_events[0].payload["amount"])

    def test_zero_colors_nonstack_entry_and_spell_copy_create_no_counters(self):
        colorless = self.session(7024420)
        prism = self.add_card(
            colorless.engine,
            seat="A",
            name="Pentad Prism",
            ref="colorless",
            zone="hand",
        )
        self.register_sunburst(colorless.engine, prism)
        item = self.cast_with_pool(colorless, prism, {"C": 2})
        self.assertEqual((), item.mana_colors_spent)
        self.resolve_top(colorless, item)
        self.assertNotIn("charge", prism.counters)

        direct = self.session(7024421)
        prism = self.add_card(
            direct.engine,
            seat="A",
            name="Pentad Prism",
            ref="graveyard-entry",
            zone="graveyard",
        )
        self.register_sunburst(direct.engine, prism)
        direct.engine.move_card(prism.object_id, "battlefield")
        self.assertNotIn("charge", prism.counters)

        copied = self.session(7024422)
        engine = copied.engine
        prism_copy_source = self.add_card(
            engine,
            seat="A",
            name="Pentad Prism",
            ref="original",
            zone="stack",
        )
        self.register_sunburst(engine, prism_copy_source)
        original = StackItem(
            stack_id="stack:original",
            ref="S-original",
            kind="spell",
            controller="A",
            label=prism_copy_source.printed_name,
            card_object_id=prism_copy_source.object_id,
            default_destination="battlefield",
            visibility=list(engine.seats),
            mana_colors_spent=("W", "U"),
        )
        engine.state.stack.append(original)
        copy_item = engine._copy_stack_item(
            controller="A",
            target=original,
            targets=(),
            target_groups={},
            reason="Sunburst copy fixture",
        )
        self.assertEqual((), copy_item.mana_colors_spent)
        copy_card = engine.state.cards[copy_item.card_object_id]
        self.resolve_top(copied, copy_item)
        self.assertEqual("battlefield", copy_card.zone)
        self.assertNotIn("charge", copy_card.counters)

    def test_sunburst_counter_uses_canonical_quantity_replacement(self):
        session = self.session(7024430)
        engine = session.engine
        prism = self.add_card(
            engine,
            seat="A",
            name="Pentad Prism",
            ref="doubled",
            zone="hand",
        )
        doubling = self.add_card(
            engine,
            seat="A",
            name="Doubling Season",
            ref="doubling-season",
            zone="battlefield",
        )
        self.assertEqual("battlefield", doubling.zone)
        self.register_sunburst(engine, prism)
        item = self.cast_with_pool(session, prism, {"W": 1, "U": 1})
        self.resolve_top(session, item)
        while engine.state.pending_decision is not None:
            options = self.replacement_options(session, "A")
            self.choose(session, "A", options[0])
        self.assertEqual(4, prism.counters["charge"])

    def test_four_player_controller_scope_checkpoint_and_replay(self):
        session = self.session(7024440, players=4)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="C",
            name="Suntouched Myr",
            ref="four-player",
            zone="stack",
        )
        self.register_sunburst(engine, card)
        item = StackItem(
            stack_id="stack:four-player",
            ref="S-four-player",
            kind="spell",
            controller="C",
            label=card.printed_name,
            card_object_id=card.object_id,
            default_destination="battlefield",
            visibility=list(engine.seats),
            mana_colors_spent=("W", "U", "B"),
        )
        engine.state.stack.append(item)
        snapshot = capture_zone_change_replacement_snapshot(
            engine,
            ((card.object_id, "battlefield"),),
            destination_controllers={card.object_id: "C"},
            mana_colors_spent={card.object_id: item.mana_colors_spent},
        )
        sunburst_effect = next(
            effect
            for effect in snapshot.effects
            if effect.effect_id.startswith(SUNBURST_HANDLER_ID)
        )
        self.assertEqual("C", sunburst_effect.operations[0].placing_player)

        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        projected = {
            seat: json.dumps(
                StateProjector(self.db, engine.state)._snapshot(f"pilot:{seat}"),
                sort_keys=True,
            )
            for seat in engine.seats
        }
        for seat in engine.seats:
            hidden_ids = set(engine.state.players[seat].zones["hand"])
            for other in set(engine.seats) - {seat}:
                self.assertTrue(
                    all(object_id not in projected[other] for object_id in hidden_ids)
                )

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for _ in range(8):
            if card.zone != "stack":
                break
            seat = engine.state.priority_player
            self.assertIsNotNone(seat)
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", card.zone)
        self.assertEqual("C", card.controller)
        self.assertEqual(3, card.counters["+1/+1"])
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "sunburst-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_sunburst_runtime_mutation_is_killed(self):
        def assert_counter(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            card = self.add_card(
                engine,
                seat="A",
                name="Pentad Prism",
                ref=f"mutant-{seed}",
                zone="stack",
            )
            self.register_sunburst(engine, card)
            item = StackItem(
                stack_id=f"stack:{seed}",
                ref=f"S-{seed}",
                kind="spell",
                controller="A",
                label=card.printed_name,
                card_object_id=card.object_id,
                default_destination="battlefield",
                visibility=list(engine.seats),
                mana_colors_spent=("W", "U"),
            )
            engine.state.stack.append(item)
            self.resolve_top(session, item)
            self.assertEqual(2, card.counters.get("charge", 0))

        assert_counter(7024450)
        original = zone_replacements._zone_change_snapshot_effects

        def remove_sunburst_effects(host, subjects, active_sources, prospective_objects):
            return tuple(
                effect
                for effect in original(host, subjects, active_sources, prospective_objects)
                if not effect.effect_id.startswith(SUNBURST_HANDLER_ID)
            )

        with patch(
            "quorune.semantic_runtime.zone_replacements."
            "_zone_change_snapshot_effects",
            side_effect=remove_sunburst_effects,
        ):
            with self.assertRaises(AssertionError):
                assert_counter(7024451)


if __name__ == "__main__":
    unittest.main()
