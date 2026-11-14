"""CR608.2c: fixed life gain is independent of damage actually dealt."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
import test_bound_effect_programs as records
import test_recipient_control as recipients
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.compiler.fixed_damage_life_sequences import fixed_damage_life_clauses
from quorune.compiler.fixed_effect_clause_sequences import FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC
from scripts.build_test_database import build_fixture_database,compact_ci_fixture_paths

CAPABILITY='resolution.effect_sequence.fixed_clauses'
NAMES=('Lavabelly Sliver','Fear of Lost Teeth','Ill-Gotten Inheritance','Snapdax, Apex of the Hunt',
    'Boomerang, Blade Flinger','Gastal Thrillseeker','Momo, Rambunctious Rascal','The Wasp, Janet Van Dyne')


class DamageLifeCompilerTests(unittest.TestCase):
    def test_literal_conjunction_keeps_two_closed_instructions_and_source_pronouns_stay_bound(self):
        reg=load_default_capability_registry()
        for pronoun in ('it','he','she'):
            text=f'When this creature enters, {pronoun} deals 2 damage to any target and you gain 3 life.'
            ir=compile_oracle_card(records.record(text,'Creature — Human'),capability_registry=reg,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals);node,=ir.faces[0].nodes
            self.assertEqual(('damage','life'),tuple(e['op'] for e in node.effects))
            self.assertEqual(('$source',2,'$controller',3),(node.effects[0]['source'],node.effects[0]['amount'],node.effects[1]['player'],node.effects[1]['delta']))
            self.assertIn(CAPABILITY,node.capability_dependencies)
            self.assertIn(FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC,node.mechanics)
        text='It deals 2 damage to any target and you gain 3 life.'
        self.assertEqual(('It deals 2 damage to any target.','You gain 3 life.'),fixed_damage_life_clauses(text))
        bare=compile_oracle_card(records.record(text,'Instant'),capability_registry=reg,capability_profile='commander_review')
        self.assertNotEqual('exact',bare.status)

    def test_unknown_linked_amounts_nested_text_and_extra_tails_are_not_reinterpreted(self):
        for text in ('This creature deals X damage to any target and you gain X life.',
                'This creature deals 2 damage to any target and you gain that much life.',
                'This creature deals 2 damage to any target and you gain 2 life for each opponent.',
                'This creature deals 2 damage to any target and you gain 2 life. Draw a card.',
                'Create a token with "This token deals 2 damage to any target and you gain 2 life."',
                'This creature deals 2 damage to any target (and you gain 2 life).'):
            self.assertIsNone(fixed_damage_life_clauses(text),text)


class DamageLifeNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.RecipientControlNativeTests.reject
    finish=recipients.RecipientControlNativeTests.finish
    finish_turn=recipients.RecipientControlNativeTests.finish_turn

    @classmethod
    def setUpClass(cls):
        names={*NAMES,'Sliver Hivelord','Generic Bound Plains','Counterspell','Fervor','Murder','Healing Salve',
            'Giant Cindermaw','Grizzly Bears','Darksteel Myr','Unsummon','Island','Metallic Sliver','Wrong Turn',
            'Magnetic Theft','Basilisk Collar','Furnace of Rath','Dictate of the Twin Gods'}
        docs=[json.loads(p.read_text(encoding='utf-8')) for p in (*compact_ci_fixture_paths(root=ROOT),ROOT/'tests/fixtures/bound-effect-program-cards.json')]
        cards={r['name']:r for d in docs for r in d['cards'] if r['name'] in names};assert set(cards)==names,names-set(cards)
        ids={r['oracle_id'] for r in cards.values()};rulings={tuple(sorted(r.items())):r for d in docs for r in d.get('rulings',[]) if r['oracle_id'] in ids}
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        doc=json.loads((ROOT/'tests/fixtures/recipient-control-cards.json').read_text(encoding='utf-8'));doc.update(cards=list(cards.values()),rulings=list(rulings.values()))
        fixture=folder/'damage-life.json';fixture.write_text(json.dumps(doc),encoding='utf-8');path=folder/'damage-life.sqlite3';build_fixture_database([fixture],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Independent fixed life gain',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def scene(self,name,before=None,*,zone='hand'):
        s=self.session(2021038+len(name));source=self.add(s.engine,name,zone=zone)
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand');self.add(s.engine,'Fervor')
        extra=before(s,source) if before else {}
        self.assert_closed(s,source);s.state.config.semantic_policy='trusted_only'
        self.prepare(s,mana={'C':30,**{c:10 for c in 'WUBRG'}});self.checkpoint(s)
        return s,source,private,extra

    def choose_target(self,s,targets,*,actor='A'):
        self.assertEqual('semantic.target',self.resolve(s).kind)
        command={'action_id':'choose','targets':targets};self.reject(s,'D',command)
        result=s.act('pilot:'+actor,command);self.assertTrue(result.ok,result.summary)

    def activate(self,s,source,targets):
        self.priority(s,'A');row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':row['id'],'targets':targets,'pay':'auto'};self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_originals_bind_normally_and_sequence_authority_cannot_be_omitted(self):
        for name in NAMES:
            with self.subTest(name=name):
                c=self.db.lookup(name,fuzzy=False);ir=compile_oracle_card(c,capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                p=_compile_best_available_card_program_from_ir(self.db,c,ir,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
                b=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review');self.assertTrue(b['strict_capability_ready'],(name,b['blockers']));self.assertEqual('capability_closed',b['trust_basis'])
        s,source,private,extra=self.scene('Fear of Lost Teeth',zone='battlefield')
        p,=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.effects]
        self.assertIn(CAPABILITY,p.capability_dependencies)
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAPABILITY).update(status='blocked',blockers=['sequence owner withdrawn'])
        withheld=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        withheld.mark_evidence_verified(validate_capability_evidence_index(index,registry=withheld))
        text='When this creature enters, it deals 2 damage to any target and you gain 3 life.'
        self.assertNotEqual('exact',compile_oracle_card(records.record(text,'Creature — Human'),capability_registry=withheld,capability_profile='commander_review').status)
        self.assertEqual('exact',compile_oracle_card(records.record('Draw a card.','Instant'),capability_registry=withheld,capability_profile='commander_review').status)

    def test_actual_source_dies_then_prevented_damage_still_gains_the_fixed_life_once(self):
        def before(s,source):
            s.state.players['B'].mana_pool.update({'C':1,'B':2,'W':2})
            return {'kill':self.add(s.engine,'Murder',seat='B',zone='hand'),'salve':self.add(s.engine,'Healing Salve',seat='B',zone='hand')}
        s,source,private,extra=self.scene('Fear of Lost Teeth',before,zone='battlefield')
        self.cast(s,'B',extra['kill'],[source.ref]);self.choose_target(s,['B'])
        self.priority(s,'B');row=self.action(s,'B',extra['salve'])
        result=s.act('pilot:B',{'action_id':row['id'],'modes':['mode_2'],'targets':['B'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.resolve_top(s);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual((41,40),tuple(s.state.players[p].life for p in ('A','B')));self.finish(s,private)

    def test_actual_entry_damage_and_life_gain_prohibition_are_independent(self):
        for prohibited in (False,True):
            with self.subTest(prohibited=prohibited):
                def before(s,source):
                    if prohibited:self.add(s.engine,'Giant Cindermaw',seat='D')
                s,source,private,extra=self.scene('Gastal Thrillseeker',before)
                self.cast(s,'A',source);self.choose_target(s,['B']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual((40 if prohibited else 41,39),tuple(s.state.players[p].life for p in ('A','B')));self.finish(s,private)

    def test_actual_illegal_death_trigger_target_prevents_both_instructions(self):
        def before(s,source):
            s.state.players['B'].mana_pool.update({'C':1,'B':2,'U':2})
            return {'kill':self.add(s.engine,'Murder',seat='B',zone='hand'),
                'body':self.add(s.engine,'Grizzly Bears',seat='B'),'bounce':self.add(s.engine,'Unsummon',seat='B',zone='hand')}
        s,source,private,extra=self.scene('Fear of Lost Teeth',before,zone='battlefield')
        self.cast(s,'B',extra['kill'],[source.ref]);self.choose_target(s,[extra['body'].ref])
        self.cast(s,'B',extra['bounce'],[extra['body'].ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[extra['body'].object_id].zone)
        self.assertEqual((40,40),tuple(s.state.players[p].life for p in ('A','B')));self.finish(s,private)

    def test_actual_granted_receiver_damage_and_fixed_gain_keep_separate_controllers(self):
        def before(s,source):return {'body':self.add(s.engine,'Metallic Sliver',zone='hand'),
            'gift':self.add(s.engine,'Wrong Turn',zone='hand'),'collar':self.add(s.engine,'Basilisk Collar'),
            'attach':self.add(s.engine,'Magnetic Theft',zone='hand')}
        s,source,private,extra=self.scene('Lavabelly Sliver',before,zone='battlefield')
        self.cast(s,'A',extra['body']);self.choose_target(s,['B'])
        self.assertEqual(extra['body'].object_id,s.state.stack[-1].source_object_id)
        self.cast(s,'A',extra['attach'],[extra['collar'].ref,extra['body'].ref]);self.resolve_top(s)
        self.cast(s,'A',extra['gift'],['D',extra['body'].ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('D',s.state.cards[extra['body'].object_id].controller)
        self.assertEqual((41,39,40,41),tuple(s.state.players[p].life for p in 'ABCD'));self.finish(s,private)

    def test_actual_attack_hits_each_opponent_but_gains_only_one_life(self):
        s,source,private,extra=self.scene('Boomerang, Blade Flinger',zone='battlefield')
        self.attack(s,[(source,'B')]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((41,39,39,39),tuple(s.state.players[p].life for p in 'ABCD'));self.finish(s,private)

    def test_actual_hybrid_mutation_uses_its_merged_source_and_fixed_gain(self):
        def before(s,source):return {'host':self.add(s.engine,'Grizzly Bears'),'target':self.add(s.engine,'Darksteel Myr',seat='B')}
        s,source,private,extra=self.scene('Snapdax, Apex of the Hunt',before)
        row=self.action(s,'A',source);option=next(o for o in row['cost_options'] if o['id'].startswith('mutate') and o['requirements'].get('B')==1)
        command={'action_id':row['id'],'cost_option':option['id'],'targets':[extra['host'].ref],'pay':'auto'}
        self.reject(s,'B',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'choose','choice':'top'});self.assertTrue(result.ok,result.summary)
        self.choose_target(s,[extra['target'].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(4,s.state.cards[extra['target'].object_id].marked_damage);self.assertEqual(44,s.state.players['A'].life)
        self.assertEqual(1,s.state.cards[extra['host'].object_id].permanent_composition.topology.mutation_count);self.finish(s,private)

    def test_actual_gendered_entry_sources_keep_the_tapped_opponent_predicate(self):
        for name in ('Momo, Rambunctious Rascal','The Wasp, Janet Van Dyne'):
            with self.subTest(name=name):
                def before(s,source):
                    body=self.add(s.engine,'Darksteel Myr',seat='B');body.tapped=True
                    return {'body':body,'untapped':self.add(s.engine,'Darksteel Myr',seat='B',ref='untapped-entry-boundary')}
                s,source,private,extra=self.scene(name,before);self.cast(s,'A',source)
                self.assertEqual('semantic.target',self.resolve(s).kind)
                self.reject(s,'A',{'action_id':'choose','targets':[extra['untapped'].ref]})
                result=s.act('pilot:A',{'action_id':'choose','targets':[extra['body'].ref]});self.assertTrue(result.ok,result.summary)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(4,s.state.cards[extra['body'].object_id].marked_damage)
                self.assertEqual(40,s.state.players['A'].life);self.finish(s,private)

    def test_actual_sacrificed_activation_resumes_damage_replacements_then_gains_once(self):
        def before(s,source):return {'furnace':self.add(s.engine,'Furnace of Rath'),'dictate':self.add(s.engine,'Dictate of the Twin Gods')}
        s,source,private,extra=self.scene('Ill-Gotten Inheritance',before,zone='battlefield')
        self.activate(s,source,['B']);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual('replacement.order',self.resolve(s).kind);s=self.replay(s,load=True)
        for _ in range(12):
            d=self.resolve(s)
            if d is None:break
            self.assertEqual('replacement.order',d.kind);self.assertEqual((40,40),tuple(s.state.players[p].life for p in ('A','B')))
            principal=s.pending_principals()[0];option=s.packet(principal,full=True)['decision']['ctx']['options'][0]['id']
            command={'action_id':'choose','choices':{'replacement':option}};self.reject(s,'D',command)
            result=s.act(principal,command);self.assertTrue(result.ok,result.summary)
        else:self.fail('Damage/life continuation did not settle')
        self.assertEqual((44,24),tuple(s.state.players[p].life for p in ('A','B')));self.finish(s,private)

    def test_fresh_conjunction_omission_is_killed_by_the_native_controller_witness(self):
        with patch('quorune.compiler.fixed_damage_life_sequences.fixed_damage_life_clauses',return_value=None),self.assertRaises(AssertionError):
            type(self)('test_actual_granted_receiver_damage_and_fixed_gain_keep_separate_controllers').test_actual_granted_receiver_damage_and_fixed_gain_keep_separate_controllers()


if __name__=='__main__':unittest.main()
