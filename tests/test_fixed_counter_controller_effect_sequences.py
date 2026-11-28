from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.fixed_counter_controller_effect_sequences import (
    fixed_counter_controller_effect_sequence_template,
)
from quorune.compiler.fixed_source_effect_sequences import SOURCE_ZONE_OBJECT
from quorune.deck import DeckDefinition, DeckEntry, DeckLoader
from quorune.event_counter_reference import EVENT_ENTRY_OBJECT
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
SEQUENCE_CAPABILITY = "resolution.effect_sequence.fixed_counter_controller"
ENTRY_COUNTER_CAPABILITY = "counter.producer.fixed_event_object"
HENGE_ORACLE_ID = "78427103-9543-41fb-b6d4-72963fe87275"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-counter-controller-sequences.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-counter-controller-sequences.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedCounterControllerEffectSequenceCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()
        cls.base = CardRecord(
            oracle_id="fixture:fixed-counter-controller-sequence",
            name="Fixed Counter Controller Sequence",
            mana_cost="{1}{U}",
            mana_value=2.0,
            type_line="Sorcery",
            oracle_text=(
                "Put a +1/+1 counter on target creature. You gain 1 life."
            ),
            power=None,
            toughness=None,
            loyalty=None,
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

    def fixture(
        self,
        text: str,
        *,
        type_line: str = "Sorcery",
        name: str = "Fixed Counter Controller Sequence",
    ) -> CardRecord:
        return replace(
            self.base,
            oracle_id=f"fixture:{abs(hash((text, type_line, name)))}",
            name=name,
            oracle_text=text,
            type_line=type_line,
        )

    def compile(self, record: CardRecord):
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_spell_trigger_and_activated_contexts_share_fixed_counter_controller_lowering(self):
        fixtures = (
            (
                self.fixture(
                    "Put a +1/+1 counter on target creature. You gain 1 life."
                ),
                "spell_ability",
                ("place_counters", "life"),
                "$target.0",
            ),
            (
                self.fixture(
                    "{2}{U}: Draw a card, then put a +1/+1 counter on "
                    "this creature.",
                    type_line="Creature — Sphinx",
                ),
                "activated_ability",
                ("draw", "place_counters"),
                SOURCE_ZONE_OBJECT,
            ),
            (
                self.fixture(
                    "When Fixed Counter Controller Sequence enters, put a "
                    "study counter on this creature. Scry 1.",
                    type_line="Creature — Wizard",
                ),
                "triggered_ability",
                ("place_counters", "scry"),
                SOURCE_ZONE_OBJECT,
            ),
        )
        for record, kind, operations, counter_subject in fixtures:
            with self.subTest(kind=kind):
                ir = self.compile(record)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertEqual(
                    "fixed-counter-controller-effect-sequence-v1",
                    node.template_id,
                )
                self.assertEqual(
                    operations,
                    tuple(effect["op"] for effect in node.effects),
                )
                counter = next(
                    effect
                    for effect in node.effects
                    if effect["op"] == "place_counters"
                )
                self.assertEqual(counter_subject, counter["card"])
                self.assertTrue(
                    {
                        SEQUENCE_CAPABILITY,
                        "counter.producer.fixed_effect",
                    }.issubset(node.capability_dependencies)
                )
                self.assertEqual(
                    record.oracle_text,
                    record.oracle_text[node.span.start : node.span.end],
                )

    def test_fixed_counter_controller_shape_and_dependency_mutants_fail_closed(self):
        template = fixed_counter_controller_effect_sequence_template(
            "Draw a card, then put a +1/+1 counter on this creature.",
            card_name="Fixed Counter Controller Sequence",
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(
            {
                SEQUENCE_CAPABILITY,
                "counter.producer.fixed_effect",
                "zone.draw.library_to_hand",
            },
            set(
                capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=template.target_schema,
                    mechanic_ids=template.mechanic_ids,
                )
            ),
        )
        draw, counter = template.effects
        malformed = (
            (draw, {**counter, "amount": True}),
            (draw, {**counter, "amount": 0}),
            (draw, {**counter, "card": "$source"}),
            (draw, {**counter, "source": "$controller"}),
            ({**draw, "private": False}, counter),
            (counter, counter),
            (draw, draw),
        )
        for effects in malformed:
            with self.subTest(effects=effects):
                dependencies = capability_dependencies_for_node(
                    effects=effects,
                    target_schema=None,
                    mechanic_ids=template.mechanic_ids,
                )
                self.assertNotIn(SEQUENCE_CAPABILITY, dependencies)

        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        record = self.fixture(
            "{2}{U}: Draw a card, then put a +1/+1 counter on this creature.",
            type_line="Creature — Sphinx",
        )
        for blocked in (SEQUENCE_CAPABILITY, "counter.producer.fixed_effect"):
            with self.subTest(blocked=blocked):
                mutated = json.loads(json.dumps(payload))
                row = next(
                    value
                    for value in mutated["capabilities"]
                    if value["id"] == blocked
                )
                row["status"] = "blocked"
                row["blockers"] = ["mutation witness"]
                ir = compile_oracle_card(
                    record,
                    capability_registry=CapabilityRegistry(mutated),
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_unsupported_fixed_counter_controller_variants_remain_material_residuals(self):
        variants = (
            "You may put a +1/+1 counter on this creature. Draw a card.",
            "Put X +1/+1 counters on this creature. Draw a card.",
            "Put a +1/+1 counter on this creature. Draw a card. Scry 1.",
            "Choose one — Put a +1/+1 counter on this creature; or draw a card.",
            "Move a +1/+1 counter onto this creature. Draw a card.",
            "Put a +1/+1 counter on this creature. Target opponent loses 1 life.",
        )
        for text in variants:
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_counter_controller_effect_sequence_template(
                        text,
                        card_name="Fixed Counter Controller Sequence",
                    )
                )
                ir = self.compile(
                    self.fixture(text, type_line="Creature — Test")
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_counter_controller_compiler_mutant_is_killed(self):
        record = self.fixture(
            "{2}{U}: Draw a card, then put a +1/+1 counter on this creature.",
            type_line="Creature — Sphinx",
        )

        def assert_exact() -> None:
            ir = self.compile(record)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            self.assertEqual(
                "fixed-counter-controller-effect-sequence-v1",
                ir.faces[0].nodes[0].template_id,
            )

        assert_exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_counter_controller_effect_sequence_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()

    def test_entry_counter_draw_requires_the_entire_untargeted_nontoken_trigger(self):
        prefix = "Whenever a nontoken creature you control enters"
        body = "put a +1/+1 counter on it and draw a card."
        text = f"{prefix}, {body}"
        node, = self.compile(self.fixture(text, type_line="Artifact")).faces[0].nodes
        self.assertTrue(node.exact)
        self.assertEqual(text, node.text)
        self.assertEqual("creature.enter", node.event)
        self.assertIsNone(node.target_schema)
        self.assertEqual(("place_counters", "draw"), tuple(effect["op"] for effect in node.effects))
        self.assertEqual(EVENT_ENTRY_OBJECT, node.effects[0]["card"])
        self.assertLessEqual(
            {SEQUENCE_CAPABILITY, ENTRY_COUNTER_CAPABILITY, "zone.draw.library_to_hand"},
            set(node.capability_dependencies),
        )
        for variant in (
            text.replace("nontoken ", ""),
            text.replace("you control", "an opponent controls"),
            text.replace("enters", "dies"),
            text.replace("put", "you may put"),
            text.replace("and draw", "and if you do, draw"),
            text.replace("a +1/+1 counter", "two +1/+1 counters"),
        ):
            with self.subTest(variant=variant):
                result = self.compile(self.fixture(variant, type_line="Artifact"))
                self.assertNotEqual("exact", result.status)
                self.assertTrue(result.material_residuals)


class EntrantCounterDrawRuntimeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "entrant-counter-draw.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database(
            [
                fixtures / "bound-effect-program-cards.json",
                fixtures / "source-attack-alone-cards.json",
                fixtures / "aggregated-zone-event-triggers.json",
                fixtures / "counter-replacement-cards.json",
                fixtures / "fixed-counter-controller-sequences.json",
                fixtures / "entrant-counter-draw-cards.json",
            ],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Entrant counter and draw",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def henge(self, session, *, zone="battlefield"):
        source = self.add(session.engine, "The Great Henge", zone=zone)
        self.assertEqual(HENGE_ORACLE_ID, source.oracle_id)
        self.assert_closed(session, source)
        return source

    def test_actual_henge_discounts_only_generic_and_activates_mana_and_life_immediately(self):
        for green in (1, 2):
            with self.subTest(green=green):
                session = self.session(11270230 + green)
                source = self.henge(session, zone="hand")
                self.add(session.engine, "Sphinx of Magosi")
                self.add(session.engine, "Ancient Brontodon", seat="B")
                self.prepare(session, mana={"C": 2, "G": green})
                self.checkpoint(session)
                if green == 1:
                    before = authoritative_state_hash(session.state)
                    result = session.act("pilot:A", {"action_id": f"cast:{source.ref}", "pay": "auto"})
                    self.assertFalse(result.ok, result.summary)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    continue
                self.cast_for(session, "A", source)
                self.assertEqual(1, session.state.players["A"].mana_pool.get("C", 0))
                self.assertEqual(0, session.state.players["A"].mana_pool.get("G", 0))
                self.assertIsNone(self.resolve(session))
                self.assertEqual(0, self.trigger_count(session, source))
                self.priority(session, "A")
                actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                mana, = (row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
                self.assertTrue(mana["mana_ability"])
                life = session.state.players["A"].life
                result = session.act("pilot:A", {"action_id": mana["id"]})
                self.assertTrue(result.ok, result.summary)
                self.assertFalse(session.state.stack)
                self.assertTrue(session.state.cards[source.object_id].tapped)
                self.assertEqual(2, session.state.players["A"].mana_pool["G"])
                self.assertEqual(life + 2, session.state.players["A"].life)
                self.replay(session, load=True)

    def test_actual_henge_ignores_tokens_and_opponents_and_resolves_one_private_draw(self):
        session = self.session(11270233)
        source = self.henge(session)
        entrant = self.add(session.engine, "Grizzly Bears", zone="hand", ref="own-entrant")
        opponent = self.add(session.engine, "Grizzly Bears", seat="B", zone="hand", ref="other-entrant")
        token_spell = self.add(session.engine, "Raise the Alarm", zone="hand")
        self.add(session.engine, "Vedalken Orrery", seat="B")
        self.prepare(session, mana={"C": 2, "G": 1, "W": 1})
        session.state.players["B"].mana_pool.update({"C": 1, "G": 1})
        self.checkpoint(session)

        library_before = tuple(session.state.players["A"].zones["library"])
        self.cast_for(session, "A", token_spell)
        self.resolve_top(session)
        self.assertEqual(0, self.trigger_count(session, source))
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(tokens))
        self.assertTrue(all(card.controller == "A" and not card.counters for card in tokens))

        self.cast_for(session, "B", opponent)
        self.resolve_top(session)
        self.assertEqual(0, self.trigger_count(session, source))
        self.assertFalse(session.state.cards[opponent.object_id].counters)
        self.assertEqual(library_before, tuple(session.state.players["A"].zones["library"]))

        self.cast_for(session, "A", entrant)
        self.resolve_top(session)
        self.assertEqual(1, self.trigger_count(session, source))
        self.assertFalse(session.state.stack[-1].targets)
        self.assertFalse(session.state.cards[entrant.object_id].counters)
        before = len(session.state.players["A"].zones["hand"])
        drawn = session.state.players["A"].zones["library"][-1]
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"+1/+1": 1}, session.state.cards[entrant.object_id].counters)
        self.assertEqual(before + 1, len(session.state.players["A"].zones["hand"]))
        self.assertIn(drawn, session.state.players["A"].zones["hand"])
        hidden = session.state.cards[drawn].ref
        for seat in "BCD":
            self.assertNotIn(hidden, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        self.replay(session)

    def test_actual_henge_draws_for_departed_entrant_after_reentry_and_source_removal(self):
        session = self.session(11270234)
        source = self.henge(session)
        entrant = self.add(session.engine, "Grizzly Bears", zone="hand")
        self.add(session.engine, "Vedalken Orrery")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        destroy = self.add(session.engine, "Disenchant", seat="B", zone="hand")
        self.prepare(session, mana={"C": 2, "G": 2})
        session.state.players["B"].mana_pool.update({"C": 1, "U": 1, "W": 1})
        self.checkpoint(session)

        self.cast_for(session, "A", entrant)
        self.resolve_top(session)
        self.assertEqual(1, self.trigger_count(session, source))
        current = session.state.cards[entrant.object_id]
        original_identity = current.logical_object_id
        self.cast_for(session, "B", bounce, [current.ref])
        self.resolve_top(session)
        self.assertEqual("hand", session.state.cards[entrant.object_id].zone)
        self.cast_for(session, "A", entrant)
        self.resolve_top(session)
        current = session.state.cards[entrant.object_id]
        self.assertNotEqual(original_identity, current.logical_object_id)
        self.assertEqual(2, self.trigger_count(session, source))

        self.cast_for(session, "B", destroy, [session.state.cards[source.object_id].ref])
        self.resolve_top(session)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual(2, self.trigger_count(session, source))
        before = len(session.state.players["A"].zones["hand"])
        session = self.replay(session, load=True)
        self.resolve_top(session)
        self.assertEqual({"+1/+1": 1}, session.state.cards[entrant.object_id].counters)
        self.assertEqual(before + 1, len(session.state.players["A"].zones["hand"]))
        self.resolve_top(session)
        self.assertEqual({"+1/+1": 1}, session.state.cards[entrant.object_id].counters)
        self.assertEqual(before + 2, len(session.state.players["A"].zones["hand"]))
        self.replay(session, load=True)

    def test_entry_counter_draw_binding_rejects_forged_domain_order_and_withdrawn_capability(self):
        session = self.session(11270235)
        source = self.henge(session)
        program, = (program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
                    if program.event == "creature.enter")
        dependencies = tuple(value for value in program.capability_dependencies if value != ENTRY_COUNTER_CAPABILITY)
        missing_dependency = replace(
            program,
            capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict(),
        )
        domain = {"field": "controller", "op": "eq", "value": "$source.controller"}
        for changed, expected in (
            (replace(program, event_condition=domain), "runtime_entry_counter_draw:invalid:"),
            (replace(program, effects=tuple(reversed(program.effects))), "runtime_entry_counter_draw:invalid:"),
            (missing_dependency, f"capability:undeclared_runtime_dependency:{ENTRY_COUNTER_CAPABILITY}"),
        ):
            with self.subTest(expected=expected):
                binding = bind_semantic_program_runtime(
                    changed, capability_registry=self.registry, profile="commander_review",
                )
                self.assertFalse(binding["strict"], binding)
                self.assertTrue(any(blocker.startswith(expected) for blocker in binding["blockers"]), binding)


class FixedCounterControllerEffectSequenceRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
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

    def add_permanent(self, session, *, seat: str, name: str, ref: str):
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
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
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
    def prepare_priority(session, *, seat: str = "A") -> None:
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
    def pass_until(session, predicate, *, limit: int = 24) -> None:
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Resolution stopped without a decision")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Resolution did not reach the expected state")

    def activate_sphinx(self, session, source, *, prepare: bool = True) -> None:
        session.state.players["A"].mana_pool["U"] = 3
        if prepare:
            self.prepare_priority(session)
        action_id = f"activate:{source.ref}:ab2"
        offered = {
            action["id"]
            for action in session.packet("pilot:A", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
        }
        self.assertIn(action_id, offered)
        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)

    def choose_replacements(self, session, *, expect_success: bool = True):
        for _ in range(12):
            decision = session.state.pending_decision
            if decision is None or decision.kind != "replacement.order":
                return None
            projected = StateProjector(self.db, session.state)._decision(
                "pilot:A"
            )
            self.assertIsNotNone(projected)
            selected = projected["ctx"]["options"][0]["id"]
            result = session.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "choices": {"replacement": selected},
                },
            )
            if not result.ok:
                if expect_success:
                    self.fail(result.summary)
                return result
        self.fail("Replacement sequence did not converge")

    def stage_sphinx(self, session, *, replacements: bool) -> CardInstance:
        source = self.add_permanent(
            session,
            seat="A",
            name="Sphinx of Magosi",
            ref=f"A-sphinx-{session.state.config.seed}",
        )
        if replacements:
            self.add_permanent(
                session,
                seat="A",
                name="Doubling Season",
                ref=f"A-doubling-{session.state.config.seed}",
            )
            self.add_permanent(
                session,
                seat="A",
                name="Doc Samson, Super Psychiatrist",
                ref=f"A-doc-{session.state.config.seed}",
            )
        return source

    def test_sphinx_draws_before_counter_replacement_and_replays(self):
        session = self.session(124001, players=4)
        source = self.stage_sphinx(session, replacements=True)
        hand_before = len(session.state.players["A"].zones["hand"])
        session.state.players["A"].mana_pool["U"] = 3
        self.prepare_priority(session)
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        self.activate_sphinx(session, source, prepare=False)
        self.pass_until(
            session,
            lambda: session.state.pending_decision is not None
            and session.state.pending_decision.kind == "replacement.order",
        )
        self.assertEqual(
            hand_before + 1,
            len(session.state.players["A"].zones["hand"]),
        )
        self.assertFalse(source.counters)
        projected = StateProjector(self.db, session.state)._decision("pilot:A")
        self.assertIsNotNone(projected)
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))
        for seat in "BCD":
            self.assertIsNone(
                StateProjector(self.db, session.state)._decision(f"pilot:{seat}")
            )

        self.choose_replacements(session)
        self.assertGreater(source.counters["+1/+1"], 1)
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "counter-controller-sequence"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_source_departure_skips_counter_but_preserves_draw(self):
        session = self.session(124002)
        source = self.stage_sphinx(session, replacements=False)
        hand_before = len(session.state.players["A"].zones["hand"])
        self.activate_sphinx(session, source)
        item = session.state.stack[-1]
        program = session.engine.semantics.get(item.semantic_key)
        self.assertIsNotNone(program)
        session.engine.move_card(
            source.object_id,
            "graveyard",
            reason="source departure witness",
        )
        session.engine._begin_resolve_item(
            item,
            [dict(effect) for effect in program.effects],
            program.destination,
            note="source departure sequence witness",
        )
        self.assertEqual(
            hand_before + 1,
            len(session.state.players["A"].zones["hand"]),
        )
        self.assertFalse(source.counters)

    def test_stale_source_replacement_resume_rolls_back_choice(self):
        session = self.session(124003)
        source = self.stage_sphinx(session, replacements=True)
        hand_before = len(session.state.players["A"].zones["hand"])
        self.activate_sphinx(session, source)
        self.pass_until(
            session,
            lambda: session.state.pending_decision is not None
            and session.state.pending_decision.kind == "replacement.order",
        )
        original = source.logical_object_id
        session.engine.move_card(
            source.object_id,
            "graveyard",
            reason="stale source witness",
        )
        session.engine.move_card(
            source.object_id,
            "battlefield",
            reason="stale source witness",
        )
        self.assertNotEqual(original, source.logical_object_id)
        before_choice = authoritative_state_hash(session.state)
        result = self.choose_replacements(session, expect_success=False)
        self.assertIsNotNone(result)
        self.assertFalse(result.ok)
        self.assertEqual(before_choice, authoritative_state_hash(session.state))
        self.assertEqual(
            hand_before + 1,
            len(session.state.players["A"].zones["hand"]),
        )
        self.assertFalse(session.state.cards[source.object_id].counters)

    def test_fixed_counter_controller_runtime_mutant_is_killed(self):
        session = self.session(124004)
        source = self.stage_sphinx(session, replacements=False)
        with self.assertRaises(AssertionError):
            with patch(
                "quorune.semantic_choices.intent_host.place_counters_on_refs",
                return_value=(),
            ):
                self.activate_sphinx(session, source)
                self.pass_until(
                    session,
                    lambda: not session.state.stack
                    and session.state.pending_decision is None,
                )
                self.assertGreater(source.counters.get("+1/+1", 0), 0)

    def test_furnace_skullbomb_target_predicate_and_draw_compose(self):
        session = self.session(124005)
        source = self.add_permanent(
            session,
            seat="A",
            name="Furnace Skullbomb",
            ref="A-furnace-skullbomb",
        )
        legal = self.add_permanent(
            session,
            seat="A",
            name="Sphinx of Magosi",
            ref="A-furnace-target",
        )
        illegal = self.add_permanent(
            session,
            seat="B",
            name="Sphinx of Magosi",
            ref="B-furnace-target",
        )
        session.state.players["A"].mana_pool["R"] = 2
        self.prepare_priority(session)
        action_id = f"activate:{source.ref}:ab2"
        action = next(
            value
            for value in session.packet("pilot:A", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
            if value["id"] == action_id
        )
        self.assertIn(legal.ref, action["target_schema"]["legal_refs"])
        self.assertNotIn(illegal.ref, action["target_schema"]["legal_refs"])
        hand_before = len(session.state.players["A"].zones["hand"])
        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:A",
            {"action_id": action_id, "targets": [illegal.ref]},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        source = session.state.cards[source.object_id]
        legal = session.state.cards[legal.object_id]

        accepted = session.act(
            "pilot:A",
            {"action_id": action_id, "targets": [legal.ref]},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", source.zone)
        self.pass_until(
            session,
            lambda: not session.state.stack,
        )
        self.assertEqual(2, legal.counters["oil"])
        self.assertEqual(
            hand_before + 1,
            len(session.state.players["A"].zones["hand"]),
        )


if __name__ == "__main__":
    unittest.main()
