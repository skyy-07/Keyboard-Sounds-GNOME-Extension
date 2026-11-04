import {ExtensionPreferences, gettext as _}
    from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

import Adw from 'gi://Adw';
import Gdk from 'gi://Gdk';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Gtk from 'gi://Gtk';

const EXT_DIR = GLib.path_get_dirname(
    decodeURIComponent(import.meta.url.replace(/^file:\/\//, '')));

const PERMISSION_FIX = 'sudo usermod -aG input $USER';
const AUTOSTART_FILE = 'mechkeys@skyy-07.github.io.desktop';

function shquote(path) {
    return `"${path.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

function writeAutostart(enable) {
    const dir = GLib.build_filenamev([GLib.get_user_config_dir(), 'autostart']);
    const file = GLib.build_filenamev([dir, AUTOSTART_FILE]);
    if (!enable) {
        try {
            GLib.unlink(file);
        } catch {
            // nothing to remove
        }
        return;
    }
    const daemon = GLib.build_filenamev([EXT_DIR, 'mechkeysd.py']);
    const exec = [
        '/usr/bin/python3', '-u',
        shquote(daemon),
        '--sounds-dir', shquote(GLib.build_filenamev([EXT_DIR, 'sounds'])),
        '--schemadir', shquote(GLib.build_filenamev([EXT_DIR, 'schemas'])),
        '--schema', 'org.gnome.shell.extensions.mechkeys',
    ].join(' ');
    const contents = [
        '[Desktop Entry]',
        'Type=Application',
        'Name=MechKeys',
        'Comment=Mechanical keyboard sounds on every keystroke',
        `Exec=${exec}`,
        'X-GNOME-Autostart-enabled=true',
        'NoDisplay=true',
        '',
    ].join('\n');
    GLib.mkdir_with_parents(dir, 0o755);
    GLib.file_set_contents(file, contents);
}

export default class MechKeysPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();

        const page = new Adw.PreferencesPage();
        window.add(page);

        page.add(this._soundGroup(settings));
        page.add(this._feelGroup(settings));
        page.add(this._systemGroup(settings));
        page.add(this._setupGroup(window));
    }

    _soundGroup(settings) {
        const group = new Adw.PreferencesGroup({title: _('Sound')});

        // -- sound profile ------------------------------------------
        const profiles = ['thock', 'clack', 'creamy'];
        const labels = ['Thock (deep & rounded)', 'Clack (bright & snappy)',
            'Creamy (soft & muted)'];
        const list = new Gtk.StringList();
        labels.forEach(l => list.append(l));
        const profile = new Adw.ComboRow({
            title: _('Switch character'),
            subtitle: _('The flavor of the synthesized clicks'),
            model: list,
        });
        group.add(profile);
        let guard = false;
        const applyProfileKey = () => {
            guard = true;
            profile.set_selected(Math.max(0, profiles.indexOf(
                settings.get_string('profile'))));
            guard = false;
        };
        profile.connect('notify::selected', () => {
            if (!guard)
                settings.set_string('profile', profiles[profile.get_selected()]);
        });
        applyProfileKey();
        const profileChanged = () => applyProfileKey();
        settings.connect('changed::profile', profileChanged);

        // -- volume --------------------------------------------------
        group.add(this._bindSpinDouble(settings, 'volume', _('Volume'),
            _('Loudness of every keystroke'), 1, 100, 100));

        // -- toggles -------------------------------------------------
        const variation = new Adw.SwitchRow({
            title: _('Random pitch variation'),
            subtitle: _('Six subtly detuned variants, picked per press'),
        });
        group.add(variation);
        const space = new Adw.SwitchRow({
            title: _('Distinct spacebar sound'),
            subtitle: _('A deeper, longer sample for the spacebar'),
        });
        group.add(space);
        settings.bind('pitch-variation', variation, 'active',
            Gio.SettingsBindFlags.DEFAULT);
        settings.bind('space-sound', space, 'active', Gio.SettingsBindFlags.DEFAULT);
        return group;
    }

    _feelGroup(settings) {
        const group = new Adw.PreferencesGroup({
            title: _('Typing feel'),
            description: _('How strokes behave under heavy typing'),
        });

        const damp = new Adw.SwitchRow({
            title: _('Rollover damping'),
            subtitle: _('Held/autorepeat strokes play quieter, like real keys'),
        });
        group.add(damp);
        settings.bind('repeat-damping', damp, 'active', Gio.SettingsBindFlags.DEFAULT);
        group.add(this._bindSpinDouble(settings, 'repeat-volume',
            _('Repeat volume'), _('Multiplier applied to repeats'), 5, 100, 100));

        const release = new Adw.SwitchRow({
            title: _('Key release sound'),
            subtitle: _('Faint sound when keys go back up'),
        });
        group.add(release);
        settings.bind('release-sound', release, 'active', Gio.SettingsBindFlags.DEFAULT);
        group.add(this._bindSpinDouble(settings, 'release-volume',
            _('Release volume'), _('Multiplier applied to releases'), 0, 100, 100));
        return group;
    }

    _systemGroup(settings) {
        const group = new Adw.PreferencesGroup({title: _('Session')});

        const test = new Adw.ActionRow({
            title: _('Test click'),
            subtitle: _('Play one click with the current profile and volume'),
        });
        const testBtn = new Gtk.Button({label: _('Play sample'), valign: Gtk.Align.CENTER});
        test.add_suffix(testBtn);
        testBtn.connect('clicked', () => {
            const pw = GLib.find_program_in_path('pw-play') ??
                GLib.find_program_in_path('paplay');
            if (!pw)
                return;
            const path = GLib.build_filenamev([EXT_DIR, 'sounds',
                settings.get_string('profile'), 'press-1.wav']);
            const proc = new Gio.Subprocess({
                argv: [pw, '--volume', settings.get_double('volume').toFixed(2), path],
                flags: Gio.SubprocessFlags.NONE,
            });
            try {
                proc.init(null);
            } catch {
                // ignore
            }
        });
        group.add(test);

        const auto = new Adw.SwitchRow({
            title: _('Start automatically on login'),
            subtitle: _('Runs the sound daemon when the session begins'),
        });
        group.add(auto);
        settings.bind('autostart', auto, 'active', Gio.SettingsBindFlags.DEFAULT);
        settings.connect('changed::autostart',
            (_s, key) => writeAutostart(settings.get_boolean(key)));
        return group;
    }

    _setupGroup(window) {
        const group = new Adw.PreferencesGroup({
            title: _('One-time setup'),
            description: _('Key events are private on Wayland; give this user read access to input devices.'),
        });

        const row = new Adw.ActionRow({
            title: _('Enable keyboard access'),
            subtitle: PERMISSION_FIX + ' — then log out and back in',
        });
        const copyBtn = new Gtk.Button({
            icon_name: 'edit-copy-symbolic',
            tooltip_text: _('Copy command'),
            valign: Gtk.Align.CENTER,
        });
        copyBtn.connect('clicked', () => {
            try {
                const display = Gdk.Display.get_default();
                display.get_clipboard().set_text(PERMISSION_FIX);
            } catch {
                // clipboard unavailable; the command is visible in the subtitle
            }
            window.add_toast(new Adw.Toast({title: _('Command copied')}));
        });
        row.add_suffix(copyBtn);
        group.add(row);

        return group;
    }

    _bindSpinDouble(settings, key, title, subtitle, minPct, maxPct, mult) {
        const row = new Adw.SpinRow({
            title,
            subtitle,
            adjustment: new Gtk.Adjustment({
                lower: minPct,
                upper: maxPct,
                step_increment: 1,
                page_increment: 5,
                value: minPct,
            }),
        });
        let guard = false;

        const applyFromKey = () => {
            if (guard)
                return;
            guard = true;
            row.set_value(Math.round(settings.get_double(key) * mult));
            row.set_value(Math.min(maxPct, Math.max(minPct, row.value)));
            guard = false;
        };
        row.connect('changed', () => {
            if (guard)
                return;
            guard = true;
            settings.set_double(key, row.value / mult);
            guard = false;
        });
        settings.connect(`changed::${key}`, applyFromKey);
        applyFromKey();
        return row;
    }
}
