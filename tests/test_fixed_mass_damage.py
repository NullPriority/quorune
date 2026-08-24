from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import DB_PATH, keep_all, load_assets, make_session
from damage_replacement_support import DamageReplacementPipelineBase
from quorune.carddb import CardDatabase
from quorune.compiler.damage_templates import (
    FixedMassDamageEffectTemplate,
    fixed_damage_effect_template,
)
from quorune.fixed_damage_set import (
    resolve_fixed_damage_set,
    snapshot_fixed_damage_set,
)
from quorune import fixed_damage_set as fixed_damage_set_module
from quorune.fixed_damage_set_model import (
    FixedDamageSetError,
    FixedDamageSetSpec,
    PermanentControllerRelation,
    PermanentDamageGroup,
    PlayerDamageGroup,
    PlayerDamageRelation,
)
from quorune.model import StackItem
from quorune.object_query import ObjectQueryResult
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
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
from quorune.semantic_runtime import (
    DealFixedDamageSetIntent,
    ReadOnlyHandlerContext,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.fixed_damage_set_handlers import (
    FixedDamageSetHandler,
)


class _SnapshotQuery:
    def __init__(self, rows):
        self.rows = tuple(rows)

    def fixed_damage_active_seats(self):
        return ("A", "B", "C", "D")

    def fixed_damage_apnap_order(self):
        return ("C", "D", "A", "B")

    def fixed_damage_object_rows(self, actor):
        if actor != "A":
            raise AssertionError("Unexpected actor")
        return self.rows


def _row(
    ref: str,
    *,
    controller: str,
    types=("creature",),
    colors=(),
    keywords=(),
    token=False,
    phased_out=False,
):
    return ObjectQueryResult(
        object_id=f"object:{ref}",
        logical_object_id=f"logical:{ref}",
        ref=ref,
        printed_name=ref,
        owner=controller,
        controller=controller,
        zone="battlefield",
        types=types,
        colors=colors,
        keywords=keywords,
        token=token,
        phased_out=phased_out,
    )


class FixedDamageSetModelTests(unittest.TestCase):
    def spec(self):
        return FixedDamageSetSpec(
            (
                PermanentDamageGroup(
                    ObjectQuerySpec(
                        zones=("battlefield",),
                        types_all=("creature",),
                    )
                ),
                PermanentDamageGroup(
                    ObjectQuerySpec(
                        zones=("battlefield",),
                        types_all=("creature",),
                        colors_any=("R",),
                    ),
                    controller_relation=(
                        PermanentControllerRelation.OPPONENTS
                    ),
                ),
                PlayerDamageGroup(PlayerDamageRelation.OPPONENTS),
            )
        )

    def test_fixed_set_snapshot_is_immutable_canonical_and_deduplicated(self):
        spec = self.spec()
        rows = (
            _row("b-red", controller="B", colors=("R",)),
            _row("a-green", controller="A", colors=("G",)),
            _row("c-red", controller="C", colors=("R",)),
            _row("d-artifact", controller="D", types=("artifact",)),
            _row("d-phased", controller="D", phased_out=True),
        )
        first = snapshot_fixed_damage_set(
            _SnapshotQuery(rows), actor="A", spec=spec
        )
        second = snapshot_fixed_damage_set(
            _SnapshotQuery(tuple(reversed(rows))),
            actor="A",
            spec=spec,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(
            ["c-red", "C", "D", "a-green", "b-red", "B"],
            [recipient.ref for recipient in first.recipients],
        )
        self.assertEqual(
            len(first.recipients),
            len({recipient.identity for recipient in first.recipients}),
        )
        serialized = spec.to_dict()
        serialized["groups"][0]["query"]["types_all"].append("artifact")
        self.assertEqual(("creature",), spec.groups[0].query.types_all)

    def test_fixed_set_handler_rejects_malformed_or_unrepresented_groups(self):
        context = ReadOnlyHandlerContext.from_sequences(
            actor="A",
            default_reason="fixture",
            seats=("A", "B", "C", "D"),
            active_seats=("A", "B", "C", "D"),
            apnap_order=("A", "B", "C", "D"),
        )
        valid = fixed_damage_effect_template(
            "Fixture deals 2 damage to each creature.",
            card_name="Fixture",
        )
        self.assertIsInstance(valid, FixedMassDamageEffectTemplate)
        plan = FixedDamageSetHandler().lower(valid.effects[0], context)
        self.assertIsInstance(plan.intents[0], DealFixedDamageSetIntent)
        malformed = (
            {**valid.effects[0], "amount": True},
            {**valid.effects[0], "unknown": 1},
            {
                **valid.effects[0],
                "groups": [
                    {
                        "kind": "permanents",
                        "controller_relation": "any",
                        "target_controller": None,
                        "query": ObjectQuerySpec(
                            zones=("battlefield",),
                            types_all=("artifact",),
                        ).canonical_dict(),
                    }
                ],
            },
        )
        for effect in malformed:
            with self.subTest(effect=effect):
                with self.assertRaises(SemanticNodeError):
                    FixedDamageSetHandler().lower(effect, context)

    def test_fixed_set_snapshot_order_and_predicate_mutants_are_killed(self):
        rows = (
            _row("b-creature", controller="B"),
            _row("c-creature", controller="C"),
            _row("d-land", controller="D", types=("land",)),
        )

        def assert_snapshot() -> None:
            snapshot = snapshot_fixed_damage_set(
                _SnapshotQuery(rows), actor="A", spec=self.spec()
            )
            self.assertEqual(
                ["c-creature", "C", "D", "b-creature", "B"],
                [recipient.ref for recipient in snapshot.recipients],
            )
            self.assertNotIn(
                "d-land", [recipient.ref for recipient in snapshot.recipients]
            )

        assert_snapshot()
        with patch.object(
            fixed_damage_set_module,
            "select_affected_permanents",
            lambda candidates, _spec, **_kwargs: tuple(candidates),
        ):
            with self.assertRaises(AssertionError):
                assert_snapshot()


class FixedMassDamageCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.base = cls.db.lookup("Lightning Bolt")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def compile(self, text, *, type_line="Sorcery"):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=text,
                type_line=type_line,
                keywords=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_closed_mass_damage_grammar_lowers_typed_queries_and_spans(self):
        cases = (
            "each creature",
            "each player",
            "each creature and each player",
            "each creature and each planeswalker",
            "each opponent and each creature and planeswalker they control",
            "each creature your opponents control",
            "each creature with flying",
            "each white and/or blue creature",
            "each nonartifact creature",
            "each nontoken creature",
            "each creature with shadow",
        )
        for phrase in cases:
            text = f"Fixture deals 2 damage to {phrase}."
            with self.subTest(phrase=phrase):
                ir = self.compile(text)
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(
                    "damage-fixed-simultaneous-set-v1", node.template_id
                )
                self.assertEqual("damage_fixed_set", node.effects[0]["op"])
                self.assertIn(
                    "damage.batch.fixed_set", node.capability_dependencies
                )
                self.assertEqual(
                    text,
                    ir.faces[0].oracle_text[node.span.start : node.span.end],
                )

    def test_target_opponent_controlled_set_uses_exact_target_schema(self):
        text = "Fixture deals 2 damage to each creature target opponent controls."
        ir = self.compile(text)
        node = ir.faces[0].nodes[0]
        self.assertEqual("exact", ir.status)
        self.assertEqual(
            {
                "zones": ["player"],
                "categories": ["player"],
                "player_relation": "opponent",
                "count": 1,
            },
            node.target_schema,
        )
        self.assertIn("target.revalidate_resolution", node.capability_dependencies)

    def test_activated_fixed_damage_set_uses_the_shared_capability_gate(self):
        text = "{T}: Fixture deals 1 damage to each player."
        ir = self.compile(text, type_line="Creature — Test")
        node = ir.faces[0].nodes[0]

        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertTrue(node.exact)
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual(
            "damage-fixed-simultaneous-set-v1",
            node.template_id,
        )
        self.assertTrue(
            {
                "damage.amount.positive",
                "damage.batch.fixed_set",
                "damage.result.player_life",
            }.issubset(node.capability_dependencies)
        )

    def test_unsupported_mass_damage_wording_remains_residual(self):
        self.assertIsNone(
            fixed_damage_effect_template(
                "Fixture deals 2 damage to each creature and you gain 2 life.",
                card_name="Fixture",
            )
        )
        variants = (
            "each creature without flying",
            "each non-Pirate creature",
            "each Dragon creature",
            "each attacking creature",
            "each creature equal to the number of Mountains you control",
            "each of up to two target creatures",
        )
        for recipient in variants:
            text = f"Fixture deals 2 damage to {recipient}."
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_mass_damage_capability_shape_fails_closed(self):
        template = fixed_damage_effect_template(
            "Fixture deals 2 damage to each creature and each player.",
            card_name="Fixture",
        )
        self.assertIsInstance(template, FixedMassDamageEffectTemplate)
        dependencies = capability_dependencies_for_node(
            effects=template.effects,
            target_schema=template.target_schema,
            mechanic_ids=template.mechanics,
        )
        self.assertEqual(
            {
                "damage.amount.positive",
                "damage.batch.fixed_set",
                "damage.result.multitype_permanent",
                "damage.result.player_life",
            },
            set(dependencies),
        )
        for effect in (
            {**template.effects[0], "amount": False},
            {**template.effects[0], "unknown": 1},
            {**template.effects[0], "groups": []},
        ):
            with self.subTest(effect=effect):
                self.assertNotIn(
                    "damage.batch.fixed_set",
                    capability_dependencies_for_node(
                        effects=(effect,),
                        target_schema=None,
                        mechanic_ids=template.mechanics,
                    ),
                )


class FixedMassDamageRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session_with_card(self, card_name, *, players, seed):
        deck = copy.deepcopy(self.mishra)
        next(entry for entry in deck.entries if entry.board == "mainboard").name = (
            card_name
        )
        session = make_session(
            self.db,
            deck,
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

    @staticmethod
    def stage_spell(session, source, program, *, targets=()):
        engine = session.engine
        engine._remove_from_zone(source)
        engine._reset_zone_change(source, "stack")
        source.zone = "stack"
        source.controller = "A"
        engine.state.stack.append(
            StackItem(
                stack_id=f"stack:{source.ref}",
                ref=f"S-{source.ref}",
                kind="spell",
                controller="A",
                label=source.printed_name,
                card_object_id=source.object_id,
                source_object_id=source.object_id,
                semantic_key=program.key,
                targets=list(targets),
                default_destination="graveyard",
                visibility=list(engine.seats),
                context={"targets_chosen_at_creation": True},
            )
        )
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

    @staticmethod
    def pass_stack(session):
        while session.state.stack:
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)

    def fixture_creature(self, engine, seat):
        card = next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and (record := engine.card_record(card)) is not None
            and "creature" in record.type_line.casefold()
            and not card.is_commander
        )
        engine.move_card(card.object_id, "battlefield", controller=seat, log=False)
        card.counters["+1/+1"] = 10
        return card

    def test_compiled_mass_damage_resolves_one_canonical_batch(self):
        session = self.session_with_card("Pyroclasm", players=4, seed=12013001)
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Pyroclasm"
        )
        creatures = [self.fixture_creature(engine, seat) for seat in engine.seats]
        hidden_refs = {
            engine.state.cards[object_id].ref
            for object_id in engine.state.players["B"].zones["hand"]
        }
        program = next(
            candidate
            for candidate in engine.semantics.programs_for_oracle(
                source.oracle_id
            )
            if candidate.event == "resolve"
        )
        self.assertEqual("trusted", program.trust_level)
        self.assertFalse(program.requires_arbiter)
        self.stage_spell(session, source, program)
        self.pass_stack(session)
        self.assertEqual([2, 2, 2, 2], [card.marked_damage for card in creatures])
        aggregate = [
            event
            for event in engine.state.events
            if event.code == "effect.damage.fixed_set"
        ]
        self.assertEqual(1, len(aggregate))
        self.assertEqual(4, aggregate[0].details["recipient_count"])
        self.assertEqual(4, len(aggregate[0].details["damage_events"]))
        self.assertEqual(40, engine.state.players["A"].life)
        projected = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:A"),
            sort_keys=True,
        )
        self.assertTrue(hidden_refs)
        self.assertTrue(all(ref not in projected for ref in hidden_refs))

    def test_targeted_set_offer_and_resolution_share_controller_scope(self):
        session = self.session_with_card("Simoon", players=4, seed=12013005)
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Simoon"
        )
        target_creature = self.fixture_creature(engine, "B")
        other_creature = self.fixture_creature(engine, "C")
        program = next(
            candidate
            for candidate in engine.semantics.programs_for_oracle(source.oracle_id)
            if candidate.event == "resolve"
        )
        public_schema = engine._public_target_schema(
            "A",
            program.target_schema,
            source_ref=source.ref,
        )
        self.assertEqual({"B", "C", "D"}, set(public_schema["legal_refs"]))
        self.stage_spell(session, source, program, targets=("B",))
        self.pass_stack(session)
        self.assertEqual(1, target_creature.marked_damage)
        self.assertEqual(0, other_creature.marked_damage)

    def test_compiled_mass_damage_replays_exactly(self):
        session = self.session_with_card("Pyroclasm", players=4, seed=12013002)
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Pyroclasm"
        )
        for seat in engine.seats:
            self.fixture_creature(engine, seat)
        program = next(
            candidate
            for candidate in engine.semantics.programs_for_oracle(source.oracle_id)
            if candidate.event == "resolve"
        )
        self.stage_spell(session, source, program)
        self.pass_stack(session)
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-mass-damage"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_fixed_set_damage_projects_no_hidden_zone_cards(self):
        session = self.session_with_card("Pyroclasm", players=4, seed=12013003)
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Pyroclasm"
        )
        self.fixture_creature(engine, "B")
        hidden = {
            engine.state.cards[object_id].ref
            for object_id in engine.state.players["B"].zones["hand"]
        }
        program = next(
            candidate
            for candidate in engine.semantics.programs_for_oracle(source.oracle_id)
            if candidate.event == "resolve"
        )
        self.stage_spell(session, source, program)
        self.pass_stack(session)
        packet = json.dumps(session.packet("pilot:A", full=True), sort_keys=True)
        self.assertTrue(hidden)
        self.assertTrue(all(ref not in packet for ref in hidden))


class FixedMassDamageInteractionTests(DamageReplacementPipelineBase):
    def test_fixed_set_damage_uses_effective_types_and_replacement_order(self):
        session = self.session(12013004, players=4)
        engine = session.engine
        self.add_permanent(
            engine, seat="A", name="Furnace of Rath", ref="a-furnace"
        )
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        first = self.add_permanent(
            engine, seat="B", name="Scute Swarm", ref="b-creature"
        )
        second = self.add_permanent(
            engine, seat="C", name="Scute Swarm", ref="c-creature"
        )
        first.counters["+1/+1"] = 10
        second.counters["+1/+1"] = 10
        spec = FixedDamageSetSpec(
            (
                PermanentDamageGroup(
                    ObjectQuerySpec(
                        zones=("battlefield",), types_all=("creature",)
                    )
                ),
            )
        )
        result = resolve_fixed_damage_set(
            engine,
            actor="A",
            source_ref=source.ref,
            amount=2,
            spec=spec,
            reason="fixed mass damage interaction",
        )
        self.assertEqual(12, result.dealt_amount)
        self.assertEqual(4, first.marked_damage)
        self.assertEqual(4, second.marked_damage)


if __name__ == "__main__":
    unittest.main()
