"""CR603.7: real combat creates delayed actions without immediate movement."""
import json,tempfile,unittest
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class DelayedObjectNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'delayed-object.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/delayed-object-action-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Delayed object actions',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',14)],['Isamaru, Hound of Konda'])

    def test_actual_combat_delays_destruction_and_sacrifice_and_skips_the_blinked_wall_after_load(self):
        s=self.session(603700001)
        fog=self.add(s.engine,'Fog Elemental',ref='actual-delayed-fog')
        first=self.add(s.engine,'Grizzly Bears',ref='first-delayed-attacker')
        second=self.add(s.engine,'Grizzly Bears',ref='second-delayed-attacker')
        asp=self.add(s.engine,'Tangle Asp',ref='actual-delayed-counterpart-source')
        counterpart=self.add(s.engine,'Grizzly Bears',seat='B',ref='actual-delayed-counterpart')
        blinked=self.add(s.engine,'Cinder Wall',seat='B',ref='actual-blinked-wall')
        doomed=self.add(s.engine,'Cinder Wall',seat='B',ref='actual-doomed-wall')
        blink=self.add(s.engine,'Cloudshift',seat='B',zone='hand',ref='actual-delayed-blink')
        self.add(s.engine,'Plains',seat='B',ref='delayed-blink-mana')
        self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='delayed-private-hand')
        for card in (fog,first,second,asp):card.acquired_control_turn_count=-1
        for card in (fog,blinked,doomed,blink,asp):self.assert_closed(s,card)
        self.prepare(s);self.checkpoint(s)
        self.attack(s,[(fog,'D'),(first,'B'),(second,'B'),(asp,'B')]);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,len(s.state.delayed_triggers))
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{blinked.ref:first.ref,doomed.ref:second.ref,counterpart.ref:asp.ref}}
        before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:C',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.until(s,lambda game:len(game.state.delayed_triggers)==4 and not game.state.stack and game.state.pending_decision.kind=='priority')
        for card in (fog,blinked,doomed,asp,counterpart):self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
        original=s.state.cards[blinked.object_id].logical_object_id
        self.cast(s,'B',blink,[blinked.ref]);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(original,s.state.cards[blinked.object_id].logical_object_id)
        self.until(s,lambda game:game.state.phase=='combat' and game.state.step=='end_combat' and bool(game.state.stack) and not game.state.pending_trigger_batches)
        self.assertEqual(4,len(s.state.stack))
        self.assertEqual(36,s.state.players['D'].life)
        self.assertEqual(40,s.state.players['B'].life)
        self.assertEqual('graveyard',s.state.cards[asp.object_id].zone)
        self.assertEqual(1,s.state.cards[counterpart.object_id].marked_damage)
        for card in (fog,blinked,doomed,counterpart):self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[blinked.object_id].zone)
        for card in (fog,doomed,counterpart):self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        self.assertEqual(4,len(s.state.delayed_triggers));self.assertFalse(any(t.active for t in s.state.delayed_triggers))
        for seat in 'ABD':self.assertNotIn('delayed-private-hand',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s,load=True)


class EarthbendNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "earthbend.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "mutate-casting-cards.json",
            fixtures / "qualified-attack-cards.json",
            fixtures / "counter-placement-event-cards.json",
            fixtures / "worldwagon-cards.json",
            fixtures / "optional-object-action-counters.json",
            fixtures / "earthbend-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Earthbend", [DeckEntry("Generic Bound Commander", 1, "commander"),
                          DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"],
        )

    def test_earthbend_compiler_preserves_target_amount_and_capability_boundaries(self):
        from unittest.mock import patch

        from quorune.compiler.earthbend_templates import earthbend_effect_template
        from quorune.earthbend_model import EARTHBEND_CAPABILITY
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.earthbend_shapes import earthbend_node_capabilities

        lesson = self.db.lookup("Earthbending Lesson")
        for text in ("Earthbend 4.", "Earthbend X, where X is twice the number of Foods you control."):
            with self.subTest(text=text):
                _, effects, schema, mechanics = earthbend_effect_template(text, source_name=lesson.name)
                self.assertIn(EARTHBEND_CAPABILITY, earthbend_node_capabilities(
                    effects=effects, target_schema=schema, mechanic_ids=mechanics,
                ))
                self.assertFalse(earthbend_node_capabilities(
                    effects=effects, target_schema={**schema, "controller_relation": "any"}, mechanic_ids=mechanics,
                ))
        for text in ("Earthbend -1.", "Earthbend X.", "Earthbend 2 an opponent controls."):
            with self.subTest(text=text):
                self.assertIsNone(earthbend_effect_template(text, source_name=lesson.name))
        self.assertEqual("exact", compile_oracle_card(lesson, capability_registry=self.registry).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        capability = next(row for row in raw["capabilities"] if row["id"] == EARTHBEND_CAPABILITY)
        capability.update(status="blocked", blockers=["withdrawn for the dependency test"])
        self.assertNotEqual("exact", compile_oracle_card(lesson, capability_registry=CapabilityRegistry(raw)).status)
        with patch("quorune.compiler.resolution_effect_templates.earthbend_effect_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(lesson, capability_registry=self.registry).status)

    def test_actual_earthbend_resumes_counter_replacements_and_returns_all_merged_cards(self):
        from quorune.characteristic_evaluation import type_parts

        s = self.session(70166001)
        lesson = self.add(s.engine, "Earthbending Lesson", zone="hand")
        heron = self.add(s.engine, "Dreamtail Heron", zone="hand")
        land = self.add(s.engine, "Forest", ref="earthbend-land")
        foreign = self.add(s.engine, "Forest", seat="B", ref="earthbend-foreign-land")
        scales = self.add(s.engine, "Hardened Scales")
        season = self.add(s.engine, "Branching Evolution")
        destroy = self.add(s.engine, "Doom Blade", seat="B", zone="hand")
        control = self.add(s.engine, "Act of Treason", seat="C", zone="hand")
        self.add(s.engine, "Vedalken Orrery", seat="C")
        private = self.add(s.engine, "Boomerang", seat="D", zone="hand", ref="earthbend-private")
        for source in (lesson, heron, scales, season, destroy, control):
            self.assert_closed(s, source)
        self.prepare(s, mana={"C": 6, "G": 1, "U": 1})
        s.state.players["B"].mana_pool.update({"C": 1, "B": 1})
        s.state.players["C"].mana_pool.update({"C": 2, "R": 1})
        self.checkpoint(s)
        offered = self.action(s, "A", lesson)
        before = authoritative_state_hash(s.state)
        invalid = {"action_id": offered["id"], "targets": [foreign.ref], "pay": "auto"}
        self.assertFalse(s.act("pilot:A", invalid).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.cast_for(s, "A", lesson, [land.ref], reserve={"U": 1})
        decision = self.resolve(s)
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(["pilot:A"], s.pending_principals())
        current = s.state.cards[land.object_id]
        types, subtypes, supertypes = type_parts(s.engine._effective_card_data(current)["type_line"])
        self.assertEqual(({"land", "creature"}, {"forest"}, {"basic"}), (types, subtypes, supertypes))
        self.assertEqual({}, current.counters)
        self.assertFalse(current.tapped)
        self.assertFalse(any(trigger.active for trigger in s.state.delayed_triggers))
        options = s.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        first = next(option["id"] for option in options if option["source"] == scales.ref)
        choice = {"action_id": "choose", "replacement": first}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act("pilot:B", choice).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        result = s.act("pilot:A", choice)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        current = s.state.cards[land.object_id]
        self.assertEqual({"+1/+1": 10}, current.counters)
        self.assertIn("haste", s.engine._combat_keywords(current))
        self.assertEqual(10, s.engine._numeric_stat(land.object_id, "power"))
        self.assertEqual(1, sum(trigger.active for trigger in s.state.delayed_triggers))

        self.priority(s, "A")
        actions = s.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        mana = next(row for row in actions if row.get("mana_ability") and land.ref in row["id"])
        result = s.act("pilot:A", {"action_id": mana["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, s.state.players["A"].mana_pool["G"])
        action = self.action(s, "A", heron)
        option, = [value for value in action["cost_options"] if value["id"].startswith("mutate")]
        result = s.act("pilot:A", {"action_id": action["id"], "cost_option": option["id"],
                                  "targets": [land.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(s).kind)
        result = s.act("pilot:A", {"action_id": "choose", "choice": "bottom"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertIsNotNone(s.state.cards[land.object_id].permanent_composition)
        self.cast(s, "C", control, [land.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual("C", s.state.cards[land.object_id].controller)
        self.cast(s, "B", destroy, [land.ref])
        decision = self.resolve(s)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        self.assertEqual("composition_departure_order", s.packet("pilot:A", full=True)["decision"]["ctx"]["operation"])
        result = s.act("pilot:A", {"action_id": "choose", "cards": [land.ref, heron.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(("A", "Earthbend — return the land"), (s.state.stack[-1].controller, s.state.stack[-1].label))
        for oid in (land.object_id, heron.object_id):
            self.assertEqual("graveyard", s.state.cards[oid].zone)
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        for oid in (land.object_id, heron.object_id):
            card = s.state.cards[oid]
            self.assertEqual(("battlefield", "A", "A", True, {}),
                             (card.zone, card.owner, card.controller, card.tapped, card.counters))
            self.assertIsNone(card.permanent_composition)
        self.assertNotIn(private.ref, json.dumps(s.packet("pilot:A", full=True)))
        self.replay(s, load=True)

    def test_actual_prevented_earthbend_counters_create_a_stale_delayed_return(self):
        s = self.session(70166002)
        lesson = self.add(s.engine, "Earthbending Lesson", zone="hand")
        land = self.add(s.engine, "Forest")
        solemnity = self.add(s.engine, "Solemnity", seat="B")
        reclaim = self.add(s.engine, "Reclaim", zone="hand")
        for source in (lesson, solemnity, reclaim):
            self.assert_closed(s, source)
        self.prepare(s, mana={"C": 3, "G": 2})
        self.checkpoint(s)
        self.cast_for(s, "A", lesson, [land.ref], reserve={"G": 1})
        self.resolve_top(s)
        self.assertEqual("graveyard", s.state.cards[land.object_id].zone)
        self.assertEqual("Earthbend — return the land", s.state.stack[-1].label)
        self.cast(s, "A", reclaim, [land.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual("library", s.state.cards[land.object_id].zone)
        self.assertEqual(land.object_id, s.state.players["A"].zones["library"][-1])
        self.assertFalse(any(trigger.active for trigger in s.state.delayed_triggers))
        self.replay(s, load=True)

    def test_actual_earthbend_public_count_and_exile_return_follow_current_land(self):
        s = self.session(70166003)
        rock = self.add(s.engine, "Rockalanche", zone="hand")
        lesson = self.add(s.engine, "Earthbending Lesson", zone="hand")
        land = self.add(s.engine, "Island", ref="earthbend-count-land")
        self.add(s.engine, "Forest", ref="earthbend-first-forest")
        self.add(s.engine, "Forest", ref="earthbend-second-forest").tapped = True
        self.add(s.engine, "Forest", seat="B", ref="earthbend-other-forest")
        exile = self.add(s.engine, "Scour from Existence", seat="B", zone="hand")
        bounce = self.add(s.engine, "Boomerang", seat="B", zone="hand")
        for source in (rock, lesson, exile, bounce):
            self.assert_closed(s, source)
        self.prepare(s, mana={"C": 8, "G": 3})
        s.state.players["B"].mana_pool.update({"C": 7, "U": 2})
        self.checkpoint(s)
        self.cast_for(s, "A", rock, [land.ref], reserve={"G": 1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual({"+1/+1": 2}, s.state.cards[land.object_id].counters)
        self.cast_for(s, "B", exile, [land.ref], reserve={"U": 2})
        self.resolve_top(s)
        self.assertEqual("exile", s.state.cards[land.object_id].zone)
        self.assertEqual("Earthbend — return the land", s.state.stack[-1].label)
        self.assertIsNone(self.resolve(s))
        current = s.state.cards[land.object_id]
        self.assertEqual(("battlefield", True, {}), (current.zone, current.tapped, current.counters))
        self.cast(s, "A", lesson, [land.ref])
        self.cast(s, "B", bounce, [land.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual("hand", s.state.cards[land.object_id].zone)
        self.assertEqual(1, len(s.state.delayed_triggers))
        self.replay(s, load=True)

    def test_actual_repeated_earthbend_resolves_each_legal_target_and_returns_once(self):
        for stale_first in (False, True):
            with self.subTest(stale_first=stale_first):
                s = self.session(70166010 + int(stale_first))
                technique = self.add(s.engine, "Cracked Earth Technique", zone="hand")
                land = self.add(s.engine, "Forest", ref="repeated-earthbend-land")
                first = self.add(s.engine, "Island", ref="stale-earthbend-land") if stale_first else land
                response = self.add(s.engine, "Boomerang" if stale_first else "Doom Blade", seat="B", zone="hand")
                for source in (technique, response):
                    self.assert_closed(s, source)
                self.prepare(s, mana={"C": 4, "G": 1})
                s.state.players["B"].mana_pool.update({"U": 2} if stale_first else {"C": 1, "B": 1})
                self.checkpoint(s)
                action = self.action(s, "A", technique)
                command = {
                    "action_id": action["id"], "pay": "auto",
                    "targets": [{"group": "target_0", "ref": first.ref}, {"group": "target_1", "ref": land.ref}],
                }
                cast = s.act("pilot:A", command)
                self.assertTrue(cast.ok, cast.summary)
                self.assertEqual(
                    {"target_0": [first.ref], "target_1": [land.ref]},
                    s.state.stack[-1].context["target_groups"],
                )
                if stale_first:
                    self.cast(s, "B", response, [first.ref])
                self.assertIsNone(self.resolve(s))
                self.assertEqual(43, s.state.players["A"].life)
                current = s.state.cards[land.object_id]
                self.assertEqual({"+1/+1": 3 if stale_first else 6}, current.counters)
                active = [trigger for trigger in s.state.delayed_triggers if trigger.active]
                self.assertEqual(1 if stale_first else 2, len(active))
                self.assertTrue(all(
                    trigger.condition["land_identity"] == current.logical_object_id for trigger in active
                ))
                if stale_first:
                    self.assertEqual("hand", s.state.cards[first.object_id].zone)
                else:
                    self.cast(s, "B", response, [land.ref])
                    self.resolve_top(s)
                    self.assertEqual("trigger.order", s.state.pending_decision.kind)
                    offered = s.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
                    self.assertEqual(2, len(offered))
                    ordered = s.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in offered]})
                    self.assertTrue(ordered.ok, ordered.summary)
                    departed = s.state.cards[land.object_id].zone_change_counter
                    self.assertEqual("graveyard", s.state.cards[land.object_id].zone)
                    self.assertIsNone(self.resolve(s))
                    current = s.state.cards[land.object_id]
                    self.assertEqual(("battlefield", True, departed + 1),
                                     (current.zone, current.tapped, current.zone_change_counter))
                self.replay(s, load=True)

    def test_actual_earthbend_linked_untap_keeps_the_announced_land(self):
        s = self.session(70166020)
        source = self.add(s.engine, "Avatar Kyoshi, Earthbender")
        land = self.add(s.engine, "Forest", ref="earthbend-linked-untap")
        land.tapped = True
        self.assert_closed(s, source)
        self.prepare(s)
        self.checkpoint(s)
        self.target(s, "A", [land.ref])
        self.assertIsNone(self.resolve(s))
        current = s.state.cards[land.object_id]
        self.assertEqual({"+1/+1": 8}, current.counters)
        self.assertFalse(current.tapped)
        self.assertEqual(1, sum(trigger.active for trigger in s.state.delayed_triggers))
        self.replay(s, load=True)
