from __future__ import annotations

from pathlib import Path
import random
import tempfile
import unittest

from damage_replacement_support import DamageReplacementPipelineBase
from inherited_composition_fixture import InheritedNativeFixture
from quorune.damage import (
    commit_prepared_damage_batch,
    prepare_damage_batch,
    resolve_damage_batch,
    source_snapshot,
)
from quorune.damage_modifier_state import (
    DamageAftermathRecipient,
    DamageModifierDuration,
    DamagePreventionShield,
    DamageSubject,
    DealDamagePreventionAftermath,
    GainLifePreventionAftermath,
    PlaceCountersPreventionAftermath,
    PreventionMode,
)
from quorune.damage_prevention_aftermath import (
    prevention_applications,
)
from quorune.model import StackItem
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.replacement import ReplaceableEvent
from quorune.semantics import SemanticProgram


class DamagePreventionAftermathTests(DamageReplacementPipelineBase):
    def _damage_aftermath_shield(
        self,
        engine,
        *,
        prevention_source,
        subject: DamageSubject,
        mode: PreventionMode = PreventionMode.NEXT_INSTANCE,
    ) -> DamagePreventionShield:
        return DamagePreventionShield(
            shield_id="damage-aftermath",
            source_id=prevention_source.ref,
            controller=prevention_source.controller,
            subject=subject,
            mode=mode,
            remaining=None,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=engine.state.turn_sequence,
            aftermath=(
                DealDamagePreventionAftermath(
                    source=source_snapshot(
                        engine,
                        prevention_source.ref,
                        controller=prevention_source.controller,
                    ),
                    recipient=DamageAftermathRecipient(
                        kind="prevented_source_controller"
                    ),
                    per_prevented=1,
                ),
            ),
        )

    def test_damage_aftermath_uses_lki_and_projected_consumed_shield(self):
        engine = self.session(615206).engine
        original_source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="original"
        )
        prevention_source = self.add_permanent(
            engine, seat="B", name="Goblin Engineer", ref="palm"
        )
        shield = self._damage_aftermath_shield(
            engine,
            prevention_source=prevention_source,
            subject=DamageSubject(ref="*", kind="any", controller="B"),
        )
        engine.state.damage_prevention_shields.append(shield)
        engine.move_card(prevention_source.object_id, "graveyard", log=False)

        result = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=original_source,
                    target="B",
                    amount=4,
                ),
            ),
        )

        self.assertEqual(40, engine.state.players["B"].life)
        self.assertEqual(36, engine.state.players["A"].life)
        self.assertFalse(engine.state.damage_prevention_shields)
        self.assertEqual(1, len(result.nested_damage_results))
        nested = result.nested_damage_results[0]
        self.assertEqual(4, nested.dealt_amount)
        self.assertEqual("palm", nested.events[0].source)
        self.assertEqual("battlefield", nested.events[0].source_zone)
        self.assertEqual("deal_damage", result.aftermath_events[0].kind)

    def test_other_prevention_applies_to_aftermath_damage(self):
        engine = self.session(615207).engine
        original_source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="original"
        )
        prevention_source = self.add_permanent(
            engine, seat="B", name="Goblin Engineer", ref="palm"
        )
        engine.state.damage_prevention_shields.extend(
            (
                self._damage_aftermath_shield(
                    engine,
                    prevention_source=prevention_source,
                    subject=DamageSubject(
                        ref="B", kind="player", controller="B"
                    ),
                ),
                DamagePreventionShield(
                    shield_id="nested-shield",
                    source_id="fixture:nested-shield",
                    controller="A",
                    subject=DamageSubject(
                        ref="A", kind="player", controller="A"
                    ),
                    mode=PreventionMode.AMOUNT,
                    remaining=2,
                    duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                    created_turn_sequence=engine.state.turn_sequence,
                ),
            )
        )

        result = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=original_source,
                    target="B",
                    amount=4,
                ),
            ),
        )

        self.assertEqual(38, engine.state.players["A"].life)
        self.assertEqual(2, result.nested_damage_results[0].dealt_amount)
        self.assertFalse(engine.state.damage_prevention_shields)

    def test_unpreventable_damage_does_not_create_scaled_damage_aftermath(self):
        engine = self.session(615208).engine
        original_source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="original"
        )
        prevention_source = self.add_permanent(
            engine, seat="B", name="Goblin Engineer", ref="palm"
        )
        engine.state.damage_prevention_shields.append(
            self._damage_aftermath_shield(
                engine,
                prevention_source=prevention_source,
                subject=DamageSubject(
                    ref="B", kind="player", controller="B"
                ),
            )
        )

        result = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=original_source,
                    target="B",
                    amount=4,
                    unpreventable=True,
                ),
            ),
        )

        self.assertEqual(36, engine.state.players["B"].life)
        self.assertEqual(40, engine.state.players["A"].life)
        self.assertEqual((), result.nested_damage_results)
        self.assertEqual(1, len(engine.state.damage_prevention_shields))

    def test_recursive_all_damage_aftermath_fails_before_mutation(self):
        engine = self.session(615209).engine
        original_source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="original"
        )
        prevention_source = self.add_permanent(
            engine, seat="B", name="Goblin Engineer", ref="loop"
        )
        engine.state.damage_prevention_shields.append(
            self._damage_aftermath_shield(
                engine,
                prevention_source=prevention_source,
                subject=DamageSubject(ref="*", kind="any", controller="B"),
                mode=PreventionMode.ALL,
            )
        )
        before = {
            seat: engine.state.players[seat].life for seat in ("A", "B")
        }

        with self.assertRaisesRegex(ValueError, "cycle"):
            prepare_damage_batch(
                engine,
                (
                    self.proposal(
                        engine,
                        source=original_source,
                        target="B",
                        amount=4,
                    ),
                ),
            )

        self.assertEqual(
            before,
            {seat: engine.state.players[seat].life for seat in ("A", "B")},
        )
        self.assertEqual(1, len(engine.state.damage_prevention_shields))

    def test_damage_aftermath_is_separate_in_a_four_player_batch(self):
        engine = self.session(615210, players=4).engine
        source_a = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="source-a"
        )
        source_c = self.add_permanent(
            engine, seat="C", name="Mishra, Eminent One", ref="source-c"
        )
        prevention_source = self.add_permanent(
            engine, seat="B", name="Goblin Engineer", ref="palm"
        )
        engine.state.damage_prevention_shields.append(
            self._damage_aftermath_shield(
                engine,
                prevention_source=prevention_source,
                subject=DamageSubject(
                    ref="B", kind="player", controller="B"
                ),
            )
        )

        result = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=source_a,
                    target="B",
                    amount=3,
                    event_id="damage:four:a-b",
                ),
                self.proposal(
                    engine,
                    source=source_c,
                    target="D",
                    amount=2,
                    event_id="damage:four:c-d",
                ),
            ),
        )

        self.assertEqual(37, engine.state.players["A"].life)
        self.assertEqual(40, engine.state.players["B"].life)
        self.assertEqual(38, engine.state.players["D"].life)
        self.assertEqual(2, len(result.events))
        self.assertEqual(3, result.nested_damage_results[0].dealt_amount)

    def test_damage_aftermath_replays_from_exact_commands(self):
        session = self.session(615513)
        engine = session.engine
        original_source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="original"
        )
        prevention_source = self.add_permanent(
            engine, seat="B", name="Goblin Engineer", ref="palm"
        )
        engine.state.damage_prevention_shields.append(
            self._damage_aftermath_shield(
                engine,
                prevention_source=prevention_source,
                subject=DamageSubject(
                    ref="B", kind="player", controller="B"
                ),
            )
        )
        program = SemanticProgram(
            key="test:prevention-damage-aftermath-replay",
            label="Replay prevention damage aftermath",
            effects=[
                {
                    "op": "damage",
                    "source": "$source",
                    "target": "B",
                    "amount": 2,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="prevention-damage-aftermath-replay",
                ref="S-prevention-damage-aftermath-replay",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                source_object_id=original_source.object_id,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in ("pilot:A", "pilot:B"):
            response = session.act(principal, {"action_id": "pass"})
            self.assertTrue(response.ok, response.summary)
        self.assertEqual(38, engine.state.players["A"].life)
        self.assertEqual(40, engine.state.players["B"].life)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "prevention-damage-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_life_aftermath_uses_one_aggregate_prevented_amount(self):
        engine = self.session(615201, players=4).engine
        source_a = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="source-a"
        )
        source_c = self.add_permanent(
            engine, seat="C", name="Mishra, Eminent One", ref="source-c"
        )
        engine.state.players["B"].life = 30
        shield = DamagePreventionShield(
            shield_id="reverse-damage",
            source_id="fixture:reverse-damage",
            controller="B",
            subject=DamageSubject(ref="B", kind="player", controller="B"),
            mode=PreventionMode.AMOUNT,
            remaining=10,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=engine.state.turn_sequence,
            aftermath=(
                GainLifePreventionAftermath(player="B", per_prevented=1),
            ),
        )
        engine.state.damage_prevention_shields.append(shield)

        result = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=source_a,
                    target="B",
                    amount=2,
                    event_id="damage:aftermath:a",
                ),
                self.proposal(
                    engine,
                    source=source_c,
                    target="B",
                    amount=3,
                    event_id="damage:aftermath:c",
                ),
            ),
        )

        self.assertEqual(35, engine.state.players["B"].life)
        self.assertEqual(1, len(result.aftermath_events))
        self.assertEqual(5, result.aftermath_events[0].prevented_amount)
        self.assertEqual(5, result.aftermath_events[0].applied_amount)
        self.assertEqual("gain_life", result.aftermath_events[0].kind)

    def test_counter_aftermath_reuses_counter_replacement_pipeline(self):
        engine = self.session(615202).engine
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="source"
        )
        target = self.add_permanent(
            engine, seat="B", name="Goblin Engineer", ref="target"
        )
        target.counters["+1/+1"] = 5
        self.add_permanent(
            engine, seat="B", name="Doubling Season", ref="doubling"
        )
        subject = DamageSubject(
            ref=target.ref,
            kind="permanent",
            controller="B",
            object_id=target.object_id,
            logical_object_id=target.logical_object_id,
            owner="B",
        )
        engine.state.damage_prevention_shields.append(
            DamagePreventionShield(
                shield_id="test-of-faith",
                source_id="fixture:test-of-faith",
                controller="B",
                subject=subject,
                mode=PreventionMode.AMOUNT,
                remaining=2,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                created_turn_sequence=engine.state.turn_sequence,
                aftermath=(
                    PlaceCountersPreventionAftermath(
                        subject=subject,
                        counter_name="+1/+1",
                        placing_player="B",
                        per_prevented=1,
                    ),
                ),
            )
        )

        result = resolve_damage_batch(
            engine,
            (self.proposal(engine, source=source, target=target, amount=2),),
        )

        self.assertEqual(9, target.counters["+1/+1"])
        self.assertEqual(4, result.aftermath_events[0].applied_amount)
        self.assertEqual("place_counters", result.aftermath_events[0].kind)

    def test_counter_result_is_visible_to_reflected_damage_before_live_commit(self):
        engine = self.session(615217, players=4).engine
        source = self.add_permanent(engine, seat="A", name="Mishra, Eminent One", ref="damage")
        reflector = self.add_permanent(engine, seat="C", name="Goblin Engineer", ref="reflector")
        target = self.add_permanent(engine, seat="B", name="Goblin Engineer", ref="shield-counter-target")
        protected = DamageSubject(
            ref=target.ref, kind="permanent", controller="B", owner="B",
            object_id=target.object_id, logical_object_id=target.logical_object_id,
        )
        engine.state.damage_prevention_shields.extend((
            DamagePreventionShield(
                shield_id="create-shield-counter",
                source_id="counter-grant",
                controller="B",
                subject=DamageSubject(ref="B", kind="player", controller="B"),
                mode=PreventionMode.NEXT_INSTANCE,
                remaining=None,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                created_turn_sequence=engine.state.turn_sequence,
                aftermath=(PlaceCountersPreventionAftermath(
                    subject=protected, counter_name="shield", placing_player="B", fixed_amount=1,
                ),),
            ),
            DamagePreventionShield(
                shield_id="reflect-to-permanent",
                source_id=reflector.ref,
                controller="C",
                subject=DamageSubject(ref="C", kind="player", controller="C"),
                mode=PreventionMode.NEXT_INSTANCE,
                remaining=None,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                created_turn_sequence=engine.state.turn_sequence,
                aftermath=(DealDamagePreventionAftermath(
                    source=source_snapshot(engine, reflector.ref, controller="C"),
                    recipient=DamageAftermathRecipient(kind="fixed", subject=protected),
                    per_prevented=1,
                ),),
            ),
        ))
        before = authoritative_state_hash(engine.state)
        prepared = prepare_damage_batch(engine, (
            self.proposal(engine, source=source, target="B", amount=2, event_id="damage:shield-grant"),
            self.proposal(engine, source=source, target="C", amount=2, event_id="damage:reflection"),
        ))
        self.assertEqual(before, authoritative_state_hash(engine.state))
        result = commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(0, target.marked_damage)
        self.assertNotIn("shield", target.counters)
        nested, = result.nested_damage_results
        self.assertEqual(0, nested.dealt_amount)
        self.assertEqual(2, nested.events[0].prevented_amount)
        self.assertEqual([("place_counters", 1), ("deal_damage", 0)], [
            (event.kind, event.applied_amount) for event in result.aftermath_events
        ])

    def test_fixed_additional_effect_happens_when_damage_cannot_be_prevented(self):
        engine = self.session(615203).engine
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="source"
        )
        engine.state.players["B"].life = 30
        engine.state.damage_prevention_shields.append(
            DamagePreventionShield(
                shield_id="fixed-additional-effect",
                source_id="fixture:fixed-additional-effect",
                controller="B",
                subject=DamageSubject(ref="B", kind="player", controller="B"),
                mode=PreventionMode.NEXT_INSTANCE,
                remaining=None,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                created_turn_sequence=engine.state.turn_sequence,
                aftermath=(
                    GainLifePreventionAftermath(player="B", fixed_amount=3),
                ),
            )
        )

        result = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=source,
                    target="B",
                    amount=2,
                    unpreventable=True,
                ),
            ),
        )

        self.assertEqual(31, engine.state.players["B"].life)
        self.assertEqual((), result.prevention_events)
        self.assertEqual(3, result.aftermath_events[0].applied_amount)
        self.assertEqual(1, len(engine.state.damage_prevention_shields))

    def test_stale_counter_aftermath_fails_before_life_or_damage_mutation(self):
        engine = self.session(615204).engine
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="source"
        )
        target = self.add_permanent(
            engine, seat="B", name="Goblin Engineer", ref="target"
        )
        target.counters["+1/+1"] = 5
        subject = DamageSubject(
            ref=target.ref,
            kind="permanent",
            controller="B",
            object_id=target.object_id,
            logical_object_id=target.logical_object_id,
            owner="B",
        )
        engine.state.damage_prevention_shields.append(
            DamagePreventionShield(
                shield_id="compound",
                source_id="fixture:compound",
                controller="B",
                subject=subject,
                mode=PreventionMode.AMOUNT,
                remaining=2,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                created_turn_sequence=engine.state.turn_sequence,
                aftermath=(
                    GainLifePreventionAftermath(player="B", fixed_amount=3),
                    PlaceCountersPreventionAftermath(
                        subject=subject,
                        counter_name="+1/+1",
                        placing_player="B",
                        per_prevented=1,
                    ),
                ),
            )
        )
        before_life = engine.state.players["B"].life
        prepared = prepare_damage_batch(
            engine,
            (self.proposal(engine, source=source, target=target, amount=2),),
        )
        target.zone_change_counter += 1

        with self.assertRaisesRegex(ValueError, "identity|changed"):
            commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(before_life, engine.state.players["B"].life)
        self.assertEqual(5, target.counters["+1/+1"])
        self.assertEqual(1, len(engine.state.damage_prevention_shields))

    def test_aggregate_aftermath_amount_property_over_simultaneous_events(self):
        engine = self.session(615205, players=4).engine
        shield = DamagePreventionShield(
            shield_id="aggregate-property",
            source_id="fixture:aggregate-property",
            controller="B",
            subject=DamageSubject(ref="B", kind="player", controller="B"),
            mode=PreventionMode.AMOUNT,
            remaining=1000,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=engine.state.turn_sequence,
            aftermath=(
                GainLifePreventionAftermath(player="B", per_prevented=1),
            ),
        )
        engine.state.damage_prevention_shields.append(shield)
        rng = random.Random(615205)
        for case in range(64):
            amounts = [rng.randrange(0, 11) for _ in range(rng.randrange(1, 9))]
            events = tuple(
                ReplaceableEvent(
                    event_id=f"property:{case}:{index}",
                    kind="damage",
                    affected_player="B",
                    payload={
                        "amount": 0,
                        "prevention_applied": {shield.effect_id: amount},
                    },
                    applied_effects=(shield.effect_id,),
                )
                for index, amount in enumerate(amounts)
            )
            applications = prevention_applications(engine, events)
            self.assertEqual(1, len(applications))
            self.assertEqual(sum(amounts), applications[0].prevented_amount)
            self.assertEqual(
                len(amounts), len(applications[0].damage_event_ids)
            )

    def test_prevention_aftermath_replays_from_exact_commands(self):
        session = self.session(615512)
        engine = session.engine
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="damage-source"
        )
        shield = DamagePreventionShield(
            shield_id="aftermath-replay",
            source_id="fixture:aftermath-replay",
            controller="B",
            subject=DamageSubject(ref="B", kind="player", controller="B"),
            mode=PreventionMode.AMOUNT,
            remaining=2,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=engine.state.turn_sequence,
            aftermath=(
                GainLifePreventionAftermath(player="B", per_prevented=1),
            ),
        )
        engine.state.damage_prevention_shields.append(shield)
        program = SemanticProgram(
            key="test:prevention-aftermath-replay",
            label="Replay prevention aftermath",
            effects=[
                {
                    "op": "damage",
                    "source": "$source",
                    "target": "B",
                    "amount": 3,
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="prevention-aftermath-replay",
                ref="S-prevention-aftermath-replay",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                source_object_id=source.object_id,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(41, engine.state.players["B"].life)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "prevention-aftermath-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()


class CombatPreventionTokenCompilerTests(unittest.TestCase):
    def test_inkshield_compiles_one_closed_token_aftermath(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.rules.prevention_component_shapes import fixed_prevention_component_capabilities
        from test_bound_effect_programs import record

        material = (
            "Prevent all combat damage that would be dealt to you this turn. "
            "For each 1 damage prevented this way, create a 2/1 white and black Inkling creature token with flying."
        )
        registry = load_default_capability_registry()
        result = compile_oracle_card(record(material), capability_registry=registry)
        self.assertEqual("exact", result.status, result.material_residuals)
        node, = result.faces[0].nodes
        self.assertEqual(material, node.text)
        self.assertIn("damage.prevention.aftermath.token_creation", node.capability_dependencies)
        effect, = node.effects
        recipe, = effect["aftermath"]
        self.assertEqual("create_tokens", recipe["kind"])
        self.assertEqual("combat", effect["damage_kind"])
        self.assertEqual("Inkling Token", recipe["name"])
        for changed in (
            {**effect, "damage_kind": "any"},
            {**effect, "subject": "$target.0"},
            {**effect, "aftermath": [{**recipe, "schema_version": True}]},
            {**effect, "aftermath": [{**recipe, "characteristics": {
                **recipe["characteristics"], "activated_ability_profile": "unrepresented",
            }}]},
        ):
            self.assertEqual((), fixed_prevention_component_capabilities(
                effects=(changed,), target_schema=None, mechanic_ids=node.mechanics,
            ))


class CombatPreventionTokenNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Commander", "Generic Bound Plains", "Grizzly Bears"},
            "ability-source-counter-cards.json": {"Shock"},
            "combat-prevention-source-scopes-cards.json": {"Flaring Pain"},
            "current-life-effect-amount-cards.json": {"Anointed Procession"},
            "additional-token-definition-cards.json": {"Parallel Lives"},
            "combat-prevention-token-aftermath-cards.json": {"Inkshield", "Holy Day"},
        }
        pieces = []
        for filename, names in support.items():
            data = json.loads((ROOT / "tests/fixtures" / filename).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            pieces.append(piece)
        database = folder / "combat-prevention-tokens.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Combat prevention tokens", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _world(self, seed, mode):
        game = self.session(seed)
        shield = self.add(game.engine, "Inkshield", seat="B", zone="hand")
        attackers = [self.add(game.engine, "Grizzly Bears", ref=f"inkshield-attacker-{i}") for i in range(3)]
        for card in attackers:
            card.acquired_control_turn_count = -1
        private = self.add(game.engine, "Shock", seat="D", zone="hand", ref="inkshield-private")
        other = None
        if mode == "split":
            other = self.add(game.engine, "Holy Day", seat="C", zone="hand")
        elif mode == "unpreventable":
            other = self.add(game.engine, "Flaring Pain", zone="hand")
        elif mode == "replacement":
            for name in ("Anointed Procession", "Parallel Lives"):
                self.assert_closed(game, self.add(game.engine, name, seat="B"))
        shock = self.add(game.engine, "Shock", zone="hand", ref="inkshield-noncombat") if mode == "base" else None
        self.assert_closed(game, shield)
        self.prepare(game, mana={"R": 10, "C": 10})
        game.state.players["B"].mana_pool.update({"B": 1, "W": 1, "C": 10})
        game.state.players["C"].mana_pool["W"] = 1
        self.checkpoint(game)
        self.cast(game, "B", shield)
        self.assertIsNone(self.resolve(game))
        if other:
            self.assert_closed(game, other)
            self.cast(game, "C" if mode == "split" else "A", other)
            self.assertIsNone(self.resolve(game))
        if shock:
            self.cast(game, "A", shock, ["B"])
            self.assertIsNone(self.resolve(game))
            self.assertEqual(38, game.state.players["B"].life)
            self.assertEqual([], self._inklings(game))
        self.attack(game, [(attackers[0], "B"), (attackers[1], "B"), (attackers[2], "C")])
        return game, private

    @staticmethod
    def _inklings(game):
        return [card for card in game.state.cards.values()
                if card.is_token and card.zone == "battlefield" and card.printed_name == "Inkling Token"]

    def _damage_boundary(self, game):
        self.until(game, lambda session: session.state.pending_decision.kind == "replacement.order"
                   or any(row.code == "combat.damage" for row in session.state.events))

    def test_actual_inkshield_uses_only_its_own_combat_prevention_to_create_tokens(self):
        from quorune.record import authoritative_state_hash

        for mode, amount, life_b, life_c in (("base", 4, 38, 38), ("split", 2, 40, 40), ("unpreventable", 0, 36, 38)):
            with self.subTest(mode=mode):
                game, _ = self._world(615500 + len(mode), mode)
                self._damage_boundary(game)
                if mode == "split":
                    shield = next(row for row in game.state.damage_prevention_shields if row.controller == "B")
                    for occurrence in range(2):
                        decision = game.packet("pilot:B", full=True)["decision"]
                        self.assertEqual("replacement.order", decision["kind"])
                        selected = next(row["id"] for row in decision["ctx"]["options"]
                                        if (row["id"] == shield.effect_id) == (occurrence == 0))
                        command = {"action_id": "choose", "choices": {"replacement": selected}}
                        before = authoritative_state_hash(game.state)
                        self.assertFalse(game.act("pilot:C", command).ok)
                        self.assertEqual(before, authoritative_state_hash(game.state))
                        result = game.act("pilot:B", command)
                        self.assertTrue(result.ok, result.summary)
                    self._damage_boundary(game)
                self.assertEqual((life_b, life_c), (game.state.players["B"].life, game.state.players["C"].life))
                tokens = self._inklings(game)
                self.assertEqual(amount, len(tokens))
                for card in tokens:
                    self.assertEqual("B", card.controller)
                    data = game.engine._effective_card_data(card)
                    self.assertEqual({"W", "B"}, set(data["colors"]))
                    self.assertEqual(("2", "1"), (str(data["power"]), str(data["toughness"])))
                    self.assertIn("Flying", data["keywords"])
                self.replay(game, load=True)

    def test_inkshield_actual_prevention_quantity_mutant_is_killed(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.rules import combat_prevention_tokens

        original = combat_prevention_tokens.combat_prevention_token_requests

        def undercount(host, applications):
            return tuple(replace(row, amount=row.amount - 1) for row in original(host, applications))

        game, _ = self._world(615512, "base")
        with patch.object(combat_prevention_tokens, "combat_prevention_token_requests", undercount):
            self._damage_boundary(game)
        self.assertEqual(3, len(self._inklings(game)))
        self.assertEqual(38, game.state.players["B"].life)
        with self.assertRaisesRegex(AssertionError, "4 != 3"):
            self.assertEqual(4, len(self._inklings(game)))

    def test_actual_inkshield_token_replacement_replays_the_committed_damage_once(self):
        import json

        game, private = self._world(615511, "replacement")
        self._damage_boundary(game)
        decision = game.packet("pilot:B", full=True)["decision"]
        self.assertEqual("replacement.order", decision["kind"])
        self.assertEqual([], self._inklings(game))
        self.assertEqual(38, game.state.players["C"].life)
        self.assertNotIn(private.ref, json.dumps(decision))
        game = self.replay(game, load=True)
        selected = decision["ctx"]["options"][0]["id"]
        result = game.act("pilot:B", {"action_id": "choose", "choices": {"replacement": selected}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(16, len(self._inklings(game)))
        self.assertEqual(1, sum(row.code == "combat.damage" for row in game.state.events))
        self.assertEqual(38, game.state.players["C"].life)
        self.replay(game, load=True)
