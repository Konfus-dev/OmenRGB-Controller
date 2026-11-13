// SPDX-License-Identifier: GPL-3.0-or-later
//
// The OMEN Lights settings window (opened from "Settings…" in the system
// menu, or from the Extensions app).
//
// Like extension.js, this only reads and writes settings; the background
// service does the work and reports back through the "status" setting.

import Adw from 'gi://Adw';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Gdk from 'gi://Gdk';
import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const MAX_RECENT_IMAGES = 8; // how many are remembered
const RECENT_IMAGES_SHOWN = 6; // how many thumbnails fit in the window
const THUMBNAIL_SIZE = 64;
const DEFAULT_COLOUR = '#FFFFFF';
const DEFAULT_MODE = 'Static';
const FIT_VALUES = ['fill', 'fit'];
const FIT_LABELS = ['Fill the screen (crop edges)', 'Show the whole image (black bars)'];

// --- small helpers ------------------------------------------------------------

/** `list` with `path` moved to the front, no duplicates, at most 8 entries. */
export function pushRecent(list, path) {
    return [path, ...list.filter(p => p !== path)].slice(0, MAX_RECENT_IMAGES);
}

/** What the service last reported, or {} if it has never run. */
function readStatus(settings) {
    try {
        return JSON.parse(settings.get_string('status'));
    } catch {
        return {};
    }
}

function rgbaToHex(rgba) {
    const part = v => Math.round(v * 255).toString(16).padStart(2, '0');
    return `#${part(rgba.red)}${part(rgba.green)}${part(rgba.blue)}`.toUpperCase();
}

function hexToRgba(hex) {
    const rgba = new Gdk.RGBA();
    if (!rgba.parse(hex))
        rgba.parse(DEFAULT_COLOUR);
    return rgba;
}

/** Read one entry, or write several, of a "dictionary of strings" setting. */
function getEntry(settings, key, name, fallback) {
    return settings.get_value(key).deepUnpack()[name] ?? fallback;
}

function setEntries(settings, key, entries) {
    const all = settings.get_value(key).deepUnpack();
    if (Object.entries(entries).every(([name, value]) => all[name] === value))
        return; // nothing new: avoid a pointless write
    settings.set_value(key, new GLib.Variant('a{ss}', {...all, ...entries}));
}

/** A row with a 0-100 slider tied to an integer setting. */
function sliderRow(settings, key, title) {
    const scale = new Gtk.Scale({
        adjustment: new Gtk.Adjustment({lower: 0, upper: 100, step_increment: 1, page_increment: 10}),
        draw_value: true,
        digits: 0,
        value_pos: Gtk.PositionType.RIGHT,
        hexpand: true,
        width_request: 220,
        valign: Gtk.Align.CENTER,
    });
    settings.bind(key, scale.adjustment, 'value', Gio.SettingsBindFlags.DEFAULT);
    const row = new Adw.ActionRow({title});
    row.add_suffix(scale);
    return row;
}

/**
 * A row with a 0-100 slider for one device, stored in a "device name ->
 * number" setting (speed, dimmest, brightest).
 */
function deviceSliderRow(settings, key, deviceName, title, subtitle, fallback) {
    const saved = settings.get_value(key).deepUnpack()[deviceName] ?? fallback;
    const scale = new Gtk.Scale({
        adjustment: new Gtk.Adjustment({
            lower: 0, upper: 100, step_increment: 1, page_increment: 10, value: saved,
        }),
        draw_value: true,
        digits: 0,
        value_pos: Gtk.PositionType.RIGHT,
        hexpand: true,
        width_request: 220,
        valign: Gtk.Align.CENTER,
    });
    scale.connect('value-changed', () => {
        const all = settings.get_value(key).deepUnpack();
        const value = Math.round(scale.get_value());
        if (all[deviceName] === value)
            return;
        settings.set_value(key, new GLib.Variant('a{si}', {...all, [deviceName]: value}));
    });
    const row = new Adw.ActionRow({title, subtitle});
    row.add_suffix(scale);
    return row;
}

/** A group that shows one warning line, hidden while there is nothing to say. */
function warningGroup() {
    const group = new Adw.PreferencesGroup({visible: false});
    const row = new Adw.ActionRow({icon_name: 'dialog-warning-symbolic'});
    row.add_css_class('warning');
    group.add(row);
    group.show = message => {
        row.title = message ?? '';
        group.visible = Boolean(message);
    };
    return group;
}

// --- LCD page -------------------------------------------------------------------

