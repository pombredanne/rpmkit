#
# -*- coding: utf-8 -*-
#
# main module of rpmkit.updateinfo
#
# Copyright (C) 2013 Satoru SATOH <ssato@redhat.com>
# Copyright (C) 2013, 2014 Red Hat, Inc.
#
# License: GPLv3+
#
from rpmkit.globals import _
from operator import itemgetter

import rpmkit.updateinfo.yumwrapper
import rpmkit.updateinfo.yumbase
import rpmkit.updateinfo.dnfbase
import rpmkit.updateinfo.utils
import rpmkit.memoize
import rpmkit.rpmutils
import rpmkit.utils as U
import rpmkit.swapi

# It looks avaialble in EPEL for RHELs:
#   https://apps.fedoraproject.org/packages/python-bunch
import bunch
import datetime
import itertools
import logging
import operator
import os
import os.path
import tablib


LOG = logging.getLogger("rpmkit.updateinfo")

_RPM_LIST_FILE = "packages.json"
_ERRATA_LIST_FILE = "errata.json"
_UPDATES_LIST_FILE = "updates.json"

BACKENDS = dict(yumwrapper=rpmkit.updateinfo.yumwrapper.Base,
                yumbase=rpmkit.updateinfo.yumbase.Base,
                dnfbase=rpmkit.updateinfo.dnfbase.Base)
DEFAULT_BACKEND = BACKENDS["yumbase"]
ERRATA_KEYWORDS = ["crash", "panic", "hang", "SEGV", "segmentation fault"]
DEFAULT_CVSS_SCORE = 4.0


def rpm_list_path(workdir, filename=_RPM_LIST_FILE):
    """
    :param workdir: Working dir to dump the result
    :param filename: Output file basename
    """
    return os.path.join(workdir, filename)


def errata_list_path(workdir, filename=_ERRATA_LIST_FILE):
    """
    :param workdir: Working dir to dump the result
    :param filename: Output file basename
    """
    return os.path.join(workdir, filename)


def updates_file_path(workdir, filename=_UPDATES_LIST_FILE):
    """
    :param workdir: Working dir to dump the result
    """
    return os.path.join(workdir, filename)


def dataset_file_path(workdir):
    """
    :param workdir: Working dir to dump the result
    :param filename: Output file basename
    """
    return os.path.join(workdir, "errata_summary.xls")


def mk_cve_vs_cvss_map():
    """
    Make up CVE vs. CVSS map w/ using swapi's virtual APIs.

    :return: A list of CVE details :: {cve: {cve, url, score, metrics}, }
    """
    return dict((c["cve"], c) for c in
                rpmkit.swapi.call("swapi.cve.getAll") if c)


@rpmkit.memoize.memoize
def get_cve_details(cve, cve_cvss_map={}):
    """
    :param cve: A dict represents CVE :: {id:, url:, ...}
    :param cve_cvss_map: A dict :: {cve: cve_and_cvss_data}

    :return: A dict represents CVE and its CVSS metrics
    """
    cveid = cve.get("id", cve.get("cve"))
    dcve = cve_cvss_map.get(cveid)
    if dcve:
        cve.update(**dcve)
        return cve

    dcve = rpmkit.swapi.call("swapi.cve.getCvss", [cveid])
    if dcve:
        dcve = dcve[0]  # :: dict
        dcve["nvd_url"] = dcve["url"]
        dcve["url"] = cve["url"]
    else:
        LOG.warn("Could not get CVSS metrics of %s", cveid)
        dcve = dict(cve=cveid, )

    cve.update(**dcve)
    return cve


def add_cvss_for_errata(errata, cve_cvss_map={}):
    """
    Complement CVSS data for CVE relevant to errata

    :param errata: Basic errata info, {advisory, type, severity, ...}
    :param cve_cvss_map: A dict :: {cve: cve_and_cvss_data}
    """
    cves = errata.get("cves", [])
    if not cves:
        return errata

    errata["cves"] = [get_cve_details(cve, cve_cvss_map) for cve in cves]
    return errata


