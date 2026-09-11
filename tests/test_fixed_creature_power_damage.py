from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from damage_replacement_support import quantity_descriptor
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.creature_power_damage_templates import (
    fixed_creature_power_damage_effect_template,
)
from quorune.creature_power_damage import (
    capture_creature_power_damage_source_lki,
)
from quorune.creature_power_damage_model import (
    CREATURE_POWER_DAMAGE_CAPABILITY,
    CREATURE_POWER_DAMAGE_LKI_CONTEXT,
    CREATURE_POWER_DAMAGE_MECHANIC,
    CREATURE_POWER_DAMAGE_OPERATION,
)
from quorune.deck import DeckLoader
from quorune.damage_modifier_state import DamagePreventionScope
from quorune.damage_prevention import (
    DamageModifierDuration,
    DamagePreventionShield,
    DamageSubject,
    PreventionDamageKind,
    PreventionMode,
    PreventionRecipientKind,
)
from quorune.errors import GameRuleError
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import CapabilityRegistry
from quorune.rules.creature_power_damage_capability_shapes import (
    fixed_creature_power_damage_node_capabilities,
)
from quorune.semantics import SemanticProgram
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "fixed-creature-power-damage-cards.json"
)


def trusted_registry(value: dict | None = None) -> CapabilityRegistry:
    registry = CapabilityRegistry(
        value or json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    )
    registry.mark_evidence_verified("0" * 64)
    return registry


def card_record(text: str, *, type_line: str = "Sorcery") -> CardRecord:
    is_creature = "Creature" in type_line
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000000402",
        name="Power Damage Fixture",
        mana_cost="{1}{G}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=text,
        power="3" if is_creature else None,
        toughness="3" if is_creature else None,
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


class FixedCreaturePowerDamageCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        self.capabilities = trusted_registry(self.registry_value)

    def compile(self, text: str, *, type_line: str = "Sorcery", registry=None):
        return compile_oracle_card(
            card_record(text, type_line=type_line),
            capability_registry=registry or self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_creature_power_damage_compiles_across_supported_contexts(self):
        cases = (
            (
                "Target creature you control fights target creature an opponent controls.",
                "Sorcery",
                "fixed-target-creature-fight-v1",
            ),
            (
                "When this creature enters, this creature fights another target creature.",
                "Creature — Beast",
                "fixed-source-creature-fight-v1",
            ),
            (
                "When this creature enters, you may have it fight target creature you don't control.",
                "Creature — Beast",
                "fixed-optional-fixed-source-creature-fight-v1",
            ),
            (
                "{T}: This creature deals damage equal to its power to any target.",
                "Creature — Archer",
                "fixed-source-creature-power-damage-v1",
            ),
            (
                "Target creature you control deals damage equal to its power to any other target.",
                "Instant",
                "fixed-target-creature-power-damage-v1",
            ),
            (
                "Target blocking creature fights another target blocking creature.",
                "Instant",
                "fixed-target-blocking-creature-fight-v1",
            ),
        )
        for text, type_line, template_id in cases:
            with self.subTest(text=text):
                compiled = self.compile(text, type_line=type_line)
                self.assertEqual("exact", compiled.status, compiled.material_residuals)
                node = next(
                    node
                    for face in compiled.faces
                    for node in face.nodes
                    if CREATURE_POWER_DAMAGE_MECHANIC in node.mechanics
                )
                self.assertEqual(template_id, node.template_id)
                self.assertTrue(node.exact)
                self.assertIn(
                    CREATURE_POWER_DAMAGE_CAPABILITY,
                    node.capability_closure,
                )

    def test_fixed_prep_fight_sequences_share_target_roles(self):
        cases = (
            (
                "Target green creature you control gets +2/+2 until end of turn. "
                "It fights target green creature an opponent controls.",
                "modify_stats_until_end_of_turn",
                "continuous.resolution.fixed_characteristics_until_end_of_turn",
            ),
            (
                "Put two +1/+1 counters on target Beast creature you control. "
                "Then that creature fights up to one target creature you don't control.",
                "place_counters",
                "counter.producer.fixed_effect",
            ),
        )
        for text, prep_operation, prep_capability in cases:
            with self.subTest(text=text):
                compiled = self.compile(text)
                self.assertEqual("exact", compiled.status, compiled.material_residuals)
                node = compiled.faces[0].nodes[0]
                self.assertEqual(prep_operation, node.effects[0]["op"])
                self.assertEqual(CREATURE_POWER_DAMAGE_OPERATION, node.effects[1]["op"])
                self.assertEqual("$target.0", node.effects[0]["card"])
                self.assertEqual("$target.0", node.effects[1]["source"])
                self.assertEqual("$target.1", node.effects[1]["target"])
                self.assertTrue(node.target_schema["globally_distinct"])
                self.assertIn(prep_capability, node.capability_dependencies)

    def test_unsupported_creature_power_wording_and_shape_mutations_fail_closed(self):
        unsupported = (
            "Target creature you control fights a random creature.",
            "Target creature you control deals damage equal to its toughness to any target.",
            "Target creature you control deals damage equal to twice its power to any target.",
            "Target creature you control deals damage equal to its power divided as you choose among any number of targets.",
            "It deals damage equal to its power to any target.",
            "Whenever another creature enters, it deals damage equal to its power to any target.",
            "Return target creature card from your graveyard to the battlefield. When you do, it fights target creature.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                compiled = self.compile(text)
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(compiled.material_residuals)

        template = fixed_creature_power_damage_effect_template(
            "Target creature you control fights target creature an opponent controls.",
            card_name="Power Damage Fixture",
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertIn(
            CREATURE_POWER_DAMAGE_CAPABILITY,
            fixed_creature_power_damage_node_capabilities(
                effects=template.effects,
                target_schema=template.target_schema,
                mechanic_ids=template.mechanics,
            ),
        )
        mutations = (
            ({**template.effects[0], "kind": "bite"}, template.target_schema),
            ({**template.effects[0], "target": "$target.0"}, template.target_schema),
            ({**template.effects[0], "source_lki": {}}, template.target_schema),
            (template.effects[0], {**template.target_schema, "globally_distinct": False}),
        )
        for effect, schema in mutations:
            with self.subTest(effect=effect, schema=schema):
                self.assertEqual(
                    (),
                    fixed_creature_power_damage_node_capabilities(
                        effects=(effect,),
                        target_schema=schema,
                        mechanic_ids=template.mechanics,
                    ),
                )

        blocked = copy.deepcopy(self.registry_value)
        dependency = next(
            row
            for row in blocked["capabilities"]
            if row["id"] == "target.revalidate_resolution"
        )
        dependency["status"] = "blocked"
        compiled = self.compile(
            "Target creature you control fights target creature an opponent controls.",
            registry=trusted_registry(blocked),
        )
        self.assertNotEqual("exact", compiled.status)

    def test_another_power_damage_recipient_excludes_the_dealing_creature(self):
        direct = fixed_creature_power_damage_effect_template(
            "Target creature you control deals damage equal to its power to another target creature.",
            card_name="Power Damage Fixture",
        )
        source = fixed_creature_power_damage_effect_template(
            "This creature deals damage equal to its power to any other target.",
            card_name="Power Damage Fixture",
            allow_source_pronoun=True,
        )
        self.assertIsNotNone(direct)
        self.assertIsNotNone(source)
        assert direct is not None and source is not None
        self.assertEqual(
            ["fighter"],
            direct.target_schema["groups"][1]["different_from_groups"],
        )
        self.assertTrue(source.target_schema["groups"][0]["source_exclusion"])


class FixedCreaturePowerDamageRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-creature-power-damage.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT / "tests" / "fixtures" / "damage-replacement-cards.json",
                FIXTURE_PATH,
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
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, card_name: str | None = None):
        mishra = copy.deepcopy(self.mishra)
        if card_name is not None:
            next(
                entry for entry in mishra.entries if entry.board == "mainboard"
            ).name = card_name
        session = make_session(
            self.db,
            mishra,
            copy.deepcopy(self.zimone),
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
    def named_card(engine, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and card.printed_name == name
            and card.zone != "command"
        )

    def promote(self, engine, *names: str) -> None:
        records = tuple(self.db.lookup(name) for name in names)
        for record in records:
            for program in engine.semantics.programs_for_oracle(record.oracle_id):
                engine.semantics.remove(program.key)
        register_generated_programs(
            self.db,
            engine.semantics,
            records,
            trust_level="trusted",
            capability_registry=trusted_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )

    def ready_spell(self, session, name: str, mana: dict[str, int]):
        engine = session.engine
        source = self.named_card(engine, name)
        engine.move_card(source.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool.update(mana)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(
            row for row in hints["actions"] if row.get("card") == source.ref
        )
        engine._issue_priority("A", hints)
        return source, action

    def add_permanent(self, engine, *, name: str, seat: str, ref: str):
        record = self.db.lookup(name)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=trusted_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_capability_declarations=True,
        )
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

    def resolve_all(self, session):
        for _ in range(96):
            decision = session.state.pending_decision
            if decision is not None and decision.kind != "priority":
                return decision
            if not session.state.stack:
                return None
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Resolution did not stabilize")

    def assert_replays(self, session, label: str) -> None:
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_source_death_trigger_captures_power_and_damage_lki(self):
        session = self.session(40208, card_name="Death Archer")
        engine = session.engine
        self.promote(engine, "Death Archer")
        source = self.named_card(engine, "Death Archer")
        target = self.creature(engine, "B")
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            semantic_events=False,
            log=False,
        )
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="B",
            semantic_events=False,
            log=False,
        )
        source.annotations["continuous_power"] = 4
        target.annotations["continuous_toughness"] = 20
        program = next(
            candidate
            for candidate in engine.semantics.programs_for_oracle(source.oracle_id)
            if candidate.event == "creature.dies.self"
        )
        old_identity = source.logical_object_id
        engine.move_card(
            source.object_id,
            "graveyard",
            reason="creature-power LKI trigger witness",
            semantic_events=True,
        )
        engine._stabilize()
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        snapshot = item.context[CREATURE_POWER_DAMAGE_LKI_CONTEXT]
        self.assertEqual(4, snapshot["power"])
        self.assertEqual(old_identity, snapshot["source"]["logical_object_id"])
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        chosen = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve_all(session))
        self.assertEqual("graveyard", source.zone)
        self.assertEqual(4, target.marked_damage)
        self.assert_replays(session, "creature-power-death-trigger")

    def test_prepared_fight_resolves_in_printed_order(self):
        session = self.session(40207, card_name="Prepared Fight")
        engine = session.engine
        self.promote(engine, "Prepared Fight")
        fighter, opponent = self.stage_creatures(
            session,
            source_power=2,
            target_power=1,
        )
        _spell, action = self.ready_spell(
            session,
            "Prepared Fight",
            {"C": 2, "G": 1},
        )
        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [fighter.ref, opponent.ref],
                "pay": "manual",
                "payment": {"C": 2, "G": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve_all(session))
        self.assertEqual(1, fighter.counters["+1/+1"])
        self.assertEqual(1, fighter.marked_damage)
        self.assertEqual(3, opponent.marked_damage)

    @staticmethod
    def creature(engine, seat: str, *, exclude=()):
        excluded = set(exclude)
        for card in engine.state.cards.values():
            if (
                card.owner == seat
                and card.object_id not in excluded
                and card.zone != "command"
                and card.is_card_object
                and "creature"
                in engine._type_parts(
                    str(engine._effective_card_data(card).get("type_line") or "")
                )[0]
            ):
                return card
        raise AssertionError("No creature fixture is available")

    def stage_creatures(self, session, *, source_power: int, target_power: int):
        engine = session.engine
        source = self.creature(engine, "A")
        target = self.creature(engine, "B")
        engine.move_card(source.object_id, "battlefield", controller="A", log=False)
        engine.move_card(target.object_id, "battlefield", controller="B", log=False)
        source.annotations.update(
            {"continuous_power": source_power, "continuous_toughness": 20}
        )
        target.annotations.update(
            {"continuous_power": target_power, "continuous_toughness": 20}
        )
        source.marked_damage = 0
        target.marked_damage = 0
        return source, target

    @staticmethod
    def effect(kind: str, source: str, target: str, *, lki=None):
        return {
            "op": CREATURE_POWER_DAMAGE_OPERATION,
            "kind": kind,
            "source": source,
            "target": target,
            "target_must_be_creature": kind == "fight",
            "source_lki": lki,
        }

    def test_fight_and_bite_resolve_through_canonical_damage(self):
        session = self.session(40200)
        engine = session.engine
        source, target = self.stage_creatures(
            session,
            source_power=3,
            target_power=2,
        )

        dealt = engine.apply_effect(
            self.effect("fight", source.ref, target.ref),
            actor="A",
        )
        self.assertEqual((3, 2), dealt)
        self.assertEqual(2, source.marked_damage)
        self.assertEqual(3, target.marked_damage)

        source.marked_damage = 0
        target.marked_damage = 0
        dealt = engine.apply_effect(
            self.effect("bite", source.ref, target.ref),
            actor="A",
        )
        self.assertEqual((3,), dealt)
        self.assertEqual(0, source.marked_damage)
        self.assertEqual(3, target.marked_damage)

    def test_fight_requires_both_current_creatures_and_bite_revalidates_targets(self):
        session = self.session(40201)
        engine = session.engine
        source, target = self.stage_creatures(
            session,
            source_power=4,
            target_power=2,
        )
        lki = capture_creature_power_damage_source_lki(
            engine,
            source,
            (self.effect("bite", "$source", target.ref),),
        )
        self.assertIsNotNone(lki)
        engine.move_card(source.object_id, "graveyard", log=False)

        self.assertEqual(
            (),
            engine.apply_effect(
                self.effect("fight", source.ref, target.ref, lki=lki),
                actor="A",
            ),
        )
        self.assertEqual(0, target.marked_damage)
        self.assertEqual(
            (4,),
            engine.apply_effect(
                self.effect("bite", source.ref, target.ref, lki=lki),
                actor="A",
            ),
        )
        self.assertEqual(4, target.marked_damage)

        target.marked_damage = 0
        engine.move_card(source.object_id, "battlefield", controller="A", log=False)
        source.annotations["continuous_power"] = 8
        self.assertEqual(
            (4,),
            engine.apply_effect(
                self.effect("bite", source.ref, target.ref, lki=lki),
                actor="A",
            ),
        )
        self.assertEqual(4, target.marked_damage)
        target.marked_damage = 0
        self.assertEqual(
            (),
            engine.apply_effect(
                self.effect("fight", source.ref, target.ref, lki=lki),
                actor="A",
            ),
        )

        engine.move_card(target.object_id, "graveyard", log=False)
        self.assertEqual(
            (),
            engine.apply_effect(
                self.effect("bite", source.ref, target.ref, lki=lki),
                actor="A",
            ),
        )
        self.assertEqual(0, target.marked_damage)

    def test_self_fight_and_malformed_effects_are_atomic(self):
        session = self.session(40202)
        engine = session.engine
        source, _target = self.stage_creatures(
            session,
            source_power=3,
            target_power=2,
        )
        dealt = engine.apply_effect(
            self.effect("fight", source.ref, source.ref),
            actor="A",
        )
        self.assertEqual((6,), dealt)
        self.assertEqual(6, source.marked_damage)

        for mutation in (
            {**self.effect("fight", source.ref, source.ref), "kind": "duel"},
            {**self.effect("fight", source.ref, source.ref), "unknown": True},
            {**self.effect("fight", source.ref, source.ref), "target_must_be_creature": False},
        ):
            with self.subTest(mutation=mutation):
                before = authoritative_state_hash(engine.state)
                with self.assertRaisesRegex(GameRuleError, "Creature-power"):
                    engine.apply_effect(mutation, actor="A")
                self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_creature_power_damage_offer_commit_and_source_incarnations_are_exact(self):
        session = self.session(40203, card_name="Sacrifice Archer")
        engine = session.engine
        self.promote(engine, "Sacrifice Archer")
        source = self.named_card(engine, "Sacrifice Archer")
        engine.move_card(source.object_id, "battlefield", controller="A", log=False)
        engine.state.players["A"].turns_begun = 1
        source.acquired_control_turn_count = 0
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(
            row
            for row in hints["actions"]
            if row.get("source") == source.ref
            and str(row.get("id") or "").startswith("activate:")
        )
        engine._issue_priority("A", hints)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        life_before = engine.state.players["B"].life
        result = session.act(
            "pilot:A",
            {"action_id": action["id"], "targets": ["B"]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", source.zone)
        self.assertIn(
            CREATURE_POWER_DAMAGE_LKI_CONTEXT,
            engine.state.stack[-1].context,
        )
        self.assertIsNone(self.resolve_all(session))
        self.assertEqual(life_before - 3, engine.state.players["B"].life)
        self.assert_replays(session, "creature-power-sacrifice-activation")

    def test_creature_power_damage_four_player_projection_and_replay(self):
        session = self.session(40204, card_name="Fixed Bite")
        engine = session.engine
        self.promote(engine, "Fixed Bite")
        fighter = self.creature(engine, "A")
        engine.move_card(fighter.object_id, "battlefield", controller="A", log=False)
        fighter.annotations.update(
            {"continuous_power": 3, "continuous_toughness": 20}
        )
        spell, action = self.ready_spell(
            session,
            "Fixed Bite",
            {"C": 1, "R": 1},
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        life_before = engine.state.players["B"].life
        result = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [fighter.ref, "B"],
                "pay": "manual",
                "payment": {"C": 1, "R": 1},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve_all(session))
        self.assertEqual(life_before - 3, engine.state.players["B"].life)
        self.assertEqual("graveyard", spell.zone)
        for seat in engine.seats:
            rendered = json.dumps(session.packet(f"pilot:{seat}", full=True))
            self.assertIn(fighter.ref, rendered)
            self.assertNotIn(fighter.object_id, rendered)
            self.assertNotIn(fighter.logical_object_id, rendered)
        self.assert_replays(session, "creature-power-four-player")

    def test_creature_power_damage_composes_with_lki_prevention_replacement_and_results(self):
        session = self.session(40205)
        engine = session.engine
        source, _target = self.stage_creatures(
            session,
            source_power=3,
            target_power=2,
        )
        source.temporary_keywords.append("Lifelink")
        engine.semantics.put(
            SemanticProgram(
                key="test:creature-power-quantity",
                label="Double creature-power damage",
                oracle_id=source.oracle_id,
                ability_id="static:creature-power-quantity",
                active_zone="battlefield",
                event="damage",
                handlers=[quantity_descriptor(multiplier=2)],
                trust_level="provisional",
            )
        )
        source_life = engine.state.players["A"].life
        target_life = engine.state.players["B"].life
        with patch.object(
            type(engine),
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            replaced = engine.apply_effect(
                self.effect("bite", source.ref, "B"),
                actor="A",
            )
        self.assertEqual((6,), replaced)
        self.assertEqual(target_life - 6, engine.state.players["B"].life)
        self.assertEqual(source_life + 6, engine.state.players["A"].life)
        engine.semantics.remove("test:creature-power-quantity")
        engine.state.players["A"].life = source_life
        engine.state.players["B"].life = target_life
        shield = DamagePreventionShield(
            shield_id="creature-power-shield",
            source_id="effect:creature-power-shield",
            controller="B",
            subject=DamageSubject(ref="B", kind="player", controller="B"),
            mode=PreventionMode.AMOUNT,
            remaining=2,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=engine.state.turn_sequence,
            damage_kind=PreventionDamageKind.ANY,
            recipient_kind=PreventionRecipientKind.ANY,
            scope=DamagePreventionScope(),
            label="Creature-power prevention",
        )
        engine.state.damage_prevention_shields.append(shield)
        prevented = engine.apply_effect(
            self.effect("bite", source.ref, "B"),
            actor="A",
        )
        self.assertEqual((1,), prevented)
        self.assertEqual(target_life - 1, engine.state.players["B"].life)
        self.assertEqual(source_life + 1, engine.state.players["A"].life)
        self.assertEqual([], engine.state.damage_prevention_shields)

    def test_creature_power_damage_replacement_choice_save_load_and_replay(self):
        session = self.session(40206, card_name="Fixed Bite")
        engine = session.engine
        self.promote(engine, "Fixed Bite")
        fighter = self.creature(engine, "A")
        engine.move_card(fighter.object_id, "battlefield", controller="A", log=False)
        fighter.annotations.update(
            {"continuous_power": 3, "continuous_toughness": 20}
       
        )
        self.add_permanent(
            engine,
            name="Furnace of Rath",
            seat="A",
            ref="A-FURNACE-ONE",
        )
        self.add_permanent(
            engine,
            name="Furnace of Rath",
            seat="A",
            ref="A-FURNACE-TWO",
        )
        _spell, action = self.ready_spell(
            session,
            "Fixed Bite",
            {"C": 1, "R": 1},
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        life_before = engine.state.players["B"].life
        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [fighter.ref, "B"],
                "pay": "manual",
                "payment": {"C": 1, "R": 1},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        decision = self.resolve_all(session)
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(life_before, engine.state.players["B"].life)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "creature-power-replacement"
            session.save(record_dir)
            loaded = CommanderSession.load(self.db, record_dir)
            packet = loaded.packet("pilot:B", full=True)
            options = packet["decision"]["ctx"]["options"]
            self.assertEqual(2, len(options))
            selected = options[0]["id"]
            choice = loaded.act(
                "pilot:B",
                {
                    "action_id": "choose",
                    "choices": {"replacement": selected},
                },
            )
            self.assertTrue(choice.ok, choice.summary)
            self.assertIsNone(self.resolve_all(loaded))
            self.assertEqual(
                life_before - 12,
                loaded.state.players["B"].life,
            )
            loaded.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
