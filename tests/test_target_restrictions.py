from __future__ import annotations

"""Independent CR115/608 source object and targeting restriction contracts."""

import json,unittest
from types import SimpleNamespace
from common import ROOT
from quorune.model import CardInstance,StackItem
from quorune.target_source import (TargetingSource,TargetingSourceBinding,TARGETING_SOURCE_CONTEXT,
    capture_targeting_source,stack_targeting_source,pin_targeting_source_departures)
from quorune.target_restrictions import TargetRestrictionSpec
from quorune.compiler.target_restriction_nodes import target_restriction_specs


class TargetRestrictionModelTests(unittest.TestCase):
    def test_public_zone_target_shape_retains_mechanics_across_sibling_probes(self):
        from dataclasses import replace
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.rules.public_zone_move_capability_shapes import fixed_public_zone_move_set_node_capabilities
        record=replace(query_record("{T}, Sacrifice this artifact: Exile target player's graveyard."),type_line='Artifact')
        ir=compile_oracle_card(record,capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status)
        node,=ir.faces[0].nodes
        for mechanics in (node.mechanics,iter(node.mechanics)):
            self.assertEqual(('zone.move.fixed_public_set','target.revalidate_resolution'),
                fixed_public_zone_move_set_node_capabilities(effects=node.effects,target_schema=node.target_schema,mechanic_ids=mechanics))
        self.assertFalse(fixed_public_zone_move_set_node_capabilities(effects=(*node.effects,{'op':'draw','player':'B','count':1}),
            target_schema=node.target_schema,mechanic_ids=iter(node.mechanics)))

    def test_shared_opponent_clause_covers_both_kinds_and_spell_static_sources_stay_residual(self):
        from dataclasses import replace
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        specs=target_restriction_specs("Enchanted creature can't be the target of spells or abilities your opponents control.",source_name='Generic Aura')
        for kind in ('spell','ability'):
            for actor in 'ABCD':
                facts=TargetingSource(kind,frozenset({'G'}),frozenset({'creature'}),frozenset(),'B')
                self.assertEqual(actor!='A',any(spec.blocks(facts,acting_controller=actor,observer_controller='A') for spec in specs))
        for kind in ('Instant','Sorcery'):
            record=replace(query_record("Creatures can't be the targets of spells."),type_line=kind)
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=load_default_capability_registry()).status)

    def test_source_binding_is_closed_and_immutable(self):
        binding=TargetingSourceBinding('object','REF','object@0','battlefield','A','Creature — Wizard',('R',))
        raw=binding.to_dict();self.assertEqual(binding,TargetingSourceBinding.from_dict(json.loads(json.dumps(raw))))
        for change in ({'schema_version':True},{'object_id':[]},{'controller':''},{'colors':['R','R']},{'zone':'outside'},{'extra':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):TargetingSourceBinding.from_dict({**raw,**change})
        for change in ({'colors':frozenset({'red'})},{'card_types':['artifact']},{'source_controller':False}):
            with self.assertRaises(ValueError):TargetingSource('ability',**change)

    def test_spell_ability_source_and_acting_controller_predicates_are_distinct(self):
        thrun=target_restriction_specs("Thrun can't be the target of nongreen spells your opponents control or abilities from nongreen sources your opponents control.",source_name='Thrun, Breaker of Silence')
        shanna=target_restriction_specs("Shanna can't be the target of abilities your opponents control.",source_name="Shanna, Sisay's Legacy")
        self.assertIsNotNone(thrun);self.assertEqual(2,len(thrun));self.assertIsNotNone(shanna)
        for actor in 'ABCD':
            for source_controller in 'ABCD':
                for colors in (frozenset(),frozenset({'R'}),frozenset({'G'}),frozenset({'G','U'})):
                    ability=TargetingSource('ability',colors,frozenset({'creature'}),frozenset(),source_controller)
                    spell=TargetingSource('spell',colors,frozenset({'instant'}),frozenset(),actor)
                    with self.subTest(actor=actor,source=source_controller,colors=colors):
                        self.assertEqual('G' not in colors and source_controller!='A',any(s.blocks(ability,acting_controller=actor,observer_controller='A') for s in thrun))
                        self.assertEqual('G' not in colors and actor!='A',any(s.blocks(spell,acting_controller=actor,observer_controller='A') for s in thrun))
                        self.assertEqual(actor!='A',shanna[0].blocks(ability,acting_controller=actor,observer_controller='A'))
                        self.assertFalse(shanna[0].blocks(spell,acting_controller=actor,observer_controller='A'))

    def test_current_source_changes_and_immediate_departure_lki_do_not_use_returned_object(self):
        source=CardInstance('source','SOURCE','oracle','Generic Wizard','A','A','battlefield')
        characteristics={'type_line':'Creature — Wizard','colors':['U']}
        host=SimpleNamespace(state=SimpleNamespace(cards={source.object_id:source},stack=[],pending_trigger_batches=[]),
            _effective_card_data=lambda card:characteristics)
        binding=capture_targeting_source(host,source)
        item=StackItem('stack','STACK','activated_ability','B','Ability',source_object_id=source.object_id,
            context={'source_logical_object_id':source.logical_object_id,TARGETING_SOURCE_CONTEXT:binding.to_dict()})
        host.state.stack.append(item)
        self.assertEqual(frozenset({'U'}),stack_targeting_source(host,item).colors)
        source.controller='C';characteristics['colors']=['R'];characteristics['type_line']='Artifact Creature — Wizard'
        self.assertEqual('C',stack_targeting_source(host,item).source_controller)
        pin_targeting_source_departures(host,[source])
        source.zone='graveyard';source.zone_change_counter+=1;source.controller='A';characteristics['colors']=['G'];characteristics['type_line']='Creature — Wizard'
        source.zone='battlefield';source.zone_change_counter+=1
        facts=stack_targeting_source(host,item)
        self.assertEqual((frozenset({'R'}),'C'),(facts.colors,facts.source_controller))
        self.assertIn('artifact',facts.card_types)
        self.assertEqual('B',item.controller)

    def test_source_with_no_types_is_known_empty_and_missing_characteristics_reject(self):
        source=CardInstance('emblem','EMBLEM','emblem-oracle','Generic emblem','A','A','command',object_kind='emblem')
        host=SimpleNamespace(_effective_card_data=lambda card:{'type_line':'','colors':[]})
        binding=capture_targeting_source(host,source)
        self.assertEqual(binding,TargetingSourceBinding.from_dict(json.loads(json.dumps(binding.to_dict()))))
        self.assertEqual(frozenset(),binding.facts().card_types)
        self.assertEqual(frozenset(),binding.facts().colors)
        self.assertIsNone(TargetingSource('ability').card_types)
        self.assertEqual('A',binding.facts().source_controller)
        for value in ({},{'type_line':None},{'type_line':False},{'type_line':[]}):
            with self.subTest(value=value),self.assertRaises(ValueError):
                capture_targeting_source(host,source,characteristics=value)

    def test_missing_source_information_is_not_falsely_colorless_and_malformed_binding_rejects(self):
        host=SimpleNamespace(state=SimpleNamespace(cards={}))
        item=StackItem('stack','STACK','activated_ability','B','Ability',source_object_id='departed')
        facts=stack_targeting_source(host,item);self.assertIsNone(facts.colors)
        red=TargetRestrictionSpec('source',('ability',),colors_any=('R',))
        self.assertTrue(red.blocks(facts,acting_controller='B',observer_controller='A'))
        item.context[TARGETING_SOURCE_CONTEXT]={'wrong':True}
        from quorune.errors import GameRuleError
        with self.assertRaises(GameRuleError):stack_targeting_source(host,item)
        copied=StackItem('copy','COPY','spell_copy','B','Copy',context={'copy_permanent_characteristics':{'type_line':'Instant','colors':['U']}})
        self.assertEqual('spell',stack_targeting_source(host,copied).kind)
        copied.kind='unsupported_stack_object'
        with self.assertRaises(GameRuleError):stack_targeting_source(host,copied)

    def test_compiler_and_dependency_omissions_keep_material_restrictions_residual(self):
        from copy import deepcopy
        from unittest.mock import patch
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from test_qualified_zone_event_queries import query_record
        from quorune.target_restrictions import TARGET_RESTRICTION_CAPABILITY
        record=query_record("Creatures can't be the targets of spells.")
        registry=load_default_capability_registry()
        self.assertEqual('exact',compile_oracle_card(record,capability_registry=registry).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (TARGET_RESTRICTION_CAPABILITY,'target.revalidate_resolution'):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==capability).update(status='blocked',blockers=['withdrawn target authority'])
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=CapabilityRegistry(changed)).status)
        with patch('quorune.compiler.target_restriction_nodes.target_restriction_node',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry).status)

    def test_omitting_source_prohibition_is_killed_by_controller_and_quality_contract(self):
        from unittest.mock import patch
        method='test_spell_ability_source_and_acting_controller_predicates_are_distinct'
        with patch.object(TargetRestrictionSpec,'blocks',return_value=False),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()

    def test_fixed_grammar_rejects_open_or_ambiguous_siblings_and_unknown_fields(self):
        for text in ("This creature can't be the target of spells that can target only Walls.",
                "This creature can't be the target of spells unless it attacked or blocked this turn.",
                "This creature can't be the target of red spells and draw a card."):
            self.assertIsNone(target_restriction_specs(text,source_name='Generic Witness'))
        raw=TargetRestrictionSpec('source',('spell',),colors_none=('G',)).to_dict()
        for patch in ({'schema_version':True},{'kinds':['other']},{'colors_none':['G','G']},{'extra':1}):
            with self.assertRaises(ValueError):TargetRestrictionSpec.from_dict({**raw,**patch})
        from quorune.semantic_runtime.target_restrictions import TargetRestrictionHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        handler=TargetRestrictionHandler()
        descriptor={'handler_id':handler.handler_id,'schema_version':1,'event':handler.event,'restrictions':[raw]}
        self.assertEqual(1,len(handler.validate(descriptor)))
        for changes in ({'schema_version':True},{'event':'zone.change'},{'restrictions':[]},{'extra':True},
                {'restrictions':[{**raw,'controller_relation':'source_owner'}]}):
            with self.assertRaises(SemanticNodeError):handler.validate({**descriptor,**changes})