function buildLcdPage(settings, window) {
    const page = new Adw.PreferencesPage({title: 'LCD', icon_name: 'video-display-symbolic'});

    const warning = warningGroup();
    page.add(warning);

    const group = new Adw.PreferencesGroup({
        title: 'Cooler LCD',
        description: 'The round screen on the CPU cooler.',
    });
    page.add(group);

    const enabledRow = new Adw.SwitchRow({title: 'Screen on', subtitle: 'Off shows a black screen'});
    settings.bind('lcd-enabled', enabledRow, 'active', Gio.SettingsBindFlags.DEFAULT);
    group.add(enabledRow);

    // Preview of the chosen image.
    const preview = new Gtk.Picture({
        width_request: 240,
        height_request: 240,
        can_shrink: true,
        halign: Gtk.Align.CENTER,
        margin_top: 12,
        margin_bottom: 12,
        overflow: Gtk.Overflow.HIDDEN,
    });
    preview.add_css_class('omen-lcd-preview'); // round, like the real screen
    const previewRow = new Adw.PreferencesRow({activatable: false});
    previewRow.set_child(preview);
    group.add(previewRow);

    // Image chooser.
    const imageRow = new Adw.ActionRow({title: 'Image'});
    const chooseButton = new Gtk.Button({label: 'Choose image…', valign: Gtk.Align.CENTER});
    const clearButton = new Gtk.Button({
        icon_name: 'edit-clear-symbolic',
        tooltip_text: 'Go back to the OMEN logo',
        valign: Gtk.Align.CENTER,
    });
    clearButton.add_css_class('flat');
    imageRow.add_suffix(clearButton);
    imageRow.add_suffix(chooseButton);
    group.add(imageRow);

    chooseButton.connect('clicked', () => {
        const filter = new Gtk.FileFilter({name: 'Images'});
        filter.add_pixbuf_formats();
        const filters = new Gio.ListStore({item_type: Gtk.FileFilter});
        filters.append(filter);
        const dialog = new Gtk.FileDialog({title: 'Choose an image for the LCD', filters});
        dialog.open(window, null, (_dialog, result) => {
            let file;
            try {
                file = dialog.open_finish(result);
            } catch {
                return; // the user pressed Cancel
            }
            const path = file.get_path();
            if (!path)
                return;
            settings.set_strv('lcd-recent-images',
                pushRecent(settings.get_strv('lcd-recent-images'), path));
            settings.set_string('lcd-image', path);
        });
    });
    clearButton.connect('clicked', () => settings.set_string('lcd-image', ''));

    // Recently used images: click one to show it again.
    const recentBox = new Gtk.Box({
        spacing: 8,
        halign: Gtk.Align.CENTER,
        margin_top: 8,
        margin_bottom: 8,
    });
    const recentRow = new Adw.PreferencesRow({activatable: false});
    recentRow.set_child(recentBox);
    group.add(recentRow);

    const rebuildRecent = () => {
        let child;
        while ((child = recentBox.get_first_child()))
            recentBox.remove(child);
        const recent = settings.get_strv('lcd-recent-images').slice(0, RECENT_IMAGES_SHOWN);
        recentRow.visible = recent.length > 1;
        for (const path of recent) {
            const thumbnail = new Gtk.Picture({
                width_request: THUMBNAIL_SIZE,
                height_request: THUMBNAIL_SIZE,
                can_shrink: true,
                content_fit: Gtk.ContentFit.COVER,
            });
            thumbnail.set_filename(path);
            const button = new Gtk.Button({
                child: thumbnail,
                tooltip_text: GLib.path_get_basename(path),
                overflow: Gtk.Overflow.HIDDEN,
            });
            button.add_css_class('flat');
            button.connect('clicked', () => settings.set_string('lcd-image', path));
            recentBox.append(button);
        }
    };

    // Fill or fit.
    const fitRow = new Adw.ComboRow({title: 'Fit', model: Gtk.StringList.new(FIT_LABELS)});
    fitRow.selected = Math.max(0, FIT_VALUES.indexOf(settings.get_string('lcd-fit')));
    fitRow.connect('notify::selected', () => {
        const value = FIT_VALUES[fitRow.selected];
        if (value !== settings.get_string('lcd-fit'))
            settings.set_string('lcd-fit', value);
    });
    group.add(fitRow);

    group.add(sliderRow(settings, 'lcd-brightness', 'Brightness'));

    // Keep the preview, labels and warning in step with the settings.
    const refresh = () => {
        const path = settings.get_string('lcd-image');
        const fit = settings.get_string('lcd-fit');
        preview.set_filename(path || null);
        preview.content_fit = fit === 'fit' ? Gtk.ContentFit.CONTAIN : Gtk.ContentFit.COVER;
        imageRow.subtitle = path ? GLib.path_get_basename(path) : 'None (OMEN logo)';
        clearButton.sensitive = Boolean(path);
        fitRow.selected = Math.max(0, FIT_VALUES.indexOf(fit));

        const status = readStatus(settings);
        if (status.lcd === undefined)
            warning.show('The OMEN Lights service is not running.');
        else if (status.error)
            warning.show(status.error);
        else if (!status.lcd)
            warning.show('Cooler LCD not found.');
        else
            warning.show(null);
    };
    const ids = ['lcd-image', 'lcd-fit', 'status'].map(
        key => settings.connect(`changed::${key}`, refresh));
    ids.push(settings.connect('changed::lcd-recent-images', rebuildRecent));
    rebuildRecent();
    window.connect('close-request', () => {
        ids.forEach(id => settings.disconnect(id));
        return false;
    });
    refresh();

    return page;
}

// --- Lights page ----------------------------------------------------------------

/**
 * One group per lighting device: an effect chooser, an "all" colour and a
 * colour per zone. `device` comes from the service's status report:
 *   name, zones            real names, used as keys when saving
 *   label, zone_labels     what to show
 *   modes, default_mode    the effects on offer
 *   colourless_modes       effects that ignore colours (their rows are hidden)
 *   speed_modes            effects that move (they get a speed slider)
 *   range_modes            effects that pulse (they get dimmest/brightest sliders)
 */
function buildDeviceGroup(settings, device) {
    const group = new Adw.PreferencesGroup({title: device.label ?? device.name});
    const zoneLabels = device.zone_labels ?? device.zones;
    const colourless = device.colourless_modes ?? [];
    const zoneKey = zone => `${device.name}/${zone}`;

    // Effect.
    const modeRow = new Adw.ComboRow({title: 'Effect', model: Gtk.StringList.new(device.modes)});
    const savedMode = getEntry(settings, 'device-modes', device.name,
        device.default_mode || DEFAULT_MODE);
    modeRow.selected = Math.max(0, device.modes.indexOf(savedMode));
    group.add(modeRow);

    // Extra sliders that only appear for effects that use them.
    const speedRow = deviceSliderRow(settings, 'device-speeds', device.name,
        'Speed', 'How fast the effect moves', 50);
    const brightestRow = deviceSliderRow(settings, 'device-max-brightness', device.name,
        'Brightest', 'The top of each pulse', 100);
    const dimmestRow = deviceSliderRow(settings, 'device-min-brightness', device.name,
        'Dimmest', 'The bottom of each pulse', 0);
    [speedRow, brightestRow, dimmestRow].forEach(row => group.add(row));
    const speedModes = device.speed_modes ?? [];
    const rangeModes = device.range_modes ?? [];

    // One colour for everything, for the common case.
    const colourRows = [];
    const zoneButtons = [];
    const colourButton = title => new Gtk.ColorDialogButton({
        dialog: new Gtk.ColorDialog({with_alpha: false, title}),
        valign: Gtk.Align.CENTER,
    });

    if (device.zones.length > 1) {
        const allButton = colourButton('All');
        allButton.rgba = hexToRgba(
            getEntry(settings, 'zone-colours', zoneKey(device.zones[0]), DEFAULT_COLOUR));
        allButton.connect('notify::rgba', () => {
            // Save every zone in one go, then update the buttons to match.
            const hex = rgbaToHex(allButton.rgba);
            setEntries(settings, 'zone-colours',
                Object.fromEntries(device.zones.map(zone => [zoneKey(zone), hex])));
            zoneButtons.forEach(button => {
                if (rgbaToHex(button.rgba) !== hex)
                    button.rgba = allButton.rgba;
            });
        });
        const allRow = new Adw.ActionRow({title: 'All', subtitle: 'Set every colour below at once'});
        allRow.add_suffix(allButton);
        group.add(allRow);
        colourRows.push(allRow);
    }

    // A colour per zone.
    device.zones.forEach((zone, index) => {
        const button = colourButton(zoneLabels[index] ?? zone);
        button.rgba = hexToRgba(getEntry(settings, 'zone-colours', zoneKey(zone), DEFAULT_COLOUR));
        button.connect('notify::rgba', () =>
            setEntries(settings, 'zone-colours', {[zoneKey(zone)]: rgbaToHex(button.rgba)}));
        const row = new Adw.ActionRow({title: zoneLabels[index] ?? zone});
        row.add_suffix(button);
        group.add(row);
        colourRows.push(row);
        zoneButtons.push(button);
    });

    // Show only the rows the chosen effect actually uses.
    const showRowsFor = mode => {
        speedRow.visible = speedModes.includes(mode);
        brightestRow.visible = dimmestRow.visible = rangeModes.includes(mode);
        colourRows.forEach(row => (row.visible = !colourless.includes(mode)));
    };
    modeRow.connect('notify::selected', () => {
        const mode = device.modes[modeRow.selected];
        setEntries(settings, 'device-modes', {[device.name]: mode});
        showRowsFor(mode);
    });
    showRowsFor(device.modes[modeRow.selected]);

    return group;
}

function buildLightsPage(settings, window) {
    const page = new Adw.PreferencesPage({title: 'Lights', icon_name: 'keyboard-brightness-symbolic'});

    const warning = warningGroup();
    page.add(warning);

    const group = new Adw.PreferencesGroup({title: 'All lights'});
    page.add(group);

    const enabledRow = new Adw.SwitchRow({title: 'Lights on'});
    settings.bind('lights-enabled', enabledRow, 'active', Gio.SettingsBindFlags.DEFAULT);
    group.add(enabledRow);
    group.add(sliderRow(settings, 'lights-brightness', 'Brightness'));

    // Device groups are rebuilt only when the list of devices really changes.
    let deviceGroups = [];
    let builtFor = null;
    const refresh = () => {
        const status = readStatus(settings);
        if (status.lcd === undefined)
            warning.show('The OMEN Lights service is not running.');
        else if (!status.openrgb)
            warning.show('OpenRGB is not running, so the lights cannot be changed.');
        else
            warning.show(null);

        const devices = status.devices ?? [];
        const signature = JSON.stringify(devices);
        if (signature === builtFor)
            return;
        builtFor = signature;
        deviceGroups.forEach(g => page.remove(g));
        deviceGroups = devices.map(device => buildDeviceGroup(settings, device));
        deviceGroups.forEach(g => page.add(g));
    };
    const id = settings.connect('changed::status', refresh);
    window.connect('close-request', () => {
        settings.disconnect(id);
        return false;
    });
    refresh();

    return page;
}

// --- Auto page ------------------------------------------------------------------

function buildAutoPage(settings, window) {
    const page = new Adw.PreferencesPage({title: 'Auto', icon_name: 'starred-symbolic'});

    const group = new Adw.PreferencesGroup({
        title: 'Auto mode',
        description: 'Match the PC to the app you are using. Your normal settings ' +
            'come back for apps without a recognisable icon, and while this window has focus.',
    });
    page.add(group);

    const enabledRow = new Adw.SwitchRow({title: 'Auto mode'});
    settings.bind('auto-enabled', enabledRow, 'active', Gio.SettingsBindFlags.DEFAULT);
    group.add(enabledRow);

    const lightsRow = new Adw.SwitchRow({
        title: 'Lights follow the app',
        subtitle: 'Uses the main colour of the app\u2019s icon',
    });
    settings.bind('auto-lights', lightsRow, 'active', Gio.SettingsBindFlags.DEFAULT);
    group.add(lightsRow);

    const lcdRow = new Adw.SwitchRow({
        title: 'LCD shows the app',
        subtitle: 'Puts the app\u2019s icon on the cooler screen',
    });
    settings.bind('auto-lcd', lcdRow, 'active', Gio.SettingsBindFlags.DEFAULT);
    group.add(lcdRow);

    // The two options only mean something while auto mode is on.
    for (const row of [lightsRow, lcdRow])
        enabledRow.bind_property('active', row, 'sensitive', GObject.BindingFlags.SYNC_CREATE);

    // What auto mode last picked up, as reported by the service.
    const nowRow = new Adw.ActionRow({title: 'Last app followed'});
    const swatch = new Gtk.ColorDialogButton({
        dialog: new Gtk.ColorDialog({with_alpha: false}),
        valign: Gtk.Align.CENTER,
        sensitive: false, // display only
    });
    nowRow.add_suffix(swatch);
    group.add(nowRow);

    const refresh = () => {
        const auto = readStatus(settings).auto ?? {};
        nowRow.visible = settings.get_boolean('auto-enabled');
        nowRow.subtitle = auto.app ? auto.app.replace(/\.desktop$/, '') : 'None yet';
        swatch.visible = Boolean(auto.colour);
        if (auto.colour)
            swatch.rgba = hexToRgba(auto.colour);
    };
    const ids = ['status', 'auto-enabled'].map(key => settings.connect(`changed::${key}`, refresh));
    window.connect('close-request', () => {
        ids.forEach(id => settings.disconnect(id));
        return false;
    });
    refresh();

    return page;
}

/** Extra styling for this window only. */
function loadStyles() {
    const provider = new Gtk.CssProvider();
    provider.load_from_string('.omen-lcd-preview { border-radius: 9999px; background: black; }');
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION);
}

// --- entry point ----------------------------------------------------------------

export default class OmenLightsPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        loadStyles();
        window.set_search_enabled(false); // two short pages; nothing to search
        window.set_default_size(560, 760);
        window.add(buildLcdPage(settings, window));
        window.add(buildLightsPage(settings, window));
        window.add(buildAutoPage(settings, window));
    }
}
