from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from common import DB_PATH, ROOT, keep_all, make_session
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.counter_replacement_templates import (
    static_counter_quantity_replacement_handler,
)
from quorune.compiler.runtime_templates import static_runtime_template
from quorune.counter_placement import (
    CounterPlacementError,
    CounterPlacementRequest,
    prepare_counter_placements,
)
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement.replay import ReplacementContinuation
from quorune.replacement_effects import ReplacementEffectError
from quorune.semantic_runtime import (
    CounterPlacementEventSpec,
    CounterQuantityReplacementV2Handler,
    CounterReplacementSourceContext,
    SemanticNodeError,
    resolve_counter_placement_replacements,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)


def descriptor(
    *,
    effect_scope: str = "any",
    placing_player_relation: str = "source_controller",
    target_controller_relation: str = "source_controller",
    target_kinds: tuple[str, ...] = ("permanent",),
    counter_names: tuple[str, ...] = (),
    target_types_all: tuple[str, ...] = (),
    target_types_any: tuple[str, ...] = (),
    multiplier: int = 1,
    additional: int = 1,
) -> dict:
    return {
        "handler_id": "replacement.counter.quantity.v2",
        "schema_version": 2,
        "event": "counter.place",
        "condition": {
            "effect_scope": effect_scope,
            "placing_player_relation": placing_player_relation,
            "target_controller_relation": target_controller_relation,
            "target_kinds": list(target_kinds),
            "counter_names": list(counter_names),
            "target_types_all": list(target_types_all),
            "target_types_any": list(target_types_any),
        },
        "modification": {
            "multiplier": multiplier,
            "additional": additional,
        },
    }


