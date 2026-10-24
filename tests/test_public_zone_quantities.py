from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope as S,CharacteristicQuantityReduction as R
from quorune.object_predicate import ObjectQuerySpec
from quorune.dynamic_characteristics import query_characteristic_count
from scripts.build_test_database import build_fixture_database
import test_created_token_counters as controls
from quorune.rules.evidence_storage import read_evidence_document

COLLECTION='quantity_expression.public_owned_zone_collection'
SIZE='quantity_expression.zone_cardinality'
NAMES=('Adamaro, First to Desire','Enemy of Enlightenment','Beacon Bolt','Cosmogoyf',
    'Sage of Ancient Lore // Werewolf of Ancient Hunger','Multani, Maro-Sorcerer',
    'Crackling Drake','Serpentine Curve','Body of Research')


class PublicZoneQuantityTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.CreatedTokenCounterTests.session
    add=controls.CreatedTokenCounterTests.add
    prepare=controls.CreatedTokenCounterTests.prepare
    checkpoint=controls.CreatedTokenCounterTests.checkpoint
    resolve=controls.CreatedTokenCounterTests.resolve
    replay=controls.CreatedTokenCounterTests.replay
    binding=controls.CreatedTokenCounterTests.binding
    actions=controls.CreatedTokenCounterTests.actions
    cast=controls.CreatedTokenCounterTests.cast
    reject=controls.CreatedTokenCounterTests.reject
    private_replay=controls.CreatedTokenCounterTests.private_replay
    priority=controls.CreatedTokenCounterTests.priority
    options=controls.CreatedTokenCounterTests.options
    use=controls.CreatedTokenCounterTests.use
    top=controls.CreatedTokenCounterTests.top
    settle=controls.CreatedTokenCounterTests.settle
    normal=controls.CreatedTokenCounterTests.normal
    advance=controls.CreatedTokenCounterTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'public-zone-quantity.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/public-zone-quantity-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Closed card collection quantities',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def clear_hands(self,s):
        for player in s.state.players.values():
            for oid in tuple(player.zones['hand']):s.engine.move_card(oid,'library',log=False)

    def hand(self,s,seat,count):
        return [self.add(s.engine,'Generic Bound Growth',seat=seat,zone='hand',ref=f'private-{seat}-{i}') for i in range(count)]

    def test_nine_originals_admit_and_require_their_actual_quantity_producer(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(809020)
        for i,name in enumerate(NAMES):
            card=self.add(s.engine,name,ref='zone-source-'+str(i));bound=self.binding(s,card)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            cap=COLLECTION if name in ('Beacon Bolt','Cosmogoyf','Crackling Drake','Serpentine Curve') else SIZE
            program=next((p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if cap in p.capability_dependencies),None)
            self.assertIsNotNone(program,name);deps=tuple(d for d in program.capability_dependencies if d!=cap)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])

    def test_real_adamaro_uses_maximum_opponent_hand_and_updates_on_concession(self):
        s=self.session(809030);self.clear_hands(s);spell=self.add(s.engine,'Adamaro, First to Desire',zone='hand')
        self.hand(s,'B',4);private=self.hand(s,'C',2)[0];self.hand(s,'D',1)
        self.prepare(s,mana={'C':1,'R':2});self.checkpoint(s);self.normal(s,spell,{'C':1,'R':2})
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(4,s.engine._numeric_stat(spell.object_id,'power'))
        self.priority(s,'B');result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
        self.assertEqual(2,s.engine._numeric_stat(spell.object_id,'power'));self.assertEqual(2,s.engine._numeric_stat(spell.object_id,'toughness'))
        self.private_replay(s,private)

    def test_real_multani_counts_all_hands_and_preserves_shroud(self):
        s=self.session(809040);self.clear_hands(s);spell=self.add(s.engine,'Multani, Maro-Sorcerer',zone='hand')
        self.hand(s,'A',1);self.hand(s,'B',3);private=self.hand(s,'C',1)[0];self.hand(s,'D',2)
        self.prepare(s,mana={'C':4,'G':2});self.checkpoint(s);self.normal(s,spell,{'C':4,'G':2})
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(7,s.engine._numeric_stat(spell.object_id,'power'))
        self.assertIn('shroud',s.engine._combat_keywords(s.state.cards[spell.object_id]))
        self.priority(s,'B');result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
        self.assertEqual(4,s.engine._numeric_stat(spell.object_id,'power'));self.private_replay(s,private)

    def test_real_cosmogoyf_counts_face_down_cards_and_itself_after_departure(self):
        s=self.session(809050);spell=self.add(s.engine,'Cosmogoyf',zone='hand')
        hidden=self.add(s.engine,'Saw It Coming',zone='hand')
        self_exiled=self.add(s.engine,'Cosmogoyf',zone='exile',ref='self-exiled-cosmogoyf')
        self.assertEqual(1,s.engine._numeric_stat(self_exiled.object_id,'power'))
        self.add(s.engine,'Divination',seat='B',zone='exile',ref='foreign-cosmo')
        exile=self.add(s.engine,'Go for the Throat',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'B':2,'G':1,'C':3});self.checkpoint(s);self.normal(s,spell,{'B':1,'G':1})
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(1,s.engine._numeric_stat(spell.object_id,'power'))
        action=next((a for a in self.actions(s) if a.get('action')=='stage_cast_lifecycle' and hidden.ref in json.dumps(a)),None)
        self.assertIsNotNone(action,self.actions(s));command={'action_id':action['id'],'pay':'manual','payment':{'C':2}}
        self.reject(s,command,'pilot:D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertTrue(s.state.cards[hidden.object_id].face_down);self.assertEqual(2,s.engine._numeric_stat(self_exiled.object_id,'power'))
        for seat in 'BCD':self.assertNotIn('Saw It Coming',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.normal(s,exile,{'B':1,'C':1},targets=[spell.ref]);self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.assertEqual(2,s.engine._numeric_stat(spell.object_id,'power'))
        self.private_replay(s,private)

    def test_real_serpentine_curve_reads_union_once_before_its_own_graveyard_arrival(self):
        s=self.session(809060);spell=self.add(s.engine,'Serpentine Curve',zone='hand')
        self.add(s.engine,'Divination',zone='graveyard');self.add(s.engine,'Lightning Bolt',zone='exile')
        hidden=self.add(s.engine,'Counterspell',zone='exile');hidden.face_down=True
        self.add(s.engine,'Divination',seat='B',zone='exile',ref='foreign-curve')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'U':1});self.checkpoint(s);self.normal(s,spell,{'C':3,'U':1})
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(1,len(tokens));self.assertEqual(3,tokens[0].counters.get('+1/+1'));self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
        self.assertEqual(3,s.engine._numeric_stat(tokens[0].object_id,'power'));self.private_replay(s,private)

    def test_real_beacon_bolt_jump_start_counts_paid_discard_but_not_the_spell(self):
        s=self.session(809070);spell=self.add(s.engine,'Beacon Bolt',zone='graveyard')
        discard=self.add(s.engine,'Divination',zone='hand');self.add(s.engine,'Lightning Bolt',zone='exile')
        target=self.add(s.engine,'Generic Bound Body',seat='B');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'U':1,'R':1});self.checkpoint(s)
        option=next((o for o in self.options(s,spell) if o['id']=='jump-start'),None);self.assertIsNotNone(option)
        self.use(s,spell,option,{'C':1,'U':1,'R':1},targets=[target.ref],discard_cards=[discard.ref])
        self.assertEqual('graveyard',s.state.cards[discard.object_id].zone);s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.assertEqual(2,s.state.cards[target.object_id].marked_damage);self.assertEqual('exile',s.state.cards[spell.object_id].zone);self.private_replay(s,private)

    def test_real_sage_draw_and_history_transform_use_different_live_hand_domains(self):
        s=self.session(809080);self.clear_hands(s);spell=self.add(s.engine,'Sage of Ancient Lore // Werewolf of Ancient Hunger',zone='hand')
        self.hand(s,'A',1);self.hand(s,'B',2);private=self.hand(s,'C',1)[0];self.hand(s,'D',1)
        self.top(s,'Island','sage-draw');self.prepare(s,mana={'C':4,'G':1});self.checkpoint(s);self.normal(s,spell,{'C':4,'G':1})
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(2,s.engine._numeric_stat(spell.object_id,'power'))
        self.advance(s,lambda current:current.state.active_player=='C' and current.state.step=='upkeep')
        self.assertIsNone(self.settle(s));card=s.state.cards[spell.object_id];self.assertEqual('Werewolf of Ancient Hunger',card.active_face)
        total=sum(len(p.zones['hand']) for p in s.state.players.values() if p.in_game)
        self.assertEqual(total,s.engine._numeric_stat(spell.object_id,'power'))
        self.assertLessEqual({'vigilance','trample'},s.engine._combat_keywords(card));self.private_replay(s,private)

    def test_real_enemy_counts_only_opponent_hands_and_revalidates_departure(self):
        s=self.session(809090);self.clear_hands(s);spell=self.add(s.engine,'Enemy of Enlightenment',zone='hand')
        self.hand(s,'A',4);self.hand(s,'B',1);private=self.hand(s,'C',1)[0]
        self.prepare(s,mana={'C':5,'B':1});self.checkpoint(s);self.normal(s,spell,{'C':5,'B':1})
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.assertEqual(3,s.engine._numeric_stat(spell.object_id,'power'))
        self.assertIn('flying',s.engine._combat_keywords(s.state.cards[spell.object_id]))
        self.priority(s,'B');result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
        self.assertEqual(4,s.engine._numeric_stat(spell.object_id,'power'))
        self.advance(s,lambda current:current.state.active_player=='A' and current.state.step=='upkeep')
        pending=self.settle(s);selected=[]
        while pending is not None:
            principal=s.pending_principals()[0];seat=principal.split(':')[-1]
            card=s.state.cards[s.state.players[seat].zones['hand'][-1]];selected.append(card.object_id)
            command={'action_id':'choose','cards':[card.ref]};self.reject(s,command,'pilot:D' if seat!='D' else 'pilot:A')
            s=self.replay(s,load=True);result=s.act(principal,command);self.assertTrue(result.ok,(pending.kind,result.summary));pending=self.settle(s)
        self.assertEqual(3,len(selected));self.assertTrue(all(s.state.cards[oid].zone=='graveyard' for oid in selected))
        self.assertEqual(4,s.engine._numeric_stat(spell.object_id,'power'));self.private_replay(s,private)

    def test_public_union_reads_no_face_down_characteristics_and_counts_cards_once(self):
        from quorune.compiler.public_zone_quantities import public_zone_quantity
        s=self.session(809100);card=self.add(s.engine,'Cosmogoyf');dual=self.add(s.engine,'Divination',zone='exile')
        hidden=self.add(s.engine,'Counterspell',zone='exile');hidden.face_down=True
        token=self.add(s.engine,'Lightning Bolt',zone='exile');token.object_kind='token';token.is_token=True
        self.add(s.engine,'Divination',seat='B',zone='graveyard',ref='foreign-union')
        original=s.engine._effective_card_data
        def effective(subject,**kwargs):
            self.assertFalse(subject.face_down,'Face-down characteristics were inspected')
            return {**original(subject,**kwargs),'type_line':'Instant Sorcery'} if subject.object_id==dual.object_id else original(subject,**kwargs)
        quantity=public_zone_quantity('the total number of instant and sorcery cards you own in exile and in your graveyard')
        with patch.object(s.engine,'_effective_card_data',side_effect=effective):self.assertEqual(1,query_characteristic_count(s.engine,card,quantity))
        raw=public_zone_quantity('cards you own in exile')
        with patch.object(s.engine,'_effective_card_data',side_effect=AssertionError('Raw count inspected characteristics')):
            self.assertEqual(2,query_characteristic_count(s.engine,card,raw))
        for reduction in (R.MAXIMUM_ZONE_SIZE,R.TOTAL_MANA_VALUE):
            with self.assertRaises(ValueError):replace(quantity,reduction=reduction)
        self.assertEqual(public_zone_quantity("cards in your opponents' hands"),public_zone_quantity('cards in your opponents’ hands'))
        for text in ('instant cards in your library','cards you own in exile with an Adventure','cards in target player\'s library','cards in your opponentsx hands'):
            self.assertIsNone(public_zone_quantity(text))

    def test_raw_hidden_zone_sizes_read_only_public_object_kind(self):
        class KindOnly:
            def __init__(self,kind):object.__setattr__(self,'object_kind',kind)
            def __getattribute__(self,key):
                if key!='object_kind':raise AssertionError('A hidden characteristic was read: '+key)
                return object.__getattribute__(self,key)
        cards={'a':KindOnly('card'),'b':KindOnly('card'),'token':KindOnly('token')}
        players={'A':SimpleNamespace(in_game=True,zones={'library':['a','token'],'hand':['a']}),
            'B':SimpleNamespace(in_game=True,zones={'library':['b'],'hand':['b']}),
            'C':SimpleNamespace(in_game=False,zones={'library':[],'hand':['a','b']})}
        host=SimpleNamespace(state=SimpleNamespace(players=players,cards=cards));source=SimpleNamespace(controller='A')
        for scope,zone,reduction,expected in ((S.CONTROLLER_ZONE,'library',R.OBJECT_COUNT,1),
                (S.ALL_ZONES,'hand',R.OBJECT_COUNT,2),(S.OPPONENT_ZONES,'hand',R.MAXIMUM_ZONE_SIZE,1)):
            quantity=CharacteristicQuantitySpec(scope,ObjectQuerySpec(zones=(zone,)),schema_version=5,reduction=reduction)
            self.assertEqual(expected,query_characteristic_count(host,source,quantity))
            self.assertEqual(quantity,CharacteristicQuantitySpec.from_dict(json.loads(json.dumps(quantity.to_dict()))))
        hidden=CharacteristicQuantitySpec(S.CONTROLLER_ZONE,ObjectQuerySpec(zones=('library',)),schema_version=5)
        players['B'].in_game=False
        maximum=CharacteristicQuantitySpec(S.OPPONENT_ZONES,ObjectQuerySpec(zones=('hand',)),schema_version=5,reduction=R.MAXIMUM_ZONE_SIZE)
        self.assertEqual(0,query_characteristic_count(host,source,maximum))
        for query in (ObjectQuerySpec(zones=('library',),types_all=('creature',)),ObjectQuerySpec(zones=('library',),known_to_actor=True)):
            with self.assertRaises(ValueError):replace(hidden,query=query)
        with self.assertRaises(ValueError):replace(hidden,schema_version=2)

    def test_real_body_of_research_counts_library_without_inspecting_cards(self):
        s=self.session(809000);spell=self.add(s.engine,'Body of Research',zone='hand')
        for oid in tuple(s.state.players['A'].zones['library']):s.engine.move_card(oid,'exile',log=False)
        self.add(s.engine,'Sol Ring',zone='library');self.add(s.engine,'Divination',zone='library')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.assertTrue(self.binding(s,spell)['strict_capability_ready'])
        self.prepare(s,mana={'G':3,'U':3});self.checkpoint(s);self.normal(s,spell,{'G':3,'U':3})
        s=self.replay(s,load=True);self.assertIsNone(self.settle(s));tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(1,len(tokens));self.assertEqual(2,tokens[0].counters.get('+1/+1'));self.private_replay(s,private)

    def test_real_crackling_drake_counts_union_once_and_draws(self):
        s=self.session(809010);spell=self.add(s.engine,'Crackling Drake',zone='hand')
        self.add(s.engine,'Divination',zone='graveyard');self.add(s.engine,'Lightning Bolt',zone='exile')
        self.add(s.engine,'Divination',seat='B',zone='exile',ref='foreign-exile')
        hidden=self.add(s.engine,'Counterspell',zone='exile');hidden.face_down=True
        drawn=self.top(s,'Sol Ring','drake-draw');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(s,spell)['strict_capability_ready']);self.prepare(s,mana={'U':2,'R':2});self.checkpoint(s)
        self.normal(s,spell,{'U':2,'R':2});s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.assertEqual(2,s.engine._numeric_stat(spell.object_id,'power'));self.assertIn('flying',s.engine._combat_keywords(s.state.cards[spell.object_id]))
        self.assertEqual('hand',s.state.cards[drawn.object_id].zone);self.private_replay(s,private)

    def test_verified_producer_withdrawal_and_fresh_missing_count_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        from copy import deepcopy
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for cap,name in ((COLLECTION,'Crackling Drake'),(SIZE,'Body of Research')):
            changed=deepcopy(raw);next(c for c in changed['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn public collection'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune import public_zone_quantities as owner
        method='test_real_body_of_research_counts_library_without_inspecting_cards'
        with patch.object(owner,'current_public_zone_quantity',return_value=0):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_real_crackling_drake_counts_union_once_and_draws'
        original=owner.current_public_zone_quantity
        def omit_collection(host,source,quantity,**kwargs):
            return 0 if quantity.query.zones==('exile','graveyard') else original(host,source,quantity,**kwargs)
        with patch.object(owner,'current_public_zone_quantity',side_effect=omit_collection):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
