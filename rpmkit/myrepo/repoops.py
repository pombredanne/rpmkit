#
# Copyright (C) 2011 Red Hat, Inc.
# Red Hat Author(s): Satoru SATOH <ssato@redhat.com>
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
import rpmkit.myrepo.shell as SH
import rpmkit.myrepo.utils as U
import rpmkit.rpmutils as RU

import glob
import logging
import operator
import os
import os.path
import subprocess
import tempfile


# timeouts:
(BUILD_TIMEOUT, MIN_TIMEOUT) = (60 * 10, 5)  # [sec]


def __destdir(repo):
    return os.path.join(repo.topdir, repo.distdir)


def __snd(x, y):
    """
    >>> __snd(1, 2)
    2
    """
    return y


def rpmdirs(repo, destdir=None):
    f = __snd if destdir is None else os.path.join
    return [f(destdir, d) for d in ["sources"] + repo.archs]


def dists_by_srpm(repo, srpm):
    return repo.dists[:1] if RU.is_noarch(srpm) else repo.dists


def mock_cfg_content(repo, dist):
    """
    Updated mock.cfg with addingg repository definitions in
    given content and returns it.

    :param repo:  Repo object
    :param dist:  Distribution object
    """
    cfg_opts = D.mockcfg_opts(dist.mockcfg())
    repo_defs = U.compile_template("release_file", repo.as_dict())

    cfg_opts["root"] = "%s-%s" % (repo.name, dist.label)
    cfg_opts["myrepo_distname"] = dist.name
    cfg_opts["yum.conf"] += "\n\n" + repo_defs

    context = {"cfg": cfg_opts}

    return U.compile_template("mock.cfg", context)


def sign_rpms_cmd(keyid, rpms):
    """
    TODO: It might ask user about the gpg passphrase everytime this method is
    called.  How to store the passphrase or streamline that with gpg-agent ?

    :param keyid:  GPG Key ID to sign with :: str
    :param rpms:  RPM file path list :: [str]
    """
    return U.compile_template("sign_rpms", {"keyid": keyid, "rpms": rpms})


def copy_cmd(repo, src, dst):
    if U.is_local(repo.server):
        if "~" in dst:
            dst = os.path.expanduser(dst)

        cmd = "cp -a %s %s" % (src, dst)
    else:
        cmd = "scp -p %s %s@%s:%s" % (src, repo.user, repo.server, dst)

    return cmd


def release_rpm_build_cmd(repo, workdir, release_file_path):
    logopt = logging.getLogger().level < logging.INFO and "--verbose" or ""

    context = repo.as_dict()
    context.update({
        "release_file": release_file_path,
        "workdir": workdir,
        "logopt": logopt,
        "release_file_list": os.path.join(workdir, "files.list"),
    })

    return U.compile_template("release_file_build", context)


def mock_cfg_rpm_build_cmd(repo, workdir, mock_cfg_file_list_path):
    context = repo.as_dict()
    context.update({
        "workdir": workdir,
        "mock_cfg_file_list": mock_cfg_file_list_path
    })

    return U.compile_template("mock_cfg_build", context)


def build_cmds(repo, srpm):
    return [
        SH.ThreadedCommand(d.build_cmd(srpm), timeout=repo.timeout) \
            for d in dists_by_srpm(repo, srpm)
    ]


# commands:
def build(repo, srpm):
    return SH.prun(build_cmds(repo, srpm))


def update(repo):
    """'createrepo --update ...', etc.
    """
    destdir = __destdir(repo)
    _TC = SH.ThreadedCommand

    # hack: degenerate noarch rpms
    if len(repo.archs) > 1:
        c = "for d in %s; "
        c += "   do (cd $d && ln -sf ../%s/*.noarch.rpm ./); "
        c += "done"
        c = c % (" ".join(repo.archs[1:]), repo.dists[0].arch)

        cmd = _TC(c, repo.user, repo.server, destdir, timeout=repo.timeout)
        cmd.run()

    c = "test -d repodata"
    c += " && createrepo --update --deltas --oldpackagedirs . --database ."
    c += " || createrepo --deltas --oldpackagedirs . --database ."

    cs = [
        _TC(c, repo.user, repo.server, d, timeout=repo.timeout) \
            for d in rpmdirs(repo, destdir)
    ]

    return SH.prun(cs)


