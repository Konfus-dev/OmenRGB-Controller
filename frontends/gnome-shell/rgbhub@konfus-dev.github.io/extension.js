// SPDX-License-Identifier: GPL-3.0-or-later
//
// The "RGB Hub" button in the top-right system menu.
//
// Pressing it opens the settings window (prefs.js), where everything is
// changed. The background service (service/rgbhub) then does the real work,
// so nothing in here can slow down or freeze the desktop.
//
// For auto mode this file also tells the service which app has focus, by
// writing its id to the "focused-app" setting.

import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {QuickToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';

const ICON = 'keyboard-brightness-symbolic';

// Focus is on "nothing" for an instant whenever a menu or the overview
// opens. Only report "nothing in focus" once it has lasted this long.
const NO_FOCUS_DELAY_MS = 800;

// GNOME shows every extension's settings in a window owned by this app.
const PREFS_APP_ID = 'org.gnome.Shell.Extensions';

/** Our settings window if it is open on any workspace, otherwise null. */
function findSettingsWindow(title) {
    return global.get_window_actors()
        .map(actor => actor.meta_window)
        .find(window =>
            window.get_title() === title &&
            (window.get_gtk_application_id() === PREFS_APP_ID ||
             window.get_wm_class() === PREFS_APP_ID)) ?? null;
}

const OmenButton = GObject.registerClass(
class OmenButton extends QuickToggle {
    _init(extension) {
        // toggleMode: false makes it a plain button, not an on/off switch.
        super._init({title: 'RGB Hub', iconName: ICON, toggleMode: false});

        this.connect('clicked', () => {
            Main.panel.closeQuickSettings();
            // If the settings window is already open, bring it to the front
            // instead of asking for a second one (which GNOME would refuse).
            const open = findSettingsWindow(extension.metadata.name);
            if (open)
                Main.activateWindow(open);
            else
                extension.openPreferences();
        });
    }
});

const OmenIndicator = GObject.registerClass(
class OmenIndicator extends SystemIndicator {
    _init(extension) {
        super._init();
        this.quickSettingsItems.push(new OmenButton(extension));
    }

    destroy() {
        this.quickSettingsItems.forEach(item => item.destroy());
        super.destroy();
    }
});

export default class OmenLightsExtension extends Extension {
    enable() {
        this._indicator = new OmenIndicator(this);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);

        // Auto mode is an extra: if it cannot start, the button still works.
        try {
            this._startFocusTracking();
        } catch (error) {
            console.error(`RGB Hub: auto mode is unavailable: ${error}`);
        }
    }

    disable() {
        this._stopFocusTracking();
        this._indicator?.destroy();
        this._indicator = null;
    }

    // --- auto mode: report which app has focus ---------------------------------

    _startFocusTracking() {
        this._settings = this.getSettings();
        this._tracker = Shell.WindowTracker.get_default();
        this._focusId = this._tracker.connect('notify::focus-app', () => this._publishFocus());
        // Report straight away when auto mode is switched on.
        this._autoId = this._settings.connect('changed::auto-enabled', () => this._publishFocus());
        // Forget whatever app the last session ended on, then report afresh.
        this._settings.set_string('focused-app', '');
        this._publishFocus();
    }

    _stopFocusTracking() {
        this._cancelNoFocusTimer();
        this._settings?.set_string('focused-app', '');
        if (this._focusId)
            this._tracker.disconnect(this._focusId);
        if (this._autoId)
            this._settings.disconnect(this._autoId);
        this._focusId = this._autoId = null;
        this._tracker = this._settings = null;
    }

    _cancelNoFocusTimer() {
        if (this._noFocusTimer)
            GLib.source_remove(this._noFocusTimer);
        this._noFocusTimer = null;
    }

    _publishFocus() {
        this._cancelNoFocusTimer();
        if (!this._settings.get_boolean('auto-enabled'))
            return; // do not write anything while auto mode is off

        const id = this._tracker.focus_app?.get_id() ?? '';
        if (id) {
            this._report(id);
            return;
        }
        // Nothing has focus. Wait, then report it only if still true: the
        // service then shows the idle look (the OS logo and colour).
        this._noFocusTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, NO_FOCUS_DELAY_MS, () => {
            this._noFocusTimer = null;
            if (!this._tracker.focus_app)
                this._report('');
            return GLib.SOURCE_REMOVE;
        });
    }

    _report(id) {
        if (id !== this._settings.get_string('focused-app'))
            this._settings.set_string('focused-app', id);
    }
}
