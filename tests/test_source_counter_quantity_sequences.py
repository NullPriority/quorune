"""CR608.2c-h: source mutation before independent result-quantity evaluation."""
from dataclasses import replace
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record


class SourceCounterQuantityCompilerTests(unittest.TestCase):
    def test_omitting_prefix_preservation_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler import source_counter_quantity_sequences as owner
        text="Whenever this creature attacks, put a +1/+1 counter on it, then create a number of 1/1 red Goblin creature tokens equal to this creature's power."
        def witness():
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            self.assertEqual('place_counters',ir.faces[0].nodes[0].effects[0]['op'])
        witness()
        with patch.object(owner,'source_counter_quantity_sequence',return_value=None),self.assertRaises(AssertionError):witness()
    def test_source_counter_prefix_keeps_scalar_quantity_and_printed_order(self):
        for text in ("Whenever this creature attacks, put a +1/+1 counter on it, then create a number of 1/1 red Goblin creature tokens equal to this creature's power.",
                'At the beginning of your upkeep, put a muster counter on this enchantment. Then create a 1/1 red and white Soldier creature token with haste for each muster counter on this enchantment.',
                '{2}, {T}: Put a charge counter on this artifact, then draw a card for each charge counter on it.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(replace(query_record(text),type_line='Artifact Creature â€” Goblin'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                effects=ir.faces[0].nodes[0].effects
                self.assertEqual(2,len(effects));self.assertEqual('place_counters',effects[0]['op'])
                self.assertEqual('$source.zone_object',effects[0]['card'])
                self.assertIn(effects[1]['op'],{'draw','create_token'})

    def test_shared_counter_results_preserve_order_binding_and_closed_source(self):
        from unittest.mock import patch
        from quorune.compiler import source_counter_quantity_sequences as owner

        prefix = "At the beginning of your upkeep, put two omen counters on this enchantment. Then "
        result = ("each player creates X Treasure tokens and this enchantment deals X damage to each player, "
                  "where X is the number of omen counters on this enchantment.")

        def compile_text(text):
            return compile_oracle_card(
                replace(query_record(text), type_line="Enchantment"),
                capability_registry=load_default_capability_registry(),
            )

        def witness():
            ir = compile_text(prefix + result)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            placement, tokens, damage = ir.faces[0].nodes[0].effects
            self.assertEqual("place_counters", placement["op"])
            self.assertEqual("create_distributed_tokens", tokens["op"])
            self.assertEqual("damage_fixed_set", damage["op"])
            self.assertEqual(tokens["token"]["quantity"], damage["amount"])
            self.assertIsNotNone(damage["amount"]["binding_id"])

        witness()
        original = owner._shared_source_counter_results

        def reordered(*args, **kwargs):
            identifier, (counter, tokens, damage), schema, mechanics = original(*args, **kwargs)
            return identifier, (counter, damage, tokens), schema, mechanics

        with patch.object(owner, "_shared_source_counter_results", side_effect=reordered):
            with self.assertRaises(AssertionError):
                witness()
        for boundary in (
            result.replace("number of omen", "number of charge"),
            result.replace("damage to each player", "damage to target player"),
            result.replace("this enchantment deals", "that creature deals"),
        ):
            with self.subTest(boundary=boundary):
                self.assertNotEqual("exact", compile_text(prefix + boundary).status)

    def test_open_context_and_linked_result_do_not_gain_source_reference(self):
        for text in ('Whenever another creature attacks, put a +1/+1 counter on it, then draw a card for each counter on it.',
                'Whenever this creature attacks, put a +1/+1 counter on it, then create the remembered token.',
                "Whenever this creature attacks, put a +1/+1 counter on it, then create a token with power equal to the counter's remembered value."):
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class SourceCounterQuantityNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'source-counter-quantity.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/source-counter-quantity-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source counter quantity',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def test_actual_krenko_reads_after_replaced_counter_and_keeps_old_source_after_blink(self):
        for blinked in (False,True):
            with self.subTest(blinked=blinked):
                s=self.session(608200401+int(blinked))
                source=self.add(s.engine,'Krenko, Tin Street Kingpin');source.acquired_control_turn_count=-1
                self.add(s.engine,'Hardened Scales')
                blink=self.add(s.engine,'Cloudshift',zone='hand') if blinked else None
                land=self.add(s.engine,'Plains') if blinked else None
                self.assert_closed(s,source);self.prepare(s,mana={});self.checkpoint(s)
                original=source.logical_object_id;self.attack(s,[(source,'B')])
                if blinked:
                    self.priority(s,'A');rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    mana=next(row for row in rows if row.get('mana_ability') and land.ref in row['id'])
                    self.assertTrue(s.act('pilot:A',{'action_id':mana['id'],'pay':'auto'}).ok)
                    self.cast(s,'A',blink,[source.ref]);self.resolve_top(s)
                    self.assertNotEqual(original,s.state.cards[source.object_id].logical_object_id)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                current=s.state.cards[source.object_id]
                self.assertEqual(0 if blinked else 2,current.counters.get('+1/+1',0))
                goblins=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token and c.printed_name=='Goblin Token']
                self.assertEqual(1 if blinked else 3,len(goblins));self.replay(s,load=True)

    def test_actual_insight_engine_draws_from_postcounter_amount_and_keeps_cards_private(self):
        s=self.session(608200403);source=self.add(s.engine,'Insight Engine');source.counters['charge']=2
        self.assert_closed(s,source);self.prepare(s,mana={'C':2});self.checkpoint(s)
        hand=set(s.state.players['A'].zones['hand']);rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in rows if row['id'].startswith('activate:'+source.ref+':') and not row.get('mana_ability'))
        command={'action_id':offered['id'],'pay':'auto'};before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertTrue(s.act('pilot:A',command).ok);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(3,s.state.cards[source.object_id].counters['charge'])
        self.assertEqual(len(hand)+3,len(s.state.players['A'].zones['hand']))
        drawn=set(s.state.players['A'].zones['hand'])-hand
        for seat in 'BCD':
            packet=json.dumps(s.packet('pilot:'+seat,full=True))
            for uid in drawn:self.assertNotIn(s.state.cards[uid].ref,packet)
        self.replay(s,load=True)

    def test_actual_aligned_heart_second_spell_creates_postcounter_tokens_with_prowess(self):
        s=self.session(608200404)
        source=self.add(s.engine,'Aligned Heart');source.counters['rally']=1
        spells=[self.add(s.engine,'Memnite',zone='hand',ref='aligned-cast-'+str(i)) for i in range(2)]
        draw=self.add(s.engine,"Chaplain's Blessing",zone='hand');land=self.add(s.engine,'Plains')
        self.assert_closed(s,source);self.assert_closed(s,draw)
        self.prepare(s,mana={});self.checkpoint(s)
        self.cast(s,'A',spells[0],[]);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[source.object_id].counters['rally'])
        self.cast(s,'A',spells[1],[]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.zone=='battlefield' and c.is_token and c.printed_name=='Monk Token']
        self.assertEqual(2,len(tokens));self.assertEqual(2,s.state.cards[source.object_id].counters['rally'])
        self.priority(s,'A');rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in rows if row.get('mana_ability') and land.ref in row['id'])
        self.assertTrue(s.act('pilot:A',{'action_id':mana['id'],'pay':'auto'}).ok)
        self.cast(s,'A',draw,[])
        self.assertEqual('trigger.order',s.state.pending_decision.kind)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx']
        self.assertEqual(2,len(ctx['triggers']))
        self.assertTrue(s.act('pilot:A',{'action_id':'order','triggers':[row['id'] for row in ctx['triggers']]}).ok)
        self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.engine._numeric_stat(token.object_id,'power')==2 for token in tokens))
        self.assertEqual(2,s.state.cards[source.object_id].counters['rally']);self.replay(s,load=True)


    def start_upkeep(self, session, source):
        self.assert_closed(session, source)
        self.prepare(session, mana={})
        from special_trigger_fixture import SpecialTriggerFixture

        SpecialTriggerFixture.begin_prepared_turn(self, session, "A")
        self.until(session, lambda game: game.state.step == "upkeep" and bool(game.state.stack))
        trigger = session.state.stack[-1]
        self.assertEqual(source.object_id, trigger.source_object_id)
        self.assertFalse(trigger.targets)

    def treasure_counts(self, session):
        return {
            seat: sum(card.zone == "battlefield" and card.is_token
                      and card.printed_name == "Treasure Token" and card.controller == seat
                      for card in session.state.cards.values())
            for seat in "ABCD"
        }

    def test_actual_descent_uses_committed_replaced_counter_count_for_treasure_first_and_damage(self):
        from unittest.mock import patch

        session = self.session(608200410)
        source = self.add(session.engine, "Descent into Avernus")
        source.counters["descent"] = 1
        replacement = self.add(session.engine, "Doubling Season")
        self.assert_closed(session, replacement)
        self.start_upkeep(session, source)
        life = {seat: session.state.players[seat].life for seat in "ABCD"}
        expected = {"A": 10, "B": 5, "C": 5, "D": 5}
        actual_at_damage = []
        actual_damage = session.engine.deal_fixed_damage_set_intent

        def observe(*args, **kwargs):
            actual_at_damage.append(self.treasure_counts(session))
            return actual_damage(*args, **kwargs)

        with patch.object(session.engine, "deal_fixed_damage_set_intent", side_effect=observe):
            self.assertIsNone(self.resolve(session))
        self.assertEqual([expected], actual_at_damage)
        self.assertEqual(expected, self.treasure_counts(session))
        self.assertEqual(5, session.state.cards[source.object_id].counters["descent"])
        self.assertEqual({seat: total - 5 for seat, total in life.items()},
                         {seat: session.state.players[seat].life for seat in "ABCD"})
        self.replay(session, load=True)

    def test_actual_descent_departure_uses_last_known_counters_and_zero_makes_no_events(self):
        for counters in (0, 3):
            with self.subTest(counters=counters):
                session = self.session(608200411 + counters)
                source = self.add(session.engine, "Descent into Avernus")
                if counters:
                    source.counters["descent"] = counters
                removal = self.add(session.engine, "Disenchant", zone="hand")
                self.assert_closed(session, removal)
                lands = [self.add(session.engine, "Plains", ref=f"descent-plains-{i}") for i in range(2)]
                self.start_upkeep(session, source)
                life = {seat: session.state.players[seat].life for seat in "ABCD"}
                self.priority(session, "A")
                for land in lands:
                    rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    action = next(row for row in rows if row.get("mana_ability") and land.ref in row["id"])
                    self.assertTrue(session.act("pilot:A", {"action_id": action["id"], "pay": "auto"}).ok)
                self.cast(session, "A", removal, [source.ref])
                self.resolve_top(session)
                self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(dict.fromkeys("ABCD", counters), self.treasure_counts(session))
                self.assertEqual({seat: total - counters for seat, total in life.items()},
                                 {seat: session.state.players[seat].life for seat in "ABCD"})
                self.replay(session, load=True)

    def test_actual_descent_uses_last_known_counters_when_phased_out(self):
        session = self.session(608200419)
        source = self.add(session.engine, "Descent into Avernus")
        source.counters["descent"] = 2
        phase = self.add(session.engine, "Clever Concealment", zone="hand")
        self.assert_closed(session, phase)
        lands = [self.add(session.engine, "Plains", ref=f"descent-phase-plains-{i}") for i in range(4)]
        self.start_upkeep(session, source)
        self.priority(session, "A")
        life = {seat: session.state.players[seat].life for seat in "ABCD"}
        for land in lands:
            rows = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            action = next(row for row in rows if row.get("mana_ability") and land.ref in row["id"])
            self.assertTrue(session.act("pilot:A", {"action_id": action["id"], "pay": "auto"}).ok)
        self.cast(session, "A", phase, [source.ref])
        self.resolve_top(session)
        self.assertTrue(session.state.cards[source.object_id].phased_out)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(dict.fromkeys("ABCD", 2), self.treasure_counts(session))
        self.assertEqual(2, session.state.cards[source.object_id].counters["descent"])
        self.assertEqual({seat: total - 2 for seat, total in life.items()},
                         {seat: session.state.players[seat].life for seat in "ABCD"})
        self.replay(session, load=True)