def _fmt_cve(cve):
    if 'score' in cve:
        return '%(cve)s (score=%(score)s, metrics=%(metrics)s, url=%(url)s)'
    else:
        return '%(cve)s (CVSS=N/A)'


def _fmt_cvess(cves):
    """
    :param cves: List of CVE dict {cve, score, url, metrics} or str "cve".
    :return: List of CVE strings
    """
    try:
        cves = [_fmt_cve(c) % c for c in cves]
    except KeyError:
        pass
    except Exception as e:
        raise RuntimeError("Wrong CVEs: %s, exc=%s" % (str(cves), str(e)))

    return cves


def _fmt_bzs(bzs):
    """
    :param cves: List of CVE dict {cve, score, url, metrics} or str "cve".
    :return: List of CVE strings
    """
    def _fmt(bz):
        if "summary" in bz:
            return "bz#%(id)s: %(summary)s (%(url)s"
        else:
            return "bz#%(id)s (%(url)s"

    try:
        bzs = [_fmt(bz) % bz for bz in bzs]
    except KeyError:
        LOG.warn("BZ Key error: " + str(bzs))
        pass

    return bzs


def _make_cell_data(x, key, default="N/A"):
    if key == "cves":
        cves = x.get("cves", [])
        try:
            return ", ".join(_fmt_cvess(cves)) if cves else default
        except Exception as e:
            raise RuntimeError("Wrong CVEs: %s, exc=%s" % (str(cves), str(e)))

    elif key == "bzs":
        bzs = x.get("bzs", [])
        return ", ".join(_fmt_bzs(bzs)) if bzs else default

    else:
        v = x.get(key, default)
        return ", ".join(v) if isinstance(v, (list, tuple)) else v


def _make_dataset(list_data, headers=None, title=None):
    """
    :param list_data: List of data
    :param headers: Dataset headers to be used as column headers
    :param title: Dataset title to be used as worksheet's name
    """
    dataset = tablib.Dataset()

    # TODO: Check title as valid worksheet name, ex. len(title) <= 31.
    # See also xlwt.Utils.valid_sheet_name.
    if title:
        dataset.title = title

    if headers:
        dataset.headers = [h.replace('_s', '') for h in headers]

        for x in list_data:
            dataset.append([_make_cell_data(x, h) for h in headers])
    else:
        for x in list_data:
            dataset.append(x.values())

    return dataset


def _date_from_errata_issue_data(date_s):
    """
    NOTE: Errata issue_date and update_date format: month/day/year,
        e.g. 12/16/10.

    >>> _date_from_errata_issue_data("12/16/10")
    (2010, 12, 16)
    """
    (m, d, y) = date_s.split('/')
    return (int("20" + y), int(m), int(d))


def _is_newer_errata(errata, since=None):
    """
    NOTE: issue_date format: month/day/year, e.g. 12/16/10

    :errata: A dict {advisory:, issue_date:, } represents an errata
    :since: Limit date in the formats, YY/MM/DD or YY-MM-DD

    >>> e = dict(advisory="RHBA-2010:0993", issue_date="12/16/10")
    >>> _is_newer_errata(e, None)
    True
    >>> _is_newer_errata(e, "2010-11-01")
    True
    >>> _is_newer_errata(e, "2010/11/01")
    True
    >>> _is_newer_errata(e, "2010-12-16")
    False
    >>> _is_newer_errata(e, "2010-12-31")
    False
    """
    if since is None:
        return True  # Unknown

    (y, m, d) = since.split('-' if '-' in since else '/')
    (y, m, d) = (int(y), int(m), int(d))
    # LOG.debug("Try to check the errata is newer than "
    #              "y=%d, m=%d, d=%d" % (y, m, d))

    # Set to dummy and old enough date if failed to get issue_date.
    issue_date = errata.get("issue_date", "1900-01-01")
    (e_y, e_m, e_d) = _date_from_errata_issue_data(issue_date)
    # LOG.debug("Issue date of the errata: y=%d, m=%d, d=%d" % (e_y, e_m,
    #                                                              e_d))

    if e_y < y:
        return False
    elif e_y > y:
        return True
    else:
        if e_m < m:
            return False
        elif e_m > m:
            return True
        else:
            if e_m < m or (e_m == m and e_d <= d):
                return False

    return True


