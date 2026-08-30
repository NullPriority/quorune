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
from quorune.compiler.ability_keyword_fragment_model import (
    AbilityKeywordFragmentLowering,
)
from quorune.compiler.program_generation import register_generated_programs
from quorune.deck import DeckLoader
from quorune.model import CardInstance
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
from quorune.storm import STORM_SEMANTIC_KEY
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
STORM_FIXTURE = ROOT / "tests" / "fixtures" / "storm-cards.json"
STORM_TEMPLATE = "storm-stack-cast-trigger-v1"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "storm.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "prowess-cards.json",
            STORM_FIXTURE,
        ],
        database,
    )
    return CardDatabase(database)


class StormCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.record = cls.db.lookup("Weather the Storm")
        cls.capabilities = load_default_capability_registry()
        fixture = json.loads(STORM_FIXTURE.read_text(encoding="utf-8"))
        cls.storm_names = tuple(card["name"] for card in fixture["cards"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_all_real_storm_cards_lower_typed_stack_trigger(self):
        self.assertEqual(33, len(self.storm_names))
        exact_cards = set()
        program_count = 0
        for name in self.storm_names:
            with self.subTest(card=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                nodes = [
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id == STORM_TEMPLATE
                ]
                self.assertEqual(1, len(nodes))
                node = nodes[0]
                self.assertTrue(node.exact)
                self.assertEqual("triggered_ability", node.kind)
                self.assertEqual("stack", node.active_zone)
                self.assertEqual("spell.cast", node.event)
                self.assertEqual(("typed_storm_resolution",), node.runtime_coverage)
                self.assertEqual(
                    ("trigger.keyword.storm",),
                    node.capability_dependencies,
                )
                self.assertEqual(
                    "storm",
                    record.oracle_text[node.span.start : node.span.end].casefold(),
                )
                self.assertEqual(
                    "ability.trigger.storm.v1",
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
                    if program.provenance.get("template_id") == STORM_TEMPLATE
                ]
                self.assertEqual(1, len(programs))
                self.assertTrue(programs[0].capability_closure["trusted"])
                program_count += len(programs)
                if ir.status == "exact":
                    exact_cards.add(name)

        self.assertEqual(33, program_count)
        self.assertEqual(
            {
                "Amphibian Downpour",
                "Astral Steel",
                "Brain Freeze",
                "Chatterstorm",
                "Dragonstorm",
                "Empty the Warrens",
                "Grapeshot",
                "Hunting Pack",
                "Radstorm",
                "Reaping the Graves",
                "Scattershot",
                "Stormscale Scion",
                "Tendrils of Agony",
                "Tempest Technique",
                "Temporal Fissure",
                "Volcanic Awakening",
                "Weather the Storm",
            },
            exact_cards,
        )

    def test_multiple_storm_instances_compile_separately(self):
        record = replace(
            self.record,
            oracle_text="You gain 3 life.\nStorm, storm",
            keywords=("Storm",),
        )
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        nodes = [
            node
            for node in ir.faces[0].nodes
            if node.template_id == STORM_TEMPLATE
        ]

        self.assertEqual(2, len(nodes))
        self.assertEqual(2, len({node.node_id for node in nodes}))
        self.assertEqual(
            2,
            len({(node.span.start, node.span.end) for node in nodes}),
        )
        self.assertTrue(all(node.exact for node in nodes))

    def test_unsupported_storm_wording_remains_material_residual(self):
        for text in (
            "You gain 3 life.\nStorm 2",
            "You gain 3 life.\nStorm — if you cast this from your hand",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(
                        self.record,
                        oracle_text=text,
                        keywords=("Storm",),
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(
                    any(
                        "storm-unsupported-wording" in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    )
                )

    def test_storm_dependency_and_compiler_mutations_fail_closed(self):
        for dependency_id in (
            "target.revalidate_resolution",
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

        with patch(
            "quorune.compiler.storm_nodes.lower_ability_keyword_fragments",
            return_value=AbilityKeywordFragmentLowering(),
        ):
            ir = compile_oracle_card(
                self.record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        node = next(
            value
            for value in ir.faces[0].nodes
            if value.template_id == STORM_TEMPLATE
        )
        self.assertFalse(node.exact)
        self.assertFalse(node.handlers)
        self.assertTrue(
            any(
                "ability.trigger.storm.v1" in blocker
                for residual in ir.material_residuals
                for blocker in residual.blockers
            )
        )


class StormRuntimeTests(unittest.TestCase):
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

    def session(self, seed: int, *, players: int = 4):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
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

    def add_card(self, engine, *, seat: str, name: str, ref: str, zone: str):
        record = self.db.lookup(name)
        public = zone in {"battlefield", "exile", "graveyard", "stack"}
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats) if public else [seat],
            revealed_to=list(engine.seats) if public else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def register(self, engine, *names: str) -> None:
        register_generated_programs(
            self.db,
            engine.semantics,
            tuple(self.db.lookup(name) for name in names),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
            promote_exact_effect_programs=True,
        )

    @staticmethod
    def record_prior_spell(engine, seat: str, ordinal: int) -> None:
        engine._log(
            seat,
            "stack.cast",
            f"Prior spell {ordinal}.",
            {"stack": f"prior-{ordinal}"},
        )
        engine._record_turn_history(
            "spell_cast",
            actor=seat,
            object_incarnation=f"prior:{ordinal}",
            types=("instant",),
        )

    def cast_storm(
        self,
        engine,
        name: str,
        *,
        ref: str,
        targets: list[str] | None = None,
    ) -> CardInstance:
        self.register(engine, name)
        source = self.add_card(
            engine,
            seat="A",
            name=name,
            ref=ref,
            zone="hand",
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        for symbol in ("C", "R", "G", "U", "B", "W"):
            engine.state.players["A"].mana_pool[symbol] = 20
        command = {"card": source.ref, "pay": "auto"}
        if targets is not None:
            command["targets"] = targets
        engine._cast("A", command)
        return source

    @staticmethod
    def begin_top_resolution(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def resolve_without_choice(self, engine) -> None:
        self.begin_top_resolution(engine)
        self.assertIsNone(engine.state.pending_decision)

    def creature_target(self, engine, *, seat: str, name: str) -> CardInstance:
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "3",
                "toughness": "3",
            },
        )[0]
        return next(card for card in engine.state.cards.values() if card.ref == ref)

    def targeted_storm_choice(self, session, *, prior_spells: int = 2):
        engine = session.engine
        first = self.creature_target(engine, seat="B", name="Storm Target One")
        second = self.creature_target(engine, seat="C", name="Storm Target Two")
        for ordinal in range(prior_spells):
            self.record_prior_spell(engine, "BCD"[ordinal % 3], ordinal + 1)
        source = self.cast_storm(
            engine,
            "Grapeshot",
            ref=f"grapeshot-{engine.state.config.seed}",
            targets=[first.ref],
        )
        trigger = engine.state.stack[-1]
        self.assertEqual(STORM_SEMANTIC_KEY, trigger.semantic_key)
        self.assertEqual(prior_spells, trigger.context["copy_count"])
        self.begin_top_resolution(engine)
        self.assertEqual("semantic.storm", engine.state.pending_decision.kind)
        return source, trigger, first, second

    def test_nontargeted_storm_copies_prior_casts_without_extra_choice(self):
        session = self.session(7024001)
        engine = session.engine
        self.record_prior_spell(engine, "B", 1)
        self.record_prior_spell(engine, "C", 2)
        life_before = engine.state.players["A"].life
        self.cast_storm(
            engine,
            "Weather the Storm",
            ref="weather-storm-source",
        )
        trigger = engine.state.stack[-1]

        self.assertEqual(STORM_SEMANTIC_KEY, trigger.semantic_key)
        self.assertEqual(2, trigger.context["copy_count"])
        self.resolve_without_choice(engine)
        self.assertEqual(
            2,
            sum(item.kind == "spell_copy" for item in engine.state.stack),
        )
        while engine.state.stack:
            self.resolve_without_choice(engine)
        self.assertEqual(life_before + 9, engine.state.players["A"].life)
        self.assertEqual(3, len(engine._current_turn_history("spell_cast")))

    def test_zero_prior_spells_resolves_without_copies(self):
        session = self.session(7024002)
        engine = session.engine
        source = self.cast_storm(
            engine,
            "Weather the Storm",
            ref="zero-storm-source",
        )
        trigger = engine.state.stack[-1]
        self.assertEqual(0, trigger.context["copy_count"])

        self.resolve_without_choice(engine)

        self.assertFalse(
            any(item.kind == "spell_copy" for item in engine.state.stack)
        )
        self.assertTrue(
            any(item.card_object_id == source.object_id for item in engine.state.stack)
        )

    def test_legacy_zero_prior_spells_excludes_the_current_cast(self):
        session = self.session(7024011)
        engine = session.engine
        engine.state.turn_history = None

        self.cast_storm(
            engine,
            "Weather the Storm",
            ref="legacy-zero-storm-source",
        )
        trigger = engine.state.stack[-1]

        self.assertEqual(0, trigger.context["copy_count"])
        self.assertEqual(
            1,
            sum(
                event.code == "stack.cast"
                and event.turn_sequence == engine.state.turn_sequence
                for event in engine.state.events
            ),
        )

    def test_legacy_prior_spells_are_snapshotted_and_replay_exactly(self):
        session = self.session(7024012)
        engine = session.engine
        engine.state.turn_history = None
        self.record_prior_spell(engine, "B", 1)
        self.record_prior_spell(engine, "C", 2)

        self.cast_storm(
            engine,
            "Weather the Storm",
            ref="legacy-two-storm-source",
        )
        trigger = engine.state.stack[-1]

        self.assertEqual(2, trigger.context["copy_count"])
        self.assertEqual(
            3,
            sum(
                event.code == "stack.cast"
                and event.turn_sequence == engine.state.turn_sequence
                for event in engine.state.events
            ),
        )
        self.assertIsNone(engine.state.turn_history)

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "legacy-storm-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_source_departure_does_not_erase_locked_storm_trigger(self):
        session = self.session(7024003)
        engine = session.engine
        self.record_prior_spell(engine, "B", 1)
        source = self.cast_storm(
            engine,
            "Weather the Storm",
            ref="departed-storm-source",
        )
        trigger = engine.state.stack[-1]
        source_item = next(
            item for item in engine.state.stack if item.card_object_id == source.object_id
        )
        engine._counter_stack_item(
            source_item.ref,
            reason="focused Storm source departure",
            as_rule=False,
            countered_by="B",
        )

        self.assertEqual("graveyard", source.zone)
        self.assertIn(trigger, engine.state.stack)
        self.resolve_without_choice(engine)
        copies = [item for item in engine.state.stack if item.kind == "spell_copy"]
        self.assertEqual(1, len(copies))

    def test_targeted_storm_copies_retarget_and_revalidate_independently(self):
        session = self.session(7024004)
        engine = session.engine
        _source, _trigger, first, second = self.targeted_storm_choice(session)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "copy_targets": [[first.ref], [second.ref]],
            },
        )

        self.assertTrue(result.ok, result.summary)
        copies = [item for item in engine.state.stack if item.kind == "spell_copy"]
        self.assertEqual([[first.ref], [second.ref]], [item.targets for item in copies])
        engine.move_card(
            second.object_id,
            "graveyard",
            reason="focused stale Storm copy target",
        )
        self.resolve_without_choice(engine)
        self.resolve_without_choice(engine)
        self.assertEqual(1, first.marked_damage)
        self.assertEqual(0, second.marked_damage)

    def test_storm_and_prowess_share_one_trigger_order_batch(self):
        session = self.session(7024005)
        engine = session.engine
        self.register(engine, "Monastery Swiftspear", "Weather the Storm")
        prowess = self.add_card(
            engine,
            seat="A",
            name="Monastery Swiftspear",
            ref="storm-prowess-source",
            zone="battlefield",
        )
        self.cast_storm(
            engine,
            "Weather the Storm",
            ref="storm-prowess-spell",
        )

        self.assertEqual("trigger.order", engine.state.pending_decision.kind)
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(2, len(batch.items))
        self.assertEqual(
            1,
            sum(item.source_ability_id == STORM_SEMANTIC_KEY for item in batch.items),
        )
        self.assertTrue(
            any(item.source_object_id == prowess.object_id for item in batch.items)
        )
        self.assertFalse(
            any(item.semantic_key == STORM_SEMANTIC_KEY for item in engine.state.stack)
        )

    def test_spell_copies_do_not_increase_the_next_storm_count(self):
        session = self.session(7024006)
        engine = session.engine
        self.record_prior_spell(engine, "B", 1)
        self.cast_storm(
            engine,
            "Weather the Storm",
            ref="first-counted-storm",
        )
        self.resolve_without_choice(engine)
        self.assertEqual(1, sum(item.kind == "spell_copy" for item in engine.state.stack))
        self.cast_storm(
            engine,
            "Weather the Storm",
            ref="second-counted-storm",
        )
        trigger = engine.state.stack[-1]

        self.assertEqual(STORM_SEMANTIC_KEY, trigger.semantic_key)
        self.assertEqual(2, trigger.context["copy_count"])
        self.assertEqual(3, len(engine._current_turn_history("spell_cast")))

    def test_runtime_requires_typed_storm_descriptor(self):
        session = self.session(7024007)
        engine = session.engine
        self.record_prior_spell(engine, "B", 1)
        with patch("quorune.storm.compiled_storm_specs", return_value=()):
            source = self.cast_storm(
                engine,
                "Weather the Storm",
                ref="descriptor-required-storm",
            )

        self.assertFalse(
            any(item.semantic_key == STORM_SEMANTIC_KEY for item in engine.state.stack)
        )
        self.assertTrue(
            any(item.card_object_id == source.object_id for item in engine.state.stack)
        )

    def test_stale_or_malformed_storm_choice_rolls_back(self):
        session = self.session(7024008)
        engine = session.engine
        _source, _trigger, first, second = self.targeted_storm_choice(session)
        engine.move_card(
            second.object_id,
            "graveyard",
            reason="focused stale Storm submission",
        )
        before = authoritative_state_hash(engine.state)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "copy_targets": [[first.ref], [second.ref]],
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        session = self.session(7024009)
        engine = session.engine
        self.targeted_storm_choice(session)
        malformed = deepcopy(engine.state.pending_decision.continuation)
        malformed["selection"]["payload"]["copy_count"] = 99
        engine.state.pending_decision.continuation = malformed
        before = authoritative_state_hash(engine.state)
        result = session.act(
            "pilot:A",
            {"action_id": "choose", "copy_targets": [[], []]},
        )
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_four_player_storm_choice_is_public_and_replays_exactly(self):
        session = self.session(7024010, players=4)
        engine = session.engine
        _source, _trigger, first, second = self.targeted_storm_choice(
            session,
            prior_spells=1,
        )
        projected = StateProjector(self.db, engine.state)._decision("pilot:A")
        self.assertIsNotNone(projected)
        serialized = json.dumps(projected, sort_keys=True)
        self.assertNotIn("object_id", serialized)
        self.assertNotIn("logical_object_id", serialized)
        self.assertIn(first.ref, serialized)
        self.assertIn(second.ref, serialized)
        for seat in ("B", "C", "D"):
            self.assertIsNone(
                StateProjector(self.db, engine.state)._decision(f"pilot:{seat}")
            )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {"action_id": "choose", "copy_targets": [[second.ref]]},
        )

        self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "storm-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
