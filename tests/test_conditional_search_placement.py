"""One normal search selection, then reached conditional placement and shuffle."""
from copy import deepcopy
from dataclasses import replace
import json,unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_recipient_control as recipients
import test_opponent_greater_metrics as search_fixtures
from inherited_composition_fixture import InheritedNativeFixture
from quorune.conditional_search_model import ConditionalSearchSpec,CONDITIONAL_SEARCH_CAPABILITY as CAP
from quorune.compiler.conditional_search_templates import conditional_search_effect_template
from quorune.library_search_model import FixedCountedLibrarySearchTemplate,counted_search_bound_dependencies
from quorune.replacement.immutable import freeze_value

STOIC='Search your library for a basic Plains card and reveal it. If an opponent controls more lands than you, put it onto the battlefield tapped. Otherwise, put it into your hand. Then shuffle.'


class ConditionalSearchModelTests(unittest.TestCase):
    def test_conditional_payload_round_trips_and_old_search_versions_remain_closed(self):
        compiled=conditional_search_effect_template(STOIC,source_name='Witness');effect,=compiled[1]
        spec=ConditionalSearchSpec.from_effect(effect);self.assertEqual(effect,spec.effect())
        self.assertEqual(spec.search,FixedCountedLibrarySearchTemplate.from_effect(effect))
        self.assertEqual(spec,ConditionalSearchSpec.from_effect(freeze_value(effect)))
        self.assertEqual(spec.search,FixedCountedLibrarySearchTemplate.from_effect(freeze_value(effect)))
        self.assertIn(CAP,counted_search_bound_dependencies(effect))
        for change in ({'schema_version':2},{'schema_version':3},{'schema_version':True},{'count':{'minimum':0,'maximum':2}},
                {'reveal':False},{'destination':'battlefield'},{'extra':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):FixedCountedLibrarySearchTemplate.from_effect({**effect,**change})
        for change in ({'kind':'unknown'},{'enters_tapped':1},{'condition':{'schema_version':True}},{'maximum':2}):
            changed=deepcopy(effect);changed['conditional_placement'].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):ConditionalSearchSpec.from_effect(changed)

    def test_selected_card_predicates_and_unknown_search_siblings_are_closed(self):
        for tail,kind in (("Put it onto the battlefield if its mana value is 2 or less. Otherwise, put it into your hand. If you search your library this way, shuffle.",'mana_value_at_most'),
                ("Put it onto the battlefield tapped if it's a land card. Otherwise, put it into your hand. Then shuffle.",'card_type')):
            result=conditional_search_effect_template('Search your library for an artifact card and reveal it. '+tail,source_name='Witness')
            self.assertEqual(kind,result[1][0]['conditional_placement']['kind'])
        self.assertIsNone(conditional_search_effect_template(STOIC+' Draw a card.',source_name='Witness'))


