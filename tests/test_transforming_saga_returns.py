"""CR 714/712.14: source-pinned terminal chapters return the physical back face."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.compiler.linked_exile_return_templates import linked_exile_return_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_exile_return import transformed_return_face
from quorune.linked_exile_return_model import LinkedExileReturnSpec, LINKED_TRANSFORMED_RETURN_CAPABILITY as CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.linked_exile_return_shapes import linked_exile_return_node_capabilities
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database

TERMINAL = 'Exile this Saga, then return it to the battlefield transformed under your control.'
SAGA = "Tales of Master Seshiro // Seshiro's Living Legacy"
BACK = "Seshiro's Living Legacy"


class TransformingSagaFixture:
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [row for row in base['cards'] if row['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        basics = Path(cls.temporary.name)/'basics.json'
        basics.write_text(json.dumps(base, ensure_ascii=False), encoding='utf-8', newline='\n')
        db_path = Path(cls.temporary.name)/'transforming-sagas.sqlite3'
        build_fixture_database([
            basics,
            ROOT/'tests/fixtures/transforming-saga-return-cards.json',
            ROOT/'tests/fixtures/untargeted-untap-cards.json',
        ], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Transforming Saga', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()


class TransformingSagaModelTests(unittest.TestCase):
    def test_transformed_spec_preserves_legacy_descriptors_and_rejects_mixed_modes(self):
        for timing, version in (('immediate',1), ('source_departure',2), ('linked_ability',3)):
            value = LinkedExileReturnSpec(timing=timing, schema_version=version).to_dict()
            self.assertNotIn('transformed', value)
            self.assertEqual(value, LinkedExileReturnSpec.from_dict(value).to_dict())
            with self.assertRaises(ValueError):
                LinkedExileReturnSpec.from_dict({**value, 'transformed':False})
        spec = LinkedExileReturnSpec(transformed=True, schema_version=4, controller='actor')
        self.assertEqual(spec, LinkedExileReturnSpec.from_dict(spec.to_dict()))
        for changed in ({'transformed':False}, {'transformed':1}, {'schema_version':True},
                {'timing':'next_end_step'}, {'controller':'owner'}, {'tapped':True},
                {'entry_counters':(('+1/+1',1),)}, {'keywords':('haste',)}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                replace(spec, **changed)

    def test_return_face_uses_physical_layout_and_permanent_eligibility(self):
        def record(layout, front='Enchantment — Saga', back='Creature — Spirit'):
            return SimpleNamespace(layout=layout, faces=({'name':'front','type_line':front},
                {'name':'physical-back','type_line':back}))
        for layout in ('transform','modal_dfc'):
            self.assertEqual('physical-back', transformed_return_face(record(layout)))
        for layout in ('normal','split','flip','meld'):
            self.assertIsNone(transformed_return_face(record(layout)))
        self.assertIsNone(transformed_return_face(record('modal_dfc', front='Sorcery', back='Land')))
        self.assertIsNone(transformed_return_face(record('transform', back='Instant')))
        self.assertIsNone(transformed_return_face(None))


class TransformingSagaCompilerTests(TransformingSagaFixture, unittest.TestCase):
    def test_original_two_face_candidates_are_strictly_bound_without_reminder_residuals(self):
        payload = json.loads((ROOT/'tests/fixtures/transforming-saga-return-cards.json').read_text(encoding='utf-8'))
        originals = [row for row in payload['cards'] if row['layout'] == 'transform']
        self.assertEqual(10, len(originals))
        semantics = SemanticRegistry()
        for row in originals:
            with self.subTest(card=row['name']):
                record = self.db.by_oracle_id(row['oracle_id'])
                ir = compile_oracle_card(record, capability_registry=self.registry)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertEqual(2, len(ir.faces))
                program = compile_best_available_card_program(self.db, record,
                    semantic_registry=semantics, capability_registry=self.registry, capability_profile='commander_review')
                self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
                    profile='commander_review')['strict_capability_ready'])

    def test_terminal_pair_is_closed_and_adjacent_open_variants_remain_unrepresented(self):
        compiled = linked_exile_return_effect_template(TERMINAL, card_name='Typed Saga', source_is_permanent=True)
        template, effects, targets, mechanics = compiled
        self.assertIsNone(targets)
        self.assertEqual(['exile','return'], [effect['phase'] for effect in effects])
        self.assertEqual(effects[0]['spec'], effects[1]['spec'])
        self.assertTrue(effects[0]['spec']['transformed'])
        self.assertIn(CAPABILITY, linked_exile_return_node_capabilities(
            effects=effects, target_schema=targets, mechanic_ids=mechanics))
        for text in (TERMINAL.replace('your control','its owner\'s control'),
                TERMINAL.replace('transformed','converted'), TERMINAL+' Rewrite history.',
                TERMINAL.replace('this Saga','target creature')):
            self.assertIsNone(linked_exile_return_effect_template(text, card_name='Typed Saga', source_is_permanent=True))
        for changes in ({'schema_version':1}, {'transformed':False}, {'controller':'owner'}):
            altered = deepcopy(effects)
            altered[0]['spec'].update(changes)
            self.assertEqual((), linked_exile_return_node_capabilities(
                effects=altered, target_schema=None, mechanic_ids=mechanics))

    def test_verified_transformed_dependency_withdrawal_preserves_ordinary_blink(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id']==CAPABILITY).update(status='blocked', blockers=['return-face owner withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=read_evidence_document(
            ROOT/'platform/capability-evidence-declarations.json'), discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual('exact', compile_oracle_card(self.db.lookup(SAGA), capability_registry=registry).status)
        self.assertEqual('exact', compile_oracle_card(self.db.lookup('Flicker of Fate'), capability_registry=registry).status)


class TransformingSagaNativeTests(TransformingSagaFixture, InheritedNativeFixture, unittest.TestCase):
    def begin_terminal(self, seed):
        session = self.session(seed)
        source = self.add(session.engine, SAGA, ref='borrowed-terminal-saga')
        source.owner = 'B'
        source.active_face = self.db.by_oracle_id(source.oracle_id).faces[0]['name']
        source.counters['lore'] = 2
        plan = self.add(session.engine, 'Contentious Plan', zone='hand')
        blink = self.add(session.engine, 'Flicker of Fate', zone='hand')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand', ref='terminal-private-hand')
        for card in (source, plan, blink):self.assert_closed(session, card)
        self.prepare(session, mana={'C':2,'U':1,'W':1})
        self.checkpoint(session)
        self.cast(session,'A',plan)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertEqual(['A'], session.state.pending_decision.actors)
        before = authoritative_state_hash(session.state)
        choice = {'action_id':'choose','objects':[source.ref],'plan':'PROLIFERATE'}
        self.assertFalse(session.act('pilot:B',choice).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act('pilot:A', choice)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(3, session.state.cards[source.object_id].counters['lore'])
        return session, source, blink, private

    def witness(self, seed):
        session, source, blink, private = self.begin_terminal(seed)
        original_identity = session.state.cards[source.object_id].logical_object_id
        decision = self.resolve(session)
        current = session.state.cards[source.object_id]
        self.assertEqual(BACK, current.active_face, 'back face must be selected before entry')
        self.assertEqual('A', current.controller, 'return controller must come from the resolving ability')
        self.assertEqual('B', current.owner)
        self.assertEqual('battlefield', current.zone)
        self.assertNotEqual(original_identity, current.logical_object_id)
        self.assertEqual({}, current.counters)
        self.assertEqual(0, current.transform_count)
        self.assertIsNone(decision)
        self.assertEqual(5, session.engine._numeric_stat(current.object_id,'power'))
        self.assertEqual(0, self.trigger_count(session,current))
        for seat in 'ABD':self.assertNotIn(private.ref, json.dumps(session.packet('pilot:'+seat,full=True)))
        life = session.state.players['B'].life
        self.attack(session,[(current,'B')])
        self.assertFalse(session.state.cards[current.object_id].tapped)
        self.until(session,lambda s:s.state.phase=='postcombat_main')
        self.assertEqual(life-5, session.state.players['B'].life)
        self.replay(session,load=True)

    def test_actual_borrowed_saga_proliferates_returns_back_and_attacks_with_haste_and_vigilance(self):
        self.witness(986_100)

    def test_actual_azusa_chapters_and_blocked_back_face_untap(self):
        game = self.session(986_405)
        name = "Azusa's Many Journeys // Likeness of the Seeker"
        source = self.add(game.engine, name, zone='hand')
        plans = [
            self.add(game.engine, 'Contentious Plan', zone='hand', ref=f'azusa-plan-{index}')
            for index in range(2)
        ]
        self.add(game.engine, 'Fervor')
        own_lands = [self.add(game.engine, 'Forest', ref=f'azusa-land-{index}') for index in range(2)]
        held_lands = [
            self.add(game.engine, 'Forest', zone='hand', ref=f'azusa-held-land-{index}')
            for index in range(3)
        ]
        opposing_land = self.add(game.engine, 'Forest', seat='B', ref='azusa-opposing-land')
        opposing_land.tapped = True
        blocker = self.add(game.engine, 'Grizzly Bears', seat='B')
        self.assert_closed(game, source)
        self.prepare(game, mana={'C': 3, 'G': 1, 'U': 2})
        self.checkpoint(game)

        starting_life = game.state.players['A'].life
        self.cast(game, 'A', source)
        self.assertIsNone(self.resolve(game))
        front = game.state.cards[source.object_id]
        self.assertEqual('battlefield', front.zone)
        self.assertEqual(1, front.counters['lore'])
        original_identity = front.logical_object_id
        self.priority(game, 'A')
        for land in held_lands[:2]:
            result = game.act('pilot:A', {'action_id': 'play-land:' + land.ref})
            self.assertTrue(result.ok, result.summary)
        actions = game.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        third = 'play-land:' + held_lands[2].ref
        self.assertNotIn(third, [action['id'] for action in actions])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act('pilot:A', {'action_id': third}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        for plan in plans:
            self.cast(game, 'A', plan)
            self.assertEqual('semantic.choice', self.resolve(game).kind)
            result = game.act('pilot:A', {'action_id': 'choose', 'objects': [source.ref], 'plan': 'PROLIFERATE'})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
            self.assertEqual(starting_life + 3, game.state.players['A'].life)
        returned = game.state.cards[source.object_id]
        self.assertEqual('battlefield', returned.zone)
        self.assertEqual('A', returned.controller)
        self.assertEqual('Likeness of the Seeker', returned.active_face)
        self.assertNotEqual(original_identity, returned.logical_object_id)
        self.assertEqual({}, returned.counters)

        lands = own_lands + held_lands[:2]
        self.priority(game, 'A')
        for land in lands:
            current = game.state.cards[land.object_id]
            decision = game.packet('pilot:A', full=True)['decision']
            action, = (row for row in decision['ctx']['legal']['actions']
                       if row['id'].startswith('activate:' + current.ref + ':'))
            result = game.act('pilot:A', {'action_id': action['id']})
            self.assertTrue(result.ok, result.summary)
            self.assertTrue(game.state.cards[land.object_id].tapped)
        self.attack(game, [(returned, 'B')])
        self.until(
            game,
            lambda session: session.state.pending_decision.kind == 'combat.blockers'
            and 'B' in session.state.pending_decision.actors,
        )
        result = game.act('pilot:B', {'action_id': 'block', 'blocks': {blocker.ref: returned.ref}})
        self.assertTrue(result.ok, result.summary)
        decision = self.resolve(game)
        self.assertEqual('semantic.choice', decision.kind)
        self.assertEqual(['A'], decision.actors)
        refs = [game.state.cards[land.object_id].ref for land in lands]
        before = authoritative_state_hash(game.state)
        for seat, selected in (('B', refs[:3]), ('A', refs), ('A', [opposing_land.ref])):
            result = game.act('pilot:' + seat, {'action_id': 'choose', 'objects': selected})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act('pilot:A', {'action_id': 'choose','objects': refs[:3]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            [False, False, False, True],
            [game.state.cards[land.object_id].tapped for land in lands],
        )
        self.assertTrue(game.state.cards[opposing_land.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_pending_terminal_does_not_chase_a_blinked_new_source_incarnation(self):
        session,source,blink,private = self.begin_terminal(986_200)
        old = session.state.cards[source.object_id].logical_object_id
        self.cast(session,'A',blink,targets=[source.ref])
        self.assertIsNone(self.resolve(session))
        current=session.state.cards[source.object_id]
        self.assertEqual('battlefield',current.zone)
        self.assertEqual(self.db.by_oracle_id(source.oracle_id).faces[0]['name'],current.active_face)
        self.assertEqual('B',current.controller)
        self.assertNotEqual(old,current.logical_object_id)
        self.assertEqual({'lore':1},current.counters)
        self.replay(session,load=True)

    def test_fresh_front_face_entry_and_owner_return_mutants_are_killed(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        original = ZoneTransitionOwner.move_cards_simultaneously
        for mode,message in (('front','back face must be selected'),('owner','return controller must come')):
            observed=[]
            def wrong_entry(owner, changes, **kwargs):
                if kwargs.get('entry_faces'):
                    observed.append(mode)
                    kwargs = dict(kwargs)
                    if mode=='front':kwargs['entry_faces']={}
                    else:kwargs['destination_controllers']={key:'B' for key in kwargs['entry_faces']}
                return original(owner,changes,**kwargs)
            with self.subTest(mode=mode), patch.object(ZoneTransitionOwner,'move_cards_simultaneously',wrong_entry):
                with self.assertRaisesRegex(AssertionError,message):self.witness(986_300+len(mode))
            self.assertTrue(observed)
