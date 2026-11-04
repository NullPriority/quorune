"""Entry counters are part of the original graveyard-to-battlefield proposal."""
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record


class ReanimationEntryCounterCompilerTests(unittest.TestCase):
    def test_complete_fixed_entry_counter_forms_require_their_reanimation_and_counter_owners(self):
        registry=load_default_capability_registry()
        for counter in ('two additional +1/+1 counters','a lifelink counter'):
            body='Return target creature card from your graveyard to the battlefield with '+counter+' on it.'
            for text,type_line in ((body,'Sorcery'),('When this creature enters, '+body[0].lower()+body[1:],'Creature — Human'),
                    ('{4}{B}: '+body,'Creature — Human')):
                with self.subTest(text=text):
                    ir=compile_oracle_card(record(text,type_line),capability_registry=registry)
                    self.assertEqual('exact',ir.status,ir.material_residuals)
                    node=ir.faces[0].nodes[0];self.assertIn('zone.reanimate.entry_counters',node.capability_dependencies)
                    self.assertIn('counter.producer.effect_entry',node.capability_dependencies)
                    effect=node.effects[0];self.assertEqual(3,effect['schema_version'])
                    self.assertEqual('$target.0',effect['card']);self.assertEqual('$stack',effect['source'])
                    self.assertEqual({'counter_name':'+1/+1','amount':2} if 'additional' in counter else {'counter_name':'lifelink','amount':1},effect['entry_counter'])
                    self.assertNotIn('target_snapshot',effect)
                    if 'lifelink' in counter:self.assertIn('counter.characteristic.keyword',node.capability_dependencies)

    def test_unrepresented_counter_rules_variable_counts_and_linked_riders_remain_residual(self):
        registry=load_default_capability_registry();prefix='Return target creature card from your graveyard to the battlefield with '
        for suffix in ('a shield counter on it.','X additional +1/+1 counters on it.',
                'two additional +1/+1 counter on it.','a lifelink counters on it.',
                'a lifelink counter on it. Rewrite history.','a quantum counter on it.',
                'two additional +1/+1 counters on it and it gains an undefined ability.'):
            with self.subTest(suffix=suffix):self.assertNotEqual('exact',compile_oracle_card(record(prefix+suffix,'Sorcery'),capability_registry=registry).status)

    def test_entry_counter_quantity_mutation_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler.reanimation_entry_counter_templates import FixedReanimationEntryCounterTemplate
        original=FixedReanimationEntryCounterTemplate.compiled
        def mutated(template):
            name,effects,schema,mechanics=original(template)
            return name,({**effects[0],'entry_counter':{**effects[0]['entry_counter'],'amount':1}},),schema,mechanics
        with patch.object(FixedReanimationEntryCounterTemplate,'compiled',mutated):
            result=unittest.TestResult()
            ReanimationEntryCounterCompilerTests('test_complete_fixed_entry_counter_forms_require_their_reanimation_and_counter_owners').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)

    def test_verified_entry_counter_authority_withdrawal_preserves_ordinary_reanimation(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id']=='zone.reanimate.entry_counters').update(status='blocked',blockers=['Entry counter authority withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        base='Return target creature card from your graveyard to the battlefield'
        self.assertNotEqual('exact',compile_oracle_card(record(base+' with two additional +1/+1 counters on it.','Sorcery'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(record(base+'.','Sorcery'),capability_registry=registry).status)

    def test_runtime_minimum_ignores_annotations_and_the_intent_pins_identity_and_counter_placer(self):
        from types import SimpleNamespace
        from quorune.compiler.reanimation_entry_counter_templates import fixed_reanimation_entry_counter_template
        from quorune.reanimation_entry_counters import reanimation_entry_counter_intent
        from quorune.rules.reanimation_entry_counters import reanimation_entry_counter_runtime_dependencies
        template=fixed_reanimation_entry_counter_template('Put target creature card from a graveyard onto the battlefield under its owner\'s control with two additional +1/+1 counters on it.')
        self.assertIsNotNone(template);_,effects,schema,_=template.compiled()
        program=SimpleNamespace(effects=effects,target_schema=schema,coverage=())
        self.assertIn('zone.reanimate.entry_counters',reanimation_entry_counter_runtime_dependencies(program))
        snapshot={'ref':'CARD','object_id':'physical-card','zone_change_counter':3}
        materialized={**effects[0],'card':'CARD','controller':'B','source':'STACK','target_snapshot':snapshot}
        intent=reanimation_entry_counter_intent(materialized,actor='A',reason='Return with counters')
        self.assertEqual(3,intent.expected_zone_change_counter);self.assertEqual('B',intent.new_controller)
        self.assertEqual(('B','STACK',2),tuple(getattr(intent.effect_entry_counters[0],k) for k in ('placing_player','source_ref','amount')))
        for change in ({'schema_version':True},{'schema_version':1},{'source':'CARD'},
                {'entry_counter':{'counter_name':'shield','amount':1}},
                {'entry_counter':{'counter_name':'decayed','amount':1}},
                {'entry_counter':{'counter_name':'+1/+1','amount':True}}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                reanimation_entry_counter_runtime_dependencies(SimpleNamespace(**{**vars(program),'effects':({**effects[0],**change},)}))
        for change in ({'target_snapshot':{}},{'target_snapshot':{**snapshot,'ref':'OTHER'}},
                {'target_snapshot':{**snapshot,'zone_change_counter':True}}, {'_replacement_selections':'unsealed'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                reanimation_entry_counter_intent({**materialized,**change},actor='A',reason='Return with counters')


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class ReanimationEntryCounterNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'reanimation-entry-counters.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/reanimation-entry-counter-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Entry-counter reanimation',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def reject(self,s,seat,command):
        before=authoritative_state_hash(s.state);result=s.act('pilot:'+seat,command)
        self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))

    def test_actual_reawakened_waits_before_entry_and_combines_counter_replacements_before_zero_toughness_checks(self):
        s=self.session(122614120);spell=self.add(s.engine,'Evil Reawakened',zone='hand')
        target=self.add(s.engine,'Stonecoil Serpent',zone='graveyard',ref='actual-zero-toughness-return')
        scales=self.add(s.engine,'Hardened Scales');doubling=self.add(s.engine,'Doubling Season')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='entry-counter-private')
        for card in (spell,scales):self.assert_closed(s,card)
        # Doubling Season's reviewed counter component is an interaction
        # control; this witness makes no whole-card admission claim for it.
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        control=compile_best_available_card_program(self.db,self.db.lookup(doubling.printed_name),
            semantic_registry=s.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(control,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['compatible_ready'])
        if not binding['strict_capability_ready']:
            self.assertTrue(any('replacement.counter.quantity.v2' in row['handler_ids']
                for row in binding['compatibility_provenance']))
        self.prepare(s,mana={'B':1,'C':4});self.checkpoint(s);self.cast(s,'A',spell,[target.ref])
        decision=self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('replacement.order',decision.kind)
        self.assertEqual('graveyard',s.state.cards[target.object_id].zone);self.assertFalse(s.state.cards[target.object_id].counters)
        packet=s.packet('pilot:A',full=True)['decision'];options=packet['ctx']['options']
        selected=next(row['id'] for row in options if scales.ref in row['id'] and not row['id'].startswith('decline:'))
        self.reject(s,'B',{'action_id':'choose','replacement':selected})
        self.reject(s,'A',{'action_id':'choose','replacement':'invalid'})
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        s=self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'choose','replacement':selected});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));current=s.state.cards[target.object_id]
        self.assertEqual('battlefield',current.zone);self.assertEqual(6,current.counters.get('+1/+1',0))
        self.assertEqual((6,6),tuple(s.engine._numeric_stat(current.object_id,k) for k in ('power','toughness')))
        self.replay(s,load=True)

    def test_actual_unbreakable_bond_installs_the_keyword_counter_and_its_lifelink_damage(self):
        s=self.session(122614121);spell=self.add(s.engine,'Unbreakable Bond',zone='hand')
        target=self.add(s.engine,'Memnite',zone='graveyard',ref='actual-keyword-counter-return')
        other=self.add(s.engine,'Phyrexian Walker',seat='B');fight=self.add(s.engine,'Prey Upon',zone='hand')
        self.add(s.engine,'Forest');self.assert_closed(s,spell);self.assert_closed(s,fight)
        self.prepare(s,mana={'B':1,'C':4});self.checkpoint(s)
        self.reject(s,'B',{'action_id':'cast:'+spell.ref,'targets':[target.ref],'pay':'auto'})
        self.cast(s,'A',spell,[target.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        current=s.state.cards[target.object_id];self.assertEqual('battlefield',current.zone)
        self.assertEqual(1,current.counters.get('lifelink',0));self.assertIn('lifelink',s.engine._combat_keywords(current))
        self.cast(s,'A',fight,[target.ref,other.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(41,s.state.players['A'].life);self.assertEqual(1,s.state.cards[other.object_id].marked_damage)
        self.replay(s,load=True)
