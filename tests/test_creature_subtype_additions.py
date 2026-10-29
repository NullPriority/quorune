"""Pinned CR205/613 and Graaz rulings: addition preserves unrelated characteristics."""

from copy import deepcopy
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_enchanted_player_statics as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.compiler.fixed_characteristic_settings import fixed_characteristic_setting_handler
from scripts.build_test_database import build_fixture_database

CAPABILITY='continuous.characteristics.fixed_public_setting'
GRAAZ='Other creatures you control have base power and toughness 5/3 and are Juggernauts in addition to their other creature types.'
HIVE='Creatures you control are Slivers in addition to their other creature types.'
CRUSADE='All Goblins are black and are Zombies in addition to their other creature types.'


class CreatureSubtypeAdditionCompilerTests(unittest.TestCase):
    def test_complete_qualified_additions_use_existing_subtype_operations_and_spans(self):
        registry=load_default_capability_registry()
        for text,subtype in ((GRAAZ,'Juggernaut'),(HIVE,'Sliver'),(CRUSADE,'Zombie'),
                ('Enchanted creature is a Time Lord in addition to its other creature types.','Time Lord')):
            ir=compile_oracle_card(query_record(text),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(text,ir.material_residuals));node,=ir.faces[0].nodes
            self.assertEqual((text,0,len(text)),(node.text,node.span.start,node.span.end))
            self.assertIn(CAPABILITY,node.capability_dependencies)
            self.assertEqual([{'op':'add_types','field':'subtypes','values':[subtype]}],node.handlers[0]['modifier']['type_operations'])
        for value in ('an Artifact','a Forest','a chosen type','a red Goblin','an UnknownSubtype','an Elf Warrior'):
            text=f'Enchanted creature is {value} in addition to its other creature types.'
            self.assertIsNone(fixed_characteristic_setting_handler(text),text)
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status,text)
        for text in ('All creatures lose all creature types.','All creatures are every creature type.',HIVE+' Draw a card.'):
            self.assertIsNone(fixed_characteristic_setting_handler(text),text)

    def test_verified_setting_withdrawal_blocks_qualified_addition_with_unrelated_keyword_control(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text());next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['Subtype setting owner withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(query_record(GRAAZ),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(query_record('Flying'),capability_registry=registry).status)


class CreatureSubtypeAdditionNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.EnchantedPlayerStaticNativeTests.session
    add=native.EnchantedPlayerStaticNativeTests.add
    prepare=native.EnchantedPlayerStaticNativeTests.prepare
    cast=native.EnchantedPlayerStaticNativeTests.cast
    cast_for=native.EnchantedPlayerStaticNativeTests.cast_for
    action=native.EnchantedPlayerStaticNativeTests.action
    priority=native.EnchantedPlayerStaticNativeTests.priority
    checkpoint=native.EnchantedPlayerStaticNativeTests.checkpoint
    resolve=native.EnchantedPlayerStaticNativeTests.resolve
    replay=native.EnchantedPlayerStaticNativeTests.replay
    assert_closed=native.EnchantedPlayerStaticNativeTests.assert_closed
    until=native.EnchantedPlayerStaticNativeTests.until
    stats=native.EnchantedPlayerStaticNativeTests.stats
    activate_mana=native.EnchantedPlayerStaticNativeTests.activate_mana

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'subtype-additions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/creature-subtype-addition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Subtype additions',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def parts(self,session,card):return tuple(set(p) for p in session.engine._type_parts(session.engine._effective_card_data(session.state.cards[card.object_id])['type_line']))

    def test_actual_graaz_preserves_other_types_counters_tokens_and_controller_with_attack_block_and_removal(self):
        session=self.session(205625001);giver=self.add(session.engine,'Graaz, Unstoppable Juggernaut',zone='hand')
        elf=self.add(session.engine,'Llanowar Elves',ref='graaz-elf');elf.counters['+1/+1']=1;elf.acquired_control_turn_count=-1
        ally=self.add(session.engine,'Grizzly Bears',ref='graaz-ally');ally.acquired_control_turn_count=-1
        arbor=self.add(session.engine,'Dryad Arbor',ref='graaz-arbor');arbor.acquired_control_turn_count=-1
        token=self.add(session.engine,'Memnite',ref='graaz-token');token.is_token=True;token.object_kind='token';token.acquired_control_turn_count=-1
        blocker=self.add(session.engine,'Grizzly Bears',seat='B',ref='graaz-enemy-blocker');wall=self.add(session.engine,'Wall of Wood',seat='B',ref='graaz-wall')
        removal=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B',ref='graaz-removal-island')
        private=self.add(session.engine,'Twiddle',seat='D',zone='hand')
        self.assert_closed(session,giver);self.prepare(session,mana={'C':8});self.checkpoint(session)
        self.cast(session,'A',giver);self.assertIsNone(self.resolve(session))
        self.assertEqual((7,5),self.stats(session,giver));self.assertEqual((6,4),self.stats(session,elf))
        for receiver in (ally,arbor,token):self.assertEqual((5,3),self.stats(session,receiver))
        self.assertEqual((2,2),self.stats(session,blocker));self.assertNotIn('juggernaut',self.parts(session,blocker)[1])
        self.assertTrue({'elf','juggernaut'}<=self.parts(session,elf)[1]);self.assertEqual(['G'],session.engine._effective_card_data(session.state.cards[elf.object_id])['colors'])
        self.assertEqual(({'land','creature'},{'forest','dryad','juggernaut'}),self.parts(session,arbor)[:2]);self.assertTrue(session.state.cards[token.object_id].is_token)
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:A',{'action_id':'attack','attackers':{}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'attack','attackers':{c.ref:'B' for c in (elf,ally,arbor,token)}});self.assertTrue(result.ok,result.summary)
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:B',{'action_id':'block','blocks':{wall.ref:elf.ref}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:elf.ref}});self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',removal,[giver.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual((2,2),self.stats(session,elf));self.assertEqual((1,1),self.stats(session,arbor));self.assertNotIn('juggernaut',self.parts(session,elf)[1])
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_graaz_layer_four_continues_after_ability_loss_but_later_only_anthem_does_not(self):
        from quorune.rules.bound_static_declarations import bound_declaration_requirements
        session=self.session(205625010);earlier=self.add(session.engine,'Humility',seat='B',ref='earlier-humility')
        champion=self.add(session.engine,'Elvish Champion',ref='later-only-anthem')
        self.add(session.engine,'Elvish Archdruid',ref='pure-stat-anthem')
        elf=self.add(session.engine,'Llanowar Elves',ref='layered-elf');elf.counters['+1/+1']=1
        giver=self.add(session.engine,'Graaz, Unstoppable Juggernaut',zone='hand')
        removal=self.add(session.engine,'Disenchant',zone='hand');later=self.add(session.engine,'Humility',seat='B',zone='hand',ref='later-humility')
        self.add(session.engine,'Vedalken Orrery',seat='B')
        self.assert_closed(session,giver);self.prepare(session,mana={'C':9,'W':1});session.state.players['B'].mana_pool.update({'C':2,'W':2});self.checkpoint(session)
        self.cast_for(session,'A',giver,reserve={'C':1,'W':1});self.assertIsNone(self.resolve(session))
        self.assertEqual((1,1),self.stats(session,giver));self.assertEqual((6,4),self.stats(session,elf))
        self.assertIn('juggernaut',self.parts(session,elf)[1]);self.assertFalse(session.engine._effective_ability_fragments(session.state.cards[giver.object_id]))
        self.assertFalse(bound_declaration_requirements(session.engine,session.state.cards[elf.object_id],error_type=ValueError))
        self.cast(session,'A',removal,[earlier.ref]);self.assertIsNone(self.resolve(session));self.assertEqual((8,6),self.stats(session,elf))
        self.assertTrue(bound_declaration_requirements(session.engine,session.state.cards[elf.object_id],error_type=ValueError))
        self.cast(session,'B',later);self.assertIsNone(self.resolve(session));self.assertEqual((3,3),self.stats(session,elf))
        self.assertIn('juggernaut',self.parts(session,elf)[1]);self.assertFalse(bound_declaration_requirements(session.engine,session.state.cards[elf.object_id],error_type=ValueError))
        self.replay(session,load=True)

    def test_actual_hivestone_enables_global_sliver_grants_and_mana_pays_for_its_removal(self):
        session=self.session(205625020);giver=self.add(session.engine,'Hivestone',zone='hand')
        body=self.add(session.engine,'Grizzly Bears',ref='hive-receiver');body.acquired_control_turn_count=-1
        other=self.add(session.engine,'Grizzly Bears',seat='B',ref='hive-opponent')
        self.add(session.engine,'Muscle Sliver');self.add(session.engine,'Gemhide Sliver',seat='B')
        construct=self.add(session.engine,'Sliver Construct',seat='B');removal=self.add(session.engine,'Naturalize',zone='hand')
        self.assert_closed(session,giver);self.prepare(session,mana={'C':3});self.checkpoint(session)
        self.cast_for(session,'A',giver,reserve={'C':1});self.assertIsNone(self.resolve(session))
        self.assertEqual((3,3),self.stats(session,body));self.assertEqual((2,2),self.stats(session,other));self.assertEqual((3,3),self.stats(session,construct))
        self.assertTrue({'bear','sliver'}<=self.parts(session,body)[1]);self.assertNotIn('sliver',self.parts(session,other)[1])
        self.activate_mana(session,'A',body,'G');self.assertEqual(1,session.state.players['A'].mana_pool['G'])
        self.cast(session,'A',removal,[giver.ref]);self.assertIsNone(self.resolve(session));self.assertEqual((2,2),self.stats(session,body))
        self.assertFalse(session.engine._activated_abilities(session.state.cards[body.object_id]));self.assertNotIn('sliver',self.parts(session,body)[1]);self.replay(session,load=True)

    def test_actual_crusade_sets_black_adds_zombie_and_keeps_goblin_kindred_and_unrelated_creatures(self):
        session=self.session(205625030);giver=self.add(session.engine,"Dralnu's Crusade",zone='hand')
        goblin=self.add(session.engine,'Raging Goblin');other=self.add(session.engine,'Goblin Piker',seat='B')
        elf=self.add(session.engine,'Llanowar Elves');kindred=self.add(session.engine,'Boggart Shenanigans',seat='B')
        removal=self.add(session.engine,'Naturalize',zone='hand')
        self.assert_closed(session,giver);self.prepare(session,mana={'C':2,'B':1,'R':1,'G':1});self.checkpoint(session)
        self.cast_for(session,'A',giver,reserve={'C':1,'G':1});self.assertIsNone(self.resolve(session))
        self.assertEqual((2,2),self.stats(session,goblin));self.assertEqual((3,2),self.stats(session,other));self.assertEqual((1,1),self.stats(session,elf))
        for card in (goblin,other,kindred):
            self.assertTrue({'goblin','zombie'}<=self.parts(session,card)[1]);self.assertEqual(['B'],session.engine._effective_card_data(session.state.cards[card.object_id])['colors'])
        self.assertEqual({'kindred','enchantment'},self.parts(session,kindred)[0]);self.assertEqual(['G'],session.engine._effective_card_data(session.state.cards[elf.object_id])['colors'])
        self.cast(session,'A',removal,[giver.ref]);self.assertIsNone(self.resolve(session));self.assertEqual((1,1),self.stats(session,goblin));self.assertEqual(['R'],session.engine._effective_card_data(session.state.cards[goblin.object_id])['colors'])
        self.assertNotIn('zombie',self.parts(session,goblin)[1]);self.replay(session,load=True)

    def test_omitted_subtype_addition_mutant_is_killed_by_original_graaz(self):
        import quorune.continuous_effects as effects
        session=self.session(205625040);self.add(session.engine,'Graaz, Unstoppable Juggernaut');body=self.add(session.engine,'Grizzly Bears')
        def witness():self.assertIn('juggernaut',self.parts(session,body)[1])
        witness();original=effects._apply_operation
        def omit(state,operation,*args,**kwargs):
            if operation.op=='add_types' and operation.field=='subtypes':return None
            return original(state,operation,*args,**kwargs)
        with patch.object(effects,'_apply_operation',side_effect=omit),self.assertRaises(AssertionError):witness()
