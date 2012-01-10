# -*- coding: utf-8 -*-
# PiTiVi , Non-linear video editor
#
#       pitivi/render.py
#
# Copyright (c) 2009, Edward Hervey <bilboed@bilboed.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this program; if not, write to the
# Free Software Foundation, Inc., 51 Franklin St, Fifth Floor,
# Boston, MA 02110-1301, USA.

"""
Rendering-related utilities and classes
"""

import os
import gtk
import gst
import ges
import time
import gst

import pitivi.utils.loggable as log

from gettext import gettext as _

from pitivi import configure
from pitivi.utils.playback import togglePlayback, Seeker
from pitivi.utils.signal import Signallable

from pitivi.utils.loggable import Loggable
from pitivi.utils.widgets import GstElementSettingsDialog
from pitivi.utils.ripple_update_group import RippleUpdateGroup
from pitivi.utils.ui import model, frame_rates, audio_rates, audio_depths, \
    audio_channels, get_combo_value, set_combo_value, beautify_ETA


#---------------- Private utils ---------------------------------------#
def get_compatible_sink_pad(factoryname, caps):
    """
    Returns the pad name of a (request) pad from factoryname which is
    compatible with the given caps.
    """
    factory = gst.registry_get_default().lookup_feature(factoryname)
    if factory == None:
        log.warning("render", "%s is not a valid factoryname", factoryname)
        return None

    res = []
    sinkpads = [x for x in factory.get_static_pad_templates() if x.direction == gst.PAD_SINK]
    for p in sinkpads:
        c = p.get_caps()
        log.log("render", "sinkcaps %s", c.to_string())
        inter = caps.intersect(c)
        log.log("render", "intersection %s", inter.to_string())
        if inter:
            res.append(p.name_template)
    if len(res) > 0:
        return res[0]
    return None


#FIME GES port make it obselete, handle it properly again
def get_compatible_sink_caps(factoryname, caps):
    """
    Returns the compatible caps between 'caps' and the sink pad caps of 'factoryname'
    """
    log.log("render", "factoryname : %s , caps : %s", factoryname, caps.to_string())
    factory = gst.registry_get_default().lookup_feature(factoryname)
    if factory == None:
        log.warning("render", "%s is not a valid factoryname", factoryname)
        return None

    res = []
    sinkcaps = [x.get_caps() for x in factory.get_static_pad_templates() if x.direction == gst.PAD_SINK]
    for c in sinkcaps:
        log.log("render", "sinkcaps %s", c.to_string())
        inter = caps.intersect(c)
        log.log("render", "intersection %s", inter.to_string())
        if inter:
            res.append(inter)

    if len(res) > 0:
        return res[0]
    return None


def list_compat(a1, b1):
    for x1 in a1:
        if not x1 in b1:
            return False
    return True


def my_can_sink_caps(muxer, ocaps, muxsinkcaps=[]):
    """ returns True if the given caps intersect with some of the muxer's
    sink pad templates' caps.
    """
    # fast version
    if muxsinkcaps != []:
        for c in muxsinkcaps:
            if not c.intersect(ocaps).is_empty():
                return True
        return False
    # slower default
    for x in muxer.get_static_pad_templates():
        if x.direction == gst.PAD_SINK:
            if not x.get_caps().intersect(ocaps).is_empty():
                return True
    return False

    # sinkcaps = (x.get_caps() for x in muxer.get_static_pad_templates() if x.direction == gst.PAD_SINK)
    # for x in sinkcaps:
    #     if not x.intersect(ocaps).is_empty():
    #         return True
    # return False


class CachedEncoderList(object):
    def __init__(self):
        self._factories = None
        self._registry = gst.registry_get_default()
        self._registry.connect("feature-added", self._registryFeatureAddedCb)

    def _ensure_factories(self):
        if self._factories is None:
            self._buildFactories()

    def _buildFactories(self):
        self._factories = self._registry.get_feature_list(gst.ElementFactory)
        self._audioEncoders = []
        self._videoEncoders = []
        self._muxers = []
        for fact in self._factories:
            klist = fact.get_klass().split('/')
            if list_compat(("Codec", "Muxer"), klist):
                self._muxers.append(fact)
            elif list_compat(("Codec", "Encoder", "Video"), klist) or list_compat(("Codec", "Encoder", "Image"), klist):
                self._videoEncoders.append(fact)
            elif list_compat(("Codec", "Encoder", "Audio"), klist):
                self._audioEncoders.append(fact)

    def available_muxers(self):
        if self._factories is None:
            self._buildFactories()
        return self._muxers

    def available_audio_renderers(self):
        if self._factories is None:
            self._buildFactories()
        return self._audioEncoders

    def available_video_renderers(self):
        if self._factories is None:
            self._buildFactories()
        return self._videoEncoders

    def _registryFeatureAddedCb(self, registry, feature):
        self._factories = None

_cached_renderer_list = None


def rendererlist():
    global _cached_renderer_list
    if _cached_renderer_list is None:
        _cached_renderer_list = CachedEncoderList()
    return _cached_renderer_list


def available_muxers():
    """ return all available muxers """
    enclist = rendererlist()
    return enclist.available_muxers()


