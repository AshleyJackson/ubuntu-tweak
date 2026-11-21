# Ubuntu Tweak - Ubuntu Configuration Tool
#
# Copyright (C) 2007-2011 Tualatrix Chou <tualatrix@gmail.com>
#
# Ubuntu Tweak is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# Ubuntu Tweak is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ubuntu Tweak; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA

import os
import shutil
import logging
from gi.repository import Gtk, GLib

from ubuntutweak.gui import GuiBuilder
from ubuntutweak.modules import ModuleLoader, TweakModule
from ubuntutweak.janitor import JanitorPlugin
from ubuntutweak.settings import GSetting
from ubuntutweak.clips import Clip
from ubuntutweak.gui.dialogs import ErrorDialog, QuestionDialog
from ubuntutweak.utils.tar import TarFile
from ubuntutweak.common.consts import TEMP_ROOT
from ubuntutweak.updater import GitHubUpdater, UpdateError
from ubuntutweak.factory import WidgetFactory
from ubuntutweak.gui.containers import GridPack


log = logging.getLogger('PreferencesDialog')


class PreferencesDialog(GuiBuilder):
    (CLIP_CHECK,
     CLIP_ICON,
     CLIP_NAME) = range(3)

    (TWEAKS_CHECK,
     TWEAKS_ICON,
     TWEAKS_NAME) = range(3)

    (JANITOR_CHECK,
     JANITOR_NAME) = range(2)

    page_dict = {'overview': 0,
                 'tweaks': 1,
                 'admins': 2,
                 'janitor': 3}

    def __init__(self, parent):
        GuiBuilder.__init__(self, file_name='preferences.ui')

        self.preferences_dialog.set_transient_for(parent)
        self.clips_setting = GSetting('com.ubuntu-tweak.tweak.clips')
        self.tweaks_setting = GSetting('com.ubuntu-tweak.tweak.tweaks')
        self.admins_setting = GSetting('com.ubuntu-tweak.tweak.admins')
        self.janitor_setting = GSetting('com.ubuntu-tweak.janitor.plugins')
        self.clips_location_setting = GSetting('com.ubuntu-tweak.tweak.last-clip-location')
        
        auto_scan_label, auto_scan_switch = WidgetFactory.create("Switch",
                                                label=_("Auto scan:"),
                                                key='com.ubuntu-tweak.janitor.auto-scan',
                                                backend="gsettings")

        check_update_label, check_update_switch = WidgetFactory.create("Switch",
                                                label=_("Check for updates:"),
                                                key='com.ubuntu-tweak.tweak.check-updates',
                                                backend="gsettings")

        sentry_label, sentry_switch = WidgetFactory.create("Switch",
                                                label=_("Send crash reports (nightly/opt-in):"),
                                                key='com.ubuntu-tweak.tweak.sentry-enabled',
                                                backend="gsettings")

        self.channel_combo = Gtk.ComboBoxText()
        self.channel_combo.append('stable', _('Stable releases'))
        self.channel_combo.append('nightly', _('Nightly prereleases'))
        self.channel_setting = GSetting('com.ubuntu-tweak.tweak.update-channel')
        active_channel = self.channel_setting.get_value() or 'stable'
        if active_channel not in ('stable', 'nightly'):
            active_channel = 'stable'
        self.channel_combo.set_active_id(active_channel)
        self.channel_combo.connect('changed', self.on_channel_changed)

        self.sentry_dsn_entry = Gtk.Entry()
        self.sentry_dsn_entry.set_placeholder_text(_('Sentry DSN (required for crash reporting)'))
        self.sentry_dsn_setting = GSetting('com.ubuntu-tweak.tweak.sentry-dsn')
        self.sentry_dsn_entry.set_text(self.sentry_dsn_setting.get_value() or '')
        self.sentry_dsn_entry.connect('changed', self.on_sentry_dsn_changed)

        self.check_now_button = Gtk.Button(label=_('Check for updates now'))
        self.check_now_button.connect('clicked', self.on_check_updates_now_clicked)

        pack = GridPack((
            auto_scan_label, auto_scan_switch,
            Gtk.Label(label=_('Update channel:')), self.channel_combo,
            check_update_label, check_update_switch,
            sentry_label, sentry_switch,
            Gtk.Label(label=_('Sentry DSN:')), self.sentry_dsn_entry,
            Gtk.Label(label=''), self.check_now_button,
        ))
        self.generic_alignment.add(pack)

        self.generic_alignment.show_all()

    def on_clip_toggle_render_toggled(self, cell, path):
        log.debug("on_clip_toggle_render_toggled")
        self.on_toggle_renderer_toggled(self.clip_model,
                                        path,
                                        self.CLIP_CHECK,
                                        self.CLIP_NAME,
                                        self.clips_setting)

    def on_tweak_toggle_renderer_toggled(self, cell, path):
        log.debug("on_tweaks_toggle_render_toggled")
        self.on_toggle_renderer_toggled(self.tweaks_model,
                                        path,
                                        self.TWEAKS_CHECK,
                                        self.TWEAKS_NAME,
                                        self.tweaks_setting)

    def on_admins_toggle_renderer_toggled(self, cell, path):
        log.debug("on_admins_toggle_render_toggled")
        self.on_toggle_renderer_toggled(self.admins_model,
                                        path,
                                        self.TWEAKS_CHECK,
                                        self.TWEAKS_NAME,
                                        self.admins_setting)

    def on_janitor_cell_renderer_toggled(self, cell, path):
        log.debug("on_admins_toggle_render_toggled")
        self.on_toggle_renderer_toggled(self.janitor_model,
                                        path,
                                        self.JANITOR_CHECK,
                                        self.JANITOR_NAME,
                                        self.janitor_setting)

    def on_toggle_renderer_toggled(self, model, path, check_id, name_id, setting):
        iter = model.get_iter(path)
        checked = not model[iter][check_id]
        model[iter][check_id] = checked

        self._do_update_model(model, check_id, name_id, setting)

    def _do_update_model(self, model, check_id, name_id, setting):
        model_list = []
        for row in model:
            if row[check_id]:
                model_list.append(row[name_id])

        log.debug("on_clip_toggle_render_toggled: %s" % model_list)
        setting.set_value(model_list)

    def run(self, feature='overview'):
        self._update_clip_model()

        for _feature in ModuleLoader.default_features:
            self._update_feature_model(_feature)

        if feature in self.page_dict:
            self.preference_notebook.set_current_page(self.page_dict[feature])

        return self.preferences_dialog.run()

    def hide(self):
        return self.preferences_dialog.hide()

    def on_channel_changed(self, widget):
        channel = widget.get_active_id() or 'stable'
        self.channel_setting.set_value(channel)

    def on_sentry_dsn_changed(self, widget):
        self.sentry_dsn_setting.set_value(widget.get_text().strip())

    def on_check_updates_now_clicked(self, widget):
        channel = self.channel_combo.get_active_id() or 'stable'
        updater = GitHubUpdater(channel=channel)
        try:
            info = updater.check()
        except Exception, e:
            ErrorDialog(title=_('Update check failed'),
                        message=str(e)).launch()
            return

        if info.get('update_available'):
            msg = _('A {0} update is available: {1}\n\n{2}').format(
                channel, info.get('remote_version', ''), info.get('html_url', ''))
            dialog = QuestionDialog(title=_('Update available'), message=msg)
            response = dialog.run()
            dialog.destroy()
            if response == Gtk.ResponseType.YES:
                try:
                    # Conservative: download only; user installs/replaces bundle manually.
                    updater.download(info)
                    ErrorDialog(title=_('Update downloaded'),
                                message=_('Update asset downloaded and verified. Extract/install it manually for safety.')).launch()
                except Exception, e:
                    ErrorDialog(title=_('Update failed'),
                                message=str(e)).launch()
        else:
            ErrorDialog(title=_('No updates available'),
                        message=_('You are running the latest %s release.') % channel,
                        type=Gtk.MessageType.INFO).launch()

    def on_move_up_button_clicked(self, widget):
        model, iter = self.clip_view.get_selection().get_selected()

        if iter:
            previous_path = str(int(model.get_string_from_iter(iter)) - 1)

            if int(previous_path) >= 0:
                previous_iter = model.get_iter_from_string(previous_path)
                model.move_before(iter, previous_iter)
                self._do_update_model(self.clip_model,
                                      self.CLIP_CHECK,
                                      self.CLIP_NAME,
                                      self.clips_setting)

    def on_move_down_button_clicked(self, widget):
        model, iter = self.clip_view.get_selection().get_selected()

        if iter:
            next_iter = model.iter_next(iter)
            model.move_after(iter, next_iter)
            self._do_update_model(self.clip_model,
                                  self.CLIP_CHECK,
                                  self.CLIP_NAME,
                                  self.clips_setting)

    def on_clip_install_button_clicked(self, widget):
        self.on_install_extension(_('Choose a clip extension'),
                                  Clip,
                                  'clips',
                                  self.clips_setting,
                                  self._update_clip_model,
                                  _('"%s" is not a Clip Extension!'))

    def on_tweaks_install_button_clicked(self, widget):
        self.on_install_extension(_('Choose a Tweaks Extension'),
                                  TweakModule,
                                  'tweaks',
                                  self.tweaks_setting,
                                  self._update_feature_model,
                                  _('"%s" is not a Tweaks Extension!'))

    def on_admins_install_button_clicked(self, widget):
        self.on_install_extension(_('Choose a Admins Extension'),
                                  TweakModule,
                                  'admins',
                                  self.admins_setting,
                                  self._update_feature_model,
                                  _('"%s" is not a Admins Extension!'))

    def on_janitor_install_button_clicked(self, widget):
        self.on_install_extension(_('Choose a Janitor Extension'),
                                  JanitorPlugin,
                                  'janitor',
                                  self.janitor_setting,
                                  self._update_feature_model,
                                  _('"%s" is not a Janitor Extension!'))

    def on_install_extension(self, dialog_label, klass, feature,
                             setting, update_func, error_message):
        dialog = Gtk.FileChooserDialog(dialog_label,
                                       action=Gtk.FileChooserAction.OPEN,
                                       buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                                Gtk.STOCK_OPEN, Gtk.ResponseType.ACCEPT))
        filter = Gtk.FileFilter()
        filter.set_name(_('Ubuntu Tweak Extension (*.py, *.tar.gz)'))
        filter.add_pattern('*.py')
        filter.add_pattern('*.tar.gz')
        dialog.add_filter(filter)
        dialog.set_current_folder(self.clips_location_setting.get_value() or
                                  GLib.get_home_dir())

        filename = ''
        install_done = False
        not_extension = False

        if dialog.run() == Gtk.ResponseType.ACCEPT:
            filename = dialog.get_filename()
        dialog.destroy()

        if filename:
            self.clips_location_setting.set_value(os.path.dirname(filename))

            log.debug("Start to check the class in %s" % filename)
            if filename.endswith('.tar.gz'):
                tar_file = TarFile(filename)
                if tar_file.is_valid():
                    tar_file.extract(TEMP_ROOT)
                    #TODO if multi-root
                    if tar_file.get_root_name():
                        temp_dir = os.path.join(TEMP_ROOT, tar_file.get_root_name())

                if ModuleLoader.is_target_class(temp_dir, klass):
                    target = os.path.join(ModuleLoader.get_user_extension_dir(feature), os.path.basename(temp_dir))
                    copy = True
                    if os.path.exists(target):
                        dialog = QuestionDialog(message=_("Would you like to remove it then install again?"),
                                                title=_('"%s" has already installed' % os.path.basename(target)))
                        response = dialog.run()
                        dialog.destroy()

                        if response == Gtk.ResponseType.YES:
                            shutil.rmtree(target)
                        else:
                            copy = False

                    if copy:
                        log.debug("Now copying tree...")
                        shutil.move(temp_dir, target)
                    else:
                        shutil.rmtree(temp_dir)
                else:
                    not_extension = True
            else:
                if ModuleLoader.is_target_class(filename, klass):
                    shutil.copy(filename, ModuleLoader.get_user_extension_dir(feature))
                    install_done = True
                else:
                    not_extension = True

        if install_done:
            update_func(feature)

            # To force empty the clips_setting to make load_cips
            value = setting.get_value()
            setting.set_value([''])
            setting.set_value(value)

        if not_extension:
            ErrorDialog(message=error_message % os.path.basename(filename)).launch()

    def _update_clip_model(self, feature=None):
        clips = self.clips_setting.get_value()

        loader = ModuleLoader('clips')

        self.clip_model.clear()

        for clip_name in clips:
            ClipClass = loader.get_module(clip_name)

            self.clip_model.append((True,
                                    ClipClass.get_pixbuf(),
                                    ClipClass.get_name()))

        for name, ClipClass in loader.module_table.items():
            if name not in clips:
                self.clip_model.append((False,
                                        ClipClass.get_pixbuf(),
                                        ClipClass.get_name()))

    def _update_feature_model(self, feature):
        module_list = getattr(self, '%s_setting' % feature).get_value() or []

        loader = ModuleLoader(feature, user_only=True)

        model = getattr(self, '%s_model' % feature)
        model.clear()

        for name, klass in loader.module_table.items():
            if klass.get_pixbuf():
                model.append((name in module_list,
                              klass.get_pixbuf(),
                              klass.get_name()))
            else:
                model.append((name in module_list,
                              klass.get_name()))
