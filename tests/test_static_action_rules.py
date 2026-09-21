from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.ability_fragments import StaticComponentSpec
from quorune.attachments import attach_objects, attach_to_player, detach_object
from quorune.card_programs import compile_card_program
from quorune.carddb import CardRecord
from quorune.compiler.activation_restriction_templates import (
    static_activation_restriction_handler,
)
from quorune.compiler.static_cast_rule_templates import static_cast_rule_handler
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.enchant_spec import SimpleEnchantSpec, enchant_spec_to_dict
from quorune.model import CardInstance
from quorune.oracle_ir import register_generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.activation_restrictions import (
    default_activation_restriction_registry,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.static_cast_rules import (
    STATIC_CAST_LIMIT_HANDLER_ID,
    STATIC_CAST_PROHIBITION_HANDLER_ID,
    STATIC_CAST_TIMING_HANDLER_ID,
    STATIC_UNCOUNTERABLE_HANDLER_ID,
    default_static_cast_rule_registry,
)
from quorune.semantic_runtime.untap_steps import current_untap_step_plan


class _NoRulingsDatabase:
    @staticmethod
    def rulings(record):
        del record
        return ()


def _permanent(text: str, *, suffix: int) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name="Static Action Rule Fixture",
        mana_cost="{3}",
        mana_value=3.0,
        type_line="Artifact",
        oracle_text=text,
        power=None,
        toughness=None,
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


class StaticActionRuleCompilerTests(unittest.TestCase):
    def setUp(self):
        self.capabilities = load_default_capability_registry()

    def compile(self, text: str, *, suffix: int, trust_level: str = "trusted"):
        return compile_card_program(
            _NoRulingsDatabase(),
            _permanent(text, suffix=suffix),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level=trust_level,
        )

    def test_static_action_rule_contract_matrix(self):
        cases = (
            (
                "You may cast creature spells as though they had flash.",
                (STATIC_CAST_TIMING_HANDLER_ID,),
            ),
            (
                "Your opponents can't cast spells during your turn.",
                (STATIC_CAST_PROHIBITION_HANDLER_ID,),
            ),
            (
                "Each player can't cast more than one spell each turn.",
                (STATIC_CAST_LIMIT_HANDLER_ID,),
            ),
            (
                "Enchanted player can't cast more than one spell each turn.",
                (STATIC_CAST_LIMIT_HANDLER_ID,),
            ),
            (
                "Creature spells you control can't be countered.",
                (STATIC_UNCOUNTERABLE_HANDLER_ID,),
            ),
            (
                "Activated abilities of artifacts can't be activated.",
                ("restriction.activation.fixed-public.v1",),
            ),
            (
                "Enchanted permanent doesn't untap during its controller's "
                "untap step and its activated abilities can't be activated.",
                ("restriction.activation.fixed-public.v1",),
            ),
        )
        for index, (text, handler_ids) in enumerate(cases, 1):
            with self.subTest(text=text):
                program = self.compile(text, suffix=116_320_000 + index)
                self.assertEqual((), program.residuals)
                ability = next(
                    value
                    for value in program.abilities
                    if set(handler_ids)
                    <= {
                        descriptor.get("handler_id")
                        for descriptor in value.handlers
                    }
                )
                self.assertIn(
                    "current_ability_fragment_required",
                    ability.coverage,
                )

    def test_static_action_rule_grammar_and_descriptors_fail_closed(self):
        unsupported = (
            "You may cast the first creature spell you cast each turn as "
            "though it had flash.",
            "Players can't cast spells that share a color with the spell "
            "most recently cast this turn.",
            "You can't cast more than two spells each turn.",
            "Activated abilities cost {2} more to activate.",
            "Target spell can't be countered this turn.",
        )
        for index, text in enumerate(unsupported, 1):
            with self.subTest(text=text):
                program = self.compile(
                    text,
                    suffix=116_321_000 + index,
                    trust_level="provisional",
                )
                self.assertTrue(program.residuals)

        cast = static_cast_rule_handler(
            "You may cast creature spells as though they had flash.",
            source_name="Static Action Rule Fixture",
        )
        activation = static_activation_restriction_handler(
            "Activated abilities of artifacts can't be activated."
        )
        assert cast is not None and activation is not None
        for registry, descriptor in (
            (default_static_cast_rule_registry(), cast[1]),
            (default_activation_restriction_registry(), activation[1]),
        ):
            for value in (
                {**descriptor, "schema_version": True},
                {**descriptor, "unknown": True},
            ):
                with self.subTest(value=value):
                    with self.assertRaises(SemanticNodeError):
                        registry.validate(value)

    def test_qualified_aura_timing_preserves_typed_enchant_restriction(self):
        qualified = static_cast_rule_handler(
            "You may cast Aura spells with enchant creature as though they "
            "had flash.",
            source_name="Static Action Rule Fixture",
        )
        unrestricted = static_cast_rule_handler(
            "You may cast Aura spells as though they had flash.",
            source_name="Static Action Rule Fixture",
        )
        assert qualified is not None and unrestricted is not None
        self.assertEqual(
            enchant_spec_to_dict(SimpleEnchantSpec("creature")),
            qualified[1]["required_enchant_spec"],
        )
        self.assertIsNone(unrestricted[1]["required_enchant_spec"])
        registry = default_static_cast_rule_registry()
        with self.assertRaises(SemanticNodeError):
            registry.validate(
                {
                    **qualified[1],
                    "required_enchant_spec": {
                        "kind": "simple_object",
                        "value": {},
                    },
                }
            )
        uncounterable = static_cast_rule_handler(
            "Creature spells can't be countered.",
            source_name="Static Action Rule Fixture",
        )
        assert uncounterable is not None
        with self.assertRaisesRegex(SemanticNodeError, "timing permissions"):
            registry.validate(
                {
                    **uncounterable[1],
                    "required_enchant_spec": qualified[1][
                        "required_enchant_spec"
                    ],
                }
            )

    def test_static_action_rule_compiler_mutant_is_killed(self):
        text = "You may cast creature spells as though they had flash."

        def assert_exact() -> None:
            self.assertEqual((), self.compile(text, suffix=116_322_001).residuals)

        assert_exact()
        with mock.patch(
            "quorune.compiler.runtime_templates.static_cast_rule_handler",
            return_value=None,
        ):
            with self.assertRaises((AssertionError, ValueError)):
                assert_exact()


class StaticActionRuleRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

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
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.state.pending_decision = None
        session.state.priority_player = None
        session.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        session,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
    ) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine._next_zone_timestamp(),
            acquired_control_turn_count=-1,
            known_to=[],
            revealed_to=[],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
        )
        return card

    def prepare_priority(
        self,
        session,
        seat: str,
        *,
        active: str,
        phase: str = "precombat_main",
        step: str = "main",
    ) -> None:
        engine = session.engine
        engine.state.active_player = active
        engine.state.phase = phase
        engine.state.step = step
        engine.state.started = True
        engine.state.players[seat].mana_pool.update({"G": 4, "U": 4, "C": 6})
        engine._grant_priority(seat)
        engine.pump()

    def test_cast_timing_prohibition_limits_and_revalidation(self):
        session = self.session(116_323_001)
        engine = session.engine
        timing = self.add_card(
            session,
            seat="B",
            name="Generic Creature Timing Rule",
            ref="B-timing-rule",
            zone="battlefield",
        )
        creature = self.add_card(
            session,
            seat="B",
            name="Generic Action Rule Creature",
            ref="B-flash-creature",
            zone="hand",
        )
        self.prepare_priority(session, "B", active="A")
        actions = session.packet("pilot:B", full=True)["decision"]["ctx"][
            "legal"
        ]["actions"]
        cast_action = next(
            action
            for action in actions
            if action.get("kind") == "cast"
            and action.get("card") == creature.ref
        )
        engine.move_card(timing.object_id, "graveyard", log=False)
        result = session.act(
            "pilot:B",
            {"action_id": cast_action["id"], "pay": "auto"},
        )
        self.assertFalse(result.ok)
        self.assertRegex(result.summary.casefold(), r"stale|timing")

        prohibited = self.session(116_323_002)
        self.add_card(
            prohibited,
            seat="A",
            name="Generic Turn Cast Prohibition",
            ref="A-turn-rule",
            zone="battlefield",
        )
        instant = self.add_card(
            prohibited,
            seat="B",
            name="Generic Action Rule Instant",
            ref="B-prohibited-instant",
            zone="hand",
        )
        self.prepare_priority(prohibited, "B", active="A")
        with self.assertRaisesRegex(Exception, "static rule"):
            prohibited.engine._cast(
                "B", {"card": instant.ref, "pay": "auto"}
            )

        chosen = self.session(116_323_009)
        chosen_source = self.add_card(
            chosen,
            seat="A",
            name="Generic Chosen Cast Prohibition",
            ref="A-chosen-rule",
            zone="battlefield",
        )
        chosen_source.annotations["chosen_name"] = "Generic Action Rule Instant"
        named_spell = self.add_card(
            chosen,
            seat="B",
            name="Generic Action Rule Instant",
            ref="B-chosen-spell",
            zone="hand",
        )
        self.prepare_priority(chosen, "B", active="B")
        with self.assertRaisesRegex(Exception, "static rule"):
            chosen.engine._cast(
                "B",
                {"card": named_spell.ref, "pay": "auto"},
            )

        conditional = self.session(116_323_010)
        conditional_source = self.add_card(
            conditional,
            seat="B",
            name="Generic Conditional Timing Rule",
            ref="B-conditional-rule",
            zone="battlefield",
        )
        conditional_spell = self.add_card(
            conditional,
            seat="B",
            name="Generic Action Rule Creature",
            ref="B-conditional-spell",
            zone="hand",
        )
        self.prepare_priority(conditional, "B", active="A")
        self.assertFalse(
            any(
                action.get("kind") == "cast"
                and action.get("card") == conditional_spell.ref
                for action in conditional.packet("pilot:B", full=True)[
                    "decision"
                ]["ctx"]["legal"]["actions"]
            )
        )

        conditional_source.tapped = True
        conditional.state.pending_decision = None
        conditional.engine.permissions.invalidate_current()
        conditional.state.priority_player = "B"
        conditional.engine._grant_priority("B")
        conditional.engine.pump()
        self.assertTrue(
            any(
                action.get("kind") == "cast"
                and action.get("card") == conditional_spell.ref
                for action in conditional.packet("pilot:B", full=True)[
                    "decision"
                ]["ctx"]["legal"]["actions"]
            )
        )

        removed = self.session(116_323_013)
        removed_source = self.add_card(
            removed,
            seat="B",
            name="Generic Creature Timing Rule",
            ref="B-removed-timing",
            zone="battlefield",
        )
        removed_spell = self.add_card(
            removed,
            seat="B",
            name="Generic Action Rule Creature",
            ref="B-removed-spell",
            zone="hand",
        )
        program = removed.engine.semantics.runtime_handler_programs_for_oracle(
            removed_source.oracle_id,
            active_zone="battlefield",
            event="cast.static.rule",
        )[0]
        commit_continuous_effect(
            removed.state,
            ContinuousEffect(
                effect_id="fixture:remove-static-cast-rule",
                source_id="fixture:remove-static-cast-rule",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=removed.engine._next_zone_timestamp(),
                operations=(
                    ContinuousOperation(
                        "remove_ability_fragment",
                        {
                            "kind": "static_component",
                            "value": StaticComponentSpec(program.key).to_dict(),
                        },
                    ),
                ),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        removed_source.object_id,
                        removed_source.logical_object_id,
                    ),
                ),
            ),
        )
        self.prepare_priority(removed, "B", active="A")
        self.assertFalse(
            any(
                action.get("kind") == "cast"
                and action.get("card") == removed_spell.ref
                for action in removed.packet("pilot:B", full=True)["decision"][
                    "ctx"
                ]["legal"]["actions"]
            )
        )

        limited = self.session(116_323_006)
        self.add_card(
            limited,
            seat="A",
            name="Generic Cast Limit",
            ref="A-cast-limit",
            zone="battlefield",
        )
        first = self.add_card(
            limited,
            seat="B",
            name="Generic Action Rule Instant",
            ref="B-first-instant",
            zone="hand",
        )
        second = self.add_card(
            limited,
            seat="B",
            name="Generic Action Rule Instant",
            ref="B-second-instant",
            zone="hand",
        )
        self.prepare_priority(limited, "B", active="B")
        limited.engine._cast("B", {"card": first.ref, "pay": "auto"})
        limited.state.priority_player = "B"
        with self.assertRaisesRegex(Exception, "static rule"):
            limited.engine._cast(
                "B",
                {"card": second.ref, "pay": "auto"},
            )

        attached_limit = self.session(116_323_017)
        limit_aura = self.add_card(
            attached_limit,
            seat="A",
            name="Generic Attached Player Cast Limit",
            ref="A-attached-cast-limit",
            zone="battlefield",
        )
        first = self.add_card(
            attached_limit,
            seat="B",
            name="Generic Action Rule Instant",
            ref="B-attached-first",
            zone="hand",
        )
        second = self.add_card(
            attached_limit,
            seat="B",
            name="Generic Action Rule Instant",
            ref="B-attached-second",
            zone="hand",
        )
        attach_to_player(
            attached_limit.state.cards,
            attached_limit.state.players,
            limit_aura,
            "B",
            source_timestamp=attached_limit.engine._next_zone_timestamp(),
        )
        self.prepare_priority(attached_limit, "B", active="B")
        attached_limit.engine._cast(
            "B",
            {"card": first.ref, "pay": "auto"},
        )
        attached_limit.state.priority_player = "B"
        with self.assertRaisesRegex(Exception, "static rule"):
            attached_limit.engine._cast(
                "B",
                {"card": second.ref, "pay": "auto"},
            )

    def test_qualified_aura_timing_distinguishes_enchant_creature_from_land(
        self,
    ):
        session = self.session(116_323_014)
        engine = session.engine
        source = self.add_card(
            session,
            seat="B",
            name="Generic Creature Aura Timing Rule",
            ref="B-creature-aura-timing",
            zone="battlefield",
        )
        creature_aura = self.add_card(
            session,
            seat="B",
            name="Generic Enchant Creature Aura",
            ref="B-creature-aura",
            zone="hand",
        )
        land_aura = self.add_card(
            session,
            seat="B",
            name="Generic Enchant Land Aura",
            ref="B-land-aura",
            zone="hand",
        )
        creature_target = self.add_card(
            session,
            seat="B",
            name="Generic Action Rule Creature",
            ref="B-aura-creature-target",
            zone="battlefield",
        )
        land_target = self.add_card(
            session,
            seat="B",
            name="Island",
            ref="B-aura-land-target",
            zone="battlefield",
        )
        self.prepare_priority(session, "B", active="A")
        offered = {
            action.get("card")
            for action in session.packet("pilot:B", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
            if action.get("kind") == "cast"
        }
        self.assertIn(creature_aura.ref, offered)
        self.assertNotIn(land_aura.ref, offered)
        self.assertEqual(
            SimpleEnchantSpec("land"),
            engine._compiled_enchant_spec(land_aura),
        )
        self.assertEqual("battlefield", land_target.zone)

        offered_creature_aura = next(
            action
            for action in session.packet("pilot:B", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
            if action.get("kind") == "cast"
            and action.get("card") == creature_aura.ref
        )
        source_id = source.object_id
        creature_aura_id = creature_aura.object_id
        creature_target_id = creature_target.object_id
        engine.move_card(source_id, "graveyard", log=False)
        stale_before = authoritative_state_hash(engine.state)
        stale = session.act(
            "pilot:B",
            {
                "action_id": offered_creature_aura["id"],
                "targets": [creature_target.ref],
                "pay": "auto",
            },
        )
        self.assertFalse(stale.ok)
        self.assertEqual(stale_before, authoritative_state_hash(engine.state))
        self.assertEqual("hand", engine.state.cards[creature_aura_id].zone)

        source = engine.state.cards[source_id]
        engine.move_card(source_id, "battlefield", controller="B", log=False)
        source = engine.state.cards[source_id]
        program = engine.semantics.runtime_handler_programs_for_oracle(
            source.oracle_id,
            active_zone="battlefield",
            event="cast.static.rule",
        )[0]
        removal = commit_continuous_effect(
            session.state,
            ContinuousEffect(
                effect_id="fixture:remove-qualified-aura-timing",
                source_id="fixture:remove-qualified-aura-timing",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(
                    ContinuousOperation(
                        "remove_ability_fragment",
                        {
                            "kind": "static_component",
                            "value": StaticComponentSpec(program.key).to_dict(),
                        },
                    ),
                ),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        source.object_id,
                        source.logical_object_id,
                    ),
                ),
            ),
        )

        def refresh_priority() -> None:
            session.state.pending_decision = None
            engine.permissions.invalidate_current()
            session.state.priority_player = "B"
            engine._grant_priority("B")
            engine.pump()

        refresh_priority()
        self.assertFalse(
            any(
                action.get("kind") == "cast"
                and action.get("card") == creature_aura.ref
                for action in session.packet("pilot:B", full=True)[
                    "decision"
                ]["ctx"]["legal"]["actions"]
            )
        )
        session.state.continuous_effects.remove(removal)
        refresh_priority()
        payment_offer = next(
            action
            for action in session.packet("pilot:B", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
            if action.get("kind") == "cast"
            and action.get("card") == creature_aura.ref
        )
        session.state.players["B"].mana_pool = {
            symbol: 0
            for symbol in session.state.players["B"].mana_pool
        }
        insufficient_before = authoritative_state_hash(engine.state)
        insufficient = session.act(
            "pilot:B",
            {
                "action_id": payment_offer["id"],
                "targets": [creature_target.ref],
                "pay": "manual",
                "payment": {},
            },
        )
        self.assertFalse(insufficient.ok)
        self.assertEqual(
            insufficient_before,
            authoritative_state_hash(engine.state),
        )
        self.assertEqual("hand", engine.state.cards[creature_aura_id].zone)

        creature_aura = engine.state.cards[creature_aura_id]
        creature_target = engine.state.cards[creature_target_id]
        engine.state.players["B"].mana_pool["U"] = 1
        refresh_priority()
        cast_offer = next(
            action
            for action in session.packet("pilot:B", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
            if action.get("kind") == "cast"
            and action.get("card") == creature_aura.ref
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        cast = session.act(
            "pilot:B",
            {
                "action_id": cast_offer["id"],
                "targets": [creature_target.ref],
                "pay": "auto",
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        for _ in range(12):
            if not engine.state.stack:
                break
            principal = session.pending_principals()[0]
            passed = session.act(principal, {"action_id": "pass"})
            self.assertTrue(passed.ok, passed.summary)
        self.assertFalse(engine.state.stack)
        self.assertEqual(
            creature_target.object_id,
            engine.state.cards[creature_aura_id].attached_to,
        )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "qualified-aura-timing-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        unrestricted = self.session(116_323_015)
        self.add_card(
            unrestricted,
            seat="B",
            name="Generic Aura Timing Rule",
            ref="B-unrestricted-aura-timing",
            zone="battlefield",
        )
        unrestricted_creature_aura = self.add_card(
            unrestricted,
            seat="B",
            name="Generic Enchant Creature Aura",
            ref="B-unrestricted-creature-aura",
            zone="hand",
        )
        unrestricted_land_aura = self.add_card(
            unrestricted,
            seat="B",
            name="Generic Enchant Land Aura",
            ref="B-unrestricted-land-aura",
            zone="hand",
        )
        self.add_card(
            unrestricted,
            seat="B",
            name="Generic Action Rule Creature",
            ref="B-unrestricted-creature-target",
            zone="battlefield",
        )
        unrestricted_land_target = self.add_card(
            unrestricted,
            seat="B",
            name="Island",
            ref="B-unrestricted-land-target",
            zone="battlefield",
        )
        self.prepare_priority(unrestricted, "B", active="A")
        unrestricted_offers = {
            action.get("card")
            for action in unrestricted.packet("pilot:B", full=True)[
                "decision"
            ]["ctx"]["legal"]["actions"]
            if action.get("kind") == "cast"
        }
        self.assertIn(unrestricted_creature_aura.ref, unrestricted_offers)
        self.assertIn(unrestricted_land_aura.ref, unrestricted_offers)
        unrestricted_land_offer = next(
            action
            for action in unrestricted.packet("pilot:B", full=True)[
                "decision"
            ]["ctx"]["legal"]["actions"]
            if action.get("kind") == "cast"
            and action.get("card") == unrestricted_land_aura.ref
        )
        self.assertIn(
            unrestricted_land_target.ref,
            unrestricted_land_offer["target_schema"]["legal_refs"],
        )

    def test_static_uncounterable_tracks_current_source_and_spell_copies(self):
        session = self.session(116_323_003)
        engine = session.engine
        source = self.add_card(
            session,
            seat="A",
            name="Generic Creature Uncounterable Rule",
            ref="A-uncounterable",
            zone="battlefield",
        )
        spell = self.add_card(
            session,
            seat="A",
            name="Generic Action Rule Creature",
            ref="A-uncounterable-spell",
            zone="hand",
        )
        self.prepare_priority(session, "A", active="A")
        engine._cast("A", {"card": spell.ref, "pay": "auto"})
        item = engine.state.stack[-1]
        engine._counter_stack_item(item.ref, countered_by="B")
        self.assertIn(item, engine.state.stack)
        copied = engine._copy_stack_item(
            controller="A",
            target=item,
            targets=(),
            target_groups={},
            reason="static uncounterable copy witness",
        )
        engine._counter_stack_item(copied.ref, countered_by="B")
        self.assertIn(copied, engine.state.stack)
        engine.move_card(source.object_id, "graveyard", log=False)
        engine._counter_stack_item(item.ref, countered_by="B")
        self.assertNotIn(item, engine.state.stack)
        engine._counter_stack_item(copied.ref, countered_by="B")
        self.assertNotIn(copied, engine.state.stack)

        abilities = self.session(116_323_011)
        self.add_card(
            abilities,
            seat="A",
            name="Generic Table Uncounterable Rule",
            ref="A-table-uncounterable",
            zone="battlefield",
        )
        ability_source = self.add_card(
            abilities,
            seat="A",
            name="Generic Action Rule Creature",
            ref="A-ability-source",
            zone="battlefield",
        )
        self.prepare_priority(abilities, "A", active="A")
        activated = abilities.engine._activated_abilities(ability_source)[0]
        abilities.engine._activate(
            "A",
            {"source": ability_source.ref, "ability": activated.ability_id},
        )
        ability_item = abilities.state.stack[-1]
        abilities.engine._counter_stack_item(
            ability_item.ref,
            countered_by="B",
        )
        self.assertIn(ability_item, abilities.state.stack)

    def test_fixed_activation_prohibitions_share_offer_commit_legality(self):
        session = self.session(116_323_004)
        engine = session.engine
        self.add_card(
            session,
            seat="A",
            name="Generic Artifact Activation Prohibition",
            ref="A-artifact-rule",
            zone="battlefield",
        )
        artifact = self.add_card(
            session,
            seat="B",
            name="Generic Action Rule Artifact",
            ref="B-artifact",
            zone="battlefield",
        )
        self.prepare_priority(session, "B", active="B")
        actions = session.packet("pilot:B", full=True)["decision"]["ctx"][
            "legal"
        ]["actions"]
        self.assertFalse(
            any(action.get("source") == artifact.ref for action in actions)
        )
        ability = engine._activated_abilities(artifact)[0]
        with self.assertRaisesRegex(
            Exception,
            "fixed_public_activation_prohibition",
        ):
            engine._activate(
                "B",
                {"source": artifact.ref, "ability": ability.ability_id},
            )

        exempt = self.session(116_323_007)
        self.add_card(
            exempt,
            seat="A",
            name="Generic Nonmana Activation Prohibition",
            ref="A-nonmana-rule",
            zone="battlefield",
        )
        mana_artifact = self.add_card(
            exempt,
            seat="B",
            name="Generic Action Rule Artifact",
            ref="B-mana-artifact",
            zone="battlefield",
        )
        self.prepare_priority(exempt, "B", active="B")
        mana_ability = exempt.engine._activated_abilities(mana_artifact)[0]
        self.assertTrue(mana_ability.mana_ability)
        self.assertTrue(
            any(
                action.get("source") == mana_artifact.ref
                for action in exempt.packet("pilot:B", full=True)["decision"][
                    "ctx"
                ]["legal"]["actions"]
            )
        )

        compound = self.session(116_323_012)
        compound_aura = self.add_card(
            compound,
            seat="A",
            name="Generic Compound Activated Ability Arrest",
            ref="A-compound-arrest",
            zone="battlefield",
        )
        compound_creature = self.add_card(
            compound,
            seat="B",
            name="Generic Action Rule Creature",
            ref="B-compound-arrested",
            zone="battlefield",
        )
        compound_creature.tapped = True
        attach_objects(
            compound.state.cards,
            compound_aura,
            compound_creature,
            source_timestamp=compound.engine._next_zone_timestamp(),
            players=compound.state.players,
        )
        plan = current_untap_step_plan(compound.engine, "B")
        self.assertIn(
            compound_creature.object_id,
            plan.prohibited_object_ids,
        )

        attached = self.session(116_323_008)
        aura = self.add_card(
            attached,
            seat="A",
            name="Generic Activated Ability Arrest",
            ref="A-arrest",
            zone="battlefield",
        )
        creature = self.add_card(
            attached,
            seat="B",
            name="Generic Action Rule Creature",
            ref="B-arrested",
            zone="battlefield",
        )
        attach_objects(
            attached.state.cards,
            aura,
            creature,
            source_timestamp=attached.engine._next_zone_timestamp(),
            players=attached.state.players,
        )
        self.prepare_priority(attached, "B", active="B")
        self.assertFalse(
            any(
                action.get("source") == creature.ref
                for action in attached.packet("pilot:B", full=True)["decision"][
                    "ctx"
                ]["legal"]["actions"]
            )
        )
        detach_object(
            attached.state.cards,
            aura,
            players=attached.state.players,
        )
        attached.state.pending_decision = None
        attached.engine.permissions.invalidate_current()
        attached.state.priority_player = "B"
        attached.engine._grant_priority("B")
        attached.engine.pump()
        self.assertTrue(
            any(
                action.get("source") == creature.ref
                for action in attached.packet("pilot:B", full=True)["decision"][
                    "ctx"
                ]["legal"]["actions"]
            )
        )

    def test_static_action_rules_replay_and_four_player_projection(self):
        session = self.session(116_323_005)
        self.add_card(
            session,
            seat="B",
            name="Generic Creature Timing Rule",
            ref="B-replay-rule",
            zone="battlefield",
        )
        creature = self.add_card(
            session,
            seat="B",
            name="Generic Action Rule Creature",
            ref="B-replay-creature",
            zone="hand",
        )
        self.prepare_priority(session, "B", active="A")
        packet = session.packet("pilot:B", full=True)
        action = next(
            value
            for value in packet["decision"]["ctx"]["legal"]["actions"]
            if value.get("kind") == "cast" and value.get("card") == creature.ref
        )
        self.assertNotIn(creature.ref, str(session.packet("pilot:A", full=True)))
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:B",
            {"action_id": action["id"], "pay": "auto"},
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "static-action-rule-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