def available_video_renderers():
    """ returns all available video renderers """
    enclist = rendererlist()
    return enclist.available_video_renderers()


def available_audio_renderers():
    """ returns all available audio renderers """
    enclist = rendererlist()
    return enclist.available_audio_renderers()


def renderers_muxer_compatible(renderers, muxer, muxsinkcaps=[]):
    """ returns the list of renderers compatible with the given muxer """
    res = []
    if muxsinkcaps == []:
        muxsinkcaps = [x.get_caps() for x in muxer.get_static_pad_templates() if x.direction == gst.PAD_SINK]
    for renderer in renderers:
        for tpl in renderer.get_static_pad_templates():
            if tpl.direction == gst.PAD_SRC:
                if my_can_sink_caps(muxer, tpl.get_caps(), muxsinkcaps):
                    res.append(renderer)
                    break
    return res


raw_audio_caps = gst.Caps("audio/x-raw-float;audio/x-raw-int")
raw_video_caps = gst.Caps("video/x-raw-yuv;video/x-raw-rgb")


def muxer_can_sink_raw_audio(muxer):
    """ Returns True if given muxer can accept raw audio """
    return my_can_sink_caps(muxer, raw_audio_caps)


def muxer_can_sink_raw_video(muxer):
    """ Returns True if given muxer can accept raw video """
    return my_can_sink_caps(muxer, raw_video_caps)


def available_combinations():
    """Return a 3-tuple of (muxers, audio, video), where:
        - muxers is a list of muxer factories
        - audio is a dictionary from muxer names to compatible audio renderers
        - video is a dictionary from muxer names to compatible video renderers
    """

    arenderers = available_audio_renderers()
    vrenderers = available_video_renderers()
    muxers = available_muxers()

    audio = {}
    video = {}
    containers = []
    for muxer in muxers:
        mux = muxer.get_name()
        aencs = renderers_muxer_compatible(arenderers, muxer)
        vencs = renderers_muxer_compatible(vrenderers, muxer)
        # only include muxers with audio and video

        if aencs and vencs:
            audio[mux] = aencs
            video[mux] = vencs
            containers.append(muxer)

    return containers, audio, video


def beautify_factoryname(factory):
    """Returns a nice name for the specified gst.ElementFactory instance."""
    # only replace lowercase versions of "format", "video", "audio"
    # otherwise they might be part of a trademark name
    words_to_remove = ["Muxer", "muxer", "Encoder", "encoder",
            "format", "video", "audio", "instead"]
    name = factory.get_longname()
    for word in words_to_remove:
        name = name.replace(word, "")
    return " ".join(word for word in name.split())


def extension_for_muxer(muxer):
    """Returns the file extension appropriate for the specified muxer."""
    exts = {
        "asfmux": "asf",
        "avimux": "avi",
        "ffmux_3g2": "3g2",
        "ffmux_avm2": "avm2",
        "ffmux_dvd": "vob",
        "ffmux_flv": "flv",
        "ffmux_ipod": "mp4",
        "ffmux_mpeg": "mpeg",
        "ffmux_mpegts": "mpeg",
        "ffmux_psp": "mp4",
        "ffmux_rm": "rm",
        "ffmux_svcd": "mpeg",
        "ffmux_swf": "swf",
        "ffmux_vcd": "mpeg",
        "ffmux_vob": "vob",
        "flvmux": "flv",
        "gppmux": "3gp",
        "matroskamux": "mkv",
        "mj2mux": "mj2",
        "mp4mux": "mp4",
        "mpegpsmux": "mpeg",
        "mpegtsmux": "mpeg",
        "mvemux": "mve",
        "mxfmux": "mxf",
        "oggmux": "ogv",
        "qtmux": "mov",
        "webmmux": "webm"}
    return exts.get(muxer)


def factorylist(factories):
    """Create a gtk.ListStore() of sorted, beautified factory names.

    @param factories: The factories available for creating the list.
    @type factories: A sequence of gst.ElementFactory instances.
    """
    columns = (str, object)
    data = [(beautify_factoryname(factory), factory)
            for factory in factories
            if factory.get_rank() > 0]
    data.sort(key=lambda x: x[0])
    return model(columns, data)


