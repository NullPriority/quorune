"""CR111.3/205.4 and 702.149: declared token types and intrinsic triggers."""
from copy import deepcopy
from dataclasses import replace
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record
from quorune.carddb import CardDatabase
from quorune.compiler.token_templates import fixed_token_creation_effect_template
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
from quorune.rules.token_prowess_requirements import token_triggered_keyword_definition_dependencies
from scripts.build_test_database import build_fixture_database


class IntrinsicTokenCompilerTests(unittest.TestCase):
    def test_supported_combat_keywords_have_one_copiable_intrinsic_instance(self):
        registry=load_default_capability_registry()
        for keyword,capability in (('training','counter.producer.training'),('mentor','counter.producer.mentor'),
                ('dethrone','counter.producer.dethrone'),('flanking','combat.trigger.flanking'),
                ('exalted','combat.trigger.exalted'),('melee','combat.trigger.melee'),
                ('battle cry','combat.trigger.battle_cry'),('bushido 2','combat.trigger.bushido')):
            with self.subTest(keyword=keyword):
                text=f'Create a 1/1 white Human creature token with {keyword}.'
                ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                effect=ir.faces[0].nodes[0].effects[0]
                self.assertEqual([keyword.title()],effect['characteristics']['keywords'])
                self.assertEqual(1,len(effect['characteristics']['ability_fragments']))
                self.assertIn(capability,ir.faces[0].nodes[0].capability_dependencies)
                self.assertIn(capability,token_triggered_keyword_definition_dependencies(effect['characteristics']))

    def test_missing_duplicate_mismatched_and_open_keyword_definitions_reject(self):
        template=fixed_token_creation_effect_template('Create a 1/1 white Human creature token with training.')
        effect=template.effect
        for fragments in ([],effect['characteristics']['ability_fragments']*2,
                [{'kind':'combat_keyword_trigger','value':{'schema_version':1,'kind':'mentor','amount':1}}]):
            changed=deepcopy(effect);changed['characteristics']['ability_fragments']=fragments
            with self.subTest(fragments=fragments):
                with self.assertRaises(ValueError):token_triggered_keyword_definition_dependencies(changed['characteristics'])
                self.assertEqual((),fixed_token_creation_node_capabilities(effects=(changed,),target_schema=None,mechanic_ids=template.mechanics))
        for suffix in ('training 2.','training and rewrite history.','bushido 0.','an unrepresented ability.'):
            self.assertIsNone(fixed_token_creation_effect_template('Create a 1/1 white Human creature token with '+suffix))

    def test_named_legendary_tokens_preserve_independent_card_types(self):
        for types,expected in (('artifact creature','Legendary Artifact Creature'),
                ('enchantment creature','Legendary Creature Enchantment'),
                ('artifact enchantment creature','Legendary Artifact Creature Enchantment')):
            text=f'Create Herald of the Dawn, a legendary 5/5 colorless Golem {types} token.'
            template=fixed_token_creation_effect_template(text)
            self.assertIsNotNone(template)
            self.assertEqual('Herald of the Dawn',template.effect['name'])
            self.assertEqual(f'Token {expected} — Golem',template.effect['characteristics']['type_line'])
            self.assertTrue(fixed_token_creation_node_capabilities(effects=(template.effect,),target_schema=None,mechanic_ids=template.mechanics))
        plain=fixed_token_creation_effect_template('Create a 5/5 colorless Golem artifact creature token named Ordinary Golem.')
        self.assertNotIn('Legendary',plain.effect['characteristics']['type_line'])
        registry=load_default_capability_registry()
        self.assertNotEqual('exact',compile_oracle_card(replace(query_record(
            'Create a 1/1 green Wolf creature token named Wolf and draw a card.'),type_line='Sorcery'),capability_registry=registry).status)


class IntrinsicTokenNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'intrinsic-tokens.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/intrinsic-token-composition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Intrinsic tokens',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    def training_witness(self,seed,*,with_buddy):
        session=self.session(seed);source=self.add(session.engine,'Torens, Fist of the Angels')
        self.add(session.engine,'Fervor');creature=self.add(session.engine,'Centaur Courser',zone='hand')
        self.assert_closed(session,source);self.assert_closed(session,creature)
        self.prepare(session,mana={'C':2,'G':1});self.checkpoint(session)
        self.cast(session,'A',creature);self.assertIsNone(self.resolve(session))
        tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.assertEqual(1,len(tokens));token=tokens[0]
        self.assertEqual(('A','A'),(token.owner,token.controller))
        self.assertEqual('Human Soldier Token',token.printed_name)
        self.attack(session,[(token,'B'),(creature,'C')] if with_buddy else [(token,'B')])
        session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(int(with_buddy),session.state.cards[token.object_id].counters.get('+1/+1',0),
            'printed training must depend on a greater-power attacking companion')
        self.replay(session,load=True)

    def test_actual_torens_token_trains_with_greater_power_and_not_when_alone(self):
        for buddy in (False,True):
            with self.subTest(buddy=buddy):self.training_witness(989100+int(buddy),with_buddy=buddy)

    def tuktuk_witness(self,seed):
        session=self.session(seed);source=self.add(session.engine,'Tuktuk the Explorer');source.owner='B'
        second=self.add(session.engine,'Tuktuk the Explorer',zone='hand',ref='tuktuk-second')
        first_kill=self.add(session.engine,'Infernal Grasp',zone='hand',ref='tuktuk-kill-first')
        second_kill=self.add(session.engine,'Infernal Grasp',zone='hand',ref='tuktuk-kill-second')
        self.assert_closed(session,source);self.prepare(session,mana={'C':10,'B':2,'R':1});self.checkpoint(session)
        self.cast(session,'A',first_kill,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.assertEqual(1,len(tokens));token=tokens[0]
        data=session.engine._effective_card_data(token)
        self.assertIn('Legendary Artifact Creature',data['type_line'],'printed token must be a legendary artifact creature')
        self.assertEqual(('A','A'),(token.owner,token.controller))
        self.assertEqual('Tuktuk the Returned',data['name'])
        self.assertEqual(('5','5'),(str(data['power']),str(data['toughness'])))
        self.cast(session,'A',second);self.assertIsNone(self.resolve(session))
        self.cast(session,'A',second_kill,[second.ref])
        self.assertEqual('state.legend',self.resolve(session).kind)
        session=self.replay(session,load=True)
        keep=next(c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield' and c.object_id!=token.object_id)
        command={'action_id':'choose','card':keep.ref};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual([keep.object_id],[c.object_id for c in session.state.cards.values() if c.is_token and c.zone=='battlefield'])
        self.replay(session,load=True)

    def test_actual_borrowed_tuktuk_creates_for_controller_and_uses_legend_choice(self):
        self.tuktuk_witness(989200)

    def test_actual_rosnakht_heroic_preserves_the_printed_token_name(self):
        session=self.session(989250);source=self.add(session.engine,'Rosnakht, Heir of Rohgahh')
        spell=self.add(session.engine,'Giant Growth',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'G':1});self.checkpoint(session)
        self.cast(session,'A',spell,[source.ref]);self.assertIsNone(self.resolve(session))
        tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(1,len(tokens));data=session.engine._effective_card_data(tokens[0])
        self.assertEqual('Kobolds of Kher Keep',data['name'])
        self.assertEqual(('0','1'),(str(data['power']),str(data['toughness'])))
        self.assertEqual(['R'],data['colors'])
        self.replay(session,load=True)

    def test_actual_kher_keep_activation_pays_taps_and_preserves_the_token_name(self):
        session=self.session(989400);source=self.add(session.engine,'Kher Keep')
        self.assert_closed(session,source);self.prepare(session,mana={'C':1,'R':1});self.checkpoint(session)
        ability=next(a for a in session.engine._activated_abilities(source) if not a.mana_ability)
        action_id=f'activate:{source.ref}:{ability.ability_id}'
        offered=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertIn(action_id,{a['id'] for a in offered})
        command={'action_id':action_id,'pay':'auto'};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(0,session.state.players['A'].mana_pool.get('C',0))
        self.assertEqual(0,session.state.players['A'].mana_pool.get('R',0))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(1,len(tokens));data=session.engine._effective_card_data(tokens[0])
        self.assertEqual(('A','A'),(tokens[0].owner,tokens[0].controller))
        self.assertEqual('Kobolds of Kher Keep',tokens[0].printed_name)
        self.assertEqual(('0','1'),(str(data['power']),str(data['toughness'])))
        self.assertNotIn('Legendary',data['type_line']);self.replay(session,load=True)

    def test_actual_rohgahh_creates_before_dragon_resolution_and_buffs_its_kobold(self):
        session=self.session(989500);source=self.add(session.engine,'Rohgahh, Kher Keep Overlord');source.owner='B'
        dragon=self.add(session.engine,'Shivan Dragon',zone='hand')
        centaur=self.add(session.engine,'Centaur Courser',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':6,'R':2,'G':1});self.checkpoint(session)
        self.cast(session,'A',dragon)
        session=self.replay(session,load=True);self.resolve_top(session)
        self.assertEqual('stack',session.state.cards[dragon.object_id].zone)
        tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(1,len(tokens));token=tokens[0]
        self.assertEqual(('A','A'),(token.owner,token.controller))
        self.assertEqual('Kobolds of Kher Keep',token.printed_name)
        self.assertEqual((2,3),tuple(session.engine._numeric_stat(token.object_id,stat) for stat in ('power','toughness')))
        self.assertIsNone(self.resolve(session));self.cast(session,'A',centaur)
        self.assertEqual(0,self.trigger_count(session,source));self.assertIsNone(self.resolve(session))
        self.assertEqual([token.object_id],[c.object_id for c in session.state.cards.values() if c.is_token and c.zone=='battlefield'])
        self.replay(session,load=True)

    def test_fresh_omitted_legendary_mutant_is_killed(self):
        import quorune.engine as owner
        original=owner.create_tokens;observed=[]
        def stripped(*args,**kwargs):
            if kwargs.get('name')=='Tuktuk the Returned':
                observed.append(True);kwargs['characteristics']={**kwargs['characteristics'],
                    'type_line':kwargs['characteristics']['type_line'].replace('Legendary ','')}
            return original(*args,**kwargs)
        with patch.object(owner,'create_tokens',stripped):
            with self.assertRaisesRegex(AssertionError,'printed token must be a legendary artifact creature'):
                self.tuktuk_witness(989300)
        self.assertTrue(observed)
