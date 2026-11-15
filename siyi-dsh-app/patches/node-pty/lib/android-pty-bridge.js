'use strict';
/**
 * node-pty drop-in for Android.
 *
 * `node-pty` itself is a native addon, and on this host the Node engine
 * (`libnode.so`) is loaded RTLD_LOCAL, so NAPI symbols never reach the global
 * symbol table and the addon cannot be dlopen()ed from the running server.
 *
 * This module keeps the public node-pty surface but delegates the actual pty to
 * `android-pty-child.js`, spawned with LD_PRELOAD=libnode.so so *that* process
 * can load the addon. DSH reads `terminal.pid` synchronously right after spawn(),
 * so the child publishes the shell pid through a handshake file and spawn blocks
 * on it (a few tens of milliseconds) before returning.
 */

const { spawn: spawnProcess } = require('child_process');
const { EventEmitter } = require('events');
const fs = require('fs');
const os = require('os');
const path = require('path');

const CHILD_ENTRY = path.join(__dirname, 'android-pty-child.js');

const sleeper = new Int32Array(new SharedArrayBuffer(4));
const sleepSync = (ms) => Atomics.wait(sleeper, 0, 0, ms);

/**
 * Locate the Node engine shared object that must be preloaded for the helper to
 * resolve NAPI symbols. The host advertises its native directory, which is the
 * authoritative location for the APK's extracted libraries.
 */
function resolveEngineLibrary() {
	const candidates = [];
	if (process.env.DSH_NATIVE_DIR) {
		candidates.push(path.join(process.env.DSH_NATIVE_DIR, 'libnode.so'));
	}
	try {
		candidates.push(path.join(path.dirname(fs.realpathSync(process.execPath)), 'libnode.so'));
	} catch {
		// execPath may be a bare name; fall through to the remaining candidates.
	}
	candidates.push('/data/data/com.siyi.dshapp/files/bin/libnode.so');
	for (const candidate of candidates) {
		try {
			if (fs.statSync(candidate).isFile()) return candidate;
		} catch {
			// Keep looking.
		}
	}
	throw new Error('node-pty(android): libnode.so not found; cannot preload NAPI symbols');
}

let engineLibrary;
let handshakeCounter = 0;

/** Live helpers, force-stopped when the server exits so no pty outlives it. */
const liveChildren = new Set();
let exitHookInstalled = false;
function trackChild(child) {
	liveChildren.add(child);
	child.once('exit', () => liveChildren.delete(child));
	if (!exitHookInstalled) {
		exitHookInstalled = true;
		process.once('exit', () => {
			for (const tracked of liveChildren) {
				try { tracked.kill('SIGKILL'); } catch { /* already gone */ }
			}
		});
	}
}

class AndroidTerminal extends EventEmitter {
	constructor(file, args, options = {}) {
		super();
		this._pid = -1;
		this._closed = false;

		const library = engineLibrary ?? (engineLibrary = resolveEngineLibrary());
		const handshake = path.join(
			os.tmpdir(),
			`dsh-pty-${process.pid}-${Date.now()}-${++handshakeCounter}.json`
		);

		const environment = { ...process.env, ...(options.env || {}) };
		environment.LD_PRELOAD = library;
		environment.LD_LIBRARY_PATH = path.dirname(library);

		const child = spawnProcess(process.execPath, [CHILD_ENTRY], {
			cwd: '/',
			env: environment,
			stdio: ['pipe', 'pipe', 'inherit', 'pipe', 'pipe']
		});
		this._child = child;
		trackChild(child);

		const control = child.stdio[3];
		const events = child.stdio[4];

		const request = {
			file,
			args: args || [],
			cwd: options.cwd || process.cwd(),
			cols: options.cols || 80,
			rows: options.rows || 24,
			env: options.env || {},
			handshake
		};
		control.write(`${JSON.stringify(request)}\n`);

		try {
			this._pid = this._awaitHandshake(handshake);
		} catch (error) {
			try { fs.unlinkSync(handshake); } catch { /* already gone */ }
			try { child.kill('SIGKILL'); } catch { /* already gone */ }
			throw error;
		}

		child.stdout.setEncoding('utf8');
		child.stdout.on('data', (chunk) => this.emit('data', chunk));
		child.stdin.on('error', () => {});
		child.stdout.on('error', () => {});

		let buffered = '';
		events.on('data', (chunk) => {
			buffered += chunk.toString('utf8');
			let index;
			while ((index = buffered.indexOf('\n')) >= 0) {
				const line = buffered.slice(0, index);
				buffered = buffered.slice(index + 1);
				if (line.length === 0) continue;
				let message;
				try { message = JSON.parse(line); } catch { continue; }
				if (message.t === 'exit') this._emitExit(message.exitCode, message.signal);
			}
		});
		events.on('error', () => {});

		child.on('exit', (code, signal) => this._emitExit(code, signal));
		child.on('error', () => this._emitExit(1, undefined));

		this.once('exit', () => {
			this._closed = true;
			try { fs.unlinkSync(handshake); } catch { /* already gone */ }
		});
	}

	/** Wait for the helper's pid handshake, which `spawn()` callers read synchronously. */
	_awaitHandshake(handshake) {
		const deadline = Date.now() + 10_000;
		for (;;) {
			let payload;
			try {
				payload = JSON.parse(fs.readFileSync(handshake, 'utf8'));
				fs.unlinkSync(handshake);
			} catch {
				payload = undefined;
			}
			if (payload !== undefined) {
				if (payload.error !== undefined) {
					throw new Error(`node-pty(android): ${payload.error}`);
				}
				if (typeof payload.pid === 'number' && payload.pid > 0) return payload.pid;
				throw new Error('node-pty(android): helper returned no pid');
			}
			if (Date.now() > deadline) {
				throw new Error('node-pty(android): timed out waiting for the pty helper');
			}
			sleepSync(2);
		}
	}

	_emitExit(exitCode, signal) {
		if (this._exitEmitted) return;
		this._exitEmitted = true;
		this.emit('exit', { exitCode, signal: signal === undefined ? null : signal });
	}

	get pid() { return this._pid; }

	onData(listener) {
		this.on('data', listener);
		return { dispose: () => { this.off('data', listener); } };
	}

	onExit(listener) {
		this.on('exit', listener);
		return { dispose: () => { this.off('exit', listener); } };
	}

	write(data) {
		if (this._closed || this._exitEmitted) return;
		try { this._child.stdin.write(data); } catch { /* helper gone */ }
	}

	resize(cols, rows) {
		if (this._closed || this._exitEmitted) return;
		try {
			this._child.stdio[3].write(`${JSON.stringify({ t: 'resize', cols, rows })}\n`);
		} catch { /* helper gone */ }
	}

	clear() { /* not part of the node-pty public surface */ }

	pause() { try { this._child.stdout.pause(); } catch { /* ignore */ } }

	resume() { try { this._child.stdout.resume(); } catch { /* ignore */ } }

	kill(signal) {
		if (this._closed || this._exitEmitted) return;
		try {
			this._child.stdio[3].write(`${JSON.stringify({ t: 'kill', sig: signal || 'SIGHUP' })}\n`);
		} catch { /* helper gone */ }
	}
}

function spawn(file, args, options) {
	return new AndroidTerminal(file, args, options);
}

module.exports = {
	spawn,
	fork: spawn,
	createTerminal: spawn,
	open(options = {}) {
		return new AndroidTerminal('/system/bin/sh', [], options);
	},
	native: null
};
