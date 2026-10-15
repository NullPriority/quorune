from __future__ import annotations

from dataclasses import replace
import json
import unittest
from public_state_action_support import PublicStateActionFixture
from unittest import mock
from common import ROOT
from quorune.card_programs import bind_card_program_runtime, compile_card_program
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry


class AdditionalConditionalModifierCompilerTests(unittest.TestCase):
    """CR 611.3a/613.4c: additional fixed bonuses add and remain live.

    The wording carries no remembered value or reference to an earlier bonus.
    Only a complete signed fixed modifier behind a represented condition is
    admitted; increasing a variable or repeating an effect remains residual.
    """

    def test_additional_and_also_fixed_bonuses_keep_the_complete_condition(self):
        from types import SimpleNamespace
        from test_qualified_zone_event_queries import query_record

        registry = load_default_capability_registry()
        cases = (
            ("As long as you have no cards in hand, this creature gets an additional +2/+0.", 2, 0),
            ("This creature gets an additional +1/-1 as long as you have no cards in hand.", 1, -1),
            ("As long as equipped creature is a Human, it gets an additional +1/+0.", 1, 0),
            ("Threshold — Enchanted creature gets an additional +2/-2 as long as there are seven or more cards in your graveyard.", 2, -2),
            ("Creatures you control also get +1/+0 and have trample as long as you control six or more creatures.", 1, 0),
        )
        for text, power, toughness in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(query_record(text), capability_registry=registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, node.text)
                self.assertEqual((0, len(text)), (node.span.start, node.span.end))
                handler = next(h for h in node.handlers if "modifier" in h)
                self.assertEqual(power, handler["modifier"]["power"])
                self.assertEqual(toughness, handler["modifier"]["toughness"])
                self.assertIn("source_condition", handler)
                self.assertTrue(bind_card_program_runtime(
                    compile_card_program(
                        SimpleNamespace(rulings=lambda _oracle_id: ()), query_record(text),
                        capability_registry=registry, capability_profile="commander_review",
                        trust_level="trusted",
                    ),
                    capability_registry=registry, profile="commander_review",
                )["strict_capability_ready"])

    def test_additional_wording_does_not_admit_unrepresented_bonus_or_condition(self):
        from test_qualified_zone_event_queries import query_record

        registry = load_default_capability_registry()
        for text in (
            "As long as you have no cards in hand, this creature gets an additional +X/+X.",
            "As long as you control a stickered permanent, this creature gets an additional +2/+0.",
            "As long as you have no cards in hand, this creature gets an additional +2/+0 for each card in your graveyard.",
            "As long as you have no cards in hand, this creature gets an additional +2/+0. Repeat this effect.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_oracle_card(
                    query_record(text), capability_registry=registry,
                ).status)

    def test_additional_modifier_producer_and_capability_omissions_fail_closed(self):
        from test_qualified_zone_event_queries import query_record

        text = "As long as you have no cards in hand, this creature gets an additional +2/+0."
        registry = load_default_capability_registry()
        with mock.patch(
            "quorune.compiler.continuous_templates._fixed_additive_modifier_body",
            side_effect=lambda body: body,
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                query_record(text), capability_registry=registry,
            ).status)
        blocked = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        for capability in blocked["capabilities"]:
            if capability["id"] == CAPABILITY_ID:
                capability["status"] = "blocked"
                capability["blockers"] = ["focused additive modifier omission"]
        self.assertNotEqual("exact", compile_oracle_card(
            query_record(text), capability_registry=CapabilityRegistry(blocked),
        ).status)

class AdditionalConditionalModifierRuntimeTests(PublicStateActionFixture, unittest.TestCase):
    def test_fixture_addition_registers_complete_card_once_per_isolated_session(self):
        from unittest.mock import patch
        from quorune.oracle_ir import register_generated_programs

        registration_calls = []

        def observed_registration(db, semantics, records, **kwargs):
            records = tuple(records)
            registration_calls.append(tuple(record.oracle_id for record in records))
            return register_generated_programs(db, semantics, records, **kwargs)

        with patch.object(self._bound, 'register_generated_programs', observed_registration):
            first = self.session(3633401)
            source_record = self.db.lookup('Heritage Druid')
            unrelated_record = self.db.lookup('Master of Waves')
            self.assertEqual([], registration_calls)
            self.assertFalse(first.engine.semantics.programs_for_oracle(source_record.oracle_id))
            self.assertFalse(first.engine.semantics.programs_for_oracle(unrelated_record.oracle_id))
            source = self.add(first.engine, 'Heritage Druid', ref='registration-first')
            self.assertEqual([(source_record.oracle_id,)], registration_calls)
            self.assertTrue(first.engine.semantics.programs_for_oracle(source.oracle_id))
            self.assertTrue(all(first.engine.semantic_program_is_current_trusted(program)
                                for program in first.engine.semantics.programs_for_oracle(source.oracle_id)))
            self.assertTrue(bind_card_program_runtime(
                first.engine.semantics.card_program_for_oracle(source.oracle_id),
                capability_registry=self.registry, profile='commander_review',
            )['strict_capability_ready'])
            self.assertFalse(first.engine.semantics.programs_for_oracle(unrelated_record.oracle_id))
            self.add(first.engine, 'Heritage Druid', seat='B', ref='registration-second')
            self.assertEqual(1, len(registration_calls))
            second = self.session(3633402)
            self.assertIsNot(first.engine.semantics, second.engine.semantics)
            self.assertFalse(second.engine.semantics.programs_for_oracle(source_record.oracle_id))
            self.add(second.engine, 'Heritage Druid', ref='registration-other-session')
            self.assertEqual([(source_record.oracle_id,)] * 2, registration_calls)
            self.assertNotEqual(source.object_id, 'fixture:registration-other-session')

    def test_fixture_card_registration_omission_keeps_untrusted_actions_unavailable(self):
        from unittest.mock import patch
        from quorune.rules.activation.query import activated_abilities

        session = self.session(3633403)
        with patch.object(self._bound, 'register_generated_programs', return_value={}):
            source = self.add(session.engine, 'Heritage Druid', ref='registration-omitted')
        for index in range(2):
            self.add(session.engine, 'Generic Selected Tap Elf', ref=f'registration-elf-{index}')
        self.assertFalse(session.engine.semantics.programs_for_oracle(source.oracle_id))
        abilities = activated_abilities(session.engine, source)
        self.assertFalse(abilities)
        self.assertFalse(any(action['id'].startswith(f'activate:{source.ref}:')
                             for action in session.engine._priority_action_hints('A')['actions']))

    def test_fixture_registration_rechecks_changed_inputs_and_failed_attempts(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.carddb import Ruling
        from quorune.oracle_ir import register_generated_programs

        session = self.session(3633404)
        source = self.add(session.engine, 'Heritage Druid', ref='registration-input-source')
        record = self.db.lookup('Heritage Druid')
        original_rulings = tuple(self.db.rulings(record))
        changed = replace(record, oracle_text=record.oracle_text + '\nUnrepresented fixture restriction.')
        with patch.object(self.db, 'lookup', return_value=changed), patch.object(
            self._bound, 'register_generated_programs', side_effect=RuntimeError('changed record registration'),
        ) as registration:
            with self.assertRaisesRegex(RuntimeError, 'changed record registration'):
                self.add(session.engine, 'Heritage Druid', ref='registration-changed-input')
            registration.assert_called_once()
        with patch.object(self.db, 'rulings', return_value=(*original_rulings,
            Ruling(oracle_id=record.oracle_id, published_at='2026-10-14', source='fixture', comment='changed'))):
            with patch.object(self._bound, 'register_generated_programs',
                              side_effect=RuntimeError('changed rulings registration')) as registration:
                with self.assertRaisesRegex(RuntimeError, 'changed rulings registration'):
                    self.add(session.engine, 'Heritage Druid', ref='registration-changed-ruling')
                registration.assert_called_once()
        for program in tuple(session.engine.semantics.programs_for_oracle(source.oracle_id)):
            session.engine.semantics.remove(program.key)
        with patch.object(self._bound, 'register_generated_programs', wraps=register_generated_programs) as registration:
            self.add(session.engine, 'Heritage Druid', ref='registration-after-removal')
            registration.assert_called_once()
        self.assertTrue(session.engine.semantics.programs_for_oracle(source.oracle_id))

    def test_boolean_intervening_condition_rechecks_after_actual_attack_response_and_replays(self):
        from quorune.model import CombatState
        session=self.session(3631502);engine=session.engine
        source=self.add(engine,'Generic Boolean Attack Observer',ref='boolean-attack-source')
        response=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='boolean-attack-response')
        session.state.players['B'].life=10
        self.assert_whole_admission(source.printed_name)
        engine.permissions.invalidate_current();session.state.pending_decision=None
        session.state.started=True;session.state.active_player='A';session.state.phase='combat'
        session.state.step='declare_attackers';session.state.phase_index=5;session.state.combat=CombatState()
        engine.pump();self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(any(item.kind=='triggered_ability' for item in session.state.stack))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==response.ref)
        before=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(12,session.state.players['B'].life)
        self.assertEqual(before-1,len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_blood_baron_boolean_life_condition_ends_after_actual_life_gain_and_replays(self):
        session=self.session(3631501);engine=session.engine
        source=self.add(engine,'Blood Baron of Vizkopa',zone='hand',ref='boolean-blood-baron')
        life_spell=self.add(engine,'Stream of Life',zone='hand',ref='boolean-life-gain-spell')
        green=self.add(engine,'Generic Boolean Green Source',ref='boolean-life-gain-land')
        session.state.players['B'].life=10
        self.assert_whole_admission(source.printed_name)
        self.assert_whole_admission(life_spell.printed_name)
        offered=self.ready(session,source,{'C':5,'W':1,'B':1})
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:D',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(10,engine._numeric_stat(source.object_id,'power'))
        self.assertIn('Flying',engine._effective_card_data(session.state.cards[source.object_id])['keywords'])
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in actions if row['id'].startswith('activate:'+green.ref+':'))
        self.assertTrue(session.act('pilot:A',{'action_id':mana['id']}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==life_spell.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':['B'],'x':2,'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(12,session.state.players['B'].life)
        self.assertEqual(4,engine._numeric_stat(source.object_id,'power'))
        keywords=engine._effective_card_data(session.state.cards[source.object_id])['keywords']
        self.assertNotIn('Flying',keywords)
        self.assertIn('Lifelink',keywords)
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_howlpack_singular_union_cast_grant_and_source_bounce_replays(self):
        session=self.session(3631101)
        engine=session.engine
        source=self.add(engine,'Howlpack Resurgence',zone='hand',ref='singular-howlpack-source')
        wolf=self.add(engine,'Generic Singular Wolf',ref='singular-wolf')
        werewolf=self.add(engine,'Generic Singular Werewolf',ref='singular-werewolf')
        dual=self.add(engine,'Generic Singular Wolf Werewolf',ref='singular-dual')
        outsider=self.add(engine,'Generic Bound Body',ref='singular-human')
        foreign=self.add(engine,'Generic Singular Wolf',seat='B',ref='singular-foreign-wolf')
        bounce=self.add(engine,'Generic Singular Enchantment Bounce',zone='hand',ref='singular-source-bounce')
        island=self.add(engine,'Generic Conditional Keyword Island',ref='singular-bounce-island')
        self.assert_whole_admission('Howlpack Resurgence')
        offered=self.ready(session,source,{'G':1,'C':2})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        for recipient in (wolf,werewolf,dual):
            self.assertEqual(3,engine._numeric_stat(recipient.object_id,'power'))
            self.assertIn('trample',{word.casefold() for word in engine._effective_card_data(recipient)['keywords']})
        for recipient in (outsider,foreign):
            self.assertEqual(2,engine._numeric_stat(recipient.object_id,'power'))
            self.assertNotIn('trample',{word.casefold() for word in engine._effective_card_data(recipient)['keywords']})
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in actions if row['id'].startswith('activate:'+island.ref+':'))
        self.assertTrue(session.act('pilot:A',{'action_id':mana['id']}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==bounce.ref)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:D',{'action_id':cast['id'],'targets':[source.ref],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True)
        self.resolve(session)
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        for recipient in (wolf,werewolf,dual):
            current=session.state.cards[recipient.object_id]
            self.assertEqual(2,engine._numeric_stat(current.object_id,'power'))
            self.assertNotIn('trample',{word.casefold() for word in engine._effective_card_data(current)['keywords']})
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_oathsworn_graveyard_cast_uses_current_controller_life_history_and_replay(self):
        session=self.session(3631002)
        engine=session.engine
        vampire=self.add(engine,'Oathsworn Vampire',zone='graveyard',ref='conditional-zone-vampire')
        self.assert_whole_admission('Oathsworn Vampire')
        tapper=self.add(engine,'Generic Conditional Keyword Tap Helper',ref='conditional-zone-lifegain')
        body=self.add(engine,'Generic Bound Body',ref='conditional-zone-lifegain-body')
        self.ready(session,tapper,{'B':1,'C':1})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row.get('card')==vampire.ref for row in actions))
        activation=next(row for row in actions if row['id'].startswith('activate:'+tapper.ref+':'))
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':activation['id'],'cost_cards':[body.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==vampire.ref)
        self.replay(session,load=True)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual('battlefield',session.state.cards[vampire.object_id].zone)
        self.assertTrue(session.state.cards[vampire.object_id].tapped)
        self.replay(session)

    def test_original_prowler_graveyard_permission_tracks_owner_condition_and_actual_cast_with_replay(self):
        session=self.session(3631001)
        engine=session.engine
        prowler=self.add(engine,'Marang River Prowler',zone='graveyard',ref='conditional-zone-prowler')
        prowler.controller='B'
        self.assert_whole_admission('Marang River Prowler')
        qualifier=self.add(engine,'Generic Conditional Green Qualifier',ref='conditional-zone-qualifier')
        foreign=self.add(engine,'Generic Conditional Green Qualifier',seat='C',ref='conditional-zone-foreign')
        bounce=self.add(engine,'Generic Recipient Count Artifact Bounce',zone='hand',ref='conditional-zone-bounce')
        island=self.add(engine,'Generic Conditional Keyword Island',ref='conditional-zone-island')
        self.ready(session,bounce,{'U':2,'C':2})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row.get('card')==prowler.ref)
        from quorune.semantic_runtime.casting_activation_metadata import compiled_self_zone_cast_permission
        self.assertTrue(compiled_self_zone_cast_permission(engine,'A',prowler))
        self.assertFalse(compiled_self_zone_cast_permission(engine,'B',prowler))
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:D',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==bounce.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[qualifier.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertFalse(compiled_self_zone_cast_permission(engine,'A',session.state.cards[prowler.object_id]))
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row.get('card')==prowler.ref for row in actions))
        qualifier_cast=next(row for row in actions if row.get('card')==qualifier.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':qualifier_cast['id'],'pay':'auto'}).ok)
        self.resolve(session)
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in actions if row['id'].startswith('activate:'+island.ref+':'))
        self.assertTrue(session.act('pilot:A',{'action_id':mana['id']}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==prowler.ref)
        self.replay(session,load=True)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual('battlefield',session.state.cards[prowler.object_id].zone)
        self.assertEqual('A',session.state.cards[prowler.object_id].controller)
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_recipient_count_equipment_cast_equip_reattach_and_bounce_replays(self):
        from quorune.attachments import attach_objects
        session = self.session(3630901)
        engine = session.engine
        gauntlets = self.add(engine, 'Golem-Skin Gauntlets', zone='hand', ref='recipient-gauntlets')
        suit = self.add(engine, 'Thran Power Suit', zone='hand', ref='recipient-suit')
        target = self.add(engine, 'Generic Bound Body', ref='recipient-body')
        other = self.add(engine, 'Generic Bound Body', ref='recipient-other-body')
        foreign = self.add(engine, 'Generic Recipient Count Equipment', seat='C', ref='recipient-foreign-equipment')
        attach_objects(engine.state.cards, foreign, target, source_timestamp=engine._next_zone_timestamp())
        bounce = self.add(engine, 'Generic Recipient Count Artifact Bounce', zone='hand', ref='recipient-attachment-bounce')
        island = self.add(engine, 'Generic Conditional Keyword Island', ref='recipient-bounce-island')
        self.assert_whole_admission('Golem-Skin Gauntlets')
        self.assert_whole_admission('Thran Power Suit')
        static = next(p for p in engine.semantics.programs_for_oracle(gauntlets.oracle_id) if p.handlers and p.handlers[0]['handler_id']=='continuous.attached.fixed-characteristics.v1')
        self.assertTrue(engine.semantic_program_is_current_trusted(static))
        self.ready(session,session.state.cards[island.object_id],{'C':9})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row.get('card')==gauntlets.ref)
        self.checkpoint(session)
        accepted = session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        for equipment, recipient in ((gauntlets,target),(suit,target),(gauntlets,other)):
            while session.state.priority_player != 'A':
                result=session.act(session.pending_principals()[0],{'action_id':'pass'})
                self.assertTrue(result.ok,result.summary)
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if equipment is suit:
                cast=next(row for row in actions if row.get('card')==suit.ref)
                self.assertTrue(session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'}).ok)
                self.resolve(session)
                while session.state.priority_player != 'A':
                    result=session.act(session.pending_principals()[0],{'action_id':'pass'})
                    self.assertTrue(result.ok,result.summary)
                actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            equip=next(row for row in actions if row['id'].startswith('activate:'+equipment.ref+':'))
            before=session.state.to_dict()
            self.assertFalse(session.act('pilot:D',{'action_id':equip['id'],'targets':[recipient.ref],'pay':'auto'}).ok)
            self.assertEqual(before,session.state.to_dict())
            accepted=session.act('pilot:A',{'action_id':equip['id'],'targets':[recipient.ref],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            self.resolve(session)
            if equipment is gauntlets and recipient is target:
                self.assertEqual(4,engine._numeric_stat(target.object_id,'power'))
                self.assertEqual(6,engine._numeric_stat(target.object_id,'toughness'))
            elif equipment is suit:
                self.assertEqual(8,engine._numeric_stat(target.object_id,'power'))
                self.assertEqual(9,engine._numeric_stat(target.object_id,'toughness'))
                self.replay(session,load=True)
            else:
                self.assertEqual(4,engine._numeric_stat(target.object_id,'power'))
                self.assertEqual(3,engine._numeric_stat(other.object_id,'power'))
        while session.state.priority_player != 'A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in actions if row['id'].startswith('activate:'+island.ref+':'))
        self.assertTrue(session.act('pilot:A',{'action_id':mana['id']}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==bounce.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[foreign.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual('hand',session.state.cards[foreign.object_id].zone)
        self.assertEqual(3,engine._numeric_stat(target.object_id,'power'))
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_omenreader_live_reduction_tracks_actual_tap_cast_and_bounce_with_replay(self):
        session = self.session(3630801)
        engine = session.engine
        source = self.add(engine, 'Centaur Omenreader', ref='conditional-cost-source')
        self.assert_whole_admission('Centaur Omenreader')
        tapper = self.add(engine, 'Generic Conditional Keyword Tap Helper', ref='conditional-cost-tapper')
        spell = self.add(engine, 'Generic Conditional Cost Creature', zone='hand', ref='conditional-cost-creature')
        bounce = self.add(engine, 'Unsummon', zone='hand', ref='conditional-cost-bounce')
        foreign = self.add(engine, 'Generic Conditional Cost Creature', seat='B', zone='hand', ref='conditional-cost-other-player')
        self.ready(session, tapper, {'U':1})
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row.get('card') == spell.ref for row in actions))
        activation = next(row for row in actions if row['id'].startswith('activate:'+tapper.ref+':'))
        self.checkpoint(session)
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:B', {'action_id':activation['id'], 'cost_cards':[source.ref]}).ok)
        self.assertEqual(before, session.state.to_dict())
        accepted = session.act('pilot:A', {'action_id':activation['id'], 'cost_cards':[source.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        while session.state.priority_player != 'A':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        cast = next(row for row in actions if row.get('card') == spell.ref)
        from quorune.semantic_runtime.cast_costs import active_fixed_spell_cost_reductions
        self.assertTrue(active_fixed_spell_cost_reductions(engine, 'A', session.state.cards[spell.object_id]))
        self.assertEqual((), active_fixed_spell_cost_reductions(engine, 'B', session.state.cards[foreign.object_id]))
        self.assertEqual((), active_fixed_spell_cost_reductions(engine, 'A', session.state.cards[bounce.object_id]))
        self.replay(session, load=True)
        accepted = session.act('pilot:A', {'action_id':cast['id'], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('battlefield', session.state.cards[spell.object_id].zone)
        while session.state.priority_player != 'A':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        cast = next(row for row in actions if row.get('card') == bounce.ref)
        accepted = session.act('pilot:A', {'action_id':cast['id'], 'targets':[source.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        from quorune.semantic_runtime.cast_costs import active_fixed_spell_cost_reductions
        self.assertEqual((), active_fixed_spell_cost_reductions(engine, 'A', session.state.cards[spell.object_id]))
        for seat in 'BCD':
            public = session.packet('pilot:'+seat, full=True)['state']['players']['A']
            self.assertNotIn('hand', public)
        self.replay(session)

    def test_original_pristine_angel_cast_and_live_protection_follow_actual_tap_untap_and_replay(self):
        from quorune.ability_fragments import ProtectionSpec
        session = self.session(3630702)
        engine = session.engine
        angel = self.add(engine, 'Pristine Angel', zone='hand', ref='conditional-pristine')
        tapper = self.add(engine, 'Generic Conditional Keyword Tap Helper', ref='conditional-protection-tapper')
        self.add(engine, 'Generic Bound Body', ref='conditional-protection-other')
        island = self.add(engine, 'Generic Conditional Keyword Island', ref='conditional-protection-island')
        bounce = self.add(engine, 'Unsummon', zone='hand', ref='conditional-protection-bounce')
        offered = self.ready(session, angel, {'W':2, 'C':4})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id':offered['id'], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertTrue(any(isinstance(f, ProtectionSpec) for f in engine._effective_ability_fragments(angel)))
        while session.state.priority_player != 'A':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        mana_action = next(row for row in actions if row['id'].startswith('activate:'+island.ref+':'))
        accepted = session.act('pilot:A', {'action_id':mana_action['id']})
        self.assertTrue(accepted.ok, accepted.summary)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        protected_cast = next((row for row in actions if row.get('card') == bounce.ref), None)
        self.assertIsNotNone(protected_cast)
        before = session.state.to_dict()
        rejected = session.act('pilot:A', {'action_id':protected_cast['id'], 'targets':[angel.ref], 'pay':'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, session.state.to_dict())
        angel = session.state.cards[angel.object_id]
        activation = next(row for row in actions if row['id'].startswith('activate:'+tapper.ref+':'))
        accepted = session.act('pilot:A', {'action_id':activation['id'], 'cost_cards':[angel.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(angel.tapped)
        self.assertFalse(any(isinstance(f, ProtectionSpec) for f in engine._effective_ability_fragments(angel)))
        self.resolve(session)
        while session.state.priority_player != 'A':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        cast = next(row for row in actions if row.get('card') == bounce.ref)
        accepted = session.act('pilot:A', {'action_id':cast['id'], 'targets':[angel.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        choice = self.resolve(session)
        self.assertEqual('semantic.choice', choice.kind)
        self.replay(session, load=True)
        selected = session.act('pilot:A', {'action_id':'choose', 'choice':'apply', 'reason':'Untap the original Angel.'})
        self.assertTrue(selected.ok, selected.summary)
        self.resolve(session)
        self.assertFalse(angel.tapped)
        self.assertEqual('battlefield', session.state.cards[angel.object_id].zone)
        self.assertTrue(any(isinstance(f, ProtectionSpec) for f in engine._effective_ability_fragments(angel)))
        for seat in 'BCD':
            public = session.packet('pilot:'+seat, full=True)['state']['players']['A']
            self.assertNotIn('hand', public)
        self.replay(session)

    def test_original_k9_conditional_ward_survives_creation_then_ends_after_real_tap_and_replays(self):
        from quorune.trigger_participation import WardSpec
        session = self.session(3630701)
        engine = session.engine
        source = self.add(engine, 'K-9, Mark I', ref='conditional-k9')
        self.assert_whole_admission('K-9, Mark I')
        target = session.state.cards[session.state.players['A'].zones['command'][0]]
        engine.move_card(target.object_id, 'battlefield', controller='A', log=False)
        outsider = self.add(engine, 'Generic Bound Body', seat='C', ref='conditional-ward-outsider')
        first = self.add(engine, 'Unsummon', seat='B', zone='hand', ref='conditional-ward-first')
        second = self.add(engine, 'Unsummon', seat='B', zone='hand', ref='conditional-ward-second')
        engine.state.players['B'].mana_pool['U'] = 2
        offered = self.ready(session, source, {'U':1, 'C':1})
        self.checkpoint(session)
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:C', {'action_id':offered['id'], 'pay':'auto'}).ok)
        self.assertEqual(before, session.state.to_dict())
        self.assertTrue(any(isinstance(f, WardSpec) for f in engine._effective_ability_fragments(target)))
        self.assertFalse(any(isinstance(f, WardSpec) for f in engine._effective_ability_fragments(source)))
        self.assertFalse(any(isinstance(f, WardSpec) for f in engine._effective_ability_fragments(outsider)))
        while session.state.priority_player != 'B':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        cast = next(row for row in actions if row.get('card') == first.ref)
        accepted = session.act('pilot:B', {'action_id':cast['id'], 'targets':[target.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        ward = self.resolve(session)
        self.assertEqual('semantic.choice', ward.kind)
        self.replay(session, load=True)
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:C', {'action_id':'choose', 'pay':False, 'plan':'DECLINE_WARD'}).ok)
        self.assertEqual(before, session.state.to_dict())
        declined = session.act('pilot:B', {'action_id':'choose', 'pay':False, 'plan':'DECLINE_WARD', 'reason':'Preserve mana.'})
        self.assertTrue(declined.ok, declined.summary)
        self.resolve(session)
        self.assertEqual('battlefield', session.state.cards[target.object_id].zone)
        while session.state.priority_player != 'A':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        activation = next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
        accepted = session.act('pilot:A', {'action_id':activation['id'], 'targets':[target.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(source.tapped)
        self.assertFalse(any(isinstance(f, WardSpec) for f in engine._effective_ability_fragments(target)))
        self.resolve(session)
        while session.state.priority_player != 'B':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        cast = next(row for row in actions if row.get('card') == second.ref)
        accepted = session.act('pilot:B', {'action_id':cast['id'], 'targets':[target.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        replacement = self.resolve(session)
        self.assertEqual('replacement.order', replacement.kind)
        declined_replacement = next(row['id'] for row in replacement.payload_by_actor['A']['options'] if row['decline'])
        accepted = session.act('pilot:A', {'action_id':'choose', 'replacement':declined_replacement})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[target.object_id].zone)
        for seat in 'ACD':
            public = session.packet('pilot:'+seat, full=True)['state']['players']['B']
            self.assertNotIn('hand', public)
        self.replay(session)

    def test_original_taste_adds_live_bonus_to_foreign_creature_and_private_draw_removes_it(self):
        """Actual hand-empty timing adds +2/+0 to the existing +2/+0."""
        session = self.session(341001)
        engine = session.engine
        self.clear_initial_hand(session)
        target = self.add(engine, "Generic Bound Body", seat="B", ref="taste-target")
        other = self.add(engine, "Generic Bound Body", seat="C", ref="taste-other")
        aura = self.add(engine, "Taste for Mayhem", zone="hand")
        draw = self.add(engine, "Divination", zone="hand")
        self.assert_whole_admission(aura.printed_name)
        action = self.ready(session, aura, {"R": 1, "U": 3})
        self.checkpoint(session)
        before = session.state.to_dict()
        denied = session.act("pilot:B", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, session.state.to_dict())
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(4, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(1, len(session.state.players["A"].zones["hand"]))
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row.get("card") == draw.ref)
        accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, len(session.state.players["A"].zones["hand"]))
        self.assertEqual(6, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(2, engine._numeric_stat(other.object_id, "power"))
        self.resolve(session)
        self.assertEqual(2, len(session.state.players["A"].zones["hand"]))
        self.assertEqual(4, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual("battlefield", session.state.cards[aura.object_id].zone)
        self.assertEqual(target.object_id, session.state.cards[aura.object_id].attached_to)
        for seat in "BCD":
            packet = session.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            public = packet["state"]["players"]["A"]
            self.assertEqual(2, public["hand_n"])
            self.assertNotIn("hand", public)
        self.replay(session, load=True)

    def test_original_jetmir_additional_thresholds_stack_and_recompute_after_real_bounce(self):
        for count, before_power, after_power, removed in (
            (6, 4, 3, "trample"),
            (9, 5, 4, "double strike"),
        ):
            with self.subTest(count=count):
                session = self.session(341010 + count)
                engine = session.engine
                self.clear_initial_hand(session)
                bodies = [self.add(engine, "Generic Bound Body", ref=f"jetmir-body-{i}") for i in range(count - 1)]
                foreign = self.add(engine, "Generic Bound Body", seat="B", ref="jetmir-foreign")
                source = self.add(engine, "Jetmir, Nexus of Revels", zone="hand")
                bounce = self.add(engine, "Unsummon", zone="hand")
                self.assert_whole_admission(source.printed_name)
                action = self.ready(session, source, {"R": 1, "G": 1, "W": 1, "U": 2})
                self.checkpoint(session)
                accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
                self.assertTrue(accepted.ok, accepted.summary)
                self.resolve(session)
                self.assertEqual(before_power, engine._numeric_stat(bodies[0].object_id, "power"))
                self.assertIn(removed, engine._combat_keywords(session.state.cards[bodies[0].object_id]))
                self.assertEqual(2, engine._numeric_stat(foreign.object_id, "power"))
                self.assertNotIn(removed, engine._combat_keywords(foreign))
                actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                action = next(row for row in actions if row.get("card") == bounce.ref)
                accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [bodies[-1].ref], "pay": "auto"})
                self.assertTrue(accepted.ok, accepted.summary)
                self.resolve(session)
                self.assertEqual("hand", session.state.cards[bodies[-1].object_id].zone)
                self.assertEqual(after_power, engine._numeric_stat(bodies[0].object_id, "power"))
                self.assertNotIn(removed, engine._combat_keywords(session.state.cards[bodies[0].object_id]))
                self.assertIn("vigilance", engine._combat_keywords(session.state.cards[bodies[0].object_id]))
                self.replay(session, load=True)