#--------------------------------- Public classes -----------------------------#
class RenderingProgressDialog(Signallable):
    __signals__ = {
        "pause": [],
        "cancel": [],
    }

    def __init__(self, app, parent):
        self.app = app
        self.system = app.app.system
        self.builder = gtk.Builder()
        self.builder.add_from_file(os.path.join(configure.get_ui_dir(),
            "renderingprogress.ui"))
        self.builder.connect_signals(self)

        self.window = self.builder.get_object("render-progress")
        self.table1 = self.builder.get_object("table1")
        self.progressbar = self.builder.get_object("progressbar")
        self.play_pause_button = self.builder.get_object("play_pause_button")
        # Parent the dialog with mainwindow, since renderingdialog is hidden.
        # It allows this dialog to properly minimize together with mainwindow
        self.window.set_transient_for(self.app)

        # UI widgets
        self.window.set_icon_from_file(configure.get_pixmap_dir() + "/pitivi-render-16.png")

        # FIXME: re-enable these widgets when bugs #650710 and 637079 are fixed
        self.play_pause_button.hide()
        self.table1.hide()

    def updatePosition(self, fraction, estimated):
        self.progressbar.set_fraction(fraction)
        self.window.set_title(_("%d%% Rendered") % int(100 * fraction))
        if estimated:
            self.progressbar.set_text(_("About %s left") % estimated)

    def setState(self, state):
        if state == gst.STATE_PLAYING:
            self.play_pause_button.props.label = gtk.STOCK_MEDIA_PAUSE
            self.system.inhibitSleep(RenderDialog.INHIBIT_REASON)
        else:
            self.play_pause_button.props.label = 'pitivi-render'
            self.system.uninhibitSleep(RenderDialog.INHIBIT_REASON)

    def _cancelButtonClickedCb(self, unused_button):
        self.emit("cancel")

    def _pauseButtonClickedCb(self, unused_button):
        self.emit("pause")