class ConditionalSearchNativeTests(InheritedNativeFixture,unittest.TestCase):
    setUpClass=classmethod(recipients.RecipientControlNativeTests.setUpClass.__func__)
    scene=recipients.RecipientControlNativeTests.scene
    reject=recipients.RecipientControlNativeTests.reject
    finish=recipients.RecipientControlNativeTests.finish
    search=search_fixtures.OpponentGreaterMetricNativeTests.search
    select_search=search_fixtures.OpponentGreaterMetricNativeTests.select_search

    def board(self,name,*,more=True):
        extra={}
        def before(s,source,gift):
            for seat,count in (('A',3),('B',4 if more else 2),('C',2)):
                for i in range(count):self.add(s.engine,'Island',seat=seat,ref='conditional-land-'+seat+str(i))
            extra['plains']=self.add(s.engine,'Generic Bound Plains',zone='library',ref='conditional-basic-plains')
            extra['tundra']=self.add(s.engine,'Tundra',zone='library',ref='conditional-nonbasic-plains')
            extra['small']=self.add(s.engine,'Mind Stone',zone='library',ref='conditional-small-artifact')
            extra['large']=self.add(s.engine,'Darksteel Ingot',zone='library',ref='conditional-large-artifact')
            extra['creature']=self.add(s.engine,'Grizzly Bears',zone='library',ref='conditional-found-creature')
            extra['opponent']=self.add(s.engine,'Hill Giant',seat='B',ref='conditional-opponent')
            extra['artifact']=self.add(s.engine,'Mind Stone',seat='B',ref='conditional-opponent-artifact')
            extra['remove']=self.add(s.engine,'Boomerang',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=2
            extra['theft']=self.add(s.engine,'Turn Against',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'C':4,'R':1})
        s,source,gift,private=self.scene(name,before=before)
        return s,source,gift,private,extra

    def stoic(self,more):
        s,source,gift,private,extra=self.board('Stoic Farmer',more=more)
        self.cast(s,'A',source);decision=self.search(s)
        refs={r['id'] for r in decision['ctx']['search_cards']};self.assertIn(extra['plains'].ref,refs);self.assertNotIn(extra['tundra'].ref,refs)
        for viewer in 'BCD':self.assertNotIn(extra['plains'].ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.reject(s,'A',{'action_id':'choose','search_cards':[extra['tundra'].ref]})
        s=self.select_search(s,[extra['plains'].ref]);card=s.state.cards[extra['plains'].object_id]
        self.assertEqual(('battlefield' if more else 'hand','A',more),(card.zone,card.controller,card.tapped))
        self.assertIn(card.ref,json.dumps(s.packet('pilot:C',full=True)));self.finish(s,private)

    def test_original_stoic_farmer_searches_even_when_condition_false_and_preserves_both_destinations(self):
        for more in (False,True):
            with self.subTest(more=more):self.stoic(more)

    def test_current_placement_is_read_after_response_and_uses_the_original_actor(self):
        for control in (False,True):
            with self.subTest(control=control):
                s,source,gift,private,extra=self.board('Stoic Farmer')
                self.cast(s,'A',source);self.resolve_top(s)
                self.cast(s,'B',extra['theft'] if control else extra['remove'],[source.ref if control else 'conditional-land-B0']);self.resolve_top(s)
                self.search(s);s=self.select_search(s,[extra['plains'].ref])
                card=s.state.cards[extra['plains'].object_id]
                self.assertEqual(('battlefield' if control else 'hand','A'),(card.zone,card.controller))
                self.finish(s,private)

    def test_original_guidelight_found_mana_value_two_vs_three_controls_placement(self):
        for small in (False,True):
            with self.subTest(small=small):
                s,source,gift,private,extra=self.board('Guidelight Pathmaker')
                self.cast(s,'A',source);self.search(s);target=extra['small' if small else 'large']
                s=self.select_search(s,[target.ref]);card=s.state.cards[target.object_id]
                self.assertEqual(('battlefield' if small else 'hand','A',False),(card.zone,card.controller,card.tapped))
                self.assertIn(card.ref,json.dumps(s.packet('pilot:C',full=True)));self.finish(s,private)

    def test_optional_decline_and_failure_to_find_have_different_shuffle_results(self):
        for decline in (False,True):
            with self.subTest(decline=decline):
                s,source,gift,private,extra=self.board('Guidelight Pathmaker');before=s.state.players['A'].stats.get('shuffle_count',0)
                self.cast(s,'A',source)
                if decline:
                    self.assertEqual('semantic.choice',self.resolve(s).kind);command={'action_id':'choose','choice':'decline'};self.reject(s,'B',command)
                    s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                else:self.search(s);s=self.select_search(s,[])
                self.assertEqual(before+(not decline),s.state.players['A'].stats.get('shuffle_count',0))
                self.assertEqual('library',s.state.cards[extra['small'].object_id].zone);self.finish(s,private)

    def test_conditional_search_authority_and_comparator_are_required_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for name in ('Stoic Farmer','Guidelight Pathmaker'):
            s,source,gift,private,extra=self.board(name)
            p=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
            for cap in (CAP,*(['condition.player.opponent_greater_metric'] if name=='Stoic Farmer' else [])):
                deps=tuple(c for c in p.capability_dependencies if c!=cap)
                changed=replace(p,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_fresh_stoic_witness_kills_always_putting_the_revealed_card_into_hand(self):
        from quorune.conditional_search_placement import materialize_conditional_search
        method='test_original_stoic_farmer_searches_even_when_condition_false_and_preserves_both_destinations'
        type(self)(method).stoic(True)
        def wrong(host,context,values):
            result=materialize_conditional_search(host,context,values)
            if context.effect.get('schema_version')==4:
                effect={**result.effect,'destination':'hand'};effect.pop('enters_tapped_override',None)
                return replace(result,effect=effect)
            return result
        with patch('quorune.conditional_search_placement.materialize_conditional_search',wrong):
            with self.assertRaises(AssertionError):type(self)(method).stoic(True)

    def cast_mode(self,s,source,mode,targets=()):
        self.priority(s,'A');row=self.action(s,'A',source)
        command={'action_id':row['id'],'modes':[mode],'targets':list(targets),'pay':'auto'};self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_original_archdruid_search_mode_places_land_tapped_and_creature_in_hand(self):
        for land in (False,True):
            with self.subTest(land=land):
                s,source,gift,private,extra=self.board("Archdruid's Charm")
                self.cast_mode(s,source,'mode_1');decision=self.search(s)
                refs={r['id'] for r in decision['ctx']['search_cards']}
                self.assertIn(extra['creature'].ref,refs);self.assertIn(extra['tundra'].ref,refs);self.assertNotIn(extra['small'].ref,refs)
                target=extra['tundra' if land else 'creature'];s=self.select_search(s,[target.ref]);card=s.state.cards[target.object_id]
                self.assertEqual(('battlefield' if land else 'hand','A',land),(card.zone,card.controller,card.tapped));self.finish(s,private)

    def test_original_archdruid_other_modes_keep_counter_current_power_damage_and_exile(self):
        for damage in (False,True):
            with self.subTest(damage=damage):
                s,source,gift,private,extra=self.board("Archdruid's Charm")
                if damage:
                    self.cast_mode(s,source,'mode_2',[gift.ref,extra['opponent'].ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                    self.assertEqual(1,s.state.cards[gift.object_id].counters.get('+1/+1'))
                    self.assertEqual(0,s.state.cards[gift.object_id].marked_damage);self.assertEqual('graveyard',s.state.cards[extra['opponent'].object_id].zone)
                else:
                    self.cast_mode(s,source,'mode_3',[extra['artifact'].ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                    self.assertEqual('exile',s.state.cards[extra['artifact'].object_id].zone);self.assertFalse(s.state.cards[gift.object_id].counters)
                self.finish(s,private)
