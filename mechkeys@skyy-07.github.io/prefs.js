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

function getInstalledPacks() {
    const packsDir = GLib.build_filenamev(
        [GLib.get_user_data_dir() || GLib.get_home_dir(), 'mechkeys', 'packs']);
    GLib.mkdir_with_parents(packsDir, 0o755);
    const packs = [];
    try {
        const dir = Gio.File.new_for_path(packsDir);
        const enumerator = dir.enumerate_children(
            'standard::name,standard::type', Gio.FileQueryInfoFlags.NONE, null);
        let info;
        while ((info = enumerator.next_file(null)) !== null) {
            if (info.get_file_type() === Gio.FileType.DIRECTORY) {
                const id = info.get_name();
                if (id.startsWith('.'))
                    continue;

                let name = id;
                const cfgPath = GLib.build_filenamev([packsDir, id, 'config.json']);
                if (GLib.file_test(cfgPath, GLib.FileTest.EXISTS)) {
                    try {
                        const [ok, bytes] = GLib.file_get_contents(cfgPath);
                        if (ok) {
                            const decoder = new TextDecoder('utf-8');
                            const cfg = JSON.parse(decoder.decode(bytes));
                            if (cfg.name)
                                name = `${cfg.name} (${id})`;
                        }
                    } catch {
                        // ignore malformed config
                    }
                }
                packs.push({id, name});
            }
        }
    } catch (e) {
        console.warn(`MechKeys enumerate packs failed: ${e}`);
    }
    return packs;
}

function getTestSoundPath(extDir, settings) {
    const profile = settings.get_string('profile');
    if (profile !== 'pack') {
        const p = GLib.build_filenamev([extDir, 'sounds', profile, 'press-1.wav']);
        if (GLib.file_test(p, GLib.FileTest.EXISTS))
            return p;
    }

    const packName = settings.get_string('pack-name') || '';
    const packsDir = GLib.build_filenamev(
        [GLib.get_user_data_dir() || GLib.get_home_dir(), 'mechkeys', 'packs']);
    const cacheDir = GLib.build_filenamev(
        [GLib.get_user_cache_dir(), 'mechkeys', 'packs']);

    if (packName) {
        const packFolder = GLib.build_filenamev([packsDir, packName]);
        const candidates = ['press-1.wav', 'press.wav', 'click-1.wav', 'space-1.wav', 'space.wav'];
        for (const c of candidates) {
            const p = GLib.build_filenamev([packFolder, c]);
            if (GLib.file_test(p, GLib.FileTest.EXISTS))
                return p;
        }
        try {
            const dir = Gio.File.new_for_path(packFolder);
            const enumerator = dir.enumerate_children(
                'standard::name', Gio.FileQueryInfoFlags.NONE, null);
            let info;
            while ((info = enumerator.next_file(null)) !== null) {
                const n = info.get_name();
                if (n.toLowerCase().endsWith('.wav') && !n.startsWith('concatenated_audio'))
                    return GLib.build_filenamev([packFolder, n]);
            }
        } catch {}
    }

    try {
        const dir = Gio.File.new_for_path(cacheDir);
        const enumerator = dir.enumerate_children(
            'standard::name', Gio.FileQueryInfoFlags.NONE, null);
        let info;
        while ((info = enumerator.next_file(null)) !== null) {
            const sub = GLib.build_filenamev([cacheDir, info.get_name()]);
            for (const cand of ['KeyA-1.wav', 'Space-1.wav', 'Enter-1.wav']) {
                const p = GLib.build_filenamev([sub, cand]);
                if (GLib.file_test(p, GLib.FileTest.EXISTS))
                    return p;
            }
        }
    } catch {}

    return GLib.build_filenamev([extDir, 'sounds', 'thock', 'press-1.wav']);
}

