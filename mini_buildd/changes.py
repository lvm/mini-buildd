# -*- coding: utf-8 -*-
import os
import stat
import logging
import tarfile
import ftplib
import re
import contextlib

import debian.deb822

import mini_buildd.setup
import mini_buildd.misc
import mini_buildd.gnupg

import mini_buildd.models.repository
import mini_buildd.models.gnupg

LOG = logging.getLogger(__name__)


class Changes(debian.deb822.Changes):
    BUILDREQUEST_RE = re.compile("^.+_mini-buildd-buildrequest_[^_]+.changes$")
    BUILDRESULT_RE = re.compile("^.+_mini-buildd-buildresult_[^_]+.changes$")

    def __init__(self, file_path):
        self._file_path = file_path
        self._file_name = os.path.basename(file_path)
        self._new = not os.path.exists(file_path)
        self._sha1 = None if self._new else mini_buildd.misc.sha1_of_file(file_path)
        super(Changes, self).__init__([] if self._new else file(file_path))
        # Be sure base dir is always available
        mini_buildd.misc.mkdirs(os.path.dirname(file_path))

    def __unicode__(self):
        if self.is_buildrequest():
            return u"Buildrequest from '{h}': {i}".format(h=self.get_or_empty("Upload-Result-To"), i=self.get_pkg_id(with_arch=True))
        elif self.is_buildresult():
            return u"Buildresult from '{h}': {i}".format(h=self.get_or_empty("Built-By"), i=self.get_pkg_id(with_arch=True))
        else:
            return u"User upload: {i}".format(i=self.get_pkg_id())

    @property
    def file_name(self):
        return self._file_name

    @property
    def file_path(self):
        return self._file_path

    def is_new(self):
        return self._new

    def is_buildrequest(self):
        return self.BUILDREQUEST_RE.match(self._file_name)

    def is_buildresult(self):
        return self.BUILDRESULT_RE.match(self._file_name)

    def get_or_empty(self, key):
        try:
            return self[key]
        except:
            return ""

    def get_repository(self):
        # Check and parse changes distribution string
        distribution_codename, repository_identity, suite_name = mini_buildd.misc.parse_distribution(self["Distribution"])

        # Get repository for identity; django exceptions will suite quite well as-is
        repository = mini_buildd.models.repository.Repository.objects.get(identity=repository_identity)
        distribution = repository.distributions.all().get(base_source__codename__exact=distribution_codename)
        suite = repository.layout.suites.all().get(name=suite_name)

        if not suite.uploadable:
            raise Exception("Suite '{s}' is not uploadable".format(s=suite))

        if not repository.mbd_is_active():
            raise Exception("Repository '{r}' is not active".format(r=repository))

        repository.mbd_check_version(self["Version"], distribution, suite)

        return repository, distribution, suite

    def get_spool_dir(self):
        return os.path.join(mini_buildd.setup.SPOOL_DIR, self._sha1)

    def get_log_dir(self):
        return os.path.join(mini_buildd.setup.LOG_DIR, self["Distribution"], self["Source"], self["Version"], self["Architecture"])

    def get_pkg_id(self, with_arch=False, arch_separator=":"):
        pkg_id = u"{s}_{v}".format(s=self["Source"], v=self["Version"])
        if with_arch:
            pkg_id += u"{s}{a}".format(s=arch_separator, a=self["Architecture"])
        return pkg_id

    def get_files(self):
        return self["Files"] if "Files" in self else []

    def add_file(self, file_name):
        if not "Files" in self:
            self["Files"] = []
        self["Files"].append({"md5sum": mini_buildd.misc.md5_of_file(file_name),
                              "size": os.path.getsize(file_name),
                              "section": "mini-buildd",
                              "priority": "extra",
                              "name": os.path.basename(file_name)})

    def save(self, gnupg):
        try:
            LOG.info("Saving changes: {f}".format(f=self._file_path))
            self.dump(fd=open(self._file_path, "w+"))
            LOG.info("Signing changes: {f}".format(f=self._file_path))
            gnupg.sign(self._file_path)
        except:
            # Existence of the file name is used as flag
            if os.path.exists(self._file_path):
                os.remove(self._file_path)
            raise

    def upload(self, hopo):
        upload = os.path.splitext(self._file_path)[0] + ".upload"
        if os.path.exists(upload):
            LOG.info("FTP: '{f}' already uploaded to '{h}'...".format(f=self._file_name, h=open(upload).read()))
        else:
            ftp = ftplib.FTP()
            ftp.connect(hopo.host, hopo.port)
            ftp.login()
            ftp.cwd("/incoming")
            for fd in self.get_files() + [{"name": self._file_name}]:
                f = fd["name"]
                LOG.debug("FTP: Uploading file: '{f}'".format(f=f))
                ftp.storbinary("STOR {f}".format(f=f), open(os.path.join(os.path.dirname(self._file_path), f)))
            open(upload, "w").write("{h}:{p}".format(h=hopo.host, p=hopo.port))
            LOG.info("FTP: '{f}' uploaded to '{h}'...".format(f=self._file_name, h=hopo.host))

    def upload_buildrequest(self, local_hopo):
        arch = self["Architecture"]
        codename = self["Base-Distribution"]

        remotes = {}

        def check_remote(remote):
            try:
                remote.mbd_check(request=None)
                state = remote.mbd_get_builder_state()
                if state.is_up() and state.has_chroot(arch, codename):
                    remotes[state.get_load()] = state.get_hopo()
            except Exception as e:
                LOG.warn("Builder check failed: {e}".format(e=str(e)))

        # Always checkout our own instance as pseudo remote
        check_remote(mini_buildd.models.gnupg.Remote(http=local_hopo.string))

        # Checkout all active remotes
        for r in mini_buildd.models.gnupg.Remote.mbd_get_active():
            check_remote(r)

        if not remotes:
            raise Exception("No builder found for {a}/{c}".format(a=arch, c=codename))

        for load, hopo in sorted(remotes.items()):
            try:
                self.upload(hopo)
                return load, hopo
            except Exception as e:
                LOG.warn("Uploading to '{h}' failed: ".format(h=hopo.string), e=str(e))

        raise Exception("Buildrequest upload failed for {a}/{c}".format(a=arch, c=codename))

    def tar(self, tar_path, add_files=None):
        with contextlib.closing(tarfile.open(tar_path, "w")) as tar:
            tar_add = lambda f: tar.add(f, arcname=os.path.basename(f))
            tar_add(self._file_path)
            for f in self.get_files():
                tar_add(os.path.join(os.path.dirname(self._file_path), f["name"]))
            if add_files:
                for f in add_files:
                    tar_add(f)

    def untar(self, path):
        tar_file = self._file_path + ".tar"
        if os.path.exists(tar_file):
            with contextlib.closing(tarfile.open(tar_file, "r")) as tar:
                tar.extractall(path=path)
        else:
            LOG.info("No tar file (skipping): {f}".format(f=tar_file))

    def archive(self):
        logdir = self.get_log_dir()
        if not os.path.exists(logdir):
            os.makedirs(logdir)
        LOG.info("Moving changes to log: '{f}'->'{l}'".format(f=self._file_path, l=logdir))
        for fd in [{"name": self._file_name}] + self.get_files():
            f = os.path.join(os.path.dirname(self._file_path), fd["name"])
            LOG.debug("Moving: '{f}' to '{d}'". format(f=fd["name"], d=logdir))
            os.rename(f, os.path.join(logdir, fd["name"]))

    def remove(self):
        LOG.info("Removing changes: '{f}'".format(f=self._file_path))
        for fd in [{"name": self._file_name}] + self.get_files():
            f = os.path.join(os.path.dirname(self._file_path), fd["name"])
            LOG.debug("Removing: '{f}'".format(f=fd["name"]))
            os.remove(f)

    def gen_buildrequests(self, daemon, repository, dist):
        # Build buildrequest files for all architectures
        breq_dict = {}
        for a in dist.mbd_get_all_architectures():
            path = os.path.join(self.get_spool_dir(), a)

            breq = Changes(os.path.join(path, "{b}_mini-buildd-buildrequest_{a}.changes".format(b=self.get_pkg_id(), a=a)))
            if breq.is_new():
                for v in ["Distribution", "Source", "Version"]:
                    breq[v] = self[v]

                # Generate sources.list et.al. to be used
                open(os.path.join(path, "apt_sources.list"), 'w').write(repository.mbd_get_apt_sources_list(self["Distribution"]))
                open(os.path.join(path, "apt_preferences"), 'w').write(repository.mbd_get_apt_preferences())
                open(os.path.join(path, "apt_keys"), 'w').write(repository.mbd_get_apt_keys(self["Distribution"]))
                chroot_setup_script = os.path.join(path, "chroot_setup_script")
                open(chroot_setup_script, 'w').write(repository.mbd_get_chroot_setup_script(self["Distribution"]))
                os.chmod(chroot_setup_script, stat.S_IRWXU)
                open(os.path.join(path, "sbuildrc_snippet"), 'w').write(repository.mbd_get_sbuildrc_snippet(self["Distribution"], a))

                # Generate tar from original changes
                self.tar(tar_path=breq.file_path + ".tar",
                         add_files=[os.path.join(path, "apt_sources.list"),
                                    os.path.join(path, "apt_preferences"),
                                    os.path.join(path, "apt_keys"),
                                    chroot_setup_script,
                                    os.path.join(path, "sbuildrc_snippet")])
                breq.add_file(breq.file_path + ".tar")

                breq["Upload-Result-To"] = daemon.mbd_get_ftp_hopo().string
                breq["Base-Distribution"] = dist.base_source.codename
                breq["Architecture"] = a
                if a == dist.architecture_all.name:
                    breq["Arch-All"] = "Yes"
                breq["Build-Dep-Resolver"] = dist.get_build_dep_resolver_display()
                breq["Apt-Allow-Unauthenticated"] = "1" if dist.apt_allow_unauthenticated else "0"
                if dist.lintian_mode != dist.LINTIAN_DISABLED:
                    # Generate lintian options
                    modeargs = {
                        dist.LINTIAN_DISABLED: "",
                        dist.LINTIAN_RUN_ONLY: "",
                        dist.LINTIAN_FAIL_ON_ERROR: "",
                        dist.LINTIAN_FAIL_ON_WARNING: "--fail-on-warning"}
                    breq["Run-Lintian"] = modeargs[dist.lintian_mode] + u" " + dist.lintian_extra_options

                breq.save(daemon.mbd_gnupg)
            else:
                LOG.info("Re-using existing buildrequest: {b}".format(b=breq.file_name))
            breq_dict[a] = breq

        return breq_dict

    def gen_buildresult(self):
        assert(self.is_buildrequest())
        bres = mini_buildd.changes.Changes(os.path.join(self.get_spool_dir(),
                                                        u"{b}.changes".
                                                        format(b=self.get_pkg_id(with_arch=True, arch_separator="_mini-buildd-buildresult_"))))

        for v in ["Distribution", "Source", "Version", "Architecture"]:
            bres[v] = self[v]

        return bres
