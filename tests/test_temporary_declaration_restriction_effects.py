from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from common import DB_PATH, keep_all, load_assets, make_session
from quorune.ability_fragments import ability_fragment_to_dict
from quorune.carddb import CardDatabase
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
            "Up to one target creature can't block this turn.",
            "Another target creature can't block this turn.",
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


if __name__ == "__main__":
    unittest.main()
