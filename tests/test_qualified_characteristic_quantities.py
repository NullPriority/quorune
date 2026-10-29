"""Public keyword qualifications count through layer6, below their P/T result."""
from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_source_attack_alone as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope,QueryPowerToughnessDefinitionSpec,QueryCharacteristicModifierSpec,PowerToughnessCalculation
from quorune.continuous_effect_model import Layer
from quorune.dynamic_characteristics import query_characteristic_count
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

CAPABILITY='quantity_expression.keyword_layer_count'


class QualifiedQuantityCompilerTests(unittest.TestCase):
    def test_versioned_keyword_quantity_is_closed_and_legacy_codec_stays_exact(self):
        old=CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,ObjectQuerySpec(zones=('battlefield',),types_all=('creature',)))
        self.assertNotIn('reduction',old.to_dict());self.assertEqual(old,CharacteristicQuantitySpec.from_dict(old.to_dict()))
        new=replace(old,query=replace(old.query,keywords_all=('flying',)),schema_version=6)
        self.assertEqual(new,CharacteristicQuantitySpec.from_dict(new.to_dict()))
        for changes in ({'schema_version':1},{'schema_version':True},{'schema_version':7},{'scope':'source_counter'},{'multiplier':2}):
            with self.assertRaises(ValueError):CharacteristicQuantitySpec.from_dict({**new.to_dict(),**changes})
        with self.assertRaises(ValueError):QueryPowerToughnessDefinitionSpec(new,True,True)
        with self.assertRaises(ValueError):QueryCharacteristicModifierSpec(new,PowerToughnessCalculation.FIXED_IF_THRESHOLD,1,1,1,('Flying',))
        self.assertNotEqual('exact',compile_oracle_card(query_record("This creature's power and toughness are each equal to the number of creatures you control with flying."),capability_registry=load_default_capability_registry()).status)

    def test_scope_source_exclusion_and_qualifier_orders_keep_distinct_meaning(self):
        from quorune.compiler.query_characteristic_templates import query_characteristic_quantity
        for phrase,scope in (('other creature you control with flying','controller_zone'),('other creature with flying you control','controller_zone'),('other creature on the battlefield with flying','all_zones'),('creature your opponents control without flying','opponent_zones')):
            q=query_characteristic_quantity(phrase,source_name='Source');self.assertIsNotNone(q,phrase);self.assertEqual(6,q.schema_version);self.assertEqual(scope,q.scope.value)
            self.assertEqual(phrase.startswith('other '),q.exclude_source)
        for phrase in ('creature in your hand with flying','creature you control with an unrepresented ability','creature you control named Fixed Name','face-down creature on the battlefield'):
            self.assertIsNone(query_characteristic_quantity(phrase,source_name='Source'))

    def test_layer_boundary_uses_current_keywords_and_phased_out_exclusion(self):
        from quorune.model import CardInstance
        q=CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),keywords_all=('flying',)),exclude_source=True,schema_version=6)
        source=CardInstance('source','source','s','Source','A','A','battlefield');other=CardInstance('other','other','o','Other','A','A','battlefield');phased=CardInstance('phased','phased','p','Phased','A','A','battlefield',phased_out=True)
        seen=[]
        class Host:
            state=SimpleNamespace(cards={c.object_id:c for c in (source,other,phased)},players={'A':SimpleNamespace(zones={'battlefield':['source','other','phased']})})
            def _effective_card_data(self,card,**options):
                seen.append(options['maximum_layer']);return {'type_line':'Creature','keywords':['Flying'] if options['maximum_layer']==Layer.ABILITY else [],'power':'1','toughness':'1','colors':[]}
            def _type_parts(self,value):return {'creature'},set(),set()
        self.assertEqual(1,query_characteristic_count(Host(),source,q));self.assertEqual([Layer.ABILITY],seen)

    def test_omitting_keyword_layer_mutant_is_killed(self):
        import quorune.dynamic_characteristics as owner
        original=owner.query_characteristic_count
        from quorune.model import CardInstance
        source=CardInstance('source','source','s','Source','A','A','battlefield');flyer=CardInstance('f','f','f','Flyer','A','A','battlefield');ground=CardInstance('g','g','g','Ground','A','A','battlefield')
        class Host:
            state=SimpleNamespace(cards={c.object_id:c for c in (source,flyer,ground)},players={'A':SimpleNamespace(zones={'battlefield':['source','f','g']})})
            def _effective_card_data(self,card,**options):return {'type_line':'Creature','keywords':['Flying'] if card.object_id=='f' and options['maximum_layer']==Layer.ABILITY else [],'power':'1','toughness':'1','colors':[]}
            def _type_parts(self,value):return {'creature'},set(),set()
        q=CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),keywords_all=('flying',)),schema_version=6)
        def witness():self.assertEqual(1,owner.query_characteristic_count(Host(),source,q))
        witness()
        def wrong(host,source,quantity,**kwargs):
            effective=host._effective_card_data
            host._effective_card_data=lambda card,**options:effective(card,**{**options,'maximum_layer':Layer.COLOR})
            return original(host,source,quantity,**kwargs)
        with patch.object(owner,'query_characteristic_count',side_effect=wrong),self.assertRaises(AssertionError):witness()

    def test_layer_six_view_does_not_materialize_later_keyword_count(self):
        from quorune.ability_fragments import ability_fragment_to_dict
        from quorune.characteristic_evaluation import evaluate_card_characteristics
        from quorune.model import CardInstance
        q=CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE,ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),keywords_all=('flying',)),schema_version=6)
        fragment=QueryCharacteristicModifierSpec(q,PowerToughnessCalculation.PER_MATCHING_OBJECT,1,1)
        card=CardInstance('source','source','s','Source','A','A','battlefield')
        base={'name':'Source','type_line':'Creature','power':'1','toughness':'1','colors':[],'keywords':[],'ability_fragments':[dict(ability_fragment_to_dict(fragment))]}
        calls=[]
        early=evaluate_card_characteristics(card,base,query_count_resolver=lambda query:calls.append(query) or 2,maximum_layer=Layer.ABILITY)
        self.assertEqual([],calls);self.assertEqual('1',str(early['power']))
        full=evaluate_card_characteristics(card,base,query_count_resolver=lambda query:calls.append(query) or 2)
        self.assertEqual([q],calls);self.assertEqual('3',str(full['power']))

    def test_verified_keyword_count_withdrawal_preserves_ordinary_artifact_count(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['keyword-layer count withdrawn'])
        reg=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()));reg.mark_evidence_verified(validate_capability_evidence_index(index,registry=reg))
        self.assertNotEqual('exact',compile_oracle_card(query_record('This creature gets +1/+1 for each other creature you control with flying.'),capability_registry=reg).status)
        self.assertEqual('exact',compile_oracle_card(query_record('This creature gets +1/+1 for each artifact you control.'),capability_registry=reg).status)


