from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from damage_replacement_support import DamageReplacementPipelineBase
from quorune.damage import (
    DamageError,
    commit_prepared_damage_batch,
    prepare_damage_batch,
    resolve_damage_batch,
)
from quorune.errors import GameRuleError
from quorune.model import GameState
from quorune.object_query import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.damage_prevention import (
    ChosenDamageSource,
    DamageModifierDuration,
    DamagePreventionShield,
    DamageSubject,
    PreventionDamageKind,
    PreventionMode,
    PreventionRecipientKind,
    expire_end_of_turn_damage_modifiers,
)
from quorune.damage_modifier_state import DamagePreventionScope
from quorune.replacement_effects import (
    ReplacementChoiceRequired,
)
from quorune.semantics import SemanticProgram
from quorune.rules.capabilities import load_default_capability_registry


class DamagePreventionShieldTests(DamageReplacementPipelineBase):
    def shield(
        self,
        engine,
        *,
        shield_id: str = "fixture-shield",
        subject: str = "B",
        mode: PreventionMode = PreventionMode.AMOUNT,
        remaining: int | None = 3,
        chosen_source=None,
        damage_kind: PreventionDamageKind = PreventionDamageKind.ANY,
        recipient_kind: PreventionRecipientKind = PreventionRecipientKind.ANY,
        scope: DamagePreventionScope = DamagePreventionScope(),
    ) -> DamagePreventionShield:
        value = DamagePreventionShield(
            shield_id=shield_id,
            source_id=f"effect:{shield_id}",
            controller="B",
            subject=DamageSubject(
                ref=subject,
                kind="any" if subject == "*" else "player",
                controller="B" if subject == "*" else subject,
            ),
            mode=mode,
            remaining=remaining,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=engine.state.turn_sequence,
            damage_kind=damage_kind,
            recipient_kind=recipient_kind,
            scope=scope,
            chosen_source=chosen_source,
            label="Fixture prevention shield",
        )
        engine.state.damage_prevention_shields.append(value)
        return value

    def test_amount_shield_is_prepared_without_mutation_then_consumed(self):
        engine = self.session(615001).engine
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        self.shield(engine, remaining=5)

        prepared = prepare_damage_batch(
            engine,
            (self.proposal(engine, source=source, target="B", amount=3),),
        )

        self.assertEqual(0, prepared.events[0].payload["amount"])
        self.assertEqual(3, prepared.events[0].payload["prevented"])
        self.assertEqual(5, engine.state.damage_prevention_shields[0].remaining)
        result = commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(40, engine.state.players["B"].life)
        self.assertEqual(2, engine.state.damage_prevention_shields[0].remaining)
        self.assertEqual(1, len(result.prevention_events))
        self.assertEqual(3, result.prevention_events[0].prevented_amount)

    def test_scoped_all_shield_filters_round_trips_and_expires(self):
        engine = self.session(615002).engine

        def resolved(proposal):
            try:
                prepared = prepare_damage_batch(engine, (proposal,))
            except ReplacementChoiceRequired as required:
                effect_id = next(
                    value
                    for value in required.pending.choice.options
                    if "prevention.shield" in value
                )
                prepared = prepare_damage_batch(
                    engine,
                    (proposal,),
                    selections=(effect_id,),
                )
            return commit_prepared_damage_batch(engine, prepared).events[0]

        creature_source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-creature-source"
        )
        noncreature_source = self.add_permanent(
            engine,
            seat="A",
            name="Scoped Prevention Ward",
            ref="a-enchantment-source",
        )
        scope = DamagePreventionScope(
            target_controller_relation="source_controller",
            target_kinds=("player",),
            source_characteristics_all=("creature",),
        )
        engine.apply_effect(
            {
                "op": "create_damage_prevention_shield",
                "source": "fixture:scoped-all",
                "subject": "*",
                "mode": "all",
                "duration": "until_end_of_turn",
                "scope": scope.to_dict(),
            },
            actor="B",
        )
        self.assertEqual(scope, engine.state.damage_prevention_shields[0].scope)
        restored = GameState.from_dict(engine.state.to_dict())
        self.assertEqual(scope, restored.damage_prevention_shields[0].scope)

        matching = resolved(
            self.proposal(engine, source=creature_source, target="B", amount=3)
        )
        self.assertEqual((0, 3), (matching.dealt_amount, matching.prevented_amount))
        nonmatching_source = resolved(
            self.proposal(
                    engine,
                    source=noncreature_source,
                    target="B",
                    amount=3,
                    event_id="damage:scoped-noncreature",
                )
        )
        self.assertEqual(
            (3, 0),
            (nonmatching_source.dealt_amount, nonmatching_source.prevented_amount),
        )
        nonmatching_target = resolved(
            self.proposal(
                    engine,
                    source=creature_source,
                    target="A",
                    amount=3,
                    event_id="damage:scoped-wrong-player",
                )
        )
        self.assertEqual(
            (3, 0),
            (nonmatching_target.dealt_amount, nonmatching_target.prevented_amount),
        )

        before = tuple(engine.state.damage_prevention_shields)
        malformed = scope.to_dict()
        malformed["target_kinds"] = ["secret"]
        with self.assertRaisesRegex(GameRuleError, "target kind"):
            engine.apply_effect(
                {
                    "op": "create_damage_prevention_shield",
                    "source": "fixture:malformed-scoped-all",
                    "subject": "*",
                    "mode": "all",
                    "duration": "until_end_of_turn",
                    "scope": malformed,
                },
                actor="B",
            )
        self.assertEqual(before, tuple(engine.state.damage_prevention_shields))
        expire_end_of_turn_damage_modifiers(engine.state)
        self.assertFalse(engine.state.damage_prevention_shields)

    def test_compiled_turn_bound_scopes_install_through_existing_shield_owner(self):
        engine = self.session(615003).engine
        source = self.add_permanent(
            engine,
            seat="A",
            name="Entry Prevention Guardian",
            ref="a-scoped-haze-source",
        )
        protected = self.add_permanent(
            engine,
            seat="B",
            name="Entry Prevention Guardian",
            ref="b-scoped-haze-target",
        )
        protected.counters["+1/+1"] = 10
        ir = compile_oracle_card(
            self.db.lookup("Scoped Prevention Haze"),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
        )
        node = ir.faces[0].nodes[0]
        self.assertEqual(2, len(node.effects))
        before = tuple(engine.state.damage_prevention_shields)
        for effect in node.effects:
            resolved_effect = dict(effect)
            resolved_effect["source"] = "fixture:scoped-haze"
            engine.apply_effect(resolved_effect, actor="B")
        self.assertEqual(
            len(before) + 2,
            len(engine.state.damage_prevention_shields),
        )

        def resolved(proposal):
            try:
                prepared = prepare_damage_batch(engine, (proposal,))
            except ReplacementChoiceRequired as required:
                effect_id = next(
                    value
                    for value in required.pending.choice.options
                    if "prevention.shield" in value
                )
                prepared = prepare_damage_batch(
                    engine,
                    (proposal,),
                    selections=(effect_id,),
                )
            return commit_prepared_damage_batch(engine, prepared).events[0]

        player_event = resolved(
            self.proposal(
                engine,
                source=source,
                target="B",
                event_id="damage:compiled-scoped-player",
            )
        )
        self.assertEqual(
            (0, 3), (player_event.dealt_amount, player_event.prevented_amount)
        )
        permanent_event = resolved(
            self.proposal(
                engine,
                source=source,
                target=protected,
                event_id="damage:compiled-scoped-permanent",
            )
        )
        self.assertEqual(
            (0, 3),
            (permanent_event.dealt_amount, permanent_event.prevented_amount),
        )

    def test_exhausted_amount_and_next_instance_shields_are_removed(self):
        for index, (mode, remaining) in enumerate(
            (
                (PreventionMode.AMOUNT, 3),
                (PreventionMode.NEXT_INSTANCE, None),
            )
        ):
            with self.subTest(mode=mode):
                engine = self.session(615010 + index).engine
                source = self.add_permanent(
                    engine,
                    seat="A",
                    name="Mishra, Eminent One",
                    ref=f"a-source-{index}",
                )
                self.shield(engine, mode=mode, remaining=remaining)
                prepared = prepare_damage_batch(
                    engine,
                    (
                        self.proposal(
                            engine,
                            source=source,
                            target="B",
                            amount=3,
                        ),
                    ),
                )
                commit_prepared_damage_batch(engine, prepared)
                self.assertEqual([], engine.state.damage_prevention_shields)

    def test_until_end_of_turn_shields_expire_at_the_cleanup_boundary(self):
        engine = self.session(615012).engine
        shield = self.shield(engine, remaining=3)
        removed = expire_end_of_turn_damage_modifiers(engine.state)
        self.assertEqual((shield.shield_id,), removed)
        self.assertEqual([], engine.state.damage_prevention_shields)

    def test_combat_and_recipient_scopes_filter_without_consuming_shield(self):
        engine = self.session(615013, players=4).engine
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        creature = self.add_permanent(
            engine, seat="B", name="Mishra, Eminent One", ref="b-creature"
        )
        self.shield(
            engine,
            subject="*",
            mode=PreventionMode.ALL,
            remaining=None,
            damage_kind=PreventionDamageKind.COMBAT,
            recipient_kind=PreventionRecipientKind.PLAYER,
        )

        noncombat = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=source,
                    target="B",
                    amount=2,
                    event_id="damage:scoped-noncombat",
                ),
            ),
        )
        permanent = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=source,
                    target=creature,
                    amount=2,
                    combat=True,
                    event_id="damage:scoped-permanent",
                ),
            ),
        )
        combat_player = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=source,
                    target="B",
                    amount=2,
                    combat=True,
                    event_id="damage:scoped-player",
                ),
            ),
        )

        self.assertEqual(2, noncombat.dealt_amount)
        self.assertEqual(2, permanent.dealt_amount)
        self.assertEqual(0, combat_player.dealt_amount)
        self.assertEqual(38, engine.state.players["B"].life)
        self.assertEqual(1, len(engine.state.damage_prevention_shields))

    def test_malformed_prevention_scope_preserves_authoritative_state(self):
        engine = self.session(615014).engine
        base = {
            "op": "create_damage_prevention_shield",
            "source": "fixture:malformed-scope",
            "subject": "*",
            "mode": "all",
            "duration": "until_end_of_turn",
        }
        for field, value in (
            ("damage_kind", "combat_and_noncombat"),
            ("recipient_kind", "creature"),
        ):
            with self.subTest(field=field):
                before = authoritative_state_hash(engine.state)
                with self.assertRaises(GameRuleError):
                    engine.apply_effect({**base, field: value}, actor="B")
                self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_combat_scope_composes_with_chosen_source_identity(self):
        engine = self.session(615015, players=4).engine
        chosen = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="chosen-source"
        )
        other = self.add_permanent(
            engine, seat="C", name="Mishra, Eminent One", ref="other-source"
        )
        self.shield(
            engine,
            subject="*",
            mode=PreventionMode.ALL,
            remaining=None,
            damage_kind=PreventionDamageKind.COMBAT,
            chosen_source=ChosenDamageSource(
                ref=chosen.ref,
                object_id=chosen.object_id,
            ),
        )

        results = (
            resolve_damage_batch(
                engine,
                (
                    self.proposal(
                        engine,
                        source=chosen,
                        target="B",
                        amount=1,
                        event_id="damage:chosen-noncombat",
                    ),
                ),
            ),
            resolve_damage_batch(
                engine,
                (
                    self.proposal(
                        engine,
                        source=other,
                        target="B",
                        amount=1,
                        combat=True,
                        event_id="damage:other-combat",
                    ),
                ),
            ),
            resolve_damage_batch(
                engine,
                (
                    self.proposal(
                        engine,
                        source=chosen,
                        target="B",
                        amount=1,
                        combat=True,
                        event_id="damage:chosen-combat",
                    ),
                ),
            ),
        )

        self.assertEqual([1, 1, 0], [result.dealt_amount for result in results])
        self.assertEqual(38, engine.state.players["B"].life)

    def test_unpreventable_damage_does_not_consume_shield(self):
        engine = self.session(615020).engine
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        shield = self.shield(engine, remaining=3)
        prepared = prepare_damage_batch(
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
        self.assertIn(shield.effect_id, prepared.events[0].applied_effects)
        self.assertEqual(0, prepared.events[0].payload["prevented"])
        result = commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(38, engine.state.players["B"].life)
        self.assertEqual(3, engine.state.damage_prevention_shields[0].remaining)
        self.assertEqual((), result.prevention_events)

    def test_chosen_source_identity_and_property_mismatch_preserve_shield(self):
        engine = self.session(615030).engine
        chosen = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="chosen"
        )
        other = self.add_permanent(
            engine, seat="A", name="White Knight", ref="other"
        )
        self.shield(
            engine,
            remaining=3,
            chosen_source=ChosenDamageSource(
                ref=chosen.ref,
                object_id=chosen.object_id,
                required_colors=("U",),
            ),
        )

        mismatch = prepare_damage_batch(
            engine,
            (self.proposal(engine, source=other, target="B", amount=2),),
        )
        commit_prepared_damage_batch(engine, mismatch)
        self.assertEqual(38, engine.state.players["B"].life)
        self.assertEqual(3, engine.state.damage_prevention_shields[0].remaining)

        match = prepare_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=chosen,
                    target="B",
                    amount=2,
                    event_id="damage:chosen",
                ),
            ),
        )
        commit_prepared_damage_batch(engine, match)
        self.assertEqual(38, engine.state.players["B"].life)
        self.assertEqual(1, engine.state.damage_prevention_shields[0].remaining)

    def test_next_instance_shield_survives_nonmatching_then_is_used(self):
        engine = self.session(615034).engine
        chosen = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="chosen"
        )
        other = self.add_permanent(
            engine, seat="A", name="White Knight", ref="other"
        )
        self.shield(
            engine,
            mode=PreventionMode.NEXT_INSTANCE,
            remaining=None,
            chosen_source=ChosenDamageSource(
                ref=chosen.ref,
                object_id=chosen.object_id,
            ),
        )
        commit_prepared_damage_batch(
            engine,
            prepare_damage_batch(
                engine,
                (self.proposal(engine, source=other, target="B", amount=1),),
            ),
        )
        self.assertEqual(39, engine.state.players["B"].life)
        self.assertEqual(1, len(engine.state.damage_prevention_shields))

        commit_prepared_damage_batch(
            engine,
            prepare_damage_batch(
                engine,
                (
                    self.proposal(
                        engine,
                        source=chosen,
                        target="B",
                        amount=2,
                        event_id="damage:chosen-next",
                    ),
                ),
            ),
        )
        self.assertEqual(39, engine.state.players["B"].life)
        self.assertEqual([], engine.state.damage_prevention_shields)

    def test_prevention_dispatches_one_aggregate_event_per_effect(self):
        engine = self.session(615035, players=4).engine
        monitor_ref = engine.create_token(
            "B",
            name="Prevention Monitor",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        monitor = engine._resolve_object(
            "B", monitor_ref, zones={"battlefield"}
        )
        engine.semantics.put(
            SemanticProgram(
                key=f"{monitor.oracle_id}:test:damage-prevented",
                label="Damage was prevented",
                oracle_id=monitor.oracle_id,
                ability_id="test:damage-prevented",
                active_zone="battlefield",
                event="damage.prevented",
                effects=[],
            )
        )
        source_a = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        source_c = self.add_permanent(
            engine, seat="C", name="Mishra, Eminent One", ref="c-source"
        )
        shield = self.shield(engine, remaining=10)

        result = resolve_damage_batch(
            engine,
            (
                self.proposal(
                    engine,
                    source=source_a,
                    target="B",
                    amount=2,
                    event_id="damage:prevented:a",
                ),
                self.proposal(
                    engine,
                    source=source_c,
                    target="B",
                    amount=3,
                    event_id="damage:prevented:c",
                ),
            ),
        )
        self.assertEqual(1, len(result.prevention_events))
        self.assertEqual(5, result.prevention_events[0].prevented_amount)
        trigger = next(
            item
            for batch in engine.state.pending_trigger_batches
            for group in batch["groups"]
            for item in group["items"]
            if item["label"] == "Damage was prevented"
        )
        self.assertEqual(shield.effect_id, trigger["context"]["effect_id"])
        self.assertEqual(5, trigger["context"]["prevented_amount"])
        self.assertEqual(
            ["damage:prevented:a", "damage:prevented:c"],
            trigger["context"]["damage_event_ids"],
        )

    def test_simultaneous_sources_require_and_replay_exact_allocation(self):
        engine = self.session(615040, players=4).engine
        source_a = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        source_c = self.add_permanent(
            engine, seat="C", name="Mishra, Eminent One", ref="c-source"
        )
        shield = self.shield(engine, remaining=4)
        proposals = (
            self.proposal(
                engine,
                source=source_a,
                target="B",
                amount=3,
                event_id="damage:source-a",
            ),
            self.proposal(
                engine,
                source=source_c,
                target="B",
                amount=3,
                event_id="damage:source-c",
            ),
        )

        with self.assertRaises(ReplacementChoiceRequired) as required:
            prepare_damage_batch(engine, proposals)
        pending = required.exception.pending
        self.assertEqual("B", pending.choice.chooser)
        allocation = pending.prevention_allocations[0]
        self.assertTrue(allocation.allocation_required)
        self.assertEqual(4, allocation.available)

        selected = {
            "effect_id": shield.effect_id,
            "allocation": {
                "damage:source-a": 1,
                "damage:source-c": 3,
            },
        }
        prepared = prepare_damage_batch(
            engine,
            proposals,
            selections=(selected,),
        )
        self.assertEqual([2, 0], [event.payload["amount"] for event in prepared.events])
        self.assertEqual(
            selected["allocation"],
            dict(prepared.journal[0].allocation),
        )
        commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(38, engine.state.players["B"].life)
        self.assertEqual([], engine.state.damage_prevention_shields)

    def test_shield_round_trip_is_canonical_and_strict(self):
        engine = self.session(615050).engine
        shield = self.shield(engine, remaining=3)
        restored = DamagePreventionShield.from_dict(shield.to_dict())
        self.assertEqual(shield, restored)
        malformed = shield.to_dict()
        malformed["unknown"] = True
        with self.assertRaisesRegex(ValueError, "unknown"):
            DamagePreventionShield.from_dict(malformed)

        state = GameState.from_dict(engine.state.to_dict())
        self.assertEqual(engine.state.to_dict(), state.to_dict())

    def test_chosen_source_versions_preserve_historical_shapes(self):
        legacy = ChosenDamageSource(
            ref="legacy-source",
            object_id="legacy-object",
            required_colors=("R",),
            required_types=("creature",),
        )
        self.assertEqual(
            {
                "ref": "legacy-source",
                "object_id": "legacy-object",
                "required_colors": ["R"],
                "required_types": ["creature"],
            },
            legacy.to_dict(),
        )
        self.assertEqual(
            legacy, ChosenDamageSource.from_dict(legacy.to_dict())
        )

        version_one = ChosenDamageSource(
            ref="v1-source",
            object_id="v1-object",
            required_colors=("U",),
            required_types=("instant",),
            snapshot_version=1,
            logical_object_id="v1-object@1",
            oracle_id="oracle-v1",
            printed_name="Version One Source",
            controller="A",
            owner="A",
            zone="stack",
            types=("instant",),
            colors=("U",),
        )
        serialized = version_one.to_dict()
        self.assertNotIn("identity_keys", serialized)
        self.assertNotIn("allowed_colors", serialized)
        self.assertEqual(
            version_one, ChosenDamageSource.from_dict(serialized)
        )

        version_two = ChosenDamageSource(
            ref="v2-source",
            object_id="v2-object",
            allowed_colors=("B", "R"),
            required_types=("creature",),
            required_subtypes=("wizard",),
            required_supertypes=("legendary",),
            required_keywords=("flying",),
            snapshot_version=2,
            logical_object_id="v2-object@1",
            oracle_id="oracle-v2",
            printed_name="Version Two Source",
            controller="A",
            owner="A",
            zone="battlefield",
            types=("creature",),
            subtypes=("wizard",),
            supertypes=("legendary",),
            colors=("B",),
            keywords=("flying",),
            identity_keys=("v2-object@1|battlefield",),
        )
        self.assertEqual(
            {"contains_any": ["B", "R"]},
            version_two.event_conditions()["source_colors"],
        )
        self.assertEqual(
            version_two,
            ChosenDamageSource.from_dict(version_two.to_dict()),
        )

        version_three = ChosenDamageSource(
            ref="v3-source",
            object_id="v3-object",
            predicate=ObjectQuerySpec(
                zones=("battlefield", "stack"),
                types_all=("creature",),
                colors_all=("U",),
                colors_any=("R",),
                known_to_actor=True,
            ),
            snapshot_version=3,
            logical_object_id="v3-object@1",
            oracle_id="oracle-v3",
            printed_name="Version Three Source",
            controller="A",
            owner="A",
            zone="battlefield",
            types=("creature",),
            colors=("R", "U"),
            identity_keys=("v3-object@1|battlefield",),
        )
        version_three_dict = version_three.to_dict()
        self.assertIn("predicate", version_three_dict)
        self.assertNotIn("required_colors", version_three_dict)
        self.assertEqual(
            {
                "contains_all": ["U"],
                "contains_any": ["R"],
            },
            version_three.event_conditions()["source_colors"],
        )
        self.assertEqual(
            version_three,
            ChosenDamageSource.from_dict(version_three_dict),
        )
        malformed_v3 = dict(version_three_dict)
        malformed_v3["unknown"] = True
        with self.assertRaisesRegex(ValueError, "unknown unknown"):
            ChosenDamageSource.from_dict(malformed_v3)

        malformed_v3_predicate = dict(version_three_dict)
        malformed_v3_predicate["predicate"] = {
            **version_three_dict["predicate"],
            "known_to_actor": False,
        }
        with self.assertRaisesRegex(ValueError, "legally known"):
            ChosenDamageSource.from_dict(malformed_v3_predicate)

        malformed_v3_term = dict(version_three_dict)
        malformed_v3_term["predicate"] = {
            **version_three_dict["predicate"],
            "types_all": [1],
        }
        with self.assertRaisesRegex(ValueError, "nonempty strings"):
            ChosenDamageSource.from_dict(malformed_v3_term)

        malformed = version_two.to_dict()
        malformed["unknown"] = True
        with self.assertRaisesRegex(ValueError, "unknown"):
            ChosenDamageSource.from_dict(malformed)

        malformed_version = version_two.to_dict()
        malformed_version["snapshot_version"] = "2"
        with self.assertRaisesRegex(ValueError, "must be integers"):
            ChosenDamageSource.from_dict(malformed_version)

        tampered_identity = version_two.to_dict()
        tampered_identity["identity_keys"] = [
            "another-object@9|battlefield"
        ]
        with self.assertRaisesRegex(ValueError, "do not match"):
            ChosenDamageSource.from_dict(tampered_identity)

    def test_stale_modifier_plan_fails_before_damage_mutation(self):
        engine = self.session(615051).engine
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        self.shield(engine, remaining=3)
        prepared = prepare_damage_batch(
            engine,
            (self.proposal(engine, source=source, target="B", amount=2),),
        )
        engine.state.damage_prevention_shields.clear()
        before = engine.state.players["B"].life
        with self.assertRaisesRegex(DamageError, "no longer matches"):
            commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(before, engine.state.players["B"].life)

    def test_shield_created_after_prepare_cannot_change_prepared_damage(self):
        engine = self.session(615053).engine
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        prepared = prepare_damage_batch(
            engine,
            (self.proposal(engine, source=source, target="B", amount=2),),
        )
        self.assertEqual(2, prepared.events[0].payload["amount"])
        self.shield(engine, remaining=3)
        before = engine.state.players["B"].life
        with self.assertRaisesRegex(DamageError, "no longer matches"):
            commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(before, engine.state.players["B"].life)

    def test_changed_modifier_value_fails_before_damage_mutation(self):
        engine = self.session(615052).engine
        source = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        shield = self.shield(engine, remaining=3)
        prepared = prepare_damage_batch(
            engine,
            (self.proposal(engine, source=source, target="B", amount=2),),
        )
        engine.state.damage_prevention_shields[0] = DamagePreventionShield(
            shield_id=shield.shield_id,
            source_id=shield.source_id,
            controller=shield.controller,
            subject=shield.subject,
            mode=shield.mode,
            remaining=2,
            duration=shield.duration,
            created_turn_sequence=shield.created_turn_sequence,
            chosen_source=shield.chosen_source,
            label=shield.label,
        )
        before = engine.state.players["B"].life
        with self.assertRaisesRegex(DamageError, "no longer matches"):
            commit_prepared_damage_batch(engine, prepared)
        self.assertEqual(before, engine.state.players["B"].life)

    def test_combat_allocation_is_seat_scoped_and_command_replays(self):
        session = self.session(615060, players=4)
        engine = session.engine
        source_a = self.add_permanent(
            engine, seat="A", name="Mishra, Eminent One", ref="a-source"
        )
        source_c = self.add_permanent(
            engine, seat="C", name="Mishra, Eminent One", ref="c-source"
        )
        shield = self.shield(
            engine,
            remaining=4,
            damage_kind=PreventionDamageKind.COMBAT,
            recipient_kind=PreventionRecipientKind.PLAYER,
            scope=DamagePreventionScope(
                source_characteristics_all=("creature",),
            ),
        )
        engine.state.active_player = "A"
        engine.state.phase = "combat"
        engine.state.step = "combat_damage"
        assignments = [
            {"source": source_a.ref, "target": "B", "amount": 3},
            {"source": source_c.ref, "target": "B", "amount": 3},
        ]
        self.assertTrue(engine._apply_combat_assignments(assignments))
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:B")
        self.assertIsNotNone(projected)
        self.assertIsNone(projector._decision("pilot:A"))
        serialized = json.dumps(projected, sort_keys=True)
        self.assertNotIn(source_a.object_id, serialized)
        self.assertNotIn(source_c.object_id, serialized)
        allocation_rows = projected["ctx"]["prevention_allocations"][
            shield.effect_id
        ]["events"]
        event_ids = [row["event_id"] for row in allocation_rows]
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "choices": {
                    "replacement": shield.effect_id,
                    "prevention_allocation": {
                        event_ids[0]: 1,
                        event_ids[1]: 3,
                    },
                },
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(38, engine.state.players["B"].life)
        expected = engine.state.to_dict()

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "prevention-allocation-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, engine.state.to_dict())


if __name__ == "__main__":
    unittest.main()
