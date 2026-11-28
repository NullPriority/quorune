from __future__ import annotations

"""CR 702.6a: fixed nonmana Equip pays its full cost before attachment."""

from pathlib import Path
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_provoke_rules as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES = ("Murderer's Axe", 'Demonmail Hauberk', 'Piston Sledge', 'Nightmare Lash',
    "Shredder's Armor", 'Sigil of Distinction', "Inventor's Axe", "Dark Knight's Greatsword")
CAPABILITY = 'attachment.equip.fixed_nonmana'


class FixedNonmanaEquipTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.ProvokeTests.session
    add = controls.ProvokeTests.add
    prepare = controls.ProvokeTests.prepare
    checkpoint = controls.ProvokeTests.checkpoint
    replay = controls.ProvokeTests.replay
    binding = controls.ProvokeTests.binding
    private_replay = controls.ProvokeTests.private_replay
    priority = controls.ProvokeTests.priority
    actions = controls.ProvokeTests.actions
    advance = controls.ProvokeTests.advance
    cast_paid = controls.ProvokeTests.cast_paid

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'fixed-nonmana-equip.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/fixed-nonmana-equip-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Fixed nonmana Equip', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_complete_nonmana_equip_originals_have_normal_admission(self):
        for i, name in enumerate(NAMES):
            session = self.session(759010+i)
            source = self.add(session.engine, name, zone='hand')
            ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            bound=self.binding(session, source)
            self.assertTrue(bound['strict_capability_ready'],(name,bound))

    def forge_action(self, session, source, *, starts="Equip"):
        ability = next(a for a in session.engine._activated_abilities(source)
            if a.oracle_line.startswith(starts))
        return f"activate:{source.ref}:{ability.ability_id}"

    def forge_command(self, session, action, target, *, price=None, seat="A", valid=True):
        command = {"action_id": action, "targets": [target.ref], "pay": "auto"}
        if price is not None:
            command["equip_payment"] = price
        before = authoritative_state_hash(session.state)
        result = session.act("pilot:" + seat, command)
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:
            self.assertEqual(before, authoritative_state_hash(session.state))

    def test_forge_anew_returns_only_owned_equipment_and_zeroes_nonmana_equip(self):
        from quorune.turn_history import equip_activated_this_turn

        session = self.session(759080)
        forge = self.add(session.engine, "Forge Anew", zone="hand")
        returned = self.add(session.engine, "Murderer's Axe", zone="graveyard")
        non_equipment = self.add(session.engine, "Sol Ring", zone="graveyard")
        foreign = self.add(session.engine, "Short Sword", seat="B", zone="graveyard")
        target = self.add(session.engine, "Generic Bound Body")
        keep = self.add(session.engine, "Generic Bound Growth", zone="hand", ref="forge-keep")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand", ref="forge-private")
        self.prepare(session, mana={"C": 2, "W": 1})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": "cast:" + forge.ref, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        pending = self.resolve(session)
        self.assertEqual("semantic.target", pending.kind)
        for illegal in (non_equipment, foreign):
            before = authoritative_state_hash(session.state)
            result = session.act("pilot:A", {"action_id": "choose", "targets": [illegal.ref]})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "targets": [returned.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        returned = session.state.cards[returned.object_id]
        self.assertEqual("battlefield", returned.zone)
        self.assertIsNone(returned.attached_to)
        self.assertFalse(equip_activated_this_turn(session.state, "A"))

        action = self.forge_action(session, returned)
        offer = next(row for row in self.actions(session) if row["id"] == action)
        options = offer["equip_payment_options"]
        self.assertEqual(["printed", "zero"], [row["id"] for row in options])
        self.assertIn("choose_cost", options[0]["cost_summary"])
        self.assertNotIn("choose_cost", options[1]["cost_summary"])
        public = session.packet("pilot:A", full=True)["decision"]["legal_actions"]
        form = next(row["form"] for row in public if row["id"] == action)
        self.assertEqual("equip_payment", form["variants"]["field"])
        printed_form, zero_form = form["variants"]["options"]
        self.assertIn("cost_cards", {field["name"] for field in printed_form["fields"]})
        self.assertNotIn("cost_cards", {field["name"] for field in zero_form["fields"]})
        self.forge_command(session, action, target, price="free", valid=False)
        self.forge_command(session, action, target, price="zero", seat="B", valid=False)
        self.forge_command(session, action, target, price="zero")
        self.assertTrue(equip_activated_this_turn(session.state, "A"))
        self.assertEqual("hand", session.state.cards[keep.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        returned = session.state.cards[returned.object_id]
        self.assertEqual(target.object_id, returned.attached_to)
        self.forge_command(session, action, target, price="zero", valid=False)
        self.private_replay(session, private)

    def test_forge_anew_earlier_paid_equip_consumes_zero_but_restricted_equip_gets_instant_timing(self):
        from quorune.turn_history import equip_activated_this_turn

        session = self.session(759081)
        forge = self.add(session.engine, "Forge Anew", zone="hand")
        sword = self.add(session.engine, "Short Sword")
        blade = self.add(session.engine, "Dúnedain Blade")
        human = self.add(session.engine, "Elite Vanguard")
        rabbit = self.add(session.engine, "Rabbit Battery")
        bolt = self.add(session.engine, "Lightning Bolt", zone="hand")
        self.prepare(session, mana={"C": 4, "R": 1, "W": 1})
        self.checkpoint(session)
        self.forge_command(session, self.forge_action(session, sword), human)
        self.assertTrue(equip_activated_this_turn(session.state, "A"))
        self.assertIsNone(self.resolve(session))
        result = session.act("pilot:A", {"action_id": "cast:" + forge.ref, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))

        result = session.act("pilot:A", {"action_id": "cast:" + bolt.ref, "targets": ["D"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.priority(session, "A")
        self.assertTrue(session.state.stack)
        blade = session.state.cards[blade.object_id]
        action = self.forge_action(session, blade, starts="Equip Human")
        offer = next(row for row in self.actions(session) if row["id"] == action)
        self.assertNotIn("equip_payment_options", offer)
        self.forge_command(session, action, human, price="zero", valid=False)
        self.forge_command(session, action, rabbit, valid=False)
        self.forge_command(session, action, human)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(human.object_id, session.state.cards[blade.object_id].attached_to)
        self.assertEqual(0, session.state.players["A"].mana_pool.get("C", 0))
        self.replay(session)

    def test_forge_anew_restricted_zero_respects_reconfigure_and_previous_record_mode(self):
        from quorune.model import TurnHistory, TurnHistoryEvent
        from quorune.turn_history import reset_turn_history

        legacy = TurnHistory(turn_sequence=1)
        self.assertNotIn("equip_activation_history_version", legacy.to_dict())
        self.assertNotIn("equip_activation_history_version", reset_turn_history(legacy, 2).to_dict())
        sealed = TurnHistory(turn_sequence=1, equip_activation_history_version=1,
            events=[TurnHistoryEvent("first_equip_activation", actor="A", object_incarnation="o1")])
        self.assertEqual(sealed, TurnHistory.from_dict(sealed.to_dict()))
        self.assertEqual([], reset_turn_history(sealed, 2).events)
        with self.assertRaises(ValueError):
            TurnHistory(turn_sequence=1, events=sealed.events)

        session = self.session(759082)
        forge = self.add(session.engine, "Forge Anew")
        self.assertTrue(self.binding(session, forge)["strict_capability_ready"])
        blade = self.add(session.engine, "Dúnedain Blade")
        battery = self.add(session.engine, "Rabbit Battery")
        opposing_forge = self.add(session.engine, "Forge Anew", seat="B", ref="opposing-forge")
        opposing_sword = self.add(session.engine, "Short Sword", seat="B", ref="opposing-short-sword")
        opposing_bear = self.add(session.engine, "Generic Bound Body", seat="B", ref="opposing-equip-target")
        human = self.add(session.engine, "Elite Vanguard")
        bolt = self.add(session.engine, "Lightning Bolt", zone="hand")
        self.prepare(session, mana={"R": 1})
        session.state.players["B"].mana_pool["C"] = 2
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": "cast:" + bolt.ref, "targets": ["D"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.priority(session, "A")
        self.assertTrue(session.state.stack)
        reconfigure = self.forge_action(session, battery, starts="Reconfigure")
        self.assertFalse(any(row["id"] == reconfigure for row in self.actions(session)))
        self.forge_command(session, reconfigure, human, price="zero", valid=False)
        restricted = self.forge_action(session, blade, starts="Equip Human")
        row = next(row for row in self.actions(session) if row["id"] == restricted)
        self.assertEqual(["zero"], [option["id"] for option in row["equip_payment_options"]])
        self.forge_command(session, restricted, human, price="zero")
        self.priority(session, "B")
        self.assertTrue(session.state.stack)
        self.assertIn(opposing_forge.object_id, session.state.cards)
        self.forge_command(session, self.forge_action(session, opposing_sword), opposing_bear,
            price="zero", seat="B", valid=False)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(human.object_id, session.state.cards[blade.object_id].attached_to)
        self.replay(session)

    def test_forge_anew_alternate_exposes_and_pays_the_actual_activation_tax(self):
        from quorune.mana_provenance import add_mana

        session = self.session(759083)
        self.add(session.engine, "Forge Anew")
        self.add(session.engine, "Suppression Field", seat="B")
        source = self.add(session.engine, "Short Sword")
        target = self.add(session.engine, "Elite Vanguard")
        add_mana(session.state.players["A"], {"C": 2}, snow=True)
        self.prepare(session)
        self.checkpoint(session)
        action = self.forge_action(session, source)
        public = session.packet("pilot:A", full=True)["decision"]["legal_actions"]
        row = next(row for row in public if row["id"] == action)
        option, = row["equip_payment_options"]
        self.assertEqual("zero", option["id"])
        self.assertEqual({"GENERIC": 2}, option["cost_summary"]["m"])
        zero, = row["form"]["variants"]["options"]
        self.assertTrue(all(field["name"] != "mana_pool_payment" for field in row["form"]["fields"]))
        self.assertIn("mana_pool_payment", {field["name"] for field in zero["fields"]})
        self.forge_command(session, action, target, price="printed", valid=False)
        self.forge_command(session, action, target, price="zero")
        self.assertEqual(0, session.state.players["A"].mana_pool.get("C", 0))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(target.object_id, session.state.cards[source.object_id].attached_to)
        self.replay(session)

    def test_forge_anew_rejects_unmodeled_permission_text(self):
        import quorune.compiler.runtime_templates as runtime
        from quorune.compiler.action_permission_templates import static_action_permission_handler

        orig = static_action_permission_handler
        bad = (
            "As long as it's your turn, you may activate reconfigure abilities any time you could cast an instant.",
            "You may pay {0} rather than pay the equip cost of the second equip ability you activate during each of your turns.",
        )
        for line in bad:
            self.assertIsNone(orig(line))
        def without_equip(text, **kwargs):
            return None if "equip" in text.casefold() else orig(text, **kwargs)

        with patch.object(runtime, "static_action_permission_handler", side_effect=without_equip):
            ir = compile_oracle_card(self.db.lookup("Forge Anew"), capability_registry=self.registry,
                capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)

    def equip(self, session, source, target, *, costs=(), valid=True):
        self.priority(session, 'A')
        abilities = [a for a in session.engine._activated_abilities(source)
            if a.builtin_semantic_key == 'builtin:equip']
        self.assertEqual(1, len(abilities))
        action = 'activate:'+source.ref+':'+abilities[0].ability_id
        command = {'action_id':action, 'targets':[target.ref], 'pay':'auto'}
        if costs:command['cost_cards'] = [c.ref for c in costs]
        if valid:self.assertTrue(any(r['id']==action for r in self.actions(session)))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:self.assertEqual(before, authoritative_state_hash(session.state))

    def stats(self, session, target):
        return (session.engine._numeric_stat(target.object_id, 'power'),
            session.engine._numeric_stat(target.object_id, 'toughness'))

    def resolve(self, session):
        for _ in range(16):
            if session.state.pending_decision is not None and session.state.pending_decision.kind=='trigger.order':
                principal=session.pending_principals()[0]
                seat=principal.removeprefix('pilot:')
                refs=[item.ref for item in session.state.pending_trigger_batches[0].items if item.controller==seat]
                self.assertTrue(refs)
                result=session.act(principal,{'action_id':'order','triggers':refs})
                self.assertTrue(result.ok,result.summary)
            decision=controls.ProvokeTests.resolve(self,session)
            if decision is None or decision.kind!='trigger.order':return decision
        self.fail('Equipment entry trigger ordering did not settle')

    def cast_equipment(self, session, source, payment, target=None, **choices):
        self.priority(session, 'A')
        command = {'action_id':'cast:'+source.ref,'pay':'manual','payment':payment,**choices}
        self.assertTrue(any(r['id']==command['action_id'] for r in self.actions(session)))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        decision = self.resolve(session)
        if target is not None:
            self.assertIsNotNone(decision)
            self.assertEqual('semantic.target', decision.kind)
            result = session.act('pilot:A', {'action_id':'choose','targets':[target.ref]})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(session))
        else:self.assertIsNone(decision)

    def test_actual_axe_discards_owned_hand_card_before_attach_and_replays_privately(self):
        session = self.session(759030)
        source = self.add(session.engine, "Murderer's Axe", zone='hand')
        target = self.add(session.engine, 'Generic Bound Body')
        cost = self.add(session.engine, 'Lightning Bolt', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C':4});self.checkpoint(session)
        self.cast_equipment(session, source, {'C':4})
        self.equip(session, source, target, costs=[private], valid=False)
        self.equip(session, source, target, costs=[cost,cost], valid=False)
        source=session.state.cards[source.object_id]
        target=session.state.cards[target.object_id]
        cost=session.state.cards[cost.object_id]
        self.equip(session, source, target, costs=[cost])
        self.assertEqual('graveyard', cost.zone)
        self.assertIsNone(source.attached_to)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(target.object_id, session.state.cards[source.object_id].attached_to)
        self.assertEqual((4,8), self.stats(session,target))
        self.private_replay(session, private)

    def test_actual_hauberk_can_pay_its_target_then_leave_the_cost_paid(self):
        session = self.session(759031)
        source = self.add(session.engine, 'Demonmail Hauberk', zone='hand')
        target = self.add(session.engine, 'Generic Bound Body')
        fodder = self.add(session.engine, 'Generic Bound Body', ref='hauberk-fodder')
        self.prepare(session, mana={'C':4});self.checkpoint(session)
        self.cast_equipment(session,source,{'C':4})
        self.equip(session,source,target,costs=[fodder]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',fodder.zone)
        self.assertEqual((6,8),self.stats(session,target))
        self.equip(session,source,target,costs=[target])
        self.assertEqual('graveyard',target.zone)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.assertIsNone(session.state.cards[source.object_id].attached_to)
        self.replay(session)

    def test_actual_sledge_entry_attaches_but_sacrificed_source_cannot_attach(self):
        session=self.session(759032)
        source=self.add(session.engine,'Piston Sledge',zone='hand')
        target=self.add(session.engine,'Generic Bound Body')
        second=self.add(session.engine,'Generic Bound Body',ref='sledge-target-two')
        self.prepare(session,mana={'C':3});self.checkpoint(session)
        self.cast_equipment(session,source,{'C':3},target)
        self.assertEqual(target.object_id,source.attached_to)
        self.assertEqual((5,7),self.stats(session,target))
        self.equip(session,source,second,costs=[source])
        self.assertEqual('graveyard',source.zone)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertIsNone(session.state.cards[source.object_id].attached_to)
        self.assertEqual((2,6),self.stats(session,second))
        self.replay(session)

    def test_actual_lash_pays_three_life_and_keeps_its_swamp_count(self):
        session=self.session(759033)
        source=self.add(session.engine,'Nightmare Lash',zone='hand')
        target=self.add(session.engine,'Generic Bound Body')
        self.add(session.engine,'Swamp',ref='lash-swamp-one')
        self.add(session.engine,'Swamp',ref='lash-swamp-two')
        self.prepare(session,mana={'C':4});self.checkpoint(session)
        self.cast_equipment(session,source,{'C':4})
        self.equip(session,source,target)
        self.assertEqual(37,session.state.players['A'].life)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(target.object_id,session.state.cards[source.object_id].attached_to)
        self.assertEqual((4,8),self.stats(session,target))
        self.replay(session)

    def test_actual_shredder_excludes_self_and_lands_and_is_once_each_turn(self):
        session=self.session(759034)
        source=self.add(session.engine,"Shredder's Armor",zone='hand')
        target=self.add(session.engine,'Generic Bound Body')
        second=self.add(session.engine,'Generic Bound Body',ref='armor-target-two')
        land=self.add(session.engine,'Swamp')
        fodder=self.add(session.engine,'Sol Ring')
        self.prepare(session,mana={'C':1,'B':1});self.checkpoint(session)
        self.cast_equipment(session,source,{'C':1,'B':1},target)
        self.equip(session,source,second,costs=[source],valid=False)
        self.equip(session,source,second,costs=[land],valid=False)
        source=session.state.cards[source.object_id]
        fodder=session.state.cards[fodder.object_id]
        self.equip(session,source,second,costs=[fodder])
        self.assertEqual('graveyard',fodder.zone)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        source=session.state.cards[source.object_id]
        self.assertEqual(second.object_id,source.attached_to)
        self.assertEqual((4,7),self.stats(session,second))
        self.equip(session,source,target,costs=[second],valid=False)
        self.replay(session)

    def test_actual_sigil_spends_charge_counters_and_updates_equipped_bonus(self):
        session=self.session(759035)
        source=self.add(session.engine,'Sigil of Distinction',zone='hand')
        target=self.add(session.engine,'Generic Bound Body')
        self.prepare(session,mana={'C':3});self.checkpoint(session)
        self.cast_equipment(session,source,{'C':3},x=3)
        self.assertEqual(3,source.counters.get('charge',0))
        for remaining in (2,1,0):
            self.equip(session,source,target)
            self.assertEqual(remaining,source.counters.get('charge',0))
            self.assertIsNone(self.resolve(session))
            self.assertEqual((2+remaining,6+remaining),self.stats(session,target))
        self.equip(session,source,target,valid=False)
        self.replay(session,load=True)

    def test_actual_inventor_pays_entry_energy_to_move_its_attachment(self):
        session=self.session(759036)
        source=self.add(session.engine,"Inventor's Axe",zone='hand')
        target=self.add(session.engine,'Generic Bound Body')
        second=self.add(session.engine,'Generic Bound Body',ref='inventor-second')
        self.prepare(session,mana={'R':1});self.checkpoint(session)
        self.cast_equipment(session,source,{'R':1},target)
        self.assertEqual(2,session.state.players['A'].energy)
        self.equip(session,source,second)
        self.assertEqual(0,session.state.players['A'].energy)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(second.object_id,session.state.cards[source.object_id].attached_to)
        self.assertEqual((4,6),self.stats(session,second))
        self.equip(session,session.state.cards[source.object_id],target,valid=False)
        self.replay(session)

    def test_actual_greatsword_job_select_and_life_equip_keep_knight_and_limit(self):
        session=self.session(759037)
        source=self.add(session.engine,"Dark Knight's Greatsword",zone='hand')
        target=self.add(session.engine,'Generic Bound Body')
        self.prepare(session,mana={'C':2,'B':1});self.checkpoint(session)
        self.cast_equipment(session,source,{'C':2,'B':1})
        tokens=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token]
        self.assertEqual(1,len(tokens))
        self.assertEqual(tokens[0].object_id,source.attached_to)
        self.assertEqual((4,1),self.stats(session,tokens[0]))
        self.equip(session,source,target)
        self.assertEqual(37,session.state.players['A'].life)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual((5,6),self.stats(session,target))
        data=session.engine._effective_card_data(session.state.cards[target.object_id])
        self.assertIn('knight',session.engine._type_parts(data['type_line'])[1])
        self.equip(session,session.state.cards[source.object_id],tokens[0],valid=False)
        self.replay(session)

    def test_closed_nonmana_equip_grammar_rejects_alternatives_and_variable_prices(self):
        from quorune.rules.attachment_actions import fixed_equip_ability_spec
        from quorune.compiler.keyword_templates import keyword_mechanics
        for text in ('Equip—Sacrifice another nonland permanent. Activate only once each turn.',
            'Chaosbringer — Equip—Pay 3 life. Activate only once each turn.'):
            self.assertEqual(('equip',),keyword_mechanics(text,('Equip',)))
        for text in ('Equip—Pay {3} or discard a card.', 'Equip—Pay X life.',
            'Equip—Sacrifice all creatures.', 'Equip—Discard two cards.', 'Equip—Pay 0 life.'):
            self.assertIsNone(fixed_equip_ability_spec(text),text)

    def test_fresh_nonmana_lowering_and_life_price_omissions_are_killed(self):
        import quorune.abilities as abilities
        import quorune.engine as engine_module
        from quorune.rules.activation.resolution import BuiltinActivationResolution
        original=abilities._parse_cost
        def free_life(text,name):
            result=original(text,name)
            return replace(result,life_payment=0) if text.casefold()=='pay 3 life' else result
        original_resolution=engine_module.builtin_activation_resolution
        def omit_attach(key,controller):
            if key=='builtin:equip':return BuiltinActivationResolution(effects=(),note='Equip attachment omitted')
            return original_resolution(key,controller)
        for owner,replacement,method in (
            ('quorune.compiler.nonmana_equip_nodes.fixed_nonmana_equip_keyword_node',lambda **kwargs:None,
             'test_complete_nonmana_equip_originals_have_normal_admission'),
            ('quorune.abilities._parse_cost',free_life,
             'test_actual_lash_pays_three_life_and_keeps_its_swamp_count'),
            ('quorune.engine.builtin_activation_resolution',omit_attach,
             'test_actual_lash_pays_three_life_and_keeps_its_swamp_count')):
            with patch(owner,replacement):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_typed_nonmana_equip_retains_an_independent_runtime_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.compiler.nonmana_equip_nodes import is_closed_nonmana_equip_program
        session=self.session(759050)
        source=self.add(session.engine,'Nightmare Lash',zone='hand')
        programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.event=='activate' and CAPABILITY in p.capability_dependencies]
        self.assertEqual(1,len(programs))
        program=programs[0]
        self.assertTrue(is_closed_nonmana_equip_program(program))
        self.assertFalse(is_closed_nonmana_equip_program(replace(program,
            cost_schema={**program.cost_schema,'life_payment':0})))
        self.assertFalse(is_closed_nonmana_equip_program(replace(program,effects=[])))
        deps=tuple(d for d in program.capability_dependencies if d!=CAPABILITY)
        for effects in (program.effects,[]):
            stripped=replace(program,capability_dependencies=deps,effects=effects,
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])

    def test_verified_owner_withdrawal_keeps_complete_originals_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['withdrawn fixed nonmana Equip owner'])
        registry=CapabilityRegistry(raw)
        decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAPABILITY+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))


if __name__ == '__main__':
    unittest.main()
