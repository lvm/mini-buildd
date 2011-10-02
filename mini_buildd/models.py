# -*- coding: utf-8 -*-
import socket
import StringIO
import os

import GnuPGInterface

import django.db
import django.core.exceptions
import django.contrib

import mini_buildd

class Mirror(django.db.models.Model):
    url = django.db.models.URLField(primary_key=True, max_length=512,
                          default="http://ftp.debian.org/debian",
                          help_text="The URL of an apt mirror/repository")

    class Meta:
        ordering = ['url']

    def __unicode__(self):
        return self.url

    class Admin(django.contrib.admin.ModelAdmin):
        search_fields = ['url']


class Source(django.db.models.Model):
    origin = django.db.models.CharField(max_length=100, default="Debian")
    codename = django.db.models.CharField(max_length=100, default="sid")
    mirrors = django.db.models.ManyToManyField('Mirror')

    class Meta:
        unique_together = ('origin', 'codename')
        ordering = ['origin', 'codename']

    def get_apt_lines(self, kind="deb", components="main contrib non-free"):
        apt_lines = []
        for m in self.mirrors.all():
            apt_lines.append(kind + ' ' if kind else '' + m.url + ' ' + self.codename + ' ' + components)
        mini_buildd.log.debug(str(apt_lines))
        return apt_lines

    def get_apt_pin(self):
        return "release n=" + self.codename + ", o=" + self.origin

    def __unicode__(self):
        return self.origin + ": " + self.codename + " [" + self.get_apt_pin() + "]"

    class Admin(django.contrib.admin.ModelAdmin):
        search_fields = ['origin', 'codename']


class PrioritisedSource(django.db.models.Model):
    source = django.db.models.ForeignKey(Source)
    prio = django.db.models.IntegerField(default=1,
                               help_text="A apt pin priority value (see 'man apt_preferences')."
                               "Examples: 1=not automatic, 1001=downgrade'")

    class Meta:
        unique_together = ('source', 'prio')

    def __unicode__(self):
        return self.source.__unicode__() + ": Prio=" + str(self.prio)


class Architecture(django.db.models.Model):
    arch = django.db.models.CharField(primary_key=True, max_length=50,
                            help_text="A valid Debian architecture (the output of 'dpkg --print-architecture' on the architecture)."
                            "Examples: 'i386', 'amd64', 'powerpc'")

    def __unicode__(self):
        return self.arch


class Builder(django.db.models.Model):
    host = django.db.models.CharField(max_length=99, default=socket.getfqdn())
    arch = django.db.models.ForeignKey(Architecture)
    parallel = django.db.models.IntegerField(default=1,
                                   help_text="Degree of parallelism this builder supports.")

    def __unicode__(self):
        return self.host + " building " + self.arch.arch

    class Meta:
        unique_together = ('host', 'arch')


class Suite(django.db.models.Model):
    name = django.db.models.CharField(primary_key=True, max_length=49,
                            help_text="A suite to support, usually s.th. like 'unstable','testing' or 'stable'.")
    migrates_from = django.db.models.ForeignKey('self', blank=True, null=True,
                                      help_text="Leave this blank to make this suite uploadable, or chose a suite where this migrates from.")
    not_automatic = django.db.models.BooleanField(default=True)
    but_automatic_upgrades = django.db.models.BooleanField(default=False)

    def __unicode__(self):
        return self.name + " (" + ("<= " + self.migrates_from.name if self.migrates_from else "uploadable") + ")"


class Layout(django.db.models.Model):
    name = django.db.models.CharField(primary_key=True, max_length=128,
                            help_text="Name for the layout.")
    suites = django.db.models.ManyToManyField(Suite)

    def __unicode__(self):
        return self.name


class Distribution(django.db.models.Model):
    # @todo: limit to distribution?  limit_choices_to={'codename': 'sid'})
    base_source = django.db.models.ForeignKey(Source, primary_key=True)
    # @todo: how to limit to source.kind?
    extra_sources = django.db.models.ManyToManyField(PrioritisedSource, blank=True, null=True)

    def __unicode__(self):
        # @todo: somehow indicate extra sources to visible name
        return self.base_source.origin + ": " + self.base_source.codename

