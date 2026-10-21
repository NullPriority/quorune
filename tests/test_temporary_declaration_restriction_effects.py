from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from common import DB_PATH, ROOT, keep_all, load_assets, make_session
from quorune.ability_fragments import ability_fragment_to_dict
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.temporary_declaration_templates import (
    ActivatedTemporaryDeclarationRestrictionTemplate,
    activated_temporary_declaration_restriction_effect_template,
    temporary_declaration_restriction_effect_template,
)
from quorune.continuous_effect_model import ContinuousOperation, Layer
from quorune.continuous_effect_state import (
    create_resolution_continuous_effect,
    expire_end_of_turn_continuous_effects,
    ResolutionEffectSource,
)
from quorune.declaration_rule_effects import (
    ResolutionDeclarationRuleEffect,
)
from quorune.engine import TURN_STEPS
from quorune.errors import GameRuleError
from quorune.model import CombatState, GameState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import load_default_capability_registry
from unittest.mock import patch
from quorune.rules.temporary_declaration_restrictions import (
    commit_temporary_declaration_restriction,
    temporary_declaration_restriction,
)


class ActivatedTemporaryDeclarationRestrictionTemplateTests(unittest.TestCase):
    def test_closed_activated_temporary_declaration_restrictions_compile(self):
        cases = {
            "Target creature can't attack this turn.": "cant_attack",
            "Target creature can't block this turn.": "cant_block",
            "Target creature can't attack or block this turn.": (
                "cant_attack_or_block"
            ),
            "Target creature can't be blocked this turn.": "unblockable",
        }
        for text, restriction in cases.items():
            with self.subTest(text=text):
                template = (
                    activated_temporary_declaration_restriction_effect_template(
                        text
                    )
                )
                self.assertIsNotNone(template)
                self.assertEqual(restriction, template.restriction)
                self.assertEqual(
                    (
                        {
                            "op": (
                                "grant_declaration_restriction_until_end_of_turn"
                            ),
                            "card": "$target.0",
                            "restriction": restriction,
                        },
                    ),
                    template.effects,
                )
                self.assertEqual(
                    {
                        "zones": ["battlefield"],
                        "categories": ["permanent"],
                        "types_any": ["creature"],
                        "count": 1,
                    },
                    template.target_schema,
                )

    def test_source_and_creature_subtype_subjects_compile_closed_shapes(self):
        source = activated_temporary_declaration_restriction_effect_template(
            "This creature can't be blocked this turn.",
            card_name="Fixture",
        )
        self.assertIsNotNone(source)
        self.assertEqual("$source", source.card_reference)
        self.assertIsNone(source.target_schema)
        self.assertEqual(
            "temporary-source-unblockable-eot-v1",
            source.template_id,
        )

        named_source = (
            activated_temporary_declaration_restriction_effect_template(
                "Fixture can't attack this turn.",
                card_name="Fixture",
            )
        )
        self.assertIsNotNone(named_source)
        self.assertEqual("$source", named_source.card_reference)
        self.assertEqual("cant_attack", named_source.restriction)

        subtype = temporary_declaration_restriction_effect_template(
            "Target Merfolk can't be blocked this turn.",
            card_name="Fixture",
            allow_source=False,
        )
        self.assertIsNotNone(subtype)
        self.assertEqual("merfolk", subtype.creature_subtype)
        self.assertEqual(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "count": 1,
                "subtypes_any": ["merfolk"],
            },
            subtype.target_schema,
        )

    def test_open_temporary_declaration_restriction_grammar_stays_residual(self):
        for text in (
            "Target creature can't block this creature this turn.",
            "Target creature can't be blocked except by Walls this turn.",
            "Target creature can't block this turn and you gain 1 life.",
            "Up to X target creatures can't block this turn.",
            "Another target creature can't block this creature this turn.",
            "Target creature can't block next turn.",
            "Target creature can block this turn.",
            "Target Nonsense can't be blocked this turn.",
            "Another creature can't be blocked this turn.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    activated_temporary_declaration_restriction_effect_template(
                        text
                    )
                )
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            ActivatedTemporaryDeclarationRestrictionTemplate("arbitrary")


class ActivatedTemporaryDeclarationRestrictionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.base = cls.db.lookup("Thundersong Trumpeter")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def compile(self, oracle_text: str, *, type_line: str = "Creature — Human"):
        record = replace(
            self.base,
            name="Fixture",
            oracle_text=oracle_text,
            type_line=type_line,
            keywords=(),
        )
        return compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_activated_only_lowering_uses_precise_shared_capabilities(self):
        cases = {
            "{T}: Target creature can't attack this turn.": "cant-attack",
            "{1}{R}: Target creature can't block this turn.": "cant-block",
            "{T}: Target creature can't attack or block this turn.": (
                "cant-attack-or-block"
            ),
            "{2}{U}: Target creature can't be blocked this turn.": "unblockable",
        }
        for oracle_text, template_suffix in cases.items():
            with self.subTest(oracle_text=oracle_text):
                ir = self.compile(oracle_text)
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(
                    f"activated-target-{template_suffix}-eot-v1",
                    node.template_id,
                )
                self.assertEqual(
                    {
                        "combat.declaration.typed_components",
                        (
                            "continuous.resolution."
                            "declaration_rules_until_end_of_turn"
                        ),
                        "target.revalidate_resolution",
                    },
                    set(node.capability_dependencies),
                )

    def test_spell_triggered_source_and_subtype_contexts_share_owner(self):
        cases = (
            (
                "Target creature can't block this turn.",
                "Instant",
                "activated-target-cant-block-eot-v1",
                True,
            ),
            (
                "When this creature enters, target creature can't block this "
                "turn.",
                "Creature — Human",
                "activated-target-cant-block-eot-v1",
                True,
            ),
            (
                "{2}{U}: This creature can't be blocked this turn.",
                "Creature — Serpent",
                "temporary-source-unblockable-eot-v1",
                False,
            ),
            (
                "{U}, {T}: Target Merfolk can't be blocked this turn.",
                "Creature — Merfolk Wizard",
                "temporary-target-merfolk-unblockable-eot-v1",
                True,
            ),
        )
        for oracle_text, type_line, template_id, targeted in cases:
            with self.subTest(oracle_text=oracle_text):
                ir = self.compile(oracle_text, type_line=type_line)
                matching = [
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id == template_id
                ]
                self.assertEqual("exact", ir.status)
                self.assertEqual(1, len(matching))
                expected = {
                    "combat.declaration.typed_components",
                    (
                        "continuous.resolution."
                        "declaration_rules_until_end_of_turn"
                    ),
                }
                if targeted:
                    expected.add("target.revalidate_resolution")
                self.assertTrue(
                    expected.issubset(
                        set(matching[0].capability_dependencies)
                    ),
                    matching[0].capability_dependencies,
                )

    def test_open_forms_do_not_enter_the_closed_family(self):
        for oracle_text, type_line in (
            (
                "{R}: Target creature can't block this creature this turn.",
                "Creature — Human",
            ),
            (
                "{R}: Target creature can't be blocked this turn except by "
                "artifact creatures.",
                "Creature — Human",
            ),
        ):
            with self.subTest(oracle_text=oracle_text):
                ir = self.compile(oracle_text, type_line=type_line)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(
                    all(
                        node.template_id is None
                        or not node.template_id.startswith(
                            ("activated-target-", "temporary-")
                        )
                        for node in ir.faces[0].nodes
                    )
                )

    def test_selected_exact_cards_promote_without_absorbing_exclusions(self):
        for name in (
            "Hall Monitor",
            "Martyred Rusalka",
            "Thundersong Trumpeter",
            "Amphin Pathmage",
            "Firefright Mage",
            "Joven's Tools",
            "Tower of Coireall",
        ):
            with self.subTest(name=name):
                ir = compile_oracle_card(
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
        for name in (
            "Duct Crawler",
        ):
            with self.subTest(name=name):
                ir = compile_oracle_card(
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)


class TemporaryDeclarationRestrictionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session_with_card(
        self,
        card_name: str,
        *,
        seed: int,
        players: int = 2,
    ):
        deck = copy.deepcopy(self.mishra)
        replaceable = next(
            entry for entry in deck.entries if entry.board == "mainboard"
        )
        replaceable.name = card_name
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
    def creature(
        engine,
        seat: str,
        name: str,
        *,
        static_restriction: str | None = None,
    ):
        fragments = (
            [
                ability_fragment_to_dict(
                    temporary_declaration_restriction(
                        static_restriction
                    )
                )
            ]
            if static_restriction is not None
            else []
        )
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": "Token Creature — Test",
                "oracle_text": "",
                "ability_fragments": fragments,
                "power": "2",
                "toughness": "2",
            },
            temporary_keywords=("Haste",),
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    @staticmethod
    def pass_stack(session):
        while session.state.stack:
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Stack resolution stopped without priority")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(
                    f"{result.summary}: "
                    f"{session.packet(principals[0], full=True).get('decision')}"
                )

    @staticmethod
    def prepare_trumpeter_activation(session, target):
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and card.printed_name == "Thundersong Trumpeter"
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            tapped=False,
            log=False,
        )
        engine.state.players["A"].turns_begun = 1
        source.acquired_control_turn_count = 0
        engine._grant_priority("A")
        engine.pump()
        packet = session.packet("pilot:A", full=True)
        action = next(
            row
            for row in packet["decision"]["ctx"]["legal"]["actions"]
            if row["id"] == f"activate:{source.ref}:ab1"
        )
        if target.ref not in action["target_schema"]["legal_refs"]:
            raise AssertionError("Prepared restriction target is not legal")
        return source, action

    def assert_replays(self, session):
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "temporary-declaration-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_resolved_rule_survives_ability_removal_while_static_rule_does_not(
        self,
    ):
        session = self.session_with_card(
            "Thundersong Trumpeter",
            seed=50811501,
        )
        engine = session.engine
        target = self.creature(engine, "B", "Restricted Blocker")
        static_target = self.creature(
            engine,
            "B",
            "Static Restricted Blocker",
            static_restriction="cant_block",
        )
        attacker = self.creature(engine, "A", "Attacker")
        _source, action = self.prepare_trumpeter_activation(
            session,
            target,
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {"action_id": action["id"], "targets": [target.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        self.pass_stack(session)

        self.assertEqual((), engine._effective_ability_fragments(target))
        declaration_effects = [
            effect
            for effect in engine.state.continuous_effects
            if isinstance(effect, ResolutionDeclarationRuleEffect)
        ]
        self.assertEqual(1, len(declaration_effects))
        self.assertEqual(
            (
                False,
                "declaration_restriction:intrinsic-attack-block-prohibition-v1",
            ),
            engine._can_block(attacker, target),
        )
        self.assertEqual(
            (
                False,
                "declaration_restriction:intrinsic-block-prohibition-v1",
            ),
            engine._can_block(attacker, static_target),
        )
        restored = GameState.from_dict(engine.state.to_dict())
        self.assertEqual(
            declaration_effects[0],
            next(
                effect
                for effect in restored.continuous_effects
                if isinstance(effect, ResolutionDeclarationRuleEffect)
            ),
        )
        self.assert_replays(session)

        removed = create_resolution_continuous_effect(
            engine,
            source=ResolutionEffectSource(stack_ref="test:remove-all-abilities"),
            targets=(target, static_target),
            layer=Layer.ABILITY,
            sublayer="6",
            operations=(ContinuousOperation("remove_all_abilities"),),
        )
        self.assertIsNotNone(removed)
        self.assertEqual((), engine._effective_ability_fragments(target))
        self.assertEqual((), engine._effective_ability_fragments(static_target))
        self.assertFalse(engine._can_block(attacker, target)[0])
        self.assertEqual(
            (True, None),
            engine._can_block(attacker, static_target),
        )

    def test_all_four_restrictions_change_declaration_behavior_and_expire(self):
        session = self.session_with_card("Thundersong Trumpeter", seed=50811502)
        engine = session.engine
        cant_attack = self.creature(engine, "A", "Cannot Attack")
        cant_block = self.creature(engine, "B", "Cannot Block")
        both = self.creature(engine, "A", "Cannot Attack Or Block")
        unblockable = self.creature(engine, "A", "Unblockable")
        free_attacker = self.creature(engine, "A", "Free Attacker")
        free_blocker = self.creature(engine, "B", "Free Blocker")
        for card, restriction in (
            (cant_attack, "cant_attack"),
            (cant_block, "cant_block"),
            (both, "cant_attack_or_block"),
            (unblockable, "unblockable"),
        ):
            engine.apply_effect(
                {
                    "op": "grant_declaration_restriction_until_end_of_turn",
                    "card": card.ref,
                    "restriction": restriction,
                },
                actor="A",
            )

        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        engine.state.pending_decision = None
        engine._issue_attackers()
        domains = engine.state.pending_decision.payload_by_actor["A"][
            "declaration_constraints"
        ]["domains"]
        self.assertNotIn(cant_attack.ref, domains)
        self.assertNotIn(both.ref, domains)
        self.assertIn(unblockable.ref, domains)
        self.assertIn(free_attacker.ref, domains)
        self.assertEqual(
            (
                False,
                "declaration_restriction:intrinsic-block-prohibition-v1",
            ),
            engine._can_block(free_attacker, cant_block),
        )
        self.assertEqual(
            (
                False,
                (
                    "declaration_restriction:"
                    "intrinsic-attack-block-prohibition-v1"
                ),
            ),
            engine._can_block(free_attacker, both),
        )
        self.assertEqual(
            (
                False,
                "declaration_restriction:intrinsic-unblockable-v1",
            ),
            engine._can_block(unblockable, free_blocker),
        )

        self.assertEqual(4, expire_end_of_turn_continuous_effects(engine.state))
        engine.state.pending_decision = None
        engine._issue_attackers()
        expired_domains = engine.state.pending_decision.payload_by_actor["A"][
            "declaration_constraints"
        ]["domains"]
        self.assertIn(cant_attack.ref, expired_domains)
        self.assertIn(both.ref, expired_domains)
        self.assertEqual((True, None), engine._can_block(free_attacker, cant_block))
        self.assertEqual((True, None), engine._can_block(free_attacker, both))
        self.assertEqual((True, None), engine._can_block(unblockable, free_blocker))

    def test_compiled_source_activation_locks_rule_to_source_incarnation(self):
        session = self.session_with_card("Agent of Horizons", seed=50811508)
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Agent of Horizons"
        )
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            tapped=False,
            log=False,
        )
        blocker = self.creature(engine, "B", "Ordinary Blocker")
        engine.state.players["A"].mana_pool.update({"C": 2, "U": 1})
        engine.state.players["A"].turns_begun = 1
        source.acquired_control_turn_count = 0
        engine._grant_priority("A")
        engine.pump()
        packet = session.packet("pilot:A", full=True)
        action = next(
            row
            for row in packet["decision"]["ctx"]["legal"]["actions"]
            if row["id"].startswith(f"activate:{source.ref}:")
        )

        result = session.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.pass_stack(session)

        self.assertEqual(
            (
                False,
                "declaration_restriction:intrinsic-unblockable-v1",
            ),
            engine._can_block(source, blocker),
        )
        effect = next(
            value
            for value in engine.state.continuous_effects
            if isinstance(value, ResolutionDeclarationRuleEffect)
        )
        self.assertEqual(
            (source.object_id, source.logical_object_id),
            (
                effect.locked_objects[0].object_id,
                effect.locked_objects[0].logical_object_id,
            ),
        )

    def test_cartouche_composes_aura_attachment_with_declaration_rule(self):
        session = self.session_with_card("Cartouche of Zeal", seed=50811509)
        engine = session.engine
        aura = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Cartouche of Zeal"
        )
        enchanted = self.creature(engine, "A", "Cartouche Attacker")
        blocker = self.creature(engine, "B", "Cartouche Blocker")
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"

        engine.move_card(
            aura.object_id,
            "battlefield",
            controller="A",
            aura_target_ref=enchanted.ref,
            semantic_events=True,
            log=False,
        )
        self.assertTrue(engine._stabilize())
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        schema = engine.state.pending_decision.payload_by_actor["A"][
            "target_schema"
        ]
        self.assertIn(blocker.ref, schema["legal_refs"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        chosen = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "targets": {"target_0": [blocker.ref]},
                "plan": "AURA_DECLARATION_COMPOSITION",
                "reason": "Restrict the opposing blocker.",
            },
        )
        self.assertTrue(chosen.ok, chosen.summary)
        self.pass_stack(session)

        self.assertEqual(enchanted.object_id, aura.attached_to)
        self.assertIn(aura.object_id, enchanted.attachments)
        characteristics = engine._effective_card_data(enchanted)
        self.assertEqual("3", characteristics["power"])
        self.assertEqual("3", characteristics["toughness"])
        self.assertIn("Haste", characteristics["keywords"])
        self.assertEqual(
            (
                False,
                "declaration_restriction:intrinsic-block-prohibition-v1",
            ),
            engine._can_block(enchanted, blocker),
        )
        self.assert_replays(session)

        engine.move_card(aura.object_id, "graveyard", log=False)
        engine._stabilize()
        self.assertIsNone(aura.attached_to)
        self.assertEqual(
            "2", engine._effective_card_data(enchanted)["power"]
        )
        self.assertFalse(engine._can_block(enchanted, blocker)[0])

    def test_source_departure_preserves_rule_but_target_reentry_does_not(self):
        session = self.session_with_card(
            "Thundersong Trumpeter",
            seed=50811504,
        )
        engine = session.engine
        source = self.creature(engine, "A", "Resolving Source")
        target = self.creature(engine, "B", "Identity Locked Blocker")
        attacker = self.creature(engine, "A", "Attacker")
        commit_temporary_declaration_restriction(
            engine,
            card=target,
            source=ResolutionEffectSource(
                stack_ref="STACK:source-departure",
                object_id=source.object_id,
                logical_object_id=source.logical_object_id,
                card_ref=source.ref,
            ),
            kind="cant_block",
        )
        effect = next(
            value
            for value in engine.state.continuous_effects
            if isinstance(value, ResolutionDeclarationRuleEffect)
        )
        self.assertEqual(source.object_id, effect.source_id)

        engine.move_card(source.object_id, "graveyard", log=False)
        self.assertFalse(engine._can_block(attacker, target)[0])

        logical_object_id = target.logical_object_id
        engine.move_card(target.object_id, "graveyard", log=False)
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="B",
            tapped=False,
            log=False,
        )
        self.assertNotEqual(logical_object_id, target.logical_object_id)
        self.assertEqual((True, None), engine._can_block(attacker, target))

    def test_illegal_target_at_resolution_commits_no_declaration_rule(self):
        session = self.session_with_card(
            "Thundersong Trumpeter",
            seed=50811505,
        )
        engine = session.engine
        target = self.creature(engine, "B", "Departing Target")
        _source, action = self.prepare_trumpeter_activation(
            session,
            target,
        )
        result = session.act(
            "pilot:A",
            {"action_id": action["id"], "targets": [target.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        engine.move_card(target.object_id, "graveyard", log=False)

        self.pass_stack(session)

        self.assertFalse(
            any(
                isinstance(effect, ResolutionDeclarationRuleEffect)
                for effect in engine.state.continuous_effects
            )
        )

    def test_cleanup_expiration_is_exactly_replayable(self):
        session = self.session_with_card(
            "Thundersong Trumpeter",
            seed=50811506,
        )
        engine = session.engine
        target = self.creature(engine, "B", "Expiring Blocker")
        attacker = self.creature(engine, "A", "Attacker")
        engine.apply_effect(
            {
                "op": "grant_declaration_restriction_until_end_of_turn",
                "card": target.ref,
                "restriction": "cant_block",
            },
            actor="A",
        )
        self.assertFalse(engine._can_block(attacker, target)[0])

        engine.state.phase_index = TURN_STEPS.index(
            ("ending", "end_step")
        )
        engine._enter_step()
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for _ in range(2):
            principals = session.pending_principals()
            self.assertTrue(principals)
            result = session.act(
                principals[0],
                {"action_id": "pass"},
            )
            self.assertTrue(result.ok, result.summary)

        self.assertFalse(
            any(
                isinstance(effect, ResolutionDeclarationRuleEffect)
                for effect in engine.state.continuous_effects
            )
        )
        self.assertEqual((True, None), engine._can_block(attacker, target))
        self.assert_replays(session)

    def test_four_player_projection_exposes_only_public_declaration_facts(self):
        session = self.session_with_card(
            "Thundersong Trumpeter",
            seed=50811507,
            players=4,
        )
        engine = session.engine
        target = self.creature(engine, "A", "Restricted Attacker")
        free_attacker = self.creature(engine, "A", "Free Attacker")
        engine.apply_effect(
            {
                "op": "grant_declaration_restriction_until_end_of_turn",
                "card": target.ref,
                "restriction": "cant_attack",
            },
            actor="A",
        )
        effect = next(
            value
            for value in engine.state.continuous_effects
            if isinstance(value, ResolutionDeclarationRuleEffect)
        )
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        engine.state.pending_decision = None
        engine._issue_attackers()

        active_packet = session.packet("pilot:A", full=True)
        domains = active_packet["decision"]["ctx"][
            "declaration_constraints"
        ]["domains"]
        self.assertNotIn(target.ref, domains)
        self.assertIn(free_attacker.ref, domains)
        internal_identities = {
            effect.effect_id,
            effect.source_id,
            target.object_id,
            target.logical_object_id,
        }
        for seat in ("A", "B", "C", "D"):
            rendered = json.dumps(
                session.packet(f"pilot:{seat}", full=True),
                sort_keys=True,
            )
            for identity in internal_identities:
                with self.subTest(seat=seat, identity=identity):
                    self.assertNotIn(identity, rendered)
            self.assertNotIn("continuous_effects", rendered)

    def test_malformed_temporary_restriction_rolls_back_without_effect(self):
        session = self.session_with_card("Thundersong Trumpeter", seed=50811503)
        engine = session.engine
        target = self.creature(engine, "B", "Unchanged Target")
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(GameRuleError, "Unsupported"):
            engine.apply_effect(
                {
                    "op": "grant_declaration_restriction_until_end_of_turn",
                    "card": target.ref,
                    "restriction": "arbitrary",
                },
                actor="A",
            )

        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual((), engine._effective_ability_fragments(target))

        engine.move_card(target.object_id, "graveyard", log=False)
        before_stale = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine.apply_effect(
                {
                    "op": "grant_declaration_restriction_until_end_of_turn",
                    "card": target.ref,
                    "restriction": "cant_block",
                },
                actor="A",
            )
        self.assertEqual(
            before_stale,
            authoritative_state_hash(engine.state),
        )


def qualified_declaration_record(text, *, type_line="Creature \u2014 Test"):
    return CardRecord(oracle_id="fixture-qualified-declaration", name="Qualified Declaration Witness",
        mana_cost="{2}", mana_value=2, type_line=type_line, oracle_text=text,
        power="2" if "Creature" in type_line else None, toughness="2" if "Creature" in type_line else None,
        loyalty=None, defense=None, colors=(), color_identity=(), keywords=(), produced_mana=(),
        layout="normal", released_at="2026-08-07", legalities={"commander": "legal"}, faces=(), raw={})


class QualifiedDeclarationCompilerTests(unittest.TestCase):
    # CR 508.1c, 509.1b and 611.2c: a full target predicate is announced and
    # revalidated; the resolved prohibition binds that incarnation and does
    # not become an ability or a continuing predicate on later characteristics.
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, *, type_line="Creature \u2014 Test"):
        return compile_oracle_card(qualified_declaration_record(text, type_line=type_line),
            capability_registry=self.registry, capability_profile="commander_review")

    def test_qualified_temporary_rules_preserve_complete_target_predicates(self):
        cases = (
            ("{1}: Another target creature can't block this turn.", "source_exclusion"),
            ("{T}: Target creature with power 2 or less can't be blocked this turn.", "numeric_characteristic"),
            ("{1}, {T}: Target creature you control with power 2 or less can't be blocked this turn.", "controller_relation"),
            ("{2}: Target Ninja creature can't be blocked this turn.", "subtypes_any"),
            ("{1}: Target creature an opponent controls can't attack or block this turn.", "controller_relation"),
        )
        for text, field in cases:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertIn(field, node.target_schema)
                self.assertEqual("grant_declaration_restriction_until_end_of_turn", node.effects[0]["op"])
                self.assertIn("target.revalidate_resolution", node.capability_dependencies)
                if field not in {"controller_relation", "source_exclusion"}:
                    self.assertIn("target.permanent.characteristic_predicate", node.capability_dependencies)

    def test_qualified_temporary_rule_missing_authority_and_producer_remain_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        text = "{T}: Target creature with power 2 or less can't be blocked this turn."
        self.assertEqual("exact", self.compile(text).status)
        for body in ("Target creature with power X or less can't be blocked this turn.",
                     "Target creature can't block this creature this turn.",
                     "Target creature can't be blocked until your next turn."):
            with self.subTest(body=body):
                self.assertNotEqual("exact", self.compile("{1}: " + body).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == "target.permanent.characteristic_predicate").update(
            status="blocked", blockers=["numeric target authority unavailable"])
        blocked = compile_oracle_card(qualified_declaration_record(text), capability_registry=CapabilityRegistry(raw), capability_profile="commander_review")
        self.assertNotEqual("exact", blocked.status)
        with patch("quorune.compiler.temporary_declaration_templates.direct_permanent_target_spec", return_value=None):
            mutant = self.compile(text)
        self.assertNotEqual("exact", mutant.status)


class QualifiedDeclarationRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "qualified-declaration.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/qualified-declaration-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Qualified declaration", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 99)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _witnesses
    session = _witnesses.BoundEffectProgramRuntimeTests.session
    add = _witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint = _witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve = _witnesses.BoundEffectProgramRuntimeTests.resolve
    replay = _witnesses.BoundEffectProgramRuntimeTests.replay

    def advance_to(self, session, kind):
        for _ in range(16):
            if session.state.pending_decision.kind == kind:return
            self.assertEqual("priority", session.state.pending_decision.kind)
            passed = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(passed.ok, passed.summary)
        self.fail("Ordinary priority did not reach " + kind)

    def test_original_tunneler_power_target_revalidates_and_resolved_rule_locks(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        for grow_before_resolution in (True, False):
            with self.subTest(grow_before_resolution=grow_before_resolution):
                session = self.session(339010 + int(grow_before_resolution))
                engine = session.engine
                source = self.add(engine, "Goblin Tunneler")
                target = self.add(engine, "Generic Bound Body")
                ordinary = self.add(engine, "Generic Qualified Declaration Large Body")
                blocker = self.add(engine, "Generic Bound Body", seat="B", ref="defending-body")
                growth = self.add(engine, "Giant Growth", zone="hand")
                target.acquired_control_turn_count = ordinary.acquired_control_turn_count = -1
                program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name),
                    semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile="commander_review")
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding)
                session.state.phase_index = TURN_STEPS.index(("precombat_main", "main"))
                action = self._witnesses.BoundEffectProgramRuntimeTests.ready(self, session, source, {"G": 1})
                self.assertIn(target.ref, action["target_schema"]["legal_refs"])
                self.assertNotIn(ordinary.ref, action["target_schema"]["legal_refs"])
                self.checkpoint(session)
                before = authoritative_state_hash(session.state)
                denied = session.act("pilot:B", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
                self.assertFalse(denied.ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                activated = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
                self.assertTrue(activated.ok, activated.summary)
                if not grow_before_resolution:self.resolve(session)
                actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                action = next(action for action in actions if action.get("card") == growth.ref)
                cast = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
                self.assertTrue(cast.ok, cast.summary)
                self.resolve(session)
                self.assertEqual("5", session.engine._effective_card_data(session.state.cards[target.object_id])["power"])
                self.advance_to(session, "combat.attackers")
                attack = session.act("pilot:A", {"a": "attack", "atk": {target.ref: "B", ordinary.ref: "B"}})
                self.assertTrue(attack.ok, attack.summary)
                self.advance_to(session, "combat.blockers")
                for seat in "ACD":self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
                legal = session.packet("pilot:B", full=True)["decision"]["ctx"]["legal_blocks"][blocker.ref]
                self.assertIn(ordinary.ref, legal)
                self.assertEqual(grow_before_resolution, target.ref in legal)
                session = self.replay(session, load=True)
                before = authoritative_state_hash(session.state)
                blocks = {blocker.ref: target.ref}
                result = session.act("pilot:B", {"a": "block", "blk": blocks})
                if grow_before_resolution:
                    self.assertTrue(result.ok, result.summary)
                else:
                    self.assertFalse(result.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    accepted = session.act("pilot:B", {"a": "block", "blk": {blocker.ref: ordinary.ref}})
                    self.assertTrue(accepted.ok, accepted.summary)
                self.assertTrue(session.state.cards[source.object_id].tapped)
                self.assertEqual(0, session.state.players["A"].mana_pool.get("G", 0))
                self.replay(session, load=True)


class GroupDeclarationCompilerTests(unittest.TestCase):
    """CR 611.2c: explicit targets stay bound; group rules stay live.

    A nontargeted rule about creatures can affect later entrants or objects
    whose matching characteristics change. It is not a keyword grant and
    does not freeze a characteristic-modifying affected set.
    """

    def compile(self, text):
        return compile_oracle_card(
            qualified_declaration_record(text, type_line="Sorcery"),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review")

    def test_static_this_turn_history_conditions_keep_their_declaration_owner(self):
        for text in (
            "This creature can't attack unless you've cast a creature spell this turn.",
            "This creature can't attack or block unless a creature died under your control this turn.",
            "This creature can't be blocked as long as you've drawn two or more cards this turn.",
        ):
            with self.subTest(text=text):
                ir=compile_oracle_card(qualified_declaration_record(text),
                    capability_registry=load_default_capability_registry(),capability_profile="commander_review")
                self.assertEqual("exact",ir.status,ir.material_residuals)
                self.assertTrue(ir.faces[0].nodes[0].template_id.startswith("intrinsic-"))

    def test_bounded_target_groups_keep_target_count_and_one_rule_instruction(self):
        from test_qualified_zone_event_queries import query_record

        registry = load_default_capability_registry()
        for text, maximum, optional in (
            ("Up to one target creature can't block this turn.", 1, True),
            ("Two target creatures can't block this turn.", 2, False),
            ("Up to three target creatures can't be blocked this turn.", 3, True),
        ):
            with self.subTest(text=text):
                card = replace(query_record(text), type_line="Sorcery", power=None, toughness=None)
                ir = compile_oracle_card(card, capability_registry=registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(maximum, node.target_schema["count"])
                self.assertEqual(optional, bool(node.target_schema.get("up_to")))
                self.assertEqual("$targets", node.effects[0]["cards"])
                self.assertEqual(text, node.text)

    def test_public_group_rules_preserve_live_controller_and_keyword_predicates(self):
        from test_qualified_zone_event_queries import query_record

        registry = load_default_capability_registry()
        for text, relation in (
            ("All creatures can't block this turn.", "any"),
            ("Creatures you control can't be blocked this turn.", "actor"),
            ("Creatures your opponents control can't block this turn.", "opponents"),
            ("Creatures without flying can't block this turn.", "any"),
        ):
            with self.subTest(text=text):
                card = replace(query_record(text), type_line="Sorcery", power=None, toughness=None)
                ir = compile_oracle_card(card, capability_registry=registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertIsNone(node.target_schema)
                self.assertEqual(relation, node.effects[0]["set"]["controller_relation"])
                self.assertEqual(["creature"], node.effects[0]["set"]["query"]["types_all"])
                if "without flying" in text:
                    self.assertEqual(["flying"], node.effects[0]["set"]["query"]["keywords_none"])

    def test_group_rule_missing_authority_and_query_producer_remain_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry

        cases = (
            ("Two target creatures can't block this turn.", "target.revalidate_resolution"),
            ("Creatures without flying can't block this turn.",
             "continuous.resolution.declaration_rules_until_end_of_turn"),
        )
        for text, capability in cases:
            with self.subTest(text=text):
                self.assertEqual("exact", self.compile(text).status)
                raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
                next(row for row in raw["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["group declaration authority unavailable"])
                blocked = compile_oracle_card(
                    qualified_declaration_record(text, type_line="Sorcery"),
                    capability_registry=CapabilityRegistry(raw), capability_profile="commander_review")
                self.assertNotEqual("exact", blocked.status)
        with patch("quorune.compiler.temporary_declaration_templates.fixed_battlefield_query_subject", return_value=None):
            self.assertNotEqual("exact", self.compile(cases[1][0]).status)

    def test_group_rule_codecs_keep_legacy_fields_and_reject_malformed_live_queries(self):
        from quorune.continuous_effect_model import ContinuousObjectIdentity, ContinuousEffectError
        from quorune.object_predicate import ObjectQuerySpec

        old = ResolutionDeclarationRuleEffect("DR-old", "source", 1,
            temporary_declaration_restriction("cant_block"), (ContinuousObjectIdentity("object", "incarnation"),))
        self.assertNotIn("schema_version", old.to_dict())
        self.assertEqual(old, ResolutionDeclarationRuleEffect.from_dict(old.to_dict()))
        live = ResolutionDeclarationRuleEffect("DR-live", "source", 2,
            temporary_declaration_restriction("cant_block"), (), live_query=ObjectQuerySpec(
                zones=("battlefield",), types_all=("creature",), keywords_none=("flying",), controller="B"))
        self.assertEqual(2, live.to_dict()["schema_version"])
        self.assertEqual(live, ResolutionDeclarationRuleEffect.from_dict(live.to_dict()))
        for changed in (
            {"schema_version": True}, {"schema_version": 1}, {"live_query": None},
            {"locked_objects": old.to_dict()["locked_objects"]}, {"extra": True},
        ):
            with self.subTest(changed=changed), self.assertRaises(ContinuousEffectError):
                ResolutionDeclarationRuleEffect.from_dict({**live.to_dict(), **changed})
        for query in (
            ObjectQuerySpec(zones=("hand",), types_all=("creature",)),
            ObjectQuerySpec(zones=("battlefield",), types_all=("artifact",)),
            ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), known_to_actor=True),
            ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), include_phased_out=True),
            ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), controller="$controller"),
        ):
            with self.assertRaises(ContinuousEffectError):
                replace(live, live_query=query)
        state = GameState.__new__(GameState)
        state.continuous_effects = [old, live]
        self.assertEqual(2, expire_end_of_turn_continuous_effects(state))
        self.assertEqual([], state.continuous_effects)

    def test_ambiguous_groups_unbounded_targets_and_other_durations_remain_residual(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.rules.node_capability_shapes import temporary_declaration_restriction_node_capabilities

        registry = load_default_capability_registry()
        for text in (
            "Other creatures can't attack this turn.",
            "X target creatures can't block this turn.",
            "Two or more target creatures can't block this turn.",
            "Creatures you control can't be blocked until your next turn.",
            "Creatures a player chooses can't block this turn.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_oracle_card(
                    replace(query_record(text), type_line="Sorcery", power=None, toughness=None),
                    capability_registry=registry,
                ).status)
        text = "Two target creatures can't block this turn."
        ir = compile_oracle_card(replace(query_record(text), type_line="Sorcery", power=None, toughness=None), capability_registry=registry)
        node = ir.faces[0].nodes[0]
        for changed in ({"count": True}, {"count": 7}, {"up_to": True}, {"owner": "you"}):
            self.assertEqual((), temporary_declaration_restriction_node_capabilities(
                effects=node.effects, target_schema={**node.target_schema, **changed}, mechanic_ids=node.mechanics))


class GroupDeclarationRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "group-declarations.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/group-declaration-rules.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Group declarations", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 10),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _bound
    session = _bound.BoundEffectProgramRuntimeTests.session
    add = _bound.BoundEffectProgramRuntimeTests.add
    ready = _bound.BoundEffectProgramRuntimeTests.ready
    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint
    resolve = _bound.BoundEffectProgramRuntimeTests.resolve
    replay = _bound.BoundEffectProgramRuntimeTests.replay
    advance_to = QualifiedDeclarationRuntimeTests.advance_to

    def offered(self, session, seat, card):
        rows = session.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in rows if row.get("card") == card.ref)

    def priority_to(self, session, seat):
        for _ in range(5):
            if session.state.priority_player == seat:
                return
            self.assertTrue(session.act(session.pending_principals()[0], {"action_id": "pass"}).ok)
        self.fail("Priority did not reach " + seat)

    def assert_whole_admission(self, name):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry

        program = compile_best_available_card_program(
            self.db, self.db.lookup(name), semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile="commander_review",
        )
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
            profile="commander_review")["strict_capability_ready"])

    def attack_and_check_blocks(self, session, attackers, forbidden, allowed):
        self.advance_to(session, "combat.attackers")
        accepted = session.act("pilot:A", {"a": "attack", "atk": {card.ref: "B" for card in attackers}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.advance_to(session, "combat.blockers")
        packet = session.packet("pilot:B", full=True)
        legal = packet["decision"]["ctx"]["legal_blocks"]
        for card in forbidden:
            self.assertNotIn(attackers[0].ref, legal.get(card.ref, ()), "live-rule prohibition missing")
        for card in allowed:
            self.assertIn(attackers[0].ref, legal[card.ref])
        for seat in "ACD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:B", {"a": "block", "blk": {forbidden[0].ref: attackers[0].ref}})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:B", {"a": "block", "blk": {allowed[0].ref: attackers[0].ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.replay(session, load=True)

    def test_malformed_group_recipients_roll_back_before_journal_mutation(self):
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.affected_permanents import AffectedPermanentSetSpec

        session = self.session(seed=611209)
        target = self.add(session.engine, "Generic Group Flash Ground", seat="B", zone="battlefield")
        spec = AffectedPermanentSetSpec(
            query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)))
        malformed = (
            {"cards": [target.ref, target.ref]},
            {"cards": [True]},
            {"cards": [target.ref] * 7},
            {"cards": target.ref},
            {"cards": [target.ref], "card": target.ref},
            {"set": {**spec.to_dict(), "exclude_source": True}},
            {"set": {**spec.to_dict(), "query": ObjectQuerySpec(
                zones=("battlefield",), types_all=("artifact",)).to_dict()}},
            {"set": spec.to_dict(), "cards": []},
            {"cards": [], "restriction": "arbitrary"},
        )
        for recipients in malformed:
            with self.subTest(recipients=recipients):
                before = authoritative_state_hash(session.state)
                with self.assertRaises(GameRuleError):
                    session.engine.apply_effect({
                        "op": "grant_declaration_restriction_until_end_of_turn",
                        "restriction": "cant_block", **recipients}, actor="A")
                self.assertEqual(before, authoritative_state_hash(session.state))

    def test_original_falter_live_empty_group_includes_later_entrants_and_rechecks_flying(self):
        session = self.session(344101)
        engine = session.engine
        attackers = [self.add(engine, "Generic Group Flash Flyer", ref="falter-attacker-" + str(i)) for i in range(2)]
        for card in attackers:
            card.acquired_control_turn_count = -1
        flyer = self.add(engine, "Generic Group Flash Flyer", seat="B", ref="existing-flyer")
        late = [self.add(engine, "Generic Group Flash Ground", seat="B", zone="hand", ref="late-ground-" + str(i)) for i in range(2)]
        source = self.add(engine, "Falter", zone="hand")
        jump = self.add(engine, "Jump", zone="hand")
        grounding = [self.add(engine, "Turn to Frog", zone="hand", ref="grounding-" + str(i)) for i in range(2)]
        session.state.players["B"].mana_pool.update({"U": 2})
        session.state.phase_index = TURN_STEPS.index(("precombat_main", "main"))
        self.assert_whole_admission(source.printed_name)
        action = self.ready(session, source, {"R": 2, "U": 7})
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:B", {"action_id": action["id"], "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        rule = next(effect for effect in session.state.continuous_effects if isinstance(effect, ResolutionDeclarationRuleEffect))
        self.assertEqual((), rule.locked_objects)
        self.assertEqual(("flying",), rule.live_query.keywords_none)
        self.replay(session, load=True)
        self.priority_to(session, "B")
        for card in late:
            action = self.offered(session, "B", card)
            accepted = session.act("pilot:B", {"action_id": action["id"], "pay": "auto"})
            self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.priority_to(session, "A")
        action = self.offered(session, "A", jump)
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [late[0].ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assert_whole_admission(grounding[0].printed_name)
        for spell, attacker in zip(grounding, attackers):
            action = self.offered(session, "A", spell)
            accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [attacker.ref], "pay": "auto"})
            self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.attack_and_check_blocks(session, attackers, [late[1]], [flyer, late[0]])

    def test_original_blizzard_revalidates_each_target_and_retains_only_resolved_incarnations(self):
        session = self.session(344102)
        engine = session.engine
        attackers = [self.add(engine, "Generic Bound Body", ref="blizzard-attacker-" + str(i)) for i in range(2)]
        for card in attackers:
            card.acquired_control_turn_count = -1
        departed = self.add(engine, "Generic Group Flash Ground", seat="B", ref="blizzard-returning")
        retained = self.add(engine, "Generic Bound Body", seat="B", ref="blizzard-retained")
        ordinary = self.add(engine, "Generic Bound Body", seat="B", ref="blizzard-unselected")
        source = self.add(engine, "Unearthly Blizzard", zone="hand")
        bounce = self.add(engine, "Unsummon", zone="hand")
        jump = self.add(engine, "Jump", zone="hand")
        session.state.players["B"].mana_pool.update({"U": 1})
        session.state.phase_index = TURN_STEPS.index(("precombat_main", "main"))
        self.assert_whole_admission(source.printed_name)
        action = self.ready(session, source, {"R": 3, "U": 4})
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [departed.ref, retained.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        action = self.offered(session, "A", bounce)
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [departed.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual("hand", session.state.cards[departed.object_id].zone)
        rule = next(effect for effect in session.state.continuous_effects if isinstance(effect, ResolutionDeclarationRuleEffect))
        self.assertIsNone(rule.live_query)
        self.assertEqual([retained.object_id], [identity.object_id for identity in rule.locked_objects])
        self.priority_to(session, "B")
        action = self.offered(session, "B", departed)
        accepted = session.act("pilot:B", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.priority_to(session, "A")
        action = self.offered(session, "A", jump)
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [retained.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.attack_and_check_blocks(session, attackers, [retained], [ordinary, departed])

    def test_live_query_omission_mutant_is_killed_by_original_falter(self):
        from quorune import continuous_effect_state as owner
        original = owner.active_resolution_declaration_rule_effects

        def omit_live_query(state, card, **kwargs):
            return original(state, card, query_row=None)

        with patch("quorune.rules.temporary_declaration_restrictions.active_resolution_declaration_rule_effects", side_effect=omit_live_query):
            with self.assertRaisesRegex(AssertionError, "live-rule prohibition missing"):
                self.test_original_falter_live_empty_group_includes_later_entrants_and_rechecks_flying()


if __name__ == "__main__":
    unittest.main()
