from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.death_return import (
    DeathReturnError,
    DeathReturnSpec,
    death_return_condition_holds,
    death_return_counter_snapshot,
)
from quorune.deck import DeckLoader
from quorune.entry_counter_model import (
    EffectEntryCounter,
    EntryCounterError,
)
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
from quorune.session import CommanderSession
from quorune.semantic_choices.intent_replacement import (
    semantic_intent_identity,
    validate_semantic_intent_identity,
)
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantic_runtime.intents import ZoneMoveIntent
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "persist-undying.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "persist-undying-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class PersistUndyingCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.persist = cls.db.lookup("Putrid Goblin")
        cls.undying = cls.db.lookup("Young Wolf")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_effect_entry_counter_model_and_zone_intent_are_canonical(self):
        counter = EffectEntryCounter(
            counter_name="  -1/-1  ",
            amount=1,
            placing_player="A",
            source_ref="S1",
            rule_id="702.79a",
        )
        self.assertEqual("-1/-1", counter.counter_name)
        self.assertEqual(counter, EffectEntryCounter.from_dict(counter.to_dict()))
        intent = ZoneMoveIntent(
            actor="A",
            object_ref="persist-card",
            expected_zones=("graveyard",),
            destination="battlefield",
            reason="Persist",
            new_controller="B",
            optional_if_missing=True,
            expected_zone_change_counter=7,
            effect_entry_counters=(counter,),
        )
        kind, identity = semantic_intent_identity(intent)
        self.assertEqual("zone_move", kind)
        self.assertEqual(identity, validate_semantic_intent_identity(kind, identity))
        self.assertEqual(7, identity["expected_zone_change_counter"])
        self.assertEqual([counter.to_dict()], identity["effect_entry_counters"])

    def test_effect_entry_counter_and_intent_identity_reject_malformed_values(self):
        for values in (
            {"counter_name": "", "amount": 1},
            {"counter_name": "+1/+1", "amount": True},
            {"counter_name": "+1/+1", "amount": 0},
            {"counter_name": "+1/+1", "amount": 1, "source_ref": " S1"},
        ):
            with self.subTest(values=values):
                with self.assertRaises(EntryCounterError):
                    payload = dict(values)
                    EffectEntryCounter(
                        placing_player="A",
                        source_ref=payload.pop("source_ref", "S1"),
                        rule_id="702.93a",
                        **payload,
                    )
        with self.assertRaises(ValueError):
            ZoneMoveIntent(
                actor="A",
                object_ref="card",
                expected_zones=("graveyard",),
                destination="battlefield",
                reason="Undying",
                effect_entry_counters=(
                    EffectEntryCounter("+1/+1", 1, "A", "S1", "702.93a"),
                ),
            )
        good = ZoneMoveIntent(
            actor="A",
            object_ref="card",
            expected_zones=("graveyard",),
            destination="battlefield",
            reason="Undying",
            optional_if_missing=True,
            expected_zone_change_counter=1,
            effect_entry_counters=(
                EffectEntryCounter("+1/+1", 1, "A", "S1", "702.93a"),
            ),
        )
        kind, identity = semantic_intent_identity(good)
        for mutation in (
            {**identity, "expected_zone_change_counter": True},
            {
                **identity,
                "effect_entry_counters": [
                    {
                        **identity["effect_entry_counters"][0],
                        "amount": False,
                    }
                ],
            },
            {**identity, "unknown": "field"},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(SemanticChoiceError):
                    validate_semantic_intent_identity(kind, mutation)

    def test_death_return_model_is_closed_and_immutable(self):
        persist = DeathReturnSpec.for_keyword("Persist")
        undying = DeathReturnSpec.for_keyword("undying")
        self.assertEqual("-1/-1", persist.entry_counter)
        self.assertEqual("+1/+1", undying.entry_counter)
        source = {"+1/+1": 1}
        snapshot = death_return_counter_snapshot(source)
        source["+1/+1"] = 9
        self.assertEqual(1, snapshot["+1/+1"])
        self.assertTrue(death_return_condition_holds({}, "-1/-1"))
        self.assertFalse(death_return_condition_holds({"-1/-1": 1}, "-1/-1"))
        for malformed in ("persist 2", "revive", ""):
            with self.subTest(malformed=malformed):
                with self.assertRaises(DeathReturnError):
                    DeathReturnSpec.for_keyword(malformed)

    def test_printed_persist_and_undying_lower_each_instance_with_exact_spans(self):
        cases = (
            (self.persist, "Flying, persist, persist", "persist", "-1/-1", "702.79a"),
            (self.undying, "Reach, undying, undying", "undying", "+1/+1", "702.93a"),
        )
        for record, text, mechanic, counter, rule_id in cases:
            with self.subTest(mechanic=mechanic):
                modified = replace(
                    record,
                    oracle_text=text,
                    keywords=(mechanic.title(), text.split(",", 1)[0]),
                )
                ir = compile_oracle_card(
                    modified,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                nodes = [
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id == f"{mechanic}-death-return-counter-v1"
                ]
                self.assertEqual("exact", ir.status)
                self.assertEqual(2, len(nodes))
                self.assertEqual(2, len({node.node_id for node in nodes}))
                self.assertEqual(2, len({(node.span.start, node.span.end) for node in nodes}))
                for node in nodes:
                    self.assertEqual("creature.dies.self", node.event)
                    self.assertEqual(
                        {
                            "field": "death_return_departed_without_counter",
                            "counter": counter,
                            "op": "truthy",
                        },
                        node.event_condition,
                    )
                    self.assertEqual(
                        f"counter.producer.{mechanic}",
                        node.capability_dependencies[0],
                    )
                    self.assertEqual(rule_id, node.effects[0]["rule_id"])
                    self.assertEqual(
                        mechanic,
                        text[node.span.start : node.span.end].casefold(),
                    )
                programs = [
                    program
                    for program in generated_programs(
                        self.db,
                        modified,
                        trust_level="trusted",
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    if program.provenance.get("template_id")
                    == f"{mechanic}-death-return-counter-v1"
                ]
                self.assertEqual(2, len(programs))
                self.assertEqual(2, len({program.key for program in programs}))
                self.assertTrue(all(program.capability_closure["trusted"] for program in programs))

    def test_unsupported_death_return_wording_remains_material_residual(self):
        for record, mechanic in ((self.persist, "Persist"), (self.undying, "Undying")):
            for text in (f"{mechanic} 2", f"{mechanic} — return it tapped"):
                with self.subTest(text=text):
                    ir = compile_oracle_card(
                        replace(record, oracle_text=text, keywords=(mechanic,)),
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    self.assertNotEqual("exact", ir.status)
                    self.assertTrue(ir.material_residuals)
                    self.assertTrue(
                        any(
                            f"{mechanic.casefold()}-unsupported-wording" in blocker
                            for residual in ir.material_residuals
                            for blocker in residual.blockers
                        )
                    )

    def test_death_return_dependencies_and_compiler_mutation_fail_closed(self):
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "counter.producer.effect_entry"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            self.persist,
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(
            any(
                "counter.producer.effect_entry" in blocker
                for residual in ir.material_residuals
                for blocker in residual.blockers
            )
        )

        with patch("quorune.oracle_ir.death_return_keyword_node", return_value=None):
            programs = generated_programs(
                self.db,
                self.persist,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(
                program.provenance.get("template_id")
                == "persist-death-return-counter-v1"
                for program in programs
            )
        )


class PersistUndyingRuntimeTests(unittest.TestCase):
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
        zone: str = "battlefield",
    ) -> CardInstance:
        record = self.db.lookup(name)
        public = zone in {"battlefield", "graveyard", "exile", "command"}
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

    def register_keyword(
        self,
        engine,
        source: CardInstance,
        *,
        mechanic: str,
        repeated: bool = False,
    ):
        record = self.db.by_oracle_id(source.oracle_id)
        # Runtime fixtures grant only the keyword under test to the chosen
        # creature body.  Isolating its Oracle line prevents unrelated printed
        # abilities on a high-toughness body from becoming trust prerequisites.
        from uuid import NAMESPACE_URL, uuid5
        identity = str(uuid5(NAMESPACE_URL,
            f'quorune-death-return-fixture:{source.object_id}:{mechanic}:{int(repeated)}'))
        record = replace(
            record, oracle_id=identity, name=f'Generic {mechanic} Runtime Body',
            oracle_text=(
                f"{mechanic}, {mechanic}" if repeated else mechanic
            ),
            keywords=(mechanic,),
        )
        if not hasattr(self, '_keyword_records'):
            self._keyword_records = {}
            by_oracle_id = self.db.by_oracle_id
            self.enterContext(patch.object(self.db, 'by_oracle_id', side_effect=lambda value:
                self._keyword_records[value] if value in self._keyword_records else by_oracle_id(value)))
        self._keyword_records[identity] = record
        source.oracle_id = identity
        source.printed_name = record.name
        for program in tuple(engine.semantics.programs_for_oracle(source.oracle_id)):
            if program.event == "creature.dies.self":
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
            == f"{mechanic.casefold()}-death-return-counter-v1"
        ]
        self.assertEqual(2 if repeated else 1, len(programs))
        for program in programs:
            engine.semantics.put(program)
        return programs

    @staticmethod
    def resolve_top(engine):
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def die(self, engine, card: CardInstance) -> None:
        engine.move_card(
            card.object_id,
            "graveyard",
            reason="death-return fixture",
            semantic_events=True,
        )
        engine._stabilize()

    def stage_competing_counter_sources(self, engine, *, seat: str):
        return (
            self.add_card(
                engine,
                seat=seat,
                name="Doubling Season",
                ref=f"{seat.casefold()}-doubling",
            ),
            self.add_card(
                engine,
                seat=seat,
                name="Doc Samson, Super Psychiatrist",
                ref=f"{seat.casefold()}-doc",
            ),
        )

    def test_persist_returns_under_owner_with_replacement_aware_counter(self):
        session = self.session(7027901)
        engine = session.engine
        source = self.add_card(
            engine, seat="A", name="Protean Hulk", ref="persist-source"
        )
        self.add_card(
            engine,
            seat="A",
            name="Doubling Season",
            ref="persist-doubling",
        )
        self.register_keyword(engine, source, mechanic="Persist")

        self.die(engine, source)
        self.assertEqual("graveyard", source.zone)
        self.assertEqual("A", engine.state.stack[-1].controller)
        self.resolve_top(engine)

        self.assertEqual("battlefield", source.zone)
        self.assertEqual("A", source.controller)
        self.assertEqual(2, source.counters["-1/-1"])

    def test_undying_returns_with_plus_counter(self):
        session = self.session(7029301)
        engine = session.engine
        source = self.add_card(
            engine, seat="A", name="Young Wolf", ref="undying-source"
        )
        self.register_keyword(engine, source, mechanic="Undying")
        self.die(engine, source)
        self.resolve_top(engine)
        self.assertEqual("battlefield", source.zone)
        self.assertEqual(1, source.counters["+1/+1"])

    def test_existing_minus_counter_prevents_persist_trigger(self):
        session = self.session(7027902)
        engine = session.engine
        source = self.add_card(
            engine, seat="A", name="Putrid Goblin", ref="persist-blocked"
        )
        source.counters["-1/-1"] = 1
        self.register_keyword(engine, source, mechanic="Persist")
        self.die(engine, source)
        self.assertEqual("graveyard", source.zone)
        self.assertFalse(engine.state.stack)

    def test_existing_plus_counter_prevents_undying_trigger(self):
        session = self.session(7029302)
        engine = session.engine
        source = self.add_card(
            engine, seat="A", name="Young Wolf", ref="undying-blocked"
        )
        source.counters["+1/+1"] = 1
        self.register_keyword(engine, source, mechanic="Undying")
        self.die(engine, source)
        self.assertEqual("graveyard", source.zone)
        self.assertFalse(engine.state.stack)

    def test_repeated_persist_instances_trigger_separately_but_only_one_returns(self):
        session = self.session(7027903)
        engine = session.engine
        source = self.add_card(
            engine, seat="A", name="Putrid Goblin", ref="double-persist"
        )
        programs = self.register_keyword(
            engine,
            source,
            mechanic="Persist",
            repeated=True,
        )
        self.assertEqual(2, len({program.key for program in programs}))
        self.die(engine, source)
        self.assertEqual("trigger.order", engine.state.pending_decision.kind)
        refs = [item.ref for item in engine.state.pending_trigger_batches[0].items]
        result = session.act("pilot:A", {"action_id": "order", "triggers": refs})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            {program.key for program in programs},
            {item.semantic_key for item in engine.state.stack},
        )
        self.resolve_top(engine)
        self.resolve_top(engine)
        self.assertEqual("battlefield", source.zone)
        self.assertEqual(1, source.counters["-1/-1"])

    def test_stale_or_departed_graveyard_incarnation_does_not_return(self):
        for offset, (name, mechanic, counter) in enumerate(
            (
                ("Putrid Goblin", "Persist", "-1/-1"),
                ("Young Wolf", "Undying", "+1/+1"),
            )
        ):
            with self.subTest(mechanic=mechanic):
                session = self.session(7027904 + offset)
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name=name,
                    ref=f"stale-{mechanic.casefold()}",
                )
                self.register_keyword(engine, source, mechanic=mechanic)
                self.die(engine, source)
                expected_incarnation = engine.state.stack[-1].context[
                    "card_zone_change_counter"
                ]
                engine.move_card(source.object_id, "exile", log=False)
                engine.move_card(source.object_id, "graveyard", log=False)
                self.assertNotEqual(
                    expected_incarnation, source.zone_change_counter
                )

                self.resolve_top(engine)

                self.assertNotEqual("battlefield", source.zone)
                self.assertNotIn(counter, source.counters)

    def test_control_change_uses_returned_owner_as_counter_placing_player(self):
        for offset, (name, mechanic) in enumerate(
            (("Putrid Goblin", "Persist"), ("Young Wolf", "Undying"))
        ):
            with self.subTest(mechanic=mechanic):
                session = self.session(7027905 + offset)
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name=name,
                    ref=f"controlled-{mechanic.casefold()}",
                )
                self.register_keyword(engine, source, mechanic=mechanic)
                engine.change_control(
                    source.object_id, "B", reason="fixture control"
                )
                self.die(engine, source)
                self.assertEqual("B", engine.state.stack[-1].controller)

                self.resolve_top(engine)

                self.assertEqual("battlefield", source.zone)
                self.assertEqual("A", source.controller)
                counter_event = next(
                    event
                    for event in reversed(engine.state.events)
                    if event.code == "counter.add"
                    and event.details.get("object") == source.ref
                )
                self.assertEqual("A", counter_event.actor)

    def test_zone_and_counter_replacements_complete_before_return_mutates(self):
        for offset, (name, mechanic, counter) in enumerate(
            (
                ("Protean Hulk", "Persist", "-1/-1"),
                ("Young Wolf", "Undying", "+1/+1"),
            )
        ):
            with self.subTest(mechanic=mechanic):
                session = self.session(7027906 + offset)
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name=name,
                    ref=f"ordered-{mechanic.casefold()}",
                )
                self.stage_competing_counter_sources(engine, seat="A")
                self.register_keyword(engine, source, mechanic=mechanic)
                self.die(engine, source)
                graveyard_incarnation = source.zone_change_counter
                before_counters = dict(source.counters)

                self.resolve_top(engine)

                self.assertEqual(
                    "replacement.order", engine.state.pending_decision.kind
                )
                self.assertEqual("graveyard", source.zone)
                self.assertEqual(
                    graveyard_incarnation, source.zone_change_counter
                )
                self.assertEqual(before_counters, source.counters)
                packet = StateProjector(self.db, engine.state)._decision(
                    "pilot:A"
                )
                selection = packet["ctx"]["options"][0]["id"]
                result = session.act(
                    "pilot:A",
                    {
                        "action_id": "choose",
                        "choices": {"replacement": selection},
                    },
                )
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("battlefield", source.zone)
                self.assertIn(source.counters[counter], {3, 4})

    def test_tokens_with_death_return_keywords_do_not_return(self):
        for offset, (name, mechanic, counter) in enumerate(
            (
                ("Putrid Goblin", "Persist", "-1/-1"),
                ("Young Wolf", "Undying", "+1/+1"),
            )
        ):
            with self.subTest(mechanic=mechanic):
                session = self.session(70279061 + offset)
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name=name,
                    ref=f"token-{mechanic.casefold()}",
                )
                source.is_token = True
                self.register_keyword(engine, source, mechanic=mechanic)

                self.die(engine, source)
                self.resolve_top(engine)

                self.assertNotEqual("battlefield", source.zone)
                self.assertNotIn(counter, source.counters)

    def test_effect_entry_counter_generation_mutant_is_killed(self):
        def assert_counter(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            source = self.add_card(
                engine,
                seat="A",
                name="Putrid Goblin",
                ref=f"mutant-persist-{seed}",
            )
            self.register_keyword(engine, source, mechanic="Persist")
            self.die(engine, source)
            self.resolve_top(engine)
            self.assertEqual(1, source.counters.get("-1/-1", 0))

        assert_counter(7027907)
        with patch(
            "quorune.semantic_runtime.zone_replacements."
            "effect_entry_counter_effects",
            return_value=(),
        ):
            with self.assertRaises(AssertionError):
                assert_counter(7027908)

    def test_four_player_simultaneous_deaths_place_triggers_apnap_and_keep_choices_scoped(self):
        session = self.session(7027909, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        source_a = self.add_card(
            engine, seat="A", name="Putrid Goblin", ref="apnap-persist-a"
        )
        source_b = self.add_card(
            engine, seat="B", name="Young Wolf", ref="apnap-undying-b"
        )
        private_a = self.add_card(
            engine, seat="A", name="Young Wolf", ref="private-a", zone="hand"
        )
        private_b = self.add_card(
            engine, seat="B", name="Young Wolf", ref="private-b", zone="hand"
        )
        self.register_keyword(engine, source_a, mechanic="Persist")
        self.register_keyword(engine, source_b, mechanic="Undying")

        engine._move_cards_simultaneously(
            (
                (source_b.object_id, "graveyard"),
                (source_a.object_id, "graveyard"),
            ),
            reason="simultaneous Persist fixture",
        )

        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(["A", "B"], [group["controller"] for group in batch["groups"]])
        self.assertEqual(
            {source_a.object_id, source_b.object_id},
            {item["source_object_id"] for item in batch.items},
        )
        projector = StateProjector(self.db, engine.state)
        projected_a = json.dumps(projector._snapshot("pilot:A"), sort_keys=True)
        projected_b = json.dumps(projector._snapshot("pilot:B"), sort_keys=True)
        self.assertNotIn(private_b.ref, projected_a)
        self.assertNotIn(private_a.ref, projected_b)
        self.assertIn(source_a.ref, projected_b)
        self.assertIn(source_b.ref, projected_a)
        self.assertNotIn("zone_change_counter", projected_a)
        self.assertNotIn("zone_change_counter", projected_b)

    def test_death_return_replacement_resume_replays_exactly(self):
        for offset, (name, mechanic) in enumerate(
            (("Protean Hulk", "Persist"), ("Young Wolf", "Undying"))
        ):
            with self.subTest(mechanic=mechanic):
                session = self.session(7027910 + offset, players=4)
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name=name,
                    ref=f"replay-{mechanic.casefold()}",
                )
                self.stage_competing_counter_sources(engine, seat="A")
                self.register_keyword(engine, source, mechanic=mechanic)
                self.die(engine, source)
                self.resolve_top(engine)
                self.assertEqual(
                    "replacement.order", engine.state.pending_decision.kind
                )
                session.initial_checkpoint = checkpoint_envelope(engine.state)
                session.commands.clear()
                session.decisions.clear()
                projector = StateProjector(self.db, engine.state)
                packet = projector._decision("pilot:A")
                self.assertIsNotNone(packet)
                self.assertIsNone(projector._decision("pilot:B"))
                serialized = json.dumps(packet, sort_keys=True)
                self.assertNotIn("replacement_batch", serialized)
                self.assertNotIn(source.object_id, serialized)
                selection = packet["ctx"]["options"][0]["id"]
                result = session.act(
                    "pilot:A",
                    {
                        "action_id": "choose",
                        "choices": {"replacement": selection},
                    },
                )
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("battlefield", source.zone)
                expected_hash = authoritative_state_hash(engine.state)

                with tempfile.TemporaryDirectory() as temporary:
                    record_dir = (
                        Path(temporary)
                        / f"{mechanic.casefold()}-return-replay"
                    )
                    session.save(record_dir)
                    replay = replay_record(record_dir, self.db, verify=True)
                self.assertTrue(replay["ok"], replay)
                self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_death_return_replacement_continuation_survives_process_restart(self):
        for offset, (name, mechanic, counter) in enumerate(
            (
                ("Protean Hulk", "Persist", "-1/-1"),
                ("Young Wolf", "Undying", "+1/+1"),
            )
        ):
            with self.subTest(mechanic=mechanic):
                session = self.session(7027920 + offset, players=4)
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name=name,
                    ref=f"restart-{mechanic.casefold()}",
                )
                self.stage_competing_counter_sources(engine, seat="A")
                self.register_keyword(engine, source, mechanic=mechanic)
                self.die(engine, source)
                self.resolve_top(engine)
                self.assertEqual(
                    "replacement.order", engine.state.pending_decision.kind
                )
                session.initial_checkpoint = checkpoint_envelope(engine.state)
                session.commands.clear()
                session.decisions.clear()

                with tempfile.TemporaryDirectory() as temporary:
                    record_dir = Path(temporary) / mechanic.casefold()
                    session.save(record_dir)
                    restarted = CommanderSession.load(self.db, record_dir)
                    packet = StateProjector(
                        self.db, restarted.engine.state
                    )._decision("pilot:A")
                    self.assertIsNotNone(packet)
                    for seat in ("B", "C", "D"):
                        self.assertIsNone(
                            StateProjector(
                                self.db, restarted.engine.state
                            )._decision(f"pilot:{seat}")
                        )
                    selection = packet["ctx"]["options"][0]["id"]
                    result = restarted.act(
                        "pilot:A",
                        {
                            "action_id": "choose",
                            "choices": {"replacement": selection},
                        },
                    )
                    self.assertTrue(result.ok, result.summary)
                    restarted_source = restarted.engine.state.cards[
                        source.object_id
                    ]
                    self.assertEqual("battlefield", restarted_source.zone)
                    self.assertIn(restarted_source.counters[counter], {3, 4})
                    restarted.save(record_dir)
                    replay = replay_record(record_dir, self.db, verify=True)
                self.assertTrue(replay["ok"], replay)
                self.assertEqual(
                    authoritative_state_hash(restarted.engine.state),
                    replay["final_state_hash"],
                )


if __name__ == "__main__":
    unittest.main()
