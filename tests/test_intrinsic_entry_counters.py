from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from scripts.build_test_database import build_fixture_database
from quorune.card_programs import CardProgram
from quorune.card_programs.adapters import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.counter_placement import PreparedCounterPlacements
from quorune.deck import DeckLoader
from quorune.engine import GameRuleError
from quorune.entry_counters import (
    EntryCounterError,
    intrinsic_entry_counter_effects,
    intrinsic_entry_counters,
    validate_battle_entry_protector,
)
from quorune.model import CardInstance, StackItem
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement import (
    AffectedObject,
    ReplaceableEvent,
    ReplacementClass,
    ReplacementEffect,
    SetField,
    apply_replacement,
    replacement_choice,
)
from quorune.replacement_effects import ReplacementChoiceRequired
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.semantic_runtime import prepare_zone_change_replacement_batch
from quorune.semantics import SemanticProgram


class IntrinsicEntryCounterTests(unittest.TestCase):
    """CR 306.5b/310.4b entry counters through one replacement tree."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "entry-counters.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT
                / "tests"
                / "fixtures"
                / "counter-replacement-cards.json",
            ],
            database,
        )
        cls.db = CardDatabase(database)
        cls.capabilities = load_default_capability_registry()
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

    def add_permanent(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
    ) -> CardInstance:
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(
            card.object_id
        )
        return card

    def add_entry_card(
        self,
        engine,
        *,
        seat: str,
        ref: str,
        type_line: str,
        zone: str = "exile",
        loyalty: object | None = None,
        defense: object | None = None,
    ) -> CardInstance:
        base = self.db.lookup("Island")
        characteristics: dict[str, object] = {
            "name": ref,
            "type_line": type_line,
            "oracle_text": "",
        }
        if loyalty is not None:
            characteristics["loyalty"] = loyalty
        if defense is not None:
            characteristics["defense"] = defense
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=base.oracle_id,
            printed_name=ref,
            owner=seat,
            controller=seat,
            zone=zone,
            annotations={"copy_overrides": characteristics},
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        if zone in engine.state.players[seat].zones:
            engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def stage_competing_sources(self, engine, *, seat: str):
        prefix = seat.casefold()
        return (
            self.add_permanent(
                engine,
                seat=seat,
                name="Doubling Season",
                ref=f"{prefix}-doubling",
            ),
            self.add_permanent(
                engine,
                seat=seat,
                name="Doc Samson, Super Psychiatrist",
                ref=f"{prefix}-doc",
            ),
        )

    @staticmethod
    def card_form_record(
        *,
        type_line: str,
        loyalty: str | None = None,
        defense: str | None = None,
    ) -> CardRecord:
        suffix = (
            306_500_000_001
            if loyalty is not None
            else 310_400_000_001 if defense is not None else 110_000_000_001
        )
        return CardRecord(
            oracle_id=(
                "00000000-0000-4000-8000-" f"{suffix:012d}"
            ),
            name="Intrinsic Entry Fixture",
            mana_cost="{2}",
            mana_value=2.0,
            type_line=type_line,
            oracle_text="Lifelink",
            power=None,
            toughness=None,
            loyalty=loyalty,
            defense=defense,
            colors=(),
            color_identity=(),
            keywords=("Lifelink",),
            produced_mana=(),
            layout="normal",
            released_at="2026-01-01",
            legalities={"commander": "legal"},
            faces=(),
            raw={},
        )

    def test_intrinsic_card_form_compiler_declares_exact_typed_capability(self):
        for type_line, loyalty, defense, expected_counter in (
            ("Legendary Planeswalker — Test", "4", None, "loyalty"),
            ("Battle — Siege", None, "3", "defense"),
        ):
            with self.subTest(type_line=type_line):
                program = compile_card_program(
                    self.db,
                    self.card_form_record(
                        type_line=type_line,
                        loyalty=loyalty,
                        defense=defense,
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )
                self.assertIn(
                    "counter.producer.intrinsic_entry",
                    program.capability_dependencies,
                )
                self.assertTrue(program.trust_closure["trusted"])
                ability = next(
                    value
                    for value in program.to_dict()["abilities"]
                    if value["runtime"]["provenance"].get("source_kind")
                    == "type_line"
                )
                self.assertEqual("static", ability["kind"])
                self.assertEqual(
                    {"line": 1, "start": 0, "end": len(type_line)},
                    ability["source_span"],
                )
                self.assertEqual(
                    "type_line",
                    ability["runtime"]["provenance"]["source_kind"],
                )
                self.assertEqual(
                    expected_counter,
                    ability["runtime"]["provenance"]
                    ["card_form_descriptor"]["counter_name"],
                )
                self.assertEqual(
                    program.to_dict(),
                    CardProgram.from_dict(program.to_dict()).to_dict(),
                )

        subtype_only = compile_card_program(
            self.db,
            self.card_form_record(type_line="Creature — Planeswalker"),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertNotIn(
            "counter.producer.intrinsic_entry",
            subtype_only.capability_dependencies,
        )

    def test_intrinsic_card_form_dependency_fails_closed(self):
        registry_value = json.loads(
            (ROOT / "quorune" / "rules" / "capability-registry.json")
            .read_text(encoding="utf-8")
        )
        dependency = next(
            row
            for row in registry_value["capabilities"]
            if row["id"] == "counter.placement.quantity_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        registry = CapabilityRegistry(copy.deepcopy(registry_value))
        registry.mark_evidence_verified("0" * 64)
        with self.assertRaisesRegex(
            ValueError, "intrinsic entry-counter capability is blocked"
        ):
            compile_card_program(
                self.db,
                self.card_form_record(
                    type_line="Planeswalker — Test",
                    loyalty="4",
                ),
                capability_registry=registry,
                capability_profile="commander_review",
                trust_level="trusted",
            )

    def test_unsupported_intrinsic_card_forms_are_material_residuals(self):
        program = compile_card_program(
            self.db,
            self.card_form_record(
                type_line="Planeswalker — Test",
                loyalty="X",
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertFalse(program.trust_closure["trusted"])
        residual = next(
            value
            for value in program.residuals
            if value["kind"] == "card_form_rule"
        )
        self.assertTrue(residual["material"])
        self.assertIn(
            "counter.producer.intrinsic_entry", residual["blockers"]
        )
        self.assertIn("nonnegative integer", residual["reason"])

    def test_intrinsic_counter_model_rejects_malformed_values(self):
        expected = intrinsic_entry_counters(
            {"loyalty": "04"},
            card_types=("planeswalker",),
        )
        self.assertEqual(
            ("loyalty", 4, "306.5b"),
            (
                expected[0].counter_name,
                expected[0].amount,
                expected[0].rule_id,
            ),
        )
        for value in (None, True, 1.5, "four", "-1"):
            with self.subTest(value=value), self.assertRaises(
                EntryCounterError
            ):
                intrinsic_entry_counters(
                    {"loyalty": value},
                    card_types=("planeswalker",),
                )
        self.assertEqual(
            (),
            intrinsic_entry_counters({}, card_types=("creature",)),
        )

    def test_ordinary_planeswalker_and_battle_entry_use_counter_owner(self):
        session = self.session(3065001)
        engine = session.engine
        walker = self.add_entry_card(
            engine,
            seat="A",
            ref="walker-entry",
            type_line="Legendary Planeswalker — Test",
            loyalty="4",
        )
        battle = self.add_entry_card(
            engine,
            seat="A",
            ref="battle-entry",
            type_line="Battle — Siege",
            defense="3",
        )

        engine.move_card(
            walker.object_id,
            "battlefield",
            controller="A",
            semantic_events=False,
        )
        engine.move_card(
            battle.object_id,
            "battlefield",
            controller="A",
            battle_protector="B",
            semantic_events=False,
        )

        self.assertEqual(4, walker.counters["loyalty"])
        self.assertTrue(walker.annotations["loyalty_initialized"])
        self.assertEqual(3, battle.counters["defense"])
        self.assertEqual("B", battle.battle_protector)

    def test_intrinsic_entry_counter_generation_mutant_is_killed(self):
        def assert_entry_counter_created(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            walker = self.add_entry_card(
                engine,
                seat="A",
                ref=f"walker-mutation-{seed}",
                type_line="Planeswalker — Test",
                loyalty="4",
            )
            engine.move_card(
                walker.object_id,
                "battlefield",
                controller="A",
                semantic_events=False,
            )
            self.assertEqual(4, walker.counters.get("loyalty", 0))

        assert_entry_counter_created(3065007)
        with patch(
            "quorune.semantic_runtime.zone_replacements."
            "intrinsic_entry_counter_effects",
            return_value=(),
        ):
            with self.assertRaises(AssertionError):
                assert_entry_counter_created(3065008)

    def test_competing_entry_counter_replacements_order_to_nine_or_ten(self):
        results: set[int] = set()
        for seed in (3065002, 3065003):
            session = self.session(seed)
            engine = session.engine
            doubling, doc = self.stage_competing_sources(
                engine, seat="A"
            )
            walker = self.add_entry_card(
                engine,
                seat="A",
                ref=f"walker-order-{seed}",
                type_line="Planeswalker — Test",
                loyalty="4",
            )
            before = authoritative_state_hash(engine.state)
            with self.assertRaises(ReplacementChoiceRequired) as raised:
                engine.move_card(
                    walker.object_id,
                    "battlefield",
                    controller="A",
                    semantic_events=False,
                )
            self.assertEqual(before, authoritative_state_hash(engine.state))
            options = raised.exception.pending.choice.options
            selected_source = doubling if seed % 2 == 0 else doc
            selected = next(
                effect_id
                for effect_id in options
                if selected_source.ref in effect_id
            )
            engine.move_card(
                walker.object_id,
                "battlefield",
                controller="A",
                replacement_selections=(selected,),
                semantic_events=False,
            )
            results.add(walker.counters["loyalty"])
        self.assertEqual({9, 10}, results)

    def test_simultaneous_entry_choices_follow_four_player_apnap(self):
        session = self.session(3065004, players=4)
        engine = session.engine
        self.stage_competing_sources(engine, seat="A")
        self.stage_competing_sources(engine, seat="B")
        walker_a = self.add_entry_card(
            engine,
            seat="A",
            ref="walker-apnap-a",
            type_line="Planeswalker — Test",
            loyalty="3",
        )
        walker_b = self.add_entry_card(
            engine,
            seat="B",
            ref="walker-apnap-b",
            type_line="Planeswalker — Test",
            loyalty="3",
        )
        changes = (
            (walker_b.object_id, "battlefield"),
            (walker_a.object_id, "battlefield"),
        )
        controllers = {
            walker_a.object_id: "A",
            walker_b.object_id: "B",
        }
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(ReplacementChoiceRequired) as first:
            prepare_zone_change_replacement_batch(
                engine,
                changes,
                destination_controllers=controllers,
                error_type=EntryCounterError,
            )
        self.assertEqual("A", first.exception.pending.choice.chooser)
        first_selection = first.exception.pending.choice.options[0]
        with self.assertRaises(ReplacementChoiceRequired) as second:
            prepare_zone_change_replacement_batch(
                engine,
                changes,
                destination_controllers=controllers,
                selections=(first_selection,),
                error_type=EntryCounterError,
            )
        self.assertEqual("B", second.exception.pending.choice.chooser)
        prepare_zone_change_replacement_batch(
            engine,
            changes,
            destination_controllers=controllers,
            selections=(
                first_selection,
                second.exception.pending.choice.options[0],
            ),
            error_type=EntryCounterError,
        )
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_resolving_entry_choice_is_private_and_replays_exactly(self):
        session = self.session(3065005)
        engine = session.engine
        self.stage_competing_sources(engine, seat="A")
        walker = self.add_entry_card(
            engine,
            seat="A",
            ref="walker-resolution",
            type_line="Planeswalker — Test",
            loyalty="4",
            zone="stack",
        )
        item = StackItem(
            stack_id="walker-resolution-stack",
            ref="S-walker-resolution",
            kind="spell",
            controller="A",
            label="Test Walker",
            card_object_id=walker.object_id,
            default_destination="battlefield",
            visibility=["A", "B"],
        )
        engine.state.stack.append(item)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[],
            destination=None,
            note="entry replacement replay",
        )

        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        projector = StateProjector(self.db, engine.state)
        projected_a = projector._decision("pilot:A")
        self.assertIsNotNone(projected_a)
        self.assertIsNone(projector._decision("pilot:B"))
        serialized = json.dumps(projected_a, sort_keys=True)
        self.assertNotIn("replacement_batch", serialized)
        self.assertNotIn(walker.object_id, serialized)

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        selection = projected_a["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": selection},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", walker.zone)
        self.assertIn(walker.counters["loyalty"], {9, 10})
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "intrinsic-entry-counter-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_counter_child_tracks_a_later_destination_replacement(self):
        event = ReplaceableEvent(
            event_id="zone-entry-retarget",
            kind="zone.change",
            affected_player=None,
            affected_object=AffectedObject(
                object_id="walker-object",
                owner="A",
                controller=None,
            ),
            payload={
                "origin": "graveyard",
                "destination": "battlefield",
                "destination_controller": "A",
                "object_kind": "card",
                "object_ref": "walker-ref",
                "object_types": ["planeswalker"],
                "logical_object_id": "walker-object:1",
                "owner": "A",
            },
        )
        entry = intrinsic_entry_counter_effects(
            object_ref="walker-ref",
            destination_controller="A",
            counters=intrinsic_entry_counters(
                {"loyalty": "4"},
                card_types=("planeswalker",),
            ),
        )[0]
        entry_choice = replacement_choice(event, (entry,))
        self.assertIsNotNone(entry_choice)
        created = apply_replacement(
            entry_choice, (entry,), entry.effect_id
        )
        redirect = ReplacementEffect(
            effect_id="redirect-entry-to-exile",
            source_id="replacement-source",
            event_kind="zone.change",
            replacement_class=ReplacementClass.OTHER,
            conditions={"destination": {"eq": "battlefield"}},
            operations=(SetField(field="destination", value="exile"),),
        )
        redirect_choice = replacement_choice(created, (redirect,))
        self.assertIsNotNone(redirect_choice)
        redirected = apply_replacement(
            redirect_choice, (redirect,), redirect.effect_id
        )

        self.assertEqual("exile", redirected.payload["destination"])
        self.assertEqual("exile", redirected.children[0].payload["target_zone"])
        self.assertEqual("card", redirected.children[0].payload["target_kind"])
        self.assertIsNone(
            redirected.children[0].payload["target_controller"]
        )

    def test_token_planeswalker_entry_uses_counter_replacement_owner(self):
        session = self.session(3065006)
        engine = session.engine
        walker_ref = engine.create_token(
            "A",
            name="Token Walker",
            characteristics={
                "type_line": "Token Planeswalker — Test",
                "loyalty": "3",
            },
        )[0]
        walker = next(
            card for card in engine.state.cards.values() if card.ref == walker_ref
        )
        self.assertEqual(3, walker.counters["loyalty"])

        self.add_permanent(
            engine,
            seat="A",
            name="Doubling Season",
            ref="a-token-doubling",
        )
        doubled_ref = engine.create_token(
            "A",
            name="Replacement Token Walker",
            characteristics={
                "type_line": "Token Planeswalker — Test",
                "loyalty": "3",
            },
        )[0]
        doubled = next(
            card
            for card in engine.state.cards.values()
            if card.ref == doubled_ref
        )
        self.assertEqual(6, doubled.counters["loyalty"])
        self.assertTrue(doubled.annotations["loyalty_initialized"])

    def test_token_battle_entry_doubles_defense_and_pins_protector(self):
        session = self.session(3065009, players=4)
        engine = session.engine
        self.add_permanent(
            engine,
            seat="A",
            name="Doubling Season",
            ref="a-battle-doubling",
        )

        battle_ref = engine.create_token(
            "A",
            name="Token Siege",
            characteristics={
                "type_line": "Token Battle — Siege",
                "defense": "4",
            },
            battle_protector="B",
        )[0]
        battle = next(
            card
            for card in engine.state.cards.values()
            if card.ref == battle_ref
        )
        self.assertEqual(8, battle.counters["defense"])
        self.assertEqual("B", battle.battle_protector)

    def test_token_and_counter_replacements_resume_sequentially_and_replay(self):
        session = self.session(3065010, players=4)
        engine = session.engine
        self.stage_competing_sources(engine, seat="A")
        for name, ref in (
            ("Stridehangar Automaton", "a-stridehangar"),
            ("Worldwalker Helm", "a-worldwalker"),
        ):
            self.add_permanent(engine, seat="A", name=name, ref=ref)

        before = authoritative_state_hash(engine.state)
        with self.assertRaises(ReplacementChoiceRequired) as raised:
            engine.create_token(
                "A",
                name="Artifact Token Walker",
                characteristics={
                    "type_line": "Token Artifact Planeswalker — Test",
                    "loyalty": "4",
                },
            )
        self.assertEqual("token.create", raised.exception.batch.events[0].kind)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        program = SemanticProgram(
            key="test:token-entry-counter-replacements",
            label="Create an artifact planeswalker token",
            effects=[
                {
                    "op": "create_token",
                    "controller": "A",
                    "name": "Artifact Token Walker",
                    "characteristics": {
                        "type_line": (
                            "Token Artifact Planeswalker — Test"
                        ),
                        "loyalty": "4",
                    },
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="token-entry-counter-replacements",
            ref="S-token-entry-counter-replacements",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(value) for value in program.effects],
            destination=None,
            note="token entry counter replacement replay",
        )

        self.assertEqual(
            "token.create",
            engine.state.pending_decision.continuation[
                "replacement_batch"
            ]["events"][0]["kind"],
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        projector = StateProjector(self.db, engine.state)
        first = projector._decision("pilot:A")
        self.assertIsNotNone(first)
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        first_selection = first["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": first_selection},
            },
        )
        self.assertTrue(result.ok, result.summary)
        entry_event = engine.state.pending_decision.continuation[
            "replacement_batch"
        ]["events"][0]
        self.assertEqual("zone.change", entry_event["kind"])
        self.assertTrue(entry_event["payload"]["prospective_subject"])
        self.assertEqual(
            ["counter.place"],
            [child["kind"] for child in entry_event["children"]],
        )
        second = StateProjector(self.db, engine.state)._decision("pilot:A")
        second_selection = second["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": second_selection},
            },
        )
        self.assertTrue(result.ok, result.summary)
        walkers = [
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.zone == "battlefield"
            and "planeswalker"
            in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[0]
        ]
        self.assertEqual(1, len(walkers))
        self.assertIn(walkers[0].counters["loyalty"], {9, 10})
        self.assertEqual(
            3,
            sum(
                card.is_token and card.zone == "battlefield"
                for card in engine.state.cards.values()
            ),
        )
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "token-entry-counter-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_token_entry_counter_owner_mutant_is_killed(self):
        def assert_doubled(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            self.add_permanent(
                engine,
                seat="A",
                name="Doubling Season",
                ref=f"a-token-mutation-{seed}",
            )
            ref = engine.create_token(
                "A",
                name="Mutation Token Walker",
                characteristics={
                    "type_line": "Token Planeswalker — Test",
                    "loyalty": "3",
                },
            )[0]
            walker = next(
                card for card in engine.state.cards.values() if card.ref == ref
            )
            self.assertEqual(6, walker.counters.get("loyalty", 0))

        assert_doubled(3065011)
        from quorune import token_creation
        original_prepare = token_creation._prepare_token_entry_counters
        def omit_counter_leaves(*args, **kwargs):
            plans, prepared_counters, entries = original_prepare(*args, **kwargs)
            return plans, PreparedCounterPlacements((), (), ()), entries
        with patch(
            "quorune.token_creation._prepare_token_entry_counters",
            side_effect=omit_counter_leaves,
        ):
            with self.assertRaises(AssertionError):
                assert_doubled(3065012)

    def test_battle_protector_validation_is_closed(self):
        self.assertEqual(
            "B",
            validate_battle_entry_protector(
                card_types=("battle",),
                subtypes=("siege",),
                controller="A",
                supplied_protector="B",
                active_seats=("A", "B", "C"),
            ),
        )
        with self.assertRaises(EntryCounterError):
            validate_battle_entry_protector(
                card_types=("battle",),
                subtypes=("siege",),
                controller="A",
                supplied_protector="A",
                active_seats=("A", "B"),
            )
        with self.assertRaisesRegex(EntryCounterError, "not compiled"):
            validate_battle_entry_protector(
                card_types=("battle",),
                subtypes=("future-subtype",),
                controller="A",
                supplied_protector="B",
                active_seats=("A", "B"),
            )


if __name__ == "__main__":
    unittest.main()
