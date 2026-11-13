"""Zero chosen targets differ from chosen targets becoming illegal (CR 608.2b)."""
from copy import deepcopy
from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients
from test_bound_effect_programs import record
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.target_set_result_sequences import (
    target_set_result_sequence_capabilities, target_set_result_sequence_runtime_dependencies,
)
from scripts.build_test_database import build_fixture_database

CAPABILITY = 'resolution.target_set.ordered_results'
TANDEM = 'Up to two target creatures each get +1/+2 until end of turn. You gain 2 life.'


class CharacteristicSetFollowupCompilerTests(unittest.TestCase):
    def test_characteristic_prefix_and_every_independent_leaf_keep_their_own_capabilities(self):
        registry = load_default_capability_registry()
        cases = ((TANDEM, 'life', 'life.change.effect'),
            ('Up to one target creature gets -2/-2 until end of turn. Amass Zombies 2.', 'amass', 'keyword_action.amass.fixed'),
            ('Up to two target creatures gain lifelink until end of turn. Create a Food token.', 'create_token', None),
            ('Up to two target creatures each get +1/+0 until end of turn. Draw a card.', 'draw', 'zone.draw.library_to_hand'),
            ('Up to two target creatures each get +1/+0 until end of turn. Scry 1.', 'scry', None))
        for text, operation, owner in cases:
            for prefix, kind in (('', 'Instant'), ('When this creature enters, ', 'Creature — Human'),
                                 ('{2}: ', 'Creature — Human')):
                ir = compile_oracle_card(record(prefix+text, type_line=kind), capability_registry=registry)
                self.assertEqual('exact', ir.status, (text, ir.material_residuals))
                node = ir.faces[0].nodes[0]
                self.assertEqual(operation, node.effects[-1]['op'])
                self.assertIn(CAPABILITY, node.capability_dependencies)
                if owner: self.assertIn(owner, node.capability_dependencies)

    def test_payload_minimum_survives_annotation_omission_and_rejects_unbound_or_malformed_tails(self):
        ir = compile_oracle_card(record(TANDEM), capability_registry=load_default_capability_registry())
        node = ir.faces[0].nodes[0]
        program = SimpleNamespace(effects=node.effects, target_schema=node.target_schema, coverage=())
        required = target_set_result_sequence_runtime_dependencies(program)
        self.assertIn(CAPABILITY, required)
        self.assertIn('life.change.effect', required)
        for effects, schema in (([{**node.effects[0], 'maximum_targets':1}, node.effects[1]], node.target_schema),
                ([node.effects[0], {**node.effects[1], 'player':'$target.0'}], node.target_schema),
                (node.effects, {**node.target_schema, 'count_parameter':{'parameter':'any_number'}})):
            self.assertFalse(target_set_result_sequence_capabilities(effects=effects, target_schema=schema, mechanic_ids=node.mechanics))
            with self.assertRaises(ValueError):
                target_set_result_sequence_runtime_dependencies(SimpleNamespace(effects=effects, target_schema=schema, coverage=()))
        for text in (TANDEM+' Rewrite history.',
                     'Up to two target creatures each get +1/+1 until end of turn. Target player draws a card.',
                     'Up to two target creatures each get +1/+1 until end of turn. If you control an artifact, those creatures also gain lifelink until end of turn.'):
            self.assertNotEqual('exact', compile_oracle_card(record(text), capability_registry=load_default_capability_registry()).status)

    def test_verified_sequence_withdrawal_keeps_the_independent_characteristic_leaf(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked', blockers=['sequence owner withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual('exact', compile_oracle_card(record(TANDEM), capability_registry=registry).status)
        self.assertEqual('exact', compile_oracle_card(record(TANDEM.split('. ')[0]+'.'), capability_registry=registry).status)


class CharacteristicSetFollowupNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipients.TargetRecipientNativeTests.reject
    finish = recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [row for row in base['cards'] if row['name']=='Generic Bound Plains']
        base['rulings'] = []
        (folder/'basic.json').write_text(json.dumps(base), encoding='utf-8')
        path = folder/'characteristic-followups.sqlite3'
        build_fixture_database([folder/'basic.json', ROOT/'tests/fixtures/characteristic-set-followup-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Characteristic follow-ups', [DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)], ['Sliver Hivelord'])

    def scene(self, name, *, count=0, before=None, land=False):
        s = self.session(6081010+len(name)+count)
        source = self.add(s.engine, name, zone='battlefield' if land else 'hand')
        bodies = [self.add(s.engine, 'Grizzly Bears', seat='B' if i==0 else 'C', ref='followup-body-'+str(i))
                  for i in range(count)]
        for body in bodies: body.acquired_control_turn_count=-1
        bounce = self.add(s.engine, 'Cloudshift', seat='B', zone='hand')
        s.state.players['B'].mana_pool['W']=1
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='followup-private-C')
        if before is not None: before(s, source, bodies)
        s.state.config.semantic_policy='trusted_only'
        self.assert_closed(s, source)
        self.prepare(s, mana=None if land else {'C':20, **{c:10 for c in 'WUBRG'}})
        self.checkpoint(s)
        return s, source, bodies, bounce, private

    def cast_checked(self, s, source, refs):
        self.priority(s, 'A')
        row=self.action(s, 'A', s.state.cards[source.object_id])
        command={'action_id':row['id'],'targets':list(refs),'pay':'auto'}
        self.reject(s, 'D', command)
        self.reject(s, 'A', {**command,'targets':['missing-followup-target']})
        result=s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)

    def tokens(self, s):
        return [c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']

    def assert_tail(self, s, name, *, happened):
        tokens=self.tokens(s)
        self.assertEqual(42 if name=='Tandem Tactics' and happened else 40, s.state.players['A'].life)
        if name=='Tandem Tactics': self.assertFalse(tokens)
        else:
            self.assertEqual(int(happened), len(tokens))
            if happened:
                token=tokens[0]
                self.assertEqual('A', token.controller)
                if name=='Bleeding Edge':
                    self.assertEqual(2, token.counters['+1/+1'])
                    self.assertEqual((2,2),tuple(s.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')))
                elif name=='Rat Out':
                    self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')))
                else: self.assertIn('Food', s.engine._effective_card_data(token)['type_line'])

    def zero_spell(self, name, *, finish=True):
        s, source, bodies, bounce, private=self.scene(name)
        self.cast_checked(s, source, [])
        s=self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assert_tail(s, name, happened=True)
        if finish: self.finish(s, private)

    def test_actual_zero_targets_without_any_eligible_creatures_keep_all_four_independent_results(self):
        for name in ('Tandem Tactics','Second Breakfast','Rat Out','Bleeding Edge'):
            with self.subTest(name=name): self.zero_spell(name)

    def test_actual_all_chosen_targets_becoming_illegal_stops_each_spell_and_its_independent_result(self):
        for name in ('Tandem Tactics','Second Breakfast','Rat Out','Bleeding Edge'):
            with self.subTest(name=name):
                s, source, bodies, bounce, private=self.scene(name, count=1)
                self.cast_checked(s, source, [bodies[0].ref])
                self.cast(s, 'B', bounce, [bodies[0].ref])
                self.resolve_top(s)
                s=self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assert_tail(s, name, happened=False)
                self.assertEqual((2,2),tuple(s.engine._numeric_stat(bodies[0].object_id,k) for k in ('power','toughness')))
                self.finish(s, private)

    def test_actual_two_targets_and_partial_invalidation_keep_one_life_result_then_cleanup_each_modifier(self):
        for blink in (False,True):
            with self.subTest(blink=blink):
                s, source, bodies, bounce, private=self.scene('Tandem Tactics', count=2)
                self.cast_checked(s, source, [c.ref for c in bodies])
                self.reject(s, 'A', {'action_id':'cast:'+source.ref,'targets':[bodies[0].ref]*2,'pay':'auto'})
                if blink:
                    self.cast(s, 'B', bounce, [bodies[0].ref])
                    self.resolve_top(s)
                s=self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual(42, s.state.players['A'].life)
                for i,body in enumerate(bodies):
                    self.assertEqual((2,2) if blink and i==0 else (3,4),
                        tuple(s.engine._numeric_stat(body.object_id,k) for k in ('power','toughness')))
                self.until(s, lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
                for body in bodies:self.assertEqual((2,2),tuple(s.engine._numeric_stat(body.object_id,k) for k in ('power','toughness')))
                self.finish(s, private)

    def test_actual_bleeding_edge_keeps_the_warden_until_resolution_finishes_and_does_not_weaken_the_new_army(self):
        extra={}
        def before(s,source,bodies):extra['warden']=self.add(s.engine,'Soul Warden')
        s, source, bodies, bounce, private=self.scene('Bleeding Edge', before=before)
        self.cast_checked(s, source, [extra['warden'].ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[extra['warden'].object_id].zone)
        self.assertEqual(41, s.state.players['A'].life)
        army,=self.tokens(s)
        self.assertEqual((2,2),tuple(s.engine._numeric_stat(army.object_id,k) for k in ('power','toughness')))
        self.finish(s, private)

    def test_actual_created_food_retains_its_normal_paid_life_ability(self):
        s, source, bodies, bounce, private=self.scene('Second Breakfast')
        self.cast_checked(s, source, [])
        self.assertIsNone(self.resolve(s))
        food,=self.tokens(s)
        self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                 if r['id'].startswith('activate:'+food.ref+':'))
        command={'action_id':row['id'],'pay':'auto'}
        self.reject(s,'D',command)
        result=s.act('pilot:A',command)
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(43,s.state.players['A'].life)
        self.assertEqual('outside',s.state.cards[food.object_id].zone)
        self.finish(s,private)

    def test_actual_opposing_spell_copy_preserves_target_count_and_gains_life_for_its_own_controller(self):
        extra={}
        def before(s,source,bodies):
            extra['copy']=self.add(s.engine,'Reverberate',seat='B',zone='hand')
            s.state.players['B'].mana_pool['R']=2
            extra['targets']=[self.add(s.engine,'Grizzly Bears',seat=seat,ref='followup-copy-'+seat) for seat in 'AD']
        s,source,bodies,bounce,private=self.scene('Tandem Tactics',count=2,before=before)
        self.assert_closed(s,extra['copy'])
        self.cast_checked(s,source,[c.ref for c in bodies])
        self.cast(s,'B',extra['copy'],[s.state.stack[-1].ref])
        self.assertIsNotNone(self.resolve(s))
        command={'action_id':'choose','targets':[c.ref for c in extra['targets']]}
        self.reject(s,'B',{**command,'targets':command['targets'][:1]})
        self.reject(s,'A',command)
        s=self.replay(s,load=True)
        result=s.act('pilot:B',command)
        self.assertTrue(result.ok,result.summary)
        self.resolve_top(s)
        self.assertEqual(40,s.state.players['A'].life)
        self.assertEqual(42,s.state.players['B'].life)
        for card in extra['targets']:
            self.assertEqual((3,4),tuple(s.engine._numeric_stat(card.object_id,k) for k in ('power','toughness')))
        for card in bodies:
            self.assertEqual((2,2),tuple(s.engine._numeric_stat(card.object_id,k) for k in ('power','toughness')))
        self.assertIsNone(self.resolve(s))
        self.assertEqual(42,s.state.players['A'].life)
        self.assertEqual(42,s.state.players['B'].life)
        self.finish(s,private)

    def test_actual_created_rat_survives_cleanup_and_cannot_block_an_actual_attacker(self):
        def before(s,source,bodies):self.add(s.engine,'Grizzly Bears',ref='followup-legal-blocker')
        s, source, bodies, bounce, private=self.scene('Rat Out',count=1,before=before)
        self.cast_checked(s,source,[bodies[0].ref])
        self.assertIsNone(self.resolve(s))
        rat,=self.tokens(s)
        self.assertEqual((1,1),tuple(s.engine._numeric_stat(rat.object_id,k) for k in ('power','toughness')))
        self.until(s,lambda game:game.state.active_player=='B' and game.state.pending_decision.kind=='combat.attackers')
        result=s.act('pilot:B',{'action_id':'attack','attackers':{bodies[0].ref:'A'}})
        self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:game.state.pending_decision.kind=='combat.blockers' and game.state.pending_decision.actors==['A'])
        self.reject(s,'A',{'action_id':'block','blocks':{rat.ref:bodies[0].ref}})
        result=s.act('pilot:A',{'action_id':'block','blocks':{}})
        self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual(38,s.state.players['A'].life)
        self.finish(s,private)

    def land_scene(self, *, extra=None):
        payers=[]
        def before(s,source,bodies):
            source.owner='D'
            for i,name in enumerate(('Forest','Forest','Forest','Swamp','Swamp')):
                payers.append(self.add(s.engine,name,ref='followup-payer-'+str(i)))
            if extra is not None:extra(s,source)
        s,source,bodies,bounce,private=self.scene('Skemfar Elderhall',land=True,before=before)
        row,=(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
              if r['id'].startswith('activate:'+source.ref+':') and not r.get('mana_ability'))
        return s,source,payers,private,{'action_id':row['id'],'targets':[],'pay':'auto'}

    def test_actual_elderhall_pays_the_source_and_five_mana_then_resolves_without_targets_after_source_departure(self):
        s,source,payers,private,command=self.land_scene()
        self.reject(s,'D',command)
        result=s.act('pilot:A',command)
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertIn(source.object_id,s.state.players['D'].zones['graveyard'])
        self.assertTrue(all(s.state.cards[c.object_id].tapped for c in payers))
        s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(2,len(self.tokens(s)))
        for token in self.tokens(s):self.assertEqual('A',token.controller)
        self.finish(s,private)

    def test_actual_elderhall_does_not_offer_its_sorcery_ability_off_turn_despite_available_mana(self):
        s,source,payers,private,command=self.land_scene()
        self.until(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
        self.priority(s,'A')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertNotIn(command['action_id'],[row['id'] for row in actions])
        self.assertTrue(all(not s.state.cards[c.object_id].tapped for c in payers))
        self.reject(s,'A',command)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.finish(s,private)

    def test_actual_elderhall_excludes_a_controlled_creature_and_creates_its_elves_after_the_opposing_modifier(self):
        creatures={}
        def extra(s,source):
            creatures['own']=self.add(s.engine,'Grizzly Bears',ref='followup-land-own')
            creatures['foreign']=self.add(s.engine,'Grizzly Bears',seat='B',ref='followup-land-foreign')
        s,source,payers,private,command=self.land_scene(extra=extra)
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id']==command['action_id'])
        self.assertEqual([creatures['foreign'].ref],row['target_schema']['legal_refs'])
        self.reject(s,'A',{**command,'targets':[creatures['own'].ref]})
        self.assertTrue(all(not s.state.cards[c.object_id].tapped for c in payers))
        result=s.act('pilot:A',{**command,'targets':[creatures['foreign'].ref]})
        self.assertTrue(result.ok,result.summary)
        s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[creatures['foreign'].object_id].zone)
        self.assertEqual((2,2),tuple(s.engine._numeric_stat(creatures['own'].object_id,k) for k in ('power','toughness')))
        self.assertEqual(2,len(self.tokens(s)))
        self.finish(s,private)

    def test_actual_zero_target_omission_mutant_is_killed_by_the_missing_life_followup(self):
        from quorune.effect_runtime import life_effects
        def omitted(host,effect,*,actor,operation,reason):return host.state.players[actor].life
        with patch.dict(life_effects.HANDLERS,{'life':omitted}),self.assertRaisesRegex(AssertionError,'42 != 40'):
            self.zero_spell('Tandem Tactics',finish=False)