def cve_socre_ge(cve, score=DEFAULT_CVSS_SCORE, default=False):
    """
    :param cve: A dict contains CVE and CVSS info.
    :param score: Lowest score to select CVEs (float). It's Set to 4.0 (PCIDSS
        limit) by default:

        * NVD Vulnerability Severity Ratings: http://nvd.nist.gov/cvss.cfm
        * PCIDSS: https://www.pcisecuritystandards.org

    :param default: Default value if failed to get CVSS score to compare with
        given score

    :return: True if given CVE's socre is greater or equal to given score.
    """
    try:
        return float(cve["score"]) >= float(score)
    except Exception:
        LOG.warn("Failed to compare CVE's score: %s, score=%.1f",
                 str(cve), score)

    return default


def has_higher_score_cve(errata, score=DEFAULT_CVSS_SCORE):
    """
    :param errata: A dict represents errata info
    :param score: Limit value of CVSS base metrics score or None
    """
    if errata.get("cves", False):
        return any(cve_socre_ge(cve, score) for cve in errata["cves"])

    return False


@rpmkit.memoize.memoize
def errata_list_unique_src_updates(errata):
    """
    :param errata: A dict represents errata info
    """
    return [sorted(g, key=itemgetter("name"))[0] for k, g in
            itertools.groupby(sorted(errata.get("packages", []),
                                     key=itemgetter("src")),
                              itemgetter("src"))]


@rpmkit.memoize.memoize
def is_subset_or_older_errata(errata, errata_ref):
    """
    Return True if `errata` has relevant update packages which is a subset of
    ones of `errata_ref` or has same relevant update packages older than ones
    of `errata_ref`. That is, return True if `errata` is not needed to apply
    because `errata_ref` subsumes `errata` and application of `errata_ref` is
    enough.

    :param errata: A dict represents errata info
    :param errata_ref: A dict represents errata info to compare with

    :return: True if update packages of `errata` is a sub set of ones of
        `errata_ref` or `errata_ref` can become an alternative of `errata`.
    """
    us = sorted(errata_list_unique_src_updates(errata))
    rs = sorted(errata_list_unique_src_updates(errata_ref))

    if len(us) > len(rs):
        return False  # `errata` has updates not in `errata_ref`.

    uns = set(p["name"] for p in us)
    rns = set(p["name"] for p in rs)

    if uns <= rns:
        return all(rpmkit.rpmutils.pcmp(u, r) < 0 for u, r
                   in itertools.izip(us, rs))

    return False


def errata_group_and_sort_by_updates(errata):
    """
    :param errata: A list of errata dict
    """
    pass


def p2na(pkg):
    """
    :param pkg: A dict represents package info including N, E, V, R, A
    """
    return (pkg["name"], pkg["arch"])


def list_updates_from_errata(errata):
    """
    :param errata: A list of errata dict
    """
    us = sorted(U.uconcat(e.get("updates", []) for e in errata),
                key=itemgetter("name"))
    return [sorted(g, cmp=rpmkit.rpmutils.pcmp, reverse=True)[0] for k, g
            in itertools.groupby(us, itemgetter("name"))]


