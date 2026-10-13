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
    def test_entry_copy_with_unrepresented_target_sibling_rejects_whole_runtime_admission(self):
        from high_risk_interaction_support import _record, _observed_piece_ids
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.compiler.unlock_frontier import analyze_card_unlocks
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import compile_oracle_card
        from common import load_assets

        db, _, _ = load_assets()
        try:
            record = _record('entry-copy-target-boundary')
            registry = load_default_capability_registry()
            ir = compile_oracle_card(record, capability_registry=registry, capability_profile='commander_review')
            program = compile_best_available_card_program(db, record, semantic_registry=SemanticRegistry(), capability_registry=registry, capability_profile='commander_review')
            row = analyze_card_unlocks(ir, program=program, program_error=None, capabilities=registry, profile='commander_review')
            self.assertLessEqual(
                {'capability.zone.entry.fixed_copy', 'residual.target_or_choice.target-predicate'},
                _observed_piece_ids(row),
            )
            self.assertEqual('residual', row['card_program_status'])
            self.assertIsNone(row['hard_construction_failure'])
            binding = bind_card_program_runtime(program, capability_registry=registry, profile='commander_review')
            self.assertFalse(binding['strict_capability_ready'])
            self.assertFalse(binding['compatible_ready'])
            self.assertIn('trust_basis:unresolved', binding['blockers'])
        finally:
            db.close()

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


    def test_intrinsic_copy_entry_has_an_independent_source_spanned_replacement_owner(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        text = 'You may have this creature enter as a copy of any creature on the battlefield.'
        ir = compile_oracle_card(record(text, 'Creature — Shapeshifter'), capability_registry=load_default_capability_registry(), capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual('replacement_effect', node.kind)
        self.assertEqual('all', node.active_zone)
        self.assertIsNone(node.target_schema)
        self.assertEqual('zone.change', node.event)
        self.assertEqual(text, node.text)
        self.assertEqual('replacement.zone.entry-copy.v1', node.handlers[0]['handler_id'])
        self.assertIn('zone.entry.fixed_copy', node.capability_dependencies)

    def test_registered_entry_copy_handler_rejects_open_fields_and_has_no_mutation_context(self):
        from quorune.compiler.entry_copy_templates import entry_copy_spec
        from quorune.entry_copy import ENTRY_COPY_HANDLER
        from quorune.semantic_runtime.entry_copy import EntryCopyHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        spec = entry_copy_spec('You may have this creature enter as a copy of any creature on the battlefield.', source_name='Fixture')
        descriptor = {'handler_id':ENTRY_COPY_HANDLER,'schema_version':1,'event':'zone.change','copy':spec.to_dict()}
        handler = EntryCopyHandler()
        self.assertEqual(spec,handler.validate(descriptor))
        self.assertEqual((),handler.lower(descriptor,object()))
        for change in ({'schema_version':True},{'unknown':True},{'copy':{**spec.to_dict(),'selection':{**spec.selection.to_dict(),'known_to_actor':False}}}):
            with self.assertRaises(SemanticNodeError):
                handler.validate({**descriptor,**change})

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
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        import json
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row = next(r for r in raw['capabilities'] if r['id'] == 'zone.entry.fixed_copy')
        row.update(status='blocked', blockers=['Independent copy-entry dependency mutation'])
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified(load_default_capability_registry().evidence_fingerprint)
        self.assertNotEqual('exact', compile_oracle_card(record(text, 'Creature — Shapeshifter'), capability_registry=registry).status)


class EntryCopyRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from scripts.build_test_database import build_fixture_database
        from common import ROOT
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'entry-copy.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/entry-copy-cards.json'], path)
        cls.db = CardDatabase(path)
        from quorune.rules.capabilities import load_default_capability_registry
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Entry copy', [DeckEntry('Generic Bound Commander',1,'commander'), DeckEntry('Generic Bound Plains',30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _witnesses
    session = _witnesses.session
    add = _witnesses.add
    ready = _witnesses.ready
    checkpoint = _witnesses.checkpoint
    resolve = _witnesses.resolve
    replay = _witnesses.replay

    def test_original_clone_choice_copies_printed_values_and_etb_for_copier_with_replay(self):
        from quorune.record import authoritative_state_hash
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        session = self.session(357501)
        engine = session.engine
        source = self.add(engine, 'Clone', zone='hand')
        original = source.oracle_id
        target = self.add(engine, 'Elvish Visionary', seat='B', ref='public-copy-source')
        target.tapped = True
        target.counters['+1/+1'] = 3
        target.temporary_keywords.append('Haste')
        program = compile_best_available_card_program(self.db, self.db.lookup('Clone'), semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')['strict_capability_ready'])
        action = self.ready(session, source, {'U':1,'C':3})
        self.checkpoint(session)
        before = len(session.state.players['A'].zones['hand'])
        accepted = session.act('pilot:A', {'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual('replacement.order', decision.kind)
        self.assertEqual('stack', session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        options = session.packet('pilot:A',full=True)['decision']['ctx']['options']
        choice = next(c for c in options if target.ref in c['id'])
        unchanged = authoritative_state_hash(session.state)
        rejected = session.act('pilot:C', {'action_id':'choose','replacement':choice['id']})
        self.assertFalse(rejected.ok)
        self.assertEqual(unchanged, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id':'choose','replacement':choice['id']})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        current = session.state.cards[source.object_id]
        engine = session.engine
        self.assertEqual('battlefield', current.zone)
        self.assertEqual(original, current.oracle_id)
        self.assertEqual('Elvish Visionary', engine.display_name(current.object_id))
        self.assertEqual(1, engine._numeric_stat(current.object_id,'power'))
        self.assertFalse(current.tapped)
        self.assertFalse(current.counters)
        self.assertNotIn('haste',engine._combat_keywords(current))
        self.assertEqual(before, len(session.state.players['A'].zones['hand']))
        import json
        projected = json.dumps(session.packet('pilot:D', full=True)['state'])
        self.assertNotIn(current.object_id, projected)
        for seat in 'ABC':
            for oid in session.state.players[seat].zones['hand']:
                self.assertNotIn(session.state.cards[oid].ref, projected)
        self.replay(session, load=True)


    def test_original_clone_copied_entry_counter_runs_after_copy_and_replays(self):
        session = self.session(357502)
        engine = session.engine
        source = self.add(engine, 'Clone', zone='hand')
        target = self.add(engine, 'Arcbound Worker', seat='B', ref='copied-counter-source')
        target.counters['+1/+1'] = 5
        self.add(engine, 'Generic Bound Counter Doubler', ref='copy-entry-doubler')
        action = self.ready(session, source, {'U':1,'C':3})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('replacement.order', self.resolve(session).kind)
        options = session.packet('pilot:A',full=True)['decision']['ctx']['options']
        choice = next(c for c in options if target.ref in c['id'] and not c['id'].startswith('decline:'))
        accepted = session.act('pilot:A', {'action_id':'choose','replacement':choice['id']})
        self.assertTrue(accepted.ok, accepted.summary)
        # The copied intrinsic counter producer runs before mutation; the
        # ordinary mandatory quantity replacement remains in the same tree.
        self.resolve(session)
        current = session.state.cards[source.object_id]
        self.assertEqual('battlefield',current.zone)
        self.assertEqual(2,current.counters.get('+1/+1'))
        self.assertEqual(2,engine._numeric_stat(current.object_id,'power'))
        self.assertEqual('Arcbound Worker',engine.display_name(current.object_id))
        self.replay(session,load=True)

    def test_original_metamorph_reanimation_copies_artifact_activation_and_retains_identity(self):
        session = self.session(357503)
        engine = session.engine
        source = self.add(engine, 'Phyrexian Metamorph', zone='graveyard')
        spell = self.add(engine, 'Zombify', zone='hand')
        target = self.add(engine, 'Sol Ring', seat='B', ref='copied-mana-source')
        action = self.ready(session, spell, {'B':1,'C':3})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('replacement.order', self.resolve(session).kind)
        options = session.packet('pilot:A',full=True)['decision']['ctx']['options']
        choice = next(c for c in options if target.ref in c['id'] and not c['id'].startswith('decline:'))
        accepted = session.act('pilot:A', {'action_id':'choose','replacement':choice['id']})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        current = session.state.cards[source.object_id]
        self.assertEqual(source.oracle_id,current.oracle_id)
        self.assertEqual('Artifact',engine._effective_card_data(current)['type_line'])
        while session.pending_principals()[0] != 'pilot:A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id':'pass'}).ok)
        actions = session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        activation = next(a for a in actions if a['id'].startswith('activate:'+current.ref+':'))
        amount = session.state.players['A'].mana_pool.get('C',0)
        accepted = session.act('pilot:A', {'action_id':activation['id'],'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(amount+2,session.state.players['A'].mana_pool.get('C',0))
        self.assertTrue(current.tapped)
        self.replay(session,load=True)

    def test_original_clone_decline_is_legal_and_face_down_copy_has_only_public_copiable_values(self):
        for face_down in (False, True):
            session = self.session(357504 if not face_down else 357505)
            engine = session.engine
            source = self.add(engine,'Clone',zone='hand')
            target = self.add(engine,'Elvish Visionary',seat='B',ref='anonymous-source')
            if face_down:
                from quorune.morph import MORPH_FACE_DOWN_ANNOTATION, face_down_characteristics
                target.face_down = True
                target.annotations[MORPH_FACE_DOWN_ANNOTATION] = face_down_characteristics('morph')
            action = self.ready(session,source,{'U':1,'C':3})
            self.checkpoint(session)
            accepted = session.act('pilot:A', {'action_id':action['id'],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            self.assertEqual('replacement.order',self.resolve(session).kind)
            options = session.packet('pilot:A',full=True)['decision']['ctx']['options']
            choice = next(c for c in options if c['id'].startswith('decline:') == (not face_down))
            accepted = session.act('pilot:A', {'action_id':'choose','replacement':choice['id']})
            self.assertTrue(accepted.ok,accepted.summary)
            self.resolve(session)
            current = session.state.cards[source.object_id]
            if face_down:
                self.assertEqual('battlefield',current.zone)
                self.assertFalse(current.face_down)
                self.assertEqual(2,engine._numeric_stat(current.object_id,'power'))
                self.assertEqual(2,engine._numeric_stat(current.object_id,'toughness'))
                self.assertEqual([],engine._effective_card_data(current)['keywords'])
                self.assertEqual([],engine._effective_card_data(current)['colors'])
            else:
                self.assertEqual('graveyard',current.zone)
                self.assertNotIn('copy_overrides',current.annotations)
            self.replay(session,load=True)

    def test_original_mirror_image_offer_filters_control_and_clone_blink_resets_copy_values(self):
        from quorune.record import authoritative_state_hash
        session = self.session(357506)
        engine = session.engine
        source = self.add(engine,'Mirror Image',zone='hand')
        own = self.add(engine,'Elvish Visionary',ref='own-copy-source')
        foreign = self.add(engine,'Hill Giant',seat='B',ref='foreign-copy-source')
        action = self.ready(session,source,{'U':1,'C':2})
        self.checkpoint(session)
        accepted = session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',self.resolve(session).kind)
        options = session.packet('pilot:A',full=True)['decision']['ctx']['options']
        self.assertTrue(any(own.ref in c['id'] for c in options))
        self.assertFalse(any(foreign.ref in c['id'] for c in options))
        unchanged = authoritative_state_hash(session.state)
        rejected = session.act('pilot:A',{'action_id':'choose','replacement':'not-a-copy-option'})
        self.assertFalse(rejected.ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        choice = next(c for c in options if own.ref in c['id'] and not c['id'].startswith('decline:'))
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','replacement':choice['id']}).ok)
        self.resolve(session)
        session = self.replay(session,load=True)
        engine = session.engine
        current = session.state.cards[source.object_id]
        old = current.logical_object_id
        engine.move_card(own.object_id,'hand',log=False)
        self.assertEqual('Elvish Visionary',engine.display_name(current.object_id))
        engine.move_card(current.object_id,'hand',log=False)
        self.assertNotEqual(old,current.logical_object_id)
        self.assertNotIn('copy_overrides',current.annotations)
        self.assertEqual('Mirror Image',engine.display_name(current.object_id))

    def test_original_mirrormade_copy_aura_keeps_intrinsic_attachment_choice_before_entry(self):
        session = self.session(357507)
        engine = session.engine
        source = self.add(engine,'Mirrormade',zone='hand')
        aura = self.add(engine,'Holy Strength',seat='B',ref='copied-aura-source')
        body = self.add(engine,'Generic Bound Body',ref='copied-aura-recipient')
        self.add(engine,'Generic Bound Body',seat='C',ref='other-aura-recipient')
        from quorune.attachments import attach_objects
        attach_objects(engine.state.cards, aura, body, source_timestamp=engine._next_zone_timestamp())
        action = self.ready(session,source,{'U':2,'C':1})
        self.checkpoint(session)
        accepted = session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',self.resolve(session).kind)
        options = session.packet('pilot:A',full=True)['decision']['ctx']['options']
        choice = next(c for c in options if aura.ref in c['id'] and not c['id'].startswith('decline:'))
        accepted = session.act('pilot:A',{'action_id':'choose','replacement':choice['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual('stack',session.state.cards[source.object_id].zone)
        self.assertIn('aura',decision.kind)
        from quorune.record import authoritative_state_hash
        session = self.replay(session,load=True)
        unchanged = authoritative_state_hash(session.state)
        rejected = session.act('pilot:C',{'action_id':'choose','aura_target':body.ref})
        self.assertFalse(rejected.ok)
        self.assertEqual(unchanged,authoritative_state_hash(session.state))
        accepted = session.act('pilot:A',{'action_id':'choose','aura_target':body.ref})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        current = session.state.cards[source.object_id]
        self.assertEqual('battlefield',current.zone)
        self.assertEqual(body.object_id,current.attached_to)
        self.assertIn(current.object_id,session.state.cards[body.object_id].attachments)
        self.assertEqual(4,session.engine._numeric_stat(body.object_id,'power'))
        self.replay(session,load=True)

    def test_original_clone_copied_undying_uses_departure_snapshot_and_new_incarnation(self):
        session = self.session(357508)
        engine = session.engine
        source = self.add(engine,'Clone',zone='hand')
        target = self.add(engine,'Young Wolf',seat='B',ref='copied-death-source')
        removal = self.add(engine,'Murder',zone='hand')
        action = self.ready(session,source,{'U':1,'B':2,'C':4})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual('replacement.order',self.resolve(session).kind)
        options = session.packet('pilot:A',full=True)['decision']['ctx']['options']
        choice = next(c for c in options if target.ref in c['id'] and not c['id'].startswith('decline:'))
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','replacement':choice['id']}).ok)
        self.resolve(session)
        while session.pending_principals()[0] != 'pilot:A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        action = next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == removal.ref)
        old = session.state.cards[source.object_id].logical_object_id
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'}).ok)
        decision = self.resolve(session)
        self.assertIsNotNone(decision)  # The returned physical Clone chooses a new copy.
        self.assertEqual('replacement.order',decision.kind)
        self.assertNotEqual(old,session.state.cards[source.object_id].logical_object_id)
        options = session.packet('pilot:A',full=True)['decision']['ctx']['options']
        choice = next(c for c in options if target.ref in c['id'] and not c['id'].startswith('decline:'))
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','replacement':choice['id']}).ok)
        self.resolve(session)
        current = session.state.cards[source.object_id]
        self.assertEqual('battlefield',current.zone)
        self.assertEqual(1,current.counters.get('+1/+1'))
        self.assertEqual('Young Wolf',engine.display_name(current.object_id))
        self.replay(session,load=True)

    def test_intrinsic_copy_producer_omission_mutant_is_killed(self):
        from unittest import mock
        from quorune.semantic_runtime import zone_replacements as owner
        with mock.patch.object(owner, 'entry_copy_replacement_effects', return_value=()):
            with self.assertRaises(AssertionError):
                self.test_original_clone_choice_copies_printed_values_and_etb_for_copier_with_replay()


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
