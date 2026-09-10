from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT, keep_all, make_session
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.fixed_target_effect_sequences import (
    FIXED_SOURCE_CHARACTERISTIC_MECHANIC,
    fixed_source_characteristics_effect_template,
)
from quorune.continuous_effects import ContinuousEffectError, ContinuousOperation, Layer
from quorune.continuous_effect_state import (
    expire_end_of_turn_continuous_effects,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, CombatState
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


FIXTURE = ROOT / "tests" / "fixtures" / "fixed-source-characteristic-effects.json"
SOURCE_CAPABILITY = (
    "continuous.resolution.fixed_source_characteristics_until_end_of_turn"
)
TARGET_CAPABILITY = "continuous.resolution.fixed_characteristics_until_end_of_turn"


def record(
    oracle_text: str,
    *,
    type_line: str = "Creature — Test",
) -> CardRecord:
    creature = "Creature" in type_line
    return CardRecord(
        oracle_id="19000000-0000-4000-8000-000000000199",
        name="Fixed Source Characteristic Fixture",
        mana_cost="{2}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power="2" if creature else None,
        toughness="2" if creature else None,
        loyalty=None,
        defense=None,
        colors=("R",) if creature else (),
        color_identity=("R",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedSourceCharacteristicCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()

    def compile(self, text: str, *, type_line: str = "Creature — Test"):
        return compile_oracle_card(
            record(text, type_line=type_line),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_source_characteristics_compile_across_shared_contexts(self):
        cases = (
            (
                "{1}: This creature gets +2/+0 and gains first strike until end of turn.",
                "Creature — Warrior",
                "activated_ability",
                {"combat.damage.participation.strike_steps"},
            ),
            (
                "Whenever you cast a noncreature spell, this creature gains vigilance and lifelink until end of turn.",
                "Creature — Soldier",
                "triggered_ability",
                {"combat.attack.vigilance", "damage.result.lifelink"},
            ),
            (
                "{1}: This creature becomes colorless until end of turn.",
                "Creature — Kavu",
                "activated_ability",
                set(),
            ),
            (
                "{2}: This artifact becomes a 2/2 white and blue Bird artifact creature with flying until end of turn.",
                "Artifact",
                "activated_ability",
                {"combat.block.flying"},
            ),
            (
                "{3}: This artifact becomes a Shapeshifter artifact creature with base power and toughness 5/5 until end of turn.",
                "Artifact",
                "activated_ability",
                set(),
            ),
            (
                "{3}: This artifact becomes a 4/4 artifact creature until end of turn.",
                "Artifact — Clue",
                "activated_ability",
                set(),
            ),
            (
                "Target creature gains shadow until end of turn.",
                "Instant",
                "spell_ability",
                {"combat.block.shadow", "target.revalidate_resolution"},
            ),
        )
        for text, type_line, kind, additional in cases:
            with self.subTest(text=text):
                compiled = self.compile(text, type_line=type_line)
                self.assertEqual("exact", compiled.status, compiled.to_dict())
                node = compiled.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertTrue(node.exact)
                expected_capability = (
                    TARGET_CAPABILITY
                    if text.startswith("Target creature")
                    else SOURCE_CAPABILITY
                )
                self.assertIn(
                    expected_capability,
                    node.capability_dependencies,
                )
                self.assertLessEqual(
                    additional,
                    set(node.capability_dependencies),
                )
                self.assertEqual(
                    text,
                    compiled.faces[0].oracle_text[
                        node.span.start : node.span.end
                    ],
                )

    def test_source_characteristic_grammar_keeps_open_forms_residual(self):
        cases = (
            (
                "{1}: This creature gets +X/+X and gains trample until end of turn.",
                "Creature — Test",
            ),
            (
                "{1}: This creature gains your choice of flying or haste until end of turn.",
                "Creature — Test",
            ),
            (
                "{1}: This creature gains banding until end of turn.",
                "Creature — Test",
            ),
            (
                "{2}: This land becomes a 2/2 creature until end of turn. It's still a land.",
                "Land",
            ),
            (
                "{2}: This artifact becomes a 2/2 Bird artifact creature until end of turn and can't be blocked this turn.",
                "Artifact",
            ),
            (
                "{2}: This artifact becomes a copy of target creature until end of turn.",
                "Artifact",
            ),
            (
                "{2}: This artifact becomes a 2/2 Bird artifact creature until end of turn.",
                "Creature — Test",
            ),
        )
        for text, type_line in cases:
            with self.subTest(text=text):
                compiled = self.compile(text, type_line=type_line)
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(compiled.material_residuals)

    def test_source_characteristic_shape_is_closed_and_capability_bound(self):
        template = fixed_source_characteristics_effect_template(
            "This artifact becomes a 2/2 blue Bird artifact creature with flying until end of turn.",
            source_is_permanent=True,
            source_card_types=("artifact",),
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(
            (SOURCE_CAPABILITY,),
            tuple(
                capability
                for capability in capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=None,
                    mechanic_ids=template.mechanics,
                )
                    if capability == SOURCE_CAPABILITY
            ),
        )
        effect = template.effects[0]
        mutations = (
            {**effect, "card": "$source"},
            {**effect, "set_card_types": ["Artifact", "Artifact"]},
            {**effect, "set_colors": ["U", "W"]},
            {**effect, "base_toughness": None},
            {**effect, "power": {"kind": "dynamic"}},
            {**effect, "keywords": ["Flying", "Flying"]},
            {**effect, "unknown": True},
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated):
                self.assertNotIn(
                    SOURCE_CAPABILITY,
                    capability_dependencies_for_node(
                        effects=(mutated,),
                        target_schema=None,
                        mechanic_ids=template.mechanics,
                    ),
                )
        self.assertEqual(
            (),
            tuple(
                ContinuousOperation(
                    "set_types",
                    [],
                    field="subtypes",
                ).value
            ),
        )
        with self.assertRaises(ContinuousEffectError):
            ContinuousOperation("set_types", [], field="card_types")


class FixedSourceCharacteristicRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "source-characteristics.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT
                / "tests"
                / "fixtures"
                / "query-power-toughness-definition-cards.json",
                FIXTURE,
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
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        session,
        *,
        name: str,
        ref: str,
        seat: str = "A",
        zone: str = "battlefield",
        register: bool = True,
    ) -> CardInstance:
        engine = session.engine
        record_value = self.db.lookup(name)
        card = CardInstance(
            object_id=f"source-characteristic:{ref}",
            ref=ref,
            oracle_id=record_value.oracle_id,
            printed_name=record_value.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=(list(engine.seats) if zone != "hand" else [seat]),
            revealed_to=(list(engine.seats) if zone != "hand" else []),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        if register:
            register_generated_programs(
                self.db,
                engine.semantics,
                (record_value,),
                trust_level="provisional",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
                promote_exact_runtime_handlers=True,
                promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True,
                promote_exact_capability_declarations=True,
            )
        return card

    @staticmethod
    def prepare_main(session) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        engine._grant_priority("A")
        engine._issue_priority("A")

    @staticmethod
    def activation_offer(session, source: CardInstance) -> dict:
        engine = session.engine
        ability = engine._activated_abilities(source)[0]
        engine.pump()
        decision = session.packet("pilot:A", full=True)["decision"]
        return next(
            action
            for action in decision["ctx"]["legal"]["actions"]
            if action["id"] == f"activate:{source.ref}:{ability.ability_id}"
        )

    @staticmethod
    def resolve_stack(session) -> None:
        for _ in range(12):
            if not session.engine.state.stack:
                return
            principal = session.pending_principals()[0]
            result = session.act(principal, {"a": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Characteristic activation did not resolve")

    def activate(self, session, source: CardInstance) -> None:
        offer = self.activation_offer(session, source)
        result = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.resolve_stack(session)

    def test_artifact_animation_uses_one_multilayer_timestamp_and_replays(self):
        session = self.session(70261101, players=4)
        engine = session.engine
        source = self.add_card(
            session,
            name="Artifact Animation Fixture",
            ref="ANIMATE",
        )
        attacker = self.add_card(
            session,
            name="Goblin Engineer",
            ref="ANIMATE-FLYER",
            seat="B",
            register=False,
        )
        attacker.temporary_keywords = ["Flying"]
        engine.state.players["A"].mana_pool["C"] = 2
        self.prepare_main(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.activate(session, source)

        effective = engine._effective_card_data(source)
        card_types, subtypes, _supertypes = engine._type_parts(
            effective["type_line"]
        )
        self.assertEqual({"artifact", "creature"}, card_types)
        self.assertEqual({"bird"}, subtypes)
        self.assertEqual(["W", "U"], effective["colors"])
        self.assertEqual("2", effective["power"])
        self.assertEqual("2", effective["toughness"])
        self.assertIn("Flying", effective["keywords"])
        attacker.attacking = "A"
        engine.state.combat = CombatState(
            attackers_declared=True,
            attackers={attacker.object_id: "A"},
            defending_players=["A"],
        )
        self.assertTrue(engine._can_block(attacker, source)[0])
        attacker.attacking = None
        engine.state.combat = CombatState()
        components = [
            effect
            for effect in engine.state.continuous_effects
            if effect.source_id == source.object_id
        ]
        self.assertEqual(
            {Layer.TYPE, Layer.COLOR, Layer.ABILITY, Layer.POWER_TOUGHNESS},
            {effect.layer for effect in components},
        )
        self.assertEqual(1, len({effect.timestamp for effect in components}))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            packet = StateProjector(self.db, engine.state)._snapshot(principal)
            self.assertNotIn("continuous_effects", json.dumps(packet))

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "source-animation-replay"
            session.save(game_dir)
            loaded = CommanderSession.load(self.db, game_dir)
            self.assertEqual(
                expected_hash,
                authoritative_state_hash(loaded.engine.state),
            )
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_source_effects_follow_incarnation_control_and_cleanup_boundaries(self):
        session = self.session(70261102)
        engine = session.engine
        growth = self.add_card(
            session,
            name="Source Growth Fixture",
            ref="GROWTH",
        )
        engine.state.players["A"].mana_pool["C"] = 1
        self.prepare_main(session)
        offer = self.activation_offer(session, growth)
        result = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        engine.change_control(
            growth.object_id,
            "B",
            reason="source characteristic control witness",
        )
        self.resolve_stack(session)
        self.assertEqual(3, engine._numeric_stat(growth.object_id, "power"))
        self.assertIn(
            "first strike",
            engine._combat_keywords(growth),
        )
        self.assertGreater(expire_end_of_turn_continuous_effects(engine.state), 0)
        self.assertEqual(1, engine._numeric_stat(growth.object_id, "power"))

        engine.change_control(growth.object_id, "A", reason="reset witness")
        engine.state.players["A"].mana_pool["C"] = 1
        self.prepare_main(session)
        offer = self.activation_offer(session, growth)
        result = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        engine.move_card(growth.object_id, "graveyard", log=False)
        engine.move_card(
            growth.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.resolve_stack(session)
        self.assertEqual(1, engine._numeric_stat(growth.object_id, "power"))
        self.assertNotIn("first strike", engine._combat_keywords(growth))

    def test_animation_updates_dynamic_counts_and_detaches_equipment(self):
        session = self.session(70261103)
        engine = session.engine
        source = self.add_card(
            session,
            name="Equipment Animation Fixture",
            ref="ANIMATE-EQUIPMENT",
        )
        recipient = self.add_card(
            session,
            name="Goblin Engineer",
            ref="ANIMATE-RECIPIENT",
            register=False,
        )
        queen = self.add_card(
            session,
            name="Queen Allenal of Ruadach",
            ref="ANIMATE-COUNT",
        )
        attach_objects(
            engine.state.cards,
            source,
            recipient,
            source_timestamp=engine._next_zone_timestamp(),
            players=engine.state.players,
        )
        self.assertEqual(2, engine._numeric_stat(queen.object_id, "power"))
        engine.state.players["A"].mana_pool["C"] = 3
        self.prepare_main(session)
        self.activate(session, source)

        self.assertIsNone(source.attached_to)
        self.assertNotIn(source.object_id, recipient.attachments)
        self.assertEqual(3, engine._numeric_stat(queen.object_id, "power"))
        self.assertEqual(1, engine._numeric_stat(source.object_id, "power"))
        self.assertEqual(5, engine._numeric_stat(source.object_id, "toughness"))

    def test_malformed_source_characteristics_roll_back_atomically(self):
        session = self.session(70261104)
        engine = session.engine
        source = self.add_card(
            session,
            name="Source Keywords Fixture",
            ref="SOURCE-ROLLBACK",
        )
        effect = copy.deepcopy(
            fixed_source_characteristics_effect_template(
                "This creature gains vigilance and lifelink until end of turn.",
                source_is_permanent=True,
                source_card_types=("creature",),
            ).effects[0]
        )
        effect["card"] = source.ref
        effect["keywords"] = ["Vigilance", "Vigilance"]
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine.apply_effect(effect, actor="A")
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_targeted_shadow_uses_shared_keyword_and_block_legality(self):
        session = self.session(70261105, players=4)
        engine = session.engine
        spell = self.add_card(
            session,
            name="Target Shadow Fixture",
            ref="TARGET-SHADOW",
            zone="hand",
        )
        target = self.add_card(
            session,
            name="Goblin Engineer",
            ref="TARGET-SHADOW-BLOCKER",
            register=False,
        )
        attacker = self.add_card(
            session,
            name="Goblin Engineer",
            ref="TARGET-SHADOW-ATTACKER",
            seat="B",
            register=False,
        )
        attacker.temporary_keywords = ["Shadow"]
        attacker.attacking = "A"
        engine.state.combat = CombatState(
            attackers_declared=True,
            attackers={attacker.object_id: "A"},
            defending_players=["A"],
        )
        self.assertFalse(engine._can_block(attacker, target)[0])
        attacker.attacking = None
        engine.state.combat = CombatState()

        engine.state.players["A"].mana_pool["U"] = 1
        self.prepare_main(session)
        engine.permissions.invalidate_current()
        engine._cast(
            "A",
            {"card": spell.ref, "targets": [target.ref], "pay": "auto"},
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("graveyard", spell.zone)
        self.assertIn("shadow", engine._combat_keywords(target))
        attacker.attacking = "A"
        engine.state.combat = CombatState(
            attackers_declared=True,
            attackers={attacker.object_id: "A"},
            defending_players=["A"],
        )
        self.assertTrue(engine._can_block(attacker, target)[0])


if __name__ == "__main__":
    unittest.main()
