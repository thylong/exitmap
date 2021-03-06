#!/usr/bin/env python

# Copyright 2015 Philipp Winter <phw@nymity.ch>
#
# This file is part of exitmap.
#
# exitmap is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# exitmap is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with exitmap.  If not, see <http://www.gnu.org/licenses/>.
"""
Implements unit tests.
"""

import unittest

import stem.control
from stem import CircStatus

import util
import stats


class TestUtil(unittest.TestCase):
    """Test the util module."""

    def test_get_relays_in_country(self):
        relays1 = util.get_relays_in_country("at")
        self.assertTrue(len(relays1) > 0)

        relays2 = util.get_relays_in_country("AT")
        self.assertTrue(len(relays1) == len(relays2))

        bogus = util.get_relays_in_country("foo")
        self.assertEqual(bogus, [])

    def test_get_source_port(self):
        self.assertEqual(util.get_source_port("SOURCE_ADDR="
                                              "255.255.255.255:0"), 0)
        self.assertEqual(util.get_source_port("SOURCE_ADDR=1.1.1.1:1"), 1)
        self.assertEqual(util.get_source_port("SOURCE_ADDR=1.1.1.1:"
                                              "65535"), 65535)

    def test_exiturl(self):
        self.assertEqual(util.exiturl("foo"), "<https://atlas.torproject.or"
                         "g/#details/foo>")
        self.assertEqual(util.exiturl(4), "<https://atlas.torproject.org/#det"
                         "ails/4>")

    def test_extract_pattern(self):
        extract_pattern1 = util.extract_pattern("Connection on fd 4 originat"
                                                "ing from 444:0000", "Connec"
                                                "tion on fd [0-9]+ originati"
                                                "ng from [^:]+:([0-9]{1,5})")
        self.assertEqual(extract_pattern1, "0000")

    def test_new_request(self):
        result = util.new_request("https://atlas.torproject.org", "test")
        self.assertEqual("https://atlas.torproject.org", result.get_full_url())
        self.assertTrue(result.has_header("User-agent"))
        self.assertTrue(result.has_header("Accept"))
        self.assertTrue(result.has_header("Accept-language"))
        self.assertTrue(result.has_header("Accept-encoding"))
        self.assertTrue(result.has_header("Connection"))

    def test_parse_log_lines(self):
        ports = {"socks": -1, "control": -1}
        util.parse_log_lines(ports, "Socks listener listening on port 8000.")
        util.parse_log_lines(ports, "Control listener listening on port 9000.")
        self.assertEqual(ports["socks"], 8000)
        self.assertEqual(ports["control"], 9000)


class TestStats(unittest.TestCase):
    """Test the stats module."""

    def setUp(self):
        self.stats = stats.Statistics()

    def test_stats(self):
        self.stats.print_progress(sampling=0)
        self.stats.print_progress
        self.assertTrue(str(self.stats))

        circ_event = stem.response.events.CircuitEvent("foo", "bar")
        circ_event.status = CircStatus.FAILED
        circ_event.reason = "foo"

        self.stats.update_circs(circ_event)
        self.assertEqual(self.stats.failed_circuits, 1)

        circ_event.status = CircStatus.BUILT

        self.stats.update_circs(circ_event)
        self.assertEqual(self.stats.successful_circuits, 1)


if __name__ == '__main__':
    unittest.main()