def deploy(repo, srpm, build=True):
    """
    FIXME: ugly code around signkey check.
    """
    if build:
        assert all(rc == 0 for rc in build(repo, srpm))

    destdir = __destdir(repo)
    rpms_to_deploy = []   # :: [(rpm_path, destdir)]
    rpms_to_sign = []

    for d in dists_by_srpm(repo, srpm):
        srpm_to_copy = glob.glob("%s/*.src.rpm" % d.mockdir())[0]
        rpms_to_deploy.append((srpm_to_copy, os.path.join(destdir, "sources")))

        brpms = [
            f for f in glob.glob("%s/*.*.rpm" % d.mockdir())\
                if not f.endswith(".src.rpm")
        ]
        logging.debug("rpms=" + str([os.path.basename(f) for f in brpms]))

        for p in brpms:
            rpms_to_deploy.append((p, os.path.join(destdir, d.arch)))

        rpms_to_sign += brpms

    if repo.signkey:
        c = sign_rpms_cmd(repo.signkey, rpms_to_sign)
        subprocess.check_call(c, shell=True)

    cs = [
        SH.ThreadedCommand(repo.copy_cmd(rpm, dest), timeout=repo.timeout) \
            for rpm, dest in rpms_to_deploy
    ]
    assert all(rc == 0 for rc in SH.prun(cs))

    return update(repo)


def deploy_mock_cfg_rpm(repo, workdir):
    """Generate mock.cfg files and corresponding RPMs.
    """
    mockcfgdir = os.path.join(workdir, "etc", "mock")
    os.makedirs(mockcfgdir)

    mock_cfg_files = []

    for dist in repo.dists:
        mc = mock_cfg_content(repo, dist)
        mock_cfg_path = os.path.join(
            mockcfgdir, "%s-%s.cfg" % (repo.name, dist.label)
        )

        open(mock_cfg_path, "w").write(mc)

        mock_cfg_files.append(mock_cfg_path)

    listfile_path = os.path.join(workdir, "mockcfg.files.list")
    open(listfile_path, "w").write(
        "\n".join(
            "%s,rpmattr=%%config(noreplace)" % mcfg \
                for mcfg in mock_cfg_files) + "\n"
    )

    rc = SH.run(
        mock_cfg_rpm_build_cmd(repo, workdir, listfile_path),
        repo.user,
        timeout=BUILD_TIMEOUT
    )
    if rc != 0:
        raise RuntimeError("Failed to create mock.cfg rpm")

    srpms = glob.glob(
        "%(wdir)s/mock-data-%(repo)s-%(dver)s/mock-data-*.src.rpm" % \
            {"wdir": workdir, "repo": repo.name, "dver": repo.distversion}
    )
    if not srpms:
        raise RuntimeError("Failed to build src.rpm")

    srpm = srpms[0]

    deploy(repo, srpm)
    return update(repo)


def deploy_release_rpm(repo, workdir=None):
    """Generate (yum repo) release package.

    @workdir str   Working directory
    """
    if workdir is None:
        workdir = tempfile.mkdtemp(dir="/tmp",
                                   prefix="%s-release-" % repo.name
                                   )

    deploy_mock_cfg_rpm(repo, workdir)

    reldir = os.path.join(workdir, "etc", "yum.repos.d")
    os.makedirs(reldir)

    release_file_path = os.path.join(reldir, "%s.repo" % repo.name)
    open(release_file_path, 'w').write(rfc)

    if repo.signkey:
        keydir = os.path.join(workdir, repo.keydir[1:])
        os.makedirs(keydir)

        rc = SH.run(
            "gpg --export --armor %s > ./%s" % (repo.signkey, repo.keyfile),
            workdir=workdir,
            timeout=MIN_TIMEOUT,
        )

        release_file_list = os.path.join(workdir, "files.list")
        open(release_file_list, "w").write(
            release_file_path + ",rpmattr=%config\n" + workdir + \
                repo.keyfile + "\n"
        )

    rc = SH.run(
        release_rpm_build_cmd(repo, workdir, release_file_path),
        repo.user,
        timeout=BUILD_TIMEOUT
    )

    srpms = glob.glob(
        "%s/%s-release-%s/%s-release*.src.rpm" % \
            (workdir, repo.name, repo.distversion, repo.name)
    )
    if not srpms:
        raise RuntimeError("Failed to build src.rpm")

    srpm = srpms[0]

    deploy(repo, srpm)
    return update(repo)


def init(repo):
    """Initialize yum repository.
    """
    destdir = __destdir(repo)

    rc = SH.run(
        "mkdir -p " + " ".join(rpmdirs(repo, destdir)),
        repo.user, repo.server,
        timeout=repo.timeout
    )

    return deploy_release_rpm(repo)


# vim:sw=4 ts=4 et:
