from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.card_programs import compile_card_program
from quorune.carddb import CardRecord
from quorune.characteristic_fragments import (
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
)
from quorune.damage_prevention import (
    DamageModifierDuration,
    DamagePreventionShield,
    DamageSubject,
    PreventionMode,
)
from quorune.model import CardInstance, StackItem
from quorune.object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from quorune.oracle_ir import generated_programs, register_generated_programs
from quorune.projection import StateProjector
from quorune.query_effect_amount_model import (
    PUBLIC_QUERY_AMOUNT_CAPABILITY,
    PUBLIC_QUERY_AMOUNT_KIND,
    PublicQueryAmountError,
    PublicQueryAmountSpec,
)
from quorune.compiler.public_query_effect_amounts import (
    PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    load_record_state,
    replay_record,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.values import resolve_semantic_value
from quorune.trigger_processing import collect_trigger_items, enqueue_trigger_batch


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def query_amount_record(
    text: str,
    *,
    suffix: int,
    name: str = "Generic Query Amount",
    type_line: str = "Sorcery",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=name,
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


def _contains_query_amount(value) -> bool:
    if isinstance(value, dict):
        return value.get("kind") == PUBLIC_QUERY_AMOUNT_KIND or any(
            _contains_query_amount(child) for child in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_query_amount(child) for child in value)
    return False


class PublicQueryEffectAmountCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def compile(self, record: CardRecord):
        return compile_card_program(
            _NoRulingsDatabase(),
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="provisional",
        )

    def test_compiler_lowers_public_query_amounts_across_contexts(self):
        cases = (
            (
                "Generic Damage deals damage to target attacking creature "
                "equal to the number of Equipment you control.",
                "Generic Damage",
                "Instant",
                "damage",
            ),
            (
                "You gain 2 life for each creature you control.",
                "Generic Life",
                "Sorcery",
                "life",
            ),
            (
                "Draw a card for each Spirit you control.",
                "Generic Draw",
                "Sorcery",
                "draw",
            ),
            (
                "Create X 1/1 red Goblin creature tokens, where X is the "
                "number of Goblins you control.",
                "Generic Tokens",
                "Sorcery",
                "create_token",
            ),
            (
                "{T}: You gain 1 life for each artifact you control.",
                "Generic Activation",
                "Artifact Creature — Construct",
                "life",
            ),
            (
                "At the beginning of your upkeep, you gain 1 life for each "
                "card in your hand.",
                "Generic Trigger",
                "Enchantment",
                "life",
            ),
        )
        for index, (text, name, type_line, operation) in enumerate(cases):
            with self.subTest(text=text):
                program = self.compile(
                    query_amount_record(
                        text,
                        suffix=172_000_000 + index,
                        name=name,
                        type_line=type_line,
                    )
                )
                self.assertEqual((), program.residuals)
                ability = next(
                    ability
                    for ability in program.abilities
                    if any(effect.get("op") == operation for effect in ability.effects)
                )
                self.assertIn(
                    PUBLIC_QUERY_AMOUNT_CAPABILITY,
                    ability.capability_dependencies,
                )
                self.assertTrue(_contains_query_amount(ability.effects))
                self.assertTrue(ability.capability_closure["trusted"])

    def test_public_query_amount_grammar_and_schema_fail_closed(self):
        excluded = (
            "You gain life equal to your life total.",
            "You gain 1 life for each creature an opponent controls.",
            "You gain 1 life for each card in an opponent's hand.",
            "You gain 1 life for each tapped creature you control.",
            "You gain 1 life for each attacking creature you control.",
            "You gain 1 life for each creature with flying you control.",
            "You gain 1 life for each other creature you control.",
            "You gain 1 life for each charge counter on Generic Query Amount.",
            "You gain 1 life for each Aura attached to Generic Query Amount.",
            "You gain that much life.",
            "You gain 1 life for each creature you control, then draw a card.",
            "You may gain 1 life for each creature you control.",
            "Choose one —\n• You gain 1 life for each creature you control.\n"
            "• Draw a card.",
            "Creatures you control have \"Whenever this creature attacks, you "
            "gain 1 life for each artifact you control.\"",
        )
        for index, text in enumerate(excluded):
            with self.subTest(text=text):
                program = self.compile(
                    query_amount_record(text, suffix=172_001_000 + index)
                )
                self.assertFalse(
                    any(
                        _contains_query_amount(ability.effects)
                        for ability in program.abilities
                    )
                )

        valid = PublicQueryAmountSpec(
            quantity=CharacteristicQuantitySpec(
                scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                query=ObjectQuerySpec(
                    zones=("battlefield",), types_all=("creature",)
                ),
            ),
            coefficient=2,
        ).to_dict()
        self.assertEqual(
            valid,
            PublicQueryAmountSpec.from_dict(valid).to_dict(),
        )
        malformed = (
            {**valid, "coefficient": 0},
            {**valid, "unknown": True},
            {
                **valid,
                "quantity": CharacteristicQuantitySpec(
                    scope=CharacteristicQuantityScope.OPPONENT_ZONES,
                    query=ObjectQuerySpec(
                        zones=("battlefield",), types_all=("creature",)
                    ),
                ).to_dict(),
            },
            {
                **valid,
                "quantity": {
                    **valid["quantity"],
                    "query": {
                        **valid["quantity"]["query"],
                        "keywords_all": ["flying"],
                    },
                },
            },
            {
                **valid,
                "quantity": {
                    **valid["quantity"],
                    "query": {
                        **valid["quantity"]["query"],
                        "state_predicate": PermanentStatePredicateSpec(
                            tapped=True
                        ).to_dict(),
                    },
                },
            },
        )
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(PublicQueryAmountError):
                    PublicQueryAmountSpec.from_dict(value)

        for effects in (
            ({"op": "life", "delta": 1},),
            (
                {"op": "life", "delta": valid},
                {"op": "draw", "count": valid},
            ),
        ):
            with self.subTest(effects=effects):
                self.assertEqual(
                    (),
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=None,
                        mechanic_ids=(PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC,),
                    ),
                )

    def test_public_query_amount_compiler_and_resolver_mutants_are_killed(self):
        record = query_amount_record(
            "You gain 1 life for each creature you control.",
            suffix=172_002_000,
        )

        def assert_compiled() -> None:
            program = self.compile(record)
            self.assertEqual((), program.residuals)
            self.assertTrue(
                any(
                    _contains_query_amount(ability.effects)
                    for ability in program.abilities
                )
            )

        assert_compiled()
        with mock.patch(
            "quorune.compiler.effect_template_composition."
            "public_query_effect_amount_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_compiled()


class PublicQueryEffectAmountRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int, *, players: int = 4):
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

    def program(
        self,
        text: str,
        *,
        suffix: int,
        type_line: str = "Sorcery",
        name: str = "Generic Query Amount",
    ):
        programs = generated_programs(
            self.db,
            query_amount_record(
                text,
                suffix=suffix,
                type_line=type_line,
                name=name,
            ),
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        candidates = [program for program in programs if program.effects]
        self.assertEqual(1, len(candidates))
        self.assertEqual("trusted", candidates[0].trust_level)
        return candidates[0]

    @staticmethod
    def token(engine, seat: str, *, name: str, type_line: str):
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": type_line,
                "power": "1",
                "toughness": "1",
            },
            reason="public query amount fixture",
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    @staticmethod
    def stack_program(
        engine,
        program,
        *,
        suffix: int,
        controller: str,
        source=None,
        targets=(),
        kind: str = "triggered_ability",
    ):
        engine.semantics.put(program)
        item = StackItem(
            stack_id=f"query-amount-{suffix}",
            ref=f"S-query-amount-{suffix}",
            kind=kind,
            controller=controller,
            label=program.label,
            source_object_id=(source.object_id if source is not None else None),
            semantic_key=program.key,
            targets=list(targets),
            visibility=list(engine.active_seats),
            context={
                "targets_chosen_at_creation": True,
                **(
                    {"source_logical_object_id": source.logical_object_id}
                    if source is not None
                    else {}
                ),
            },
        )
        engine.state.stack.append(item)
        return item

    @staticmethod
    def resolve(engine, item, program):
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(effect) for effect in program.effects],
            destination=program.destination,
            note=program.notes,
        )

    def test_query_amounts_use_current_layer_five_state_and_locked_controller(self):
        session = self.session(172_003_000)
        engine = session.engine
        for index in range(3):
            self.token(
                engine,
                "A",
                name=f"A Creature {index}",
                type_line="Token Creature — Citizen",
            )
        changed = self.token(
            engine,
            "A",
            name="Layer Four Device",
            type_line="Token Artifact — Device",
        )
        engine.apply_effect(
            {"op": "add_type", "card": changed.ref, "type": "Creature"},
            actor="A",
        )
        self.assertIn("Creature", engine._effective_card_data(changed)["type_line"])
        source = self.token(
            engine,
            "A",
            name="Ability Source",
            type_line="Token Creature — Wizard",
        )
        life = self.program(
            "{T}: You gain 1 life for each creature you control.",
            suffix=172_003_001,
            type_line="Artifact Creature — Wizard",
        )
        item = self.stack_program(
            engine,
            life,
            suffix=172_003_001,
            controller="A",
            source=source,
        )
        self.token(
            engine,
            "A",
            name="Late Resolution Creature",
            type_line="Token Creature — Citizen",
        )
        engine.change_control(source.object_id, "B", reason="locked controller test")
        a_before = engine.state.players["A"].life
        self.resolve(engine, item, life)
        self.assertEqual(a_before + 5, engine.state.players["A"].life)

        b_before = engine.state.players["B"].life
        copied = self.stack_program(
            engine,
            life,
            suffix=172_003_002,
            controller="B",
            kind="copy",
        )
        self.resolve(engine, copied, life)
        self.assertEqual(b_before + 1, engine.state.players["B"].life)

        damage = self.program(
            "Generic Damage deals damage to target player or planeswalker "
            "equal to the number of creatures you control.",
            suffix=172_003_003,
            name="Generic Damage",
        )
        damage_item = self.stack_program(
            engine,
            damage,
            suffix=172_003_003,
            controller="A",
            targets=("B",),
            kind="spell",
        )
        engine.state.damage_prevention_shields.append(
            DamagePreventionShield(
                shield_id="query-amount-prevention",
                source_id="fixture:query-amount-prevention",
                controller="B",
                subject=DamageSubject(ref="B", kind="player", controller="B"),
                mode=PreventionMode.AMOUNT,
                remaining=2,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                created_turn_sequence=engine.state.turn_sequence,
                label="Query amount prevention fixture",
            )
        )
        b_before = engine.state.players["B"].life
        self.resolve(engine, damage_item, damage)
        self.assertEqual(b_before - 3, engine.state.players["B"].life)

    def test_zero_query_amount_and_stale_target_use_existing_target_owner(self):
        session = self.session(172_003_050, players=2)
        engine = session.engine
        target = engine.state.cards[
            engine.state.players["B"].zones["library"][-1]
        ]
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        target.annotations["object_characteristics"] = {
            "type_line": "Creature — Citizen",
            "power": "2",
            "toughness": "2",
        }
        target.attacking = "A"
        program = self.program(
            "Generic Damage deals damage to target attacking creature equal to the "
            "number of artifacts you control.",
            suffix=172_003_051,
            name="Generic Damage",
        )
        zero = self.stack_program(
            engine,
            program,
            suffix=172_003_051,
            controller="A",
            targets=(target.ref,),
            kind="spell",
        )
        self.resolve(engine, zero, program)
        self.assertEqual(0, target.marked_damage)

        stale = self.stack_program(
            engine,
            program,
            suffix=172_003_052,
            controller="A",
            targets=(target.ref,),
            kind="spell",
        )
        engine.move_card(target.object_id, "graveyard", log=False)
        before = authoritative_state_hash(engine.state)
        engine._begin_resolve_item(
            stale,
            [dict(effect) for effect in program.effects],
            program.destination,
            note=program.notes,
        )
        self.assertFalse(any(item.ref == stale.ref for item in engine.state.stack))
        self.assertEqual("graveyard", target.zone)
        self.assertNotEqual(before, authoritative_state_hash(engine.state))

    def test_query_amounts_delegate_to_draw_and_token_owners(self):
        session = self.session(172_003_100, players=2)
        engine = session.engine
        for index in range(2):
            self.token(
                engine,
                "A",
                name=f"Spirit {index}",
                type_line="Token Creature — Spirit",
            )
        draw = self.program(
            "Draw a card for each Spirit you control.",
            suffix=172_003_101,
        )
        draw_item = self.stack_program(
            engine,
            draw,
            suffix=172_003_101,
            controller="A",
            kind="spell",
        )
        hand_before = len(engine.state.players["A"].zones["hand"])
        self.resolve(engine, draw_item, draw)
        self.assertEqual(
            hand_before + 2,
            len(engine.state.players["A"].zones["hand"]),
        )

        for index in range(2):
            self.token(
                engine,
                "A",
                name=f"Goblin {index}",
                type_line="Token Creature — Goblin",
            )
        tokens = self.program(
            "Create X 1/1 red Goblin creature tokens, where X is the "
            "number of Goblins you control.",
            suffix=172_003_102,
        )
        token_item = self.stack_program(
            engine,
            tokens,
            suffix=172_003_102,
            controller="A",
            kind="spell",
        )
        before = sum(
            card.zone == "battlefield"
            and card.controller == "A"
            and "goblin" in engine._type_parts(
                engine._effective_card_data(card)["type_line"]
            )[1]
            for card in engine.state.cards.values()
        )
        self.resolve(engine, token_item, tokens)
        after = sum(
            card.zone == "battlefield"
            and card.controller == "A"
            and "goblin" in engine._type_parts(
                engine._effective_card_data(card)["type_line"]
            )[1]
            for card in engine.state.cards.values()
        )
        self.assertEqual(before + 2, after)

        replacement_source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and card.printed_name == "Stridehangar Automaton"
        )
        engine.move_card(
            replacement_source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        artifact_tokens = self.program(
            "Create X 1/1 colorless Thopter artifact creature tokens, where "
            "X is the number of artifacts you control.",
            suffix=172_003_103,
        )
        artifact_item = self.stack_program(
            engine,
            artifact_tokens,
            suffix=172_003_103,
            controller="A",
            kind="spell",
        )
        thopters_before = sum(
            card.zone == "battlefield" and card.printed_name == "Thopter"
            for card in engine.state.cards.values()
        )
        self.resolve(engine, artifact_item, artifact_tokens)
        thopters_after = sum(
            card.zone == "battlefield" and card.printed_name == "Thopter"
            for card in engine.state.cards.values()
        )
        self.assertEqual(thopters_before + 2, thopters_after)
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "token.create"
        )
        self.assertEqual(1, event.details["base_quantity"])
        self.assertEqual(1, event.details["replacement_count"])

    def test_query_amounts_trigger_uses_normalized_event_and_apnap_owner(self):
        session = self.session(172_003_200)
        engine = session.engine
        record = replace(
            self.db.lookup("Boon Reflection"),
            name="Generic End Step Query",
            oracle_text=(
                "At the beginning of your end step, you lose 2 life for each "
                "card in your hand."
            ),
        )
        result = register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        self.assertGreaterEqual(result["exact_programs_promoted"], 1)
        source = CardInstance(
            object_id="fixture:query-amount-trigger",
            ref="query-amount-trigger",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            zone_timestamp=engine._next_zone_timestamp(),
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[source.object_id] = source
        engine.state.players["A"].zones["battlefield"].append(source.object_id)
        items = collect_trigger_items(
            engine,
            "step.begin",
            {"phase": "ending", "step": "end_step", "player": "A"},
        )
        matching = [
            item
            for item in items
            if item.source_object_id == source.object_id
            and item.context.get("event") == "step.begin"
        ]
        self.assertEqual(1, len(matching))
        enqueue_trigger_batch(engine, matching)
        self.assertEqual(
            ["A", "B", "C", "D"],
            list(engine.state.pending_trigger_batches[0].apnap_order),
        )
        engine._stabilize()
        item = engine.state.stack[-1]
        program = engine.semantics.get(item.semantic_key)
        self.assertIsNotNone(program)
        hand_count = len(engine.state.players["A"].zones["hand"])
        life_before = engine.state.players["A"].life
        self.resolve(engine, item, program)
        self.assertEqual(
            life_before - 2 * hand_count,
            engine.state.players["A"].life,
        )

    def test_query_amounts_life_use_existing_replacement_owner(self):
        session = self.session(172_003_250, players=2)
        engine = session.engine
        record = self.db.lookup("Boon Reflection")
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        source = CardInstance(
            object_id="fixture:query-amount-life-replacement",
            ref="query-amount-life-replacement",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            zone_timestamp=engine._next_zone_timestamp(),
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[source.object_id] = source
        engine.state.players["A"].zones["battlefield"].append(source.object_id)
        for index in range(2):
            self.token(
                engine,
                "A",
                name=f"Life Creature {index}",
                type_line="Token Creature — Citizen",
            )
        program = self.program(
            "You gain 1 life for each creature you control.",
            suffix=172_003_251,
        )
        item = self.stack_program(
            engine,
            program,
            suffix=172_003_251,
            controller="A",
            kind="spell",
        )
        life_before = engine.state.players["A"].life
        self.resolve(engine, item, program)
        self.assertEqual(life_before + 4, engine.state.players["A"].life)
        self.assertTrue(
            any(
                event.code == "replacement.apply"
                and "life.gain.multiplier" in str(event.details.get("effect_id"))
                for event in engine.state.events
            )
        )

    def test_query_amount_four_player_projection_save_load_and_replay(self):
        session = self.session(172_004_000)
        engine = session.engine
        program = self.program(
            "You gain 2 life for each card in your hand.",
            suffix=172_004_001,
        )
        item = self.stack_program(
            engine,
            program,
            suffix=172_004_001,
            controller="A",
            kind="spell",
        )
        hand_refs = {
            engine.state.cards[object_id].ref
            for object_id in engine.state.players["A"].zones["hand"]
        }
        projected = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:B")
        )
        self.assertTrue(all(ref not in projected for ref in hand_refs))
        expected_gain = 2 * len(hand_refs)
        life_before = engine.state.players["A"].life
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for _ in range(20):
            if not engine.state.stack:
                break
            principals = session.pending_principals()
            self.assertTrue(principals)
            result = session.act(principals[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertFalse(engine.state.stack)
        self.assertEqual(
            life_before + expected_gain,
            engine.state.players["A"].life,
        )
        projected = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:B")
        )
        self.assertTrue(all(ref not in projected for ref in hand_refs))

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "public-query-amount-record"
            session.save(record_dir)
            loaded = load_record_state(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertEqual(expected_hash, authoritative_state_hash(loaded))
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_public_query_amount_resolver_mutant_is_killed(self):
        def assert_runtime() -> None:
            session = self.session(172_005_000, players=2)
            engine = session.engine
            for index in range(3):
                self.token(
                    engine,
                    "A",
                    name=f"Mutation Creature {index}",
                    type_line="Token Creature — Citizen",
                )
            program = self.program(
                "You gain 1 life for each creature you control.",
                suffix=172_005_001,
            )
            item = self.stack_program(
                engine,
                program,
                suffix=172_005_001,
                controller="A",
            )
            before = engine.state.players["A"].life
            self.resolve(engine, item, program)
            self.assertEqual(before + 3, engine.state.players["A"].life)

        assert_runtime()
        with mock.patch(
            "quorune.semantic_runtime.values.resolve_public_query_amount",
            return_value=1,
        ):
            with self.assertRaises(AssertionError):
                assert_runtime()

        session = self.session(172_005_002, players=2)
        engine = session.engine
        malformed_program = self.program(
            "You gain 1 life for each creature you control.",
            suffix=172_005_003,
        )
        effect = dict(malformed_program.effects[0])
        effect["delta"] = {**effect["delta"], "coefficient": 0}
        item = self.stack_program(
            engine,
            malformed_program,
            suffix=172_005_003,
            controller="A",
        )
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(SemanticNodeError):
            resolve_semantic_value(engine, effect, item)
        self.assertEqual(before, authoritative_state_hash(engine.state))


if __name__ == "__main__":
    unittest.main()