class Repository(django.db.models.Model):
    id = django.db.models.CharField(primary_key=True, max_length=50, default=socket.gethostname())
    host = django.db.models.CharField(max_length=100, default=socket.getfqdn())

    layout = django.db.models.ForeignKey(Layout)
    dists = django.db.models.ManyToManyField(Distribution)
    archs = django.db.models.ManyToManyField(Architecture)

    gnupg_template = django.db.models.TextField(default="""
Key-Type: DSA
Key-Length: 1024
Subkey-Type: ELG-E
Subkey-Length: 1024
Expire-Date: 0""")

    apt_allow_unauthenticated = django.db.models.BooleanField(default=False)
    mail = django.db.models.EmailField(blank=True)
    extdocurl = django.db.models.URLField(blank=True)

    def __init__(self, *args, **kwargs):
        super(Repository, self).__init__(*args, **kwargs)

        # Internal convenience variables
        self.incoming_path = os.path.join(mini_buildd.opts.home, "rep", self.id, "incoming")

        self.uploadable_dists = []
        for d in self.dists.all():
            for s in self.layout.suites.all():
                if s.migrates_from == None:
                    self.uploadable_dists.append("{d}-{id}-{s}".format(
                            id=self.id,
                            d=d.base_source.codename,
                            s=s.name))

    def __unicode__(self):
        return self.id

    def get_dist(self, dist="", suite=""):
        return dist + "-" + self.id + "-" + suite

    def get_apt_line(self, kind="deb", dist="", suite="", components="main contrib non-free"):
        return kind + (' ' if kind else '') + "http://" + self.host + ":8066/mini_buildd/public_html/rep/" + self.id + " " + self.get_dist(dist=dist, suite=suite) + " " + components

    def repreproConfig(self):
        archs = []
        for a in self.archs.all():
            archs.append(a.arch)

        result = StringIO.StringIO()
        for d in self.dists.all():
            for s in self.layout.suites.all():
                result.write("""
Codename: {d}-{id}-{s}
Suite: {d}-{id}-{s}
Label: {d}-{id}-{s}
Origin: mini-buildd-{id}
Components: main contrib non-free
Architectures: source {archs}
Description: {s} {d} packages for {id}
SignWith: default
NotAutomatic: {na}
ButAutomaticUpgrades: {bau}
""".format(dist=self.get_dist(dist=d.base_source.codename, suite=s.name),
           id=self.id,
           d=d.base_source.codename,
           s=s.name,
           archs=" ".join(archs),
           na="yes" if s.not_automatic else "no",
           bau="yes" if s.but_automatic_upgrades else "no"))

        return result.getvalue()

    def updateGpgKey(self):
        gnupg = GnuPGInterface.GnuPG()
        gnupg.options.meta_interactive = 0
        gnupg.options.homedir = os.path.join(mini_buildd.opts.home, "rep", self.id, ".gnupg")

        if os.path.exists(gnupg.options.homedir):
            mini_buildd.log.info("GPG home {h} exists, skipping key generation...".format(h=gnupg.options.homedir))
        else:
            proc = gnupg.run(["--gen-key"],
                          create_fhs=['stdin', 'stdout', 'stderr'])

            proc.handles['stdin'].write('''{tpl}
Name-Real: mini-buildd-{id} on {h}
Name-Email: mini-buildd-{id}@{h}
'''.format(tpl=self.gnupg_template, id=self.id, h=self.host))

            mini_buildd.log.debug("Generating gnupg key...")
            proc.handles['stdin'].close()
            report = proc.handles['stderr'].read()
            proc.handles['stderr'].close()
            try:
                proc.wait()
            except:
                mini_buildd.log.error(report)
                raise

    def clean(self):
        self.updateGpgKey()