class QualifiedQuantityNativeTests(unittest.TestCase):
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
    replay=native.SourceAttackAloneNativeTests.replay
    assert_closed=native.SourceAttackAloneNativeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'qualified-quantity.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/qualified-quantity-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Qualified quantities',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_actual_skycat_uses_current_layer_six_keywords_and_own_controller_scope(self):
        session=self.session(613641001);source=self.add(session.engine,'Skycat Sovereign',zone='hand');body=self.add(session.engine,'Grizzly Bears')
        foreign=self.add(session.engine,'Ornithopter',seat='B');aura=self.add(session.engine,'Spectral Flight',zone='hand');remove=self.add(session.engine,'Disenchant',seat='B',zone='hand')
        self.add(session.engine,'Plains',seat='B');self.add(session.engine,'Wastes',seat='B');self.assert_closed(session,source)
        private=session.state.cards[session.state.players['D'].zones['hand'][0]].ref
        self.prepare(session,mana={'C':1,'W':1,'U':2});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'C':1,'U':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.engine._numeric_stat(source.object_id,'power'));self.cast(session,'A',aura,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(2,session.engine._numeric_stat(source.object_id,'power'));self.assertEqual(4,session.engine._numeric_stat(body.object_id,'power'))
        session=self.replay(session,load=True);self.cast(session,'B',remove,[aura.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.engine._numeric_stat(source.object_id,'power'));self.assertIn('flying',session.engine._combat_keywords(session.state.cards[foreign.object_id]))
        for seat in 'ABC':self.assertNotIn(private,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_radiant_counts_foreign_flyers_but_excludes_itself_and_ground_creatures(self):
        session=self.session(613641010);source=self.add(session.engine,'Radiant, Archangel',zone='hand');self.add(session.engine,'Ornithopter');foreign=self.add(session.engine,'Ornithopter',seat='B',ref='foreign-radiant-flyer');self.add(session.engine,'Grizzly Bears')
        bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B');self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'W':2});self.checkpoint(session);self.cast(session,'A',source);self.assertIsNone(self.resolve(session));self.assertEqual(5,session.engine._numeric_stat(source.object_id,'power'))
        self.cast(session,'B',bounce,[foreign.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual(4,session.engine._numeric_stat(source.object_id,'power'));self.replay(session,load=True)

    def test_runtime_keyword_quantity_dependency_is_inferred_from_descriptor(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(613641020);source=self.add(session.engine,'Skycat Sovereign');found=0
        for p in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in p.capability_dependencies:continue
            found+=1;deps=tuple(c for c in p.capability_dependencies if c!=CAPABILITY);changed=replace(p,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review');self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers'])
        self.assertGreater(found,0)
