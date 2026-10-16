from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.abilities import parse_activated_abilities
from quorune.activation_usage import (
    ActivationLimit,
    ActivationUsageError,
    activation_usage_verdict,
    commit_activation_usage,
)
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.fixed_source_effect_sequences import (
    SOURCE_ZONE_OBJECT,
    fixed_source_effect_sequence_template,
)
from quorune.deck import DeckLoader
from quorune.mana_undo import available_mana_undo
from quorune.model import CardInstance
from quorune.oracle_ir import (
    compile_oracle_card,
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
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
EXHAUST_CAPABILITY = "activation.exhaust.once_per_object"
SOURCE_SEQUENCE_CAPABILITY = "resolution.effect_sequence.fixed_source"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-source-sequences.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "exhaust-source-sequences.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedSourceEffectSequenceCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.pacesetter = cls.db.lookup("Pacesetter Paragon")
        cls.loot = cls.db.lookup("Loot, the Pathfinder")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def record(
        self,
        text: str,
        *,
        name: str = "Exhaust Fixture",
        type_line: str = "Creature — Test",
        keywords: tuple[str, ...] = ("Exhaust",),
    ) -> CardRecord:
        return replace(
            self.pacesetter,
            oracle_id="fixture:fixed-source-sequence",
            name=name,
            oracle_text=text,
            type_line=type_line,
            keywords=keywords,
        )

    def compile(self, text: str, **kwargs):
        return compile_oracle_card(
            self.record(text, **kwargs),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_exhaust_parser_and_usage_owner_are_per_ability(self):
        abilities = parse_activated_abilities(
            card_name=self.loot.name,
            oracle_text=self.loot.oracle_text,
            keywords=self.loot.keywords,
        )
        self.assertEqual(("ab2", "ab3", "ab4"), tuple(
            ability.ability_id for ability in abilities
        ))
        self.assertTrue(all(
            ability.activation_limit is ActivationLimit.EXHAUST_ONCE
            for ability in abilities
        ))
        self.assertNotIn("Activate each exhaust", abilities[0].effect_text)

        source = CardInstance(
            object_id="exhaust-usage-source",
            ref="A-exhaust-usage",
            oracle_id=self.loot.oracle_id,
            printed_name=self.loot.name,
            owner="A",
            controller="A",
            zone="battlefield",
        )
        commit_activation_usage(
            source,
            ability_id="ab2",
            limit=ActivationLimit.EXHAUST_ONCE,
            turn_sequence=1,
        )
        self.assertFalse(activation_usage_verdict(
            source,
            ability_id="ab2",
            limit=ActivationLimit.EXHAUST_ONCE,
            turn_sequence=99,
        ).available)
        self.assertTrue(activation_usage_verdict(
            source,
            ability_id="ab3",
            limit=ActivationLimit.EXHAUST_ONCE,
            turn_sequence=99,
        ).available)
        source.controller = "B"
        source.phased_out = True
        self.assertFalse(activation_usage_verdict(
            source,
            ability_id="ab2",
            limit=ActivationLimit.EXHAUST_ONCE,
            turn_sequence=100,
        ).available)

    def test_exhaust_usage_rejects_malformed_state_and_resets_on_zone_change(self):
        source = CardInstance(
            object_id="exhaust-reset-source",
            ref="A-exhaust-reset",
            oracle_id=self.pacesetter.oracle_id,
            printed_name=self.pacesetter.name,
            owner="A",
            controller="A",
            zone="battlefield",
        )
        source.annotations["exhaust_activations"] = ["ab1", "ab1"]
        with self.assertRaises(ActivationUsageError):
            activation_usage_verdict(
                source,
                ability_id="ab1",
                limit=ActivationLimit.EXHAUST_ONCE,
                turn_sequence=1,
            )
        source.annotations.clear()
        commit_activation_usage(
            source,
            ability_id="ab1",
            limit=ActivationLimit.EXHAUST_ONCE,
            turn_sequence=1,
        )
        original = source.logical_object_id

        with tempfile.TemporaryDirectory() as temporary:
            db = focused_card_database(temporary)
            loader = DeckLoader(db)
            mishra = loader.load(
                ROOT / "examples" / "mishra-eminent-one.txt",
                commander="Mishra, Eminent One",
                deck_name="Mishra",
            )
            zimone = loader.load(
                ROOT / "examples" / "zimone-and-dina.txt",
                commander="Zimone and Dina",
                deck_name="Zimone",
            )
            session = make_session(db, mishra, zimone, players=2, seed=122001)
            keep_all(session)
            engine = session.engine
            engine.state.cards[source.object_id] = source
            engine.state.players["A"].zones["battlefield"].append(source.object_id)
            engine.move_card(source.object_id, "graveyard", reason="reset witness")
            engine.move_card(source.object_id, "battlefield", reason="reset witness")
            self.assertNotEqual(original, source.logical_object_id)
            self.assertTrue(activation_usage_verdict(
                source,
                ability_id="ab1",
                limit=ActivationLimit.EXHAUST_ONCE,
                turn_sequence=1,
            ).available)
            db.close()

    def test_exhaust_limit_composes_with_existing_fixed_counter_effect(self):
        text = (
            "Exhaust — {3}{G}: Put three +1/+1 counters on this creature."
        )
        ir = self.compile(text)

        self.assertEqual("exact", ir.status)
        self.assertFalse(ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual("exhaust_once", node.cost["activation_limit"])
        self.assertTrue({
            EXHAUST_CAPABILITY,
            "counter.producer.fixed_effect",
        }.issubset(set(node.capability_dependencies)))

        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        row = next(
            value
            for value in payload["capabilities"]
            if value["id"] == EXHAUST_CAPABILITY
        )
        row["status"] = "blocked"
        row["blockers"] = ["mutation witness"]
        gated = compile_oracle_card(
            self.record(text),
            capability_registry=CapabilityRegistry(payload),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", gated.status)
        self.assertTrue(gated.material_residuals)

    def test_fixed_source_sequence_compiles_exact_source_spanned_activated_nodes(self):
        ir = compile_oracle_card(
            self.pacesetter,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", ir.status)
        node = next(
            value
            for value in ir.faces[0].nodes
            if value.template_id
            == "fixed-source-counter-characteristics-sequence-v1"
        )
        self.assertTrue(node.exact)
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual("exhaust_once", node.cost["activation_limit"])
        self.assertEqual(
            self.pacesetter.oracle_text,
            self.pacesetter.oracle_text[node.span.start : node.span.end],
        )
        self.assertEqual(
            [SOURCE_ZONE_OBJECT, SOURCE_ZONE_OBJECT],
            [effect["card"] for effect in node.effects],
        )
        self.assertTrue({
            EXHAUST_CAPABILITY,
            SOURCE_SEQUENCE_CAPABILITY,
            "counter.producer.fixed_effect",
            "continuous.resolution.fixed_characteristics_until_end_of_turn",
            "combat.damage.participation.strike_steps",
        }.issubset(set(node.capability_dependencies)))

        loot_ir = compile_oracle_card(
            self.loot,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", loot_ir.status)
        self.assertFalse(loot_ir.material_residuals)
        mana = next(
            value
            for value in loot_ir.faces[0].nodes
            if value.template_id == "activated-mana-fixed-output-v1"
        )
        self.assertTrue(mana.exact)
        self.assertEqual("exhaust_once", mana.cost["activation_limit"])
        self.assertIn(EXHAUST_CAPABILITY, mana.capability_dependencies)

    def test_fixed_source_sequence_is_reused_by_trigger_context(self):
        ir = compile_oracle_card(
            self.record(
                "When this creature enters, put a +1/+1 counter on this "
                "creature. It gains vigilance until end of turn.",
                name="Source Sequence Trigger",
                keywords=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

        self.assertEqual("exact", ir.status)
        self.assertFalse(ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual("triggered_ability", node.kind)
        self.assertEqual(
            "fixed-source-counter-characteristics-sequence-v1",
            node.template_id,
        )
        self.assertEqual(
            [SOURCE_ZONE_OBJECT, SOURCE_ZONE_OBJECT],
            [effect["card"] for effect in node.effects],
        )

    def test_fixed_source_sequence_rejects_open_variants(self):
        variants = (
            "Put a +1/+1 counter on this creature. It gains flying.",
            "You may put a +1/+1 counter on this creature. It gains flying until end of turn.",
            "Put X +1/+1 counters on this creature. It gains flying until end of turn.",
            "Put a +1/+1 counter on this creature. It gains ward {2} until end of turn.",
            "It gains flying until end of turn. Put a +1/+1 counter on this creature.",
            "Put a +1/+1 counter on this creature. It gains flying until end of turn. Draw a card.",
        )
        for text in variants:
            with self.subTest(text=text):
                self.assertIsNone(fixed_source_effect_sequence_template(
                    text,
                    card_name="Exhaust Fixture",
                    source_is_permanent=True,
                ))
                ir = self.compile(f"Exhaust — {{0}}: {text}")
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
        self.assertIsNone(fixed_source_effect_sequence_template(
            "Put a +1/+1 counter on this spell. It gains flying until end of turn.",
            card_name="Exhaust Fixture",
            source_is_permanent=False,
        ))

    def test_exhaust_override_wording_remains_material_residual(self):
        text = (
            "Exhaust — {0}: Put a +1/+1 counter on this creature. "
            "It gains flying until end of turn.\n"
            "Whenever this creature enters, you may activate each of its "
            "exhaust abilities one additional time this turn."
        )
        ir = self.compile(text)
        sequence = next(
            value
            for value in ir.faces[0].nodes
            if value.template_id
            == "fixed-source-counter-characteristics-sequence-v1"
        )
        self.assertTrue(sequence.exact)
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_fixed_source_sequence_shape_and_dependency_mutants_fail_closed(self):
        template = fixed_source_effect_sequence_template(
            "Put a +1/+1 counter on this creature. "
            "It gains flying until end of turn.",
            card_name="Exhaust Fixture",
            source_is_permanent=True,
        )
        self.assertIsNotNone(template)
        assert template is not None
        expected = {
            "combat.block.flying",
            "continuous.resolution.fixed_characteristics_until_end_of_turn",
            "counter.producer.fixed_effect",
            SOURCE_SEQUENCE_CAPABILITY,
        }
        self.assertEqual(expected, set(capability_dependencies_for_node(
            effects=template.effects,
            target_schema=None,
            mechanic_ids=template.mechanic_ids,
        )))
        malformed = (
            ({**template.effects[0], "amount": True}, template.effects[1]),
            ({**template.effects[0], "card": "$source"}, template.effects[1]),
            (template.effects[0], {**template.effects[1], "keyword": "Ward {2}"}),
            (template.effects[0], {**template.effects[1], "extra": True}),
            (template.effects[1], template.effects[0]),
        )
        for effects in malformed:
            with self.subTest(effects=effects):
                dependencies = capability_dependencies_for_node(
                    effects=effects,
                    target_schema=None,
                    mechanic_ids=template.mechanic_ids,
                )
                self.assertNotIn(SOURCE_SEQUENCE_CAPABILITY, dependencies)
                self.assertNotIn("counter.producer.fixed_effect", dependencies)

        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        for blocked in (SOURCE_SEQUENCE_CAPABILITY, "counter.producer.fixed_effect"):
            mutated = json.loads(json.dumps(payload))
            row = next(
                value for value in mutated["capabilities"] if value["id"] == blocked
            )
            row["status"] = "blocked"
            row["blockers"] = ["mutation witness"]
            registry = CapabilityRegistry(mutated)
            ir = compile_oracle_card(
                self.pacesetter,
                capability_registry=registry,
                capability_profile="commander_review",
            )
            self.assertNotEqual("exact", ir.status)
            self.assertTrue(ir.material_residuals)


class FixedSourceEffectSequenceRuntimeTests(unittest.TestCase):
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
    def prepare_priority(session, *, seat: str = "A"):
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
    def pass_until(session, predicate, *, limit: int = 24):
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

    def choose_replacements(self, session, *, expect_success: bool = True):
        for _ in range(12):
            decision = session.engine.state.pending_decision
            if decision is None or decision.kind != "replacement.order":
                return None
            packet = StateProjector(self.db, session.state)._decision("pilot:A")
            self.assertIsNotNone(packet)
            selected = packet["ctx"]["options"][0]["id"]
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

    def stage_pacesetter(self, session, *, replacements: bool = True):
        source = self.add_permanent(
            session,
            seat="A",
            name="Pacesetter Paragon",
            ref=f"A-pacesetter-{session.state.config.seed}",
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
        session.state.players["A"].mana_pool["R"] = 3
        self.prepare_priority(session)
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        action_id = f"activate:{source.ref}:ab1"
        packet = session.packet("pilot:A", full=True)
        offered = {
            action["id"]
            for action in packet["decision"]["ctx"]["legal"]["actions"]
        }
        self.assertIn(action_id, offered)
        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        return source

    def test_exhaust_mana_activation_is_explicit_nonreversible_and_replays(self):
        session = self.session(122101)
        source = self.add_permanent(
            session,
            seat="A",
            name="Loot, the Pathfinder",
            ref="A-loot-exhaust",
        )
        session.state.players["A"].mana_pool["G"] = 1
        self.prepare_priority(session)
        ability = next(
            value
            for value in session.engine._activated_abilities(source)
            if value.ability_id == "ab2"
        )
        self.assertIs(ability.activation_limit, ActivationLimit.EXHAUST_ONCE)
        self.assertNotIn(
            source.object_id,
            {
                value.object_id
                for value in session.engine.available_mana_sources("A")
            },
        )
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "a": "activate",
                "source": source.ref,
                "ability": ability.ability_id,
                "mana_output": {"B": 3},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(source.tapped)
        self.assertEqual(0, session.state.players["A"].mana_pool["G"])
        self.assertEqual(3, session.state.players["A"].mana_pool["B"])
        self.assertIsNone(available_mana_undo(session.state, "A"))
        self.assertEqual(
            ("ab2",), tuple(source.annotations["exhaust_activations"])
        )
        self.assertEqual(
            ("unavailable", "exhaust_ability_already_activated"),
            session.engine._ability_availability("A", source, ability),
        )

        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "exhaust-mana-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_exhaust_usage_commit_mutant_is_killed(self):
        session = self.session(122106)
        source = self.add_permanent(
            session,
            seat="A",
            name="Loot, the Pathfinder",
            ref="A-loot-exhaust-mutation",
        )
        session.state.players["A"].mana_pool["G"] = 1
        self.prepare_priority(session)
        before = authoritative_state_hash(session.state)
        with patch(
            "quorune.rules.activation.payment_commit.commit_activation_usage",
            return_value=None,
        ):
            result = session.act(
                "pilot:A",
                {
                    "a": "activate",
                    "source": source.ref,
                    "ability": "ab2",
                    "mana_output": {"B": 3},
                },
            )
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        restored = session.state.cards[source.object_id]
        self.assertFalse(restored.tapped)
        self.assertNotIn("exhaust_activations", restored.annotations)

    def test_exhaust_source_sequence_suspends_for_replacement_and_resumes_exactly(self):
        session = self.session(122102, players=4)
        source = self.stage_pacesetter(session)
        self.pass_until(
            session,
            lambda: session.state.pending_decision is not None
            and session.state.pending_decision.kind == "replacement.order",
        )
        projected = StateProjector(self.db, session.state)._decision("pilot:A")
        self.assertIsNotNone(projected)
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))
        for seat in ("B", "C", "D"):
            self.assertIsNone(
                StateProjector(self.db, session.state)._decision(f"pilot:{seat}")
            )

        self.choose_replacements(session)
        self.assertGreater(source.counters["+1/+1"], 1)
        self.assertIn("double strike", session.engine._combat_keywords(source))
        self.assertEqual(
            ("ab1",), tuple(source.annotations["exhaust_activations"])
        )
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "source-sequence-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_source_sequence_rejects_stale_source_identity_without_partial_mutation(self):
        session = self.session(122103)
        source = self.stage_pacesetter(session)
        self.pass_until(
            session,
            lambda: session.state.pending_decision is not None
            and session.state.pending_decision.kind == "replacement.order",
        )
        original = source.logical_object_id
        session.engine.move_card(source.object_id, "graveyard", reason="stale source")
        session.engine.move_card(source.object_id, "battlefield", reason="stale source")
        self.assertNotEqual(original, source.logical_object_id)

        result = self.choose_replacements(session, expect_success=False)
        self.assertIsNotNone(result)
        self.assertFalse(result.ok)
        current = session.state.cards[source.object_id]
        self.assertEqual({}, current.counters)
        self.assertNotIn("double strike", session.engine._combat_keywords(current))
        self.assertFalse(session.state.continuous_effects)

    def test_source_sequence_replacement_rollback_is_atomic(self):
        session = self.session(122104)
        source = self.stage_pacesetter(session)
        self.pass_until(
            session,
            lambda: session.state.pending_decision is not None
            and session.state.pending_decision.kind == "replacement.order",
        )
        result = None
        with patch(
            "quorune.effect_runtime.objects_stack_and_tokens."
            "create_resolution_continuous_effect",
            return_value=None,
        ):
            result = self.choose_replacements(session, expect_success=False)
        self.assertIsNotNone(result)
        self.assertFalse(result.ok)
        current = session.state.cards[source.object_id]
        self.assertEqual({}, current.counters)
        self.assertNotIn("double strike", session.engine._combat_keywords(current))
        self.assertFalse(session.state.continuous_effects)

    def test_source_sequence_replacement_continuation_survives_restart(self):
        session = self.session(122105, players=4)
        source = self.stage_pacesetter(session)
        self.pass_until(
            session,
            lambda: session.state.pending_decision is not None
            and session.state.pending_decision.kind == "replacement.order",
        )
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "source-sequence-restart"
            session.save(record_dir)
            restarted = CommanderSession.load(self.db, record_dir)
            self.choose_replacements(restarted)
            restarted_source = restarted.state.cards[source.object_id]
            self.assertGreater(restarted_source.counters["+1/+1"], 1)
            self.assertIn(
                "double strike",
                restarted.engine._combat_keywords(restarted_source),
            )
            restarted.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(
            authoritative_state_hash(restarted.state),
            replay["final_state_hash"],
        )

    def test_source_sequence_compiler_mutant_is_killed(self):
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_source_effect_sequence_template",
            return_value=None,
        ):
            ir = compile_oracle_card(
                self.db.lookup("Pacesetter Paragon"),
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)


if __name__ == "__main__":
    unittest.main()
