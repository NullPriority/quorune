"""CR105.3/613.1e: color scopes, timestamps and complete target companions."""
import json,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from common import ROOT
import test_recipient_control as recipients
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from quorune.semantics import SemanticRegistry
from quorune.compiler.resolution_effect_templates import typed_resolution_effect_template
from quorune.resolution_color_model import decode_resolution_color,TARGET_COLOR_SCOPE_CAPABILITY as CAPABILITY
from quorune.resolution_characteristic_model import decode_fixed_target_color
from quorune.target_cardinality import AnnouncedTargetCardinality,bind_any_number_target_schema
from scripts.build_test_database import build_fixture_database,compact_ci_fixture_paths

NAMES=('Dwarven Song',"Sea Kings' Blessing",'Touch of Darkness','Sylvan Paradise',"Heaven's Gate",'Aurora Griffin',
    'Disciple of Kangee','Viridescent Wisps','Niveous Wisps','Cerulean Wisps','Singe','Indigo Faerie','Ersatz Gnomes',
    'Metathran Transport','Fylamarid','Crimson Wisps','Cloudchaser Kestrel','Aphotic Wisps',"Traitor's Clutch")


class ScopedColorCompilerTests(unittest.TestCase):
    def test_new_color_operands_are_closed_and_legacy_color_bytes_are_unchanged(self):
        r=typed_resolution_effect_template('Target creature becomes blue until end of turn.',card_name='Witness')
        e,=r[1];self.assertEqual(('U',),decode_resolution_color(e)[1])
        for change in ({'schema_version':True},{'color_mode':'choose'},{'colors':['U','G']},
                {'colors':[],'color_mode':'add'},{'duration':'forever'},{'card':'$target.1'},{'extra':1}):
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):decode_resolution_color({**e,**change})
        old=typed_resolution_effect_template('Target spell or permanent becomes blue.',card_name='Witness')
        self.assertEqual({'op':'apply_source_characteristics_until_end_of_turn','schema_version':14,
            'card':'$target.0','colors':['U'],'duration':'zone_object'},old[1][0])
        self.assertEqual(3,len(decode_fixed_target_color(old[1][0])))
        for text in ('Target creature becomes red until end of turn and attacks this turn if able.',
                'Target creature becomes a color of your choice until end of turn.',
                'Target creature becomes blue until your next turn.',
                'Target creature becomes blue until end of turn. Exile target artifact.'):
            self.assertIsNone(typed_resolution_effect_template(text,card_name='Witness'),text)

    def test_minimum_one_has_no_fixed_upper_bound_and_old_zero_selection_is_retained(self):
        spec=AnnouncedTargetCardinality(selection='one_or_more',parameter='any_number',schema_version=3)
        self.assertEqual(spec,AnnouncedTargetCardinality.from_dict(spec.to_dict()))
        schema={'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],'count_parameter':spec.to_dict()}
        self.assertEqual((1,23),(bind_any_number_target_schema(schema,23)['min'],bind_any_number_target_schema(schema,23)['max']))
        old=AnnouncedTargetCardinality(selection='up_to',parameter='any_number',schema_version=2)
        self.assertEqual((0,23),old.bounds(23))
        for bad in ({'schema_version':1,'parameter':'x','selection':'one_or_more'},
                {'schema_version':2,'parameter':'any_number','selection':'one_or_more'},
                {'schema_version':3,'parameter':'x','selection':'one_or_more'}):
            with self.assertRaises(ValueError):AnnouncedTargetCardinality.from_dict(bad)


class ScopedColorNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.RecipientControlNativeTests.reject
    finish=recipients.RecipientControlNativeTests.finish
    finish_turn=recipients.RecipientControlNativeTests.finish_turn

    @classmethod
    def setUpClass(cls):
        names={*NAMES,'Sliver Hivelord','Generic Bound Plains','Grizzly Bears','Counterspell','Fusion Elemental',
            'Dragonfire Blade','Thoughtlace','Unsummon','Island','Forest','Fervor','Ornithopter','Boreal Elemental'}
        paths=(*compact_ci_fixture_paths(root=ROOT),ROOT/'tests/fixtures/bound-effect-program-cards.json')
        docs=[json.loads(p.read_text(encoding='utf-8')) for p in paths]
        cards={r['name']:r for d in docs for r in d['cards'] if r['name'] in names};assert set(cards)==names,names-set(cards)
        ids={r['oracle_id'] for r in cards.values()}
        rulings={tuple(sorted(r.items())):r for d in docs for r in d.get('rulings',[]) if r['oracle_id'] in ids}
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        doc=json.loads((ROOT/'tests/fixtures/recipient-control-cards.json').read_text(encoding='utf-8'));doc.update(cards=list(cards.values()),rulings=list(rulings.values()))
        fixture=folder/'colors.json';fixture.write_text(json.dumps(doc),encoding='utf-8')
        path=folder/'colors.sqlite3';build_fixture_database([fixture],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Scoped fixed colors',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def scene(self,name,before=None,*,zone='hand'):
        s=self.session(2021036+len(name));source=self.add(s.engine,name,zone=zone)
        body=self.add(s.engine,'Grizzly Bears');private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        extra=before(s,source,body) if before else {}
        self.assert_closed(s,source);s.state.config.semantic_policy='trusted_only'
        self.prepare(s,mana={'C':30,**{c:10 for c in 'WUBRG'}});self.checkpoint(s)
        return s,source,body,private,extra

    def colors(self,s,card):return set(s.engine._public_object_query_result(s.state.cards[card.object_id],_maximum_layer=None).colors)

    def activate(self,s,source,target,*,ability_id=None):
        self.priority(s,'A')
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        row=next(r for r in rows if r['id'].startswith('activate:'+source.ref+':') and (ability_id is None or r['id'].endswith(':'+ability_id)))
        command={'action_id':row['id'],'targets':[target],'pay':'auto'};self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_originals_bind_normally_and_color_authority_cannot_be_removed_with_labels(self):
        for name in NAMES:
            with self.subTest(name=name):
                card=self.db.lookup(name,fuzzy=False);ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                program=_compile_best_available_card_program_from_ir(self.db,card,ir,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
                bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']));self.assertEqual('capability_closed',bound['trust_basis'])
        for name in ('Indigo Faerie','Dwarven Song'):
            s=self.session(202103601);source=self.add(s.engine,name,zone='hand')
            programs=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAPABILITY in p.capability_dependencies and p.effects]
            self.assertEqual(1,len(programs));p,=programs
            unlabeled=replace(p,coverage=[])
            self.assertTrue(bind_semantic_program_runtime(unlabeled,capability_registry=self.registry,profile='commander_review')['strict'])
            deps=tuple(c for c in p.capability_dependencies if c!=CAPABILITY)
            changed=replace(unlabeled,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
            malformed=replace(unlabeled,target_schema={'zones':['player'],'categories':['player'],'count':1})
            self.assertFalse(bind_semantic_program_runtime(malformed,capability_registry=self.registry,profile='commander_review')['strict'])
            if name=='Dwarven Song':
                effect={**p.effects[0],'maximum_targets':2};effect.pop('announced_target_count')
                malformed=replace(unlabeled,effects=[effect])
                self.assertFalse(bind_semantic_program_runtime(malformed,capability_registry=self.registry,profile='commander_review')['strict'])

    def test_actual_wisps_preserve_color_and_all_printed_companions_then_expire(self):
        cases=(('Cerulean Wisps','U',2,2,None,False),('Niveous Wisps','W',2,2,None,True),
            ('Viridescent Wisps','G',3,2,None,False),('Crimson Wisps','R',2,2,'haste',False),('Aphotic Wisps','B',2,2,'fear',False))
        for name,color,power,toughness,keyword,tapped in cases:
            with self.subTest(name=name):
                def before(s,source,body):body.tapped=name=='Cerulean Wisps'
                s,spell,body,private,extra=self.scene(name,before)
                hand=len(s.state.players['A'].zones['hand'])
                self.cast(s,'A',spell,[body.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual({color},self.colors(s,body));self.assertEqual(tapped,s.state.cards[body.object_id].tapped)
                self.assertEqual((power,toughness),tuple(s.engine._numeric_stat(body.object_id,k) for k in ('power','toughness')))
                self.assertEqual(hand,len(s.state.players['A'].zones['hand']))
                if keyword:self.assertIn(keyword,s.engine._combat_keywords(s.state.cards[body.object_id]))
                self.finish_turn(s);self.assertEqual({'G'},self.colors(s,body))
                self.assertEqual((2,2),tuple(s.engine._numeric_stat(body.object_id,k) for k in ('power','toughness')))
                self.finish(s,private)

    def test_actual_additive_and_replacing_colors_follow_timestamps_and_change_equip_quotes(self):
        def before(s,source,body):return {'griffin':self.add(s.engine,'Aurora Griffin'),'blade':self.add(s.engine,'Dragonfire Blade')}
        s,faerie,body,private,extra=self.scene('Indigo Faerie',before,zone='battlefield')
        self.activate(s,faerie,body.ref);self.assertIsNone(self.resolve(s));self.assertEqual({'G','U'},self.colors(s,body))
        self.activate(s,extra['griffin'],body.ref);self.assertIsNone(self.resolve(s));self.assertEqual({'W'},self.colors(s,body))
        self.activate(s,faerie,body.ref);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual({'W','U'},self.colors(s,body))
        self.priority(s,'A');rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        row=next(r for r in rows if r['id'].startswith('activate:'+extra['blade'].ref+':'))
        quotes={p['requirements']['GENERIC'] for p in row['target_costs']['prices'] if any(body.ref in refs for refs in p['targets'].values())}
        self.assertEqual({2},quotes)
        self.finish_turn(s);self.assertEqual({'G'},self.colors(s,body));self.finish(s,private)

    def test_actual_spell_colorless_setting_follows_resolution_but_not_a_new_incarnation(self):
        def before(s,source,body):return {'spell':self.add(s.engine,'Grizzly Bears',zone='hand',ref='colorless-bear-spell'),
            'bounce':self.add(s.engine,'Unsummon',zone='hand'),'haste':self.add(s.engine,'Fervor')}
        s,gnomes,body,private,extra=self.scene('Ersatz Gnomes',before,zone='battlefield')
        self.cast(s,'A',extra['spell']);ref=s.state.stack[-1].ref
        self.activate(s,gnomes,ref,ability_id='ab1');self.resolve_top(s)
        self.assertEqual(set(),s.engine._source_colors_for_ref(ref))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(set(),self.colors(s,extra['spell']))
        self.cast(s,'A',extra['bounce'],[extra['spell'].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual({'G'},set(s.engine._effective_card_data(s.state.cards[extra['spell'].object_id])['colors']))
        self.finish(s,private)

    def test_actual_damage_and_shadow_companions_remain_complete(self):
        for name in ('Singe',"Traitor's Clutch"):
            with self.subTest(name=name):
                s,spell,body,private,extra=self.scene(name)
                self.cast(s,'A',spell,[body.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual({'B'},self.colors(s,body))
                card=s.state.cards[body.object_id]
                if name=='Singe':self.assertEqual(1,card.marked_damage)
                else:
                    self.assertEqual(3,s.engine._numeric_stat(body.object_id,'power'))
                    self.assertIn('shadow',s.engine._combat_keywords(card))
                self.finish(s,private)

    def test_actual_all_illegal_color_target_does_not_draw_or_follow_the_new_incarnation(self):
        def before(s,source,body):
            self.add(s.engine,'Island',seat='B')
            return {'bounce':self.add(s.engine,'Unsummon',seat='B',zone='hand')}
        s,spell,body,private,extra=self.scene('Cerulean Wisps',before)
        self.cast(s,'A',spell,[body.ref]);hand=len(s.state.players['A'].zones['hand'])
        self.cast(s,'B',extra['bounce'],[body.ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(hand+1,len(s.state.players['A'].zones['hand']))
        self.assertEqual('hand',s.state.cards[body.object_id].zone)
        self.assertEqual({'G'},set(s.engine._effective_card_data(s.state.cards[body.object_id])['colors']))
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.finish(s,private)

    def test_actual_minimum_one_accepts_seven_distinct_targets_and_skips_a_bounced_one(self):
        def before(s,source,body):
            self.add(s.engine,'Island',seat='B')
            return {'targets':[self.add(s.engine,'Ornithopter',seat='B',ref='color-target-'+str(i)) for i in range(7)],
                'bounce':self.add(s.engine,'Unsummon',seat='B',zone='hand')}
        s,spell,body,private,extra=self.scene('Dwarven Song',before)
        row=self.action(s,'A',spell)
        self.reject(s,'A',{'action_id':row['id'],'targets':[],'pay':'auto'})
        self.reject(s,'A',{'action_id':row['id'],'targets':[extra['targets'][0].ref]*2,'pay':'auto'})
        self.cast(s,'A',spell,[c.ref for c in extra['targets']])
        self.cast(s,'B',extra['bounce'],[extra['targets'][0].ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[extra['targets'][0].object_id].zone)
        self.assertTrue(all(self.colors(s,c)=={'R'} for c in extra['targets'][1:]));self.assertEqual({'G'},self.colors(s,body))
        self.finish(s,private)

    def test_actual_target_tax_preview_preserves_minimum_one_and_pays_the_selected_price(self):
        def before(s,source,body):return {'taxed':self.add(s.engine,'Boreal Elemental',seat='B')}
        s,spell,body,private,extra=self.scene('Dwarven Song',before)
        row=self.action(s,'A',spell)
        self.assertEqual(1,row['target_schema']['groups'][0]['min'])
        self.reject(s,'A',{'action_id':row['id'],'targets':[],'pay':'auto'})
        before_mana=sum(s.state.players['A'].mana_pool.values())
        self.cast(s,'A',spell,[extra['taxed'].ref])
        self.assertEqual(before_mana-3,sum(s.state.players['A'].mana_pool.values()))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual({'R'},self.colors(s,extra['taxed']));self.assertEqual({'G'},self.colors(s,body))
        self.finish(s,private)

    def test_fresh_color_journal_omission_is_killed_by_actual_additive_replacing_sequence(self):
        with patch('quorune.continuous_effect_state.create_resolution_continuous_effect',return_value=None),self.assertRaises(AssertionError):
            type(self)('test_actual_additive_and_replacing_colors_follow_timestamps_and_change_equip_quotes').test_actual_additive_and_replacing_colors_follow_timestamps_and_change_equip_quotes()


if __name__=='__main__':unittest.main()
