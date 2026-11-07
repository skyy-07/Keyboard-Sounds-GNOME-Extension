import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

// exit code emitted by mechkeysd.py when /dev/input is unreadable
const PERM_EXIT_CODE = 12;

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

const Indicator = GObject.registerClass(
class MechKeysIndicator extends PanelMenu.Button {
    _init(ext, settings) {
        super._init(0.0, _('MechKeys'), true);

        this._ext = ext;
        this._settings = settings;

        this._icon = new St.Icon({
            icon_name: 'input-keyboard-symbolic',
            style_class: 'system-status-icon',
        });
        this.add_child(this._icon);

        this._switch = new PopupMenu.PopupSwitchMenuItem(_('Keyboard sounds'), true);
        this._switchId = this._switch.connect('toggled', (_o, state) => {
            state ? ext.startDaemon() : ext.stopDaemon();
        });
        this.menu.addMenuItem(this._switch);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const test = new PopupMenu.PopupMenuItem(_('Test click'));
        test.connect('activate', () => ext.playTest());
        this.menu.addMenuItem(test);

        const settingsItem = new PopupMenu.PopupMenuItem(_('Settings…'));
        settingsItem.connect('activate', () => ext.openPreferences());
        this.menu.addMenuItem(settingsItem);
    }

    setRunning(running) {
        this._switch?.setToggleState(running);
        if (this._icon)
            this._icon.opacity = running ? 255 : 130;
    }

    destroy() {
        if (this._switchId && this._switch) {
            this._switch.disconnect(this._switchId);
            this._switchId = null;
        }
        this._switch?.destroy();
        this._switch = null;
        this._icon?.destroy();
        this._icon = null;
        this._ext = null;
        this._settings = null;
        super.destroy();
    }
});

export default class MechKeysExtension extends Extension {
    enable() {
        this._settings = this.getSettings('org.gnome.shell.extensions.mechkeys');
        this._daemon = null;
        this._daemonWaitId = null;
        this._indicator = new Indicator(this, this._settings);
        Main.panel.addToStatusArea('mechkeys', this._indicator);
        this.startDaemon();
    }

    disable() {
        this.stopDaemon();
        this._indicator?.destroy();
        this._indicator = null;
        this._settings = null;
    }

    startDaemon() {
        if (this._daemon && !this._daemon.get_if_exited())
            return;
        const daemonBin = GLib.find_program_in_path('python3') ?? 'python3';
        const argv = [
            daemonBin, '-u',
            GLib.build_filenamev([this.path, 'mechkeysd.py']),
            '--sounds-dir', GLib.build_filenamev([this.path, 'sounds']),
            '--schemadir', GLib.build_filenamev([this.path, 'schemas']),
            '--schema', 'org.gnome.shell.extensions.mechkeys',
        ];
        try {
            this._daemon = new Gio.Subprocess({
                argv,
                flags: Gio.SubprocessFlags.NONE,
            });
            this._daemon.init(null);
            this._daemonWaitId = this._daemon.connect('wait', (_sub, _result) => {
                this._daemonWaitId = null;
                const status = this._daemon ? this._daemon.get_exit_status() : 0;
                if (status === PERM_EXIT_CODE) {
                    Main.notify(_('MechKeys needs keyboard access'),
                        _('Run “sudo usermod -aG input $USER” in a terminal, then log out and back in.'));
                }
                this._indicator?.setRunning(false);
            });
            this._indicator?.setRunning(true);
        } catch (exc) {
            this._daemon = null;
            this._daemonWaitId = null;
            Main.notify(_('MechKeys could not start'), String(exc));
        }
    }

    stopDaemon() {
        const daemon = this._daemon;
        this._daemon = null;
        if (daemon) {
            if (this._daemonWaitId) {
                daemon.disconnect(this._daemonWaitId);
                this._daemonWaitId = null;
            }
            if (!daemon.get_if_exited()) {
                daemon.send_signal(GLib.ProcessSignal.TERM);
            }
        }
        this._indicator?.setRunning(false);
    }

    playTest() {
        try {
            const volume = this._settings.get_double('volume');
            const path = getTestSoundPath(this.path, this._settings);
            const pw = GLib.find_program_in_path('pw-play') ??
                GLib.find_program_in_path('paplay');
            if (!pw)
                return;
            const proc = new Gio.Subprocess({
                argv: [pw, '--volume', volume.toFixed(2), path],
                flags: Gio.SubprocessFlags.NONE,
            });
            proc.init(null);
        } catch (exc) {
            console.warn(`MechKeys test click failed: ${exc}`);
        }
    }
}
