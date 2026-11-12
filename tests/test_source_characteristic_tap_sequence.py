"""CR400.7/608.2c/701.26a: a source characteristic precedes its tap instruction."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_effect_clause_shapes import fixed_effect_clause_sequence_node_capabilities
from scripts.build_test_database import build_fixture_database

COMPOSITION = 'resolution.effect_sequence.fixed_clauses'
SOURCE = 'continuous.resolution.fixed_source_characteristics_until_end_of_turn'
TAP = 'permanent.tap.effect'


class SourceCharacteristicTapCompilerTests(unittest.TestCase):
    def test_explicit_source_antecedent_uses_two_existing_closed_leaf_owners(self):
        registry = load_default_capability_registry()
        for first,last in (('This creature gains indestructible until end of turn.','Tap it.'),
                ('This creature gets +2/+0 until end of turn.','Untap it.')):
            record = query_record('{1}: '+first+' '+last)
            ir = compile_oracle_card(record,capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node = ir.faces[0].nodes[0];self.assertEqual(2,len(node.effects))
            self.assertEqual('fixed-source-characteristic-tap-sequence-v1',node.template_id)
            self.assertEqual('apply_source_characteristics_until_end_of_turn',node.effects[0]['op'])
            self.assertEqual(last.split()[0].casefold(),node.effects[1]['op'])
            self.assertTrue(all(effect['card']=='$source.zone_object' for effect in node.effects))
            required = fixed_effect_clause_sequence_node_capabilities(effects=node.effects,target_schema=None,mechanic_ids=node.mechanics)
            self.assertIn(COMPOSITION,required);self.assertIn(SOURCE,required)

    def test_target_implicit_subject_unknown_body_and_unrepresented_siblings_remain_material(self):
        from quorune.compiler.fixed_effect_clause_sequences import _two_sentence_clauses
        targeted = 'Target creature gains indestructible until end of turn. Tap it.'
        self.assertEqual(('Target creature gains indestructible until end of turn.','Tap it.'),_two_sentence_clauses(targeted))
        registry = load_default_capability_registry()
        for text in ('It gains indestructible until end of turn. Tap it.',
                'This creature gains mystery until end of turn. Tap it.',
                'This creature gains indestructible until end of turn. Tap it and rewrite history.',
                'This creature gains indestructible until end of turn. Tap it. Rewrite history.'):
            ir = compile_oracle_card(query_record('{1}: '+text),capability_registry=registry)
            self.assertNotEqual('exact',ir.status,text);self.assertTrue(ir.material_residuals)
        record = replace(query_record('This creature gains indestructible until end of turn. Tap it.'),type_line='Instant')
        self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry).status)

    def test_verified_composition_source_and_tap_withdrawal_retain_independent_draw(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        original = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())
        for cap in (COMPOSITION,SOURCE,TAP):
            raw = deepcopy(original);next(row for row in raw['capabilities'] if row['id']==cap).update(status='blocked',blockers=['source/tap owner withdrawn'])
            registry = CapabilityRegistry(raw)
            index = build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(query_record('Discard a card: This creature gains indestructible until end of turn. Tap it.'),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw a card.'),type_line='Sorcery'),capability_registry=registry).status)


class SourceCharacteristicTapNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory();path = Path(cls.temporary.name)/'source-characteristic-tap.sqlite3'
        base = json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings'] = []
        basic = Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/source-characteristic-tap-cards.json'],path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source characteristic tap',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def activate(self,session,source,cost):
        self.priority(session,'A')
        offered = next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+source.ref+':'))
        command = {'action_id':offered['id'],'cost_cards':[cost.ref],'pay':'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result = session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[cost.object_id].zone)

    def finish(self,session,private):
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)

    def test_actual_elf_can_pay_while_tapped_and_protection_expires_without_preventing_zero_toughness(self):
        for tapped in (False,True):
            with self.subTest(tapped=tapped):
                session = self.session(995100+tapped);source = self.add(session.engine,'Iron-Shield Elf');source.owner = 'B';source.tapped = tapped
                first = self.add(session.engine,'Lightning Bolt',seat='B',zone='hand',ref='first-elf-bolt')
                second = self.add(session.engine,'Lightning Bolt',seat='B',zone='hand',ref='second-elf-bolt')
                shrink = self.add(session.engine,'Grasp of Darkness',seat='B',zone='hand')
                self.add(session.engine,'Mountain',seat='B');self.add(session.engine,'Swamp',seat='B',ref='first-elf-swamp');self.add(session.engine,'Swamp',seat='B',ref='second-elf-swamp')
                private = self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='elf-tap-private')
                cost = session.state.cards[session.state.players['A'].zones['hand'][0]]
                self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
                self.activate(session,source,cost);self.assertEqual(tapped,session.state.cards[source.object_id].tapped,'tap is a resolution effect, not an activation cost')
                session = self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertTrue(session.state.cards[source.object_id].tapped)
                self.assertIn('indestructible',session.engine._combat_keywords(session.state.cards[source.object_id]))
                self.cast(session,'B',first,targets=[source.ref]);self.assertIsNone(self.resolve(session))
                self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
                if tapped:
                    self.cast(session,'B',shrink,targets=[source.ref]);self.assertIsNone(self.resolve(session))
                    self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
                else:
                    self.until(session,lambda s:s.state.active_player=='B' and s.state.phase=='precombat_main')
                    self.assertNotIn('indestructible',session.engine._combat_keywords(session.state.cards[source.object_id]))
                    self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
                    self.cast(session,'B',second,targets=[source.ref]);self.assertIsNone(self.resolve(session))
                    self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
                self.assertIn(source.object_id,session.state.players['B'].zones['graveyard']);self.finish(session,private)

    def test_actual_elf_blink_does_not_tap_or_protect_the_returned_incarnation(self):
        session = self.session(995110);source = self.add(session.engine,'Iron-Shield Elf')
        blink = self.add(session.engine,'Cloudshift',zone='hand')
        private = self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='elf-blink-private')
        cost = session.state.cards[next(oid for oid in session.state.players['A'].zones['hand'] if oid!=blink.object_id)]
        identity = source.logical_object_id
        self.assert_closed(session,source);self.prepare(session,mana={'W':1});self.checkpoint(session)
        self.activate(session,source,cost);self.cast(session,'A',blink,targets=[source.ref]);self.resolve_top(session)
        self.assertNotEqual(identity,session.state.cards[source.object_id].logical_object_id)
        session = self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[source.object_id].tapped)
        self.assertNotIn('indestructible',session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.finish(session,private)

    def test_actual_predator_tap_triggers_once_and_already_tapped_activation_does_not(self):
        for tapped in (False,True):
            with self.subTest(tapped=tapped):
                session = self.session(995120+tapped);source = self.add(session.engine,'Immersturm Predator');source.tapped = tapped
                cost = self.add(session.engine,'Grizzly Bears');cost.owner = 'B'
                repeated_cost = self.add(session.engine,'Grizzly Bears',ref='second-predator-payment');repeated_cost.owner = 'B'
                private = self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='predator-tap-private')
                self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
                self.activate(session,source,cost);self.assertEqual(tapped,session.state.cards[source.object_id].tapped)
                session = self.replay(session,load=True);decision = self.resolve(session)
                if tapped:self.assertIsNone(decision)
                else:
                    self.assertEqual('semantic.target',decision.kind)
                    session = self.replay(session,load=True);self.target(session,'A',[]);self.assertIsNone(self.resolve(session))
                self.assertTrue(session.state.cards[source.object_id].tapped)
                self.assertEqual(0 if tapped else 1,session.state.cards[source.object_id].counters.get('+1/+1',0))
                self.activate(session,source,repeated_cost);self.assertIsNone(self.resolve(session))
                self.assertEqual(0 if tapped else 1,session.state.cards[source.object_id].counters.get('+1/+1',0),
                    'an already tapped and indestructible source remains activatable without another tap occurrence')
                self.assertIn(cost.object_id,session.state.players['B'].zones['graveyard'])
                self.assertIn(repeated_cost.object_id,session.state.players['B'].zones['graveyard'])
                self.assertIn('indestructible',session.engine._combat_keywords(session.state.cards[source.object_id]));self.finish(session,private)

    def test_normal_binding_cannot_omit_source_characteristic_or_tap_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(995130);source = self.add(session.engine,'Iron-Shield Elf');self.assert_closed(session,source)
        program, = [program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(effect.get('op')=='tap' for effect in program.effects)]
        for cap in (SOURCE,TAP,COMPOSITION,'permanent.indestructible.ordinary'):
            dependencies = tuple(value for value in program.capability_dependencies if value!=cap)
            missing = replace(program,capability_dependencies=dependencies,capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
        from quorune.rules.fixed_effect_clause_shapes import source_characteristic_tap_runtime_dependencies
        legacy = replace(program,provenance={**program.provenance,'template_id':'fixed-effect-clause-sequence-v1'})
        self.assertEqual((),source_characteristic_tap_runtime_dependencies(legacy))

    def test_fresh_omitted_source_pronoun_binding_is_killed_by_original_normal_admission(self):
        import quorune.compiler.fixed_effect_clause_sequences as owner
        original = owner._two_sentence_clauses;observed = []
        def omitted(text):
            result = original(text);raw = owner._period_sentence_clauses(text)
            if raw is not None and raw[1].casefold()=='tap it.' and result!=raw:
                observed.append(True);return raw
            return result
        session = self.session(995140)
        with patch.object(owner,'_two_sentence_clauses',omitted):
            source = self.add(session.engine,'Iron-Shield Elf')
            with self.assertRaises(AssertionError):self.assert_closed(session,source)
        self.assertTrue(observed)
