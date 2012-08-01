# -*- coding: utf-8 -*-
"""
Run reprepro commands.
"""
import logging

import mini_buildd.misc

LOG = logging.getLogger(__name__)


class Reprepro():
    def __init__(self, basedir, morguedir=False):
        self._cmd = ["reprepro", "--verbose", "--basedir={b}".format(b=basedir)]
        if morguedir:
            self._cmd.append("--morguedir=+b/morguedir")

    def clearvanished(self):
        mini_buildd.misc.call(self._cmd + ["clearvanished"])

    def export(self):
        mini_buildd.misc.call(self._cmd + ["export"])

    def include(self, distribution, changes):
        return mini_buildd.misc.call(self._cmd + ["include", distribution, changes])

    def copysrc(self, dest_distribution, source_distribution, package, version):
        return mini_buildd.misc.call(
            self._cmd + ["copysrc", dest_distribution, source_distribution, package, version])

    def removesrc(self, distribution, package, version):
        return mini_buildd.misc.call(
            self._cmd + ["removesrc", distribution, package, version])

    def listmatched(self, pattern, dist):
        return mini_buildd.misc.call(
            self._cmd +
            ["--type=dsc",
             "--list-format=${$source}|${$sourceversion}|${$codename};",
             "listmatched",
             dist,
             pattern])
