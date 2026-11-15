'use strict';
/**
 * Android pty helper child process.
 *
 * On Android the app runs a thin `libdshnode.so` wrapper whose 82 MB `libnode.so`
 * engine is loaded RTLD_LOCAL, so NAPI symbols are absent from the global symbol
 * table and every native addon dlopen() fails with
 * "cannot locate symbol napi_fatal_error". Setting LD_PRELOAD=libnode.so puts the
 * engine in the global scope, which makes native addons loadable -- but the
 * already-running server process cannot receive that variable retroactively.
 *
 * Therefore each terminal is backed by this helper: the parent spawns it with
 * LD_PRELOAD set, it allocates the real pty here, and the two processes exchange
 * raw bytes over stdin/stdout plus JSON control lines on fds 3 and 4.
 *
 * Protocol
 *   parent -> child  fd0 : raw stdin bytes for the pty
 *   child  -> parent fd1 : raw pty output
 *   parent -> child  fd3 : JSON lines {t:'resize',cols,rows} / {t:'kill',sig}
 *   child  -> parent fd4 : JSON lines {t:'exit',exitCode,signal}
 *   pid handshake        : JSON {"pid":N} written to the path in the init line
 */

const fs = require('fs');
const path = require('path');
const tty = require('tty');

const PTY_NODE = path.join(
	__dirname, '..', 'prebuilds', `${process.platform}-${process.arch}`, 'pty.node'
);

const debug = (message) => {
	if (process.env.DSH_PTY_DEBUG) {
		try { process.stderr.write(`[pty-helper] ${message}\n`); } catch { /* ignore */ }
	}
};

const sleeper = new Int32Array(new SharedArrayBuffer(4));
const sleepSync = (ms) => Atomics.wait(sleeper, 0, 0, ms);

/** Blocking read of one '\n'-terminated line from a pipe fd. */
function readLineSync(fd) {
	const byte = Buffer.alloc(1);
	const chunks = [];
	for (;;) {
		let n;
		try {
			n = fs.readSync(fd, byte, 0, 1, null);
		} catch (error) {
			if (error.code === 'EAGAIN') { sleepSync(2); continue; }
			throw error;
		}
		if (n === 0) throw new Error('android-pty-child: init stream closed');
		// Decode the whole line at once: per-byte decoding corrupts every multi-byte
		// character, which breaks non-ASCII paths like /storage/emulated/0/思忆集.
		if (byte[0] === 0x0a) return Buffer.concat(chunks).toString('utf8');
		chunks.push(Buffer.from([byte[0]]));
		if (chunks.length > 4 * 1024 * 1024) throw new Error('android-pty-child: init line too large');
	}
}

let handshakePath;

function publishHandshake(payload) {
	if (handshakePath === undefined) return;
	const tmp = `${handshakePath}.tmp`;
	try {
		fs.writeFileSync(tmp, JSON.stringify(payload));
		fs.renameSync(tmp, handshakePath);
	} catch {
		// The parent times out on its own; nothing useful to do here.
	}
}

function sendEvent(payload) {
	try {
		fs.writeSync(4, `${JSON.stringify(payload)}\n`);
	} catch {
		// Parent gone; the pty will be torn down with this process.
	}
}

/** Queue pty writes so a full tty buffer cannot wedge the child. */
function createPtyWriter(fd) {
	let pending = [];
	let scheduled = false;
	const flush = () => {
		while (pending.length > 0) {
			const chunk = pending[0];
			try {
				fs.writeSync(fd, chunk);
			} catch (error) {
				if (error.code === 'EAGAIN') {
					if (!scheduled) {
						scheduled = true;
						setTimeout(() => { scheduled = false; flush(); }, 4);
					}
					return;
				}
				pending = [];
				return;
			}
			pending.shift();
		}
	};
	return (chunk) => {
		if (chunk.length === 0) return;
		pending.push(chunk);
		flush();
	};
}

function main() {
	const init = JSON.parse(readLineSync(3));
	handshakePath = init.handshake;

	const native = { exports: {} };
	process.dlopen(native, PTY_NODE);
	const pty = native.exports;

	const environment = [];
	for (const [key, value] of Object.entries(init.env || {})) {
		if (typeof value === 'string') environment.push(`${key}=${value}`);
	}

	/**
	 * Exit is a two-phase affair. The native callback fires as soon as the shell
	 * is reaped, but the tail of its output is still in flight through the pty
	 * master and this process's stdout pipe. Leaving immediately truncates that
	 * tail, so the exit is reported only after the master read has drained
	 * (EIO/end), and stdout has been flushed.
	 */
	let exitInfo;
	let drained = false;
	let leaving = false;
	let output;

	const maybeLeave = () => {
		if (leaving || exitInfo === undefined) return;
		if (!drained) return;
		leaving = true;
		sendEvent({ t: 'exit', exitCode: exitInfo.exitCode, signal: exitInfo.signal });
		try { if (output) output.pause(); } catch { /* already gone */ }
		try { process.stdin.pause(); process.stdin.destroy(); } catch { /* already gone */ }
		let left = false;
		const leave = () => {
			if (left) return;
			left = true;
			process.exit(0);
		};
		try { process.stdout.write('', leave); } catch { leave(); }
		setTimeout(leave, 300);
	};

	const recordExit = (exitCode, signal) => {
		if (exitInfo !== undefined) return;
		exitInfo = { exitCode, signal };
		debug(`exit observed code=${exitCode} sig=${signal}`);
		// Safety net: a master read that never signals must not strand the helper.
		setTimeout(() => { drained = true; maybeLeave(); }, 1500);
		maybeLeave();
	};

	const terminal = pty.fork(
		init.file,
		init.args || [],
		environment,
		init.cwd,
		init.cols,
		init.rows,
		-1,
		-1,
		true,
		'',
		recordExit
	);

	publishHandshake({ pid: terminal.pid });
	debug(`forked pid=${terminal.pid} fd=${terminal.fd} file=${init.file}`);

	output = new tty.ReadStream(terminal.fd);
	output.on('data', (chunk) => {
		try { process.stdout.write(chunk); } catch { /* parent gone */ }
	});
	output.on('error', () => { drained = true; maybeLeave(); });
	output.on('end', () => { drained = true; maybeLeave(); });

	const writePty = createPtyWriter(terminal.fd);
	process.stdin.on('data', (chunk) => writePty(chunk));
	process.stdin.on('error', () => { drained = true; recordExit(0, null); });
	process.stdin.resume();

	let control = '';
	fs.createReadStream(null, { fd: 3 })
		.on('data', (chunk) => {
			control += chunk.toString('utf8');
			let index;
			while ((index = control.indexOf('\n')) >= 0) {
				const line = control.slice(0, index);
				control = control.slice(index + 1);
				if (line.length === 0) continue;
				let message;
				try { message = JSON.parse(line); } catch { continue; }
				if (message.t === 'resize') {
					try { pty.resize(terminal.fd, message.cols, message.rows); } catch { /* terminal gone */ }
				} else if (message.t === 'kill') {
					try { process.kill(terminal.pid, message.sig || 'SIGHUP'); } catch { /* already gone */ }
				}
			}
		})
		.on('error', () => { drained = true; maybeLeave(); });
}

try {
	main();
} catch (error) {
	publishHandshake({ error: String((error && error.message) || error) });
	try {
		process.stderr.write(`android-pty-child: ${(error && error.stack) || error}\n`);
	} catch { /* ignore */ }
	process.exit(1);
}
