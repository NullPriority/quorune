"""Native inherited compositions partitioned by the existing loadfile granularity."""
import json,unittest
from test_qualified_zone_event_queries import query_record
import inherited_composition_fixture as fixtures
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry

class SourcePronounBindingCompilerTests(unittest.TestCase):
    def test_source_led_scalar_pronoun_never_reads_the_result_target(self):
        from quorune.oracle_ir import compile_oracle_card
        from dataclasses import replace
        registry=load_default_capability_registry()
        source="When this creature enters, it deals damage equal to its power to target creature with flying an opponent controls."
        node,=compile_oracle_card(query_record(source),capability_registry=registry).faces[0].nodes
        self.assertTrue(node.exact,node.residual_ids);self.assertEqual('source_characteristic',node.effects[0]['amount']['origin'])
        target="When this creature enters, target creature you control gets +X/+X until end of turn, where X is its power."
        ir=compile_oracle_card(query_record(target),capability_registry=registry);self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertEqual('target_characteristic',ir.faces[0].nodes[0].effects[0]['power']['origin'])
        explicit=compile_oracle_card(query_record(source.replace('it deals','This creature deals')),capability_registry=registry)
        self.assertEqual('source_characteristic',explicit.faces[0].nodes[0].effects[0]['amount']['origin'])

    def test_restored_target_precedence_mutant_is_killed(self):
        from unittest.mock import patch
        from dataclasses import replace
        from quorune.scalar_effect_amount_model import ScalarAmountOrigin
        import quorune.compiler.scalar_effect_amounts as owner
        original=owner._characteristic
        def wrong(value,**kwargs):
            result=original(value,**kwargs)
            if result is not None and value.casefold()=='its power' and kwargs['source_event'] and kwargs['body'].casefold().startswith('it deals'):
                return replace(result,origin=ScalarAmountOrigin.TARGET)
            return result
        self.test_source_led_scalar_pronoun_never_reads_the_result_target()
        with patch.object(owner,'_characteristic',side_effect=wrong),self.assertRaises(AssertionError):self.test_source_led_scalar_pronoun_never_reads_the_result_target()

