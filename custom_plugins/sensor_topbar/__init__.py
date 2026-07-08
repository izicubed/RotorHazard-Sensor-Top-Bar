'''
Sensor Top Bar plugin for RotorHazard.

Adds a dark, flat-design top bar (shown on every page) that displays live
telemetry from any installed sensors, plus a computed battery charge %.

The entry point below only registers handlers; all real work happens once the
server has finished starting up (Evt.STARTUP), which is the point at which
hardware sensors have already been discovered.
'''

from eventmanager import Evt
from .topbar_controller import TopBarController


def initialize(rhapi):
    controller = TopBarController(rhapi)

    # Serve the plugin's front-end assets (JS/CSS) via a Flask blueprint.
    # Done at import time so the routes exist before any page is served.
    controller.register_blueprint()

    # Register UI (settings panel + options) and start the telemetry loop only
    # after startup, when sensor discovery has completed.
    rhapi.events.on(Evt.STARTUP, controller.on_startup)

    # Let a freshly-loaded page pull the latest reading immediately rather than
    # waiting for the next broadcast tick.
    rhapi.ui.socket_listen('topbar_request', controller.on_request)
