#
# Copyright (C) 2012 Red Hat, Inc.
# Red Hat Author(s): Satoru SATOH <ssato at redhat.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
import rpmkit.rpmdb.datasrc.rhn as R
import rpmkit.rpmdb.models.packages as P
import random
import unittest


CHANNELS = []


class TestFunctions(unittest.TestCase):

    def test_00_rpc(self):
        global CHANNELS

        self.assertTrue(bool(R.rpc("api.getVersion")))

        CHANNELS = R.rpc("channel.listSoftwareChannels")
        self.assertTrue(bool(CHANNELS))

    def test_10_get_packages(self):
        global CHANNELS

        chan = random.choice(CHANNELS)
        xs = R.get_packages(chan["label"])

        self.assertTrue(bool(xs))

    def test_20_get_errata(self):
        global CHANNELS

        chan = random.choice(CHANNELS)
        xs = R.get_errata(chan["label"])

        self.assertTrue(bool(xs))

    def test_30_get_package_id(self):
        global CHANNELS

        chan = random.choice(CHANNELS)
        xs = R.get_packages(chan["label"])
        x = random.choice(xs)

        ys = R.get_package_id(x.name, x.version, x.release, x.epoch, x.arch)
        self.assertTrue(bool(ys))

    def test_40_get_package_files(self):
        global CHANNELS

        chan = random.choice(CHANNELS)
        xs = R.get_packages(chan["label"])
        x = random.choice(xs)

        ys = R.get_package_files(x.name, x.version, x.release, x.epoch, x.arch)
        self.assertTrue(bool(ys))

    def test_40_get_package_errata(self):
        global CHANNELS

        chan = random.choice(CHANNELS)
        xs = R.get_packages(chan["label"])
        x = random.choice(xs)

        ys = R.get_package_errata(x.name, x.version, x.release, x.epoch, x.arch)
        self.assertTrue(bool(ys))

    def test_40_get_cves(self):
        global CHANNELS

        xs = []

        while not xs:
            chan = random.choice(CHANNELS)
            xs = [
                x for x in R.get_errata(chan["label"]) \
                    if P.is_security_errata(x)
            ]

        x = random.choice(xs)

        ys = R.get_cves(x.advisory)
        self.assertTrue(bool(ys))


# vim:sw=4:ts=4:et:
