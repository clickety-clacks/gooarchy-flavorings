import QtQuick
import Quickshell
import Quickshell.Io

FloatingWindow {
    id: root
    title: "Scottland widget: System Stats"
    color: "transparent"

    property var reading: ({})
    property var palette: ({})
    readonly property color background: palette.background || "#2e3440"
    readonly property color foreground: palette.foreground || "#d8dee9"
    readonly property string fontFamily: palette.font_family || Qt.application.font.family
    readonly property real textScale: Math.max(0.5, Math.min(3, Number(palette.text_scale) || 1))
    readonly property real padding: 18
    readonly property real rowGap: 4
    readonly property real fontSize: 18 * textScale

    readonly property bool cpuAvailable: typeof reading.cpu === "number"
        && Number.isInteger(reading.cpu) && reading.cpu >= 0 && reading.cpu <= 100
    readonly property bool memoryAvailable: typeof reading.memory === "number"
        && Number.isInteger(reading.memory) && reading.memory >= 0 && reading.memory <= 100
    readonly property string cpuText: cpuAvailable ? `CPU ${reading.cpu}%` : "CPU unavailable"
    readonly property string memoryText: memoryAvailable ? `Mem ${reading.memory}%` : "Mem unavailable"

    TextMetrics { id: cpuMetrics; text: root.cpuText; font.family: root.fontFamily; font.pixelSize: root.fontSize }
    TextMetrics { id: memoryMetrics; text: root.memoryText; font.family: root.fontFamily; font.pixelSize: root.fontSize }

    implicitWidth: Math.max(184, Math.ceil(Math.max(cpuMetrics.advanceWidth, memoryMetrics.advanceWidth)) + 2 * padding)
    implicitHeight: Math.max(96, Math.ceil(2 * fontSize + rowGap + 2 * padding))
    minimumSize: Qt.size(implicitWidth, implicitHeight)
    maximumSize: Qt.size(implicitWidth, implicitHeight)

    FileView {
        path: Quickshell.env("SCOTTLAND_PALETTE") || ""
        watchChanges: true
        printErrors: false
        onFileChanged: reload()
        onLoaded: {
            try { root.palette = JSON.parse(text()) }
            catch (_error) { root.palette = ({}) }
        }
    }

    Process {
        id: dataReader
        command: ["python3", Quickshell.shellRoot + "/data.py"]
        running: true
        stdout: SplitParser {
            onRead: data => {
                try {
                    const value = JSON.parse(data)
                    root.reading = value && typeof value === "object" && !Array.isArray(value) ? value : ({})
                } catch (_error) {
                    root.reading = ({})
                }
            }
        }
    }

    Process {
        id: openWindow
        command: ["busctl", "--user", "call", "org.scottland.Widgets",
            "/org/scottland/widget/" + (Quickshell.env("SCOTTLAND_WIDGET_ID") || ""),
            "org.scottland.Widget", "Open"]
    }

    Rectangle {
        anchors.fill: parent
        radius: 16
        color: root.background

        Column {
            anchors.centerIn: parent
            spacing: root.rowGap

            Text {
                text: root.cpuText
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: root.fontSize
                font.weight: Font.DemiBold
            }

            Text {
                text: root.memoryText
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: root.fontSize
                font.weight: Font.DemiBold
            }
        }

        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: openWindow.running = true
        }
    }

    Connections {
        target: Quickshell
        function onLastWindowClosed() {
            dataReader.running = false
            Qt.quit()
        }
    }
}
