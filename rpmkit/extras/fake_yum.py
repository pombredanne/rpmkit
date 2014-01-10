#
# Copyright (C) 2013, 2014 Red Hat, Inc.
# Author: Satoru SATOH <ssato@redhat.com>
# License: GPLv3+
#
from __future__ import print_function
from logging import DEBUG, INFO

import rpmkit.rpmutils as RR
import rpmkit.utils as RU

import anyconfig
import dnf.cli.cli
import dnf.transaction
import logging
import optparse
import os.path
import re
import sys


def get_base(root):
    """
    :param root: RPM DB root dir
    """
    base = dnf.cli.cli.BaseCli()

    if root != '/':
        base.conf.installroot = base.conf.cachedir = root

    base.fill_sack(load_available_repos=False)
    base.goal_parameters.allow_uninstall = True

    return base


def list_removed(pkgspecs, root):
    """
    :param root: RPM DB root dir (relative or absolute)
    :param pkgspecs: a list of names or wildcards specifying packages to erase

    :return: A list of name of packages to be removed
    """
    root = os.path.abspath(root)

    base = get_base(root)
    for pspec in pkgspecs:
        base.remove(pspec)
    base.resolve()

    return sorted(set(x.erased.name for x in
                      base.transaction.get_items(dnf.transaction.ERASE)))


_USAGE = """\
%prog [OPTION ...] COMMAND [COMMAND_ARGS...]

Commands:
  rem[ove] RPM_NAMES_OR_PATTERNS_OR_FILE...
                 List removed RPMs along with the RPMs specified in args
  e[rase]        Same as the above
  s[tandalones]  List the standalone RPMs which required by not any other
                 RPMs nor requires any other RPMs
  l[eaves]       List the leaf RPMs which is required by no any other RPMs

Examples:
  %prog -R ./rhel-6-client-1 rem libreport abrt
  %prog -R ./rhel-6-client-1 -v e /path/to/rpm_list_to_removes.txt
  %prog -R ./rhel-6-client-1 -x ./rpm_list_to_keep.txt e NetworkManager'*'
  %prog -R ./rhel-6-client-1 e 'NetworkManager.*'  # In regexp.
  %prog -R ./rhel-6-client-1 s
  %prog -R ./rhel-6-client-1 leaves"""


_CMDS = (CMD_REMOVE, CMD_STANDALONES, CMD_LEAVES) = ("remove", "standalones",
                                                     "leaves")
_ARGS_CMD_MAP = dict(rem=CMD_REMOVE, e=CMD_REMOVE, s=CMD_STANDALONES,
                     l=CMD_LEAVES)

_FMT_CHOICES = tuple(anyconfig.list_types())


def option_parser(usage=_USAGE, ac_fmt_choices=_FMT_CHOICES):
    defaults = dict(verbose=False, root='/', excludes=None, output=None,
                    format="simple", st_rpms=1, use_dnf=False)

    p = optparse.OptionParser(usage)
    p.set_defaults(**defaults)

    fmt_choices = list(ac_fmt_choices) + ["simple"]
    fmt_choices_s = ', '.join(fmt_choices)

    p.add_option("-R", "--root",
                 help="Relative or absolute path to root dir where "
                      "var/lib/rpm exists. [/]")
    p.add_option("-x", "--excludes",
                 help="Comma separated RPM names to exclude from results "
                      "or path to file listing such RPM names line by line. "
                      "It's only effective for some sub commands such as "
                      "remove (erase) and standalones.")
    p.add_option("-o", "--output", help="Output file path [stdout]")
    p.add_option("-f", "--format", choices=fmt_choices,
                 help="Output format selected from %(type)s. If --option was "
                      "given and its file extension matches one of %(type)s, "
                      "the output format will be automatically selected. "
                      "[%%default]" % {"type": fmt_choices_s})
    p.add_option("", "--use-dnf", action="store_true",
                 help="Use DNF as dependency solving backend. "
                      "This is very experimental and only work w/ 'remove' "
                      "sub-command w/o --excludes option")
    p.add_option("-v", "--verbose", action="store_true", help="Verbose mode")

    sog = optparse.OptionGroup(p, "Options for standalones command")
    sog.add_option("", "--st-nrpms", type="int",
                   help="Number of RPMs to find standadlone RPMs. "
                        "Only RPMs has no requires/required RPMs will be "
                        "selected if it's 1 (default) and RPMs has N "
                        "requires and/or required RPMs at a maximum.")
    p.add_option_group(sog)

    return p


def load_list_from_file(filepath):
    """
    :param filepath: List file path
    :return: A list of results in ``filepath``
    """
    return [l.rstrip() for l in open(filepath).readlines()
            if l and not l.startswith('#')]


def is_file(filepath):
    """
    :param filepath: Maybe file path :: str
    """
    return os.path.exists(filepath) and os.path.isfile(filepath)


def main(cmd_map=_ARGS_CMD_MAP):
    p = option_parser()
    (options, args) = p.parse_args()

    RU.init_log(DEBUG if options.verbose else INFO)

    if not args:
        p.print_usage()
        sys.exit(1)

    (cmd, rpms) = (args[0], args[1:])

    cmd = cmd_map.get(cmd[0], cmd_map.get(cmd[:3], False))
    if not cmd:
        print("Error: Invalid command: " + cmd)
        p.print_usage()
        sys.exit(1)

    root = os.path.abspath(options.root)
    all_rpms = [p["name"] for p in RR.list_installed_rpms(root)]

    if options.excludes:
        if is_file(options.excludes):
            excludes = load_list_from_file(options.excludes)
        else:
            excludes = options.excludes.split(',')

        excludes = RU.select_from_list(excludes, all_rpms)
        logging.info("%d RPMs found in given excludes list" % len(excludes))
    else:
        excludes = []

    if cmd == CMD_REMOVE:
        if not rpms:
            print("remove (erase) command requires RPMs: list of RPM names or "
                  "glob/regex patterns, or a file contains RPM names or "
                  "glob/regex patterns line by line")
            sys.exit(1)

        if len(rpms) == 1 and is_file(rpms[0]):
            rpms = load_list_from_file(rpms[0])

        rpms = RU.select_from_list(rpms, all_rpms)

        if options.use_dnf:
            logging.warn("--use-dnf option does not support --excludes "
                         "and exludes are simply ignored.")
            xs = sorted(list_removed(rpms, root))
        else:
            xs = RR.compute_removed(rpms, root, excludes=excludes)

        data = dict(removed=xs, )

    elif cmd == CMD_STANDALONES:
        xs = sorted(RR.list_standalones(root, options.st_nrpms, excludes))
        data = dict(standalones=xs, )
    else:
        xs = sorted(RR.get_leaves(root))
        data = dict(leaves=xs, )

    output = open(options.output, 'w') if options.output else sys.stdout

    if options.format != "simple":
        if options.output:
            anyconfig.dump(dict(data=data, ), options.output,
                           forced_type=options.format)
        else:
            res = anyconfig.dumps(dict(data=data, ),
                                  forced_type=options.format)
            print(res)
    else:
        if options.output:
            ext = os.path.splitext(options.output)[1][1:]

            if ext in _FMT_CHOICES:
                anyconfig.dump(dict(data=data, ), options.output,
                               forced_type=ext)
            else:
                with open(options.output, 'w') as out:
                    for x in xs:
                        print(x, file=output)
        else:
            for x in xs:
                print(x)

    output.close()

if __name__ == "__main__":
    main()

# vim:sw=4:ts=4:et:
