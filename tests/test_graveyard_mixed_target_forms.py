"""Independent OR boundaries for type, subtype and supertype graveyard cards."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from quorune.compiler.return_to_hand_templates import targeted_own_graveyard_return_to_hand_effect_template
from quorune.graveyard_target_unions import mixed_graveyard_target,mixed_graveyard_target_from_schema
from quorune.rules.graveyard_card_targets import OwnGraveyardCardTargetSpec
from quorune.targets import target_plan


class GraveyardMixedFormModelTests(unittest.TestCase):
    def test_verified_mixed_owner_withdrawal_keeps_ordinary_return_and_draw(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']=='target.card.own_graveyard_mixed_forms').update(status='blocked',blockers=['mixed target owner withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,expected in (('Return target Spirit, instant, or sorcery card from your graveyard to your hand.',False),
                ('Return target instant or sorcery card from your graveyard to your hand.',True),('Draw a card.',True)):
            self.assertEqual(expected,compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry).status=='exact')
    def test_qualities_are_alternatives_and_preserve_the_common_graveyard_header(self):
        for phrase,positives,negative in (
                ('Spirit, instant, or sorcery',(({'creature'},{'spirit'},set()),({'instant'},set(),set()),({'sorcery'},set(),set())),({'artifact'},set(),set())),
                ('artifact, enchantment, or legendary',(({'artifact'},set(),set()),({'enchantment'},set(),set()),({'creature'},set(),{'legendary'})),({'creature'},{'spirit'},set()))):
            spec=mixed_graveyard_target(phrase);self.assertIsNotNone(spec)
            schema=spec.to_target_schema();group=target_plan(schema).groups[0]
            self.assertEqual((['graveyard'],['card'],'you',1),(schema['zones'],schema['categories'],schema['owner_relation'],schema['count']))
            for types,subtypes,supertypes in positives:
                self.assertTrue(group.matches_type_characteristics(types=types,subtypes=subtypes,supertypes=supertypes))
            self.assertFalse(group.matches_type_characteristics(types=negative[0],subtypes=negative[1],supertypes=negative[2]))
            self.assertEqual(schema,OwnGraveyardCardTargetSpec.from_target_schema(schema).to_target_schema())

    def test_mana_value_bound_wraps_the_whole_disjunction(self):
        template=targeted_own_graveyard_return_to_hand_effect_template(
            'Return target creature or Vehicle card with mana value 2 or less from your graveyard to your hand.')
        self.assertIsNotNone(template);schema=template.target_schema;self.assertEqual(2,schema['mana_value_max'])
        group=target_plan(schema).groups[0]
        self.assertTrue(group.matches_type_characteristics(types={'artifact'},subtypes={'vehicle'},supertypes=set()))
        self.assertTrue(group.matches_type_characteristics(types={'creature'},subtypes=set(),supertypes=set()))
        self.assertFalse(group.matches_type_characteristics(types={'artifact'},subtypes={'equipment'},supertypes=set()))
        self.assertEqual(schema,mixed_graveyard_target_from_schema(schema).to_target_schema())

    def test_malformed_duplicate_nested_and_unowned_qualifiers_reject(self):
        spec=mixed_graveyard_target('creature or Vehicle');schema=spec.to_target_schema()
        for changed in ({'owner_relation':'opponent'},{'zones':['battlefield']},{'count':True},
                {'characteristic_forms_any':[]},{'characteristic_forms_any':schema['characteristic_forms_any']*2},
                {'mana_value_max':True},{'unknown':True}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):mixed_graveyard_target_from_schema({**schema,**changed})
        for phrase in ('Spirit or unknownkind','Spirit or Spirit','creature or tapped Vehicle','Spirit or instant or sorcery or artifact or land'):
            self.assertIsNone(mixed_graveyard_target(phrase))
        for field,value in (('types_any',('creature',)),('colors_any',('W',))):
            with self.assertRaises(ValueError):replace(spec,**{field:value})

    def test_homogeneous_old_payload_retains_its_existing_owner(self):
        text='Return target instant or sorcery card from your graveyard to your hand.'
        template=targeted_own_graveyard_return_to_hand_effect_template(text)
        self.assertEqual({'zones':['graveyard'],'categories':['card'],'owner_relation':'you','count':1,
            'types_any':['instant','sorcery']},template.target_schema)
        self.assertIsNone(mixed_graveyard_target('instant or sorcery'))


class GraveyardMixedFormNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'graveyard-mixed.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/graveyard-mixed-target-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Mixed graveyard',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    def choose_target(self,session,selected,excluded=()):
        self.assertEqual('semantic.target',session.state.pending_decision.kind)
        for card in excluded:
            before=authoritative_state_hash(session.state)
            result=session.act('pilot:A',{'action_id':'choose','targets':[card.ref]})
            self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        return self.target(session,'A',[selected.ref])

    def pastcaller_witness(self,seed,index):
        session=self.session(seed);source=self.add(session.engine,'Returned Pastcaller',zone='hand')
        spirit=self.add(session.engine,'Spectral Sailor',zone='graveyard')
        instant=self.add(session.engine,'Counterspell',zone='graveyard')
        sorcery=self.add(session.engine,'Divination',zone='graveyard')
        other=self.add(session.engine,'Sol Ring',zone='graveyard')
        foreign=self.add(session.engine,'Spectral Sailor',seat='B',zone='graveyard',ref='foreign-spirit')
        private=self.add(session.engine,'Counterspell',seat='B',zone='hand',ref='graveyard-private')
        chosen=(spirit,instant,sorcery)[index];identity=chosen.logical_object_id
        self.assert_closed(session,source);self.prepare(session,mana={'C':20,'R':5,'W':5});self.checkpoint(session)
        offered=self.action(session,'A',source)
        result=session.act('pilot:A',{'action_id':offered['id'],'cost_option':offered['cost_options'][0]['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertEqual('semantic.target',self.resolve(session).kind)
        session=self.replay(session,load=True);self.choose_target(session,chosen,(other,foreign))
        for seat in 'ACD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[chosen.object_id].zone,'each printed alternative must return its selected card')
        self.assertNotEqual(identity,session.state.cards[chosen.object_id].logical_object_id)
        for card in (spirit,instant,sorcery):
            if card.object_id!=chosen.object_id:self.assertEqual('graveyard',session.state.cards[card.object_id].zone)
        self.replay(session,load=True)

    def test_actual_pastcaller_returns_each_alternative_and_rejects_wrong_owner_or_quality(self):
        for index in range(3):
            with self.subTest(index=index):self.pastcaller_witness(992100+index,index)

    def test_actual_adventure_can_return_a_legendary_creature_without_artifact_or_enchantment(self):
        session=self.session(992200);source=self.add(session.engine,'Crystal Dragon // Rob the Hoard',zone='hand')
        chosen=self.add(session.engine,'Isamaru, Hound of Konda',zone='graveyard')
        other=self.add(session.engine,'Grizzly Bears',zone='graveyard')
        self.assert_closed(session,source);self.prepare(session,mana={'C':20,'W':5});self.checkpoint(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(a for a in actions if a.get('card')==source.ref and a.get('face')=='Rob the Hoard')
        before=authoritative_state_hash(session.state)
        bad={'action_id':offered['id'],'face':'Rob the Hoard','targets':[other.ref],'pay':'auto'}
        self.assertFalse(session.act('pilot:A',bad).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{**bad,'targets':[chosen.ref]});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[chosen.object_id].zone)
        self.assertEqual('exile',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def test_actual_crewed_recovery_unit_applies_mana_bound_to_creature_or_vehicle(self):
        session=self.session(992300);source=self.add(session.engine,'Imperial Recovery Unit')
        pilot=self.add(session.engine,'Grizzly Bears');self.add(session.engine,'Fervor')
        chosen=self.add(session.engine,'Consulate Dreadnought',zone='graveyard')
        expensive=self.add(session.engine,"Renegade Freighter",zone='graveyard')
        unrelated=self.add(session.engine,'Sol Ring',zone='graveyard')
        self.assert_closed(session,source);self.prepare(session);self.checkpoint(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(a for a in actions if a.get('source')==source.ref and any(
            cost.get('k')=='crew' for cost in a.get('cost_summary',{}).get('choose_cost',())))
        result=session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[pilot.ref]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.attack(session,[(source,'B')])
        self.assertEqual('semantic.target',session.state.pending_decision.kind)
        session=self.replay(session,load=True);self.choose_target(session,chosen,(expensive,unrelated))
        self.assertIsNone(self.resolve(session));self.assertEqual('hand',session.state.cards[chosen.object_id].zone)
        self.replay(session,load=True)

    def test_actual_raptor_can_select_food_or_select_zero(self):
        for index,choose in enumerate((True,False)):
            with self.subTest(choose=choose):
                session=self.session(992400+index);source=self.add(session.engine,'Ragamuffin Raptor',zone='hand')
                food=self.add(session.engine,'Candy Trail',zone='graveyard');other=self.add(session.engine,'Sol Ring',zone='graveyard')
                self.assert_closed(session,source);self.prepare(session,mana={'C':20,'G':5});self.checkpoint(session)
                self.cast(session,'A',source);self.assertEqual('semantic.target',self.resolve(session).kind)
                session=self.replay(session,load=True)
                if choose:self.choose_target(session,food,(other,))
                else:
                    result=session.act('pilot:A',{'action_id':'choose','targets':[]});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(session));self.assertEqual('hand' if choose else 'graveyard',session.state.cards[food.object_id].zone)
                self.replay(session,load=True)

    def test_fresh_omitted_subtype_alternative_is_killed(self):
        from quorune.target_forms import TargetCharacteristicForm
        original=TargetCharacteristicForm.matches;observed=[]
        def omitted(form,**kwargs):
            if form.subtypes_any==('spirit',):observed.append(True);return False
            return original(form,**kwargs)
        with patch.object(TargetCharacteristicForm,'matches',omitted):
            with self.assertRaises((AssertionError,StopIteration)):self.pastcaller_witness(992500,0)
        self.assertTrue(observed)
