from __future__ import annotations

"""CR 202.3b distinguishes an original back face from a copy of that face."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase, CardRecord
from quorune.carddb_characteristics import base_card_characteristics
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions
import test_self_public_zone_casting as zone_actions


FIXTURE = ROOT / 'tests/fixtures/nonmodal-face-mana-value-cards.json'


def face_record(**changes):
    record = CardRecord(oracle_id='fixture:nonmodal-mana-value', name='Front // Back', mana_cost='{4}{G}',
        mana_value=5, type_line='Creature — Human', oracle_text='', power='2', toughness='3',
        loyalty=None, defense=None, colors=('G',), color_identity=('G',), keywords=(), produced_mana=(),
        layout='transform', released_at='2026-01-01', legalities={'commander': 'legal'}, raw={},
        faces=({'name': 'Front', 'mana_cost': '{4}{G}', 'type_line': 'Creature — Human', 'oracle_text': '',
                'power': '2', 'toughness': '3', 'colors': ['G']},
            {'name': 'Back', 'mana_cost': '', 'type_line': 'Creature — Eldrazi', 'oracle_text': '',
                'power': '7', 'toughness': '5', 'colors': []}))
    return replace(record, **changes)


def face_card(**changes):
    card = CardInstance(object_id='fixture:face-card', ref='face-card', oracle_id='fixture:nonmodal-mana-value',
        printed_name='Front // Back', owner='B', controller='A', zone='battlefield', active_face='Back')
    return replace(card, **changes)


class NonmodalFaceManaValueTests(unittest.TestCase):
    def test_original_back_face_retains_front_value_without_front_cost_or_color(self):
        record = face_record()
        for zone in ('battlefield', 'stack'):
            with self.subTest(zone=zone):
                data = base_card_characteristics(face_card(zone=zone), record)
                self.assertEqual(5, data['mana_value'])
                self.assertEqual('', data['mana_cost'])
                self.assertEqual([], data['colors'])
                self.assertEqual('7', data['power'])
        self.assertEqual(5, base_card_characteristics(face_card(active_face='Front'), record)['mana_value'])
        self.assertEqual(5, base_card_characteristics(face_card(zone='graveyard', active_face=None), record)['mana_value'])

    def test_modal_faces_and_split_selected_stack_and_prospective_views_keep_their_values(self):
        faces = (face_record().faces[0], {**face_record().faces[1], 'mana_cost': '{2}{U}', 'colors': ['U']})
        modal = face_record(layout='modal_dfc', faces=faces)
        for zone in ('battlefield', 'stack'):
            self.assertEqual(3, base_card_characteristics(face_card(zone=zone), modal)['mana_value'])
        split = face_record(layout='split', faces=faces, mana_value=8)
        self.assertEqual(3, base_card_characteristics(face_card(zone='stack'), split)['mana_value'])
        self.assertEqual(8, base_card_characteristics(face_card(zone='graveyard'), split)['mana_value'])
        self.assertEqual(3, base_card_characteristics(face_card(zone='graveyard',
            annotations={'prospective_cast_face': True}), split)['mana_value'])

    def test_copiable_back_face_value_is_derived_from_its_cost_and_keeps_copy_exceptions(self):
        from quorune.carddb_characteristics import copiable_card_characteristics
        record = face_record()
        card = face_card()
        source = base_card_characteristics(card, record)
        before = deepcopy(source)
        copied = copiable_card_characteristics(card, record, source)
        self.assertEqual(0, copied['mana_value'])
        self.assertEqual('', copied['mana_cost'])
        self.assertEqual(before, source)
        # A prior copy effect can replace that face's copiable mana cost.
        changed = face_card(annotations={'copy_overrides': {'mana_cost': '{2}{U}', 'mana_value': 3}})
        self.assertEqual(3, copiable_card_characteristics(changed, record, source)['mana_value'])
        normal = face_record(layout='normal', faces=(), name='Normal')
        self.assertEqual(5, copiable_card_characteristics(face_card(active_face=None), normal,
            base_card_characteristics(face_card(active_face=None), normal))['mana_value'])


    def test_split_permanent_face_value_provenance_is_independent(self):
        from quorune.model import GameConfig, GameState
        from quorune.record_state_provenance import format_state_versions, validate_state_versions

        field = "split_permanent_face_values_version"
        state = GameState(
            game_id="split-face-value-fixture", config=GameConfig(), players={}, cards={},
            deck_names={}, commander_oracle_ids={}, turn_order=[], current_turn=None,
            last_normal_turn_player=None,
        )
        wire = state.to_dict()
        self.assertEqual(2, wire[field])
        self.assertEqual(wire, GameState.from_dict(wire).to_dict())
        current = {"format": format_state_versions(state)}
        self.assertEqual(2, current["format"][field])
        validate_state_versions(current, state)
        old = {key: value for key, value in wire.items() if key != field}
        legacy = GameState.from_dict(old)
        self.assertEqual(old, legacy.to_dict())
        self.assertIsNone(legacy.split_permanent_face_values_version)
        self.assertEqual(state.cast_face_offers_version, legacy.cast_face_offers_version)
        historical = {"format": format_state_versions(legacy)}
        self.assertNotIn(field, historical["format"])
        validate_state_versions(historical, legacy)
        for manifest, source in ((current, legacy), (historical, state)):
            with self.assertRaises(ValueError):
                validate_state_versions(manifest, source)
        single_face = GameState.from_dict({**wire, field: 1})
        self.assertEqual(1, single_face.to_dict()[field])
        validate_state_versions({"format": format_state_versions(single_face)}, single_face)
        with self.assertRaises(ValueError):
            validate_state_versions(current, single_face)
        for invalid in (True, False, 0, 3, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    GameState.from_dict({**wire, field: invalid})
                with self.assertRaises(ValueError):
                    validate_state_versions({"format": {**current["format"], field: invalid}}, state)



class SharedTypeDoorStateTests(unittest.TestCase):
    def test_designations_keep_printed_order_and_validate_closed_state(self):
        from quorune.shared_type_doors import SharedTypeDoors

        locked = SharedTypeDoors(("Left", "Right"))
        right = locked.unlock("Right")
        both = right.unlock("Left")
        self.assertEqual(("Left", "Right"), both.unlocked)
        self.assertEqual(("Other Right",), right.rebind(("Other Left", "Other Right")).unlocked)
        self.assertEqual(both, SharedTypeDoors.from_dict(both.to_dict()))
        self.assertEqual((), locked.unlocked)
        self.assertEqual(("Left",), right.locked)
        with self.assertRaises(ValueError):
            both.unlock("Right")
        wire = both.to_dict()
        for invalid in (
            {**wire, "schema_version": True}, {**wire, "printed": 1},
            {**wire, "faces": ["Left", "Left"]}, {**wire, "unlocked": ["Right", "Left"]},
            {**wire, "unlocked": ["Left", "Left"]}, {**wire, "unlocked": ["Other"]},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                SharedTypeDoors.from_dict(invalid)



    def test_room_form_and_unlock_mutations_fail_their_contracts(self):
        from types import SimpleNamespace
        from quorune.compiler.card_form_rules import compile_card_form_rules
        from quorune.rules.room_unlock import collect_room_unlock_events
        from quorune.shared_type_doors import SharedTypeDoors

        faces = (
            {"name": "Left", "type_line": "Enchantment — Room", "mana_cost": "{U}"},
            {"name": "Right", "type_line": "Enchantment — Room", "mana_cost": "{4}{U}"},
        )
        record = face_record(layout="split", type_line="Enchantment — Room", faces=faces)

        def form_contract():
            form, = compile_card_form_rules(record, compiled_face_ids=("Left", "Right")).nodes
            self.assertEqual(("Left", "Right"), form.faces)
            self.assertEqual("shared_type_split_permanent", form.descriptor()["kind"])

        def event_contract():
            published = []
            host = SimpleNamespace(
                _log=lambda *args, **kwargs: None,
                _dispatch_semantic_event=lambda event, context, **kwargs: published.append((event, context)),
            )
            card = SimpleNamespace(ref="room", object_id="physical", logical_object_id="physical@1", controller="B")
            locked = SharedTypeDoors(("Left", "Right"))
            first = locked.unlock("Right")
            collect_room_unlock_events(host, card, locked, first, actor="B", trigger_batch=[])
            self.assertEqual(["room.door_unlocked"], [event for event, _ in published])
            collect_room_unlock_events(host, card, first, first.unlock("Left"), actor="B", trigger_batch=[])
            self.assertEqual(
                ["room.door_unlocked", "room.door_unlocked", "room.fully_unlocked"],
                [event for event, _ in published],
            )
            self.assertTrue(all(context["actor"] == "B" for _, context in published))

        form_contract()
        event_contract()
        with mock.patch("quorune.compiler.room_bindings.printed_shared_type_faces", return_value=()):
            with self.assertRaises(ValueError):
                form_contract()
        with mock.patch("quorune.rules.room_unlock._publish_unlock", return_value=None):
            with self.assertRaises(AssertionError):
                event_contract()


class NonmodalFaceManaValueRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.SpreeModeCostRuntimeTests.session
    checkpoint = actions.SpreeModeCostRuntimeTests.checkpoint
    resolve = actions.SpreeModeCostRuntimeTests.resolve
    replay = actions.SpreeModeCostRuntimeTests.replay
    prepare = actions.SpreeModeCostRuntimeTests.prepare
    assert_closed = actions.SpreeModeCostRuntimeTests.assert_closed
    reject = actions.SpreeModeCostRuntimeTests.reject
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    attack = InheritedNativeFixture.attack
    until = InheritedNativeFixture.until
    resolve_top = InheritedNativeFixture.resolve_top

    def add(self, engine, name, **kwargs):
        card = actions.SpreeModeCostRuntimeTests.add(self, engine, name, **kwargs)
        record = self.db.lookup(name)
        if record.faces and card.zone == 'battlefield':
            card.active_face = str(record.faces[0]['name'])
        return card

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'nonmodal-value.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Nonmodal value witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def transform(self, session, card, **payment):
        action = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:' + card.ref + ':') and 'Transform' in row['label'])
        self.reject(session, action['id'], 'C', pay='auto')
        result = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto', **payment})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        return session.state.cards[card.object_id]


    def test_cast_split_permanent_face_controls_battlefield_target_legality(self):
        session = self.session(20203009)
        pool = self.add(session.engine, "Bottomless Pool // Locker Room", zone="hand")
        sauna = self.add(session.engine, "Roaring Furnace // Steaming Sauna", zone="hand")
        decay = self.add(session.engine, "Abrupt Decay", seat="B", zone="hand")
        self.assert_closed(session, decay)
        session.state.players["B"].mana_pool.update({"B": 1, "G": 1})
        self.prepare(session, mana={"U": 3, "C": 3})
        self.checkpoint(session)

        for source, name, cost, value in (
            (pool, "Bottomless Pool", "{U}", 1),
            (sauna, "Steaming Sauna", "{3}{U}{U}", 5),
        ):
            with self.subTest(face=name):
                actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                offer = next(row for row in actions if row.get("card") == source.ref and row.get("face") == name)
                self.assertEqual(cost, offer["cost"])
                cast = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
                self.assertTrue(cast.ok, cast.summary)
                self.assertEqual(value, session.engine._effective_card_data(session.state.cards[source.object_id])["mana_value"])
                self.resolve_room_abilities(session, "pilot:A")
                current = session.state.cards[source.object_id]
                self.assertEqual("battlefield", current.zone)
                data = session.engine._effective_card_data(current)
                self.assertEqual((name, cost, value, ["U"]),
                                 (data["name"], data["mana_cost"], data["mana_value"], data["colors"]))

        for _ in range(4):
            if session.state.priority_player == "B":
                break
            passed = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(passed.ok, passed.summary)
        self.assertEqual("B", session.state.priority_player)
        actions = session.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        removal = next(row for row in actions if row.get("card") == decay.ref)
        self.assertIn(pool.ref, removal["target_schema"]["legal_refs"])
        self.assertNotIn(sauna.ref, removal["target_schema"]["legal_refs"])
        self.reject(session, removal["id"], "B", targets=[sauna.ref], pay="auto")
        self.reject(session, removal["id"], "C", targets=[pool.ref], pay="auto")
        accepted = session.act("pilot:B", {"action_id": removal["id"], "targets": [pool.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[pool.object_id]
        self.assertEqual("graveyard", current.zone)
        self.assertEqual(6, session.engine._effective_card_data(current)["mana_value"])
        self.assertEqual("battlefield", session.state.cards[sauna.object_id].zone)
        self.replay(session, load=True)


    def cast_room_half(self, session, card, door, **payment):
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(row for row in actions if row.get("card") == card.ref and row.get("face") == door)
        result = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto", **payment})
        self.assertTrue(result.ok, result.summary)
        self.resolve_room_abilities(session, "pilot:A")

    def room_unlock_offer(self, session, card, door):
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row.get("card") == card.ref
                    and row.get("action") == "unlock_room" and row.get("door") == door)

    def test_room_paid_unlock_replays_and_a_blinked_room_reenters_with_both_doors_locked(self):
        session = self.session(20203601)
        room = self.add(session.engine, "Bottomless Pool // Locker Room", zone="hand")
        blink = self.add(session.engine, "Flicker of Fate", seat="B", zone="hand")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand")
        self.assert_closed(session, blink)
        session.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.prepare(session, mana={"C": 4, "U": 2})
        self.checkpoint(session)

        self.cast_room_half(session, room, "Bottomless Pool", reserve={"C": 4, "U": 1})
        current = session.state.cards[room.object_id]
        self.assertEqual(("Bottomless Pool",), current.shared_type_doors.unlocked)
        identity = current.logical_object_id
        offer = self.room_unlock_offer(session, room, "Locker Room")
        self.assertEqual("{4}{U}", offer["cost"])
        self.reject(session, offer["id"], "B", pay="auto")
        result = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        current = session.state.cards[room.object_id]
        self.assertEqual(("Bottomless Pool", "Locker Room"), current.shared_type_doors.unlocked)
        self.assertEqual(identity, current.logical_object_id)
        self.assertEqual(6, session.engine._effective_card_data(current)["mana_value"])
        self.assertFalse(any(session.state.players["A"].mana_pool.values()))
        self.assertFalse(session.state.stack)
        self.assertEqual(
            ["room.door_unlocked", "room.unlock_payment", "room.door_unlocked", "room.fully_unlocked"],
            [event.code for event in session.state.events if event.code.startswith("room.")],
        )

        session = self.replay(session, load=True)
        self.cast(session, "B", blink, [room.ref])
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[room.object_id]
        self.assertNotEqual(identity, current.logical_object_id)
        self.assertEqual((), current.shared_type_doors.unlocked)
        values = session.engine._effective_card_data(current)
        self.assertEqual(("", "", 0, "Enchantment — Room"),
                         (values["name"], values["mana_cost"], values["mana_value"], values["type_line"]))
        self.assertEqual(2, sum(event.code == "room.door_unlocked" for event in session.state.events))
        projected = json.dumps(session.packet("pilot:D", full=True))
        self.assertIn("room_doors", projected)
        self.assertNotIn(private.ref, projected)
        self.replay(session, load=True)

    def test_actual_copy_enchantment_copies_both_door_definitions_without_the_unlock_designations(self):
        session = self.session(20203602)
        room = self.add(session.engine, "Bottomless Pool // Locker Room", zone="hand")
        clone = self.add(session.engine, "Copy Enchantment", zone="hand")
        self.assert_closed(session, clone)
        self.prepare(session, mana={"C": 6, "U": 4})
        self.checkpoint(session)

        self.cast_room_half(session, room, "Bottomless Pool", reserve={"C": 4, "U": 1})
        offer = self.room_unlock_offer(session, room, "Locker Room")
        result = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        result = session.act("pilot:A", {
            "action_id": f"cast:{clone.ref}", "pay": "auto", "reserve": {"U": 1},
        })
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", self.resolve(session).kind)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        choice = next(row for row in options if room.ref in row["id"] and not row["id"].startswith("decline:"))
        result = session.act("pilot:A", {"action_id": "choose", "replacement": choice["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[clone.object_id]
        self.assertEqual(("Bottomless Pool", "Locker Room"), current.shared_type_doors.faces)
        self.assertEqual((), current.shared_type_doors.unlocked)
        self.assertEqual("", session.engine._effective_card_data(current)["name"])
        self.assertEqual(2, sum(event.code == "room.door_unlocked" for event in session.state.events))

        offer = self.room_unlock_offer(session, clone, "Bottomless Pool")
        result = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_room_abilities(session, "pilot:A")
        self.assertEqual(("Bottomless Pool",), session.state.cards[clone.object_id].shared_type_doors.unlocked)
        self.assertEqual(("Bottomless Pool", "Locker Room"), session.state.cards[room.object_id].shared_type_doors.unlocked)
        self.replay(session, load=True)


    def test_actual_room_spell_copy_enters_with_its_own_paid_door_lifecycle(self):
        session = self.session(20203605)
        room = self.add(session.engine, "Bottomless Pool // Locker Room", zone="hand")
        lithoform = self.add(session.engine, "Lithoform Engine")
        reviewed, = (program for program in session.engine.semantics.programs_for_oracle(lithoform.oracle_id)
                     if program.ability_id == "ability:ab3")
        self.assertEqual("trusted", reviewed.trust_level)
        self.assertTrue(any(effect.get("op") == "copy_stack_item" for effect in reviewed.effects))
        self.prepare(session, mana={"C": 8, "U": 2})
        self.checkpoint(session)

        def offers():
            decision = session.packet("pilot:A", full=True)["decision"]
            return decision["ctx"]["legal"]["actions"] if decision is not None and decision["kind"] == "priority" else []

        cast = next(row for row in offers() if row.get("card") == room.ref and row.get("face") == "Bottomless Pool")
        result = session.act("pilot:A", {"action_id": cast["id"], "pay": "auto", "reserve": {"C": 8, "U": 1}})
        self.assertTrue(result.ok, result.summary)
        original = session.state.stack[-1]
        copy = next(row for row in offers() if row["id"].startswith(f"activate:{lithoform.ref}:")
                    and original.ref in row.get("target_schema", {}).get("legal_refs", ()))
        result = session.act("pilot:A", {"action_id": copy["id"], "targets": [original.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)
        item = session.state.stack[-1]
        self.assertEqual(("spell_copy", "Bottomless Pool"), (item.kind, item.chosen_face))
        self.resolve_top(session)
        token = session.state.cards[item.card_object_id]
        self.assertTrue(token.is_token)
        self.assertEqual(("Bottomless Pool",), token.shared_type_doors.unlocked)
        self.assertFalse(any(row.get("action") == "unlock_room" for row in offers()))

        self.resolve_room_abilities(session, "pilot:A")
        unlock = self.room_unlock_offer(session, token, "Locker Room")
        result = session.act("pilot:A", {"action_id": unlock["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        current = session.state.cards[token.object_id]
        self.assertEqual(("Bottomless Pool", "Locker Room"), current.shared_type_doors.unlocked)
        data = session.engine._effective_card_data(current)
        self.assertEqual((6, ["U"]), (data["mana_value"], data["colors"]))
        self.assertEqual(("Bottomless Pool",), session.state.cards[room.object_id].shared_type_doors.unlocked)
        self.replay(session, load=True)

    def test_actual_locker_room_combat_trigger_follows_its_door(self):
        for unlocked in (False, True):
            with self.subTest(unlocked=unlocked):
                session = self.session(20203603 + unlocked)
                room = self.add(session.engine, "Bottomless Pool // Locker Room", zone="hand")
                creature = self.add(session.engine, "Grizzly Bears")
                creature.acquired_control_turn_count = -1
                programs = session.engine.semantics.programs_for_oracle(room.oracle_id)
                locker, = (program for program in programs if program.provenance.get("face_id") == "Locker Room")
                self.assertTrue(session.engine.semantic_program_is_current_trusted(locker))
                self.prepare(session, mana={"C": 4, "U": 2})
                self.checkpoint(session)
                self.cast_room_half(session, room, "Bottomless Pool", reserve={"C": 4, "U": 1})
                if unlocked:
                    offer = self.room_unlock_offer(session, room, "Locker Room")
                    result = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
                    self.assertTrue(result.ok, result.summary)
                library_size = len(session.state.players["A"].zones["library"])
                self.attack(session, [(creature, "B")])
                self.until(session, lambda game: game.state.phase_index >= 9 and not game.state.stack)
                self.assertEqual(38, session.state.players["B"].life)
                self.assertEqual(library_size - int(unlocked), len(session.state.players["A"].zones["library"]))
                self.replay(session, load=True)


    def resolve_room_abilities(self, session, principal, target=None, optional_choice=None):
        for _ in range(8):
            pending = self.resolve(session)
            if pending is None:
                return
            self.assertEqual((principal,), tuple(session.pending_principals()))
            if pending.kind == "trigger.order":
                context = session.packet(principal, full=True)["decision"]["ctx"]
                payload = {"action_id": "order", "triggers": [row["id"] for row in context["triggers"]]}
            elif pending.kind == "semantic.choice":
                self.assertIn(optional_choice, {"apply", "decline"})
                payload = {"action_id": "choose", "choice": optional_choice}
            else:
                self.assertEqual("semantic.target", pending.kind)
                payload = {"action_id": "choose", "targets": [target.ref] if target is not None else []}
            result = session.act(principal, payload)
            self.assertTrue(result.ok, result.summary)
        self.fail("Room triggers did not finish")

    def test_native_room_entry_doubles_only_the_eerie_entry_branch(self):
        session = self.session(20203701)
        room = self.add(session.engine, "Bottomless Pool // Locker Room", zone="hand")
        watcher = self.add(session.engine, "Entity Tracker")
        doubler = self.add(session.engine, "Yarok, the Desecrated")
        bear = self.add(session.engine, "Grizzly Bears", seat="B")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand")
        for card in (room, watcher, doubler):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 4, "U": 2})
        self.checkpoint(session)
        before = len(session.state.players["A"].zones["library"])

        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        cast = next(row for row in actions if row.get("card") == room.ref and row.get("face") == "Bottomless Pool")
        result = session.act("pilot:A", {"action_id": cast["id"], "pay": "auto", "reserve": {"C": 4, "U": 1}})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)
        batch_items = [item for batch in session.state.pending_trigger_batches for item in batch.items]
        queued = batch_items + [item for item in session.state.stack if item.kind == "triggered_ability"]
        events = [(item.source_object_id, item.to_dict()["context"]["event"]) for item in queued]
        self.assertCountEqual([
            (watcher.object_id, "permanent.enter"), (watcher.object_id, "permanent.enter"),
            (room.object_id, "room.door_unlocked"),
        ], events)
        session = self.replay(session, load=True)
        self.resolve_room_abilities(session, "pilot:A", target=bear)
        self.assertEqual("hand", session.state.cards[bear.object_id].zone)
        self.assertEqual(before - 2, len(session.state.players["A"].zones["library"]))

        unlock = self.room_unlock_offer(session, room, "Locker Room")
        self.reject(session, unlock["id"], "C", pay="auto")
        result = session.act("pilot:A", {"action_id": unlock["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_room_abilities(session, "pilot:A")
        self.assertEqual(before - 3, len(session.state.players["A"].zones["library"]))
        self.assertNotIn(private.ref, json.dumps(session.packet("pilot:D", full=True)))
        self.replay(session, load=True)

    def test_native_opponent_room_unlock_leaves_the_other_watchers_inactive(self):
        session = self.session(20203702)
        room = self.add(session.engine, "Bottomless Pool // Locker Room", seat="B", zone="hand")
        watcher = self.add(session.engine, "Entity Tracker")
        for card in (room, watcher):
            self.assert_closed(session, card)
        self.prepare(session, actor="B", active="B", mana={"C": 4, "U": 2})
        self.checkpoint(session)
        before = len(session.state.players["A"].zones["library"])
        actions = session.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        cast = next(row for row in actions if row.get("card") == room.ref and row.get("face") == "Locker Room")
        result = session.act("pilot:B", {"action_id": cast["id"], "pay": "auto", "reserve": {"U": 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        actions = session.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        unlock = next(row for row in actions if row.get("card") == room.ref and row.get("action") == "unlock_room")
        result = session.act("pilot:B", {"action_id": unlock["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_room_abilities(session, "pilot:B")
        self.assertEqual(before, len(session.state.players["A"].zones["library"]))
        self.assertEqual(1, sum(event.code == "room.fully_unlocked" for event in session.state.events))
        self.replay(session, load=True)


    def test_native_graveyard_eerie_returns_to_the_room_owners_hand(self):
        session = self.session(20203704)
        room = self.add(session.engine, "Bottomless Pool // Locker Room", zone="hand")
        fear = self.add(session.engine, "Fear of Infinity", zone="graveyard")
        self.assert_closed(session, room)
        self.assert_closed(session, fear)
        self.prepare(session, mana={"C": 4, "U": 2})
        self.checkpoint(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        cast = next(row for row in actions if row.get("card") == room.ref and row.get("face") == "Locker Room")
        result = session.act("pilot:A", {"action_id": cast["id"], "pay": "auto", "reserve": {"U": 1}})
        self.assertTrue(result.ok, result.summary)
        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        self.reject(session, "choose", "B", choice="apply")
        self.resolve_room_abilities(session, "pilot:A", optional_choice="decline")
        self.assertEqual("graveyard", session.state.cards[fear.object_id].zone)

        offer = self.room_unlock_offer(session, room, "Bottomless Pool")
        result = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_room_abilities(session, "pilot:A", optional_choice="apply")
        self.assertEqual("hand", session.state.cards[fear.object_id].zone)
        self.replay(session, load=True)

    def test_native_room_unlock_applies_the_existing_two_target_counter_set(self):
        session = self.session(20203705)
        room = self.add(session.engine, "Grand Entryway // Elegant Rotunda", zone="hand")
        targets = [self.add(session.engine, "Grizzly Bears", seat=seat, ref=f"rotunda-{seat}") for seat in "BCD"]
        self.assert_closed(session, room)
        self.prepare(session, mana={"C": 8, "W": 2})
        self.checkpoint(session)
        self.cast_room_half(session, room, "Grand Entryway")
        self.assertTrue(any(card.is_token and card.controller == "A" and card.zone == "battlefield"
                            for card in session.state.cards.values()))

        offer = self.room_unlock_offer(session, room, "Elegant Rotunda")
        result = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.target", self.resolve(session).kind)
        self.reject(session, "choose", "A", targets=[target.ref for target in targets])
        self.reject(session, "choose", "B", targets=[targets[0].ref])
        result = session.act("pilot:A", {"action_id": "choose", "targets": [target.ref for target in targets[:2]]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([1, 1, 0], [session.state.cards[target.object_id].counters.get("+1/+1", 0)
                                      for target in targets])
        self.replay(session, load=True)

    def test_room_form_and_event_payloads_require_their_declared_authority(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.shared_type_doors import ROOM_UNLOCK_EVENT_CAPABILITY, SHARED_TYPE_FORM_CAPABILITY

        session = self.session(20203703)
        for name, marker in (("Bottomless Pool // Locker Room", SHARED_TYPE_FORM_CAPABILITY),
                             ("Entity Tracker", ROOM_UNLOCK_EVENT_CAPABILITY)):
            with self.subTest(name=name):
                card = self.add(session.engine, name, zone="hand")
                full = compile_best_available_card_program(
                    self.db, self.db.by_oracle_id(card.oracle_id), semantic_registry=session.engine.semantics,
                    capability_registry=self.registry, capability_profile="commander_review",
                )
                programs = [ability for ability in full.abilities if marker in ability.capability_dependencies]
                program = next(p for p in programs if name == "Entity Tracker" or p.event == "intrinsic_ability")
                original = bind_semantic_program_runtime(program, capability_registry=self.registry, profile="commander_review")
                self.assertTrue(original["strict"], original["blockers"])
                dependencies = tuple(value for value in program.capability_dependencies if value != marker)
                modified = replace(program, coverage=[], capability_dependencies=dependencies,
                    capability_closure=(
                        self.registry.closure(dependencies, profile="commander_review").to_dict()
                        if dependencies else None
                    ))
                binding = bind_semantic_program_runtime(modified, capability_registry=self.registry, profile="commander_review")
                self.assertFalse(binding["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{marker}", binding["blockers"])

    def test_actual_kessig_transform_keeps_one_mana_value(self):
        session = self.session(20203002)
        source = self.add(session.engine, 'Kessig Prowler // Sinuous Predator', ref='value-kessig')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 4, 'G': 1})
        self.checkpoint(session)
        current = self.transform(session, source)
        self.assertEqual('Sinuous Predator', current.active_face)
        self.assertEqual(1, session.engine._effective_card_data(current)['mana_value'])
        self.assertEqual('', session.engine._effective_card_data(current)['mana_cost'])
        self.replay(session, load=True)

    def test_actual_eliminate_rejects_transformed_five_mana_creature(self):
        session = self.session(20203003)
        source = self.add(session.engine, 'Bonded Herdbeast // Plated Kilnbeast', ref='value-herd')
        removal = self.add(session.engine, 'Eliminate', zone='hand', ref='value-removal')
        self.add(session.engine, 'Generic Bound Body', seat='B', ref='value-small-contrast')
        self.assert_closed(session, source)
        self.assert_closed(session, removal)
        self.prepare(session, mana={'C': 5, 'B': 1})
        self.checkpoint(session)
        current = self.transform(session, source)
        self.assertEqual('Plated Kilnbeast', current.active_face)
        self.reject(session, 'cast:' + removal.ref, targets=[current.ref], pay='auto')
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_actual_clone_of_back_face_is_zero_and_eliminate_uses_that_value(self):
        session = self.session(20203004)
        engine = session.engine
        source = self.add(engine, 'Bonded Herdbeast // Plated Kilnbeast', ref='value-original-back')
        clone = self.add(engine, 'Clone', zone='hand', ref='value-physical-clone')
        removal = self.add(engine, 'Eliminate', zone='hand', ref='value-copy-removal')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='value-private')
        for card in (source, clone, removal):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 8, 'U': 1, 'B': 1})
        self.checkpoint(session)
        current = self.transform(session, source)
        self.assertEqual(5, session.engine._effective_card_data(current)['mana_value'])
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:B', full=True)))
        self.cast(session, 'A', clone)
        pending = self.resolve(session)
        self.assertEqual('replacement.order', pending.kind)
        session = self.replay(session, load=True)
        option = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['options']
            if current.ref in row['id'] and not row['id'].startswith('decline:'))
        self.reject(session, 'choose', 'D', replacement=option['id'])
        result = session.act('pilot:A', {'action_id': 'choose', 'replacement': option['id']})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        copied = session.state.cards[clone.object_id]
        self.assertEqual(0, session.engine._effective_card_data(copied)['mana_value'])
        self.assertEqual('', session.engine._effective_card_data(copied)['mana_cost'])
        self.assertEqual(5, session.engine._effective_card_data(session.state.cards[source.object_id])['mana_value'])
        self.reject(session, 'cast:' + removal.ref, targets=[session.state.cards[source.object_id].ref], pay='auto')
        self.cast(session, 'A', removal, [copied.ref])
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[clone.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.replay(session)

    def test_actual_token_copy_of_back_face_has_zero_mana_value(self):
        session = self.session(20203005)
        engine = session.engine
        source = self.add(engine, 'Bonded Herdbeast // Plated Kilnbeast', ref='value-token-original')
        spell = self.add(engine, 'Cackling Counterpart', zone='hand', ref='value-token-copy')
        self.assert_closed(session, source)
        self.assert_closed(session, spell)
        self.prepare(session, mana={'C': 5, 'U': 2})
        self.checkpoint(session)
        current = self.transform(session, source, reserve={'U': 2})
        self.assertEqual(5, session.engine._effective_card_data(current)['mana_value'])
        self.cast(session, 'A', spell, [current.ref])
        self.resolve(session)
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(1, len(tokens))
        self.assertEqual(0, session.engine._effective_card_data(tokens[0])['mana_value'])
        self.assertEqual('', session.engine._effective_card_data(tokens[0])['mana_cost'])
        self.replay(session, load=True)

    def test_original_face_value_mutation_is_detected_by_actual_transform(self):
        from quorune.rules.face_characteristic_values import printed_face_mana_value
        with mock.patch('quorune.carddb_characteristics.current_face_mana_value',
                side_effect=lambda record, face: printed_face_mana_value(face)):
            with self.assertRaises(AssertionError):
                self.test_actual_kessig_transform_keeps_one_mana_value()

    def test_copying_original_derived_value_is_detected_by_actual_clone(self):
        def old_snapshot(card, record, base):
            values = dict(base)
            values.update(deepcopy(dict(card.annotations.get('copy_overrides') or {})))
            return values
        with mock.patch('quorune.ability_fragment_host.copiable_card_characteristics', side_effect=old_snapshot):
            with self.assertRaises(AssertionError):
                self.test_actual_clone_of_back_face_is_zero_and_eliminate_uses_that_value()