class SourceCharacteristicCompositionNativeTests(fixtures.InheritedNativeFixture, unittest.TestCase):
    def test_actual_noise_marine_counts_its_cascaded_cast_and_never_the_free_mana_price(self):
        session=self.session(608646010);source=self.add(session.engine,'Noise Marine',zone='hand');candidate=self.add(session.engine,'Shock',zone='library',ref='noise-cascade-shock')
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'R':1});self.checkpoint(session);self.cast(session,'A',source)
        decision=self.resolve(session);self.assertEqual('selection.exile_cast',decision.kind)
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:C',{'action_id':'cast','targets':['B']}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'cast','targets':['B']});self.assertTrue(result.ok,result.summary)
        item=next(i for i in session.state.stack if i.card_object_id==candidate.object_id);self.assertEqual(0,sum(item.mana_spent_counts.amounts))
        self.target(session,'A',['B']);life=session.state.players['B'].life;session=self.replay(session,load=True);self.settle(session)
        self.assertEqual(life-2,session.state.players['B'].life);self.assertEqual('battlefield',session.state.cards[source.object_id].zone);self.replay(session,load=True)

    def test_actual_pyremaw_places_counter_before_power_damage_and_uses_departure_lki(self):
        for removed in (False,True):
            with self.subTest(removed=removed):
                session=self.session(608646020+removed);source=self.add(session.engine,'Caldera Pyremaw',zone='hand');spell=self.add(session.engine,'Reach Through Mists',zone='hand')
                bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B');self.assert_closed(session,source)
                self.prepare(session,mana={'C':3,'R':2,'U':1});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'U':1});self.settle(session)
                self.cast(session,'A',spell);self.target(session,'A',['B']);life=session.state.players['B'].life
                if removed:self.cast(session,'B',bounce,[source.ref]);self.resolve_top(session)
                session=self.replay(session,load=True);self.settle(session);self.assertEqual(life-(3 if removed else 4),session.state.players['B'].life)
                self.assertEqual('hand' if removed else 'battlefield',session.state.cards[source.object_id].zone);self.assertEqual(0 if removed else 1,session.state.cards[source.object_id].counters.get('+1/+1',0));self.replay(session,load=True)

    def test_actual_sharpshooter_reads_current_count_or_its_battlefield_lki(self):
        for removed in (False,True):
            with self.subTest(removed=removed):
                session=self.session(608646030+removed);source=self.add(session.engine,'Sharpshooter Elf',zone='hand');self.add(session.engine,'Grizzly Bears')
                flyer=self.add(session.engine,'Shivan Dragon',seat='B');tokens=self.add(session.engine,'Raise the Alarm',zone='hand');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B')
                self.assert_closed(session,source);self.prepare(session,mana={'C':3,'G':1,'W':1});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'C':1,'W':1});self.target(session,'A',[flyer.ref])
                if removed:self.cast(session,'B',bounce,[source.ref]);self.resolve_top(session)
                self.cast(session,'A',tokens);self.resolve_top(session);session=self.replay(session,load=True);self.settle(session)
                self.assertEqual('battlefield',session.state.cards[flyer.object_id].zone)
                self.assertEqual(2 if removed else 4,session.state.cards[flyer.object_id].marked_damage)
                self.replay(session,load=True)

    def test_actual_chainweb_escape_counters_precede_entry_damage_and_survive_source_bounce(self):
        session=self.session(608646040);source=self.add(session.engine,'Chainweb Aracnir',zone='graveyard');fodder=[self.add(session.engine,'Generic Bound Plains',zone='graveyard',ref='aracnir-fodder-'+str(i)) for i in range(4)]
        flyer=self.add(session.engine,'Shivan Dragon',seat='B');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B');self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'G':2});self.checkpoint(session);offered=self.action(session,'A',source)
        result=session.act('pilot:A',{'action_id':offered['id'],'cost_option':'escape','exile_cards':[c.ref for c in fodder],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.target(session,'A',[flyer.ref]);self.assertEqual(3,session.state.cards[source.object_id].counters.get('+1/+1'));self.assertEqual(4,session.engine._numeric_stat(source.object_id,'power'))
        self.cast(session,'B',bounce,[source.ref]);self.resolve_top(session);session=self.replay(session,load=True);self.settle(session)
        self.assertEqual('battlefield',session.state.cards[flyer.object_id].zone);self.assertEqual(4,session.state.cards[flyer.object_id].marked_damage);self.assertEqual('hand',session.state.cards[source.object_id].zone)
        for card in fodder:self.assertEqual('exile',session.state.cards[card.object_id].zone)
        self.replay(session,load=True)

    def test_actual_duggan_reads_resolution_hand_count_and_once_usage_survives_untap(self):
        session=self.session(608646090);source=self.add(session.engine,'Duggan, Private Detective',zone='hand');target=self.add(session.engine,'Darksteel Colossus',seat='B')
        draw=self.add(session.engine,'Inspiration',zone='hand');untap=self.add(session.engine,'Twiddle',zone='hand');self.add(session.engine,'Fervor');self.add(session.engine,'Forest');self.add(session.engine,'Wastes')
        private=self.add(session.engine,'Shock',seat='D',zone='hand',ref='duggan-private');self.assert_closed(session,source)
        self.prepare(session,mana={'C':6,'G':2,'U':3});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'C':4,'G':1,'U':2});self.settle(session)
        clues=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield' and 'clue' in session.engine._type_parts(session.engine._effective_card_data(c)['type_line'])[1]];self.assertEqual(1,len(clues))
        initial=len(session.state.players['A'].zones['hand']);self.assertEqual(initial,session.engine._numeric_stat(source.object_id,'power'));self.priority(session,'A')
        offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+session.state.cards[source.object_id].ref+':'))
        result=session.act('pilot:A',{'action_id':offered['id'],'targets':[target.ref],'pay':'auto','reserve':{'C':3,'U':2}});self.assertTrue(result.ok,result.summary)
        self.cast_for(session,'A',draw,['A'],reserve={'U':1});self.resolve_top(session);hand=len(session.state.players['A'].zones['hand']);self.assertEqual(initial+1,hand)
        session=self.replay(session,load=True);self.settle(session);self.assertEqual(2*hand,session.state.cards[target.object_id].marked_damage)
        self.cast(session,'A',untap,[session.state.cards[source.object_id].ref]);self.assertEqual('semantic.choice',self.resolve(session).kind)
        result=session.act('pilot:A',{'action_id':'choose','choice':'untap'});self.assertTrue(result.ok,result.summary);self.settle(session);self.assertFalse(session.state.cards[source.object_id].tapped);self.priority(session,'A')
        self.assertFalse(any(a['id'].startswith('activate:'+session.state.cards[source.object_id].ref+':') for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']))
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'targets':[target.ref],'pay':'auto'}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

