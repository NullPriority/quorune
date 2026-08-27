from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.compiler.counter_placement_templates import (
    SupportCounterPlacementTemplate,
    support_counter_placement_effect_template,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card
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
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "support-counter-placement.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class SupportCounterPlacementCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.base = cls.db.lookup("Sol Ring")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, text: str, *, type_line: str):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=text,
                type_line=type_line,
                keywords=("Support",),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_support_compiles_in_spell_trigger_and_activated_contexts(self):
        contexts = (
            (
                "Support 2. (Put a +1/+1 counter on each of up to two target creatures.)",
                "Instant",
                "spell_ability",
                False,
                2,
            ),
            (
                "When this creature enters, support 3. (Put a +1/+1 counter on each of up to three other target creatures.)",
                "Creature — Kor Ally",
                "triggered_ability",
                True,
                3,
            ),
            (
                "{4}{G}{W}: Support 2. (Put a +1/+1 counter on each of up to two other target creatures.)",
                "Creature — Elf Soldier Ally",
                "activated_ability",
                True,
                2,
            ),
        )
        for text, type_line, kind, excludes_source, maximum in contexts:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id
                    and value.template_id.startswith("support-fixed-")
                )
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(
                    {
                        "op": "place_counters_on_targets",
                        "cards": "$targets",
                        "maximum_targets": maximum,
                        "counter": "+1/+1",
                        "amount": 1,
                        "source": "$source",
                    },
                    node.effects[0],
                )
                self.assertEqual(["creature"], node.target_schema["types_any"])
                self.assertEqual(maximum, node.target_schema["up_to"])
                self.assertEqual(
                    "permanent" if excludes_source else "spell",
                    node.target_schema["support_source_context"],
                )
                self.assertEqual(
                    excludes_source,
                    node.target_schema.get("source_exclusion", False),
                )
                self.assertIn(
                    "counter.producer.support",
                    node.capability_dependencies,
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_permanent_support_excludes_source_while_spell_support_does_not(self):
        permanent = support_counter_placement_effect_template(
            "Support 2.",
            source_is_permanent=True,
        )
        spell = support_counter_placement_effect_template(
            "Support 2.",
            source_is_permanent=False,
        )
        self.assertIsInstance(permanent, SupportCounterPlacementTemplate)
        self.assertIsInstance(spell, SupportCounterPlacementTemplate)
        assert permanent is not None and spell is not None
        self.assertTrue(permanent.target_schema["source_exclusion"])
        self.assertNotIn("source_exclusion", spell.target_schema)
        self.assertNotEqual(permanent.template_id, spell.template_id)
        self.assertEqual(
            "permanent",
            permanent.target_schema["support_source_context"],
        )
        self.assertEqual(
            "spell",
            spell.target_schema["support_source_context"],
        )
        self.assertEqual(permanent.effects, spell.effects)

    def test_support_shape_mutations_fail_closed(self):
        template = support_counter_placement_effect_template(
            "Support 2.",
            source_is_permanent=True,
        )
        assert template is not None
        self.assertEqual(
            ("counter.producer.support",),
            capability_dependencies_for_node(
                effects=template.effects,
                target_schema=template.target_schema,
                mechanic_ids=template.mechanics,
            ),
        )
        for effect in (
            {**template.effects[0], "amount": 2},
            {**template.effects[0], "counter": "charge"},
            {**template.effects[0], "maximum_targets": True},
            {**template.effects[0], "unknown": True},
        ):
            with self.subTest(effect=effect):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=(effect,),
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        for schema in (
            {**template.target_schema, "support_source_context": "spell"},
            {
                key: value
                for key, value in template.target_schema.items()
                if key != "support_source_context"
            },
            {
                key: value
                for key, value in template.target_schema.items()
                if key != "source_exclusion"
            },
            {**template.target_schema, "source_exclusion": False},
            {**template.target_schema, "types_any": ["artifact"]},
            {**template.target_schema, "controller_relation": "you"},
            {**template.target_schema, "types_none": ["artifact"]},
        ):
            with self.subTest(schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=template.effects,
                        target_schema=schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "counter.producer.fixed_permanent_target_set_effect"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        ir = compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text="Support 2.",
                type_line="Sorcery",
                keywords=("Support",),
                faces=(),
            ),
            capability_registry=registry,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_unsupported_support_variants_remain_material_residuals(self):
        cases = (
            ("Support X.", "Sorcery"),
            ("Support 0.", "Sorcery"),
            ("Support 2 twice.", "Sorcery"),
            ("At the beginning of combat on your turn, you may pay {3}{W}. If you do, support 2.", "Creature"),
        )
        for text, type_line in cases:
            with self.subTest(text=text):
                self.assertIsNone(
                    support_counter_placement_effect_template(
                        text,
                        source_is_permanent="creature" in type_line.casefold(),
                    )
                )
                ir = self.compile(text, type_line=type_line)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_support_source_context_uses_exact_card_types(self):
        for type_line in (
            "Scheme",
            "Creaturelike",
            "Instant Creature — Shapeshifter",
        ):
            with self.subTest(type_line=type_line):
                ir = self.compile("Support 2.", type_line=type_line)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_support_compiler_mutant_is_killed(self):
        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile("Support 2.", type_line="Sorcery").status,
            )

        exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "support_counter_placement_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()


class SupportCounterPlacementRuntimeTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
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
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        return card

    @staticmethod
    def support_program() -> SemanticProgram:
        template = SupportCounterPlacementTemplate(
            maximum_targets=2,
            source_is_permanent=True,
        )
        return SemanticProgram(
            key="fixture:support-two",
            label="Support 2",
            effects=[dict(value) for value in template.effects],
            target_schema=dict(template.target_schema),
            trust_level="provisional",
        )

    @staticmethod
    def stack_item(
        engine,
        *,
        program: SemanticProgram,
        source: CardInstance,
        refs: list[str],
    ) -> StackItem:
        item = StackItem(
            stack_id="stack:support-two",
            ref="S-support-two",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            source_object_id=source.object_id,
            targets=list(refs),
            visibility=list(engine.seats),
            context={
                "target_groups": {"target_0": list(refs)},
                "target_snapshots": {
                    ref: engine._target_snapshot(ref) for ref in refs
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        engine.state.stack.append(item)
        return item

    def test_permanent_support_excludes_source_from_legal_targets(self):
        session = self.session(7014101)
        engine = session.engine
        source = self.add_permanent(
            engine, seat="A", name="Scute Swarm", ref="support-source"
        )
        first = self.add_permanent(
            engine, seat="A", name="Scute Swarm", ref="support-first"
        )
        second = self.add_permanent(
            engine, seat="B", name="Scute Swarm", ref="support-second"
        )
        options = engine._semantic_target_options(
            "A",
            self.support_program().target_schema,
            source_ref=source.ref,
        )
        self.assertNotIn(source.ref, options)
        self.assertIn(first.ref, options)
        self.assertIn(second.ref, options)

    def test_support_all_original_targets_illegal_rolls_back(self):
        session = self.session(7014102)
        engine = session.engine
        source = self.add_permanent(
            engine, seat="A", name="Scute Swarm", ref="support-source"
        )
        target = self.add_permanent(
            engine, seat="B", name="Scute Swarm", ref="support-illegal"
        )
        program = self.support_program()
        engine.semantics.put(program)
        item = self.stack_item(
            engine,
            program=program,
            source=source,
            refs=[target.ref],
        )
        before = authoritative_state_hash(engine.state)
        engine.move_card(target.object_id, "graveyard", reason="response")
        after_response = authoritative_state_hash(engine.state)
        self.assertNotEqual(before, after_response)

        engine._begin_resolve_item(
            item,
            [dict(value) for value in program.effects],
            None,
        )

        self.assertEqual({}, target.counters)
        self.assertFalse(
            any(event.code == "counter.add" for event in engine.state.events)
        )

    def test_support_partial_target_revalidation_and_quantity_replacement_replay(
        self,
    ):
        session = self.session(7014103)
        engine = session.engine
        source = self.add_permanent(
            engine, seat="A", name="Scute Swarm", ref="support-source"
        )
        legal = self.add_permanent(
            engine, seat="A", name="Scute Swarm", ref="support-legal"
        )
        illegal = self.add_permanent(
            engine, seat="B", name="Scute Swarm", ref="support-illegal"
        )
        self.add_permanent(
            engine,
            seat="A",
            name="Doubling Season",
            ref="support-doubling",
        )
        self.add_permanent(
            engine,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref="support-doc",
        )
        program = self.support_program()
        engine.semantics.put(program)
        self.stack_item(
            engine,
            program=program,
            source=source,
            refs=[legal.ref, illegal.ref],
        )
        engine.move_card(illegal.object_id, "graveyard", reason="response")
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
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        self.assertIsNotNone(projected)
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        serialized = json.dumps(projected, sort_keys=True)
        self.assertNotIn(legal.object_id, serialized)
        hidden_refs = {
            engine.state.cards[object_id].ref
            for seat in ("B", "C", "D")
            for object_id in engine.state.players[seat].zones["hand"]
        }
        self.assertTrue(
            all(json.dumps(ref) not in serialized for ref in hidden_refs)
        )
        selected = projected["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": selected},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertGreater(legal.counters["+1/+1"], 1)
        self.assertEqual({}, illegal.counters)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "support-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(5, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
