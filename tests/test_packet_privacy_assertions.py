"""Opaque authorization tokens are not semantic card-reference projections."""
from copy import deepcopy
import json
import unittest

from common import packet_without_decision_capability


class PacketPrivacyAssertionTests(unittest.TestCase):
    def packet(self):
        return {'mode':'full','principal':'pilot:A','state':{'players':{'B':{'hand_n':3}}},
            'decision':{'kind':'priority','cap':'not-a-real-capability-containing-B04',
                'ctx':{'legal_actions':[{'id':'pass'}]}}}

    def test_opaque_capability_collision_does_not_report_a_card_reference_leak(self):
        packet=self.packet();original=deepcopy(packet)
        with self.assertRaises(AssertionError):self.assertNotIn('B04',json.dumps(packet))
        filtered=packet_without_decision_capability(packet)
        self.assertNotIn('B04',json.dumps(filtered))
        self.assertEqual(original,packet,'privacy inspection must not alter authorization tokens')
        self.assertEqual(packet['decision']['ctx'],filtered['decision']['ctx'])
        self.assertNotIn('cap',filtered['decision'])

    def test_state_context_and_nested_cap_fields_still_expose_injected_reference_leaks(self):
        for surface in ('hand','context','nested_cap'):
            with self.subTest(surface=surface):
                packet=self.packet()
                if surface=='hand':packet['state']['players']['B']['hand']=[{'ref':'B04'}]
                elif surface=='context':packet['decision']['ctx']['message']='Hidden card B04 must stay hidden'
                else:packet['decision']['ctx']['cap']='B04'
                with self.assertRaises(AssertionError):
                    self.assertNotIn('B04',json.dumps(packet_without_decision_capability(packet)))

    def test_missing_or_null_decisions_preserve_the_complete_packet(self):
        for packet in ({'state':{'private_ref':'B04'}},{'state':{'private_ref':'B04'},'decision':None}):
            self.assertEqual(packet,packet_without_decision_capability(packet))
