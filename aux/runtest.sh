#! /bin/bash
set -e

curdir=${0%/*}
topdir=${curdir}/../

if `env | grep -q 'WITH_COVERAGE' 2>/dev/null`; then
    coverage_opts="--with-coverage --cover-tests"
fi

function _pylint () {
    pylint --rcfile=$curdir/pylintrc --errors-only $@
}

which pep8 2>&1 > /dev/null && check_with_pep8=1 || check_with_pep8=0
which flake8 2>&1 > /dev/null && { check_with_pep8=0; check_with_flake8=1;} || check_with_flake8=0
which pylint 2>&1 > /dev/null && check_with_pylint=1 || check_with_pylint=0

if test $# -gt 0; then
    if test $check_with_pep8 = 1; then
        for x in $@; do pep8 ${x%%:*}; done
    fi
    test $check_with_flake8 = 1 && flake8 $@
    if test $check_with_pylint = 1; then
        for x in $@; do _pylint ${x%%:*}; done
    fi
    PYTHONPATH=$topdir nosetests -c $curdir/nose.cfg ${coverage_opts} $@
else
    # Find out python package dir and run tests for .py files under it.
    for d in ${topdir}/*; do
        if test -d $d -a -f $d/__init__.py; then
            pypkgdir=$d

            for f in $(find ${pypkgdir} -name '*.py'); do
                echo "[Info] Check $f..."
                if test $check_with_pep8 = 1; then pep8 $f; fi
                if test $check_with_pylint = 1; then _pylint $f; fi
            done

            break
        fi
    done
    PYTHONPATH=$topdir nosetests -c $curdir/nose.cfg ${coverage_opts} --all-modules
    test $check_with_flake8 = 1 && flake8 ${topdir}
fi

# vim:sw=4:ts=4:et:
