// SPDX-License-Identifier: GPL-3.0-or-later
//
// The "OMEN Lights" button in the top-right system menu.
//
// This file only reads and writes settings. The background service
// (service/omenrgb) sees the changes and does the real work, so nothing in
// here can slow down or freeze the desktop.

import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickMenuToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';

const ICON = 'keyboard-brightness-symbolic';
const RECENT_IMAGES_SHOWN = 4;

// A menu row with an icon and a slider tied to a 0-100 integer setting.
const SliderItem = GObject.registerClass(
class SliderItem extends PopupMenu.PopupBaseMenuItem {
    _init(settings, key, iconName, accessibleName) {
        super._init({activate: false});

        this.add_child(new St.Icon({icon_name: iconName, style_class: 'popup-menu-icon'}));

        this._slider = new Slider(settings.get_int(key) / 100);
        this._slider.x_expand = true;
        this._slider.accessible_name = accessibleName;
        this.add_child(this._slider);

        // Slider moved by the user -> save the setting.
        this._slider.connect('notify::value', () => {
            const percent = Math.round(this._slider.value * 100);
            if (percent !== settings.get_int(key))
                settings.set_int(key, percent);
        });
        // Setting changed elsewhere (settings window) -> move the slider.
        settings.connectObject(`changed::${key}`, () => {
            const percent = settings.get_int(key);
            if (percent !== Math.round(this._slider.value * 100))
                this._slider.value = percent / 100;
        }, this);
    }
});

const OmenToggle = GObject.registerClass(
class OmenToggle extends QuickMenuToggle {
    _init(extension) {
        super._init({title: 'OMEN Lights', iconName: ICON, toggleMode: true});

        this._settings = extension.getSettings();

        // Clicking the button switches all case lights on or off.
        this._settings.bind('lights-enabled', this, 'checked', Gio.SettingsBindFlags.DEFAULT);

        this.menu.setHeader(ICON, 'OMEN Lights');

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('Light brightness'));
        this.menu.addMenuItem(new SliderItem(
            this._settings, 'lights-brightness', 'display-brightness-symbolic', 'Light brightness'));

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('LCD brightness'));
        this.menu.addMenuItem(new SliderItem(
            this._settings, 'lcd-brightness', 'video-display-symbolic', 'LCD brightness'));

        this._recentSection = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._recentSection);
        this._settings.connectObject(
            'changed::lcd-recent-images', () => this._rebuildRecent(),
            'changed::lcd-image', () => this._rebuildRecent(),
            this);
        this._rebuildRecent();

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Settings…', () => extension.openPreferences());
    }

    // The list of recently used LCD images; clicking one shows it.
    _rebuildRecent() {
        this._recentSection.removeAll();
        const recent = this._settings.get_strv('lcd-recent-images').slice(0, RECENT_IMAGES_SHOWN);
        if (recent.length === 0)
            return;

        const current = this._settings.get_string('lcd-image');
        this._recentSection.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('LCD image'));
        for (const path of recent) {
            const thumbnail = new Gio.FileIcon({file: Gio.File.new_for_path(path)});
            const item = new PopupMenu.PopupImageMenuItem(GLib.path_get_basename(path), thumbnail);
            if (path === current)
                item.setOrnament(PopupMenu.Ornament.CHECK);
            item.connect('activate', () => this._settings.set_string('lcd-image', path));
            this._recentSection.addMenuItem(item);
        }
    }
});

const OmenIndicator = GObject.registerClass(
class OmenIndicator extends SystemIndicator {
    _init(extension) {
        super._init();
        this.quickSettingsItems.push(new OmenToggle(extension));
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
