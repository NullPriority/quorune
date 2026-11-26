from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_cast_grants as controls
from quorune.rules.evidence_storage import read_evidence_document


class AdditionalTokenStatusTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TurnCastGrantTests.session
    add=controls.TurnCastGrantTests.add
    prepare=controls.TurnCastGrantTests.prepare
    checkpoint=controls.TurnCastGrantTests.checkpoint
    resolve=controls.TurnCastGrantTests.resolve
    replay=controls.TurnCastGrantTests.replay
    binding=controls.TurnCastGrantTests.binding
    actions=controls.TurnCastGrantTests.actions
    cast=controls.TurnCastGrantTests.cast
    reject=controls.TurnCastGrantTests.reject
    private_replay=controls.TurnCastGrantTests.private_replay
    priority=controls.TurnCastGrantTests.priority
    options=controls.TurnCastGrantTests.options
    use=controls.TurnCastGrantTests.use

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'additional-status.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/additional-token-status-cards.json',ROOT/'tests/fixtures/token-article-cards.json',
            ROOT/'tests/fixtures/fixed-resolution-payment-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Additional token creation status',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_normal_registered_tapped_creation_applies_to_added_map_and_replays(self):
        s=self.session(807000);observer=self.add(s.engine,'Generic Token Status Observer')
        spell=self.add(s.engine,'Generic Tapped Artifact Subject',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        for card in (observer,spell):self.assertTrue(self.binding(s,card)['strict_capability_ready'])
        self.prepare(s,mana={'G':1});self.checkpoint(s);self.cast(s,'A',spell);self.assertIsNone(self.resolve(s))
        tokens={c.printed_name:c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'}
        self.assertEqual({'Servo Token','Map Token'},set(tokens));self.assertTrue(tokens['Servo Token'].tapped)
        self.assertTrue(tokens['Map Token'].tapped,{name:card.tapped for name,card in tokens.items()})
        self.private_replay(s,private)

    def test_intrinsic_keywords_stay_with_their_definition_and_plain_status_stays_plain(self):
        s=self.session(807010);self.add(s.engine,'Generic Token Status Observer')
        spell=self.add(s.engine,'Generic Intrinsic Artifact Subject',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(s,spell)['strict_capability_ready']);self.prepare(s,mana={'G':1});self.checkpoint(s)
        self.cast(s,'A',spell);self.assertIsNone(self.resolve(s));tokens={c.printed_name:c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'}
        self.assertEqual({'Servo Token','Map Token'},set(tokens));self.assertFalse(any(c.tapped for c in tokens.values()))
        self.assertIn('deathtouch',s.engine._combat_keywords(tokens['Servo Token']))
        self.assertNotIn('Deathtouch',s.engine._copyable_characteristics(tokens['Map Token']).get('keywords',[]));self.private_replay(s,private)

    def test_nonmatching_controller_and_type_do_not_receive_additional_tokens(self):
        for i,(owner,name) in enumerate((('B','Generic Tapped Artifact Subject'),('A','Generic Tapped Creature Subject'))):
            s=self.session(807020+i);self.add(s.engine,'Generic Token Status Observer',seat=owner)
            spell=self.add(s.engine,name,zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.assertTrue(self.binding(s,spell)['strict_capability_ready']);self.prepare(s,mana={'G':1});self.checkpoint(s)
            self.cast(s,'A',spell);self.assertIsNone(self.resolve(s));tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
            self.assertEqual(1,len(tokens));self.assertNotEqual('Map Token',tokens[0].printed_name);self.assertTrue(tokens[0].tapped);self.private_replay(s,private)

    def test_status_survives_both_legal_quantity_replacement_orders_and_pending_load(self):
        for map_first in (True,False):
            s=self.session(807030+map_first);self.add(s.engine,'Generic Token Status Observer');multiplier=self.add(s.engine,'Parallel Lives')
            spell=self.add(s.engine,'Generic Tapped Artifact Subject',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.assertTrue(self.binding(s,multiplier)['strict_capability_ready']);self.prepare(s,mana={'G':1});self.checkpoint(s)
            self.cast(s,'A',spell);pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('replacement.order',pending.kind)
            s=self.replay(s,load=True);options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
            selected=next((row for row in options if ('Map' in row['label'])==map_first),None);self.assertIsNotNone(selected,options)
            command={'action_id':'choose','replacement':selected['id']};self.reject(s,command,'pilot:D')
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
            for _ in range(4):
                pending=self.resolve(s)
                if pending is None:break
                self.assertEqual('replacement.order',pending.kind);row=s.packet('pilot:A',full=True)['decision']['ctx']['options'][0]
                result=s.act('pilot:A',{'action_id':'choose','replacement':row['id']});self.assertTrue(result.ok,result.summary)
            else:self.fail('Token replacement order did not finish')
            tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertTrue(tokens)
            self.assertTrue(all(c.tapped for c in tokens));self.assertEqual(2,sum(c.printed_name=='Servo Token' for c in tokens))
            self.assertEqual(2 if map_first else 1,sum(c.printed_name=='Map Token' for c in tokens));self.private_replay(s,private)

    def test_owner_api_temporary_status_is_separate_from_intrinsic_characteristics(self):
        s=self.session(807040);self.add(s.engine,'Generic Token Status Observer');self.prepare(s)
        with s.engine.transaction():
            refs=s.engine.create_token('A',name='Servo',tapped=True,temporary_keywords=('Haste',),
                characteristics={'type_line':'Token Artifact Creature — Servo','power':'1','toughness':'1','colors':[], 'keywords':['Deathtouch']})
        tokens={s.engine._resolve_object('A',ref).printed_name:s.engine._resolve_object('A',ref) for ref in refs}
        self.assertEqual({'Servo','Map Token'},set(tokens))
        for card in tokens.values():self.assertTrue(card.tapped);self.assertIn('Haste',card.temporary_keywords)
        self.assertIn('Deathtouch',s.engine._copyable_characteristics(tokens['Servo'])['keywords'])
        self.assertNotIn('Deathtouch',s.engine._copyable_characteristics(tokens['Map Token']).get('keywords',[]))

    def test_ambiguous_mixed_status_rejects_before_creation_and_rolls_back(self):
        from quorune.token_creation import create_token_batch
        from quorune.replacement.model import ReplacementEffectError
        from quorune.record import authoritative_state_hash
        from quorune.compiler.token_templates import fixed_token_creation_effect_template
        s=self.session(807050);self.add(s.engine,'Generic Token Status Observer');self.prepare(s)
        compiled=fixed_token_creation_effect_template('Create a tapped 1/1 colorless Servo artifact creature token and a 2/2 colorless Construct artifact creature token.')
        self.assertIsNotNone(compiled);before=authoritative_state_hash(s.state)
        with self.assertRaises(ReplacementEffectError),s.engine.transaction():create_token_batch(s.engine,'A',tokens=compiled.effect['tokens'])
        self.assertEqual(before,authoritative_state_hash(s.state));self.assertFalse(any(c.is_token for c in s.state.cards.values()))

    def test_verified_withdrawal_and_fresh_missing_status_are_rejected(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        cap='token.creation.additional_replacement';raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==cap).update(status='blocked',blockers=['withdrawn status replacement'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()));registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup('Generic Token Status Observer'),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.replacement import application
        method='test_normal_registered_tapped_creation_applies_to_added_map_and_replays'
        with patch.object(application,'_shared_token_creation_status',return_value={}):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_matching_quantity_native_tokens_keep_status_in_each_replacement_order(self):
        for additional_first in (False, True):
            with self.subTest(additional_first=additional_first):
                s = self.session(807070 + additional_first)
                source = self.add(s.engine, "Chatterfang, Squirrel General")
                self.add(s.engine, "Parallel Lives")
                spell = self.add(s.engine, "Generic Pair of Tapped Servos", zone="hand")
                private = self.add(s.engine, "Generic Bound Growth", seat="C", zone="hand")
                self.assertTrue(self.binding(s, source)["strict_capability_ready"])
                self.assertTrue(self.binding(s, spell)["strict_capability_ready"])
                self.prepare(s, mana={"G": 1})
                self.checkpoint(s)
                self.cast(s, "A", spell)
                self.assertEqual("replacement.order", self.resolve(s).kind)
                s = self.replay(s, load=True)
                options = s.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                selected, = (row for row in options if ("Squirrel" in row["label"]) is additional_first)
                result = s.act("pilot:A", {"action_id": "choose", "replacement": selected["id"]})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(s))
                tokens = [card for card in s.state.cards.values() if card.is_token and card.zone == "battlefield"]
                self.assertEqual(4, sum(card.printed_name == "Servo Token" for card in tokens))
                self.assertEqual(4, sum(card.printed_name == "Squirrel Token" for card in tokens))
                self.assertTrue(all(card.tapped and card.controller == "A" for card in tokens))
                self.private_replay(s, private)

    def test_matching_quantity_native_noncreature_tokens_and_recipient_scope(self):
        for source_seat in ("A", "B"):
            s = self.session(807077 + (source_seat == "B"))
            self.add(s.engine, "Chatterfang, Squirrel General", seat=source_seat)
            spell = self.add(s.engine, "Generic Pair of Treasures", zone="hand")
            self.prepare(s, mana={"G": 1})
            self.checkpoint(s)
            self.cast(s, "A", spell)
            self.assertIsNone(self.resolve(s))
            tokens = [card for card in s.state.cards.values() if card.is_token and card.zone == "battlefield"]
            self.assertEqual(2, sum(card.printed_name == "Treasure Token" for card in tokens))
            added = [card for card in tokens if card.printed_name == "Squirrel Token"]
            self.assertEqual(2 if source_seat == "A" else 0, len(added))
            self.assertTrue(all(card.controller == "A" for card in tokens))
            for card in added:
                characteristics = s.engine._copyable_characteristics(card)
                self.assertEqual(("1", "1"), (characteristics["power"], characteristics["toughness"]))
                self.assertFalse(characteristics.get("activated_abilities"))
            self.replay(s)

    def test_announced_subtype_sacrifice_pays_selected_x_once_and_replays(self):
        from quorune.choice_forms import build_action_form
        from quorune.record import authoritative_state_hash

        s = self.session(807080)
        source = self.add(s.engine, "Chatterfang, Squirrel General")
        squirrels = tuple(self.add(s.engine, "Generic Subtype Sacrifice Squirrel", ref=f"paid-squirrel-{i}") for i in range(3))
        foreign = self.add(s.engine, "Generic Subtype Sacrifice Squirrel", seat="B", ref="foreign-squirrel")
        wrong = self.add(s.engine, "Generic Subtype Sacrifice Target", ref="wrong-subtype")
        target = self.add(s.engine, "Generic Subtype Sacrifice Target", seat="B", ref="stats-target")
        private = self.add(s.engine, "Generic Bound Growth", seat="C", zone="hand")
        self.assertTrue(self.binding(s, source)["strict_capability_ready"])
        self.prepare(s, mana={"B": 2})
        self.checkpoint(s)

        def offer():
            return next(row for row in self.actions(s) if row.get("source") == source.ref
                        and row["id"].startswith("activate:"))

        action = offer()
        self.assertEqual(4, action["choice_schema"]["x"]["maximum"])
        expected = {source.ref, *(card.ref for card in squirrels)}
        self.assertEqual(expected, set(action["choice_schema"]["cost_objects"]["legal_refs"]))
        form = build_action_form(action, decision_kind="priority", context={})
        cost_field, = (field for field in form["fields"] if field["name"] == "cost_objects")
        self.assertEqual(("refs", "x", 0, 4), (cost_field["control"], cost_field["count_from"],
                                               cost_field["minimum"], cost_field["maximum"]))
        self.assertFalse(any(field["name"] == "cost_cards" for field in form["fields"]))
        before = authoritative_state_hash(s.state)
        for announced_x, picked in (
            (2, [squirrels[0].ref]), (1, [foreign.ref]), (1, [wrong.ref]),
            (True, [squirrels[0].ref]), (2, [squirrels[0].ref, squirrels[0].ref]),
        ):
            bad = s.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto",
                                   "x": announced_x, "cost_objects": picked})
            self.assertFalse(bad.ok, bad.summary)
            self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto",
                                  "x": 0, "cost_objects": []})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, s.state.stack[-1].x_value)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(("battlefield",) * 3, tuple(s.state.cards[card.object_id].zone for card in squirrels))
        self.assertEqual((2, 5), (s.engine._numeric_stat(target.object_id, "power"),
                                  s.engine._numeric_stat(target.object_id, "toughness")))
        self.assertEqual(1, s.state.players["A"].mana_pool["B"])
        for _ in range(8):
            if s.pending_principals() == ["pilot:A"]:
                break
            accepted = s.act(s.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(accepted.ok, accepted.summary)
        else:
            self.fail("The activation controller did not regain priority")
        result = s.act("pilot:A", {"action_id": offer()["id"], "targets": [target.ref], "pay": "auto",
                                  "x": 2, "cost_objects": [squirrels[0].ref, squirrels[1].ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, s.state.stack[-1].x_value)
        self.assertEqual(("graveyard", "graveyard", "battlefield"),
                         tuple(s.state.cards[card.object_id].zone for card in squirrels))
        self.assertEqual(0, s.state.players["A"].mana_pool["B"])
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual((4, 3), (s.engine._numeric_stat(target.object_id, "power"),
                                  s.engine._numeric_stat(target.object_id, "toughness")))
        self.private_replay(s, private)

    def test_announced_subtype_sacrifice_requires_exact_bound_price_and_result(self):
        from dataclasses import replace
        from quorune.rules.announced_subtype_sacrifice import ANNOUNCED_SUBTYPE_SACRIFICE_CAPABILITY
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card

        card = self.db.lookup("Chatterfang, Squirrel General")
        opts = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(card, **opts)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (entry for entry in ir.faces[0].nodes if entry.kind == "activated_ability")
        self.assertIn(ANNOUNCED_SUBTYPE_SACRIFICE_CAPABILITY, node.capability_dependencies)
        s = self.session(807081)
        self.add(s.engine, card.name)
        program, = (entry for entry in s.engine.semantics.programs_for_oracle(card.oracle_id)
                    if ANNOUNCED_SUBTYPE_SACRIFICE_CAPABILITY in entry.capability_dependencies and entry.effects)
        for change in (
            {"effects": ({**program.effects[0], "power": 1},)},
            {"target_schema": None},
            {"cost_schema": {**program.cost_schema, "choices": []}},
        ):
            bound = bind_semantic_program_runtime(replace(program, **change), capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"])
        with patch("quorune.compiler.activation_x_costs._announced_subtype_cost", return_value=None):
            omitted = compile_oracle_card(card, **opts)
        self.assertFalse(next(entry for entry in omitted.faces[0].nodes if entry.kind == "activated_ability").exact)

    def test_announced_subtype_sacrifice_replacement_batch_loads_and_resumes_exact_x(self):
        s = self.session(807082)
        source = self.add(s.engine, "Chatterfang, Squirrel General")
        paid = tuple(self.add(s.engine, "Generic Subtype Sacrifice Squirrel", ref=f"replaced-squirrel-{i}")
                     for i in range(2))
        target = self.add(s.engine, "Generic Subtype Sacrifice Target", seat="B")
        self.add(s.engine, "Generic Payment Destination Replacement", seat="B")
        self.add(s.engine, "Generic Payment Competing Replacement", seat="C")
        self.prepare(s, mana={"B": 1})
        self.checkpoint(s)
        offer = next(row for row in self.actions(s) if row.get("source") == source.ref
                     and row["id"].startswith("activate:"))
        accepted = s.act("pilot:A", {"action_id": offer["id"], "targets": [target.ref], "pay": "auto",
                                    "x": 2, "cost_objects": [card.ref for card in reversed(paid)]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIn("replacement", s.state.pending_decision.kind)
        self.assertFalse(s.state.stack)
        self.assertTrue(all(s.state.cards[card.object_id].zone == "battlefield" for card in paid))
        s = self.replay(s, load=True)
        choices = 0
        for _ in range(4):
            if not s.state.pending_decision or "replacement" not in s.state.pending_decision.kind:
                break
            principal, = s.pending_principals()
            self.assertEqual("pilot:A", principal)
            option = s.packet(principal, full=True)["decision"]["ctx"]["options"][0]
            result = s.act(principal, {"action_id": "choose", "replacement": option["id"]})
            self.assertTrue(result.ok, result.summary)
            choices += 1
        self.assertEqual(2, choices)
        activation, = s.state.stack
        self.assertEqual(2, activation.x_value)
        self.assertTrue(all(s.state.cards[card.object_id].zone == "exile" for card in paid))
        self.assertEqual(0, s.state.players["A"].mana_pool["B"])
        self.assertIsNone(self.resolve(s))
        self.assertEqual((4, 3), (s.engine._numeric_stat(target.object_id, "power"),
                                  s.engine._numeric_stat(target.object_id, "toughness")))
        self.replay(s)