def compute_delta(refdir, errata, updates):
    """
    :param refdir: Dir has reference data files: packages.json, errata.json
        and updates.json
    :param errata: A list of errata
    :param updates: A list of update packages
    """
    emsg = "Reference %s not found: %s"
    assert os.path.exists(refdir), emsg % ("data dir", refdir)

    ref_es_file = os.path.join(refdir, "errata.json")
    ref_us_file = os.path.join(refdir, "updates.json")
    assert os.path.exists(ref_es_file), emsg % ("errata file", ref_es_file)
    assert os.path.exists(ref_us_file), emsg % ("updates file", ref_us_file)

    ref_es_data = U.json_load(ref_es_file)
    ref_us_data = U.json_load(ref_us_file)
    LOG.info("Loaded reference errata and updates file")

    nevra_keys = ("name", "epoch", "version", "release", "arch")
    ref_eadvs = U.uniq(e["advisory"] for e in ref_es_data["data"])
    ref_nevras = U.uniq([p[k] for k in nevra_keys] for p in
                        ref_us_data["data"])

    return ([e for e in errata if e["advisory"] in ref_eadvs],
            [u for u in updates if [u[k] for k in nevra_keys]
             in ref_nevras])


def errata_matches_keywords_g(errata, keywords=ERRATA_KEYWORDS):
    """
    :param errata: A list of errata
    :param keywords: Keyword list to filter 'important' RHBAs
    """
    for e in errata:
        mks = [k for k in keywords if k in e["description"]]
        if mks:
            e["keywords"] = mks
            yield e


def higher_score_cve_errata_g(errata, score=DEFAULT_CVSS_SCORE):
    """
    :param errata: A list of errata
    :param score: CVSS base metrics score
    """
    for e in errata:
        cves = e.get("cves", [])
        if cves and any(cve_socre_ge(cve, score) for cve in cves):
            cvsses_s = ", ".join("{cve} ({score}, {metrics})".format(**c)
                                 for c in cves)
            cves_s = ", ".join("{cve} ({url})".format(**c) for c in cves)
            e["cvsses_s"] = cvsses_s
            e["cves_s"] = cves_s

            yield e


def errata_complement_g(errata, updates):
    """
    TODO: What should be complemented?

    :param errata: A list of errata
    :param updates: A list of update packages
    """
    unas = set(p2na(u) for u in updates)
    for e in errata:
        e["updates"] = U.uniq(p for p in e.get("packages", [])
                              if p2na(p) in unas)
        e["update_names"] = U.uniq(u["name"] for u in e["updates"])
        e["bzs_s"] = ", ".join("rhbz#%s" % bz["id"] for bz in e.get("bzs", []))

        yield e


def analyze_errata(errata, updates, score=-1, keywords=ERRATA_KEYWORDS):
    """
    :param errata: A list of applicable errata sorted by severity
        if it's RHSA and advisory in ascending sequence
    :param updates: A list of update packages
    :param score: CVSS base metrics score
    :param keywords: Keyword list to filter 'important' RHBAs
    """
    errata = list(errata_complement_g(errata, updates))

    rhsa = [e for e in errata if e.get("severity", None) is not None]
    rhsa_cri = [e for e in rhsa if e.get("severity") == "Critical"]
    rhsa_imp = [e for e in rhsa if e.get("severity") == "Important"]

    # TODO: degenerate errata by listing only latest update rpms:
    us_of_rhsa_cri = list_updates_from_errata(rhsa_cri)
    us_of_rhsa_imp = list_updates_from_errata(rhsa_imp)

    is_rhba = lambda e: e["advisory"].startswith("RHBA")

    rhba = [e for e in errata if is_rhba(e)]
    rhba_by_kwds = list(errata_matches_keywords_g(rhba, keywords))

    if score < 0:
        rhsa_by_score = []
        rhba_by_score = []
        us_of_rhba_by_score = []
    else:
        rhsa_by_score = list(higher_score_cve_errata_g(rhsa, score))
        rhba_by_score = list(higher_score_cve_errata_g(rhba, score))
        us_of_rhsa_by_score = list_updates_from_errata(rhsa_by_score)
        us_of_rhba_by_score = list_updates_from_errata(rhba_by_score)

    us_of_rhba_by_kwds = list_updates_from_errata(rhba_by_kwds)

    rhea = [e for e in errata if e["advisory"].startswith("RHEA")]

    return dict(rhsa=rhsa, rhsa_cri=rhsa_cri, rhsa_imp=rhsa_imp,
                rhsa_by_cvss_score=rhsa_by_score,
                us_of_rhsa_cri=us_of_rhsa_cri, us_of_rhsa_imp=us_of_rhsa_imp,
                rhba=rhba, rhba_by_kwds=rhba_by_kwds,
                rhba_by_cvss_score=rhba_by_score,
                us_of_rhba_by_kwds=us_of_rhba_by_kwds,
                us_of_rhsa_by_cvss_score=us_of_rhsa_by_score,
                us_of_rhba_by_cvss_score=us_of_rhba_by_score,
                rhea=rhea)


