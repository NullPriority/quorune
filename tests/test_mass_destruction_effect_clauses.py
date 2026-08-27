from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT, keep_all, load_assets, make_session
from quorune.affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    PermanentControllerRelation,
    select_affected_permanents,
)
from quorune.carddb import CardDatabase
from quorune.compiler.destruction_templates import (
    MassDestructionEffectTemplate,
    mass_destruction_effect_template,
)
from quorune.destruction import (
    commit_destruction_plan,
    DestructionCause,
    DestructionError,
    prepare_destructions,
    request_for_card,
)
from quorune.destruction_sets import (
    resolve_destruction_set,
    snapshot_destruction_set,
)
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantic_runtime import (
    DestroyPermanentSetIntent,
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.destruction_handlers import (
    DestroyPermanentSetHandler,
)


def row(
    ref: str,
    controller: str,
    *,
    card_types: tuple[str, ...] = ("creature",),
    logical_id: str | None = None,
) -> ObjectQueryResult:
    suffix = ref.casefold()
    return ObjectQueryResult(
        object_id=f"object-{suffix}",
        logical_object_id=logical_id or f"logical-{suffix}",
        ref=ref,
        printed_name=f"Fixture {ref}",
        owner=controller,
        controller=controller,
        zone="battlefield",
        types=card_types,
    )


class MassDestructionModelTests(unittest.TestCase):
    def test_set_descriptor_is_closed_canonical_and_copy_isolated(self):
        raw = ObjectQuerySpec(
            zones=("battlefield",),
            types_any=("creature", "artifact"),
            colors_any=("R",),
            tapped=True,
        ).canonical_dict()
        descriptor = {
            "controller_relation": "opponents",
            "target_controller": None,
            "exclude_source": True,
            "query": raw,
        }
        spec = AffectedPermanentSetSpec.from_dict(descriptor)
        fingerprint = spec.fingerprint
        descriptor["query"]["types_any"].append("land")
        descriptor["controller_relation"] = "any"

        self.assertEqual(
            ("artifact", "creature"),
            spec.query.types_any,
        )
        self.assertEqual(PermanentControllerRelation.OPPONENTS, spec.controller_relation)
        self.assertEqual(fingerprint, spec.fingerprint)
        self.assertEqual(spec, AffectedPermanentSetSpec.from_dict(spec.to_dict()))
        with self.assertRaises(AffectedPermanentSetError):
            AffectedPermanentSetSpec.from_dict(
                {**spec.to_dict(), "arbitrary": "open grammar"}
            )

    def test_apnap_selection_is_stable_unique_and_source_aware(self):
        spec = AffectedPermanentSetSpec(
            query=ObjectQuerySpec(
                zones=("battlefield",),
                types_all=("artifact",),
            ),
            controller_relation=PermanentControllerRelation.OPPONENTS,
            exclude_source=True,
        )
        rows = (
            row("A-source", "A", card_types=("artifact",)),
            row("D-one", "D", card_types=("artifact",)),
            row("B-one", "B", card_types=("artifact",)),
            row("C-creature", "C"),
            row("C-one", "C", card_types=("artifact",)),
        )
        expected = ("B-one", "C-one", "D-one")
        for candidate in (rows, tuple(reversed(rows))):
            selected = select_affected_permanents(
                candidate,
                spec,
                actor="A",
                active_seats=("A", "B", "C", "D"),
                apnap_order=("B", "C", "D", "A"),
                source_ref="A-source",
            )
            self.assertEqual(expected, tuple(value.ref for value in selected))

    def test_mass_destruction_selection_mutant_is_killed(self):
        opponents = AffectedPermanentSetSpec(
            query=ObjectQuerySpec(zones=("battlefield",)),
            controller_relation=PermanentControllerRelation.OPPONENTS,
        )
        selected = select_affected_permanents(
            (row("A-own", "A"), row("B-opp", "B")),
            opponents,
            actor="A",
            active_seats=("A", "B"),
            apnap_order=("A", "B"),
        )
        self.assertEqual(("B-opp",), tuple(value.ref for value in selected))
        self.assertNotIn("A-own", {value.ref for value in selected})


class MassDestructionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _, _ = load_assets()
        cls.base = cls.db.lookup("Lightning Greaves")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def compile(self, oracle_text: str, *, type_line: str = "Sorcery"):
        return compile_oracle_card(
            replace(
                self.base,
                name="Mass Destruction Fixture",
                oracle_text=oracle_text,
                type_line=type_line,
                keywords=(),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_closed_parser_covers_reusable_fixed_set_predicates(self):
        accepted = {
            "Destroy all creatures.": {"types_all": ["creature"]},
            "Destroy all nonland permanents.": {"excluded_types": ["land"]},
            "Destroy all artifacts, creatures, and enchantments.": {
                "types_any": ["artifact", "creature", "enchantment"]
            },
            "Destroy all legendary creatures.": {
                "supertypes_all": ["legendary"]
            },
            "Destroy all tapped creatures.": {"tapped": True},
            "Destroy all nontoken creatures.": {"token": False},
            "Destroy all red creatures.": {"colors_any": ["R"]},
            "Destroy all creatures with flying.": {"keywords_all": ["flying"]},
            "Destroy all other creatures.": {"exclude_source": True},
            "Destroy all artifacts your opponents control.": {
                "controller_relation": "opponents"
            },
        }
        for text, expected in accepted.items():
            with self.subTest(text=text):
                template = mass_destruction_effect_template(text)
                self.assertIsNotNone(template)
                assert template is not None
                serialized = template.spec.to_dict()
                query = serialized["query"]
                for field, value in expected.items():
                    self.assertEqual(
                        value,
                        serialized.get(field, query.get(field)),
                    )

        targeted = mass_destruction_effect_template(
            "Destroy all creatures target opponent controls."
        )
        self.assertIsNotNone(targeted)
        assert targeted is not None
        self.assertEqual("$target.0", targeted.spec.target_controller)
        self.assertEqual("opponent", targeted.target_schema["player_relation"])

        prohibited = mass_destruction_effect_template(
            "Destroy all creatures. They can't be regenerated."
        )
        self.assertIsNotNone(prohibited)
        assert prohibited is not None
        self.assertTrue(prohibited.regeneration_prohibited)
        self.assertTrue(prohibited.effects[0]["regeneration_prohibited"])

    def test_spell_trigger_and_activated_contexts_share_mass_destruction_lowering(self):
        contexts = (
            ("Destroy all creatures.", "Sorcery", "spell_ability"),
            (
                "When this creature enters, destroy all creatures.",
                "Creature — Test",
                "triggered_ability",
            ),
            (
                "{2}{B}, {T}: Destroy all creatures.",
                "Creature — Test",
                "activated_ability",
            ),
        )
        for text, type_line, kind in contexts:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertTrue(node.exact)
                self.assertIn(
                    "permanent.destroy.fixed_set",
                    node.capability_dependencies,
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_unsupported_mass_destruction_variants_remain_residuals(self):
        unsupported = (
            "Destroy all creatures with mana value 3 or less.",
            "Destroy all nonwhite creatures.",
            "Destroy all non-Human creatures.",
            "Destroy all creatures that attacked this turn.",
            "Destroy all creatures with counters on them.",
            "Destroy all creatures. Draw a card for each creature destroyed this way.",
            "Choose a creature type. Destroy all creatures that aren't of that type.",
            "At the beginning of the next end step, destroy all creatures.",
            "Destroy X target creatures.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(mass_destruction_effect_template(text))
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_mass_destruction_shape_mutants_fail_closed(self):
        template = mass_destruction_effect_template(
            "Destroy all artifacts your opponents control."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(
            ("permanent.destroy.fixed_set",),
            capability_dependencies_for_node(
                effects=template.effects,
                target_schema=template.target_schema,
                mechanic_ids=template.mechanics,
            ),
        )
        effect = dict(template.effects[0])
        malformed = (
            ({**effect, "op": "destroy"}, template.target_schema),
            ({**effect, "source": "$target.0"}, template.target_schema),
            ({**effect, "extra": True}, template.target_schema),
            ({**effect, "set": {**effect["set"], "exclude_source": "yes"}}, None),
            (effect, {"zones": ["battlefield"]}),
        )
        for effects, schema in malformed:
            with self.subTest(effects=effects, schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=(effects,),
                        target_schema=schema,
                        mechanic_ids=template.mechanics,
                    )
                )

    def test_handler_lowers_only_the_strict_typed_set(self):
        template = mass_destruction_effect_template("Destroy all creatures.")
        self.assertIsNotNone(template)
        assert template is not None
        context = ReadOnlyHandlerContext(
            actor="A",
            default_reason="mass destruction fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=("A", "B", "C", "D"),
                apnap_order=("B", "C", "D", "A"),
            ),
        )
        plan = DestroyPermanentSetHandler().lower(template.effects[0], context)
        self.assertEqual("generic.destroy-permanent-set.v1", plan.handler_id)
        self.assertEqual(1, len(plan.intents))
        self.assertIsInstance(plan.intents[0], DestroyPermanentSetIntent)
        for malformed in (
            {"op": "destroy_all", "source": "$source"},
            {**template.effects[0], "source": True},
            {**template.effects[0], "arbitrary": True},
        ):
            with self.subTest(effect=malformed):
                with self.assertRaises(SemanticNodeError):
                    DestroyPermanentSetHandler().lower(malformed, context)


class MassDestructionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int):
        session = make_session(
            self.db,
            copy.deepcopy(self.mishra),
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
    def card(engine, seat: str, name: str):
        return next(
            value
            for value in engine.state.cards.values()
            if value.owner == seat and value.printed_name == name
        )

    @staticmethod
    def put(engine, card):
        engine.move_card(
            card.object_id,
            "battlefield",
            controller=card.owner,
            tapped=False,
            log=False,
        )
        return card

    @staticmethod
    def pass_stack(session):
        while session.state.stack:
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Stack resolution stopped without priority")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)

    def test_mass_destruction_apnap_shield_indestructible_and_replay(self):
        session = self.session(7013101)
        engine = session.engine
        blast = self.card(engine, "A", "Vandalblast")
        ordinary = self.put(engine, self.card(engine, "B", "Lightning Greaves"))
        shielded = self.put(engine, self.card(engine, "D", "Lightning Greaves"))
        shielded.counters["shield"] = 1
        indestructible = self.put(engine, self.card(engine, "C", "Darksteel Citadel"))
        own = self.put(engine, self.card(engine, "A", "Sensei's Divining Top"))
        engine.move_card(blast.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool.update({"C": 4, "R": 1})
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(row for row in hints["actions"] if row.get("card") == blast.ref)
        self.assertIn(
            "overload",
            {option["id"] for option in action["cost_options"]},
        )
        engine._issue_priority("A", hints)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "cost_option": "overload",
                "targets": [],
                "pay": "manual",
                "payment": {"C": 4, "R": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_stack(session)

        self.assertEqual("graveyard", ordinary.zone)
        self.assertEqual("battlefield", shielded.zone)
        self.assertNotIn("shield", shielded.counters)
        self.assertEqual("battlefield", indestructible.zone)
        self.assertEqual("battlefield", own.zone)
        self.assertIn(
            "effect.permanent.destroy_set",
            {event.code for event in engine.state.events},
        )
        for seat in ("A", "B", "C", "D"):
            projected = session.projector._snapshot(f"pilot:{seat}")
            rendered = json.dumps(projected, sort_keys=True)
            self.assertNotIn("logical_object_id", rendered)

        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "mass-destruction-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_mass_destruction_uses_typed_query_and_source_exclusion(self):
        session = self.session(7013102)
        engine = session.engine
        source = self.put(engine, self.card(engine, "A", "Lightning Greaves"))
        opponent = self.put(engine, self.card(engine, "B", "Sol Ring"))
        creature = self.put(engine, self.card(engine, "D", "Birds of Paradise"))
        spec = AffectedPermanentSetSpec(
            query=ObjectQuerySpec(
                zones=("battlefield",),
                types_all=("artifact",),
            ),
            exclude_source=True,
        )
        snapshot = snapshot_destruction_set(
            engine,
            actor="A",
            spec=spec,
            source_ref=source.ref,
        )
        self.assertEqual((opponent.ref,), tuple(value.ref for value in snapshot.permanents))

        result = resolve_destruction_set(
            engine,
            actor="A",
            spec=spec,
            source_ref=source.ref,
            reason="typed source-excluding fixture",
        )
        self.assertEqual((opponent.object_id,), result.destroyed_object_ids)
        self.assertEqual("battlefield", source.zone)
        self.assertEqual("battlefield", creature.zone)

    def test_mass_destruction_stale_snapshot_rolls_back(self):
        session = self.session(7013103)
        engine = session.engine
        first = self.put(engine, self.card(engine, "B", "Lightning Greaves"))
        second = self.put(engine, self.card(engine, "C", "Lightning Greaves"))
        plan = prepare_destructions(
            engine,
            (request_for_card(first), request_for_card(second)),
            cause=DestructionCause.EFFECT,
            actor="A",
            reason="stale mass destruction fixture",
            event_order=(first.object_id, second.object_id),
        )
        second.counters["shield"] = 1
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(DestructionError, "stale"):
            commit_destruction_plan(engine, plan)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("battlefield", first.zone)
        self.assertEqual("battlefield", second.zone)


if __name__ == "__main__":
    unittest.main()
