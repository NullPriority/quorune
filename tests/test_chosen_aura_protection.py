from __future__ import annotations

"""CR 614.12a/702.16n: source designation and protection belong to the Aura."""

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_attached_protection_exceptions as controls
from quorune.rules.evidence_storage import read_evidence_document


class ChosenAuraProtectionTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.AttachedProtectionExceptionTests.session
    add=controls.AttachedProtectionExceptionTests.add
    prepare=controls.AttachedProtectionExceptionTests.prepare
    checkpoint=controls.AttachedProtectionExceptionTests.checkpoint
    resolve=controls.AttachedProtectionExceptionTests.resolve
    replay=controls.AttachedProtectionExceptionTests.replay
    binding=controls.AttachedProtectionExceptionTests.binding
    priority=controls.AttachedProtectionExceptionTests.priority
    cast=controls.AttachedProtectionExceptionTests.cast
    private_replay=controls.AttachedProtectionExceptionTests.private_replay
    actions=controls.AttachedProtectionExceptionTests.actions

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'chosen-protection.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
                                ROOT/'tests/fixtures/chosen-aura-protection-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Chosen Aura protection',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def choose(self,session,color):
        self.assertEqual('replacement.order',self.resolve(session).kind)
        option=next(row['id'] for row in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if row['id'].endswith(':'+color))
        result=session.act('pilot:A',{'action_id':'choose','choices':{'replacement':option}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))

    def test_actual_pentarch_entry_choice_belongs_to_aura_controller_and_draw_remains_private(self):
        from quorune.ability_fragments import protection_specs,ability_fragment_from_dict
        session=self.session(750400);body=self.add(session.engine,'Grizzly Bears',seat='B')
        aura=self.add(session.engine,'Pentarch Ward',zone='hand')
        matching=self.add(session.engine,'Humble',zone='hand')
        nonmatching=self.add(session.engine,'Boomerang',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,aura)['strict_capability_ready'],'Complete original Pentarch Ward must admit')
        self.prepare(session,mana={'W':2,'C':3});session.state.players['B'].mana_pool['U']=2;self.checkpoint(session)
        hands={seat:len(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
        before_hand=set(session.state.players['A'].zones['hand'])
        self.cast(session,'A',aura,[body.ref],{'W':1,'C':2})
        self.assertEqual('replacement.order',self.resolve(session).kind)
        self.assertEqual(['pilot:A'],list(session.pending_principals()))
        self.assertEqual('stack',session.state.cards[aura.object_id].zone)
        self.assertEqual(hands['A']-1,len(session.state.players['A'].zones['hand']))
        session=self.replay(session,load=True)
        option=next(r['id'] for r in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if r['id'].endswith(':W'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'choose','choices':{'replacement':option}}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','choices':{'replacement':option}});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session))
        current=session.state.cards[aura.object_id]
        self.assertEqual(('battlefield',body.object_id,'W'),(current.zone,current.attached_to,current.annotations.get('chosen_color')))
        specs=protection_specs(ability_fragment_from_dict(r) for r in session.engine._effective_card_data(session.state.cards[body.object_id]).get('ability_fragments',()))
        self.assertEqual(1,len(specs),'The actual registered layer-six grant must produce one protection instance')
        spec,=specs;self.assertEqual(('W',current.logical_object_id),(spec.quality,spec.exempt_aura_incarnation))
        self.assertEqual(hands['A'],len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':self.assertEqual(hands[seat],len(session.state.players[seat].zones['hand']))
        drawn=set(session.state.players['A'].zones['hand'])-before_hand;self.assertEqual(1,len(drawn))
        revealed=session.state.cards[next(iter(drawn))].ref
        self.assertIn(revealed,json.dumps(session.packet('pilot:A',full=True)))
        for seat in 'BCD':self.assertNotIn(revealed,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.priority(session,'A');before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+matching.ref,'targets':[body.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'B',nonmatching,[body.ref],{'U':2});session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(('hand','graveyard'),(session.state.cards[body.object_id].zone,session.state.cards[aura.object_id].zone))
        self.private_replay(session,private)

    def test_closed_handler_uses_only_the_retained_source_choice_and_current_aura(self):
        from dataclasses import replace
        from quorune.semantic_runtime.chosen_aura_protection import SourceChosenAuraProtectionHandler
        from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.replacement.immutable import FrozenMap
        handler=SourceChosenAuraProtectionHandler()
        descriptor={'handler_id':handler.handler_id,'schema_version':1,'event':handler.event,'subject':'source_attached_creature'}
        context=ContinuousEffectSourceContext('aura','AURA','A',1,'component',
            attached_object=ContinuousObjectIdentity('body','body@1'),source_logical_object_id='aura@1',
            source_designations=FrozenMap({'chosen_color':'R'}))
        effects=handler.lower(descriptor,context)
        self.assertEqual(1,len(effects));fragment=effects[0].operations[1].value
        self.assertEqual(('R','aura@1'),(fragment['value']['quality'],fragment['value']['exempt_aura_incarnation']))
        self.assertEqual((),handler.lower(descriptor,replace(context,attached_object=None)))
        for change in ({'schema_version':True},{'subject':'source_controller_creatures'},{'extra':0}):
            with self.assertRaises(ValueError):handler.validate({**descriptor,**change})
        for color in (None,'','colorless','WU','purple'):
            with self.assertRaises(ValueError):handler.lower(descriptor,replace(context,source_designations=FrozenMap({'chosen_color':color})))
        with self.assertRaises(ValueError):handler.lower(descriptor,replace(context,source_logical_object_id=None))
        other=handler.lower(descriptor,replace(context,source_controller='B',source_logical_object_id='aura@2'))[0].operations[1].value
        self.assertEqual(('R','aura@2'),(other['value']['quality'],other['value']['exempt_aura_incarnation']))

    def test_fresh_actual_chosen_compiler_and_registered_grant_omissions_are_killed(self):
        from quorune.compiler.attached_protection_exceptions import attached_protection_exception_handler
        method='test_actual_pentarch_entry_choice_belongs_to_aura_controller_and_draw_remains_private'
        def omitted(line,**kwargs):
            return None if 'the chosen color' in line else attached_protection_exception_handler(line,**kwargs)
        with patch('quorune.compiler.attached_protection_exceptions.attached_protection_exception_handler',omitted):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        with patch('quorune.semantic_runtime.chosen_aura_protection.SourceChosenAuraProtectionHandler.lower',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_canonically_verified_chosen_and_entry_owner_withdrawals_and_minimum_strip_reject(self):
        import json
        from dataclasses import replace
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        cap='protection.attachment.source_chosen_color'
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        def verified(value):
            registry=CapabilityRegistry(value);index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry));return registry
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Pentarch Ward'),capability_registry=verified(raw),capability_profile='commander_review').status)
        for dependency in (cap,'protection.attachment.source_aura_exception','zone.entry.public_designation'):
            data=json.loads(json.dumps(raw));next(r for r in data['capabilities'] if r['id']==dependency).update(status='blocked',blockers=['withdrawn chosen Aura owner'])
            ir=compile_oracle_card(self.db.lookup('Pentarch Ward'),capability_registry=verified(data),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            blockers=[b for residual in ir.material_residuals for b in residual.blockers]
            self.assertTrue(any('status:'+dependency+':blocked' in b for b in blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        session=self.session(750401);source=self.add(session.engine,'Pentarch Ward',zone='hand')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
        deps=tuple(c for c in program.capability_dependencies if c!=cap)
        changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_actual_flickering_return_ends_the_grant_and_recast_selects_for_a_new_incarnation(self):
        session=self.session(750410);body=self.add(session.engine,'Grizzly Bears')
        aura=self.add(session.engine,'Flickering Ward',zone='hand')
        self.assertTrue(self.binding(session,aura)['strict_capability_ready'])
        self.prepare(session,mana={'W':3});self.checkpoint(session)
        self.cast(session,'A',aura,[body.ref],{'W':1});self.choose(session,'U')
        from quorune.ability_fragments import protection_specs,ability_fragment_from_dict
        spec,=protection_specs(ability_fragment_from_dict(r) for r in session.engine._effective_card_data(session.state.cards[body.object_id]).get('ability_fragments',()))
        self.assertEqual('U',spec.quality)
        old=session.state.cards[aura.object_id].logical_object_id
        self.priority(session,'A');action=next(row for row in self.actions(session) if row['id'].startswith('activate:'+aura.ref+':'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'manual','payment':{'W':1}});self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[aura.object_id].zone)
        self.assertFalse(any(r.get('kind')=='protection' for r in session.engine._effective_card_data(session.state.cards[body.object_id]).get('ability_fragments',())))
        self.cast(session,'A',session.state.cards[aura.object_id],[body.ref],{'W':1})
        self.assertEqual('replacement.order',self.resolve(session).kind)
        session=self.replay(session,load=True);self.choose(session,'W')
        current=session.state.cards[aura.object_id]
        self.assertNotEqual(old,current.logical_object_id)
        self.assertEqual(('battlefield',body.object_id,'W'),(current.zone,current.attached_to,current.annotations.get('chosen_color')))
        self.assertEqual(0,session.state.players['A'].mana_pool.get('W',0));self.replay(session)

    def test_actual_cho_manno_flash_enters_on_another_turn_and_keeps_the_source_choice(self):
        session=self.session(750411);body=self.add(session.engine,'Grizzly Bears',seat='B')
        aura=self.add(session.engine,"Cho-Manno's Blessing",zone='hand')
        self.assertTrue(self.binding(session,aura)['strict_capability_ready'])
        self.prepare(session,actor='B',active='B');session.state.players['A'].mana_pool['W']=2;self.checkpoint(session)
        self.priority(session,'A')
        self.assertTrue(any(row['id']=='cast:'+aura.ref for row in self.actions(session)))
        self.cast(session,'A',aura,[body.ref],{'W':2});session=self.replay(session,load=True);self.choose(session,'B')
        current=session.state.cards[aura.object_id]
        self.assertEqual(('B','battlefield',body.object_id,'B'),(session.state.active_player,current.zone,current.attached_to,current.annotations.get('chosen_color')))
        self.assertEqual(0,session.state.players['A'].mana_pool.get('W',0));self.replay(session)

class BenevolentBlessingTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = ChosenAuraProtectionTests.session
    add = ChosenAuraProtectionTests.add
    prepare = ChosenAuraProtectionTests.prepare
    checkpoint = ChosenAuraProtectionTests.checkpoint
    resolve = ChosenAuraProtectionTests.resolve
    replay = ChosenAuraProtectionTests.replay
    binding = ChosenAuraProtectionTests.binding
    priority = ChosenAuraProtectionTests.priority
    cast = ChosenAuraProtectionTests.cast
    actions = ChosenAuraProtectionTests.actions
    choose = ChosenAuraProtectionTests.choose
    attack = controls.AttachedProtectionExceptionTests.attack
    blockers = controls.AttachedProtectionExceptionTests.blockers
    block = controls.AttachedProtectionExceptionTests.block
    advance = controls.AttachedProtectionExceptionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        folder = Path(cls.temp.name)
        fixtures = ROOT / "tests" / "fixtures"
        base = json.loads((fixtures / "bound-effect-program-cards.json").read_bytes())
        support = {"Generic Bound Commander", "Generic Bound Plains"}
        base["cards"] = [row for row in base["cards"] if row["name"] in support]
        assert {row["name"] for row in base["cards"]} == support
        base["rulings"] = []
        base_path = folder / "support.json"
        base_path.write_text(json.dumps(base), encoding="utf-8")
        database = folder / "benevolent-blessing.sqlite3"
        build_fixture_database([base_path, fixtures / "benevolent-blessing-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        leader = "Generic Bound Commander"
        cls.deck = DeckDefinition("Already attached protection", [
            DeckEntry(leader, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [leader])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def attach_for_setup(self, game, attachment, body):
        from quorune.attachments import attach_objects

        attach_objects(game.state.cards, attachment, body, players=game.state.players,
                       source_timestamp=game.engine._next_zone_timestamp())

    def reject(self, game, actor, action):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, action)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def white_blessing(self, game, aura, body):
        self.cast(game, "A", aura, [body.ref], {"C": 1, "W": 1})
        self.assertEqual("replacement.order", self.resolve(game).kind)
        self.assertEqual(["pilot:A"], list(game.pending_principals()))
        option = next(row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                      if row["id"].endswith(":W"))
        self.reject(game, "B", {"action_id": "choose", "choices": {"replacement": option}})
        game = self.replay(game, load=True)
        self.choose(game, "W")
        current = game.state.cards[aura.object_id]
        self.assertEqual(("battlefield", body.object_id, "W"),
                         (current.zone, current.attached_to, current.annotations.get("chosen_color")))
        return game

    def test_actual_benevolent_blessing_keeps_prior_own_attachments_and_rechecks_control(self):
        game = self.session(702_160_501)
        creature = self.add(game.engine, "Grizzly Bears")
        blessing = self.add(game.engine, "Benevolent Blessing", zone="hand")
        friend_aura = self.add(game.engine, "Holy Strength", ref="AURA-OURS")
        friend_gear = self.add(game.engine, "Barbed Spike", ref="GEAR-OURS")
        other_aura = self.add(game.engine, "Holy Strength", seat="C", ref="AURA-THEIRS")
        other_gear = self.add(game.engine, "Barbed Spike", seat="C", ref="GEAR-THEIRS")
        theft = self.add(game.engine, "Steal Artifact", seat="B", zone="hand")
        target_spell = self.add(game.engine, "Swords to Plowshares", seat="B", zone="hand")
        private = self.add(game.engine, "Holy Strength", seat="D", zone="hand", ref="PRIVATE-D")
        for attachment in (friend_aura, friend_gear, other_aura, other_gear):
            self.attach_for_setup(game, attachment, creature)
        for original in (blessing, friend_aura, friend_gear, theft, target_spell):
            result = self.binding(game, original)
            self.assertTrue(result["strict_capability_ready"], result["blockers"])
        self.prepare(game, actor="B", active="B", mana={"C": 2, "W": 1, "U": 2})
        game.state.players["A"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        game = self.white_blessing(game, blessing, creature)
        lookup = game.state.cards
        self.assertEqual(creature.object_id, lookup[friend_aura.object_id].attached_to)
        self.assertEqual(creature.object_id, lookup[friend_gear.object_id].attached_to)
        self.assertEqual("graveyard", lookup[other_aura.object_id].zone)
        self.assertEqual(("battlefield", None), (lookup[other_gear.object_id].zone, lookup[other_gear.object_id].attached_to))
        self.priority(game, "B")
        self.reject(game, "B", {"action_id": "cast:" + target_spell.ref, "targets": [creature.ref], "pay": "auto"})
        self.cast(game, "B", theft, [friend_gear.ref], {"C": 2, "U": 2})
        self.assertIsNone(self.resolve(game))
        friend = game.state.cards[friend_gear.object_id]
        self.assertEqual(("B", "battlefield", None), (friend.controller, friend.zone, friend.attached_to))
        self.assertEqual(creature.object_id, game.state.cards[friend_aura.object_id].attached_to)
        for viewer in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_benevolent_blessing_rejects_new_attachment_targets_and_white_blockers(self):
        game = self.session(702_160_502)
        creature = self.add(game.engine, "Grizzly Bears")
        companion = self.add(game.engine, "Grizzly Bears", ref="ORDINARY-ATTACKER")
        opponent = self.add(game.engine, "Isamaru, Hound of Konda", seat="B")
        blessing = self.add(game.engine, "Benevolent Blessing", zone="hand")
        later_aura = self.add(game.engine, "Holy Strength", zone="hand")
        later_gear = self.add(game.engine, "Barbed Spike", ref="LATER-WHITE-EQUIP")
        for attacker in (creature, companion):
            attacker.acquired_control_turn_count = -1
        self.prepare(game, mana={"C": 5, "W": 2})
        self.checkpoint(game)
        game = self.white_blessing(game, blessing, creature)
        self.priority(game, "A")
        self.reject(game, "A", {"action_id": "cast:" + later_aura.ref, "targets": [creature.ref], "pay": "auto"})
        equip, = (offer for offer in self.actions(game) if offer["id"].startswith("activate:" + later_gear.ref + ":"))
        self.assertEqual({companion.ref}, set(equip["target_schema"]["legal_refs"]))
        self.reject(game, "A", {"action_id": equip["id"], "targets": [creature.ref], "pay": "auto"})
        self.attack(game, {creature.ref: "B", companion.ref: "B"})
        self.blockers(game)
        self.block(game, {opponent.ref: creature.ref}, valid=False)
        self.block(game, {opponent.ref: companion.ref})
        self.replay(game, load=True)

    def test_actual_benevolent_blessing_prevents_matching_combat_damage(self):
        game = self.session(702_160_503)
        creature = self.add(game.engine, "Grizzly Bears")
        attacker = self.add(game.engine, "Isamaru, Hound of Konda", seat="B")
        attacker.acquired_control_turn_count = -1
        blessing = self.add(game.engine, "Benevolent Blessing", zone="hand")
        self.prepare(game, actor="B", active="B")
        game.state.players["A"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        game = self.white_blessing(game, blessing, creature)
        self.advance(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "combat.attackers")
        attacked = game.act("pilot:B", {"action_id": "attack", "attacks": {attacker.ref: "A"}})
        self.assertTrue(attacked.ok, attacked.summary)
        self.blockers(game)
        blocked = game.act("pilot:A", {"action_id": "block", "blocks": {creature.ref: attacker.ref}})
        self.assertTrue(blocked.ok, blocked.summary)
        self.advance(game, lambda s: s.state.phase == "postcombat_main")
        protected = game.state.cards[creature.object_id]
        self.assertEqual(("battlefield", 0), (protected.zone, protected.marked_damage))
        self.assertEqual("graveyard", game.state.cards[attacker.object_id].zone)
        self.replay(game, load=True)

    def test_benevolent_blessing_same_event_attachment_cutoff_and_new_incarnation(self):
        from quorune.ability_fragments import ability_fragment_from_dict, protection_specs

        game = self.session(702_160_504)
        creature = self.add(game.engine, "Grizzly Bears")
        blessing = self.add(game.engine, "Benevolent Blessing", zone="graveyard", ref="01-PROTECTION")
        owned = self.add(game.engine, "Holy Strength", zone="graveyard", ref="02-OWN-AURA")
        foreign = self.add(game.engine, "Holy Strength", seat="B", zone="graveyard")
        spell = self.add(game.engine, "Open the Vaults", zone="hand", ref="FIRST-RETURN")
        second = self.add(game.engine, "Open the Vaults", zone="hand", ref="SECOND-RETURN")
        disenchant = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.prepare(game, mana={"C": 8, "W": 4})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", spell, payment={"C": 4, "W": 2})
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        option = next(row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                      if row["id"].endswith(":W"))
        colored = game.act("pilot:A", {"action_id": "choose", "choices": {"replacement": option}})
        self.assertTrue(colored.ok, colored.summary)
        pending = self.resolve(game)
        actors = []
        while pending is not None and pending.kind == "aura.entry":
            actor, = pending.actors
            actors.append(actor)
            self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard"
                                for card in (blessing, owned, foreign)))
            command = {"action_id": "choose", "aura_target": creature.ref}
            self.reject(game, "D", command)
            chosen = game.act("pilot:" + actor, command)
            self.assertTrue(chosen.ok, chosen.summary)
            pending = self.resolve(game)
        self.assertEqual(["A", "A", "B"], actors)
        self.assertIsNone(pending)
        source = game.state.cards[blessing.object_id]
        own = game.state.cards[owned.object_id]
        self.assertGreater(own.zone_timestamp, source.zone_timestamp)
        self.assertEqual(creature.object_id, own.attached_to)
        data = game.engine._effective_card_data(game.state.cards[creature.object_id])
        spec, = protection_specs(ability_fragment_from_dict(row) for row in data["ability_fragments"])
        self.assertGreaterEqual(spec.attachment_retention.cutoff, own.zone_timestamp)
        self.assertEqual("battlefield", own.zone)
        self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)

        old = own.logical_object_id
        self.cast(game, "B", disenchant, [own.ref], {"C": 1, "W": 1})
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[owned.object_id]
        self.assertEqual("graveyard", current.zone)
        self.assertNotEqual(old, current.logical_object_id)
        self.cast(game, "A", second, payment={"C": 4, "W": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[owned.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
        self.replay(game, load=True)

    def test_benevolent_blessing_retention_codec_preserves_other_protection(self):
        from quorune.ability_fragments import ProtectionQualityKind, ProtectionSpec, ability_fragment_to_dict
        from quorune.compiler.attached_protection_exceptions import attached_protection_exception_handler
        from quorune.protection import ProtectionSource, ProtectionVerdict, protection_verdict
        from types import SimpleNamespace
        from quorune.protection_attachment_exceptions import (
            CurrentProtectionAttachment, ProtectionAttachmentRetention, attached_protection_runtime_dependencies,
        )

        scope = ProtectionAttachmentRetention("source@1", "creature@2", "A", 17)
        current = CurrentProtectionAttachment("equipment@1", "creature@2", "A", 17)
        spec = ProtectionSpec(ProtectionQualityKind.COLOR, "W", 5, attachment_retention=scope)
        self.assertEqual(spec, ProtectionSpec.from_dict(spec.to_dict()))
        data = {"ability_fragments": [ability_fragment_to_dict(spec)]}
        with self.assertRaisesRegex(ValueError, "live chosen-color handler"):
            attached_protection_runtime_dependencies(SimpleNamespace(handlers=(data,)))
        white = ProtectionSource(colors=frozenset({"W"}))
        self.assertIs(ProtectionVerdict.ALLOWED, protection_verdict(data, white, retaining_attachment=current))
        for rejected in (None, CurrentProtectionAttachment("equipment@1", "creature@2", "B", 17),
                         CurrentProtectionAttachment("equipment@1", "creature@2", "A", 18),
                         CurrentProtectionAttachment("equipment@1", "creature@3", "A", 16)):
            self.assertIs(ProtectionVerdict.BLOCKED, protection_verdict(data, white, retaining_attachment=rejected))
        data["ability_fragments"].append(ability_fragment_to_dict(ProtectionSpec(ProtectionQualityKind.COLOR, "W")))
        self.assertIs(ProtectionVerdict.BLOCKED, protection_verdict(data, white, retaining_attachment=current))
        parsed = self.db.lookup("Benevolent Blessing", fuzzy=False).oracle_text.splitlines()[-1]
        self.assertIsNotNone(attached_protection_exception_handler(parsed))
        for changed in (parsed.replace("you control", "your opponents control"), parsed.replace("already ", "")):
            self.assertIsNone(attached_protection_exception_handler(changed))

    def test_benevolent_blessing_requires_versioned_grant_and_owner_minimum(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.protection_attachment_exceptions import CONTROLLED_ATTACHMENT_PROTECTION_CAPABILITY

        card = self.db.lookup("Benevolent Blessing", fuzzy=False)
        with patch("quorune.compiler.attached_protection_exceptions.attached_protection_exception_handler", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry).status)
        game = self.session(702_160_505)
        self.add(game.engine, "Benevolent Blessing", zone="hand")
        program, = (program for program in game.engine.semantics.programs_for_oracle(card.oracle_id)
                    if CONTROLLED_ATTACHMENT_PROTECTION_CAPABILITY in program.capability_dependencies)
        deps = tuple(value for value in program.capability_dependencies if value != CONTROLLED_ATTACHMENT_PROTECTION_CAPABILITY)
        closure = self.registry.closure(deps, profile="commander_review").to_dict() if deps else None
        stripped = replace(program, capability_dependencies=deps, capability_closure=closure)
        result = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + CONTROLLED_ATTACHMENT_PROTECTION_CAPABILITY, result["blockers"])