def make_summary_dataset(workdir, data, score=-1):
    """
    :param workdir: Working dir to dump the result
    :param data: RPMs, Update RPMs and various errata data summarized
    :param score: CVSS base metrics score limit
    """
    ds = [(_("# of Security Errata (critical)"),
           len(data["errata"]["rhsa_cri"]), "", ""),
          (_("# of Security Errata (important)"),
           len(data["errata"]["rhsa_imp"]), "", ""),
          (_("# of RPMs need to be updated by Security Errata (critical)"),
           len(data["errata"]["us_of_rhsa_cri"]), "", ""),
          (_("# of RPMs need to be updated by Security Errata (important)"),
           len(data["errata"]["us_of_rhsa_imp"]), "", ""),
          (_("# of Bug Errata (keyword)"),
           len(data["errata"]["rhba_by_kwds"]), "", ""),
          (_("# of RPMs need to be updated by Bug Errata (keyword)"),
           len(data["errata"]["us_of_rhba_by_kwds"]), "", "")]

    if score < 0:
        cvss_ds = []
    else:
        cvss_ds = [(_("# of Security Errata (CVSS Score >= "
                      "%.1f)") % score,
                    len(data["errata"]["rhsa_by_cvss_score"]), "", ""),
                   (_("# of Bug Errata (CVSS Score >= "
                      "%.1f)") % score,
                    len(data["errata"]["rhba_by_cvss_score"]), "", ""),
                   (_("# of RPMs need to be updated by Security Errata "
                      "(CVSS Score >= %.1f)") % score,
                    len(data["errata"]["us_of_rhsa_by_cvss_score"]), "", ""),
                   (_("# of RPMs need to be updated by Bug Errata "
                      "(CVSS Score >= %.1f)") % score,
                    len(data["errata"]["us_of_rhba_by_cvss_score"]), "", "")]

    others_ds = [(_("# of Security Errata (all)"),
                  len(data["errata"]["rhsa"]), "", ""),
                 (_("# of Bug Errata"), len(data["errata"]["rhba"]), "", ""),
                 # TODO: Needed ?
                 # (_("# of Enhancement Errata"),
                 #  len(data["errata"]["rhea"]), "-", ""),
                 (_("# of Installed RPMs"), len(data["installed"]), "", ""),
                 (_("# of Update RPMs"), len(data["updates"]), "", "")]

    dataset = tablib.Dataset()
    dataset.title = _("Summary")
    dataset.headers = (_("item"), _("value"), _("rating"), _("comments"))
    for d in ds + cvss_ds + others_ds:
        dataset.append(d)

    return dataset


