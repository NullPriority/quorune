"""CR115.3/120.4/608.2b: printed shares resolve as one damage instruction."""
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
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.fixed_damage_role_model import FixedDamageRoleSpec,fixed_damage_role_spec,FIXED_DAMAGE_ROLE_CAPABILITY as CAPABILITY
from quorune.compiler.fixed_damage_roles import fixed_damage_role_template
from quorune.rules.fixed_damage_roles import fixed_damage_role_node_capabilities
from scripts.build_test_database import build_fixture_database,compact_ci_fixture_paths

NAMES=('Arc Trail','Boulder Dash','Lunge','Hungry Flames','Punish the Enemy','Shower of Sparks','Cone of Flame',
    'Cunning Strike','Explosive Welcome','Trick Shot','Conduct Electricity','Drakuseth, Maw of Flames')


class FixedDamageRoleCompilerTests(unittest.TestCase):
    def compile(self,text):return fixed_damage_role_template(text,card_name='Witness',source_is_permanent=False)

    def test_roles_preserve_amounts_cardinality_distinctness_and_canonical_scalar_legality(self):
        r=self.compile('Witness deals 1 damage to any target, 2 damage to another target, and 3 damage to a third target.')
        effect,=r[1];spec=fixed_damage_role_spec(effect)
        self.assertEqual((1,2,3),spec.amounts);self.assertEqual(6,spec.total)
        self.assertEqual(['damage_role_0','damage_role_1'],r[2]['groups'][2]['different_from_groups'])
        self.assertIn(CAPABILITY,fixed_damage_role_node_capabilities(effects=r[1],target_schema=r[2],mechanic_ids=r[3]))
        optional=self.compile('Witness deals 4 damage to any target and 3 damage to each of up to two other targets.')
        spec=fixed_damage_role_spec(optional[1][0]);self.assertEqual((10,(1,0),(1,2)),(spec.total,spec.minimums,spec.maximums))
        self.assertEqual(spec,FixedDamageRoleSpec.from_dict(spec.to_dict()))
        for altered in ({'schema_version':True},{'amounts':[1,False]},{'amounts':[1]},
                {'maximums':[1,3]},{'minimums':[0,0]},{'extra':1}):
            with self.assertRaises((ValueError,TypeError)):FixedDamageRoleSpec.from_dict({**spec.to_dict(),**altered})
        for altered in ({'amount':True},{'amount':11},{'source':'$target.0'},{'extra':1}):
            with self.assertRaises(ValueError):fixed_damage_role_spec({**optional[1][0],**altered})
        bad={'groups':[dict(g) for g in optional[2]['groups']]};bad['groups'][1]['max']=3
        self.assertFalse(fixed_damage_role_node_capabilities(effects=optional[1],target_schema=bad,mechanic_ids=optional[3]))

    def test_unrepresented_amounts_choosers_recipients_ordinals_and_tails_stay_residual(self):
        for text in ('Witness deals X damage to any target and 1 damage to any other target.',
                "Witness deals 1 damage to any target and 1 damage to any target of an opponent's choice.",
                'Witness deals 1 damage to any target and 3 damage to itself.',
                'Witness deals 1 damage to any target and 2 damage to a third target.',
                'Witness deals 1 damage to any target and 2 damage to each of up to three other targets.',
                'Witness deals 1 damage to any target and 2 damage to any other target. Draw a card.'):
            self.assertIsNone(self.compile(text),text)


class FixedDamageRoleNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.RecipientControlNativeTests.reject
    finish=recipients.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        names={*NAMES,'Sliver Hivelord','Generic Bound Plains','Counterspell','Darksteel Myr','Grizzly Bears',
            'Unsummon','Island','Fervor','Zulaport Cutthroat','Llanowar Elves','Cackling Counterpart',
            'Furnace of Rath','Dictate of the Twin Gods','Reverberate'}
        docs=[json.loads(p.read_text(encoding='utf-8')) for p in (*compact_ci_fixture_paths(root=ROOT),ROOT/'tests/fixtures/bound-effect-program-cards.json')]
        cards={r['name']:r for d in docs for r in d['cards'] if r['name'] in names};assert set(cards)==names,names-set(cards)
        ids={r['oracle_id'] for r in cards.values()};rulings={tuple(sorted(r.items())):r for d in docs for r in d.get('rulings',[]) if r['oracle_id'] in ids}
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        doc=json.loads((ROOT/'tests/fixtures/recipient-control-cards.json').read_text(encoding='utf-8'));doc.update(cards=list(cards.values()),rulings=list(rulings.values()))
        fixture=folder/'roles.json';fixture.write_text(json.dumps(doc),encoding='utf-8');path=folder/'roles.sqlite3';build_fixture_database([fixture],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Fixed damage roles',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def scene(self,name,before=None,*,zone='hand'):
        s=self.session(2021037+len(name));source=self.add(s.engine,name,zone=zone)
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand');self.add(s.engine,'Fervor')
        extra=before(s,source) if before else {}
        self.assert_closed(s,source);s.state.config.semantic_policy='trusted_only'
        self.prepare(s,mana={'C':40,**{c:10 for c in 'WUBRG'}});self.checkpoint(s)
        return s,source,private,extra

    def cast_roles(self,s,source,refs):
        row=self.action(s,'A',source);command={'action_id':row['id'],'targets':refs,'pay':'auto'}
        self.reject(s,'B',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_originals_bind_normally_and_payload_authority_survives_removed_labels(self):
        for name in NAMES:
            with self.subTest(name=name):
                c=self.db.lookup(name,fuzzy=False);ir=compile_oracle_card(c,capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                p=_compile_best_available_card_program_from_ir(self.db,c,ir,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
                b=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(b['strict_capability_ready'],(name,b['blockers']));self.assertEqual('capability_closed',b['trust_basis'])
        s,source,private,extra=self.scene('Arc Trail')
        p,=[p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.effects]
        p=replace(p,coverage=[]);self.assertTrue(bind_semantic_program_runtime(p,capability_registry=self.registry,profile='commander_review')['strict'])
        deps=tuple(c for c in p.capability_dependencies if c!=CAPABILITY)
        p=replace(p,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        b=bind_semantic_program_runtime(p,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(b['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,b['blockers'])

    def test_actual_cone_requires_three_distinct_targets_and_keeps_each_printed_amount(self):
        def before(s,source):return {'one':self.add(s.engine,'Darksteel Myr',seat='B'),
            'two':self.add(s.engine,'Darksteel Myr',seat='C',ref='second-durable-myr')}
        s,source,private,extra=self.scene('Cone of Flame',before);row=self.action(s,'A',source)
        for targets in ([extra['one'].ref,'D'],[extra['one'].ref,extra['one'].ref,'D']):
            self.reject(s,'A',{'action_id':row['id'],'targets':targets,'pay':'auto'})
        self.cast_roles(s,source,[extra['one'].ref,extra['two'].ref,'D']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual([1,2],[s.state.cards[extra[k].object_id].marked_damage for k in ('one','two')]);self.assertEqual(37,s.state.players['D'].life)
        self.finish(s,private)

    def test_actual_partial_role_loss_keeps_the_surviving_share_and_paid_cast(self):
        def before(s,source):
            self.add(s.engine,'Island',seat='B')
            return {'body':self.add(s.engine,'Grizzly Bears',seat='B'),'bounce':self.add(s.engine,'Unsummon',seat='B',zone='hand')}
        s,source,private,extra=self.scene('Hungry Flames',before)
        self.cast_roles(s,source,[extra['body'].ref,'D']);paid=dict(s.state.players['A'].mana_pool)
        self.cast(s,'B',extra['bounce'],[extra['body'].ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(38,s.state.players['D'].life)
        self.assertEqual('hand',s.state.cards[extra['body'].object_id].zone);self.assertEqual(paid,s.state.players['A'].mana_pool);self.finish(s,private)

    def test_actual_optional_roles_allow_zero_and_combine_only_unqualified_reuse(self):
        for name,overlap in (('Conduct Electricity',True),('Trick Shot',False)):
            with self.subTest(name=name):
                def before(s,source):
                    return {'body':self.add(s.engine,'Darksteel Myr'),'copy':self.add(s.engine,'Cackling Counterpart',zone='hand')}
                s,source,private,extra=self.scene(name,before)
                self.assert_closed(s,extra['copy']);self.cast(s,'A',extra['copy'],[extra['body'].ref]);self.assertIsNone(self.resolve(s))
                token,=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
                row=self.action(s,'A',source)
                targets={'damage_role_0':[token.ref],'damage_role_1':[token.ref]}
                if not overlap:
                    self.reject(s,'A',{'action_id':row['id'],'targets':targets,'pay':'auto'});targets['damage_role_1']=[]
                self.cast_roles(s,source,targets);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(8 if overlap else 6,s.state.cards[token.object_id].marked_damage);self.finish(s,private)

    def test_actual_companion_draw_and_mana_follow_the_complete_damage_instruction(self):
        for name,amount in (('Cunning Strike',2),('Explosive Welcome',5)):
            with self.subTest(name=name):
                def before(s,source):return {'body':self.add(s.engine,'Darksteel Myr',seat='B')}
                s,source,private,extra=self.scene(name,before);hand=len(s.state.players['A'].zones['hand']);mana=sum(s.state.players['A'].mana_pool.values())
                self.cast_roles(s,source,[extra['body'].ref,'D']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(amount,s.state.cards[extra['body'].object_id].marked_damage);self.assertEqual(38 if name=='Cunning Strike' else 37,s.state.players['D'].life)
                self.assertEqual(hand if name=='Cunning Strike' else hand-1,len(s.state.players['A'].zones['hand']))
                if name=='Explosive Welcome':self.assertEqual(mana-5,sum(s.state.players['A'].mana_pool.values()))
                self.finish(s,private)

    def test_actual_attack_trigger_announces_optional_roles_then_uses_departed_source_facts(self):
        def before(s,source):
            source.acquired_control_turn_count=-1;self.add(s.engine,'Island',seat='B')
            return {'body':self.add(s.engine,'Darksteel Myr',seat='B'),'bounce':self.add(s.engine,'Unsummon',seat='B',zone='hand')}
        s,source,private,extra=self.scene('Drakuseth, Maw of Flames',before,zone='battlefield')
        self.attack(s,[(source,'B')]);self.assertEqual('semantic.target',s.state.pending_decision.kind)
        targets={'damage_role_0':[extra['body'].ref],'damage_role_1':['C','D']}
        command={'action_id':'choose','targets':targets};self.reject(s,'C',command)
        self.reject(s,'A',{'action_id':'choose','targets':{**targets,'damage_role_1':['A','C','D']}})
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.cast(s,'B',extra['bounce'],[source.ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[source.object_id].zone);self.assertEqual(4,s.state.cards[extra['body'].object_id].marked_damage)
        self.assertEqual((37,37),tuple(s.state.players[p].life for p in ('C','D')));self.finish(s,private)

    def test_actual_all_illegal_roles_fizzle_without_producing_the_printed_mana(self):
        def before(s,source):
            self.add(s.engine,'Island',seat='B');self.add(s.engine,'Island',seat='B',ref='second-bounce-island')
            return {'one':self.add(s.engine,'Grizzly Bears',seat='B'),'two':self.add(s.engine,'Llanowar Elves',seat='B'),
                'bounce_one':self.add(s.engine,'Unsummon',seat='B',zone='hand'),
                'bounce_two':self.add(s.engine,'Unsummon',seat='B',zone='hand',ref='second-bounce-spell')}
        s,source,private,extra=self.scene('Explosive Welcome',before)
        self.cast_roles(s,source,[extra[k].ref for k in ('one','two')]);paid=dict(s.state.players['A'].mana_pool)
        for key in ('one','two'):
            self.cast(s,'B',extra['bounce_'+key],[extra[key].ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(paid,s.state.players['A'].mana_pool);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertTrue(all(s.state.cards[extra[k].object_id].zone=='hand' for k in ('one','two')));self.finish(s,private)

    def test_actual_copy_changes_targets_without_changing_or_reallocating_printed_shares(self):
        def before(s,source):
            s.state.players['B'].mana_pool['R']=2
            return {'copy':self.add(s.engine,'Reverberate',seat='B',zone='hand')}
        s,source,private,extra=self.scene('Arc Trail',before)
        self.cast_roles(s,source,['B','D']);self.cast(s,'B',extra['copy'],[s.state.stack[-1].ref])
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.reject(s,'B',{'action_id':'choose','targets':['A','A']})
        s=self.replay(s,load=True);command={'action_id':'choose','targets':['A','C']};self.reject(s,'C',command)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual((38,38,39,39),tuple(s.state.players[p].life for p in 'ABCD'));self.finish(s,private)

    def test_actual_simultaneous_damage_keeps_a_dying_observer_for_both_creatures(self):
        def before(s,source):return {'observer':self.add(s.engine,'Zulaport Cutthroat'),'victim':self.add(s.engine,'Llanowar Elves')}
        s,source,private,extra=self.scene('Arc Trail',before)
        self.cast_roles(s,source,[extra['observer'].ref,extra['victim'].ref]);s=self.replay(s,load=True)
        for _ in range(12):
            d=self.resolve(s)
            if d is None:break
            self.assertEqual('trigger.order',d.kind)
            refs=[item.ref for batch in s.state.pending_trigger_batches for item in batch.items if item.controller=='A']
            result=s.act('pilot:A',{'action_id':'order','triggers':refs});self.assertTrue(result.ok,result.summary)
        else:self.fail('Damage death triggers did not settle')
        self.assertEqual((42,38,38,38),tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='graveyard' for c in extra.values()));self.finish(s,private)

    def test_actual_fixed_shares_resume_one_atomic_replacement_batch_after_pending_reload(self):
        def before(s,source):
            return {'furnace':self.add(s.engine,'Furnace of Rath'),'dictate':self.add(s.engine,'Dictate of the Twin Gods')}
        s,source,private,extra=self.scene('Boulder Dash',before)
        for card in extra.values():self.assert_closed(s,card)
        self.cast_roles(s,source,['B','D']);self.assertEqual('replacement.order',self.resolve(s).kind)
        self.assertEqual((40,40),tuple(s.state.players[p].life for p in ('B','D')));s=self.replay(s,load=True)
        for _ in range(12):
            d=self.resolve(s)
            if d is None:break
            self.assertEqual('replacement.order',d.kind)
            self.assertEqual((40,40),tuple(s.state.players[p].life for p in ('B','D')))
            principal=s.pending_principals()[0];packet=s.packet(principal,full=True)
            option=packet['decision']['ctx']['options'][0]['id'];command={'action_id':'choose','choices':{'replacement':option}}
            self.reject(s,'C',command);result=s.act(principal,command);self.assertTrue(result.ok,result.summary)
        else:self.fail('Fixed damage replacement batch did not settle')
        self.assertEqual((32,36),tuple(s.state.players[p].life for p in ('B','D')));self.finish(s,private)

    def test_fresh_fixed_share_omission_is_killed_by_the_native_simultaneous_witness(self):
        from quorune.rules.fixed_damage_roles import bind_fixed_damage_roles
        def omit(host,effect,item):
            result=bind_fixed_damage_roles(host,effect,item);result['announced_allocation']=result['announced_allocation'][:1];return result
        with patch('quorune.rules.fixed_damage_roles.bind_fixed_damage_roles',side_effect=omit),self.assertRaises(AssertionError):
            type(self)('test_actual_simultaneous_damage_keeps_a_dying_observer_for_both_creatures').test_actual_simultaneous_damage_keeps_a_dying_observer_for_both_creatures()


if __name__=='__main__':unittest.main()
