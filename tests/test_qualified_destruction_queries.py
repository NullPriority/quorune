"""CR701.8/608.2h/903.3: fixed qualified simultaneous destruction sets."""
import unittest
from quorune.compiler.destruction_templates import mass_destruction_effect_template
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record
from dataclasses import replace


class QualifiedDestructionCompilerTests(unittest.TestCase):
    def test_fixed_existing_query_fields_lower_to_one_simultaneous_set(self):
        cases={
            'Destroy all creatures with toughness 4 or greater.':('numeric_characteristic',{'characteristic':'toughness','comparison':'at_least','value':4}),
            'Destroy all creatures with power 4 or greater.':('numeric_characteristic',{'characteristic':'power','comparison':'at_least','value':4}),
            'Destroy all nonwhite creatures.':('colors_none',['W']),
            'Destroy all nonbasic lands.':('excluded_supertypes',['basic']),
            'Destroy all non-Aura enchantments.':('excluded_subtypes',['aura']),
            'Destroy all creatures and planeswalkers except for commanders.':('commander',False),
        }
        registry=load_default_capability_registry()
        for text,(field,value) in cases.items():
            with self.subTest(text=text):
                template=mass_destruction_effect_template(text);self.assertIsNotNone(template)
                self.assertEqual(value,template.spec.query.to_dict()[field])
                self.assertEqual(1,len(template.effects));self.assertEqual('destroy_all',template.effects[0]['op'])
                ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)

    def test_descriptor_owned_withdrawal_rejects_qualified_selection_and_keeps_plain_destroy(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.rules.capabilities import capability_dependencies_for_node
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        template=mass_destruction_effect_template('Destroy all nonwhite creatures.')
        self.assertIn('permanent.destroy.qualified_fixed_set',capability_dependencies_for_node(
            effects=template.effects,target_schema=None,mechanic_ids=template.mechanics))
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        self.assertIn('permanent.destroy.qualified_fixed_set',typed_result_amount_runtime_dependencies(template.effects[0]))
        self.assertIn('state_query.permanent.negative_color',typed_result_amount_runtime_dependencies(template.effects[0]))
        next(c for c in raw['capabilities'] if c['id']=='permanent.destroy.qualified_fixed_set').update(status='blocked',blockers=['Qualified set authority withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,expected in (('Destroy all nonwhite creatures.',False),('Destroy all creatures.',True)):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(expected,ir.status=='exact')

    def test_aura_equipment_union_retains_domains_and_plain_aura_is_not_all_enchantments(self):
        aura=mass_destruction_effect_template('Destroy all Auras.');self.assertIsNotNone(aura)
        self.assertEqual(('enchantment',),aura.spec.query.types_all)
        self.assertEqual(('aura',),aura.spec.query.subtypes_all)
        union=mass_destruction_effect_template('Destroy all Auras and Equipment.');self.assertIsNotNone(union)
        self.assertEqual(2,len(union.spec.query.any_of))
        self.assertEqual({(('enchantment',),('aura',)),(('artifact',),('equipment',))},
            {(part.types_all,part.subtypes_all) for part in union.spec.query.any_of})

    def test_unrepresented_siblings_and_open_queries_remain_residual(self):
        for text in ('Destroy all creatures with power X or greater.',
                'Destroy all creatures of the creature type of your choice.',
                'Destroy all creatures and all permanents attached to creatures.',
                'Destroy all Auras and Equipment attached to target creature.',
                "Destroy all creatures that aren't enchanted.",
                'Destroy all creatures with the remembered quality.'):
            with self.subTest(text=text):self.assertIsNone(mass_destruction_effect_template(text))


class QualifiedDestructionSelectionTests(unittest.TestCase):
    def test_fixed_selection_distinguishes_numeric_color_subtype_basic_and_commander_facts(self):
        from quorune.object_query import ObjectQueryResult
        from quorune.affected_permanents import select_affected_permanents
        def row(ref,**fields):
            for field in ('power','toughness'):
                if field in fields:fields['effective_'+field]=fields.pop(field)
            return ObjectQueryResult(object_id=ref,logical_object_id=ref+'@0',ref=ref,printed_name='Generic '+ref,
                owner='A',controller=fields.pop('controller','A'),zone='battlefield',**fields)
        rows=(row('small',types=('creature',),power=3,toughness=3),
            row('equal',types=('creature',),power=4,toughness=4),
            row('white',types=('creature',),colors=('W','R'),power=5,toughness=5),
            row('commander',types=('creature',),commander=True,power=5,toughness=5),
            row('legendary',types=('creature',),supertypes=('legendary',),power=5,toughness=5),
            row('aura',types=('enchantment',),subtypes=('aura',)),
            row('equipment',types=('artifact',),subtypes=('equipment',)),
            row('enchantment',types=('enchantment',)),
            row('basic',types=('land',),supertypes=('basic',)),
            row('typed-land',types=('land',),subtypes=('plains',)))
        cases={
            'Destroy all creatures with power 4 or greater.':{'equal','white','commander','legendary'},
            'Destroy all nonwhite creatures.':{'small','equal','commander','legendary'},
            'Destroy all creatures and planeswalkers except for commanders.':{'small','equal','white','legendary'},
            'Destroy all Auras and Equipment.':{'aura','equipment'},
            'Destroy all non-Aura enchantments.':{'enchantment'},
            'Destroy all nonbasic lands.':{'typed-land'},
        }
        for text,expected in cases.items():
            with self.subTest(text=text):
                selected=select_affected_permanents(rows,mass_destruction_effect_template(text).spec,
                    actor='A',active_seats=tuple('ABCD'),apnap_order=tuple('CDAB'),source_ref='spell')
                self.assertEqual(expected,{r.ref for r in selected})

    def test_omitting_commander_exclusion_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler import qualified_destruction_queries as owner
        original=owner.qualified_destruction_query
        def omit(phrase,**kwargs):
            result=original(phrase,**kwargs)
            return replace(result,commander=None) if result is not None and result.commander is False else result
        method='test_fixed_selection_distinguishes_numeric_color_subtype_basic_and_commander_facts'
        baseline=unittest.TestResult();QualifiedDestructionSelectionTests(method).run(baseline)
        self.assertTrue(baseline.wasSuccessful(),baseline.errors+baseline.failures)
        with patch.object(owner,'qualified_destruction_query',side_effect=omit):
            result=unittest.TestResult();QualifiedDestructionSelectionTests(method).run(result)
        self.assertFalse(result.errors,result.errors);self.assertEqual(1,len(result.failures))


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.attachments import attach_objects
from scripts.build_test_database import build_fixture_database


class QualifiedDestructionNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'qualified-destruction.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/qualified-destruction-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Qualified destruction',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def test_actual_threshold_reads_current_toughness_after_response_and_replays(self):
        s=self.session(701800201)
        wipe=self.add(s.engine,'Citywide Bust',zone='hand')
        exact=self.add(s.engine,'Loxodon Smiter',seat='B')
        boosted=self.add(s.engine,'Grizzly Bears',seat='C',ref='threshold-boosted')
        below=self.add(s.engine,'Grizzly Bears',seat='D',ref='threshold-below')
        response=self.add(s.engine,'Giant Growth',seat='C',zone='hand')
        land=self.add(s.engine,'Forest',seat='C')
        private=self.add(s.engine,'Counterspell',seat='D',zone='hand')
        for card in (wipe,response):self.assert_closed(s,card)
        self.prepare(s,mana={'W':2,'C':1});self.checkpoint(s)
        self.cast(s,'A',wipe,[]);self.priority(s,'C')
        actions=s.packet('pilot:C',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in actions if row.get('mana_ability') and land.ref in row['id'])
        self.assertTrue(s.act('pilot:C',{'action_id':mana['id'],'pay':'auto'}).ok)
        self.cast(s,'C',response,[boosted.ref]);self.resolve_top(s)
        self.assertEqual(5,s.engine._numeric_stat(boosted.object_id,'toughness'))
        before=authoritative_state_hash(s.state)
        wrong=next(seat for seat in 'ABCD' if 'pilot:'+seat not in s.pending_principals())
        self.assertFalse(s.act('pilot:'+wrong,{'action_id':'pass'}).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(['graveyard','graveyard','battlefield'],[s.state.cards[c.object_id].zone for c in (exact,boosted,below)])
        for viewer in 'ABC':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_aura_equipment_union_nonwhite_and_nonbasic_keep_distinct_public_facts(self):
        s=self.session(701800203)
        bearer=self.add(s.engine,'Grizzly Bears',seat='B')
        aura=self.add(s.engine,'Pacifism',seat='C')
        equipment=self.add(s.engine,'Bonesplitter',seat='D')
        for source in (aura,equipment):attach_objects(s.state.cards,source,bearer,source_timestamp=s.state.turn_sequence+1,players=s.state.players)
        enchantment=self.add(s.engine,'Glorious Anthem')
        white=self.add(s.engine,'Elite Vanguard',seat='C')
        artifact=self.add(s.engine,'Memnite',seat='D')
        basic=self.add(s.engine,'Plains',seat='B')
        typed=self.add(s.engine,'Savannah',seat='C')
        simple=self.add(s.engine,'Pure // Simple',zone='hand')
        color=self.add(s.engine,'Mass Calcify',zone='hand')
        lands=self.add(s.engine,'Ruination',zone='hand')
        for card in (simple,color,lands):self.assert_closed(s,card)
        self.prepare(s,mana={'W':12,'G':6,'R':2,'C':20});self.checkpoint(s)
        self.priority(s,'A')
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in rows if row.get('card')==simple.ref and row.get('face')=='Simple')
        self.assertTrue(s.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(['graveyard','graveyard','battlefield'],[s.state.cards[c.object_id].zone for c in (aura,equipment,enchantment)])
        self.cast(s,'A',color,[]);self.assertIsNone(self.resolve(s))
        self.assertEqual(['graveyard','graveyard','battlefield'],[s.state.cards[c.object_id].zone for c in (bearer,artifact,white)])
        self.cast(s,'A',lands,[]);self.assertIsNone(self.resolve(s))
        self.assertEqual(['battlefield','graveyard'],[s.state.cards[c.object_id].zone for c in (basic,typed)])
        self.replay(s,load=True)

    def test_actual_commander_exclusion_preserves_designation_and_destroys_ordinary_legendary(self):
        s=self.session(701800202)
        commanders=[next(c for c in s.state.cards.values() if c.owner==seat and c.is_commander) for seat in 'AB']
        for card in commanders:s.engine.move_card(card.object_id,'battlefield',controller=card.owner,log=False)
        ordinary=self.add(s.engine,'Isamaru, Hound of Konda',seat='C',ref='ordinary-legendary')
        body=self.add(s.engine,'Grizzly Bears',seat='D')
        planeswalker=self.add(s.engine,'Jace Beleren',seat='C')
        planeswalker.counters['loyalty']=3
        wipe=self.add(s.engine,'Slash the Ranks',zone='hand')
        self.assert_closed(s,wipe);self.prepare(s,mana={'W':2,'C':3});self.checkpoint(s)
        self.cast(s,'A',wipe,[]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(['battlefield','battlefield'],[s.state.cards[c.object_id].zone for c in commanders])
        self.assertEqual(['graveyard']*3,[s.state.cards[c.object_id].zone for c in (ordinary,body,planeswalker)])
        self.replay(s,load=True)