class RenderDialog(Loggable):
    """Render dialog box.

    @ivar preferred_arenderer: The last audio renderer selected by the user.
    @type preferred_arenderer: str
    @ivar preferred_vrenderer: The last video renderer selected by the user.
    @type preferred_vrenderer: str
    @ivar settings: The settings used for rendering.
    @type settings: MultimediaSettings
    """
    INHIBIT_REASON = _("Currently rendering media")

    def __init__(self, app, project, pipeline=None):

        from pitivi.preset import RenderPresetManager

        Loggable.__init__(self)

        self.app = app
        self.project = project
        self.system = app.app.system
        self._timeline = self.app.timeline
        self._seeker = Seeker(80)
        if pipeline != None:
            self._pipeline = pipeline
        else:
            self._pipeline = self.project.pipeline

        self.outfile = None
        self.settings = project.getSettings()
        self.timestarted = 0

        # Various gstreamer signal connection ID's
        # {object: sigId}
        self._gstSigId = {}

        self.builder = gtk.Builder()
        self.builder.add_from_file(os.path.join(configure.get_ui_dir(),
            "renderingdialog.ui"))
        self._setProperties()
        self.builder.connect_signals(self)

        # UI widgets
        icon = os.path.join(configure.get_pixmap_dir(), "pitivi-render-16.png")
        self.window.set_icon_from_file(icon)

        # FIXME: re-enable this widget when bug #637078 is implemented
        self.selected_only_button.destroy()

        # The Render dialog and the Project Settings dialog have some
        # common settings, for example the audio sample rate.
        # When these common settings are changed in the Render dialog,
        # we don't want them to be saved, so we create a copy of the project's
        # settings to be used by the Render dialog for rendering.
        render_settings = project.getSettings().copy()
        # Note: render_settings will end up as self.settings.

        # Directory and Filename
        self.filebutton.set_current_folder(self.app.settings.lastExportFolder)
        if not self.project.name:
            self.updateFilename(_("Untitled"))
        else:
            self.updateFilename(self.project.name)

        # We store these so that when the user tries various container formats,
        # (AKA muxers) we select these a/v renderers, if they are compatible with
        # the current container format.
        self.preferred_vrenderer = self.settings.vrenderer
        self.preferred_arenderer = self.settings.arenderer

        self._initializeComboboxModels()
        self._displaySettings()
        self._displayRenderSettings()

        self.window.connect("delete-event", self._deleteEventCb)
        self.settings.connect("settings-changed", self._settingsChanged)

        # Monitor changes

        self.wg = RippleUpdateGroup()
        self.wg.addVertex(self.frame_rate_combo, signal="changed")
        self.wg.addVertex(self.save_render_preset_button,
                 update_func=self._updateRenderSaveButton)
        self.wg.addVertex(self.channels_combo, signal="changed")
        self.wg.addVertex(self.sample_rate_combo, signal="changed")
        self.wg.addVertex(self.sample_depth_combo, signal="changed")
        self.wg.addVertex(self.muxercombobox, signal="changed")
        self.wg.addVertex(self.audio_renderer_combo, signal="changed")
        self.wg.addVertex(self.video_renderer_combo, signal="changed")
        self.render_presets = RenderPresetManager()
        self.render_presets.loadAll()

        self._fillPresetsTreeview(
                self.render_preset_treeview, self.render_presets,
                self._updateRenderPresetButtons)

        self.wg.addEdge(self.frame_rate_combo,
            self.save_render_preset_button)
        self.wg.addEdge(self.audio_renderer_combo,
            self.save_render_preset_button)
        self.wg.addEdge(self.video_renderer_combo,
            self.save_render_preset_button)
        self.wg.addEdge(self.muxercombobox,
            self.save_render_preset_button)
        self.wg.addEdge(self.channels_combo,
            self.save_render_preset_button)
        self.wg.addEdge(self.sample_rate_combo,
            self.save_render_preset_button)
        self.wg.addEdge(self.sample_depth_combo,
            self.save_render_preset_button)

        self._infobarForPresetManager = {
                self.render_presets: self.render_preset_infobar}

        # Bind widgets to RenderPresetsManager
        self.bindCombo(self.render_presets, "channels",
            self.channels_combo)
        self.bindCombo(self.render_presets, "sample-rate",
            self.sample_rate_combo)
        self.bindCombo(self.render_presets, "depth",
            self.sample_depth_combo)
        self.bindCombo(self.render_presets, "acodec",
            self.audio_renderer_combo)
        self.bindCombo(self.render_presets, "vcodec",
            self.video_renderer_combo)
        self.bindCombo(self.render_presets, "container",
            self.muxercombobox)
        self.bindCombo(self.render_presets, "frame-rate",
            self.frame_rate_combo)
        self.bindHeight(self.render_presets)
        self.bindWidth(self.render_presets)

        self.createNoPreset(self.render_presets)

    def createNoPreset(self, mgr):
        mgr.prependPreset(_("No preset"), {
            "depth": int(get_combo_value(self.sample_depth_combo)),
            "channels": int(get_combo_value(self.channels_combo)),
            "sample-rate": int(get_combo_value(self.sample_rate_combo)),
            "acodec": get_combo_value(self.audio_renderer_combo).get_name(),
            "vcodec": get_combo_value(self.video_renderer_combo).get_name(),
            "container": get_combo_value(self.muxercombobox).get_name(),
            "frame-rate": gst.Fraction(int(get_combo_value(self.frame_rate_combo).num),
                                        int(get_combo_value(self.frame_rate_combo).denom)),
            "height": self.getDimension("height"),
            "width": self.getDimension("width")})

    def bindCombo(self, mgr, name, widget):
        if name == "container":
            mgr.bindWidget(name,
                lambda x: self.muxer_setter(widget, x),
                lambda: get_combo_value(widget).get_name())

        elif name == "acodec":
            mgr.bindWidget(name,
                lambda x: self.acodec_setter(widget, x),
                lambda: get_combo_value(widget).get_name())

        elif name == "vcodec":
            mgr.bindWidget(name,
                lambda x: self.vcodec_setter(widget, x),
                lambda: get_combo_value(widget).get_name())

        elif name == "depth":
            mgr.bindWidget(name,
                lambda x: self.sample_depth_setter(widget, x),
                lambda: get_combo_value(widget))

        elif name == "sample-rate":
            mgr.bindWidget(name,
                lambda x: self.sample_rate_setter(widget, x),
                lambda: get_combo_value(widget))

        elif name == "channels":
            mgr.bindWidget(name,
                lambda x: self.channels_setter(widget, x),
                lambda: get_combo_value(widget))

        elif name == "frame-rate":
            mgr.bindWidget(name,
                lambda x: self.framerate_setter(widget, x),
                lambda: get_combo_value(widget))

    def muxer_setter(self, widget, value):
        set_combo_value(widget, gst.element_factory_find(value))
        self.settings.setRenderers(muxer=value)

        # Update the extension of the filename.
        basename = os.path.splitext(self.fileentry.get_text())[0]
        self.updateFilename(basename)

        # Update muxer-dependent widgets.
        self.muxer_combo_changing = True
        try:
            self.updateAvailableEncoders()
        finally:
            self.muxer_combo_changing = False

    def acodec_setter(self, widget, value):
        set_combo_value(widget, gst.element_factory_find(value))
        self.settings.setRenderers(arenderer=value)
        if not self.muxer_combo_changing:
            # The user directly changed the audio renderer combo.
            self.preferred_arenderer = value

    def vcodec_setter(self, widget, value):
        set_combo_value(widget, gst.element_factory_find(value))
        self.settings.setRenderers(vrenderer=value)
        if not self.muxer_combo_changing:
            # The user directly changed the video renderer combo.
            self.preferred_vrenderer = value

    def sample_depth_setter(self, widget, value):
        set_combo_value(widget, value)
        self.settings.setAudioProperties(depth=value)

    def sample_rate_setter(self, widget, value):
        set_combo_value(widget, value)
        self.settings.setAudioProperties(rate=value)

    def channels_setter(self, widget, value):
        set_combo_value(widget, value)
        self.settings.setAudioProperties(nbchanns=value)

    def framerate_setter(self, widget, value):
        set_combo_value(widget, value)
        self.settings.setVideoProperties(framerate=value)

    def bindHeight(self, mgr):
        mgr.bindWidget("height",
                       lambda x: self.settings.setVideoProperties(height=x),
                       lambda: 0)

    def bindWidth(self, mgr):
        mgr.bindWidget("width",
                       lambda x: self.settings.setVideoProperties(width=x),
                       lambda: 0)

    def getDimension(self, dimension):
        value = self.settings.getVideoWidthAndHeight()
        if dimension == "height":
            return value[1]
        elif dimension == "width":
            return value[0]

    def _fillPresetsTreeview(self, treeview, mgr, update_buttons_func):
        """Set up the specified treeview to display the specified presets.

        @param treeview: The treeview for displaying the presets.
        @type treeview: TreeView
        @param mgr: The preset manager.
        @type mgr: PresetManager
        @param update_buttons_func: A function which updates the buttons for
        removing and saving a preset, enabling or disabling them accordingly.
        @type update_buttons_func: function
        """
        renderer = gtk.CellRendererText()
        renderer.props.editable = True
        column = gtk.TreeViewColumn("Preset", renderer, text=0)
        treeview.append_column(column)
        treeview.props.headers_visible = False
        model = mgr.getModel()
        treeview.set_model(model)
        model.connect("row-inserted", self._newPresetCb,
            column, renderer, treeview)
        renderer.connect("edited", self._presetNameEditedCb, mgr)
        renderer.connect("editing-started", self._presetNameEditingStartedCb,
            mgr)
        treeview.get_selection().connect("changed", self._presetChangedCb,
            mgr, update_buttons_func)
        treeview.connect("focus-out-event", self._treeviewDefocusedCb, mgr)

    def _newPresetCb(self, model, path, iter_, column, renderer, treeview):
        """Handle the addition of a preset to the model of the preset manager.
        """
        treeview.set_cursor_on_cell(path, column, renderer, start_editing=True)
        treeview.grab_focus()

    def _presetNameEditedCb(self, renderer, path, new_text, mgr):
        """Handle the renaming of a preset."""
        from pitivi.preset import DuplicatePresetNameException

        try:
            mgr.renamePreset(path, new_text)
            self._updateRenderPresetButtons()
        except DuplicatePresetNameException:
            error_markup = _('"%s" already exists.') % new_text
            self._showPresetManagerError(mgr, error_markup)

    def _presetNameEditingStartedCb(self, renderer, editable, path, mgr):
        """Handle the start of a preset renaming."""
        self._hidePresetManagerError(mgr)

    def _treeviewDefocusedCb(self, widget, event, mgr):
        """Handle the treeview loosing the focus."""
        self._hidePresetManagerError(mgr)

    def _showPresetManagerError(self, mgr, error_markup):
        """Show the specified error on the infobar associated with the manager.

        @param mgr: The preset manager for which to show the error.
        @type mgr: PresetManager
        """
        infobar = self._infobarForPresetManager[mgr]
        # The infobar must contain exactly one object in the content area:
        # a label for displaying the error.
        label = infobar.get_content_area().children()[0]
        label.set_markup(error_markup)
        infobar.show()

    def _hidePresetManagerError(self, mgr):
        """Hide the error infobar associated with the manager.

        @param mgr: The preset manager for which to hide the error infobar.
        @type mgr: PresetManager
        """
        infobar = self._infobarForPresetManager[mgr]
        infobar.hide()

    def _updateRenderSaveButton(self, unused_in, button):
        button.set_sensitive(self.render_presets.isSaveButtonSensitive())

    @staticmethod
    def _getUniquePresetName(mgr):
        """Get a unique name for a new preset for the specified PresetManager.
        """
        existing_preset_names = list(mgr.getPresetNames())
        preset_name = _("New preset")
        i = 1
        while preset_name in existing_preset_names:
            preset_name = _("New preset %d") % i
            i += 1
        return preset_name

    def _addRenderPresetButtonClickedCb(self, button):
        preset_name = self._getUniquePresetName(self.render_presets)
        self.render_presets.addPreset(preset_name, {
            "depth": int(get_combo_value(self.sample_depth_combo)),
            "channels": int(get_combo_value(self.channels_combo)),
            "sample-rate": int(get_combo_value(self.sample_rate_combo)),
            "acodec": get_combo_value(self.audio_renderer_combo).get_name(),
            "vcodec": get_combo_value(self.video_renderer_combo).get_name(),
            "container": get_combo_value(self.muxercombobox).get_name(),
            "frame-rate": gst.Fraction(int(get_combo_value(self.frame_rate_combo).num),
                                        int(get_combo_value(self.frame_rate_combo).denom)),
            "height": 0,
            "width": 0})

        self.render_presets.restorePreset(preset_name)
        self._updateRenderPresetButtons()

    def _saveRenderPresetButtonClickedCb(self, button):
        self.render_presets.savePreset()
        self.save_render_preset_button.set_sensitive(False)
        self.remove_render_preset_button.set_sensitive(True)

    def _updateRenderPresetButtons(self):
        can_save = self.render_presets.isSaveButtonSensitive()
        self.save_render_preset_button.set_sensitive(can_save)
        can_remove = self.render_presets.isRemoveButtonSensitive()
        self.remove_render_preset_button.set_sensitive(can_remove)

    def _removeRenderPresetButtonClickedCb(self, button):
        selection = self.render_preset_treeview.get_selection()
        model, iter_ = selection.get_selected()
        if iter_:
            self.render_presets.removePreset(model[iter_][0])

    def _presetChangedCb(self, selection, mgr, update_preset_buttons_func):
        """Handle the selection of a preset."""
        model, iter_ = selection.get_selected()
        if iter_:
            self.selected_preset = model[iter_][0]
        else:
            self.selected_preset = None

        mgr.restorePreset(self.selected_preset)
        self._displaySettings()
        update_preset_buttons_func()
        self._hidePresetManagerError(mgr)

    def _setProperties(self):
        self.window = self.builder.get_object("render-dialog")
        self.selected_only_button = self.builder.get_object(
            "selected_only_button")
        self.frame_rate_combo = self.builder.get_object("frame_rate_combo")
        self.scale_spinbutton = self.builder.get_object("scale_spinbutton")
        self.channels_combo = self.builder.get_object("channels_combo")
        self.sample_rate_combo = self.builder.get_object(
                        "sample_rate_combo")
        self.sample_depth_combo = self.builder.get_object(
                        "sample_depth_combo")
        self.muxercombobox = self.builder.get_object("muxercombobox")
        self.audio_renderer_combo = self.builder.get_object(
            "audio_renderer_combo")
        self.video_renderer_combo = self.builder.get_object(
            "video_renderer_combo")
        self.filebutton = self.builder.get_object("filebutton")
        self.fileentry = self.builder.get_object("fileentry")
        self.resolution_label = self.builder.get_object("resolution_label")
        self.render_preset_treeview = self.builder.get_object(
                                        "render_preset_treeview")
        self.save_render_preset_button = self.builder.get_object(
                                        "save_render_preset_button")
        self.remove_render_preset_button = self.builder.get_object(
                                        "remove_render_preset_button")
        self.render_preset_infobar = self.builder.get_object(
            "render-preset-infobar")

    def _settingsChanged(self, settings):
        self.updateResolution()

    def _initializeComboboxModels(self):
        # Avoid loop import
        from pitivi.settings import MultimediaSettings
        self.frame_rate_combo.set_model(frame_rates)
        self.channels_combo.set_model(audio_channels)
        self.sample_rate_combo.set_model(audio_rates)
        self.sample_depth_combo.set_model(audio_depths)
        self.muxercombobox.set_model(factorylist(MultimediaSettings.muxers))

    def _displaySettings(self):
        """Display the settings that also change in the ProjectSettingsDialog.
        """
        # Video settings
        set_combo_value(self.frame_rate_combo, self.settings.videorate)
        # Audio settings
        set_combo_value(self.channels_combo, self.settings.audiochannels)
        set_combo_value(self.sample_rate_combo, self.settings.audiorate)
        set_combo_value(self.sample_depth_combo, self.settings.audiodepth)

    def _displayRenderSettings(self):
        """Display the settings which can be changed only in the RenderDialog.
        """
        # Video settings
        # note: this will trigger an update of the video resolution label
        self.scale_spinbutton.set_value(self.settings.render_scale)
        # Muxer settings
        # note: this will trigger an update of the codec comboboxes
        set_combo_value(self.muxercombobox,
            gst.element_factory_find(self.settings.muxer))

    def _checkForExistingFile(self, *args):
        """
        Display a warning icon and tooltip if the file path already exists.
        """
        path = self.filebutton.get_current_folder()
        if not path:
            # This happens when the window is initialized.
            return
        warning_icon = gtk.STOCK_DIALOG_WARNING
        filename = self.fileentry.get_text()
        if not filename:
            tooltip_text = _("A file name is required.")
        elif filename and os.path.exists(os.path.join(path, filename)):
            tooltip_text = _("This file already exists.\n"
                             "If you don't want to overwrite it, choose a "
                             "different file name or folder.")
        else:
            warning_icon = None
            tooltip_text = None
        self.fileentry.set_icon_from_stock(1, warning_icon)
        self.fileentry.set_icon_tooltip_text(1, tooltip_text)

    def updateFilename(self, basename):
        """Updates the filename UI element to show the specified file name."""
        extension = extension_for_muxer(self.settings.muxer)
        if extension:
            name = "%s%s%s" % (basename, os.path.extsep, extension)
        else:
            name = basename
        self.fileentry.set_text(name)

    def updateAvailableEncoders(self):
        """Update the renderer comboboxes to show the available renderers."""
        video_renderers = self.settings.getVideoEncoders()
        video_renderer_model = factorylist(video_renderers)
        self.video_renderer_combo.set_model(video_renderer_model)

        audio_renderers = self.settings.getAudioEncoders()
        audio_renderer_model = factorylist(audio_renderers)
        self.audio_renderer_combo.set_model(audio_renderer_model)

        self._updateEncoderCombo(
                self.video_renderer_combo, self.preferred_vrenderer)
        self._updateEncoderCombo(
                self.audio_renderer_combo, self.preferred_arenderer)

    def _updateEncoderCombo(self, renderer_combo, preferred_renderer):
        """Select the specified renderer for the specified renderer combo."""
        if preferred_renderer:
            # A preferrence exists, pick it if it can be found in
            # the current model of the combobox.
            vrenderer = gst.element_factory_find(preferred_renderer)
            set_combo_value(renderer_combo, vrenderer, default_index=0)
        else:
            # No preferrence exists, pick the first renderer from
            # the current model of the combobox.
            renderer_combo.set_active(0)

    def _elementSettingsDialog(self, factory, settings_attr):
        """Open a dialog to edit the properties for the specified factory.

        @param factory: An element factory whose properties the user will edit.
        @type factory: gst.ElementFactory
        @param settings_attr: The MultimediaSettings attribute holding
        the properties.
        @type settings_attr: str
        """
        properties = getattr(self.settings, settings_attr)
        self.dialog = GstElementSettingsDialog(factory, properties=properties)
        self.dialog.window.set_transient_for(self.window)
        self.dialog.ok_btn.connect("clicked", self._okButtonClickedCb, settings_attr)
        self.dialog.window.run()

    def startAction(self):
        """ Start the render process """
        self._pipeline.set_state(gst.STATE_NULL)
        self._pipeline.set_mode(ges.TIMELINE_MODE_SMART_RENDER)
        encodebin = self._pipeline.get_by_name("internal-encodebin")
        self._gstSigId[encodebin] = encodebin.connect("element-added",
                self._elementAddedCb)
        self.timestarted = time.time()
        self._pipeline.set_state(gst.STATE_PLAYING)

    def _cancelRender(self, progress):
        self.debug("aborting render")
        self._shutDown()
        self._destroyProgressWindow()

    def _shutDown(self):
        """ The render process has been aborted, shutdown the gstreamer pipeline
        and disconnect from its signals """
        self._pipeline.set_state(gst.STATE_NULL)
        self._disconnectFromGst()
        self._pipeline.set_mode(ges.TIMELINE_MODE_PREVIEW)

    def _pauseRender(self, progress):
        togglePlayback(self._pipeline)

    def _destroyProgressWindow(self):
        """ Handle the completion or the cancellation of the render process. """
        self.progress.window.destroy()
        self.progress = None
        self.window.show()  # Show the rendering dialog again

    def _disconnectFromGst(self):
        for obj, id in self._gstSigId.iteritems():
            obj.disconnect(id)
        self._gstSigId = {}
        self._seeker.disconnect_by_function(self._updatePositionCb)

    def _updateProjectSettings(self):
        """Updates the settings of the project if the render settings changed.
        """
        settings = self.project.getSettings()
        if (settings.muxer == self.settings.muxer
            and settings.arenderer == self.settings.arenderer
            and settings.vrenderer == self.settings.vrenderer
            and settings.containersettings == self.settings.containersettings
            and settings.acodecsettings == self.settings.acodecsettings
            and settings.vcodecsettings == self.settings.vcodecsettings
            and settings.render_scale == self.settings.render_scale):
            # No setting which can be changed in the Render dialog
            # and which we want to save have been changed.
            return
        settings.setRenderers(muxer=self.settings.muxer,
                             arenderer=self.settings.arenderer,
                             vrenderer=self.settings.vrenderer)
        settings.containersettings = self.settings.containersettings
        settings.acodecsettings = self.settings.acodecsettings
        settings.vcodecsettings = self.settings.vcodecsettings
        settings.setVideoProperties(render_scale=self.settings.render_scale)
        # Signal that the project settings have been changed.
        self.project.setSettings(settings)

    def destroy(self):
        self._updateProjectSettings()
        self.window.destroy()

    #------------------- Callbacks ------------------------------------------#

    #-- UI callbacks
    def _okButtonClickedCb(self, unused_button, settings_attr):
        setattr(self.settings, settings_attr, self.dialog.getSettings())
        self.dialog.window.destroy()

    def _renderButtonClickedCb(self, unused_button):
        """ The render button inside the render dialog has been clicked,
        start the rendering process. """
        self.outfile = os.path.join(self.filebutton.get_uri(),
                                    self.fileentry.get_text())
        self.progress = RenderingProgressDialog(self.app, self)
        self.window.hide()  # Hide the rendering settings dialog while rendering

        # FIXME GES: Handle presets here!
        # FIXME: Handle audio-only or video-only here
        self.containerprofile = gst.pbutils.EncodingContainerProfile(None, None,
                gst.Caps(self.muxertype), None)
        self.videoprofile = gst.pbutils.EncodingVideoProfile(gst.Caps(self.videotype),
                None, self.settings.getVideoCaps(True), 0)
        self.audioprofile = gst.pbutils.EncodingAudioProfile(gst.Caps(self.audiotype), None,
                self.settings.getAudioCaps(), 0)

        self.containerprofile.add_profile(self.videoprofile)
        self.containerprofile.add_profile(self.audioprofile)
        self._pipeline.set_render_settings(self.outfile, self.containerprofile)
        self.startAction()
        self.progress.window.show()
        self.progress.connect("cancel", self._cancelRender)
        self.progress.connect("pause", self._pauseRender)
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        self._gstSigId[bus] = bus.connect('message', self._busMessageCb)
        self._seeker.connect("position-changed", self._updatePositionCb)

    def _closeButtonClickedCb(self, unused_button):
        self.debug("Render dialog's Close button clicked")
        self.destroy()

    def _deleteEventCb(self, window, event):
        self.debug("Render dialog is being deleted")
        self.destroy()

    #-- GStreamer callbacks
    def _busMessageCb(self, unused_bus, message):
        if message.type == gst.MESSAGE_EOS:  # Render complete
            self.debug("got EOS message, render complete")
            self._shutDown()
            self._destroyProgressWindow()
        elif message.type == gst.MESSAGE_STATE_CHANGED and self.progress:
            prev, state, pending = message.parse_state_changed()
            self.progress.setState(state)

    def _updatePositionCb(self, seeker, position):
        if self.progress:
            text = None
            timediff = time.time() - self.timestarted
            length = self._timeline.duration
            fraction = float(min(position, length)) / float(length)
            if timediff > 5.0 and position:
                # only display ETA after 5s in order to have enough averaging and
                # if the position is non-null
                totaltime = (timediff * float(length) / float(position)) - timediff
                text = beautify_ETA(int(totaltime * gst.SECOND))
            self.progress.updatePosition(fraction, text)

    def _elementAddedCb(self, bin, element):
        # Setting properties on gst.Element-s has they are added to the
        # gst.Encodebin
        if element.get_factory() == get_combo_value(self.video_renderer_combo):
            for setting in self.settings.vcodecsettings:
                element.set_property(setting, self.settings.vcodecsettings[setting])
        elif element.get_factory() == get_combo_value(self.audio_renderer_combo):
            for setting in self.settings.acodecsettings:
                element.set_property(setting, self.settings.vcodecsettings[setting])

    #-- Settings changed callbacks
    def _scaleSpinbuttonChangedCb(self, button):
        render_scale = self.scale_spinbutton.get_value()
        self.settings.setVideoProperties(render_scale=render_scale)
        self.updateResolution()

    def updateResolution(self):
        width, height = self.settings.getVideoWidthAndHeight(render=True)
        self.resolution_label.set_text(u"%d×%d" % (width, height))

    def _projectSettingsButtonClickedCb(self, button):
        from pitivi.project import ProjectSettingsDialog
        dialog = ProjectSettingsDialog(self.window, self.project)
        dialog.window.connect("destroy", self._projectSettingsDestroyCb)
        dialog.window.run()

    def _projectSettingsDestroyCb(self, dialog):
        """Handle the destruction of the ProjectSettingsDialog."""
        settings = self.project.getSettings()
        self.settings.setVideoProperties(width=settings.videowidth,
                                         height=settings.videoheight,
                                         framerate=settings.videorate)
        self.settings.setAudioProperties(nbchanns=settings.audiochannels,
                                         rate=settings.audiorate,
                                         depth=settings.audiodepth)
        self._displaySettings()

    def _frameRateComboChangedCb(self, combo):
        framerate = get_combo_value(combo)
        self.settings.setVideoProperties(framerate=framerate)

    def _videoEncoderComboChangedCb(self, combo):
        vrenderer = get_combo_value(combo).get_name()
        for template in gst.registry_get_default().lookup_feature(vrenderer).get_static_pad_templates():
            if template.name_template == "src":
                self.videotype = template.get_caps().to_string()
                for elem in self.videotype.split(","):
                    if "{" in elem or "[" in elem:
                        self.videotype = self.videotype[:self.videotype.index(elem) - 1]
                        break
        self.settings.setRenderers(vrenderer=vrenderer)
        if not self.muxer_combo_changing:
            # The user directly changed the video renderer combo.
            self.preferred_vrenderer = vrenderer

    def _videoSettingsButtonClickedCb(self, button):
        factory = get_combo_value(self.video_renderer_combo)
        self._elementSettingsDialog(factory, 'vcodecsettings')

    def _channelsComboChangedCb(self, combo):
        self.settings.setAudioProperties(nbchanns=get_combo_value(combo))

    def _sampleDepthComboChangedCb(self, combo):
        self.settings.setAudioProperties(depth=get_combo_value(combo))

    def _sampleRateComboChangedCb(self, combo):
        self.settings.setAudioProperties(rate=get_combo_value(combo))

    def _audioEncoderChangedComboCb(self, combo):
        arenderer = get_combo_value(combo).get_name()
        self.settings.setRenderers(arenderer=arenderer)
        for template in gst.registry_get_default().lookup_feature(arenderer).get_static_pad_templates():
            if template.name_template == "src":
                self.audiotype = template.get_caps().to_string()
                for elem in self.audiotype.split(","):
                    if "{" in elem or "[" in elem:
                        self.audiotype = self.audiotype[:self.audiotype.index(elem) - 1]
                        break
        if not self.muxer_combo_changing:
            # The user directly changed the audio renderer combo.
            self.preferred_arenderer = arenderer

    def _audioSettingsButtonClickedCb(self, button):
        factory = get_combo_value(self.audio_renderer_combo)
        self._elementSettingsDialog(factory, 'acodecsettings')

    def _muxerComboChangedCb(self, muxer_combo):
        """Handle the changing of the container format combobox."""
        muxer = get_combo_value(muxer_combo).get_name()
        for template in gst.registry_get_default().lookup_feature(muxer).get_static_pad_templates():
            if template.name_template == "src":
                self.muxertype = template.get_caps().to_string()
        self.settings.setRenderers(muxer=muxer)

        # Update the extension of the filename.
        basename = os.path.splitext(self.fileentry.get_text())[0]
        self.updateFilename(basename)

        # Update muxer-dependent widgets.
        self.muxer_combo_changing = True
        try:
            self.updateAvailableEncoders()
        finally:
            self.muxer_combo_changing = False
