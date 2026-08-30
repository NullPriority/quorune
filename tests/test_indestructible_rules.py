from __future__ import annotations

import json
import unittest

from common import DB_PATH, ROOT, keep_all, load_assets, make_session
from quorune.card_programs.adapters import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.destruction import (
    commit_destruction_plan,
    DestructionCause,
    DestructionDisposition,
    DestructionError,
    destroy_permanent_refs,
    prepare_destructions,
    request_for_card,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.permanent_exile import exile_permanent
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.state_based_actions import (
    PermanentSnapshot,
    evaluate_permanent_state_based_actions,
)


INDESTRUCTIBLE_CAPABILITY = "permanent.indestructible.ordinary"
STATE_BASED_DESTRUCTION_CAPABILITY = "permanent.destroy.state_based_damage"
REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def indestructible_card(
    text: str,
    *,
    keywords: tuple[str, ...] = ("Indestructible",),
    type_line: str = "Artifact Creature — Golem",
    suffix: int = 702_012_001,
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name="Generic Indestructible Fixture",
        mana_cost="{3}",
        mana_value=3.0,
        type_line=type_line,
        oracle_text=text,
        power="3",
        toughness="3",
        loyalty=None,
        defense=None,
        colors=(),
        color_identity=(),
        keywords=keywords,
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class IndestructibleCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_plain_indestructible_compiles_to_closed_capability(self):
        text = "Indestructible"
        program = compile_card_program(
            self.db,
            indestructible_card(text),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        self.assertEqual(
            (INDESTRUCTIBLE_CAPABILITY,),
            program.capability_dependencies,
        )
        self.assertEqual(
            "capability_closed",
            program.trust_closure["trust_basis"],
        )
        self.assertTrue(program.trust_closure["trusted"])
        ability = program.to_dict()["abilities"][0]
        self.assertEqual(
            {"line": 1, "start": 0, "end": len(text)},
            ability["source_span"],
        )
        self.assertEqual([], program.to_dict()["residuals"])

        repeated = compile_oracle_card(
            indestructible_card(
                "Indestructible, indestructible",
                suffix=702_012_002,
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", repeated.status)
        self.assertEqual(
            ("indestructible", "indestructible"),
            repeated.faces[0].nodes[0].mechanics,
        )
        self.assertEqual(
            (INDESTRUCTIBLE_CAPABILITY,),
            repeated.faces[0].nodes[0].capability_dependencies,
        )

        granted = compile_oracle_card(
            indestructible_card(
                "Target creature gains indestructible until end of turn. "
                "Put a +1/+1 counter on it.",
                keywords=(),
                type_line="Instant",
                suffix=702_012_003,
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", granted.status)
        self.assertIn(
            INDESTRUCTIBLE_CAPABILITY,
            granted.faces[0].nodes[0].capability_dependencies,
        )

    def test_unsupported_indestructible_variants_remain_residuals(self):
        for suffix, text in enumerate(
            (
                "Indestructible from black",
                "You have indestructible.",
            ),
            start=702_012_100,
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    indestructible_card(text, suffix=suffix),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_indestructible_dependencies_fail_closed(self):
        dependency_ids = (
            "combat.damage.assignment.trample",
            "permanent.destroy.effect",
            "permanent.destroy.fixed_set",
            STATE_BASED_DESTRUCTION_CAPABILITY,
            "damage.result.creature_mark",
            "damage.result.deathtouch",
        )
        for dependency_id in dependency_ids:
            with self.subTest(dependency_id=dependency_id):
                value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                dependency = next(
                    row
                    for row in value["capabilities"]
                    if row["id"] == dependency_id
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = ["dependency mutation"]
                registry = CapabilityRegistry(value)

                closure = registry.closure(
                    [INDESTRUCTIBLE_CAPABILITY],
                    profile="commander_review",
                )
                self.assertFalse(closure.trusted)
                self.assertTrue(
                    any(dependency_id in blocker for blocker in closure.blockers)
                )
                ir = compile_oracle_card(
                    indestructible_card(
                        "Indestructible",
                        suffix=702_012_200,
                    ),
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)


class IndestructibleRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

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

    @staticmethod
    def token(engine, controller: str, name: str, *, toughness: int = 3):
        ref = engine.create_token(
            controller,
            name=name,
            characteristics={
                "type_line": "Token Artifact Creature — Test",
                "power": "3",
                "toughness": str(toughness),
                "keywords": [],
            },
        )[0]
        return engine._resolve_object(
            controller,
            ref,
            zones={"battlefield"},
        )

    def test_current_indestructible_controls_effect_and_state_based_destruction(self):
        session = self.session(702_012_301)
        engine = session.engine
        target = self.token(engine, "B", "Current keyword witness")
        target.temporary_keywords.append("INDESTRUCTIBLE")

        effect_plan = prepare_destructions(
            engine,
            (request_for_card(target),),
            cause=DestructionCause.EFFECT,
            actor="A",
            reason="effect witness",
        )
        state_plan = prepare_destructions(
            engine,
            (request_for_card(target),),
            cause=DestructionCause.STATE_BASED_ACTION,
            actor=None,
            reason="state-based witness",
        )
        self.assertEqual(
            DestructionDisposition.INDESTRUCTIBLE,
            effect_plan.entries[0].disposition,
        )
        self.assertEqual(
            DestructionDisposition.INDESTRUCTIBLE,
            state_plan.entries[0].disposition,
        )

        target.temporary_keywords.clear()
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(DestructionError, "stale"):
            commit_destruction_plan(engine, effect_plan)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        target.temporary_keywords.append("Indestructible")
        protected = destroy_permanent_refs(
            engine,
            (target.ref,),
            actor="A",
            reason="current keyword witness",
        )
        self.assertEqual((target.object_id,), protected.indestructible_object_ids)
        target.temporary_keywords.clear()
        destroyed = destroy_permanent_refs(
            engine,
            (target.ref,),
            actor="A",
            reason="lost keyword witness",
        )
        self.assertEqual((target.object_id,), destroyed.destroyed_object_ids)

    def test_state_based_lethal_and_deathtouch_share_typed_destruction(self):
        session = self.session(702_012_302)
        engine = session.engine
        lethal = self.token(engine, "B", "Lethal witness", toughness=3)
        deathtouch = self.token(
            engine,
            "C",
            "Deathtouch witness",
            toughness=8,
        )
        protected = self.token(
            engine,
            "D",
            "Protected lethal witness",
            toughness=2,
        )
        lethal.marked_damage = 3
        deathtouch.deathtouch_damage = True
        protected.marked_damage = 2
        protected.temporary_keywords.append("Indestructible")

        self.assertFalse(engine._stabilize())

        self.assertEqual("outside", lethal.zone)
        self.assertEqual("outside", deathtouch.zone)
        self.assertEqual("battlefield", protected.zone)
        destroyed = {
            event.details["object"]
            for event in engine.state.events
            if event.code == "permanent.destroyed"
        }
        self.assertEqual({lethal.ref, deathtouch.ref}, destroyed)

    def test_indestructible_is_destroy_only_and_malformed_keywords_fail_closed(self):
        batch = evaluate_permanent_state_based_actions(
            (
                PermanentSnapshot(
                    object_id="zero-toughness",
                    card_types=frozenset({"creature"}),
                    toughness=0,
                    indestructible=True,
                ),
            )
        )
        self.assertEqual(("zero-toughness",), batch.put_in_graveyard)
        self.assertEqual((), batch.destroy)

        session = self.session(702_012_303)
        engine = session.engine
        exiled = self.token(engine, "B", "Exile witness")
        exiled.temporary_keywords.append("Indestructible")
        result = exile_permanent(
            engine,
            exiled.ref,
            actor="A",
            reason="nondestruction witness",
        )
        self.assertTrue(result.exiled)

        malformed = self.token(engine, "C", "Malformed keyword witness")
        malformed.temporary_keywords.append(7)  # type: ignore[arg-type]
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(DestructionError, "keywords"):
            prepare_destructions(
                engine,
                (request_for_card(malformed),),
                cause=DestructionCause.EFFECT,
                actor="A",
                reason="malformed keyword witness",
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))


if __name__ == "__main__":
    unittest.main()