import test_fixed_reveal_additional_costs as reveal


ORIGINAL_NAMES=("Gaea's Revenge", 'Fiendslayer Paladin', "Shanna, Sisay's Legacy", 'Thrun, Breaker of Silence', 'Spellbane Centaur', 'Artifact Ward', 'Bartel Runeaxe', "Suq'Ata Firewalker", 'Karplusan Strider', 'Dense Foliage', 'Ground Seal', 'Spectral Shield', 'Canopy Cover', 'Shielding Plax')


class TargetRestrictionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=reveal.FixedRevealCostRuntimeTests.session
    add=reveal.FixedRevealCostRuntimeTests.add
    fund=reveal.FixedRevealCostRuntimeTests.fund
    checkpoint=reveal.FixedRevealCostRuntimeTests.checkpoint
    resolve=reveal.FixedRevealCostRuntimeTests.resolve
    replay=reveal.FixedRevealCostRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'target-restrictions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/target-restriction-cards.json',ROOT/'tests/fixtures/scoped-damage-prevention-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Target restriction witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def actions(self,session,actor='A'):
        packet=session.packet('pilot:'+actor,full=True)
        return packet['decision']['ctx']['legal']['actions']

    def test_actual_dense_foliage_filters_spells_but_allows_native_ability_private_rollback_and_replay(self):
        from quorune.record import authoritative_state_hash
        s=self.session(1151901);e=s.engine
        self.add(e,'Dense Foliage');body=self.add(e,'Generic Bound Body',seat='B')
        source=self.add(e,"Suq'Ata Firewalker");source.acquired_control_turn_count=-1
        shock=self.add(e,'Shock',zone='hand')
        private=self.add(e,'Generic Bound Body',seat='B',zone='hand',ref='private-target-restriction-hand')
        self.fund(s,{'R':1})
        action=next(a for a in self.actions(s) if a.get('card')==shock.ref)
        self.assertNotIn(body.ref,action['target_schema']['legal_refs'])
        for principal in ('pilot:B','pilot:A'):
            before=authoritative_state_hash(s.state)
            denied=s.act(principal,{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
            self.assertFalse(denied.ok,denied.summary);self.assertEqual(before,authoritative_state_hash(s.state))
        for viewer in 'ACD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        s=self.replay(s,load=True)
        ability=next(a for a in self.actions(s) if a['id'].startswith('activate:'+source.ref+':'))
        self.assertIn(body.ref,ability['target_schema']['legal_refs'])
        accepted=s.act('pilot:A',{'action_id':ability['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[body.object_id].marked_damage)
        self.assertEqual('hand',s.state.cards[shock.object_id].zone)
        self.replay(s,load=True)

    def test_all_selected_originals_have_complete_admission_with_source_specific_restrictions(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        cards=json.loads((ROOT/'tests/fixtures/target-restriction-cards.json').read_text(encoding='utf-8'))['cards']
        for raw in cards:
            if raw['name'] not in ORIGINAL_NAMES:continue
            card=self.db.by_oracle_id(raw['oracle_id'])
            program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),
                capability_registry=self.registry,capability_profile='commander_review')
            with self.subTest(card=card.name):self.assertTrue(bind_card_program_runtime(program,
                capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])


    def pass_to(self,session,actor):
        for _ in range(8):
            if 'pilot:'+actor in session.pending_principals():return
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.fail('Expected principal did not receive priority')

    def cast(self,session,card,targets,actor='A'):
        self.pass_to(session,actor)
        offers=[a for a in self.actions(session,actor) if a.get('card')==card.ref]
        self.assertTrue(offers,{'card':card.printed_name,'mana':dict(session.state.players[actor].mana_pool),'actions':[(a['id'],a.get('card')) for a in self.actions(session,actor)]})
        action=offers[0]
        result=session.act('pilot:'+actor,{'action_id':action['id'],'targets':list(targets),'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def resolve_top(self,session):
        count=len(session.state.stack)
        for _ in range(16):
            if len(session.state.stack)<count:return
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.fail('Top stack object did not resolve')

    def test_native_thrun_rechecks_source_controller_instead_of_locked_ability_controller(self):
        s=self.session(1151902);e=s.engine
        target=self.add(e,'Thrun, Breaker of Silence')
        source=self.add(e,'Prodigal Sorcerer');source.acquired_control_turn_count=-1
        seize=self.add(e,'Word of Seizing',seat='B',zone='hand')
        e.state.players['B'].mana_pool.update({'R':2,'C':3});self.fund(s,{})
        ability=next(a for a in self.actions(s) if a['id'].startswith('activate:'+source.ref+':'))
        self.assertIn(target.ref,ability['target_schema']['legal_refs'])
        accepted=s.act('pilot:A',{'action_id':ability['id'],'targets':[target.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.cast(s,seize,[source.ref],actor='B');s=self.replay(s,load=True)
        self.resolve_top(s);self.assertEqual('B',s.state.cards[source.object_id].controller)
        self.assertEqual('A',s.state.stack[-1].controller)
        self.resolve_top(s);self.assertEqual(0,s.state.cards[target.object_id].marked_damage)
        self.assertFalse(s.state.stack)
        self.replay(s,load=True)

    def test_native_gaea_uses_blue_departure_lki_after_green_source_returns_to_printed_hand(self):
        s=self.session(1151903);e=s.engine
        target=self.add(e,"Gaea's Revenge")
        source=self.add(e,'Generic Targeting Green Pinger',seat='B');source.acquired_control_turn_count=-1
        frog=self.add(e,'Turn to Frog',zone='hand');bounce=self.add(e,'Unsummon',zone='hand')
        e.state.players['A'].mana_pool.update({'U':4,'C':3});self.fund(s,{},actor='B')
        ability=next(a for a in self.actions(s,'B') if a['id'].startswith('activate:'+source.ref+':'))
        self.assertIn(target.ref,ability['target_schema']['legal_refs'])
        accepted=s.act('pilot:B',{'action_id':ability['id'],'targets':[target.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.cast(s,frog,[source.ref]);self.resolve_top(s)
        self.assertEqual(['U'],s.engine._effective_card_data(s.state.cards[source.object_id])['colors'])
        self.cast(s,bounce,[source.ref]);self.resolve_top(s)
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        binding=TargetingSourceBinding.from_dict(s.state.stack[-1].context[TARGETING_SOURCE_CONTEXT])
        self.assertEqual(('U',),binding.colors)
        self.assertEqual(['G'],s.engine._effective_card_data(s.state.cards[source.object_id])['colors'])
        s=self.replay(s,load=True);self.resolve_top(s)
        self.assertEqual(0,s.state.cards[target.object_id].marked_damage)
        self.replay(s,load=True)

    def test_native_thrun_allows_locked_opponent_ability_from_now_owned_nongreen_source(self):
        s=self.session(1151904);e=s.engine
        target=self.add(e,'Thrun, Breaker of Silence')
        source=self.add(e,'Generic Targeting Green Pinger',seat='B');source.acquired_control_turn_count=-1
        seize=self.add(e,'Word of Seizing',zone='hand');frog=self.add(e,'Turn to Frog',zone='hand')
        bounce=self.add(e,'Unsummon',zone='hand')
        e.state.players['A'].mana_pool.update({'R':2,'C':5,'U':4});self.fund(s,{},actor='B')
        ability=next(a for a in self.actions(s,'B') if a['id'].startswith('activate:'+source.ref+':'))
        accepted=s.act('pilot:B',{'action_id':ability['id'],'targets':[target.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.cast(s,seize,[source.ref]);self.resolve_top(s)
        self.assertEqual('A',s.state.cards[source.object_id].controller)
        self.cast(s,frog,[source.ref]);self.resolve_top(s)
        self.assertEqual('B',s.state.stack[-1].controller)
        self.assertEqual(['U'],s.engine._effective_card_data(s.state.cards[source.object_id])['colors'])
        self.cast(s,bounce,[source.ref]);self.resolve_top(s)
        binding=TargetingSourceBinding.from_dict(s.state.stack[-1].context[TARGETING_SOURCE_CONTEXT])
        self.assertEqual('A',binding.controller);self.assertEqual('B',s.state.cards[source.object_id].owner)
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.resolve_top(s);self.assertEqual(1,s.state.cards[target.object_id].marked_damage)
        self.replay(s,load=True)


    def test_original_self_color_restrictions_keep_green_or_other_color_actions_and_whole_bodies(self):
        from quorune.record import authoritative_state_hash
        cases=[("Gaea's Revenge",'A','Murder','Giant Growth'),('Karplusan Strider','A','Murder','Shock'),
            ("Suq'Ata Firewalker",'A','Shock','Giant Growth'),('Fiendslayer Paladin','B','Murder','Giant Growth'),
            ('Thrun, Breaker of Silence','B','Murder','Giant Growth')]
        for index,(name,actor,denied_name,allowed_name) in enumerate(cases):
            with self.subTest(card=name):
                s=self.session(1151920+index);e=s.engine
                target=self.add(e,name);self.add(e,'Generic Bound Body',seat='C',ref='fallback-target')
                denied_spell=self.add(e,denied_name,seat=actor,zone='hand',ref='denied-spell')
                allowed_spell=self.add(e,allowed_name,seat=actor,zone='hand',ref='allowed-spell')
                power=e._numeric_stat(target.object_id,'power')
                self.fund(s,{'B':6,'R':6,'G':6,'C':8},actor=actor)
                offer=next(a for a in self.actions(s,actor) if a.get('card')==denied_spell.ref)
                self.assertNotIn(target.ref,offer['target_schema']['legal_refs'])
                before=authoritative_state_hash(s.state)
                result=s.act('pilot:'+actor,{'action_id':offer['id'],'targets':[target.ref],'pay':'auto'})
                self.assertFalse(result.ok,result.summary);self.assertEqual(before,authoritative_state_hash(s.state))
                if name=='Fiendslayer Paladin':self.assertLessEqual({'first strike','lifelink'},e._combat_keywords(target))
                if name=='Thrun, Breaker of Silence':self.assertLessEqual({'trample','indestructible'},e._combat_keywords(target))
                self.cast(s,allowed_spell,[target.ref],actor=actor);self.assertIsNone(self.resolve(s))
                if allowed_name=='Shock':self.assertEqual(2,s.state.cards[target.object_id].marked_damage)
                else:self.assertEqual(power+3,s.engine._numeric_stat(target.object_id,'power'))
                self.replay(s,load=True)

    def test_original_shanna_rejects_opponent_abilities_but_allows_opponent_spells(self):
        s=self.session(1151930);e=s.engine
        target=self.add(e,"Shanna, Sisay's Legacy");self.add(e,'Generic Bound Body')
        fallback=self.add(e,'Generic Bound Body',seat='C',ref='shanna-fallback')
        source=self.add(e,'Prodigal Sorcerer',seat='B');source.acquired_control_turn_count=-1
        murder=self.add(e,'Murder',seat='B',zone='hand');self.fund(s,{'B':6},actor='B')
        self.assertEqual(2,e._numeric_stat(target.object_id,'power'))
        ability=next(a for a in self.actions(s,'B') if a['id'].startswith('activate:'+source.ref+':'))
        self.assertNotIn(target.ref,ability['target_schema']['legal_refs']);self.assertIn(fallback.ref,ability['target_schema']['legal_refs'])
        spell=next(a for a in self.actions(s,'B') if a.get('card')==murder.ref)
        self.assertIn(target.ref,spell['target_schema']['legal_refs'])
        self.cast(s,murder,[target.ref],actor='B');self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[target.object_id].zone);self.replay(s,load=True)

    def test_original_spellbane_protects_current_controlled_creatures_from_blue_only(self):
        s=self.session(1151931);e=s.engine
        source=self.add(e,'Spellbane Centaur');own=self.add(e,'Generic Bound Body')
        other=self.add(e,'Generic Bound Body',seat='B',ref='spellbane-other')
        bounce=self.add(e,'Unsummon',zone='hand');self.fund(s,{'U':3})
        offer=next(a for a in self.actions(s) if a.get('card')==bounce.ref)
        for card in (source,own):self.assertNotIn(card.ref,offer['target_schema']['legal_refs'])
        self.assertIn(other.ref,offer['target_schema']['legal_refs'])
        self.cast(s,bounce,[other.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[other.object_id].zone);self.replay(s,load=True)

    def test_original_auras_preserve_stats_and_distinguish_spell_from_artifact_source_abilities(self):
        for index,name in enumerate(('Artifact Ward','Spectral Shield')):
            with self.subTest(aura=name):
                s=self.session(1151932+index);e=s.engine
                body=self.add(e,'Generic Bound Body');other=self.add(e,'Generic Bound Body',seat='B',ref='aura-other')
                aura=self.add(e,name,zone='hand');blue=self.add(e,'Prodigal Sorcerer');blue.acquired_control_turn_count=-1
                rod=self.add(e,'Rod of Ruin');murder=self.add(e,'Murder',zone='hand')
                toughness=e._numeric_stat(body.object_id,'toughness');self.fund(s,{'W':5,'U':5,'B':5,'C':10})
                self.cast(s,aura,[body.ref]);self.assertIsNone(self.resolve(s))
                self.assertEqual(body.object_id,s.state.cards[aura.object_id].attached_to)
                self.assertEqual(toughness+(2 if name=='Spectral Shield' else 0),s.engine._numeric_stat(body.object_id,'toughness'))
                ability=next(a for a in self.actions(s) if a['id'].startswith('activate:'+rod.ref+':'))
                spell=next(a for a in self.actions(s) if a.get('card')==murder.ref)
                if name=='Artifact Ward':
                    self.assertNotIn(body.ref,ability['target_schema']['legal_refs'])
                    self.assertIn(body.ref,spell['target_schema']['legal_refs'])
                    accepted=s.act('pilot:A',{'action_id':ability['id'],'targets':[other.ref],'pay':'auto'})
                    self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
                    self.assertEqual(1,s.state.cards[other.object_id].marked_damage)
                else:
                    self.assertIn(body.ref,ability['target_schema']['legal_refs'])
                    self.assertNotIn(body.ref,spell['target_schema']['legal_refs'])
                blue_ability=next(a for a in self.actions(s) if a['id'].startswith('activate:'+blue.ref+':'))
                accepted=s.act('pilot:A',{'action_id':blue_ability['id'],'targets':[body.ref]})
                self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
                self.assertEqual(1,s.state.cards[body.object_id].marked_damage);self.replay(s,load=True)

    def test_original_bartel_rejects_aura_spells_but_remains_a_legal_ordinary_spell_target(self):
        s=self.session(1151934);e=s.engine
        target=self.add(e,'Bartel Runeaxe');self.add(e,'Generic Bound Body',seat='B',ref='bartel-fallback')
        pacifism=self.add(e,'Pacifism',zone='hand');shock=self.add(e,'Shock',zone='hand')
        self.fund(s,{'W':4,'R':3,'C':3})
        aura=next(a for a in self.actions(s) if a.get('card')==pacifism.ref)
        self.assertNotIn(target.ref,aura['target_schema']['legal_refs'])
        self.assertIn('vigilance',e._combat_keywords(target))
        self.cast(s,shock,[target.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,s.state.cards[target.object_id].marked_damage);self.replay(s,load=True)

    def test_original_ground_seal_blocks_graveyard_card_targets_but_not_targeted_player_exile(self):
        s=self.session(1151935);e=s.engine
        seal=self.add(e,'Ground Seal',zone='hand');card=self.add(e,'Generic Bound Body',zone='graveyard',ref='seal-own-grave')
        other=self.add(e,'Generic Bound Body',seat='B',zone='graveyard',ref='seal-other-grave')
        crypt=self.add(e,"Tormod's Crypt");raise_dead=self.add(e,'Raise Dead',zone='hand')
        self.fund(s,{'G':4,'B':4,'C':5})
        initial_hand=set(e.state.players['A'].zones['hand']);initial=len(initial_hand)
        self.cast(s,seal,[]);self.assertIsNone(self.resolve(s))
        self.assertEqual(initial,len(s.state.players['A'].zones['hand']))
        drawn_ids=set(s.state.players['A'].zones['hand'])-initial_hand
        self.assertEqual(1,len(drawn_ids));drawn=s.state.cards[next(iter(drawn_ids))]
        self.assertIn(drawn.ref,json.dumps(s.packet('pilot:A',full=True)))
        for viewer in 'BCD':self.assertNotIn(drawn.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.assertFalse(any(a.get('card')==raise_dead.ref for a in self.actions(s)))
        ability=next(a for a in self.actions(s) if a['id'].startswith('activate:'+crypt.ref+':'))
        self.assertIn('B',ability['target_schema']['legal_refs'])
        accepted=s.act('pilot:A',{'action_id':ability['id'],'targets':['B']})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('exile',s.state.cards[other.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[crypt.object_id].zone)
        self.replay(s,load=True)


    def test_restriction_entering_after_cast_revalidates_and_humility_removes_intrinsic_prohibition(self):
        s=self.session(1151940);e=s.engine
        self.add(e,'Leyline of Anticipation');body=self.add(e,'Generic Bound Body')
        dense=self.add(e,'Dense Foliage',zone='hand');murder=self.add(e,'Murder',seat='B',zone='hand')
        e.state.players['A'].mana_pool.update({'G':3,'C':4});self.fund(s,{'B':5},actor='B')
        self.cast(s,murder,[body.ref],actor='B');self.cast(s,dense,[]);self.resolve_top(s)
        s=self.replay(s,load=True);self.resolve_top(s)
        self.assertEqual('battlefield',s.state.cards[body.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[murder.object_id].zone);self.replay(s,load=True)
        s=self.session(1151941);e=s.engine
        target=self.add(e,"Gaea's Revenge");self.add(e,'Generic Bound Body',seat='B',ref='humility-fallback')
        humility=self.add(e,'Humility',zone='hand');murder=self.add(e,'Murder',zone='hand')
        self.fund(s,{'W':6,'B':6,'C':8})
        initial=next(a for a in self.actions(s) if a.get('card')==murder.ref)
        self.assertNotIn(target.ref,initial['target_schema']['legal_refs'])
        self.cast(s,humility,[]);self.assertIsNone(self.resolve(s))
        after=next(a for a in self.actions(s) if a.get('card')==murder.ref)
        self.assertIn(target.ref,after['target_schema']['legal_refs'])
        self.cast(s,murder,[target.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[target.object_id].zone);self.replay(s,load=True)

    def test_clone_copies_prohibition_without_targeting_the_original(self):
        s=self.session(1151942);e=s.engine
        original=self.add(e,"Gaea's Revenge",seat='B');self.add(e,'Generic Bound Body',seat='C',ref='clone-fallback')
        clone=self.add(e,'Clone',zone='hand');murder=self.add(e,'Murder',zone='hand');growth=self.add(e,'Giant Growth',zone='hand')
        self.fund(s,{'U':4,'G':4,'B':4,'C':8})
        self.cast(s,clone,[]);choice=self.resolve(s);self.assertIsNotNone(choice)
        self.assertEqual('replacement.order',choice.kind)
        option=next(o for o in s.packet('pilot:A',full=True)['decision']['ctx']['options'] if original.ref in o['id'])
        accepted=s.act('pilot:A',{'action_id':'choose','replacement':option['id']});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[clone.object_id].zone)
        offer=next(a for a in self.actions(s) if a.get('card')==murder.ref)
        self.assertNotIn(clone.ref,offer['target_schema']['legal_refs'])
        self.cast(s,growth,[clone.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(11,s.engine._numeric_stat(clone.object_id,'power'));self.replay(s,load=True)

    def test_pending_entry_trigger_retains_source_incarnation_and_departure_colors(self):
        s=self.session(1151943);e=s.engine
        target=self.add(e,"Gaea's Revenge");source=self.add(e,'Generic Targeting Green Entry Pinger',zone='hand')
        frog=self.add(e,'Turn to Frog',zone='hand');bounce=self.add(e,'Unsummon',zone='hand')
        self.fund(s,{'G':3,'U':4,'C':4})
        self.cast(s,source,[]);decision=self.resolve(s)
        self.assertIsNotNone(decision);self.assertEqual('semantic.target',decision.kind)
        s=self.replay(s,load=True)
        accepted=s.act('pilot:A',{'action_id':'choose','targets':[target.ref]});self.assertTrue(accepted.ok,accepted.summary)
        self.cast(s,frog,[source.ref]);self.resolve_top(s)
        self.cast(s,bounce,[source.ref]);self.resolve_top(s)
        binding=TargetingSourceBinding.from_dict(s.state.stack[-1].context[TARGETING_SOURCE_CONTEXT])
        self.assertEqual(('U',),binding.colors)
        self.resolve_top(s);self.assertEqual(0,s.state.cards[target.object_id].marked_damage);self.replay(s,load=True)

    def test_bartel_allows_nontargeted_aura_entry_from_a_compiled_graveyard_return(self):
        s=self.session(1151944);e=s.engine
        target=self.add(e,'Bartel Runeaxe');aura=self.add(e,'Pacifism',zone='graveyard')
        source=self.add(e,'Generic Targeting Enchantment Return',zone='hand');self.fund(s,{'W':4})
        self.cast(s,source,[aura.ref]);decision=self.resolve(s)
        self.assertIsNotNone(decision);self.assertEqual('aura.entry',decision.kind)
        s=self.replay(s,load=True)
        accepted=s.act('pilot:A',{'action_id':'choose','aura_target':target.ref});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(target.object_id,s.state.cards[aura.object_id].attached_to)
        self.assertEqual('battlefield',s.state.cards[aura.object_id].zone)
        self.replay(s,load=True)

    def test_split_spell_offers_commit_and_revalidation_use_the_selected_face_colors(self):
        from quorune.record import authoritative_state_hash
        s=self.session(1151945);e=s.engine
        target=self.add(e,'Karplusan Strider');self.add(e,'Generic Bound Body',seat='B',ref='split-fallback')
        split=self.add(e,'Stand // Deliver',zone='hand');shock=self.add(e,'Shock',zone='hand')
        self.fund(s,{'W':4,'U':4,'R':4,'C':6})
        offers=[a for a in self.actions(s) if a.get('card')==split.ref]
        stand=next(a for a in offers if a.get('face')=='Stand');deliver=next(a for a in offers if a.get('face')=='Deliver')
        self.assertIn(target.ref,stand['target_schema']['legal_refs']);self.assertNotIn(target.ref,deliver['target_schema']['legal_refs'])
        before=authoritative_state_hash(s.state)
        denied=s.act('pilot:A',{'action_id':deliver['id'],'targets':[target.ref],'pay':'auto'})
        self.assertFalse(denied.ok,denied.summary);self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:A',{'action_id':stand['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.cast(s,shock,[target.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(0,s.state.cards[target.object_id].marked_damage);self.replay(s,load=True)


    def test_native_omissions_fail_the_actual_prohibition_and_lki_witnesses(self):
        from unittest.mock import patch
        from dataclasses import replace
        from quorune import target_restrictions,target_source
        for method,module,name in (
            ('test_actual_dense_foliage_filters_spells_but_allows_native_ability_private_rollback_and_replay',target_restrictions,'current_target_prohibition'),
            ('test_native_gaea_uses_blue_departure_lki_after_green_source_returns_to_printed_hand',target_source,'pin_targeting_source_departures')):
            with patch.object(module,name,return_value=False),self.assertRaises(AssertionError):
                getattr(type(self)(method),method)()
        original=TargetRestrictionSpec.blocks
        def wrong(self,facts,*,acting_controller,observer_controller):
            if self.controller_relation=='source_opponent' and facts is not None:
                facts=replace(facts,source_controller=acting_controller)
            return original(self,facts,acting_controller=acting_controller,observer_controller=observer_controller)
        method='test_native_thrun_rechecks_source_controller_instead_of_locked_ability_controller'
        with patch.object(TargetRestrictionSpec,'blocks',wrong),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()


    def test_unpinned_sacrifice_mana_mode_stays_rejected_before_mutation(self):
        from quorune.record import authoritative_state_hash
        s=self.session(1151946);e=s.engine
        target=self.add(e,"Gaea's Revenge")
        source=self.add(e,'Generic Targeting Paid Green Pinger',seat='B')
        altar=self.add(e,"Ashnod's Altar",seat='B');self.fund(s,{'C':1},actor='B')
        ability=next(a for a in self.actions(s,'B') if a['id'].startswith('activate:'+source.ref+':'))
        before=authoritative_state_hash(s.state)
        result=s.act('pilot:B',{'action_id':ability['id'],'targets':[target.ref],
            'pay':'manual','mana':[{'source':altar.ref,'ability':'ab1','cost_cards':[source.ref]}],'payment':{'C':1}})
        self.assertFalse(result.ok,result.summary)
        self.assertIn('compiler-pinned mana mode',result.summary)
        self.assertEqual(before,authoritative_state_hash(s.state))

    def test_selected_self_sacrifice_cost_keeps_the_source_incarnation_and_controller(self):
        s=self.session(1151947);e=s.engine
        target=self.add(e,'Thrun, Breaker of Silence',seat='B')
        source=self.add(e,'Stronghold Assassin',seat='B');source.acquired_control_turn_count=-1
        self.fund(s,{},actor='B');original=source.logical_object_id
        ability=next(a for a in self.actions(s,'B') if a['id'].startswith('activate:'+source.ref+':'))
        result=s.act('pilot:B',{'action_id':ability['id'],'targets':[target.ref],'cost_cards':[source.ref]})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertIn(TARGETING_SOURCE_CONTEXT,s.state.stack[-1].context)
        binding=TargetingSourceBinding.from_dict(s.state.stack[-1].context[TARGETING_SOURCE_CONTEXT])
        self.assertEqual(original,binding.logical_object_id)
        self.assertEqual('B',binding.controller)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
        self.replay(s,load=True)


    def test_targeted_announcement_rolls_back_while_mana_payment_is_pending_and_resumes_once(self):
        from quorune.damage_prevention_creation import (PreventionShieldCreationRequest,PreventionSubjectAllocation,
            plan_prevention_shield_creation,commit_prevention_shield_creation)
        from quorune.damage_modifier_state import PreventionMode,DamageModifierDuration
        from quorune.record import authoritative_state_hash
        s=self.session(1151948);e=s.engine
        target=self.add(e,"Gaea's Revenge");source=self.add(e,'Generic Targeting Blue Price Green Pinger')
        reef=self.add(e,'Shivan Reef')
        for label in ('first','second'):
            request=PreventionShieldCreationRequest(source_id='fixture:target-payment:'+label,controller='A',mode=PreventionMode.AMOUNT,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,subjects=(PreventionSubjectAllocation('A',1),),label='Prevent '+label)
            commit_prevention_shield_creation(e,plan_prevention_shield_creation(e,request))
        self.fund(s,{})
        ability=next(a for a in self.actions(s) if a['id'].startswith('activate:'+source.ref+':'))
        accepted=s.act('pilot:A',{'action_id':ability['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',s.state.pending_decision.kind)
        self.assertFalse(s.state.stack);self.assertFalse(s.state.cards[reef.object_id].tapped)
        s=self.replay(s,load=True);ctx=s.packet('pilot:A',full=True)['decision']['ctx']
        before=authoritative_state_hash(s.state)
        denied=s.act('pilot:C',{'action_id':'choose','replacement':ctx['options'][0]['id']})
        self.assertFalse(denied.ok,denied.summary);self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:A',{'action_id':'choose','replacement':ctx['options'][0]['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(1,len(s.state.stack))
        binding=TargetingSourceBinding.from_dict(s.state.stack[-1].context[TARGETING_SOURCE_CONTEXT])
        self.assertEqual(source.logical_object_id,binding.logical_object_id)
        self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.cards[target.object_id].marked_damage)
        self.assertEqual(1,sum(event.code=='stack.activate' for event in s.state.events))
        self.replay(s,load=True)


    def test_omitting_announced_source_capture_is_killed_by_native_self_sacrifice(self):
        from unittest.mock import patch
        method='test_selected_self_sacrifice_cost_keeps_the_source_incarnation_and_controller'
        with patch('quorune.rules.activation.payment_commit.announce_targeted_activation',return_value=None),self.assertRaises(AssertionError):
            getattr(type(self)(method),method)()


    def test_target_query_source_work_scales_with_sources_and_rechecks_the_next_query(self):
        from unittest.mock import patch
        from quorune.target_source import spell_targeting_source
        s=self.session(1151949);e=s.engine
        bodies=[self.add(e,'Generic Bound Body',ref='bounded-query-'+str(i)) for i in range(16)]
        spell=self.add(e,'Murder',zone='hand')
        program=next(p for p in e.semantics.programs_for_oracle(spell.oracle_id) if p.effects)
        with patch.object(e,'_effective_ability_fragments',wraps=e._effective_ability_fragments) as fragments:
            schema=e._public_target_schema('A',program.target_schema,source_ref=spell.ref,acting_source=spell_targeting_source(e,spell))
            self.assertEqual({c.ref for c in bodies},set(schema['legal_refs']))
            self.assertLessEqual(fragments.call_count,len(bodies))
        # These are independent read-only query boundaries, before any gameplay
        # checkpoint. A newly present prohibition must be visible immediately.
        self.add(e,'Dense Foliage')
        self.assertIsNone(e._public_target_schema('A',program.target_schema,source_ref=spell.ref,acting_source=spell_targeting_source(e,spell)))


    def test_worded_opponent_auras_follow_aura_controller_and_keep_own_spells_legal(self):
        from quorune.record import authoritative_state_hash
        for index,name in enumerate(('Canopy Cover','Shielding Plax')):
            with self.subTest(aura=name):
                s=self.session(1151950+index);e=s.engine
                target=self.add(e,'Generic Bound Body',seat='B',ref='worded-protected-body')
                self.add(e,'Generic Bound Body',seat='C',ref='worded-fallback')
                own=self.add(e,'Prodigal Sorcerer',ref='worded-own-pinger');own.acquired_control_turn_count=-1
                other=self.add(e,'Prodigal Sorcerer',seat='B',ref='worded-other-pinger');other.acquired_control_turn_count=-1
                aura=self.add(e,name,zone='hand');growth=self.add(e,'Giant Growth',zone='hand')
                murder=self.add(e,'Murder',seat='B',zone='hand')
                e.state.players['B'].mana_pool.update({'B':5});self.fund(s,{'G':8,'C':10})
                initial_hand=set(s.state.players['A'].zones['hand']);power=e._numeric_stat(target.object_id,'power')
                self.cast(s,aura,[target.ref]);self.assertIsNone(self.resolve(s))
                if name=='Shielding Plax':
                    drawn=set(s.state.players['A'].zones['hand'])-initial_hand
                    self.assertEqual(1,len(drawn));ref=s.state.cards[next(iter(drawn))].ref
                    for viewer in 'BCD':self.assertNotIn(ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                offer=next(a for a in self.actions(s) if a.get('card')==growth.ref)
                self.assertIn(target.ref,offer['target_schema']['legal_refs'])
                self.cast(s,growth,[target.ref]);self.assertIsNone(self.resolve(s))
                self.assertEqual(power+3,s.engine._numeric_stat(target.object_id,'power'))
                ability=next(a for a in self.actions(s) if a['id'].startswith('activate:'+own.ref+':'))
                self.assertIn(target.ref,ability['target_schema']['legal_refs'])
                accepted=s.act('pilot:A',{'action_id':ability['id'],'targets':[target.ref]})
                self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
                self.assertEqual(1,s.state.cards[target.object_id].marked_damage)
                self.pass_to(s,'B')
                spell=next(a for a in self.actions(s,'B') if a.get('card')==murder.ref)
                ability=next(a for a in self.actions(s,'B') if a['id'].startswith('activate:'+other.ref+':'))
                self.assertNotIn(target.ref,spell['target_schema']['legal_refs'])
                self.assertNotIn(target.ref,ability['target_schema']['legal_refs'])
                before=authoritative_state_hash(s.state)
                denied=s.act('pilot:B',{'action_id':spell['id'],'targets':[target.ref],'pay':'auto'})
                self.assertFalse(denied.ok,denied.summary);self.assertEqual(before,authoritative_state_hash(s.state))
                self.replay(s,load=True)