def dump_datasets(workdir, rpms, errata, updates, score=-1,
                  keywords=ERRATA_KEYWORDS):
    """
    :param workdir: Working dir to dump the result
    :param rpms: A list of installed RPMs
    :param errata: A list of applicable errata
    :param updates: A list of update RPMs
    :param score: CVSS base metrics score
    :param keywords: Keyword list to filter 'important' RHBAs
    """
    data = dict(errata=analyze_errata(errata, updates, score, keywords),
                rpms=rpms, installed=rpms, updates=updates,
                rpmnames_need_updates=U.uniq(u["name"] for u in updates))
    U.json_dump(data, os.path.join(workdir, "summary.json"))

    rpmkeys = ("name", "version", "release", "epoch", "arch", "summary",
               "vendor", "buildhost")

    summary_ds = [make_summary_dataset(workdir, data, score)]
    base_ds = [_make_dataset(updates,
                             ("name", "version", "release", "epoch", "arch"),
                             _("Update RPMs")),
               _make_dataset(errata,
                             ("advisory", "type", "severity", "synopsis",
                              "description", "issue_date", "update_date",
                              "url", "cves_s", "bzs_s", "update_names"),
                             _("Errata Details")),
               _make_dataset(rpms, rpmkeys, _("Installed RPMs"))]

    ekeys = ("advisory", "synopsis", "url", "update_names")
    urpmkeys = ("name", "version", "release", "epoch", "arch")

    main_ds = [_make_dataset(data["errata"]["rhsa_cri"], ekeys,
                             _("RHSAs (Critical)")),
               _make_dataset(data["errata"]["us_of_rhsa_cri"], urpmkeys,
                             _("Update RPMs by RHSAs (Critical)")),
               _make_dataset(data["errata"]["rhsa_imp"], ekeys,
                             _("RHSAs (Important)")),
               _make_dataset(data["errata"]["us_of_rhsa_imp"], urpmkeys,
                             _("Updates by RHSAs (Important)")),
               _make_dataset(data["errata"]["rhba_by_kwds"],
                             ("advisory", "synopsis", "keywords", "url",
                              "update_names"),
                             _("RHBAs (keyword)")),
               _make_dataset(data["errata"]["us_of_rhba_by_kwds"], urpmkeys,
                             _("Updates by RHBAs (Keyword)"))]

    if score >= 0:
        cvss_ds = [_make_dataset(data["errata"]["rhsa_by_cvss_score"],
                                 ("advisory", "severity", "synopsis",
                                  "cves_s", "cvsses_s", "url"),
                                 _("RHSAs (CVSS score >= %.1f)") % score),
                   _make_dataset(data["errata"]["rhba_by_cvss_score"],
                                 ("advisory", "synopsis", "cves_s",
                                  "cvsses_s", "url"),
                                 _("RHBAs (CVSS score >= %.1f)") % score)]
        main_ds.extend(cvss_ds)

    book = tablib.Databook(summary_ds + main_ds + base_ds)

    with open(dataset_file_path(workdir), 'wb') as out:
        out.write(book.xls)


def get_backend(backend, fallback=rpmkit.updateinfo.yumbase.Base,
                backends=BACKENDS):
    return backends.get(backend, fallback)


def prepare(root, workdir=None, repos=[], did=None,
            backend=DEFAULT_BACKEND, backends=BACKENDS):
    """
    :param root: Root dir of RPM db, ex. / (/var/lib/rpm)
    :param workdir: Working dir to save results
    :param repos: List of yum repos to get updateinfo data (errata and updtes)
    :param did: Identity of the data (ex. hostname) or empty str
    :param backend: Backend module to use to get updates and errata
    :param backends: Backend list

    :return: A bunch.Bunch object of (Base, workdir, installed_rpms_list)
    """
    if not rpmkit.updateinfo.utils.check_rpmdb_root(root, True):
        raise RuntimeError("Not a root of RPM DB: %s [%s]" % root, did)

    if workdir is None:
        LOG.info("Set workdir to root [%s]: %s", did, root)
        workdir = root
    else:
        if not os.path.exists(workdir):
            LOG.info("Creating working dir [%s]: %s", did, workdir)
            os.makedirs(workdir)

    host = bunch.bunchify(dict(id=did, root=root, workdir=workdir,
                               repos=repos))

    base = get_backend(backend)(host.root, host.repos, workdir=host.workdir)
    LOG.info("Initialized backend [%s]: backend=%s", host.id, base.name)
    host.base = base

    LOG.info("Dump Installed RPMs list loaded from: %s [%s]",
             host.root, host.id)
    host.installed = sorted(host.base.list_installed(),
                            key=operator.itemgetter("name", "epoch", "version",
                                                    "release"))
    LOG.info("%d Installed RPMs found [%s]", len(host.installed), host.id)
    U.json_dump(dict(data=host.installed, ), rpm_list_path(host.workdir))

    return host


