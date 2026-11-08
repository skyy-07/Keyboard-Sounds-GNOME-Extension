import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const DBUS_NAME = 'io.github.skyy07.MechKeys';
const DBUS_PATH = '/io/github/skyy07/MechKeys';

const MechKeysInterface = `
<node>
  <interface name="io.github.skyy07.MechKeys">
    <method name="GetStatus">
      <arg type="b" name="running" direction="out"/>
      <arg type="s" name="profile" direction="out"/>
      <arg type="d" name="volume" direction="out"/>
    </method>
    <method name="SetRunning">
      <arg type="b" name="running" direction="in"/>
    </method>
    <method name="PlayTest">
    </method>
    <method name="Reload">
    </method>
    <method name="ImportPack">
      <arg type="s" name="path" direction="in"/>
      <arg type="s" name="name" direction="out"/>
    </method>
    <method name="ListPacks">
      <arg type="s" name="json_packs" direction="out"/>
    </method>
    <method name="Quit">
    </method>
    <signal name="StatusChanged">
      <arg type="b" name="running"/>
    </signal>
  </interface>
</node>`;

const MechKeysProxy = Gio.DBusProxy.makeProxyWrapper(MechKeysInterface);

function getTestSoundPath(extDir, settings) {
    if (!settings)
        return GLib.build_filenamev([extDir, 'sounds', 'thock', 'press-1.wav']);

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
    _init(ext) {
        super._init(0.0, _('MechKeys'), true);

        this._ext = ext;

        this._icon = new St.Icon({
            icon_name: 'input-keyboard-symbolic',
            style_class: 'system-status-icon',
        });
        this.add_child(this._icon);

        this._switch = new PopupMenu.PopupSwitchMenuItem(_('Keyboard sounds'), false);
        this._switchId = this._switch.connect('toggled', (_o, state) => {
            ext.setRunning(state);
        });
        this.menu.addMenuItem(this._switch);

        this._statusItem = new PopupMenu.PopupMenuItem(_('Checking daemon…'));
        this._statusItem.reactive = false;
        this.menu.addMenuItem(this._statusItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const test = new PopupMenu.PopupMenuItem(_('Test click'));
        this._testItem = test;
        this._testId = test.connect('activate', () => ext.playTest());
        this.menu.addMenuItem(test);

        const settingsItem = new PopupMenu.PopupMenuItem(_('Settings…'));
        this._settingsItem = settingsItem;
        this._settingsId = settingsItem.connect('activate', () => ext.openPreferences());
        this.menu.addMenuItem(settingsItem);

        this.setConnected(false, false);
    }

    setConnected(connected, running) {
        if (!connected) {
            this._switch?.setToggleState(false);
            if (this._switch)
                this._switch.reactive = false;
            if (this._statusItem)
                this._statusItem.label.text = _('Daemon not running');
            if (this._icon)
                this._icon.opacity = 130;
        } else {
            if (this._switch) {
                this._switch.reactive = true;
                this._switch.setToggleState(running);
            }
            if (this._statusItem)
                this._statusItem.label.text = running ? _('Daemon active') : _('Daemon paused');
            if (this._icon)
                this._icon.opacity = running ? 255 : 160;
        }
    }

    destroy() {
        if (this._switchId && this._switch) {
            this._switch.disconnect(this._switchId);
            this._switchId = null;
        }
        this._switch?.destroy();
        this._switch = null;

        if (this._testId && this._testItem) {
            this._testItem.disconnect(this._testId);
            this._testId = null;
        }
        this._testItem?.destroy();
        this._testItem = null;

        if (this._settingsId && this._settingsItem) {
            this._settingsItem.disconnect(this._settingsId);
            this._settingsId = null;
        }
        this._settingsItem?.destroy();
        this._settingsItem = null;

        this._statusItem?.destroy();
        this._statusItem = null;

        this._icon?.destroy();
        this._icon = null;
        this._ext = null;
        super.destroy();
    }
});

export default class MechKeysExtension extends Extension {
    enable() {
        this._settings = this.getSettings('org.gnome.shell.extensions.mechkeys');
        this._indicator = new Indicator(this);
        Main.panel.addToStatusArea('mechkeys', this._indicator);

        this._proxy = null;
        this._ownerNotifyId = null;
        this._signalId = null;
        this._settingsChangedId = null;

        this._connectDBus();

        this._settingsChangedId = this._settings.connect('changed', () => {
            this._onSettingsChanged();
        });
    }

    disable() {
        if (this._settingsChangedId && this._settings) {
            this._settings.disconnect(this._settingsChangedId);
            this._settingsChangedId = null;
        }

        if (this._proxy) {
            if (this._ownerNotifyId) {
                this._proxy.disconnect(this._ownerNotifyId);
                this._ownerNotifyId = null;
            }
            if (this._signalId) {
                this._proxy.disconnectSignal(this._signalId);
                this._signalId = null;
            }
            this._proxy = null;
        }

        this._indicator?.destroy();
        this._indicator = null;
        this._settings = null;
    }

    _connectDBus() {
        try {
            this._proxy = new MechKeysProxy(
                Gio.DBus.session,
                DBUS_NAME,
                DBUS_PATH,
                (proxy, error) => {
                    if (error) {
                        console.warn(`MechKeys D-Bus connection failed: ${error}`);
                        this._indicator?.setConnected(false, false);
                        return;
                    }
                    this._ownerNotifyId = proxy.connect('notify::g-name-owner', () => {
                        this._syncDaemonState();
                    });
                    this._signalId = proxy.connectSignal('StatusChanged', (_proxy, _sender, [running]) => {
                        this._indicator?.setConnected(true, running);
                    });
                    this._syncDaemonState();
                }
            );
        } catch (e) {
            console.warn(`MechKeys proxy init error: ${e}`);
            this._indicator?.setConnected(false, false);
        }
    }

    _syncDaemonState() {
        if (!this._proxy || !this._proxy.g_name_owner) {
            this._indicator?.setConnected(false, false);
            return;
        }
        this._proxy.GetStatusRemote((result, error) => {
            if (error) {
                this._indicator?.setConnected(false, false);
            } else {
                const [running] = result;
                this._indicator?.setConnected(true, running);
            }
        });
    }

    setRunning(state) {
        if (this._proxy && this._proxy.g_name_owner) {
            this._proxy.SetRunningRemote(state, (_result, error) => {
                if (error)
                    console.warn(`MechKeys SetRunning error: ${error}`);
            });
        } else {
            this._indicator?.setConnected(false, false);
            Main.notify(_('MechKeys daemon is not running'),
                _('Run "mechkeysd" or install with: pip install mechkeys'));
        }
    }

    playTest() {
        if (this._proxy && this._proxy.g_name_owner) {
            this._proxy.PlayTestRemote((_result, error) => {
                if (error)
                    this._playLocalTest();
            });
        } else {
            this._playLocalTest();
        }
    }

    _onSettingsChanged() {
        if (this._proxy && this._proxy.g_name_owner) {
            this._proxy.ReloadRemote((_result, _error) => {});
        }
    }

    _playLocalTest() {
        try {
            const volume = this._settings?.get_double('volume') ?? 0.6;
            const path = getTestSoundPath(this.path, this._settings);
            const pw = GLib.find_program_in_path('pw-play') ??
                GLib.find_program_in_path('paplay');
            if (!pw || !path)
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
