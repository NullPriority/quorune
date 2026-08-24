from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorune.amass import (
    AmassError,
    FixedAmassSpec,
    plural_amass_subtype,
)
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.amass_templates import fixed_amass_effect_template
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
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
from quorune.rules.node_capability_shapes import (
    fixed_amass_node_capabilities,
)
from quorune.semantics import SemanticProgram
from tests.common import DB_PATH, keep_all, load_assets, make_session


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def amass_record(
    text: str,
    *,
    suffix: int,
    type_line: str = "Sorcery",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-9001-{suffix:012d}",
        name=f"Generic Amass Fixture {suffix}",
        mana_cost="{2}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class AmassCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry_value = json.loads(
            REGISTRY_PATH.read_text(encoding="utf-8")
        )
        cls.capabilities = load_default_capability_registry()
        cls.database = CardDatabase(DB_PATH)

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def test_amass_subtype_labels_use_closed_ordinary_plurals(self):
        self.assertEqual("Orcs", plural_amass_subtype("Orc"))
        self.assertEqual("Elves", plural_amass_subtype("Elf"))
        self.assertEqual("Dwarves", plural_amass_subtype("Dwarf"))
        self.assertEqual("Wolves", plural_amass_subtype("Wolf"))
        self.assertEqual("Foxes", plural_amass_subtype("Fox"))

    def test_fixed_amass_compiles_in_spell_trigger_and_activated_contexts(
        self,
    ):
        fixtures = (
            (
                amass_record("Amass Orcs 2.", suffix=1),
                "spell_ability",
                "Orc",
                2,
            ),
            (
                amass_record(
                    "When this creature enters, amass Zombies 1.",
                    suffix=2,
                    type_line="Creature — Bird Soldier",
                ),
                "triggered_ability",
                "Zombie",
                1,
            ),
            (
                amass_record(
                    "{2}: Amass Slivers 3.",
                    suffix=3,
                    type_line="Artifact",
                ),
                "activated_ability",
                "Sliver",
                3,
            ),
        )
        for record, expected_kind, subtype, amount in fixtures:
            with self.subTest(kind=expected_kind):
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                template_id = (
                    f"amass-{subtype.casefold()}-fixed-{amount}-v1"
                )
                node = next(
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id == template_id
                )
                self.assertEqual("exact", ir.status)
                self.assertEqual(expected_kind, node.kind)
                self.assertEqual(
                    ({"op": "amass", "subtype": subtype, "amount": amount},),
                    node.effects,
                )
                self.assertIn(
                    "keyword_action.amass.fixed",
                    node.capability_dependencies,
                )
                if expected_kind == "triggered_ability":
                    self.assertIn(
                        "trigger.placement.apnap",
                        node.capability_dependencies,
                    )
                self.assertEqual(
                    record.oracle_text,
                    record.oracle_text[node.span.start : node.span.end],
                )
                program = next(
                    program
                    for program in generated_programs(
                        self.database,
                        record,
                        trust_level="trusted",
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    if program.provenance.get("template_id") == template_id
                )
                self.assertTrue(program.capability_closure["trusted"])

    def test_unsupported_amass_variants_remain_material_residuals(self):
        composed = "Amass Orcs 1, then amass Zombies 1."
        self.assertIsNone(fixed_amass_effect_template(composed))
        for suffix, text in enumerate(
            (
                "Amass Orcs X.",
                "Amass Orcs 0.",
                "Amass Orcs -1.",
                "Amass 2.",
                "Amass Modified creatures 2.",
                "Amass Orcs 1 twice.",
                "If you control an Army, amass Orcs 2.",
            ),
            start=10,
        ):
            with self.subTest(text=text):
                self.assertIsNone(fixed_amass_effect_template(text))
                ir = compile_oracle_card(
                    amass_record(text, suffix=suffix),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_amass_dependencies_shapes_and_compiler_mutation_fail_closed(self):
        valid = ({"op": "amass", "subtype": "Orc", "amount": 2},)
        mechanics = ("amass", "cr-111-tokens", "cr-122-counters")
        self.assertEqual(
            ("keyword_action.amass.fixed",),
            fixed_amass_node_capabilities(
                effects=valid,
                target_schema=None,
                mechanic_ids=mechanics,
            ),
        )
        for effects, target_schema, mechanic_ids in (
            (({**valid[0], "future": True},), None, mechanics),
            (({**valid[0], "amount": True},), None, mechanics),
            (({**valid[0], "amount": 0},), None, mechanics),
            (({**valid[0], "subtype": "Modified"},), None, mechanics),
            (valid, {"count": 1}, mechanics),
            (valid, None, ("amass", "cr-122-counters")),
        ):
            with self.subTest(effects=effects):
                self.assertEqual(
                    (),
                    fixed_amass_node_capabilities(
                        effects=effects,
                        target_schema=target_schema,
                        mechanic_ids=mechanic_ids,
                    ),
                )
        for amount in (True, 0, -1):
            with self.subTest(amount=amount):
                with self.assertRaises(AmassError):
                    FixedAmassSpec(subtype="Orc", amount=amount)

        record = amass_record("Amass Orcs 2.", suffix=30)

        def assert_exact(registry):
            ir = compile_oracle_card(
                record,
                capability_registry=registry,
                capability_profile="commander_review",
            )
            self.assertEqual("exact", ir.status)

        assert_exact(self.capabilities)
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_amass_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact(self.capabilities)

        for dependency_id in (
            "token.creation.additional_replacement",
            "counter.placement.quantity_replacement",
        ):
            value = json.loads(json.dumps(self.registry_value))
            dependency = next(
                row
                for row in value["capabilities"]
                if row["id"] == dependency_id
            )
            dependency["status"] = "blocked"
            dependency["blockers"] = ["test mutation"]
            ir = compile_oracle_card(
                record,
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


class AmassRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.database,
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
        engine.semantics.put(
            SemanticProgram(
                key="fixture:amass",
                label="Amass fixture",
                effects=[
                    {"op": "amass", "subtype": "Orc", "amount": 2}
                ],
            )
        )
        return session

    def add_permanent(
        self,
        engine,
        *,
        owner: str,
        name: str,
        ref: str,
    ) -> CardInstance:
        record = self.database.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=owner,
            controller=owner,
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[owner].zones["battlefield"].append(card.object_id)
        return card

    def stack_amass(self, engine, *, actor: str = "A") -> None:
        source = next(
            card
            for card in engine.state.cards.values()
            if card.controller == actor
            and card.zone == "battlefield"
            and "army"
            not in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[1]
        )
        ref = engine._next_ref("S")
        engine.state.stack.append(
            StackItem(
                stack_id=engine._stable_runtime_id("stack", ref),
                ref=ref,
                kind="activated_ability",
                controller=actor,
                label="Amass fixture",
                source_object_id=source.object_id,
                semantic_key="fixture:amass",
                visibility=list(engine.seats),
                context={
                    "source_logical_object_id": source.logical_object_id
                },
            )
        )
        engine._prepare_stack_resolution()

    @staticmethod
    def choose_army(session, seat: str, ref: str):
        return session.act(
            f"pilot:{seat}",
            {
                "action_id": "choose",
                "objects": [ref],
                "plan": "AMASS",
                "reason": "Choose the Army for the fixed Amass instruction.",
            },
        )

    def choose_replacement(self, session, seat: str) -> None:
        packet = StateProjector(
            self.database, session.engine.state
        )._decision(f"pilot:{seat}")
        selected = packet["ctx"]["options"][0]["id"]
        result = session.act(
            f"pilot:{seat}",
            {
                "action_id": "choose",
                "replacement": selected,
                "plan": "ORDER_REPLACEMENTS",
                "reason": "Choose the next applicable replacement effect.",
            },
        )
        self.assertTrue(result.ok, result.summary)

    def effective_subtypes(self, engine, card: CardInstance) -> set[str]:
        return engine._type_parts(
            str(engine._effective_card_data(card).get("type_line") or "")
        )[1]

    def install_generated_tippy_toe(self, engine) -> None:
        record = self.database.lookup("Tippy-Toe, Terrific Partner")
        for program in engine.semantics.programs_for_oracle(record.oracle_id):
            if program.event == "token.create":
                engine.semantics.remove(program.key)
        result = register_generated_programs(
            self.database,
            engine.semantics,
            (record,),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertEqual(1, result["runtime_handlers_promoted"])

    def test_amass_without_army_creates_then_counters_one_black_army(self):
        session = self.session(7014701)
        engine = session.engine
        self.add_permanent(
            engine,
            owner="A",
            name="Sensei's Divining Top",
            ref="amass-source",
        )

        self.stack_amass(engine)

        armies = [
            card
            for card in engine.state.cards.values()
            if card.controller == "A"
            and card.zone == "battlefield"
            and card.is_token
            and "army" in self.effective_subtypes(engine, card)
        ]
        self.assertEqual(1, len(armies))
        army = armies[0]
        self.assertEqual("Orc Army", army.printed_name)
        self.assertEqual(["B"], engine._effective_card_data(army)["colors"])
        self.assertEqual(2, army.counters["+1/+1"])
        self.assertFalse(engine.state.stack)
        codes = [event.code for event in engine.state.events]
        self.assertLess(codes.index("token.create"), codes.index("counter.add"))
        self.assertEqual(
            "keyword_action.amass.completed",
            next(
                event.code
                for event in reversed(engine.state.events)
                if event.code == "keyword_action.amass.completed"
            ),
        )

    def test_amass_existing_army_uses_counters_before_zone_object_subtype(self):
        session = self.session(7014702)
        engine = session.engine
        self.add_permanent(
            engine,
            owner="A",
            name="Sensei's Divining Top",
            ref="amass-source",
        )
        army = self.add_permanent(
            engine,
            owner="A",
            name="Zombie Army",
            ref="existing-army",
        )

        self.stack_amass(engine)

        self.assertEqual(2, army.counters["+1/+1"])
        self.assertTrue(
            {"army", "zombie", "orc"}.issubset(
                self.effective_subtypes(engine, army)
            )
        )
        codes = [event.code for event in engine.state.events]
        self.assertLess(
            codes.index("counter.add"), codes.index("permanent.subtype")
        )
        subtype_event = next(
            event
            for event in engine.state.events
            if event.code == "permanent.subtype"
        )
        self.assertTrue(subtype_event.details["continuous_effect"])

        previous_identity = army.logical_object_id
        engine.move_card(army.object_id, "graveyard")
        engine.move_card(army.object_id, "battlefield", controller="A")
        self.assertNotEqual(previous_identity, army.logical_object_id)
        self.assertNotIn("orc", self.effective_subtypes(engine, army))
        self.assertTrue(
            {"army", "zombie"}.issubset(self.effective_subtypes(engine, army))
        )
        self.assertNotIn("+1/+1", army.counters)

    def test_multiple_army_choice_is_seat_scoped_and_replays_exactly(self):
        session = self.session(7014703, players=4)
        engine = session.engine
        self.add_permanent(
            engine,
            owner="A",
            name="Sensei's Divining Top",
            ref="amass-source",
        )
        first = self.add_permanent(
            engine, owner="A", name="Zombie Army", ref="first-army"
        )
        second = self.add_permanent(
            engine, owner="A", name="Goblin Army", ref="second-army"
        )
        self.stack_amass(engine)

        projector = StateProjector(self.database, engine.state)
        packet = projector._decision("pilot:A")
        self.assertEqual(
            [first.ref, second.ref],
            packet["legal_actions"][0]["choice_schema"]["legal_refs"],
        )
        for seat in "BCD":
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        serialized = json.dumps(packet, sort_keys=True)
        for seat in engine.seats:
            for object_id in engine.state.players[seat].zones["hand"]:
                self.assertNotIn(engine.state.cards[object_id].ref, serialized)

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = self.choose_army(session, "A", second.ref)
        self.assertTrue(result.ok, result.summary)
        self.assertNotIn("+1/+1", first.counters)
        self.assertEqual(2, second.counters["+1/+1"])
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "amass-choice-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.database, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(1, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_amass_stale_army_choice_rolls_back(self):
        session = self.session(7014704)
        engine = session.engine
        self.add_permanent(
            engine,
            owner="A",
            name="Sensei's Divining Top",
            ref="amass-source",
        )
        first = self.add_permanent(
            engine, owner="A", name="Zombie Army", ref="first-army"
        )
        self.add_permanent(
            engine, owner="A", name="Goblin Army", ref="second-army"
        )
        self.stack_amass(engine)
        engine.move_card(first.object_id, "graveyard")
        engine.move_card(first.object_id, "battlefield", controller="A")
        before = authoritative_state_hash(engine.state)

        result = self.choose_army(session, "A", first.ref)

        self.assertFalse(result.ok)
        self.assertIn("identity set changed", result.summary)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertNotIn("+1/+1", first.counters)
        self.assertIsNotNone(engine.state.pending_decision)

    def test_amass_token_and_counter_replacements_suspend_in_rule_order_and_replay(
        self,
    ):
        session = self.session(7014705, players=4)
        engine = session.engine
        self.add_permanent(
            engine,
            owner="A",
            name="Sensei's Divining Top",
            ref="amass-source",
        )
        self.install_generated_tippy_toe(engine)
        for name, ref in (
            ("Tippy-Toe, Terrific Partner", "additional-food-one"),
            ("Tippy-Toe, Terrific Partner", "additional-food-two"),
            ("Doubling Season", "double-counters"),
            ("Doc Samson, Super Psychiatrist", "add-counters"),
        ):
            self.add_permanent(engine, owner="A", name=name, ref=ref)

        tokens_before = {
            card.object_id
            for card in engine.state.cards.values()
            if card.is_token and card.zone == "battlefield"
        }
        self.stack_amass(engine)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual(
            tokens_before,
            {
                card.object_id
                for card in engine.state.cards.values()
                if card.is_token and card.zone == "battlefield"
            },
        )
        self.assertIsNone(
            StateProjector(self.database, engine.state)._decision("pilot:B")
        )

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.choose_replacement(session, "A")
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        armies = [
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.zone == "battlefield"
            and "army" in self.effective_subtypes(engine, card)
        ]
        self.assertEqual(1, len(armies))
        army = armies[0]
        self.assertNotIn("+1/+1", army.counters)
        self.choose_replacement(session, "A")
        self.assertEqual(6, army.counters["+1/+1"])
        self.assertEqual(
            ["Food", "Food", "Orc Army"],
            sorted(
                card.printed_name
                for card in engine.state.cards.values()
                if card.object_id not in tokens_before
                and card.is_token
                and card.zone == "battlefield"
            ),
        )
        codes = [event.code for event in engine.state.events]
        self.assertLess(codes.index("token.create"), codes.index("counter.add"))
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "amass-replacement-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.database, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
