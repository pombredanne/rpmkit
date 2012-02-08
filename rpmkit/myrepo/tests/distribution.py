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
import rpmkit.myrepo.distribution as D
import rpmkit.myrepo.environ as E

import random
import unittest


def sample_dist():
    return random.choice(E.list_dists())


def is_base_dist(dist):
    """
    Fixme: Ugly

    >>> is_base_dist("fedora-16-i386")
    True
    >>> is_base_dist("rhel-6-x86_64")
    True
    >>> is_base_dist("fedora-xyz-extras-fedora-16-x86_64")
    False
    """
    return len(dist.split("-")) == 3


def sample_base_dist():
    return random.choice([d for d in E.list_dists() if is_base_dist(d)])


class Test_00(unittest.TestCase):

    # NOTE: Effectful computation.
    def test_00_mockcfg_opts(self):
        bdist = sample_dist()
        self.assertTrue(isinstance(D.mockcfg_opts(bdist), dict))

    def test_10_build_cmd(self):
        bdist = sample_dist()
        c = D.build_cmd(bdist, "foo-x.y.z.src.rpm")
        self.assertTrue(isinstance(c, str))
        self.assertNotEquals(c, "")


"""
FIXME: 
Test_10_Distribution.test_01__init__w_arch sometimes fails.

======================================================================
ERROR: test_01__init__w_arch (rpmkit.myrepo.tests.distribution.Test_10_Distribution)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/ssato/repos/public/github.com/ssato/rpmkit.git/rpmkit/myrepo/tests/distribution.py", line 71, in test_01__init__w_arch
    d = D.Distribution(n, v, a)
  File "/home/ssato/repos/public/github.com/ssato/rpmkit.git/rpmkit/myrepo/distribution.py", line 77, in __init__
    self.mockcfg_opts = mockcfg_opts(self.bdist_label)
  File "/home/ssato/repos/public/github.com/ssato/rpmkit.git/rpmkit/memoize.py", line 25, in wrapped
    cache[key] = fn(*args, **kwargs)
  File "/home/ssato/repos/public/github.com/ssato/rpmkit.git/rpmkit/myrepo/distribution.py", line 43, in mockcfg_opts
    execfile(__mockcfg_path(bdist), cfg)
  File "/etc/mock/epel-5-ppc.cfg", line 6, in <module>
    config_opts['macros']['%__arch_install_post'] = '%{nil}'
KeyError: 'macros'
"""


class Test_10_Distribution(unittest.TestCase):

    def test_00__init__w_min_args(self):
        (n, v, _a) = sample_base_dist().split("-")
        d = D.Distribution(n, v)

        self.assertTrue(isinstance(d, D.Distribution))

    def test_01__init__w_arch(self):
        (n, v, a) = sample_base_dist().split("-")
        d = D.Distribution(n, v, a)

        self.assertTrue(isinstance(d, D.Distribution))

    def test_02__init__w_bdist_label(self):
        """
        FIXME: Distribution.__init__(..., bdist_label=bdist) requires mock
        config file actually exists for bdist.
        """
        return

        (n, v, a) = sample_base_dist().split("-")
        bdist = "%s-extra-packages-%s-%s-%s" % (n, n, v, a)
        d = D.Distribution(n, v, bdist_label=bdist)

        self.assertTrue(isinstance(d, D.Distribution))


# vim:sw=4 ts=4 et:
