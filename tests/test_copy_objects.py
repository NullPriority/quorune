from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from common import keep_all, load_assets, make_session
from quorune.damage import damage_proposal, resolve_damage_batch
from quorune.damage_modifier_state import (
    DamageModifierDuration,
    PreventionMode,
)
from quorune.damage_prevention_creation import (
    commit_prevention_shield_creation,
    plan_prevention_shield_creation,
    PreventionShieldCreationRequest,
    PreventionSubjectAllocation,
)
from quorune.damage_source import REPRESENTED_DAMAGE_SOURCE_ZONES
from quorune.model import GameState, StackItem
from quorune.object_query import ObjectQuerySpec
from quorune.record import (
    checkpoint_envelope,
    deck_list_fingerprints,
    replay_record,
)
from quorune.targets import TargetGroup
from quorune.util import stable_json


class EntryCopyContractTests(unittest.TestCase):
    def test_entry_copy_value_preserves_nontarget_selection_and_typed_exceptions(self):
        from quorune.compiler.entry_copy_templates import entry_copy_spec
        from quorune.entry_copy import EntryCopySpec
        cases = (
            ('You may have this creature enter as a copy of any creature on the battlefield.', ('creature',), (), None, ()),
            ('You may have this creature enter as a copy of a creature you control.', ('creature',), (), '$controller', ()),
            ('You may have this creature enter as a copy of a creature an opponent controls.', ('creature',), (), None, ('$controller',)),
            ('You may have this creature enter as a copy of any Ally creature on the battlefield.', ('creature',), ('ally',), None, ()),
            ("You may have this creature enter as a copy of any artifact or creature on the battlefield, except it's an artifact in addition to its other types.", (), (), None, ()),
        )
        for text, types, subtypes, controller, excluded in cases:
            spec = entry_copy_spec(text, source_name='Generic Copier')
            self.assertIsNotNone(spec, text)
            self.assertEqual(types, spec.selection.types_all)
            self.assertEqual(subtypes, spec.selection.subtypes_all)
            self.assertEqual(controller, spec.selection.controller)
            self.assertEqual(excluded, spec.selection.excluded_controllers)
            self.assertFalse(spec.selection.include_phased_out)
            self.assertEqual(spec, EntryCopySpec.from_dict(spec.to_dict()))
        spec = entry_copy_spec(cases[-1][0], source_name='Generic Copier')
        self.assertEqual(('artifact','creature'), spec.selection.types_any)
        self.assertEqual(('artifact',), spec.exception.add_card_types)


    def test_entry_copy_contract_rejects_hidden_targets_open_tails_and_malformed_values(self):
        from quorune.compiler.entry_copy_templates import entry_copy_spec
        from quorune.entry_copy import EntryCopySpec
        text = 'You may have this creature enter as a copy of any creature on the battlefield.'
        spec = entry_copy_spec(text, source_name='Generic Copier')
        for changed in (
            text + ' Then rewrite history.',
            text.replace('any creature on the battlefield', 'a card in a graveyard'),
            text.replace('any creature on the battlefield', 'target creature'),
            text.replace('any creature on the battlefield', 'a creature with an unknown quality'),
            text.replace('this creature', 'Another creature'),
            text.replace('.', ', except it has "Rewrite history."'),
        ):
            self.assertIsNone(entry_copy_spec(changed, source_name='Generic Copier'), changed)
        for change in ({'schema_version':True}, {'unknown':True}, {'selection':{**spec.selection.to_dict(),'zones':['hand']}}):
            with self.assertRaises(ValueError):
                EntryCopySpec.from_dict({**spec.to_dict(), **change})
        # Syntax/value recognition supplies no compiler-to-gameplay trust until
        # the intrinsic replacement, copied entry and ability owners are bound.
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        self.assertNotEqual('exact', compile_oracle_card(record(text, 'Creature — Shapeshifter'), capability_registry=load_default_capability_registry()).status)


class CopyObjectLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=seed,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = None
        return session

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner
            and card.is_card_object
            and card.printed_name == name
        )

    def put_spell_on_stack(
        self,
        engine,
        owner: str,
        name: str,
        *,
        ref: str,
    ) -> StackItem:
        card = self.card(engine, owner, name)
        engine._remove_from_zone(card)
        engine._reset_zone_change(card, "stack")
        card.zone = "stack"
        card.controller = owner
        card.known_to = list(engine.seats)
        card.revealed_to = list(engine.seats)
        item = StackItem(
            stack_id=engine._stable_runtime_id("stack", ref),
            ref=ref,
            kind="spell",
            controller=owner,
            label=name,
            card_object_id=card.object_id,
            default_destination=(
                "battlefield"
                if self.db.lookup(name).is_permanent_spell
                else "graveyard"
            ),
            visibility=list(engine.seats),
            context={"dynamic_effects": []},
        )
        engine.state.stack.append(item)
        return item

    def copy_spell(self, engine, original: StackItem) -> StackItem:
        return engine._copy_stack_item(
            controller=original.controller,
            target=original,
            targets=list(original.targets),
            target_groups={},
            reason="copy-object lifecycle test",
        )

    def test_spell_copy_has_a_serialized_stack_object(self):
        engine = self.make_session(70701).engine
        original = self.put_spell_on_stack(
            engine,
            "A",
            "Chaos Warp",
            ref="S-copy-source",
        )

        copied = self.copy_spell(engine, original)
        copy_object = engine.state.cards[copied.card_object_id]

        self.assertEqual("spell_copy", copy_object.object_kind)
        self.assertTrue(copy_object.is_spell_copy)
        self.assertFalse(copy_object.is_card_object)
        self.assertEqual("stack", copy_object.zone)
        self.assertNotEqual(original.card_object_id, copied.card_object_id)
        self.assertFalse(engine._stabilize())
        self.assertEqual("stack", copy_object.zone)
        engine._assert_invariants()

        restored = GameState.from_dict(engine.state.to_dict())
        restored_copy = restored.cards[copy_object.object_id]
        self.assertEqual("spell_copy", restored_copy.object_kind)
        self.assertEqual(
            copy_object.zone_timestamp,
            restored_copy.zone_timestamp,
        )
        self.assertEqual(
            copy_object.object_id,
            next(
                item.card_object_id
                for item in restored.stack
                if item.ref == copied.ref
            ),
        )

    def test_spell_copy_preserves_explicit_referred_object_provenance(self):
        engine = self.make_session(70712).engine
        original = self.put_spell_on_stack(
            engine,
            "A",
            "Chaos Warp",
            ref="S-copy-referred-source",
        )
        referred = self.card(engine, "B", "Sol Ring")
        original.referred_object_ids = [referred.object_id]

        copied = self.copy_spell(engine, original)

        self.assertEqual([referred.object_id], copied.referred_object_ids)

    def test_countered_spell_copy_reaches_graveyard_then_ceases(self):
        engine = self.make_session(70702).engine
        voidwalker = self.card(engine, "B", "Dauthi Voidwalker")
        engine.move_card(
            voidwalker.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        original = self.put_spell_on_stack(
            engine,
            "A",
            "Chaos Warp",
            ref="S-counter-copy-source",
        )
        copied = self.copy_spell(engine, original)
        copy_object = engine.state.cards[copied.card_object_id]

        engine._counter_stack_item(
            copied.ref,
            reason="copy lifecycle counter",
            countered_by="B",
        )

        self.assertEqual("graveyard", copy_object.zone)
        self.assertIn(
            copy_object.object_id,
            engine.state.players["A"].zones["graveyard"],
        )
        self.assertNotIn("void", copy_object.counters)
        self.assertFalse(engine._stabilize())
        self.assertEqual("outside", copy_object.zone)
        self.assertNotIn(
            copy_object.object_id,
            engine.state.players["A"].zones["graveyard"],
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "state.objects_ceased"
        )
        self.assertEqual(
            [
                {
                    "object": copy_object.ref,
                    "kind": "spell_copy",
                    "zone": "graveyard",
                }
            ],
            event.details["objects"],
        )

    def test_card_copy_is_not_a_card_and_ceases_from_graveyard(self):
        engine = self.make_session(70703).engine
        source = self.card(engine, "A", "Sol Ring")
        fingerprints = deck_list_fingerprints(engine.state)

        copied = engine.create_card_copy(
            "A",
            source.ref,
            zone="graveyard",
        )
        group = TargetGroup.from_mapping(
            {
                "zones": ["graveyard"],
                "categories": ["card"],
                "artifact": True,
                "count": 1,
            }
        )

        self.assertTrue(copied.is_card_copy)
        self.assertFalse(copied.is_card_object)
        self.assertNotIn(
            copied.ref,
            engine._target_candidates("A", group),
        )
        self.assertEqual(
            fingerprints,
            deck_list_fingerprints(engine.state),
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual("outside", copied.zone)

    def test_card_copy_on_battlefield_is_a_nontoken_permanent(self):
        engine = self.make_session(70704).engine
        source = self.card(engine, "A", "Sol Ring")
        copied = engine.create_card_copy(
            "A",
            source.ref,
            zone="battlefield",
        )
        group = TargetGroup.from_mapping(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "artifact": True,
                "count": 1,
            }
        )

        self.assertFalse(engine._stabilize())
        self.assertEqual("battlefield", copied.zone)
        self.assertFalse(copied.is_token)
        self.assertIn(
            copied.ref,
            engine._target_candidates("B", group),
        )

    def test_permanent_spell_copy_becomes_the_same_token_object(self):
        engine = self.make_session(70705).engine
        original = self.put_spell_on_stack(
            engine,
            "A",
            "Sol Ring",
            ref="S-permanent-copy-source",
        )
        copied = self.copy_spell(engine, original)
        copy_object_id = copied.card_object_id
        copy_object = engine.state.cards[copy_object_id]
        token_events_before = sum(
            event.code == "token.create"
            for event in engine.state.events
        )
        stack_incarnation = copy_object.logical_object_id

        engine._begin_resolve_item(
            copied,
            [],
            "battlefield",
        )

        self.assertIs(copy_object, engine.state.cards[copy_object_id])
        self.assertEqual("battlefield", copy_object.zone)
        self.assertEqual("token", copy_object.object_kind)
        self.assertTrue(copy_object.is_token)
        self.assertEqual(
            stack_incarnation,
            copy_object.logical_object_id,
        )
        self.assertNotIn(copied, engine.state.stack)
        self.assertEqual(
            token_events_before,
            sum(
                event.code == "token.create"
                for event in engine.state.events
            ),
        )

    def test_chosen_permanent_spell_copy_uses_public_stack_identity(self):
        engine = self.make_session(70713).engine
        original = self.put_spell_on_stack(
            engine,
            "A",
            "Sol Ring",
            ref="S-chosen-permanent-copy-source",
        )
        copied = self.copy_spell(engine, original)
        copy_object = engine.state.cards[copied.card_object_id]

        query = engine._semantic_choice_query("B")
        self.assertIn(copied.ref, query.damage_source_candidate_refs())
        self.assertNotIn(
            copy_object.ref, query.damage_source_candidate_refs()
        )
        plan = plan_prevention_shield_creation(
            engine,
            PreventionShieldCreationRequest(
                source_id="fixture:chosen-copy",
                controller="B",
                mode=PreventionMode.NEXT_INSTANCE,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                subjects=(PreventionSubjectAllocation("B", None),),
                chosen_source_ref=copied.ref,
                source_predicate=ObjectQuerySpec(
                    zones=REPRESENTED_DAMAGE_SOURCE_ZONES,
                    types_all=("artifact",),
                    known_to_actor=True,
                ),
            ),
        )
        commit_prevention_shield_creation(engine, plan)

        engine._begin_resolve_item(copied, [], "battlefield")
        result = resolve_damage_batch(
            engine,
            (
                damage_proposal(
                    engine,
                    proposal_id="damage:chosen-permanent-copy",
                    actor="A",
                    source_ref=copy_object.ref,
                    target="B",
                    amount=1,
                    combat=False,
                    reason="chosen permanent spell copy continuity",
                ),
            ),
        )

        self.assertEqual(0, result.dealt_amount)
        self.assertFalse(engine.state.damage_prevention_shields)

    def test_spell_copy_is_a_spell_target_not_an_ability(self):
        engine = self.make_session(70706).engine
        original = self.put_spell_on_stack(
            engine,
            "A",
            "Chaos Warp",
            ref="S-spell-category-source",
        )
        copied = self.copy_spell(engine, original)
        group = TargetGroup.from_mapping(
            {
                "zones": ["stack"],
                "categories": ["spell"],
                "count": 1,
            }
        )

        candidates = engine._target_candidates("B", group)
        self.assertIn(original.ref, candidates)
        self.assertIn(copied.ref, candidates)

    def test_copy_object_identity_is_not_projected_to_pilots(self):
        session = self.make_session(70707)
        engine = session.engine
        original = self.put_spell_on_stack(
            engine,
            "A",
            "Chaos Warp",
            ref="S-private-copy-source",
        )
        copied = self.copy_spell(engine, original)
        copy_object = engine.state.cards[copied.card_object_id]

        packet = stable_json(session.packet("pilot:B", full=True))

        self.assertNotIn(copy_object.object_id, packet)
        self.assertNotIn(copy_object.ref, packet)
        self.assertNotIn("object_kind", packet)
        self.assertIn(copied.ref, packet)

    def test_spell_copy_cessation_replays_exactly(self):
        session = self.make_session(70708)
        engine = session.engine
        original = self.put_spell_on_stack(
            engine,
            "A",
            "Chaos Warp",
            ref="S-replay-copy-source",
        )
        copied = self.copy_spell(engine, original)
        copy_object = engine.state.cards[copied.card_object_id]
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        first = session.act(
            "pilot:A",
            {
                "action_id": "pass",
                "plan": "HOLD",
                "reason": "Pass priority to test copy-object replay.",
            },
        )
        self.assertTrue(first.ok, first.summary)
        second = session.act(
            "pilot:B",
            {
                "action_id": "pass",
                "plan": "HOLD",
                "reason": "Allow the copied spell to resolve.",
            },
        )
        self.assertTrue(second.ok, second.summary)
        self.assertEqual("outside", copy_object.zone)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "copy-record"
            session.save(record_dir)
            replay = replay_record(
                record_dir,
                self.db,
                verify=True,
            )
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()
