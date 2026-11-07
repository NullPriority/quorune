from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from common import ROOT
import test_fixed_reveal_additional_costs as reveal
import test_self_public_zone_casting as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.model import StackItem
from quorune.oracle_ir import register_generated_programs
from quorune.replacement.immutable import thaw_value
from quorune.rules.casting.spell_text import CAST_TEXT_FRAMES, SpellTextBody, compose_cast_text
from quorune.rules.capabilities import load_default_capability_registry
from quorune.selection.target_validation import CopyTargetValidation, validate_grouped_target_assignment
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.executor import prepare_stack_resolution_effect
from quorune.semantic_runtime.values import resolve_semantic_value
from quorune.semantics import SemanticRegistry
from quorune.target_source import stack_targeting_source
from quorune.targets import target_plan
from scripts.build_test_database import build_fixture_database


PERMANENT = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1}
PLAYER = {"zones": ["player"], "categories": ["player"], "count": 1}


class _ValueHost:
    def __init__(self):
        self.state = SimpleNamespace(cards={}, active_player="A")
        self.logged = []

    def _semantic_value(self, value, item):
        return resolve_semantic_value(self, value, item)

    def _stack_source_ref(self, item):
        return item.card_object_id or item.ref

    def _target_snapshot(self, ref):
        return {"controller": "A" if ref == "A-body" else "B"}

    def _log(self, actor, kind, summary, payload, **kwargs):
        self.logged.append((kind, payload))


def _sealed_spell(composition, announced, current=None):
    bound = composition.bind(announced)
    return StackItem(
        stack_id="spell", ref="blue-host", kind="spell", controller="A", label="Main body",
        targets=[ref for refs in (current or announced).values() for ref in refs],
        context={
            "target_schema_override": thaw_value(composition.target_schema),
            "target_groups": announced,
            "target_groups_current": current or announced,
            "cast_option_effects": bound["effects"],
            CAST_TEXT_FRAMES: bound[CAST_TEXT_FRAMES],
        },
    )


