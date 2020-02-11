#!/usr/bin/env python3
#
# Polychromatic is licensed under the GPLv3.
# Copyright (C) 2017-2020 Luke Horwell <code@horwell.me>
#
"""
This module is the 'controller' aspect of Polychromatic Controller.
"""

import os
import json
import glob
import gettext
import webbrowser

from threading import Thread
from . import common
from . import locales
from . import preferences as pref
from .backends import openrazer


class PolychromaticController():
    """
    Functions for Polychromatic's GUI operations.
    """
    def __init__(self, _app, _window, _webview, _debug):
        # Required for program operation
        global dbg
        dbg = _debug

        self.window = _window
        self.webview = _webview
        self.send_view_data = _app.send_view_data
        self.send_view_variable = _app.send_view_variable

        # Set later in initalise_app()
        self.version = None
        self.versions = None
        self.backends = {
            "openrazer": False
        }

    def parse_request(self, request, data):
        """
        Process a request sent from the frontend to the controller.
        """
        try:
            requests = {
                "update_device_list": self._update_device_list,
                "open_device": self._open_device,
                "apply_to_all": self._apply_to_all,
                "set_device_state": self._set_device_state,
                "debug_matrix": self._debug_matrix,
                "open_help": self._open_help,
                "troubleshoot_openrazer": self._troubleshoot_openrazer
            }
        except KeyError:
            dbg.stdout("Unknown Request: " + str(request) + " with data: " + str(data), dbg.error)
            self._internal_error("Internal Error", "<code>{0}</code> is not implemented.".format(request), "serious")
            return False

        try:
            requests[request](data)
        except Exception as e:
            dbg.stdout("Failed to execute request: " + str(request) + " with data: " + str(data), dbg.error)
            dbg.stdout(common.get_exception_as_string(e), dbg.error)
            traceback = common.get_exception_as_string(e)
            self._internal_error(locales.LOCALES["error_generic_title"], locales.LOCALES["error_generic_text"] + "<br><br><code>{0}</code>".format(traceback), "serious")

    def run_function(self, function, data={}):
        """
        When the Controller is ready to update the frontend, run a function
        and pass data.
        """
        self.webview.run_js("{0}({1});".format(function, json.dumps(data)))

    def initalise_app(self, version, versions):
        """
        Starts loading the logic for the application.
        """
        self.version = version
        self.versions = versions
        dbg.stdout("Version " + version, dbg.debug, 1)

        self.send_view_variable("LOCALES", locales.LOCALES)
        self.send_view_variable("COLOURS", pref.load_file(pref.path.colours))
        self.send_view_variable("BUTTON_SVGS", self._get_button_svg_list())
        self.run_function("build_view")

        # View caches device list via the CACHE_DEVICES variable.
        dbg.stdout("OpenRazer: Getting device list...", dbg.action, 1)
        devices = openrazer.get_device_list()
        self.send_view_variable("CACHE_DEVICES", devices)

        if devices == -1:
            dbg.stdout("OpenRazer: Daemon not running", dbg.error)

        elif type(devices) == str:
            dbg.stdout("OpenRazer: Error! Exception: " + str(devices), dbg.error)
            self._internal_error(locales.LOCALES["error_not_ready_title"], locales.LOCALES["error_not_ready_text"] + "<code>{0}</code>".format(devices), "serious")

        else:
            # Daemon OK
            dbg.stdout("OpenRazer: Ready", dbg.success, 1)
            self.send_view_variable("OPENRAZER_READY", True);
            self.backends["openrazer"] = True

        # Warn if configuration is compatible for this version.
        pref_data = pref.load_file(path.preferences)
        pref_version = pref.version
        save_version = pref_data["config_version"]
        if save_version > pref_version:
            self.run_function("_warn_save_data_version", {
                "app_version": version,
                "pref_version": pref_version,
                "save_version": save_version
            })

        dbg.stdout("Application Ready. Showing window.", dbg.success, 1)
        self.window.show_window()

        self.run_function("_set_tab_devices")
        return True

    def _internal_error(self, title, reason, style):
        """
        Inform the user of event of a serious problem at the Controller layer.
        """
        self.webview.run_js("open_dialog(`{0}`, `{1}`, '{2}', [['OK', '']], '40em', '80em')".format(title, reason.replace("\n", "<br>"), style));

    def _get_button_svg_list(self):
        """
        Collects all SVGs and stores them into an array so the view can use them
        when building buttons with icons.

        Instead of an img tag, an svg tag allows manipulation of colours via CSS.
        """
        icons = glob.glob(common.get_data_dir_path() + "/ui/img/button/*.svg")
        output = {}
        for path in icons:
            name = path.split("/").pop().replace(".svg", "")
            with open(path, "r") as f:
                output[name] = "".join(f.readlines())
        return output

    def _update_device_list(self, data=None):
        """
        Sends an updated device list/integer to the controler.

        Data parameter:
        {
            "callback": <Name of JavaScript function to run>
        }
        """
        self.send_view_variable("CACHE_DEVICES", openrazer.get_device_list())
        self.run_function(data["callback"])

    def _open_device(self, data):
        """
        Shows the details page for a specific device.

        Data parameter:
        {
            "uid": <id in Razer list>
        }
        """
        data = openrazer.get_device(data["uid"])

        if data == None:
            # Device no longer avaiable (-1)
            self.run_function("open_device_overview")
        elif data == str:
            # Daemon exception (-2)
            self.run_function("_open_device_error", {"code": -2, "exception": data})
        else:
            # OK
            self.run_function("_open_device", data)

    def _apply_to_all(self, data):
        """
        Sets all compatible devices to a specific state.

        Data parameter:
        {
            "type": <str: "string" or "brightness">,
            "value": <str: effect name>
                     <int: brightness value>
                     <str: colour hex>
        }
        """
        request_type = data["type"]
        request_value = data["value"]

        # Default values for effects
        effect_params = {
            "spectrum": None,
            "wave": 1,
            "reactive": 2,
            "breath_single": None,
            "static": None
        }

        for device in openrazer.get_device_list():
            if device["available"] == False:
                continue

            for zone in device["zones"]:
                if request_type == "effect":
                    param = effect_params[request_value]
                    openrazer.set_device_state(device["uid"], request_value, zone, None, [param])

                elif request_type == "brightness":
                    openrazer.set_device_state(device["uid"], "brightness", zone, None, [request_value])

                elif request_type == "colour":
                    openrazer.set_device_colours(device["uid"], zone, [request_value])

    def _set_device_state(self, data):
        """
        Sets the state of a specific device right now.

        Data parameter:
        {
            "uid": <id in backend>
            "backend": <backend provider>
            "backend_request": <string, e.g. 'wave', 'brightness', 'dpi'>
            "zone": <zone name>
            "colour_hex": [<string of primary hex value>, <second hex>, etc]
            "params": [<if applicable>] or empty: []
        }
        """
        uid = int(data["uid"])
        backend = data["backend"]
        backend_request = data["backend_request"]
        zone = data["zone"]
        colour_hex = data["colour_hex"]
        params = data["params"]

        dbg.stdout("Processing request '{2}' for device {0} in backend '{1}'...".format(uid, backend, backend_request), dbg.action, 1)
        request = openrazer.set_device_state(uid, backend_request, zone, colour_hex, params)

        if request == None:
            # Device no longer available
            dbg.stdout("Device not found in backend", dbg.warning)
            self._internal_error(locales.LOCALES["error_device_gone_title"], locales.LOCALES["error_device_gone_text"], "warning")

        elif request == False:
            # Invalid request
            dbg.stdout("Invalid request.", dbg.warning)
            self._internal_error(locales.LOCALES["error_bad_request_title"], locales.LOCALES["error_bad_request_text"], "warning")

        elif type(request) == str:
            # Daemon exception
            self._internal_error(locales.LOCALES["error_backend_title"], locales.LOCALES["error_backend_text"] + "<pre>{0}</pre>".format(request), "serious")
            dbg.stdout(request, dbg.error)

        elif request == True:
            # Request OK
            dbg.stdout("Successfully executed request", dbg.success, 1)

    def _debug_matrix(self, data):
        """
        Allows the user to test custom effect functionality.

        Data parameter:
        {
            "uid": <id in backend> (string)
            "backend": <backend provider> (string)
            "position": [row, column] (list)
        }
        """
        uid = int(data["uid"])
        backend = data["backend"]
        row = data["position"][0]
        column = data["position"][1]

        request = openrazer.debug_matrix(uid, int(row), int(column))

        if request == None:
            # Device no longer available
            self._internal_error(locales.LOCALES["error_device_gone_title"], locales.LOCALES["error_device_gone_text"], "warning")

        elif type(request) == str:
            # Daemon exception
            self._internal_error(locales.LOCALES["error_backend_title"], locales.LOCALES["error_backend_text"] + "<pre>{0}</pre>".format(request), "serious")
            dbg.stdout(request, dbg.error)

        elif request == True:
            # Request OK
            dbg.stdout("OK: [{0},{1}]".format(row, column), dbg.success, 1)

    def _open_help(self, data):
        """
        Opens the Polychromatic website for help resources.

        Data parameter is empty: {}
        """
        webbrowser.open("https://polychromatic.app/docs");

    def _troubleshoot_openrazer(self, data):
        """
        Performs some self checks for common issues with OpenRazer.

        Data parameter is empty: {}
        """
        try:
            dbg.stdout("Running troubleshooter for OpenRazer...", dbg.warning, 1)
            results = openrazer.troubleshoot()
            self.run_function("_show_troubleshoot_results", results)
            dbg.stdout("Troubleshooting finished.", dbg.success, 1)
        except:
            dbg.stdout("Troubleshooting encountered an exception.", dbg.error, 1)
            self._internal_error(locales.LOCALES["troubleshoot"], locales.LOCALES["troubleshoot_cannot_run"], "serious")


# Module Initalization
dbg = common.Debugging()
path = pref.Paths()
_ = common.setup_translations(__file__, "polychromatic")
