"use strict";
/**
 * Android dispatch shim (思忆集 patch).
 *
 * The stock entry point eagerly runs loadNativeModule('pty') at import time,
 * which throws on Android because this host's libnode.so is loaded RTLD_LOCAL
 * and the addon cannot resolve NAPI symbols from the running server. Delegate to
 * the helper-process bridge instead; every other platform keeps the original
 * implementation untouched in ./index.native.js.
 */
if (process.platform === "android" && process.env.DSH_PTY_NATIVE !== "1") {
	module.exports = require("./android-pty-bridge.js");
} else {
	module.exports = require("./index.native.js");
}