def main(root, workdir=None, repos=[], did=None, score=-1,
         keywords=ERRATA_KEYWORDS, refdir=None, backend=DEFAULT_BACKEND,
         backends=BACKENDS):
    """
    :param root: Root dir of RPM db, ex. / (/var/lib/rpm)
    :param workdir: Working dir to save results
    :param repos: List of yum repos to get updateinfo data (errata and updtes)
    :param did: Identity of the data (ex. hostname) or empty str
    :param backend: Backend module to use to get updates and errata
    :param score: CVSS base metrics score
    :param keywords: Keyword list to filter 'important' RHBAs
    :param refdir: A dir holding reference data previously generated to
        compute delta (updates since that data)
    :param backends: Backend list
    """
    host = prepare(root, workdir, repos, did, backend, backends)

    timestamp = datetime.datetime.now().strftime("%F %T")
    metadata = bunch.bunchify(dict(id=host.id, root=host.root,
                                   workdir=host.workdir, repos=host.repos,
                                   backend=host.base.name, score=score,
                                   keywords=keywords,
                                   installed=len(host.installed),
                                   generated=timestamp))
    LOG.info("Dump metadata [%s]: root=%s", metadata.id, metadata.root)
    U.json_dump(metadata.toDict(), os.path.join(workdir, "metadata.json"))

    base = host.base
    workdir = host.workdir

    LOG.info("Dump Errata list...")
    es = [add_cvss_for_errata(e, mk_cve_vs_cvss_map()) for e
          in base.list_errata()]
    LOG.info("%d Errata found for installed rpms [%s]", len(es), host.id)
    U.json_dump(dict(data=es, ), errata_list_path(workdir))
    host.errata = es

    LOG.info("Dump Update RPMs list...")
    us = base.list_updates()
    LOG.info("%d Update RPMs found for installed rpms [%s]", len(us), host.id)
    U.json_dump(dict(data=us, ), updates_file_path(workdir))
    host.updates = us

    ips = host.installed
    es = U.uniq(es, cmp=rpmkit.updateinfo.utils.cmp_errata)
    us = U.uniq(us, key=itemgetter("name", "epoch", "version", "release"))

    LOG.info("Dump dataset file from RPMs and Errata data...")
    dump_datasets(workdir, ips, es, us, score, keywords)

    if refdir:
        LOG.info("Computing delta errata and updates for data in %s", refdir)
        (es, us) = compute_delta(refdir, es, us)

        deltadir = os.path.join(workdir, "delta")
        if not os.path.exists(deltadir):
            LOG.info("Creating delta working dir [%s]: %s", host.id, deltadir)
            os.makedirs(deltadir)

        LOG.info("%d Delta Errata found for installed rpms [%s]", len(es),
                 host.id)
        U.json_dump(dict(data=es, ), errata_list_path(deltadir))

        LOG.info("%d Delta Update RPMs found for installed rpms [%s]",
                 len(us), host.id)
        U.json_dump(dict(data=us, ), updates_file_path(deltadir))

        es = sorted(es, cmp=rpmkit.updateinfo.utils.cmp_errata)
        us = sorted(us, key=itemgetter("name", "epoch", "version", "release"))

        LOG.info("Dump dataset file from RPMs and Errata data...")
        dump_datasets(workdir, ips, es, us, score, keywords)

# vim:sw=4:ts=4:et:
