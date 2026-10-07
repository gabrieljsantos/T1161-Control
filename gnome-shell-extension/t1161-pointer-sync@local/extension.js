import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const POSITION_PATH = '/run/t1161-control/pointer-position';

export default class T1161PointerSync extends Extension {
    enable() {
        this._seat = Clutter.get_default_backend().get_default_seat();
        this._last = '';
        this._file = Gio.File.new_for_path(POSITION_PATH);
        this._monitor = this._file.monitor_file(Gio.FileMonitorFlags.NONE, null);
        this._changedId = this._monitor.connect('changed', () => {
            this._syncPointer();
        });
        this._syncPointer();
    }

    disable() {
        if (this._monitor) {
            if (this._changedId)
                this._monitor.disconnect(this._changedId);
            this._monitor.cancel();
        }
        this._changedId = 0;
        this._monitor = null;
        this._file = null;
        this._seat = null;
        this._last = '';
    }

    _syncPointer() {
        try {
            const [ok, bytes] = GLib.file_get_contents(POSITION_PATH);
            if (!ok)
                return;
            const state = new TextDecoder().decode(bytes).trim();
            if (!state || state === this._last)
                return;
            const [rawX, rawY, present] = state.split(/\s+/).map(Number);
            if (present !== 1 || !Number.isFinite(rawX) || !Number.isFinite(rawY))
                return;
            this._last = state;
            const x = Math.round(rawX / 4095 * Math.max(0, global.screen_width - 1));
            const y = Math.round(rawY / 4095 * Math.max(0, global.screen_height - 1));
            this._seat.warp_pointer(x, y);
        } catch (error) {
            if (!error.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND))
                console.error(`[T1161 Pointer Sync] ${error.message ?? error}`);
        }
    }
}