class CounterQuantityReplacementV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_closed_descriptor_distinguishes_effects_costs_players_and_types(self):
        handler = CounterQuantityReplacementV2Handler()
        with self.assertRaisesRegex(SemanticNodeError, "effect_scope"):
            handler.validate(descriptor(effect_scope="sometimes"))
        with self.assertRaisesRegex(SemanticNodeError, "player scope"):
            handler.validate(
                descriptor(
                    target_kinds=("permanent", "player"),
                    target_types_all=("creature",),
                )
            )
        with self.assertRaisesRegex(SemanticNodeError, "unique"):
            handler.validate(
                descriptor(target_types_any=("Creature", "creature"))
            )
        with self.assertRaisesRegex(SemanticNodeError, "unsupported"):
            handler.validate(descriptor(target_types_all=("contraption",)))
        with self.assertRaisesRegex(SemanticNodeError, "combine all and any"):
            handler.validate(
                descriptor(
                    target_types_all=("creature",),
                    target_types_any=("artifact",),
                )
            )

        effect_only = handler.replacement_effect(
            descriptor(effect_scope="effect_only", multiplier=2, additional=0),
            CounterReplacementSourceContext("doubling", "A"),
        )
        any_placement = handler.replacement_effect(
            descriptor(),
            CounterReplacementSourceContext("doc", "A"),
        )
        cost_event = CounterPlacementEventSpec(
            event_id="loyalty-cost",
            subject_kind="permanent",
            subject_id="walker",
            owner="A",
            controller="A",
            target_zone="battlefield",
            target_types=("planeswalker",),
            placing_player="A",
            counter_name="loyalty",
            amount=2,
            source_ref="walker",
            effect_generated=False,
        ).event()
        effect_resolution = resolve_counter_placement_replacements(
            batch_id="effect-only-cost",
            events=(cost_event,),
            effects=(effect_only,),
            apnap_order=("A", "B"),
        )
        any_resolution = resolve_counter_placement_replacements(
            batch_id="any-cost",
            events=(cost_event,),
            effects=(any_placement,),
            apnap_order=("A", "B"),
        )
        self.assertEqual(2, effect_resolution.batch.events[0].payload["amount"])
        self.assertEqual(3, any_resolution.batch.events[0].payload["amount"])

        player_event = CounterPlacementEventSpec(
            event_id="player-counter",
            subject_kind="player",
            subject_id="B",
            placing_player="A",
            counter_name="poison",
            amount=2,
            source_ref="vorinclex",
            effect_generated=False,
        ).event()
        player_effect = handler.replacement_effect(
            descriptor(
                target_controller_relation="any",
                target_kinds=("player",),
                multiplier=2,
                additional=0,
            ),
            CounterReplacementSourceContext("vorinclex", "A"),
        )
        player_resolution = resolve_counter_placement_replacements(
            batch_id="player-counter-batch",
            events=(player_event,),
            effects=(player_effect,),
            apnap_order=("A", "B", "C", "D"),
        )
        self.assertEqual(
            4, player_resolution.batch.events[0].payload["amount"]
        )

        typed_event = CounterPlacementEventSpec(
            event_id="typed-counter",
            subject_kind="permanent",
            subject_id="target",
            owner="B",
            controller="B",
            target_zone="battlefield",
            target_types=("artifact",),
            placing_player="A",
            counter_name="charge",
            amount=1,
            source_ref="source",
            effect_generated=True,
        ).event()
        typed_effect = handler.replacement_effect(
            descriptor(
                placing_player_relation="any",
                target_controller_relation="any",
                target_types_any=("artifact", "creature"),
            ),
            CounterReplacementSourceContext("source", "A"),
        )
        typed_resolution = resolve_counter_placement_replacements(
            batch_id="typed-counter-batch",
            events=(typed_event,),
            effects=(typed_effect,),
            apnap_order=("A", "B"),
        )
        self.assertEqual(
            2, typed_resolution.batch.events[0].payload["amount"]
        )

    def test_generic_compiler_accepts_closed_family_and_rejects_near_misses(self):
        examples = {
            "If one or more +1/+1 counters would be put on a creature you control, that many plus one +1/+1 counters are put on it instead.": (
                "any",
                ["creature"],
                [],
            ),
            "If one or more +1/+1 counters would be put on an artifact or creature you control, twice that many +1/+1 counters are put on it instead.": (
                "any",
                [],
                ["artifact", "creature"],
            ),
            "If an effect would put one or more counters on a permanent you control, it puts twice that many of those counters on that permanent instead.": (
                "effect_only",
                [],
                [],
            ),
            "If you would put one or more counters on a permanent or player, put twice that many of each of those kinds of counters on that permanent or player instead.": (
                "any",
                [],
                [],
            ),
        }
        for text, expected in examples.items():
            with self.subTest(text=text):
                compiled = static_counter_quantity_replacement_handler(text)
                self.assertIsNotNone(compiled)
                condition = compiled[1]["condition"]
                self.assertEqual(expected[0], condition["effect_scope"])
                self.assertEqual(expected[1], condition["target_types_all"])
                self.assertEqual(expected[2], condition["target_types_any"])

        for text in (
            "If one or more +1/+1 counters would be put on another creature you control, that many plus one +1/+1 counters are put on it instead.",
            "If one or more counters would be put on a permanent your team controls, that many plus one of each of those kinds of counters are put on that permanent instead.",
            "If an opponent would put one or more counters on a permanent or player, they put half that many counters there instead, rounded down.",
            "If one or more -1/-1 counters would be put on a creature you control, that many minus one are put on it instead.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    static_counter_quantity_replacement_handler(text)
                )

        class_text = (
            "If one or more +1/+1 counters would be put on a creature you "
            "control, that many plus one +1/+1 counters are put on it instead."
        )
        self.assertIsNotNone(static_runtime_template(class_text))
        self.assertIsNone(
            static_runtime_template(class_text, source_is_class=True)
        )

    def test_generic_compiler_emits_exact_source_spanned_card_program_node(self):
        text = (
            "If one or more +1/+1 counters would be put on a creature you "
            "control, that many plus one +1/+1 counters are put on it instead."
        )
        record = CardRecord(
            oracle_id="00000000-0000-4000-8000-000000606416",
            name="Counter Quantity Fixture",
            mana_cost="{1}{G}",
            mana_value=2.0,
            type_line="Enchantment",
            oracle_text=text,
            power=None,
            toughness=None,
            loyalty=None,
            defense=None,
            colors=("G",),
            color_identity=("G",),
            keywords=(),
            produced_mana=(),
            layout="normal",
            released_at="2026-01-01",
            legalities={"commander": "legal"},
            faces=(),
            raw={},
        )
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        nodes = [
            node
            for face in ir.faces
            for node in face.nodes
            if node.event == "counter.place"
        ]
        self.assertEqual(1, len(nodes))
        node = nodes[0]
        self.assertTrue(node.exact)
        self.assertEqual(text[node.span.start : node.span.end], node.text)
        self.assertEqual(
            "replacement.counter.quantity.v2",
            node.handlers[0]["handler_id"],
        )
        program = next(
            value
            for value in generated_programs(
                self.db,
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if value.event == "counter.place"
        )
        self.assertEqual(
            {
                "start": node.span.start,
                "end": node.span.end,
                "line": node.span.line,
            },
            program.provenance["source_span"],
        )

    def test_positive_loyalty_cost_declares_fine_grained_capability(self):
        record = CardRecord(
            oracle_id="00000000-0000-4000-8000-000000606004",
            name="Loyalty Cost Fixture",
            mana_cost="{2}{U}",
            mana_value=3.0,
            type_line="Legendary Planeswalker — Fixture",
            oracle_text="+1: Draw a card.\n-1: Draw a card.",
            power=None,
            toughness=None,
            loyalty="3",
            defense=None,
            colors=("U",),
            color_identity=("U",),
            keywords=(),
            produced_mana=(),
            layout="normal",
            released_at="2026-01-01",
            legalities={"commander": "legal"},
            faces=(),
            raw={},
        )
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        abilities = [
            node
            for face in ir.faces
            for node in face.nodes
            if node.kind == "activated_ability"
        ]
        plus = next(
            node for node in abilities if node.cost["loyalty_delta"] == 1
        )
        minus = next(
            node for node in abilities if node.cost["loyalty_delta"] == -1
        )
        self.assertIn(
            "activation.loyalty.positive_counter_cost",
            plus.capability_dependencies,
        )
        self.assertNotIn(
            "activation.loyalty.positive_counter_cost",
            minus.capability_dependencies,
        )

        registry_value = json.loads(
            (ROOT / "quorune" / "rules" / "capability-registry.json")
            .read_text(encoding="utf-8")
        )
        capability = next(
            row
            for row in registry_value["capabilities"]
            if row["id"] == "activation.loyalty.positive_counter_cost"
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["test mutation"]
        blocked_registry = CapabilityRegistry(registry_value)
        blocked_registry.mark_evidence_verified("0" * 64)
        blocked_ir = compile_oracle_card(
            record,
            capability_registry=blocked_registry,
            capability_profile="commander_review",
        )
        blocked_face, blocked_plus = next(
            (face, node)
            for face in blocked_ir.faces
            for node in face.nodes
            if node.kind == "activated_ability"
            and node.cost["loyalty_delta"] == 1
        )
        self.assertFalse(blocked_plus.exact)
        self.assertTrue(
            any(
                any(
                    blocker.startswith(
                        "capability:status:"
                        "activation.loyalty.positive_counter_cost:"
                    )
                    for blocker in residual.blockers
                )
                for residual in blocked_face.residuals
                if residual.residual_id in blocked_plus.residual_ids
            )
        )

    def test_player_counter_choices_follow_four_player_apnap(self):
        handler = CounterQuantityReplacementV2Handler()
        effects = tuple(
            handler.replacement_effect(
                descriptor(
                    placing_player_relation="any",
                    target_controller_relation="any",
                    target_kinds=("player",),
                    multiplier=multiplier,
                    additional=additional,
                ),
                CounterReplacementSourceContext(
                    source_ref,
                    source_controller,
                    component_id=component_id,
                ),
            )
            for source_ref, source_controller, component_id, multiplier, additional in (
                ("source-A", "A", "double", 2, 0),
                ("source-C", "C", "add", 1, 1),
            )
        )
        events = tuple(
            CounterPlacementEventSpec(
                event_id=f"poison-{seat}",
                subject_kind="player",
                subject_id=seat,
                placing_player="A",
                counter_name="poison",
                amount=1,
                source_ref="spell",
                effect_generated=True,
            ).event()
            for seat in ("D", "B")
        )
        first = resolve_counter_placement_replacements(
            batch_id="four-player-counters",
            events=events,
            effects=effects,
            apnap_order=("A", "B", "C", "D"),
        )
        self.assertEqual("B", first.pending.choice.chooser)
        after_b = resolve_counter_placement_replacements(
            batch_id="four-player-counters",
            events=events,
            effects=effects,
            apnap_order=("A", "B", "C", "D"),
            selections=(effects[0].effect_id,),
        )
        self.assertEqual("D", after_b.pending.choice.chooser)


class LoyaltyCounterCostReplacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "loyalty-costs.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
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
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        return session

    def add_permanent(self, session, name: str, ref: str) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        from common import register_current_fixture_programs
        register_current_fixture_programs(self.db, engine.semantics, (record,),
                                          profile=engine.state.config.review_profile)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["A"].zones["battlefield"].append(card.object_id)
        return card

    def stage_daretti(self, session):
        engine = session.engine
        daretti = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Daretti, Scrap Savant"
        )
        engine.move_card(
            daretti.object_id, "battlefield", controller="A", log=False
        )
        plus = engine._activated_abilities(daretti)[0]
        return daretti, plus

    @staticmethod
    def issue_priority(session) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority("A")
        engine._issue_priority("A")

    def test_doubling_season_ignores_positive_loyalty_cost_but_doc_applies(self):
        doubling_session = self.session(6061401)
        doubling, plus = self.stage_daretti(doubling_session)
        self.add_permanent(doubling_session, "Doubling Season", "A-doubling")
        before = doubling.counters["loyalty"]
        doubling_session.engine._activate(
            "A", {"source": doubling.ref, "ability": plus.ability_id}
        )
        self.assertEqual(before + plus.loyalty_delta, doubling.counters["loyalty"])

        doc_session = self.session(6061402)
        daretti, plus = self.stage_daretti(doc_session)
        self.add_permanent(
            doc_session, "Doc Samson, Super Psychiatrist", "A-doc"
        )
        before = daretti.counters["loyalty"]
        doc_session.engine._activate(
            "A", {"source": daretti.ref, "ability": plus.ability_id}
        )
        self.assertEqual(
            before + plus.loyalty_delta + 1,
            daretti.counters["loyalty"],
        )

    def test_negative_loyalty_removal_is_not_counter_placement(self):
        session = self.session(6061404)
        daretti, _plus = self.stage_daretti(session)
        self.add_permanent(session, "Doc Samson, Super Psychiatrist", "A-doc")
        minus = next(
            ability
            for ability in session.engine._activated_abilities(daretti)
            if ability.loyalty_delta == -2
        )
        target = next(
            card
            for card in session.engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Sol Ring"
        )
        session.engine.move_card(target.object_id, "graveyard", log=False)
        before = daretti.counters["loyalty"]
        session.engine._activate(
            "A",
            {
                "source": daretti.ref,
                "ability": minus.ability_id,
                "targets": [target.ref],
            },
        )
        self.assertEqual(before - 2, daretti.counters["loyalty"])

    def test_pinned_counter_event_ids_and_internal_fields_fail_closed(self):
        session = self.session(6061405)
        daretti, plus = self.stage_daretti(session)
        request = CounterPlacementRequest(
            subject_kind="permanent",
            subject_id=daretti.object_id,
            counter_name="loyalty",
            amount=plus.loyalty_delta,
            placing_player="A",
            source_ref=daretti.ref,
            effect_generated=False,
        )
        for malformed in ("event", (" event",), ("event", "event")):
            with self.subTest(event_ids=malformed):
                with self.assertRaises(CounterPlacementError):
                    prepare_counter_placements(
                        session.engine,
                        (request,),
                        event_ids=malformed,
                    )

        self.issue_priority(session)
        before = daretti.counters["loyalty"]
        result = session.act(
            "pilot:A",
            {
                "a": "activate",
                "source": daretti.ref,
                "ability": plus.ability_id,
                "_mana_payment_id": "forged",
                "_mana_replacement_selections": {"forged": ["effect"]},
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(before, daretti.counters["loyalty"])
        self.assertFalse(session.engine.state.stack)

    def test_competing_cost_replacements_suspend_before_mutation_and_replay(self):
        session = self.session(6061403, players=4)
        engine = session.engine
        daretti, plus = self.stage_daretti(session)
        self.add_permanent(session, "Doc Samson, Super Psychiatrist", "A-doc")
        self.add_permanent(session, "Vorinclex, Monstrous Raider", "A-vorinclex")
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.lookup("Vorinclex, Monstrous Raider"),),
            trust_level="provisional",
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
        )
        self.issue_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        before = daretti.counters["loyalty"]

        result = session.act(
            "pilot:A",
            {"a": "activate", "source": daretti.ref, "ability": plus.ability_id},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual(
            before,
            engine.state.cards[daretti.object_id].counters["loyalty"],
        )
        self.assertFalse(engine.state.stack)
        for seat in ("B", "C", "D"):
            self.assertIsNone(
                StateProjector(self.db, engine.state)._decision(
                    f"pilot:{seat}"
                )
            )
        projected = StateProjector(self.db, engine.state)._decision("pilot:A")
        selected = projected["ctx"]["options"][0]["id"]
        expected_delta = (
            (plus.loyalty_delta + 1) * 2
            if "A-doc" in selected
            else plus.loyalty_delta * 2 + 1
        )

        continuation = deepcopy(engine.state.pending_decision.continuation)
        restored = ReplacementContinuation.from_dict(continuation)
        self.assertEqual("priority_action_cost", restored.resume_kind)
        tampered = deepcopy(continuation)
        tampered["replacement_batch"]["events"][0]["payload"][
            "counter_name"
        ] = "+1/+1"
        with self.assertRaisesRegex(
            ReplacementEffectError, "continuation event"
        ):
            ReplacementContinuation.from_dict(tampered)
        tampered = deepcopy(continuation)
        event_id = tampered["replacement_batch"]["events"][0]["event_id"]
        tampered["priority_response"]["_mana_replacement_selections"] = {
            event_id: [{"effect_id": selected, "event_id": event_id}]
        }
        with self.assertRaisesRegex(
            ReplacementEffectError, "journal is malformed"
        ):
            ReplacementContinuation.from_dict(tampered)

        result = session.act(
            "pilot:A",
            {"a": "choose", "replacement": selected},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            before + expected_delta,
            engine.state.cards[daretti.object_id].counters["loyalty"],
        )
        self.assertEqual(1, len(engine.state.stack))
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "loyalty-cost-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
