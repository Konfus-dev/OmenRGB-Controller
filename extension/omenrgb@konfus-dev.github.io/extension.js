// SPDX-License-Identifier: GPL-3.0-or-later
//
// The "OMEN Lights" button in the top-right system menu.
//
// Pressing it opens the settings window (prefs.js), where everything is
// changed. The background service (service/omenrgb) then does the real work,
// so nothing in here can slow down or freeze the desktop.

import GObject from 'gi://GObject';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {QuickToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';

const ICON = 'keyboard-brightness-symbolic';

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
        super._init({title: 'OMEN Lights', iconName: ICON, toggleMode: false});

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
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
