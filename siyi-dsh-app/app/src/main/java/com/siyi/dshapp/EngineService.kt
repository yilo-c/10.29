package com.siyi.dshapp

import android.app.*
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import java.io.BufferedReader
import java.io.File

/**
 * 前台服务：解压引擎与 profile，启动 dsh web（--profile web --port 3080），
 * 从 stdout 抓取带 token 的 URL 存入 WebState。
 */
class EngineService : Service() {

    companion object {
        const val CHANNEL_ID = "siyi_engine"
        const val NOTIF_ID = 1001
        const val WEB_PORT = 3080
    }

    private var process: Process? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(CHANNEL_ID, "引擎", NotificationManager.IMPORTANCE_LOW)
            getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIF_ID, notif("准备引擎…"))
        if (process == null) Thread { boot() }.start()
        return START_STICKY
    }

    private fun notif(text: String): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("思忆")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true)
            .build()

    private fun update(text: String) {
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, notif(text))
    }

    private fun boot() {
        val err = EngineInstaller.ensureAll(this)
        if (err != null) { update(err); return }

        val nodeBin = File(applicationInfo.nativeLibraryDir, "libdshnode.so")
        val engine = EnginePaths.engineRoot(this)
        val binJs = File(engine, "node_modules/@deepseek-ai/dsh/lib/bin.js")
        val home = EnginePaths.dshHome(this)

        if (!nodeBin.exists()) { update("缺少 libdshnode.so"); return }
        if (!binJs.exists()) { update("引擎缺失: ${binJs.path}"); return }

        update("启动 web 服务…")
        val cmd = listOf(
            nodeBin.absolutePath, "--expose-internals", binJs.absolutePath,
            "--profile", "web", "--port", WEB_PORT.toString(), "--no-open"
        )
        val pb = ProcessBuilder(cmd)
        pb.directory(engine)
        pb.environment()["DSH_HOME"] = home.absolutePath
        pb.environment()["DSH_NATIVE_DIR"] = applicationInfo.nativeLibraryDir
        pb.environment()["TZ"] = "Asia/Shanghai"
        // Android 的 os.homedir() 返回 /data（App 无权访问），
        // 目录选择器会因此报 EACCES。改用共享存储作为 HOME。
        val userHome = File("/sdcard/思忆集")
        userHome.mkdirs()
        pb.environment()["HOME"] = userHome.absolutePath
        pb.environment()["USERPROFILE"] = userHome.absolutePath

        runCatching {
            val p = pb.start()
            process = p
            // 读 stdout 抓 token
            val logFile = File(EnginePaths.dshHome(this), "engine-stdout.log")
            Thread {
                runCatching {
                    p.inputStream.bufferedReader().use { br: BufferedReader ->
                        br.forEachLine { line ->
                            runCatching { logFile.appendText(line + "\n") }
                            val m = Regex("token=([A-Za-z0-9_-]+)").find(line)
                            if (m != null) {
                                WebState.setUrl("http://127.0.0.1:$WEB_PORT/?token=${m.groupValues[1]}")
                                update("就绪，可打开界面")
                            }
                        }
                    }
                }
            }.start()
            // 读 stderr 判断失败
            Thread {
                val errLog = File(EnginePaths.dshHome(this), "engine-stderr.log")
                runCatching {
                    p.errorStream.bufferedReader().use { br ->
                        br.forEachLine { line ->
                            runCatching { errLog.appendText(line + "\n") }
                            if (line.contains("startup failed", true) || line.contains("Error:", true)) {
                                WebState.setError(line.take(200))
                                update("启动异常: ${line.take(60)}")
                            }
                        }
                    }
                }
            }.start()
        }.onFailure { update("启动失败: ${it.message}") }
    }

    override fun onDestroy() {
        runCatching { process?.destroy() }
        process = null
        WebState.clear()
        super.onDestroy()
    }
}

/** 引擎就绪后共享给 UI 的地址 */
object WebState {
    @Volatile private var url: String? = null
    @Volatile private var error: String? = null
    fun setUrl(u: String) { url = u; error = null }
    fun setError(e: String) { error = e }
    fun getUrl(): String? = url
    fun getError(): String? = error
    fun isReady(): Boolean = url != null
    fun clear() { url = null; error = null }
}
