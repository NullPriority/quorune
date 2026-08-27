from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.compiler.destruction_templates import (
    TargetedDestructionEffectTemplate,
    targeted_destruction_effect_template,
)
from quorune.compiler.direct_target import DirectPermanentTargetSpec
from quorune.deck import DeckLoader
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "targeted-destruction.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "targeted-destruction-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class TargetedDestructionTemplateTests(unittest.TestCase):
    def test_targeted_destruction_template_is_immutable_and_copy_isolated(self):
        template = TargetedDestructionEffectTemplate(
            DirectPermanentTargetSpec(types_any=("creature",))
        )

        self.assertEqual("destroy-target-creature-v2", template.template_id)
        self.assertEqual(
            ({"op": "destroy", "card": "$target.0"},),
            template.effects,
        )
        schema = template.target_schema
        schema["types_any"].append("artifact")
        effects = template.effects
        effects[0]["op"] = "exile"
        self.assertEqual(["creature"], template.target_schema["types_any"])
        self.assertEqual("destroy", template.effects[0]["op"])
        with self.assertRaisesRegex(ValueError, "target"):
            TargetedDestructionEffectTemplate(  # type: ignore[arg-type]
                "creature"
            )

    def test_destruction_whole_clause_parser_accepts_only_closed_direct_targets(self):
        cases = (
            (
                "Destroy target artifact or land.",
                {"types_any": ["artifact", "land"]},
            ),
            (
                "Destroy target artifact, creature, or planeswalker.",
                {"types_any": ["artifact", "creature", "planeswalker"]},
            ),
            (
                "Destroy target Spirit.",
                {"subtypes_any": ["spirit"]},
            ),
            (
                "Destroy target non-Vampire, non-Werewolf, non-Zombie creature.",
                {
                    "types_any": ["creature"],
                    "subtypes_none": ["vampire", "werewolf", "zombie"],
                },
            ),
            (
                "Destroy target nonblack creature.",
                {"types_any": ["creature"], "colors_none": ["B"]},
            ),
            (
                "Destroy target tapped creature.",
                {
                    "types_any": ["creature"],
                    "state_predicate": {
                        "entered_this_turn": False,
                        "tapped": True,
                        "counter_name": None,
                        "minimum_counter_count": None,
                    },
                },
            ),
            (
                "Destroy another target creature.",
                {"types_any": ["creature"], "source_exclusion": True},
            ),
            (
                "Destroy target creature an opponent controls.",
                {
                    "types_any": ["creature"],
                    "controller_relation": "opponent",
                },
            ),
            (
                "Destroy target attacking creature.",
                {
                    "types_any": ["creature"],
                    "combat_state": "attacking",
                },
            ),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                template = targeted_destruction_effect_template(text)
                self.assertIsNotNone(template)
                assert template is not None
                self.assertTrue(expected.items() <= template.target_schema.items())
        for text in (
            "Destroy up to one target creature.",
            "You may destroy target creature.",
            "Destroy target creature or Spacecraft.",
            "Destroy target Spirit or enchantment.",
            "Destroy target creature. It can't be regenerated.",
            "Destroy all creatures.",
            "Sacrifice target creature.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    targeted_destruction_effect_template(text)
                )


class TargetedDestructionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.base = cls.db.lookup("Lightning Greaves")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, oracle_text: str, *, type_line: str = "Instant"):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=oracle_text,
                type_line=type_line,
                keywords=(),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_spell_trigger_and_activated_contexts_share_targeted_destruction_lowering(self):
        contexts = (
            (
                "Destroy target artifact or land.",
                "Instant",
                "spell_ability",
                "destroy-target-artifact-or-land-v2",
            ),
            (
                "When this creature enters, destroy target non-Vampire creature.",
                "Creature — Test",
                "triggered_ability",
                "destroy-target-creature-non-vampire-v2",
            ),
            (
                "{2}{B}, {T}: Destroy target nonblack creature.",
                "Creature — Test",
                "activated_ability",
                "destroy-target-creature-non-b-v2",
            ),
        )
        for text, type_line, kind, template_id in contexts:
            with self.subTest(kind=kind, text=text):
                ir = self.compile(text, type_line=type_line)
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(template_id, node.template_id)
                self.assertEqual(
                    {
                        "permanent.destroy.effect",
                        "target.permanent.characteristic_predicate",
                        "target.revalidate_resolution",
                    },
                    set(node.capability_dependencies)
                    - {
                        "trigger.event.normalized_zone_change",
                        "trigger.placement.apnap",
                    },
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_unsupported_destruction_variants_remain_material_residuals(self):
        for text in (
            "Destroy up to one target creature.",
            "Destroy target creature or Spacecraft.",
            "Destroy target Spirit or enchantment.",
            "Destroy target creature. It can't be regenerated.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_targeted_destruction_shape_mutants_fail_closed(self):
        template = TargetedDestructionEffectTemplate(
            DirectPermanentTargetSpec(
                types_any=("artifact", "creature", "planeswalker")
            )
        )
        self.assertEqual(
            {
                "permanent.destroy.effect",
                "target.permanent.characteristic_predicate",
                "target.revalidate_resolution",
            },
            set(
                capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=template.target_schema,
                    mechanic_ids=template.mechanics,
                )
            ),
        )
        malformed_effects = (
            ({"op": "destroy", "card": "$target.1"},),
            ({"op": "destroy", "card": "$source"},),
            (
                {
                    "op": "destroy",
                    "card": "$target.0",
                    "reason": "open grammar",
                },
            ),
            ({"op": "move", "card": "$target.0"},),
        )
        for effects in malformed_effects:
            with self.subTest(effects=effects):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        malformed_schemas = (
            {**template.target_schema, "zones": ["graveyard"]},
            {**template.target_schema, "count": 2},
            {**template.target_schema, "types_any": ["noncreature"]},
            {
                **template.target_schema,
                "types_any": ["artifact", "creature"],
                "subtypes_any": ["vehicle"],
            },
            {**template.target_schema, "controller": "opponent"},
        )
        for schema in malformed_schemas:
            with self.subTest(schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=template.effects,
                        target_schema=schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        self.assertFalse(
            capability_dependencies_for_node(
                effects=template.effects,
                target_schema=template.target_schema,
                mechanic_ids=("cr-115-targets",),
            )
        )

    def test_generated_direct_target_program_is_capability_closed(self):
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (
                self.db.lookup("Murder"),
                self.db.lookup("Demolish"),
                self.db.lookup("Victim of Night"),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_effect_programs=True,
        )
        programs = [program for program in registry.programs() if program.effects]
        self.assertEqual(3, result["exact_effect_programs_promoted"])
        self.assertEqual(3, len(programs))
        self.assertEqual({"trusted"}, {program.trust_level for program in programs})
        self.assertTrue(
            all(
                {
                    "permanent.destroy.effect",
                    "target.revalidate_resolution",
                }.issubset(program.capability_dependencies)
                for program in programs
            )
        )
        self.assertEqual(
            2,
            sum(
                "target.permanent.characteristic_predicate"
                in program.capability_dependencies
                for program in programs
            ),
        )

    def test_targeted_destruction_parser_mutation_is_killed(self):
        def assert_demolish_is_exact() -> None:
            ir = self.compile("Destroy target artifact or land.")
            self.assertEqual("exact", ir.status)
            self.assertEqual(
                "destroy-target-artifact-or-land-v2",
                ir.faces[0].nodes[0].template_id,
            )

        assert_demolish_is_exact()
        with patch(
            "quorune.compiler.destruction_templates."
            "direct_permanent_target_spec",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_demolish_is_exact()


class TargetedDestructionRuntimeTests(unittest.TestCase):
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

    def session_with_demolish(self, *, seed: int):
        deck = copy.deepcopy(self.mishra)
        next(entry for entry in deck.entries if entry.board == "mainboard").name = (
            "Demolish"
        )
        session = make_session(
            self.db,
            deck,
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

    @staticmethod
    def card(engine, *, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    @staticmethod
    def permanent(engine, *, owner: str, required_type: str, exclude=()):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner
            and (record := engine.card_record(card)) is not None
            and required_type in record.type_line.casefold()
            and all(value not in record.type_line.casefold() for value in exclude)
        )

    @staticmethod
    def prepare_main(session, source):
        engine = session.engine
        engine.move_card(source.object_id, "hand", controller="A", log=False)
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].mana_pool.update({"C": 3, "R": 1})
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

    @staticmethod
    def cast_action(session, source):
        return next(
            action
            for action in session.packet("pilot:A", full=True)["decision"]["ctx"][
                "legal"
            ]["actions"]
            if action["id"] == f"cast:{source.ref}"
        )

    @staticmethod
    def pass_stack(session):
        while session.state.stack:
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Stack resolution stopped without priority")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)

    def assert_replays(self, session, label: str):
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_compiled_union_destruction_offer_command_resolution_and_replay(self):
        session = self.session_with_demolish(seed=811601)
        engine = session.engine
        source = self.card(engine, owner="A", name="Demolish")
        artifact = self.card(engine, owner="B", name="Sol Ring")
        land = self.permanent(engine, owner="B", required_type="land")
        creature = self.permanent(engine, owner="B", required_type="creature")
        for card in (artifact, land, creature):
            engine.move_card(card.object_id, "battlefield", controller="B", log=False)
        self.prepare_main(session, source)

        action = self.cast_action(session, source)
        legal_refs = set(action["target_schema"]["legal_refs"])
        self.assertTrue({artifact.ref, land.ref}.issubset(legal_refs))
        self.assertNotIn(creature.ref, legal_refs)
        for seat in "BCD":
            self.assertNotIn(
                action["id"],
                str(session.packet(f"pilot:{seat}", full=True)),
            )

        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [creature.ref],
                "pay": "manual",
                "payment": {"C": 3, "R": 1},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        source = engine.state.cards[source.object_id]
        artifact = engine.state.cards[artifact.object_id]
        land = engine.state.cards[land.object_id]
        creature = engine.state.cards[creature.object_id]

        action = self.cast_action(session, source)
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [artifact.ref],
                "pay": "manual",
                "payment": {"C": 3, "R": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_stack(session)

        self.assertEqual("graveyard", artifact.zone)
        self.assertEqual("graveyard", source.zone)
        self.assertEqual("battlefield", land.zone)
        self.assertEqual("battlefield", creature.zone)
        self.assert_replays(session, "typed-union-destruction")

    def test_compiled_union_destruction_respects_indestructible(self):
        session = self.session_with_demolish(seed=811602)
        engine = session.engine
        source = self.card(engine, owner="A", name="Demolish")
        target = self.card(engine, owner="C", name="Darksteel Citadel")
        engine.move_card(target.object_id, "battlefield", controller="C", log=False)
        self.prepare_main(session, source)

        action = self.cast_action(session, source)
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"C": 3, "R": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_stack(session)

        self.assertEqual("battlefield", target.zone)
        self.assertIn(
            "permanent.destroy.prohibited",
            [event.code for event in engine.state.events],
        )
        self.assert_replays(session, "typed-union-indestructible")


if __name__ == "__main__":
    unittest.main()
