from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import keep_all, load_assets, make_session
from quorune.carddb import CardRecord
from quorune.compiled_activated_abilities import compiled_activated_abilities
from quorune.damage import damage_proposal, resolve_damage_batch
from quorune.compiler.token_templates import (
    FIXED_TOKEN_DEFINITION_BATCH_MECHANIC,
    fixed_token_creation_effect_template,
)
from quorune.errors import GameRuleError
from quorune.model import StackItem
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
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
from quorune.semantics import SemanticRegistry


def token_record(
    name: str,
    oracle_text: str,
    suffix: int,
    *,
    type_line: str = "Instant",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=name,
        mana_cost="{2}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
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


class FixedTokenCreationCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_fixed_token_creation_compiles_shared_spell_trigger_and_activation_grammar(
        self,
    ):
        fixtures = (
            (
                token_record(
                    "Fixed Spirit Spell",
                    "Create two 1/1 white Spirit creature tokens with flying.",
                    470001,
                ),
                "spell_ability",
                "create-fixed-creature-token-v2",
                {"combat.block.flying", "token.creation.fixed_definition"},
                "Token Creature — Spirit",
            ),
            (
                token_record(
                    "Fixed Spirit Trigger",
                    "When this creature enters, create a tapped 3/2 red and "
                    "white Spirit creature token.",
                    470002,
                    type_line="Creature — Human",
                ),
                "triggered_ability",
                "create-fixed-creature-token-v2",
                {
                    "token.creation.fixed_definition",
                    "trigger.event.normalized_zone_change",
                    "trigger.placement.apnap",
                },
                "Token Creature — Spirit",
            ),
            (
                token_record(
                    "Fixed Food Activation",
                    "{2}, {T}: Create a Food token.",
                    470003,
                    type_line="Artifact",
                ),
                "activated_ability",
                "create-fixed-food-token-v1",
                {"life.change.effect", "token.creation.fixed_definition"},
                "Token Artifact — Food",
            ),
            (
                token_record(
                    "Fixed Glimmer Spell",
                    "Create a 1/1 white Glimmer enchantment creature token.",
                    470004,
                ),
                "spell_ability",
                "create-fixed-creature-token-v2",
                {"token.creation.fixed_definition"},
                "Token Creature Enchantment — Glimmer",
            ),
            (
                token_record(
                    "Fixed Golem Activation",
                    "{2}, {T}: Create a 3/3 colorless Golem enchantment "
                    "artifact creature token.",
                    470005,
                    type_line="Artifact",
                ),
                "activated_ability",
                "create-fixed-creature-token-v2",
                {"token.creation.fixed_definition"},
                "Token Artifact Creature Enchantment — Golem",
            ),
        )
        for record, kind, template_id, required, token_type_line in fixtures:
            with self.subTest(record=record.name):
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertEqual(template_id, node.template_id)
                self.assertTrue(required.issubset(node.capability_dependencies))
                self.assertEqual(
                    token_type_line,
                    node.effects[0]["characteristics"]["type_line"],
                )
                self.assertEqual(
                    record.oracle_text[node.span.start : node.span.end],
                    node.text,
                )

        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            tuple(record for record, *_ in fixtures),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
        )
        self.assertEqual(5, result["exact_programs_promoted"])
        self.assertEqual(
            {"trusted"}, {program.trust_level for program in registry.programs()}
        )

    def test_fixed_token_creation_rejects_dynamic_copy_attached_and_custom_ability_variants(
        self,
    ):
        unsupported = (
            "Create X 1/1 green Saproling creature tokens.",
            "Create a token that's a copy of target creature you control.",
            "Create a Wicked Role token attached to target creature you control.",
            "Create a 1/1 black Rat creature token with \"This token can't block.\"",
            "Create two 1/1 white Soldier creature tokens that are tapped and attacking.",
            "Create a 1/1 blue and red Otter creature token with prowess.",
            "Create Ashaya, the Awoken World, a legendary 4/4 green Elemental creature token.",
            "Create a tapped Powerstone token.",
            "Create a 3/3 colorless Golem artifact artifact creature token.",
            "Create 0 1/1 green Saproling creature tokens.",
        )
        base = token_record("Unsupported Token Fixture", unsupported[0], 470010)
        for index, text in enumerate(unsupported):
            with self.subTest(text=text):
                self.assertIsNone(fixed_token_creation_effect_template(text))
                ir = compile_oracle_card(
                    replace(
                        base,
                        oracle_id=f"00000000-0000-4000-8000-{470010 + index:012d}",
                        oracle_text=text,
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_enchantment_tokens_and_additional_cost_spell_are_closed(self):
        fixtures = (
            (
                token_record(
                    "Golem Enchantment Fixture",
                    "{2}, {T}: Create a 3/3 colorless Golem enchantment "
                    "artifact creature token.",
                    470006,
                    type_line="Artifact",
                ),
                "Token Artifact Creature Enchantment — Golem",
            ),
            (
                token_record(
                    "Cleric Enchantment Fixture",
                    "{2}, {T}: Create a 2/1 white Cleric enchantment "
                    "creature token.",
                    470007,
                    type_line="Artifact",
                ),
                "Token Creature Enchantment — Cleric",
            ),
            (
                token_record(
                    "Additional Cost Token Fixture",
                    "As an additional cost to cast this spell, sacrifice an "
                    "artifact.\nCreate three 1/1 red Goblin creature tokens.",
                    470008,
                    type_line="Sorcery",
                ),
                "Token Creature — Goblin",
            ),
        )
        for record, token_type_line in fixtures:
            with self.subTest(card_name=record.name):
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                node = next(
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if "create-fixed-creature-token-v2" in str(
                        node.template_id
                    )
                )
                self.assertTrue(node.exact, ir.material_residuals)
                self.assertIn(
                    "token.creation.fixed_definition",
                    node.capability_dependencies,
                )
                self.assertEqual(
                    token_type_line,
                    node.effects[0]["characteristics"]["type_line"],
                )

    def test_fixed_token_capability_shape_rejects_malformed_effects(self):
        template = fixed_token_creation_effect_template(
            "Create two 1/1 white Spirit creature tokens with flying."
        )
        self.assertIsNotNone(template)
        effect = dict(template.effect)
        mechanics = template.mechanics
        self.assertIn(
            "token.creation.fixed_definition",
            capability_dependencies_for_node(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=mechanics,
            ),
        )
        malformed = (
            {**effect, "quantity": 0},
            {**effect, "quantity": True},
            {**effect, "controller": "$target.0"},
            {**effect, "tapped": False},
            {**effect, "unknown": True},
            {
                **effect,
                "characteristics": {
                    **effect["characteristics"],
                    "colors": ["R", "W"],
                },
            },
        )
        for candidate in malformed:
            with self.subTest(effect=candidate):
                self.assertNotIn(
                    "token.creation.fixed_definition",
                    capability_dependencies_for_node(
                        effects=(candidate,),
                        target_schema=None,
                        mechanic_ids=mechanics,
                    ),
                )
        self.assertNotIn(
            "token.creation.fixed_definition",
            capability_dependencies_for_node(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=("cr-111-tokens",),
            ),
        )

    def test_fixed_token_compiler_mutation_is_killed(self):
        record = token_record(
            "Fixed Token Mutation Fixture",
            "Create a 1/1 green Saproling creature token.",
            470020,
        )
        self.assertTrue(
            generated_programs(
                self.db,
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        )
        with patch(
            "quorune.oracle_ir.fixed_token_creation_effect_template",
            return_value=None,
        ):
            ir = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertEqual("unresolved", ir.status)
        self.assertEqual("spell_effect", ir.material_residuals[0].kind)

    def test_fixed_token_batches_and_clues_promote_the_bounded_real_cards(self):
        exact_names = (
            "Mascot Exhibition",
            "Forbidden Friendship",
            "Bestial Menace",
            "Sokka's Sword Training",
            "Cunning Maneuver",
            "Forecasting Fortune Teller",
            "Knowledge Seeker",
        )
        for name in exact_names:
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                self.assertFalse(ir.material_residuals)
                programs = generated_programs(
                    self.db,
                    record,
                    trust_level="trusted",
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertTrue(programs)
                self.assertEqual(
                    {"trusted"},
                    {program.trust_level for program in programs},
                )

        partial = {
            "Devouring Sugarmaw // Have for Dinner": "trigger",
            "True Ancestry": "spell_effect",
        }
        for name, residual_kind in partial.items():
            with self.subTest(name=name):
                ir = compile_oracle_card(
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("partial", ir.status)
                self.assertIn(
                    residual_kind,
                    {residual.kind for residual in ir.material_residuals},
                )
                token_nodes = [
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if "token.creation.fixed_definition"
                    in node.capability_dependencies
                ]
                self.assertEqual(1, len(token_nodes))

    def test_fixed_token_batch_and_clue_shapes_are_exact_and_fail_closed(self):
        batch = fixed_token_creation_effect_template(
            "Create a 1/1 red Dinosaur creature token with haste and a "
            "1/1 white Human Soldier creature token."
        )
        self.assertIsNotNone(batch)
        self.assertEqual(
            "create-fixed-token-definition-batch-v1", batch.template_id
        )
        self.assertEqual("create_token_batch", batch.effect["op"])
        self.assertEqual(2, len(batch.effect["tokens"]))
        self.assertIn(FIXED_TOKEN_DEFINITION_BATCH_MECHANIC, batch.mechanics)
        self.assertEqual(
            {
                "activation.tap_untap_cost.haste",
                "combat.attack.haste",
                "token.creation.fixed_definition",
            },
            set(
                capability_dependencies_for_node(
                    effects=(batch.effect,),
                    target_schema=None,
                    mechanic_ids=batch.mechanics,
                )
            ),
        )

        clue = fixed_token_creation_effect_template("Create a Clue token.")
        self.assertIsNotNone(clue)
        self.assertEqual(
            {
                "token.creation.fixed_definition",
                "zone.draw.library_to_hand",
            },
            set(
                capability_dependencies_for_node(
                    effects=(clue.effect,),
                    target_schema=None,
                    mechanic_ids=clue.mechanics,
                )
            ),
        )

        malformed = (
            {**batch.effect, "tokens": batch.effect["tokens"][:1]},
            {**batch.effect, "tokens": [*batch.effect["tokens"], {}, {}]},
            {**batch.effect, "tokens": tuple(batch.effect["tokens"])},
            {
                **batch.effect,
                "tokens": [
                    {**batch.effect["tokens"][0], "quantity": 0},
                    batch.effect["tokens"][1],
                ],
            },
            {**batch.effect, "unknown": True},
        )
        for effect in malformed:
            with self.subTest(effect=effect):
                self.assertNotIn(
                    "token.creation.fixed_definition",
                    capability_dependencies_for_node(
                        effects=(effect,),
                        target_schema=None,
                        mechanic_ids=batch.mechanics,
                    ),
                )
        self.assertNotIn(
            "token.creation.fixed_definition",
            capability_dependencies_for_node(
                effects=(batch.effect,),
                target_schema=None,
                mechanic_ids=tuple(
                    mechanic
                    for mechanic in batch.mechanics
                    if mechanic != FIXED_TOKEN_DEFINITION_BATCH_MECHANIC
                ),
            ),
        )

        unsupported = (
            "Create X 1/1 green Saproling creature tokens and a Food token.",
            "Create a token that's a copy of target creature and a Food token.",
            "Create a Wicked Role token attached to target creature and a "
            "Treasure token.",
            "Create a 1/1 black Rat creature token with \"This token can't "
            "block.\" and a Food token.",
            "Create a 1/1 white Soldier creature token, a 1/1 blue Bird "
            "creature token with flying, a 1/1 black Rat creature token, "
            "and a 1/1 red Goblin creature token.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(fixed_token_creation_effect_template(text))

    def test_fixed_token_batch_compiler_mutation_is_killed(self):
        record = self.db.lookup("Mascot Exhibition")
        self.assertEqual(
            "exact",
            compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            ).status,
        )
        with patch(
            "quorune.oracle_ir.fixed_token_creation_effect_template",
            return_value=None,
        ):
            mutated = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertEqual("unresolved", mutated.status)
        self.assertTrue(mutated.material_residuals)


class FixedTokenCreationRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(
        self,
        seed: int,
        *,
        players: int = 2,
        card_name: str | None = None,
    ):
        deck = self.mishra
        if card_name is not None:
            deck = copy.deepcopy(self.mishra)
            next(
                entry for entry in deck.entries if entry.board == "mainboard"
            ).name = card_name
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
    def _prepare_priority(session, source):
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            tapped=False,
            log=False,
        )
        engine.state.players["A"].mana_pool["W"] = 3
        engine._grant_priority("A")
        engine.pump()

    @staticmethod
    def _resolve_registered_program(engine, program, suffix: int):
        item = StackItem(
            stack_id=f"registered-token-{suffix}",
            ref=f"S-registered-token-{suffix}",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            visibility=list(engine.active_seats),
        )
        engine.state.stack.append(item)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(effect) for effect in program.effects],
            destination=program.destination,
            note=program.notes,
        )

    @staticmethod
    def card(engine, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == name
        )

    def _compiled_program(self, text: str, suffix: int):
        record = token_record(
            f"Compiled Token Fixture {suffix}",
            text,
            suffix,
        )
        programs = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual(1, len(programs))
        program = programs[0]
        self.assertEqual("trusted", program.trust_level)
        self.assertFalse(program.requires_arbiter)
        return program

    def _stack_compiled_program(self, engine, text: str, suffix: int):
        program = self._compiled_program(text, suffix)
        engine.semantics.put(program)
        item = StackItem(
            stack_id=f"compiled-token-{suffix}",
            ref=f"S-compiled-token-{suffix}",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            visibility=list(engine.active_seats),
        )
        engine.state.stack.append(item)
        return item, program

    def _resolve_compiled_program(self, engine, text: str, suffix: int):
        item, program = self._stack_compiled_program(engine, text, suffix)
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(effect) for effect in program.effects],
            destination=program.destination,
            note=program.notes,
        )
        return program

    @staticmethod
    def _pass_until(session, predicate, *, limit: int = 24):
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Resolution stopped without a decision")
            result = session.act(
                principals[0], {"action_id": "pass"}
            )
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Resolution did not reach the expected state")

    def test_compiled_fixed_token_effect_uses_canonical_creation_owner(self):
        session = self.session(470101)
        engine = session.engine
        before = set(engine.state.cards)
        self._resolve_compiled_program(
            engine,
            "Create two 1/1 white Spirit creature tokens with flying.",
            470101,
        )
        spirits = [
            card
            for object_id, card in engine.state.cards.items()
            if object_id not in before and card.printed_name == "Spirit"
        ]
        self.assertEqual(2, len(spirits))
        self.assertEqual(1, len({card.zone_timestamp for card in spirits}))
        self.assertTrue(all(card.is_token for card in spirits))
        self.assertTrue(
            all(
                engine._effective_card_data(card)["keywords"] == ["Flying"]
                for card in spirits
            )
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "token.create"
        )
        self.assertEqual(2, event.details["base_quantity"])

        self._resolve_compiled_program(
            engine,
            "Create a Food token.",
            470102,
        )
        food = next(
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Food"
        )
        data = engine._effective_card_data(food)
        self.assertEqual("Artifact — Food", data["type_line"])
        self.assertEqual(1, len(data["activated_abilities"]))
        self.assertEqual("", data["oracle_text"])
        self.assertIn("You gain 3 life", data["display_oracle_text"])

        self._resolve_compiled_program(
            engine,
            "Create a 3/3 colorless Golem enchantment artifact creature token.",
            470103,
        )
        golem = next(
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Golem"
        )
        golem_data = engine._effective_card_data(golem)
        self.assertEqual(
            "Artifact Creature Enchantment — Golem",
            golem_data["type_line"],
        )
        self.assertEqual("3", golem_data["power"])
        self.assertEqual("3", golem_data["toughness"])

    def test_compiled_fixed_token_batch_is_one_simultaneous_transaction(self):
        session = self.session(470130)
        engine = session.engine
        before = set(engine.state.cards)
        self._resolve_compiled_program(
            engine,
            "Create a 1/1 red Dinosaur creature token with haste and a "
            "1/1 white Human Soldier creature token.",
            470130,
        )
        created = [
            card
            for object_id, card in engine.state.cards.items()
            if object_id not in before
        ]
        self.assertEqual(
            {"Dinosaur", "Human Soldier"},
            {card.printed_name for card in created},
        )
        self.assertEqual(1, len({card.zone_timestamp for card in created}))
        dinosaur = next(
            card for card in created if card.printed_name == "Dinosaur"
        )
        soldier = next(
            card for card in created if card.printed_name == "Human Soldier"
        )
        self.assertEqual(
            ["Haste"], engine._effective_card_data(dinosaur)["keywords"]
        )
        self.assertEqual(
            [], engine._effective_card_data(soldier)["keywords"]
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "token.create"
        )
        self.assertEqual(2, event.details["base_quantity"])
        self.assertEqual(0, event.details["replacement_count"])
        self.assertEqual(
            {card.ref for card in created}, set(event.details["objects"])
        )

    def test_fixed_token_batch_replacement_order_is_private_and_shared(self):
        session = self.session(470131, players=4)
        engine = session.engine
        self._install_replacement_sources(
            engine, ("Stridehangar Automaton", "Worldwalker Helm")
        )
        item, program = self._stack_compiled_program(
            engine,
            "Create a 1/1 white Human creature token and a Food token.",
            470131,
        )
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(effect) for effect in program.effects],
            destination=program.destination,
            note=program.notes,
        )

        decision = engine.state.pending_decision
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(["A"], decision.actors)
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        before = authoritative_state_hash(engine.state)
        rejected = engine.try_submit(
            token=engine.permissions.capability_for("pilot:A").token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": "unknown-replacement"},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        result = engine.submit(
            token=engine.permissions.capability_for("pilot:A").token,
            principal="pilot:A",
            action="choose",
            payload={
                "replacement": projected["ctx"]["options"][0]["id"]
            },
        )
        self.assertTrue(result.ok, result.summary)
        tokens = [
            card
            for card in engine.state.cards.values()
            if card.is_token and card.zone == "battlefield"
        ]
        self.assertEqual(
            {"Food", "Human", "Map", "Thopter"},
            {card.printed_name for card in tokens},
        )
        self.assertEqual(1, len({card.zone_timestamp for card in tokens}))
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "token.create"
        )
        self.assertEqual(2, event.details["base_quantity"])
        self.assertEqual(2, event.details["replacement_count"])

    def test_fixed_token_batch_malformed_effect_rolls_back_atomically(self):
        session = self.session(470132)
        engine = session.engine
        template = fixed_token_creation_effect_template(
            "Create a 1/1 red Dinosaur creature token with haste and a "
            "1/1 white Human Soldier creature token."
        )
        self.assertIsNotNone(template)
        malformed = copy.deepcopy(template.effect)
        malformed["tokens"][1]["characteristics"][
            "activated_ability_profile"
        ] = "unsupported-profile"
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine.apply_effect(malformed, actor="A")
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_compiled_clue_activation_draws_privately_and_replays_exactly(self):
        session = self.session(470133)
        engine = session.engine
        self._resolve_compiled_program(
            engine, "Create a Clue token.", 470133
        )
        clue = next(
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Clue"
        )
        abilities = compiled_activated_abilities(engine, clue)
        self.assertEqual(1, len(abilities))
        ability = abilities[0]
        self.assertEqual("builtin:draw:1", ability.builtin_semantic_key)
        self.assertTrue(ability.sacrifice_source)
        self.assertFalse(ability.tap_source)

        engine.state.players["A"].mana_pool["C"] = 2
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine.pump()
        action_id = f"activate:{clue.ref}:{ability.ability_id}"
        packet = session.packet("pilot:A", full=True)
        offered = {
            action["id"]
            for action in packet["decision"]["ctx"]["legal"]["actions"]
        }
        self.assertIn(action_id, offered)
        top = engine.state.players["A"].zones["library"][-1]
        top_ref = engine.state.cards[top].ref
        hand_before = len(engine.state.players["A"].zones["hand"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("outside", clue.zone)
        self.assertEqual(0, engine.state.players["A"].mana_pool["C"])
        self._pass_until(session, lambda: not engine.state.stack)
        self.assertEqual(
            hand_before + 1, len(engine.state.players["A"].zones["hand"])
        )
        self.assertIn(top, engine.state.players["A"].zones["hand"])
        self.assertNotIn(
            "hand",
            session.packet("pilot:B", full=True)["state"]["players"]["A"],
        )
        self.assertNotIn(
            top_ref,
            json.dumps(session.packet("pilot:B", full=True)),
        )

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "clue-token-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def _install_replacement_sources(self, engine, names):
        for name in names:
            source = self.card(engine, name)
            engine.move_card(source.object_id, "battlefield", controller="A")

    def test_compiled_fixed_token_effect_suspends_for_replacement_order(self):
        session = self.session(470102, players=4)
        engine = session.engine
        self._install_replacement_sources(
            engine, ("Stridehangar Automaton", "Worldwalker Helm")
        )
        item, program = self._stack_compiled_program(
            engine,
            "Create a Treasure token.",
            470103,
        )
        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(effect) for effect in program.effects],
            destination=program.destination,
            note=program.notes,
        )

        decision = engine.state.pending_decision
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(["A"], decision.actors)
        projector = StateProjector(self.db, engine.state)
        projected = projector._decision("pilot:A")
        for seat in ("B", "C", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        serialized = json.dumps(projected)
        self.assertNotIn("replacement_batch", serialized)
        self.assertNotIn("replacement_effects", serialized)
        before = authoritative_state_hash(engine.state)
        capability = engine.permissions.capability_for("pilot:A")
        rejected = engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": "unknown-replacement"},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        capability = engine.permissions.capability_for("pilot:A")
        result = engine.submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": projected["ctx"]["options"][0]["id"]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            {"Map", "Thopter", "Treasure"},
            {
                card.printed_name
                for card in engine.state.cards.values()
                if card.is_token and card.zone == "battlefield"
            },
        )

    def test_compiled_fixed_token_effect_replays_exactly(self):
        session = self.session(470103)
        engine = session.engine
        self._install_replacement_sources(engine, ("Worldwalker Helm",))
        self._stack_compiled_program(
            engine,
            "Create a Treasure token.",
            470104,
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
        self.assertEqual(
            {"Map": 1, "Treasure": 1},
            {
                name: sum(
                    card.is_token
                    and card.zone == "battlefield"
                    and card.printed_name == name
                    for card in engine.state.cards.values()
                )
                for name in ("Map", "Treasure")
            },
        )

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-token-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])

    def test_selected_soldier_cost_resolves_combat_prevention_and_replays(
        self,
    ):
        session = self.session(
            470120,
            card_name="Knight-Captain of Eos",
        )
        engine = session.engine
        source = self.card(engine, "Knight-Captain of Eos")
        engine.create_token(
            "A",
            name="Soldier",
            quantity=1,
            characteristics={
                "type_line": "Token Creature — Soldier",
                "colors": ["W"],
                "power": "1",
                "toughness": "1",
            },
            reason="typed prevention interaction fixture",
        )
        soldier = next(
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.controller == "A"
            and card.printed_name == "Soldier"
        )
        combat_source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B" and card.printed_name == "Seedborn Muse"
        )
        engine.move_card(
            combat_source.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        soldiers_before = sum(
            card.is_token and card.printed_name == "Soldier"
            for card in engine.state.cards.values()
        )
        self._prepare_priority(session, source)

        programs = engine.semantics.programs_for_oracle(source.oracle_id)
        token_program = next(
            program
            for program in programs
            if program.effects
            and program.effects[0].get("op") == "create_token"
        )
        self.assertEqual("trusted", token_program.trust_level)
        prevention_carrier = next(
            program
            for program in programs
            if program.event == "activate"
        )
        self.assertEqual("trusted", prevention_carrier.trust_level)
        self.assertEqual(
            [
                {
                    "op": "create_damage_prevention_shield",
                    "source": "$source",
                    "subject": "*",
                    "mode": "all",
                    "duration": "until_end_of_turn",
                    "damage_kind": "combat",
                }
            ],
            prevention_carrier.effects,
        )
        self.assertEqual(
            {
                "activation.selected_zone_change.fixed",
                "damage.prevention.persistent_amount",
            },
            set(prevention_carrier.capability_dependencies),
        )

        prevention = next(
            ability
            for ability in compiled_activated_abilities(engine, source)
            if "Prevent all combat damage" in ability.effect_text
        )
        self.assertEqual(
            ("payable", None),
            engine._ability_availability("A", source, prevention),
        )
        action_id = f"activate:{source.ref}:{prevention.ability_id}"
        packet = session.packet("pilot:A", full=True)
        action = next(
            action
            for action in packet["decision"]["ctx"]["legal"]["actions"]
            if action["id"] == action_id
        )
        self.assertEqual(
            [soldier.ref],
            action["cost_summary"]["choose_cost"][0]["legal_refs"],
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        activated = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [soldier.ref]},
        )
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual("outside", soldier.zone)
        self.assertTrue(engine.state.stack)
        self._pass_until(session, lambda: not engine.state.stack)
        self.assertEqual(1, len(engine.state.damage_prevention_shields))
        shield = engine.state.damage_prevention_shields[0]
        self.assertEqual("combat", shield.damage_kind.value)
        self.assertEqual("all", shield.mode.value)

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "knight-captain-prevention"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        life_before = engine.state.players["A"].life
        damage = resolve_damage_batch(
            engine,
            (
                damage_proposal(
                    engine,
                    proposal_id="knight-captain-combat-witness",
                    actor="B",
                    source_ref=combat_source.ref,
                    target="A",
                    amount=3,
                    combat=True,
                    reason="Knight-Captain prevention witness",
                ),
            ),
        )
        self.assertEqual(0, damage.dealt_amount)
        self.assertEqual(3, damage.events[0].prevented_amount)
        self.assertEqual(life_before, engine.state.players["A"].life)

        self._resolve_registered_program(engine, token_program, 470120)
        self.assertEqual(
            soldiers_before + 2,
            sum(
                card.is_token and card.printed_name == "Soldier"
                for card in engine.state.cards.values()
            ),
        )

    def test_replacement_residuals_are_not_admitted_beside_trusted_token_trigger(
        self,
    ):
        record = self.db.lookup("Topography Tracker")
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        residual = next(
            row
            for row in ir.material_residuals
            if row.kind == "replacement_effect"
        )
        self.assertEqual(
            "replacement/prevention ordering is not compiled",
            residual.reason,
        )
        self.assertTrue(
            {
                "replacement applicability",
                "self-replacement and prevention ordering",
            }.issubset(residual.blockers)
        )

        session = self.session(
            470121,
            card_name="Topography Tracker",
        )
        engine = session.engine
        source = self.card(engine, "Topography Tracker")
        self._prepare_priority(session, source)
        programs = engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertEqual("create_token", programs[0].effects[0]["op"])
        self.assertFalse(programs[0].handlers)
        self.assertFalse(
            engine.semantics.runtime_handler_programs_for_oracle(
                source.oracle_id,
                active_zone="battlefield",
                event="replace",
            )
        )

        self._resolve_registered_program(engine, programs[0], 470121)
        maps = [
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Map"
        ]
        self.assertEqual(1, len(maps))
        abilities = engine._effective_card_data(maps[0])["activated_abilities"]
        self.assertEqual(1, len(abilities))
        self.assertIn(
            "explores",
            abilities[0]["effect_text"],
        )


if __name__ == "__main__":
    unittest.main()
