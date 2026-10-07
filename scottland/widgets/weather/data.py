#!/usr/bin/env python3
"""Stream this widget's published reading from Scottland's WG9 D-Bus property."""
import json
import os

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

BUS_NAME = "org.scottland.Widgets"
INTERFACE = "org.scottland.Widget"
PROPERTIES = "org.freedesktop.DBus.Properties"
WIDGET_ID = os.environ.get("SCOTTLAND_WIDGET_ID", "")
OBJECT_PATH = f"/org/scottland/widget/{WIDGET_ID}"


def emit(payload):
    if isinstance(payload, GLib.Variant):
        try:
            payload = payload.unpack()
        except (TypeError, ValueError):
            payload = None
    try:
        reading = json.loads(payload)
    except (TypeError, ValueError):
        reading = None
    print(json.dumps(reading if isinstance(reading, dict) else {}, separators=(",", ":")), flush=True)


def main():
    if not WIDGET_ID or "/" in WIDGET_ID:
        emit({})
        return

    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def read_data():
        try:
            reply = connection.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                PROPERTIES,
                "Get",
                GLib.Variant("(ss)", (INTERFACE, "Data")),
                GLib.VariantType.new("(v)"),
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
            emit(reply.get_child_value(0).get_variant().unpack())
        except (GLib.Error, AttributeError):
            emit({})

    def on_properties_changed(_connection, _sender, _path, _interface, _signal, parameters, _data):
        try:
            changed_interface, changed, invalidated = parameters.unpack()
        except (TypeError, ValueError):
            return
        if changed_interface != INTERFACE:
            return
        if "Data" in changed:
            emit(changed["Data"])
        elif "Data" in invalidated:
            read_data()

    connection.signal_subscribe(
        BUS_NAME,
        PROPERTIES,
        "PropertiesChanged",
        OBJECT_PATH,
        None,
        Gio.DBusSignalFlags.NONE,
        on_properties_changed,
    )
    # Subscribe first, then read: an app update during startup is observed either way.
    read_data()
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
