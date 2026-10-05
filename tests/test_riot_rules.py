from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.abilities import ActivatedAbility
from quorune.carddb import CardDatabase
from quorune.continuous_effect_state import (
    expire_end_of_turn_continuous_effects,
)
from quorune.continuous_effects import ContinuousEffectOrigin
from quorune.deck import DeckLoader
from quorune.entry_keyword_grants import (
    EntryKeywordGrant,
    EntryKeywordGrantError,
)
from quorune.errors import StateInvariantError
from quorune.haste import has_effective_haste
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement_effects import (
    GrantAffectedObjectKeyword,
    ReplacementEffect,
)
from quorune.replacement.operations import ReplacementOperationError
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.riot import riot_entry_handler_descriptor
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.entry_choices import (
    RiotEntryChoiceHandler,
    RiotEntryChoiceNode,
)
from quorune.semantic_runtime.zone_replacement_model import (
    ZoneChangeSubjectSnapshot,
)
from quorune.semantic_runtime import zone_replacements
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "riot.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "riot-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class RiotCompilerAndModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        cls.card = cls.db.lookup("Zhur-Taa Goblin")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_ordinary_riot_lowers_to_linked_source_spanned_programs(self):
        ir = compile_oracle_card(
            self.card,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        nodes = [
            node
            for node in ir.faces[0].nodes
            if node.template_id == "riot-linked-entry-choice-v1"
        ]
        self.assertEqual("exact", ir.status)
        self.assertEqual(1, len(nodes))
        self.assertEqual(("counter.producer.riot",), nodes[0].capability_dependencies)
        self.assertEqual(
            (0, self.card.oracle_text.index(" (")),
            (nodes[0].span.start, nodes[0].span.end),
        )
        programs = self.riot_programs(self.card)
        self.assertEqual(1, len(programs))
        self.assertTrue(programs[0].capability_closure["trusted"])

        repeated = replace(
            self.card,
            oracle_text="Riot, Riot",
            keywords=("Riot",),
        )
        repeated_ir = compile_oracle_card(
            repeated,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        repeated_nodes = [
            node
            for node in repeated_ir.faces[0].nodes
            if node.template_id == "riot-linked-entry-choice-v1"
        ]
        repeated_programs = self.riot_programs(repeated)
        self.assertEqual("exact", repeated_ir.status)
        self.assertEqual(2, len(repeated_nodes))
        self.assertEqual(2, len({node.node_id for node in repeated_nodes}))
        self.assertEqual(2, len(repeated_programs))
        self.assertEqual(2, len({program.key for program in repeated_programs}))

    def riot_programs(self, record):
        return [
            program
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "riot-linked-entry-choice-v1"
        ]

    def test_riot_models_descriptors_and_operations_reject_malformed_values(self):
        node = RiotEntryChoiceNode(" +1/+1 ", 1, " Haste ", "702.136a")
        self.assertEqual(("+1/+1", "haste"), (node.counter_name, node.alternative_keyword))
        operation = GrantAffectedObjectKeyword(" Haste ", sequence=2)
        self.assertEqual(
            {
                "op": "grant_affected_object_keyword",
                "keyword": "haste",
                "sequence": 2,
            },
            operation.to_dict(),
        )
        grant = EntryKeywordGrant("replacement:riot", " Haste ", 0)
        self.assertEqual("haste", grant.keyword)

        subject = ZoneChangeSubjectSnapshot(
            object_id="object:R1",
            object_ref="R1",
            logical_object_id="logical:R1",
            owner="A",
            controller="A",
            origin="stack",
            destination="battlefield",
            destination_controller="A",
            entry_face_id="front",
            object_types=("creature",),
            is_card_object=True,
        )
        effect = RiotEntryChoiceHandler().subject_replacement_effect(
            riot_entry_handler_descriptor(),
            subject=subject,
            component_id="program:riot:1",
        )
        serialized = effect.to_dict()
        self.assertIn("decline_operations", serialized)
        self.assertEqual(serialized, ReplacementEffect.from_dict(serialized).to_dict())
        legacy = dict(serialized)
        legacy.pop("decline_operations")
        legacy["optional"] = False
        self.assertEqual(legacy, ReplacementEffect.from_dict(legacy).to_dict())

        for constructor, args in (
            (RiotEntryChoiceNode, ("", 1, "haste", "702.136a")),
            (RiotEntryChoiceNode, ("+1/+1", True, "haste", "702.136a")),
            (RiotEntryChoiceNode, ("+1/+1", 1, "flying", "702.136a")),
            (GrantAffectedObjectKeyword, ("deathtouch",)),
            (GrantAffectedObjectKeyword, ("haste", True)),
            (EntryKeywordGrant, ("", "haste", 0)),
            (EntryKeywordGrant, ("effect", "deathtouch", 0)),
        ):
            with self.subTest(constructor=constructor.__name__):
                with self.assertRaises(
                    (SemanticNodeError, ReplacementOperationError, EntryKeywordGrantError)
                ):
                    constructor(*args)
        malformed = {**riot_entry_handler_descriptor(), "unknown": True}
        with self.assertRaises(SemanticNodeError):
            RiotEntryChoiceHandler().validate(malformed)

    def test_unsupported_riot_wording_remains_material_residual(self):
        for text in ("Riot 2", "Riot — it enters tapped"):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(self.card, oracle_text=text, keywords=("Riot",)),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(
                    any(
                        "riot-unsupported-wording" in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    )
                )

    def test_riot_dependencies_fail_closed(self):
        for capability_id in (
            "counter.placement.quantity_replacement",
            "combat.attack.haste",
            "activation.tap_untap_cost.haste",
        ):
            with self.subTest(capability_id=capability_id):
                value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                capability = next(
                    row
                    for row in value["capabilities"]
                    if row["id"] == capability_id
                )
                capability["status"] = "blocked"
                capability["blockers"] = ["test mutation"]
                ir = compile_oracle_card(
                    self.card,
                    capability_registry=CapabilityRegistry(value),
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)


class RiotRuntimeTests(unittest.TestCase):
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
        current_controller = controller or seat
        public = zone in {"battlefield", "graveyard", "exile", "command", "stack"}
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=current_controller,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats) if public else [seat],
            revealed_to=list(engine.seats) if public else [],
        )
        engine.state.cards[card.object_id] = card
        if zone in engine.state.players[seat].zones:
            engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def register_riot(self, engine, card: CardInstance, *, repeated: bool = False):
        record = self.db.by_oracle_id(card.oracle_id)
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
            == "riot-linked-entry-choice-v1"
        ]
        self.assertEqual(1, len(programs))
        if repeated:
            # Runtime multiplicity is represented by independent handler
            # instances. Keep the real card's pinned source fingerprint here;
            # compiler coverage above separately proves that repeated printed
            # instances receive distinct source spans and program identities.
            descriptor = dict(programs[0].handlers[0])
            programs = [
                replace(
                    programs[0],
                    handlers=[dict(descriptor), dict(descriptor)],
                )
            ]
        for program in programs:
            engine.semantics.put(program)
        return programs

    def begin_entry(self, session, card: CardInstance) -> None:
        engine = session.engine
        item = StackItem(
            stack_id=f"stack:{card.ref}",
            ref=f"S-{card.ref}",
            kind="spell",
            controller=card.controller,
            label=card.printed_name,
            card_object_id=card.object_id,
            default_destination="battlefield",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[],
            destination=None,
            note="Riot entry fixture",
        )

    @staticmethod
    def replacement_options(session, seat: str):
        decision = StateProjector(session.engine.card_db, session.state)._decision(
            f"pilot:{seat}"
        )
        return decision, [option["id"] for option in decision["ctx"]["options"]]

    def choose(self, session, seat: str, selection: str):
        result = session.act(
            f"pilot:{seat}",
            {"action_id": "choose", "choices": {"replacement": selection}},
        )
        self.assertTrue(result.ok, result.summary)

    def enter_with_result(
        self,
        *,
        seed: int,
        choose_haste: bool,
        players: int = 2,
        controller: str = "A",
        repeated: bool = False,
    ):
        session = self.session(seed, players=players)
        card = self.add_card(
            session.engine,
            seat="A",
            controller=controller,
            name="Zhur-Taa Goblin",
            ref=f"riot-{seed}",
            zone="stack",
        )
        self.register_riot(session.engine, card, repeated=repeated)
        self.begin_entry(session, card)
        _, options = self.replacement_options(session, controller)
        selection = next(
            option
            for option in options
            if option.startswith("decline:") == choose_haste
        )
        self.choose(session, controller, selection)
        return session, card

    def test_counter_and_haste_choices_are_linked_results(self):
        counter_session, counter_card = self.enter_with_result(
            seed=70213601,
            choose_haste=False,
        )
        self.assertEqual("battlefield", counter_card.zone)
        self.assertEqual(1, counter_card.counters.get("+1/+1", 0))
        self.assertFalse(has_effective_haste(counter_session.engine, counter_card))

        haste_session, haste_card = self.enter_with_result(
            seed=70213602,
            choose_haste=True,
        )
        self.assertEqual("battlefield", haste_card.zone)
        self.assertEqual(0, haste_card.counters.get("+1/+1", 0))
        self.assertTrue(has_effective_haste(haste_session.engine, haste_card))
        self.assertTrue(
            any(
                effect.origin is ContinuousEffectOrigin.REPLACEMENT
                and effect.source_id.startswith("replacement.zone.riot-entry-choice.v1:")
                for effect in haste_session.state.continuous_effects
            )
        )
        expire_end_of_turn_continuous_effects(haste_session.state)
        self.assertTrue(has_effective_haste(haste_session.engine, haste_card))
        haste_session.engine.move_card(
            haste_card.object_id,
            "graveyard",
            reason="Riot zone-object duration fixture",
            semantic_events=False,
        )
        self.assertFalse(has_effective_haste(haste_session.engine, haste_card))

    def test_riot_counter_uses_quantity_replacement_and_haste_uses_existing_rules(self):
        session = self.session(70213603)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Zhur-Taa Goblin",
            ref="riot-counter-interaction",
            zone="stack",
        )
        self.add_card(
            engine,
            seat="A",
            name="Doubling Season",
            ref="riot-doubling",
            zone="battlefield",
        )
        self.register_riot(engine, card)
        self.begin_entry(session, card)
        _, options = self.replacement_options(session, "A")
        self.choose(
            session,
            "A",
            next(option for option in options if not option.startswith("decline:")),
        )
        while (
            engine.state.pending_decision is not None
            and engine.state.pending_decision.kind == "replacement.order"
        ):
            _, options = self.replacement_options(session, "A")
            self.choose(session, "A", options[0])
        self.assertEqual(2, card.counters.get("+1/+1", 0))

        haste_session, haste_card = self.enter_with_result(
            seed=70213604,
            choose_haste=True,
        )
        ability = ActivatedAbility(
            ability_id="ab-riot-haste",
            line_index=0,
            oracle_line="{T}: Add {G}.",
            cost_text="{T}",
            effect_text="Add {G}.",
            zones=("battlefield",),
            mana={},
            tap_source=True,
        )
        self.assertIsNone(
            haste_session.engine._attack_declaration_error(haste_card, "A")
        )
        self.assertEqual(
            ("payable", None),
            haste_session.engine._ability_availability("A", haste_card, ability),
        )

    def test_multiple_riot_instances_are_independent(self):
        for seed, haste_choices, expected_counters, expected_grants in (
            (70213605, (False, False), 2, 0),
            (70213606, (False, True), 1, 1),
            (70213607, (True, True), 0, 2),
        ):
            with self.subTest(haste_choices=haste_choices):
                session = self.session(seed)
                card = self.add_card(
                    session.engine,
                    seat="A",
                    name="Zhur-Taa Goblin",
                    ref=f"repeated-riot-{seed}",
                    zone="stack",
                )
                self.register_riot(session.engine, card, repeated=True)
                self.begin_entry(session, card)
                for choose_haste in haste_choices:
                    _, options = self.replacement_options(session, "A")
                    selection = next(
                        option
                        for option in options
                        if option.startswith("decline:") == choose_haste
                    )
                    self.choose(session, "A", selection)
                self.assertEqual(expected_counters, card.counters.get("+1/+1", 0))
                grants = [
                    effect
                    for effect in session.state.continuous_effects
                    if effect.origin is ContinuousEffectOrigin.REPLACEMENT
                    and effect.locked_objects[0].object_id == card.object_id
                ]
                self.assertEqual(expected_grants, len(grants))
                self.assertEqual(bool(expected_grants), has_effective_haste(session.engine, card))

    def test_riot_choice_is_destination_controller_scoped_private_and_replays(self):
        session = self.session(70213608, players=4)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            controller="C",
            name="Zhur-Taa Goblin",
            ref="controlled-riot",
            zone="stack",
        )
        self.register_riot(engine, card)
        self.begin_entry(session, card)
        self.assertIsNone(StateProjector(self.db, engine.state)._decision("pilot:A"))
        projected, options = self.replacement_options(session, "C")
        self.assertIsNotNone(projected)
        self.assertNotIn(card.object_id, json.dumps(projected, sort_keys=True))
        self.assertTrue(
            all(
                session.packet(f"pilot:{seat}", full=True)["decision"] is None
                for seat in ("A", "B", "D")
            )
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.choose(
            session,
            "C",
            next(option for option in options if option.startswith("decline:")),
        )
        self.assertEqual("C", card.controller)
        self.assertTrue(has_effective_haste(engine, card))
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "riot-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_riot_commit_failure_rolls_back_without_mutation(self):
        session = self.session(70213609)
        card = self.add_card(
            session.engine,
            seat="A",
            name="Zhur-Taa Goblin",
            ref="riot-rollback",
            zone="stack",
        )
        self.register_riot(session.engine, card)
        self.begin_entry(session, card)
        _, options = self.replacement_options(session, "A")
        selection = next(option for option in options if option.startswith("decline:"))
        before = authoritative_state_hash(session.state)
        with patch(
            "quorune.entry_results.commit_entry_keyword_grants",
            side_effect=EntryKeywordGrantError("test commit failure"),
        ):
            with self.assertRaisesRegex(StateInvariantError, "test commit failure"):
                session.act(
                    "pilot:A",
                    {
                        "action_id": "choose",
                        "choices": {"replacement": selection},
                    },
                )
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertEqual("stack", session.state.cards[card.object_id].zone)

    def test_riot_runtime_mutations_are_killed(self):
        def assert_result(seed: int, *, haste_result: bool) -> None:
            session = self.session(seed)
            card = self.add_card(
                session.engine,
                seat="A",
                name="Zhur-Taa Goblin",
                ref=f"riot-mutant-{seed}",
                zone="stack",
            )
            self.register_riot(session.engine, card)
            self.begin_entry(session, card)
            if session.state.pending_decision is not None:
                _, options = self.replacement_options(session, "A")
                selection = next(
                    option
                    for option in options
                    if option.startswith("decline:") == haste_result
                )
                self.choose(session, "A", selection)
            if haste_result:
                self.assertTrue(has_effective_haste(session.engine, card))
            else:
                self.assertEqual(1, card.counters.get("+1/+1", 0))

        assert_result(70213610, haste_result=False)
        original = zone_replacements._zone_change_snapshot_effects

        def remove_riot_effects(host, subjects, active_sources, prospective_objects):
            return tuple(
                effect
                for effect in original(host, subjects, active_sources, prospective_objects)
                if not effect.effect_id.startswith(
                    "replacement.zone.riot-entry-choice.v1:"
                )
            )

        with patch(
            "quorune.semantic_runtime.zone_replacements._zone_change_snapshot_effects",
            side_effect=remove_riot_effects,
        ):
            with self.assertRaises(AssertionError):
                assert_result(70213611, haste_result=False)

        assert_result(70213612, haste_result=True)
        with patch(
            "quorune.entry_results.commit_entry_keyword_grants",
            return_value=(),
        ):
            with self.assertRaises(AssertionError):
                assert_result(70213613, haste_result=True)


if __name__ == "__main__":
    unittest.main()