function importPackViaPython(sourcePath, extDir, callback) {
    const daemonBin = GLib.find_program_in_path('python3') ?? 'python3';
    const script = GLib.build_filenamev([extDir, 'mechkeysd.py']);
    try {
        const proc = new Gio.Subprocess({
            argv: [daemonBin, script, '--import-pack', sourcePath],
            flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
        });
        proc.init(null);
        proc.communicate_utf8_async(null, null, (_p, res) => {
            try {
                const [, stdout, stderr] = proc.communicate_utf8_finish(res);
                if (proc.get_successful()) {
                    callback(null, (stdout || '').trim());
                } else {
                    callback(new Error((stderr || '').trim() || _('Import failed')));
                }
            } catch (e) {
                callback(e);
            }
        });
    } catch (e) {
        callback(e);
    }
}

export default class MechKeysPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings('org.gnome.shell.extensions.mechkeys');

        const page = new Adw.PreferencesPage();
        window.add(page);

        page.add(this._soundGroup(settings));
        page.add(this._capsuleGroup(settings, window));
        page.add(this._feelGroup(settings));
        page.add(this._systemGroup(settings));
        page.add(this._setupGroup(window));
    }

    _soundGroup(settings) {
        const group = new Adw.PreferencesGroup({title: _('Sound')});

        // -- sound profile ------------------------------------------
        const profiles = ['thock', 'clack', 'creamy', 'pack'];
        const labels = ['Thock (deep & rounded)', 'Clack (bright & snappy)',
            'Creamy (soft & muted)', 'Custom capsule (user sound pack)'];
        const list = new Gtk.StringList();
        labels.forEach(l => list.append(l));
        const profile = new Adw.ComboRow({
            title: _('Switch character'),
            subtitle: _('The flavor of the synthesized clicks or your custom capsule'),
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

    _capsuleGroup(settings, window) {
        const packsDir = GLib.build_filenamev(
            [GLib.get_user_data_dir() || GLib.get_home_dir(), 'mechkeys', 'packs']);

        const group = new Adw.PreferencesGroup({
            title: _('Sound Capsule'),
            description: _('Use your own keyboard recordings, MechVibes kits, or custom WAV packs'),
        });

        const packCombo = new Adw.ComboRow({
            title: _('Active sound capsule'),
            subtitle: _('Select an installed kit or sound folder'),
        });
        group.add(packCombo);

        let packs = [];
        let guard = false;

        const rebuildModel = () => {
            guard = true;
            packs = getInstalledPacks();
            const newList = new Gtk.StringList();
            if (packs.length === 0) {
                newList.append(_('(No sound capsules installed)'));
                packCombo.set_model(newList);
                packCombo.set_selected(0);
            } else {
                packs.forEach(p => newList.append(p.name));
                packCombo.set_model(newList);
                const curId = settings.get_string('pack-name');
                const idx = packs.findIndex(p => p.id === curId);
                packCombo.set_selected(idx >= 0 ? idx : 0);
            }
            guard = false;
        };

        packCombo.connect('notify::selected', () => {
            if (guard)
                return;
            const sel = packCombo.get_selected();
            if (packs[sel]) {
                guard = true;
                settings.set_string('pack-name', packs[sel].id);
                settings.set_string('profile', 'pack');
                guard = false;
            }
        });

        settings.connect('changed::pack-name', () => {
            if (guard)
                return;
            guard = true;
            const curId = settings.get_string('pack-name');
            const idx = packs.findIndex(p => p.id === curId);
            if (idx >= 0)
                packCombo.set_selected(idx);
            guard = false;
        });

        rebuildModel();

        // -- Open Folder ActionRow ------------------------------------
        const openFolderRow = new Adw.ActionRow({
            title: _('Capsules folder'),
            subtitle: packsDir + _(' — drop sound folders here'),
        });
        const refreshBtn = new Gtk.Button({
            icon_name: 'view-refresh-symbolic',
            tooltip_text: _('Refresh capsules list'),
            valign: Gtk.Align.CENTER,
            css_classes: ['flat'],
        });
        refreshBtn.connect('clicked', () => {
            rebuildModel();
            if (window?.add_toast)
                window.add_toast(new Adw.Toast({title: _('Capsules refreshed')}));
        });
        const openBtn = new Gtk.Button({
            label: _('Open Folder'),
            valign: Gtk.Align.CENTER,
            css_classes: ['flat'],
        });
        openBtn.connect('clicked', () => {
            GLib.mkdir_with_parents(packsDir, 0o755);
            try {
                Gio.AppInfo.launch_default_for_uri(
                    GLib.filename_to_uri(packsDir, null), null);
            } catch (e) {
                console.warn(`Could not open folder: ${e}`);
            }
        });
        openFolderRow.add_suffix(refreshBtn);
        openFolderRow.add_suffix(openBtn);
        group.add(openFolderRow);

        // -- Import ActionRow -----------------------------------------
        const importRow = new Adw.ActionRow({
            title: _('Import sound capsule'),
            subtitle: _('Extract a .zip archive or copy a sound folder'),
        });

        const importZipBtn = new Gtk.Button({
            label: _('Import .zip…'),
            valign: Gtk.Align.CENTER,
            css_classes: ['flat'],
        });
        importZipBtn.connect('clicked', () => {
            const filter = new Gtk.FileFilter();
            filter.set_name(_('Zip Archives (*.zip)'));
            filter.add_pattern('*.zip');

            const dialog = new Gtk.FileDialog({
                title: _('Choose Sound Capsule Archive (.zip)'),
            });
            dialog.set_default_filter(filter);
            dialog.open(window, null, (source, result) => {
                try {
                    const file = source.open_finish(result);
                    const path = file.get_path();
                    if (path) {
                        importPackViaPython(path, EXT_DIR, (err, name) => {
                            if (!err && name) {
                                settings.set_string('pack-name', name);
                                settings.set_string('profile', 'pack');
                                rebuildModel();
                                if (window?.add_toast) {
                                    window.add_toast(new Adw.Toast({
                                        title: _(`Imported capsule: ${name}`),
                                    }));
                                }
                            } else if (err) {
                                if (window?.add_toast) {
                                    window.add_toast(new Adw.Toast({
                                        title: _(`Import failed: ${err.message}`),
                                    }));
                                }
                            }
                        });
                    }
                } catch {
                    // user cancelled
                }
            });
        });

        const importFolderBtn = new Gtk.Button({
            label: _('Import folder…'),
            valign: Gtk.Align.CENTER,
            css_classes: ['flat'],
        });
        importFolderBtn.connect('clicked', () => {
            const dialog = new Gtk.FileDialog({
                title: _('Choose Sound Capsule Folder'),
            });
            dialog.select_folder(window, null, (source, result) => {
                try {
                    const folder = source.select_folder_finish(result);
                    const path = folder.get_path();
                    if (path) {
                        importPackViaPython(path, EXT_DIR, (err, name) => {
                            if (!err && name) {
                                settings.set_string('pack-name', name);
                                settings.set_string('profile', 'pack');
                                rebuildModel();
                                if (window?.add_toast) {
                                    window.add_toast(new Adw.Toast({
                                        title: _(`Imported capsule: ${name}`),
                                    }));
                                }
                            } else if (err) {
                                if (window?.add_toast) {
                                    window.add_toast(new Adw.Toast({
                                        title: _(`Import failed: ${err.message}`),
                                    }));
                                }
                            }
                        });
                    }
                } catch {
                    // user cancelled
                }
            });
        });

        importRow.add_suffix(importZipBtn);
        importRow.add_suffix(importFolderBtn);
        group.add(importRow);

        // -- Manual folder name EntryRow ------------------------------
        group.add(this._bindString(settings, 'pack-name',
            _('Capsule folder name'),
            _('Folder name inside ~/.local/share/mechkeys/packs/'),
            'r1-36ef0a69'));

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
            const path = getTestSoundPath(EXT_DIR, settings);
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

    _bindString(settings, key, title, subtitle, placeholder) {
        const row = new Adw.EntryRow({
            title,
            tooltip_text: subtitle ?? '',
        });
        let guard = false;
        const applyFromKey = () => {
            if (guard)
                return;
            guard = true;
            row.text = settings.get_string(key);
            guard = false;
        };
        row.connect('changed', () => {
            if (guard)
                return;
            guard = true;
            settings.set_string(key, row.text);
            guard = false;
        });
        settings.connect(`changed::${key}`, applyFromKey);
        applyFromKey();
        return row;
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