class ComposedSpellTextTests(unittest.TestCase):
    def test_distinctness_and_controller_relations_are_scoped_to_the_original_body(self):
        main = SpellTextBody((), {
            "groups": [{**PERMANENT, "id": "left"}, {**PERMANENT, "id": "right"}],
            "globally_distinct": True, "same_player_groups": [("left", "right")],
        })
        added = SpellTextBody(({"op": "tap", "card": "$target.0"},), PERMANENT)
        plan = target_plan(compose_cast_text((main, added)).target_schema)
        candidates = {group.group_id: ["B-one", "B-two", "A-body"] for group in plan.groups}
        grouped = {"target_0": ["B-one"], "target_1": ["B-two"], "target_2": ["B-one"]}
        host = _ValueHost()
        self.assertEqual(["B-one", "B-two", "B-one"],
            validate_grouped_target_assignment(host, plan, grouped, candidates))
        for invalid in (["B-one"], ["A-body"]):
            with self.assertRaises(GameRuleError):
                validate_grouped_target_assignment(host, plan, {**grouped, "target_1": invalid}, candidates)
        copied = CopyTargetValidation(
            original_counts={key: len(refs) for key, refs in grouped.items()}, retained_groups={}, retained_snapshots={})
        self.assertEqual([1, 1, 1], [group.max_targets for group in copied.fixed_plan(plan).groups])

    def test_illegal_added_targets_skip_only_dependent_results_and_keep_main_source(self):
        host = _ValueHost()
        composition = compose_cast_text((
            SpellTextBody(({"op": "draw", "player": "$controller", "count": 1},)),
            SpellTextBody(({"op": "damage", "source": "$source", "target": "$target.0", "amount": 2},), PERMANENT),
            SpellTextBody(({"op": "mill", "player": "$target.0", "count": 4},), PLAYER),
            SpellTextBody(({"op": "choose_option", "player": "$controller",
                "then_by_choice": {"tap": [{"op": "tap", "card": "$target.0"}], "decline": []}},), PERMANENT),
            SpellTextBody(({"op": "create_damage_prevention_shield", "subject": "$target.0"},), PERMANENT),
        ))
        announced = {"target_0": ["B-one"], "target_1": ["B"], "target_2": ["B-one"], "target_3": ["B-one"]}
        current = {"target_0": ["B-one"], "target_1": [None], "target_2": [None], "target_3": [None]}
        original = _sealed_spell(composition, announced, current)
        item = StackItem.from_dict(json.loads(json.dumps(original.to_dict())))
        effects = [prepare_stack_resolution_effect(host, item, effect)
            for effect in item.context["cast_option_effects"]]
        self.assertEqual([
            {"op": "draw", "player": "A", "count": 1},
            {"op": "damage", "source": "blue-host", "target": "B-one", "amount": 2},
            None, None, None,
        ], effects)
        self.assertEqual(["B-one", None, None, None], item.targets)
        self.assertEqual(["effect.target.skipped"] * 3, [kind for kind, _ in host.logged])

    def test_selected_modes_and_their_variable_groups_bind_before_added_text(self):
        main = SpellTextBody((), {"min_modes": 2, "max_modes": 2, "modes": {
            "z-first": {"zones": ["battlefield"], "categories": ["permanent"], "up_to": 2,
                "effects": [{"op": "tap", "card": "$target.0"}]},
            "a-second": {**PERMANENT, "effects": [{"op": "untap", "card": "$target.0"}]},
        }}, ("z-first", "a-second"))
        added = SpellTextBody(({"op": "bounce", "card": "$target.0"},), PERMANENT)
        composition = compose_cast_text((main, added))
        original = _sealed_spell(composition, {"target_0": ["B-first"], "target_1": ["B-second"], "target_2": ["B-added"]})
        copied_groups = {"target_0": ["A-first"], "target_1": ["A-second"], "target_2": ["A-added"]}
        copy = replace(original, kind="spell_copy", ref="copy", context={
            **original.context,
            "target_groups": copied_groups, "target_groups_current": copied_groups,
        })
        host = _ValueHost()
        effects = [prepare_stack_resolution_effect(host, copy, effect)
            for effect in copy.context["cast_option_effects"]]
        self.assertEqual([("tap", "A-first"), ("untap", "A-second"), ("bounce", "A-added")],
            [(effect["op"], effect["card"]) for effect in effects])
        without_first_target = _sealed_spell(composition,
            {"target_0": [], "target_1": ["B-second"], "target_2": ["B-added"]})
        skipped, second, last = [prepare_stack_resolution_effect(host, without_first_target, effect)
            for effect in without_first_target.context["cast_option_effects"]]
        self.assertIsNone(skipped)
        self.assertEqual(("B-second", "B-added"), (second["card"], last["card"]))

    def test_missing_or_malformed_scope_fails_closed(self):
        main = SpellTextBody(({"op": "draw", "player": "$controller", "count": 1},))
        added = SpellTextBody(({"op": "tap", "card": "$target.0"},), PERMANENT)
        composition = compose_cast_text((main, added))
        with self.assertRaises(ValueError):
            composition.bind({})
        item = _sealed_spell(composition, {"target_0": ["B-one"]})
        effect = item.context["cast_option_effects"][1]
        with self.assertRaises(SemanticNodeError):
            prepare_stack_resolution_effect(_ValueHost(), replace(item, context={}), effect)
        with self.assertRaises(SemanticNodeError):
            prepare_stack_resolution_effect(_ValueHost(), item, {**effect, "_cast_text_frame": True})
        substituted = replace(item, context={**item.context, "target_groups_current": {"target_0": ["A-body"]}})
        with self.assertRaises(SemanticNodeError):
            prepare_stack_resolution_effect(_ValueHost(), substituted, effect)


class ComposedSpellStackTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = reveal.FixedRevealCostRuntimeTests.session
    add = reveal.FixedRevealCostRuntimeTests.add
    fund = reveal.FixedRevealCostRuntimeTests.fund
    checkpoint = reveal.FixedRevealCostRuntimeTests.checkpoint
    resolve = reveal.FixedRevealCostRuntimeTests.resolve
    replay = reveal.FixedRevealCostRuntimeTests.replay
    cast = actions.SelfPublicZoneRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "composed-spell.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/composed-spell-text-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Composed spell witness", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])
        donor = cls.db.lookup("Glacial Ray", fuzzy=False)
        isolated = replace(donor, oracle_text=donor.oracle_text.splitlines()[0], keywords=())
        semantics = SemanticRegistry()
        register_generated_programs(cls.db, semantics, (isolated,), capability_registry=cls.registry,
            capability_profile="commander_review", promote_exact_effect_programs=True)
        cls.added_program, = semantics.programs_for_oracle(donor.oracle_id)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def attach_prepared_text(self, session, target, prohibited):
        """Exercise the stack seam with a server-built composition fixture."""
        engine = session.engine
        item, = session.state.stack
        main = engine.semantics.get(item.semantic_key)
        self.assertEqual(("trusted", "trusted"), (main.trust_level, self.added_program.trust_level))
        plan = compose_cast_text(tuple(SpellTextBody(program.effects, program.target_schema)
            for program in (main, self.added_program)))
        schema = thaw_value(plan.target_schema)
        item.context["target_schema_override"] = schema
        source = stack_targeting_source(engine, item)
        with self.assertRaises(GameRuleError):
            engine._validate_semantic_targets("A", main, [prohibited.ref],
                acting_source=source, source_ref=engine._stack_source_ref(item), target_schema=schema)
        targets, groups = engine._validate_semantic_targets("A", main, [target.ref],
            acting_source=source, source_ref=engine._stack_source_ref(item), target_schema=schema)
        bound = plan.bind(groups)
        item.targets = targets
        item.context.update(target_groups=groups,
            target_snapshots={ref: engine._target_snapshot(ref) for ref in targets},
            cast_option_effects=bound["effects"])
        item.context[CAST_TEXT_FRAMES] = bound[CAST_TEXT_FRAMES]
        damage = prepare_stack_resolution_effect(engine, item, bound["effects"][1])
        self.assertEqual("reach-through-mists", damage["source"])
        self.assertEqual(["U"], damage["source_snapshot"]["colors"])

    def test_live_stack_uses_host_color_and_fizzles_when_all_composed_targets_leave(self):
        for invalidated in (False, True):
            with self.subTest(invalidated=invalidated):
                session = self.session(7024701 + invalidated)
                source = self.add(session.engine, "Reach Through Mists", zone="hand")
                target = self.add(session.engine, "Silver Knight", seat="B")
                prohibited = self.add(session.engine, "Goblin Piledriver", seat="B")
                bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
                session.state.players["B"].mana_pool["U"] = 1
                self.fund(session, {"U": 1})
                self.cast(session, "A", source)
                self.attach_prepared_text(session, target, prohibited)
                before = len(session.state.players["A"].zones["hand"])
                self.checkpoint(session)
                if invalidated:
                    self.cast(session, "B", bounce, [target.ref])
                self.assertIsNone(self.resolve(session))
                self.assertEqual("hand" if invalidated else "graveyard", session.state.cards[target.object_id].zone)
                self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
                self.assertEqual(before + (not invalidated), len(session.state.players["A"].zones["hand"]))
                self.replay(session, load=True)

    def test_real_stack_copy_keeps_composed_text_with_its_own_targets(self):
        session = self.session(7024703)
        engine = session.engine
        source = self.add(engine, "Reach Through Mists", zone="hand")
        first = self.add(engine, "Silver Knight", seat="B", ref="original-target")
        second = self.add(engine, "Silver Knight", seat="B", ref="copy-target")
        prohibited = self.add(engine, "Goblin Piledriver", seat="B")
        self.fund(session, {"U": 1})
        self.cast(session, "A", source)
        self.attach_prepared_text(session, first, prohibited)
        original, = session.state.stack
        copy = engine._copy_stack_item(controller="A", target=original, targets=[second.ref],
            target_groups={"target_0": [second.ref]}, reason="composed spell copy witness")
        self.assertEqual(original.context[CAST_TEXT_FRAMES], copy.context[CAST_TEXT_FRAMES])
        self.assertEqual([first.ref], original.targets)
        self.assertEqual([second.ref], copy.targets)
        damage = prepare_stack_resolution_effect(engine, copy, copy.context["cast_option_effects"][1])
        self.assertEqual((copy.card_object_id, ["U"]),
            (damage["source_snapshot"]["object_id"], damage["source_snapshot"]["colors"]))
        before = len(session.state.players["A"].zones["hand"])
        self.checkpoint(session)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(["graveyard", "graveyard"],
            [session.state.cards[card.object_id].zone for card in (first, second)])
        self.assertEqual(before + 2, len(session.state.players["A"].zones["hand"]))
        self.replay(session, load=True)
