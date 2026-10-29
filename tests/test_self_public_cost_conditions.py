"""Intrinsic discounts reuse shared conditions at prospective spell-cost time."""
from copy import deepcopy
from dataclasses import dataclass,replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_source_attack_alone as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import CardInstance
from quorune.record import authoritative_state_hash
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.self_cast_reductions import CastReductionMetric,CastReductionMetricKind,SelfSpellCostReductionSpec,cast_reduction_multiplier,evaluated_self_reduction
from quorune.compiler.cast_cost_modifier_templates import self_spell_cost_reduction_handler
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.rules.casting.public_conditions import prospective_cost_condition_view
from unittest.mock import patch

CAPABILITY='casting.cost.self_public_state_condition'


@dataclass
class PublicPlayer:
    zones:dict
    poison:int=0
    life:int=40
    in_game:bool=True


def cost_host():
    source=CardInstance('source','source','source-oracle','Source','B','B','graveyard')
    state=SimpleNamespace(cards={'source':source},turn_order=list('ABCD'),players={s:PublicPlayer({'graveyard':['source'] if s=='B' else [],'hand':[],'stack':[],'battlefield':[]}) for s in 'ABCD'})
    return SimpleNamespace(state=state),source


class SelfPublicCostCompilerTests(unittest.TestCase):
    def test_shared_metric_codec_is_closed_and_old_metric_payload_is_unchanged(self):
        old=self_spell_cost_reduction_handler('This spell costs {1} less to cast if you control an artifact.')[1]['reduction']['terms'][0]['metric']
        self.assertNotIn('public_state',old);self.assertEqual(old,CastReductionMetric.from_dict(old).to_dict())
        condition=fixed_public_state_condition('an opponent has three or more poison counters',source_name='Source')
        spec=CastReductionMetric(CastReductionMetricKind.PUBLIC_STATE,public_state=condition,schema_version=5);self.assertEqual(spec,CastReductionMetric.from_dict(spec.to_dict()))
        for change in ({'schema_version':1},{'schema_version':True},{'minimum':1},{'require_all':True},{'extra':True}):
            with self.assertRaises(ValueError):CastReductionMetric.from_dict({**spec.to_dict(),**change})

    def test_prospective_view_uses_casting_controller_and_removes_prior_zone_without_mutation(self):
        host,source=cost_host();before=deepcopy(host.state.players['B'].zones)
        for candidate in (source,replace(source,zone='stack',controller='A')):
            view,spell=prospective_cost_condition_view(host,'A',candidate)
            self.assertEqual(('B','A','stack'),(spell.owner,spell.controller,spell.zone));self.assertNotIn(source.object_id,view.state.players['B'].zones['graveyard'])
            self.assertEqual([source.object_id],view.state.players['B'].zones['stack']);self.assertEqual(before,host.state.players['B'].zones)
            self.assertEqual(('B','graveyard'),(source.controller,source.zone))
        with self.assertRaises(ValueError):prospective_cost_condition_view(host,'A',replace(source,object_id='missing'))

    def test_shared_condition_uses_one_opponent_threshold_and_never_own_or_sum(self):
        host,source=cost_host();condition=fixed_public_state_condition('an opponent has three or more poison counters',source_name='Source')
        metric=CastReductionMetric(CastReductionMetricKind.PUBLIC_STATE,public_state=condition,schema_version=5)
        host.state.players['A'].poison=8;host.state.players['B'].poison=1;host.state.players['C'].poison=2
        self.assertEqual(0,cast_reduction_multiplier(host,'A',metric,source=source));host.state.players['C'].poison=3
        self.assertEqual(1,cast_reduction_multiplier(host,'A',metric,source=source))
        host.state.players['C'].poison=None
        with self.assertRaises(ValueError):cast_reduction_multiplier(host,'A',metric,source=source)
        with self.assertRaises(ValueError):cast_reduction_multiplier(host,'A',metric,source=None)

    def test_two_discount_sentences_are_independent_terms_and_unknown_second_clause_is_residual(self):
        text='This spell costs {1} less to cast if you control a Spirit. It also costs {1} less to cast if you control an enchantment.'
        compiled=self_spell_cost_reduction_handler(text);self.assertIsNotNone(compiled)
        spec=SelfSpellCostReductionSpec.from_dict(compiled[1]['reduction']);self.assertEqual(2,len(spec.terms))
        host,source=cost_host();spirit=CardInstance('spirit','spirit','spirit','Spirit','A','A','battlefield');enchant=CardInstance('enchant','enchant','enchant','Enchant','A','A','battlefield')
        host.state.cards.update({c.object_id:c for c in (spirit,enchant)})
        host._effective_card_data=lambda card,**kw:{'name':card.printed_name,'type_line':'Creature — Spirit' if card.object_id=='spirit' else 'Enchantment','mana_value':1,'colors':[],'keywords':[],'power':'1','toughness':'1'}
        host._type_parts=lambda text:({'creature'},{'spirit'},set()) if 'Creature' in text else ({'enchantment'},set(),set())
        for ids,expected in (([],0),(['spirit'],1),(['enchant'],1),(['spirit','enchant'],2)):
            host.state.players['A'].zones['battlefield']=ids;self.assertEqual(expected,evaluated_self_reduction(host,'A',spec,source=source)['GENERIC'])
        self.assertIsNone(self_spell_cost_reduction_handler(text.replace('you control an enchantment','an unrepresented condition holds')))

    def test_verified_public_condition_withdrawal_preserves_legacy_artifact_reduction(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['public self-cost condition withdrawn'])
        reg=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()));reg.mark_evidence_verified(validate_capability_evidence_index(index,registry=reg))
        self.assertNotEqual('exact',compile_oracle_card(query_record('This spell costs {2} less to cast if an opponent has three or more poison counters.'),capability_registry=reg).status)
        self.assertEqual('exact',compile_oracle_card(query_record('This spell costs {1} less to cast if you control an artifact.'),capability_registry=reg).status)
        paired='This spell costs {1} less to cast if you control a Spirit. It also costs {1} less to cast if you control an enchantment.'
        self.assertEqual('exact',compile_oracle_card(query_record(paired),capability_registry=reg).status)

    def test_omitting_prospective_zone_removal_mutant_is_killed(self):
        import quorune.rules.casting.public_conditions as owner
        host,source=cost_host()
        def witness():
            view,spell=owner.prospective_cost_condition_view(host,'A',source)
            self.assertNotIn(source.object_id,view.state.players['B'].zones['graveyard'])
        witness()
        with patch.object(owner,'prospective_cost_condition_view',return_value=(host,source)),self.assertRaises(AssertionError):witness()


class SelfPublicCostNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.SourceAttackAloneNativeTests.session
    add=native.SourceAttackAloneNativeTests.add
    prepare=native.SourceAttackAloneNativeTests.prepare
    cast=native.SourceAttackAloneNativeTests.cast
    cast_for=native.SourceAttackAloneNativeTests.cast_for
    action=native.SourceAttackAloneNativeTests.action
    priority=native.SourceAttackAloneNativeTests.priority
    checkpoint=native.SourceAttackAloneNativeTests.checkpoint
    resolve=native.SourceAttackAloneNativeTests.resolve
    resolve_top=native.SourceAttackAloneNativeTests.resolve_top
    replay=native.SourceAttackAloneNativeTests.replay
    assert_closed=native.SourceAttackAloneNativeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'self-public-cost.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/self-public-cost-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Self public cost',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def offered(self,session,source):
        return next((a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='cast:'+source.ref),None)

    def test_actual_corrupted_discount_uses_one_opponent_and_private_draws_keep_their_principals(self):
        session=self.session(601643001);source=self.add(session.engine,'Distorted Curiosity',zone='hand');prologue=self.add(session.engine,'Prologue to Phyresis',seat='B',zone='hand')
        self.assert_closed(session,source);session.state.players['A'].poison=8;session.state.players['B'].poison=1;session.state.players['C'].poison=2
        self.prepare(session,mana={'U':1});session.state.players['B'].mana_pool.update({'C':1,'U':1});self.checkpoint(session);self.priority(session,'A');self.assertIsNone(self.offered(session,source))
        foreign_draw=session.state.cards[session.state.players['B'].zones['library'][-1]].ref
        self.cast(session,'B',prologue);self.assertIsNone(self.resolve(session));self.priority(session,'A');self.assertIsNotNone(self.offered(session,source))
        hand=len(session.state.players['A'].zones['hand']);before=authoritative_state_hash(session.state);command={'action_id':'cast:'+source.ref,'pay':'auto'}
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state));self.cast(session,'A',source)
        item=next(i for i in session.state.stack if i.card_object_id==source.object_id);self.assertEqual(1,sum(item.mana_spent_counts.amounts))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
        for seat in 'ACD':self.assertNotIn(foreign_draw,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_dusk_cast_from_graveyard_excludes_itself_until_another_creature_card_arrives(self):
        session=self.session(601643010);provider=self.add(session.engine,'Karador, Ghost Chieftain');source=self.add(session.engine,'Dusk Feaster',zone='graveyard')
        target=self.add(session.engine,'Grizzly Bears');self.add(session.engine,'Sol Ring',zone='graveyard');self.add(session.engine,'Swamp',zone='graveyard');self.add(session.engine,'Shock',zone='graveyard')
        kill=self.add(session.engine,'Doom Blade',seat='B',zone='hand');self.assert_closed(session,provider);self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'B':2});session.state.players['B'].mana_pool.update({'C':1,'B':1});self.checkpoint(session);self.priority(session,'A');self.assertIsNone(self.offered(session,source))
        self.cast(session,'B',kill,[target.ref]);self.assertIsNone(self.resolve(session));self.priority(session,'A');offered=self.offered(session,source);self.assertIsNotNone(offered)
        option=next(o for o in offered['cost_options'] if o.get('permission_source'))
        result=session.act('pilot:A',{'action_id':offered['id'],'cost_option':option['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        item=next(i for i in session.state.stack if i.card_object_id==source.object_id);self.assertEqual(5,sum(item.mana_spent_counts.amounts))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual('battlefield',session.state.cards[source.object_id].zone);self.replay(session,load=True)

    def test_actual_paired_snare_discount_pays_blue_and_rejects_a_changed_stale_offer(self):
        for remove in (False,True):
            with self.subTest(remove=remove):
                session=self.session(601643020+remove);source=self.add(session.engine,'Geistlight Snare',zone='hand');spirit=self.add(session.engine,'Chapel Geist');self.add(session.engine,'Fervor')
                self.add(session.engine,'Vedalken Orrery',seat='B');spell=self.add(session.engine,'Divination',seat='B',zone='hand');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
                self.assert_closed(session,source);self.prepare(session,mana={'U':1});session.state.players['B'].mana_pool.update({'C':2,'U':2});self.checkpoint(session)
                self.cast_for(session,'B',spell,reserve={'U':1});self.priority(session,'A');offered=self.offered(session,source);self.assertIsNotNone(offered)
                target=next(i for i in session.state.stack if i.card_object_id==spell.object_id)
                command={'action_id':offered['id'],'targets':[target.ref],'pay':'auto'}
                if remove:
                    self.cast(session,'B',bounce,[spirit.ref]);self.resolve_top(session);self.priority(session,'A');self.assertIsNone(self.offered(session,source))
                    before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(session.state));self.assertEqual(1,session.state.players['A'].mana_pool['U']);self.replay(session,load=True)
                else:
                    result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary);item=next(i for i in session.state.stack if i.card_object_id==source.object_id);self.assertEqual(1,sum(item.mana_spent_counts.amounts))
                    decision=self.resolve(session);self.assertIsNotNone(decision);self.assertEqual('B',decision.actors[0]);result=session.act('pilot:B',{'action_id':'choose','pay':False});self.assertTrue(result.ok,result.summary)
                    self.assertIsNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[spell.object_id].zone);self.replay(session,load=True)

    def test_runtime_shared_cost_dependency_is_inferred_without_declaration(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(601643030);source=self.add(session.engine,'Dusk Feaster');found=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            found+=1;deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY);changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
        self.assertGreater(found,0)

    def test_actual_mortality_spear_uses_its_casters_committed_life_gain_not_opponents_gain(self):
        session=self.session(601643040);source=self.add(session.engine,'Mortality Spear',zone='hand');fountain=self.add(session.engine,'Radiant Fountain',zone='hand')
        target=self.add(session.engine,'Grizzly Bears',seat='B');revitalize=self.add(session.engine,'Revitalize',seat='B',zone='hand')
        self.assert_closed(session,source);self.assert_closed(session,fountain);self.prepare(session,mana={'B':1,'G':1});session.state.players['B'].mana_pool.update({'C':1,'W':1});self.checkpoint(session)
        self.priority(session,'A');self.assertIsNone(self.offered(session,source));self.cast(session,'B',revitalize);self.assertIsNone(self.resolve(session));self.priority(session,'A');self.assertIsNone(self.offered(session,source))
        result=session.act('pilot:A',{'action_id':'play-land:'+fountain.ref});self.assertTrue(result.ok,result.summary);self.priority(session,'A');self.assertIsNone(self.offered(session,source))
        self.assertIsNone(self.resolve(session));self.priority(session,'A');self.assertIsNotNone(self.offered(session,source))
        self.cast(session,'A',source,[target.ref]);item=next(i for i in session.state.stack if i.card_object_id==source.object_id);self.assertEqual(2,sum(item.mana_spent_counts.amounts))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[target.object_id].zone);self.replay(session,load=True)

    def test_actual_avatar_life_threshold_revalidates_each_offer_and_keeps_original_mana_value(self):
        session=self.session(601643050);source=self.add(session.engine,'Avatar of Hope',zone='hand');first=self.add(session.engine,'Lightning Bolt',zone='hand',ref='first-self-bolt');second=self.add(session.engine,'Lightning Bolt',zone='hand',ref='second-self-bolt')
        self.add(session.engine,'Sol Ring');self.add(session.engine,'Mountain');heal=self.add(session.engine,'Healing Salve',seat='B',zone='hand')
        self.assert_closed(session,source);self.assert_closed(session,heal);session.state.players['A'].life=4;self.prepare(session,mana={'R':1,'W':2});session.state.players['B'].mana_pool['W']=1;self.checkpoint(session)
        self.priority(session,'A');self.assertIsNone(self.offered(session,source));self.cast(session,'A',first,['A']);self.assertIsNone(self.resolve(session));self.priority(session,'A')
        self.assertEqual(1,session.state.players['A'].life);old=self.offered(session,source);self.assertIsNotNone(old)
        self.priority(session,'B');action=self.action(session,'B',heal)
        result=session.act('pilot:B',{'action_id':action['id'],'modes':['mode_1'],'targets':['A'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.priority(session,'A');self.assertEqual(4,session.state.players['A'].life);self.assertIsNone(self.offered(session,source))
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:A',{'action_id':old['id'],'pay':'auto'}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',second,['A']);self.assertIsNone(self.resolve(session));self.priority(session,'A');self.assertEqual(1,session.state.players['A'].life);self.assertIsNotNone(self.offered(session,source))
        self.cast(session,'A',source);item=next(i for i in session.state.stack if i.card_object_id==source.object_id);self.assertEqual(2,sum(item.mana_spent_counts.amounts));self.assertEqual(8.0,session.engine._effective_card_data(session.state.cards[source.object_id])['mana_value'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual((4,9),tuple(session.engine._numeric_stat(source.object_id,s) for s in ('power','toughness')));self.replay(session,load=True)
