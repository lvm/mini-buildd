# -*- coding: utf-8 -*-
import urllib
import StringIO

import django.db.models
import django.contrib.admin
import django.contrib.messages

import mini_buildd.misc
import mini_buildd.gnupg

import mini_buildd.models.base


class GnuPGPublicKey(mini_buildd.models.base.StatusModel):
    key_id = django.db.models.CharField(max_length=100, blank=True, default="",
                                        help_text="Give a key id here to retrieve the actual key automatically per configured keyserver.")
    key = django.db.models.TextField(blank=True, default="",
                                     help_text="ASCII-armored Gnu PG public key. Leave the key id blank if you fill this manually.")

    key_long_id = django.db.models.CharField(max_length=254, blank=True, default="")
    key_created = django.db.models.CharField(max_length=254, blank=True, default="")
    key_expires = django.db.models.CharField(max_length=254, blank=True, default="")
    key_name = django.db.models.CharField(max_length=254, blank=True, default="")
    key_fingerprint = django.db.models.CharField(max_length=254, blank=True, default="")

    class Meta(mini_buildd.models.base.StatusModel.Meta):
        abstract = True
        app_label = "mini_buildd"

    class Admin(mini_buildd.models.base.StatusModel.Admin):
        search_fields = ["key_id", "key_long_id", "key_name", "key_fingerprint"]
        readonly_fields = ["key_long_id", "key_created", "key_expires", "key_name", "key_fingerprint"]

    def __unicode__(self):
        return u"{i}: {n} ({s})".format(i=self.key_long_id, n=self.key_name, s=self.mbd_get_status_display())

    def mbd_prepare(self, _request):
        gpg = mini_buildd.gnupg.TmpGnuPG()
        if self.key_id:
            # Receive key from keyserver
            gpg.recv_key(self.mbd_get_daemon().model.gnupg_keyserver, self.key_id)
            self.key = gpg.get_pub_key(self.key_id)
        elif self.key:
            gpg.add_pub_key(self.key)

        for key in gpg.pub_keys_info():
            if key[0] == "pub":
                self.key_long_id = key[4]
                self.key_created = key[5]
                self.key_expires = key[6]
                self.key_name = key[9]
            if key[0] == "fpr":
                self.key_fingerprint = key[9]

    def mbd_unprepare(self, _request):
        self.key_long_id = ""
        self.key_created = ""
        self.key_expires = ""
        self.key_name = ""
        self.key_fingerprint = ""
        if self.key_id:
            self.key = ""

    def mbd_check(self, request):
        pass


class AptKey(GnuPGPublicKey):
    pass


class Uploader(GnuPGPublicKey):
    user = django.db.models.OneToOneField(django.contrib.auth.models.User)
    may_upload_to = django.db.models.ManyToManyField("Repository")

    class Admin(GnuPGPublicKey.Admin):
        search_fields = GnuPGPublicKey.Admin.search_fields + ["user"]
        readonly_fields = GnuPGPublicKey.Admin.readonly_fields + ["user"]

    def __unicode__(self):
        return "User '{u}': {s}".format(u=self.user, s=super(Uploader, self).__unicode__())


def cb_create_user_profile(sender, instance, created, **kwargs):
    "Automatically create a user profile with every user that is created"
    if created:
        Uploader.objects.create(user=instance)
django.db.models.signals.post_save.connect(cb_create_user_profile, sender=django.contrib.auth.models.User)


class Remote(GnuPGPublicKey):
    http = django.db.models.CharField(primary_key=True, max_length=255, default="")
    pickled_state = django.db.models.TextField(blank=True, editable=False)

    wake_command = django.db.models.CharField(max_length=255, default="", blank=True, help_text="For future use.")

    class Admin(GnuPGPublicKey.Admin):
        search_fields = GnuPGPublicKey.Admin.search_fields + ["http"]
        readonly_fields = GnuPGPublicKey.Admin.readonly_fields + ["key", "key_id", "pickled_state"]

    def __unicode__(self):
        return "{S} ({s})".format(S=self.mbd_get_builder_state(), s=self.mbd_get_status_display())

    def mbd_get_builder_state(self):
        if self.pickled_state:
            return mini_buildd.misc.BuilderState(pickled_state=StringIO.StringIO(self.pickled_state.encode("UTF-8")))
        else:
            return mini_buildd.misc.BuilderState(state=[False, self.http, 0, {}])

    def mbd_prepare(self, request):
        url = "http://{h}/mini_buildd/download/archive.key".format(h=self.http)
        self.mbd_msg_info(request, "Downloading '{u}'...".format(u=url))
        self.key = urllib.urlopen(url).read()
        if self.key:
            self.mbd_msg_warn(request, "Downloaded remote key integrated: Please check key manually before activation!")
        else:
            raise Exception("Empty remote key from '{u}' -- maybe the remote is not prepared yet?".format(u=url))
        super(Remote, self).mbd_prepare(request)

        self.mbd_check(request)

    def mbd_unprepare(self, request):
        self.key = ""
        self.pickled_state = ""
        self.mbd_msg_info(request, "Remote key and state removed.")

    def mbd_check(self, request):
        url = "http://{h}/mini_buildd/download/builder_state".format(h=self.http)
        self.pickled_state = urllib.urlopen(url).read()
        state = self.mbd_get_builder_state()
        if not state.is_up():
            raise Exception("Remote builder down")
