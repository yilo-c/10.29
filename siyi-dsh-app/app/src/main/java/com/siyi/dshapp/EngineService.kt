package com.siyi.dshapp

import android.app.*
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat

/**
 * 前台服务：只是 EngineController 的壳。
 *
 * ## 为什么拆出 EngineController
 *
 * 之前的架构把引擎状态劈成了两半（进程在 EngineService 的静态字段、
 * 地址/错误在另一个全局单例 WebState），启动路径也有多条（MainActivity、
 * BootReceiver、START_STICKY 重投），结果 boot() 会跑第二遍，
 * 第二个引擎抢 3180 失败 → web-app 拿不到 host → 白屏。
 *
 * 现在本服务不做任何引擎决策：
 *   - 不持有进程句柄
 *   - 不持有地址/错误
 *   - 不判断要不要启动
 * 全部交给 EngineController.ensureRunning()，它是唯一的启动入口，
 * 重复调用安全（引擎已活着就直接返回）。
 *
 * 因此 onStartCommand 被调用几次都无所谓 —— 这才是治本，不是打补丁。
 */
class EngineService : Service() {

    companion object {
        const val CHANNEL_ID = "siyi_engine"
        const val NOTIF_ID = 1001
    }

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
        // 唯一的启动入口。重复调用安全：引擎已活着就直接返回。
        Thread {
            EngineController.ensureRunning(applicationContext) { text -> update(text) }
        }.start()
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

    override fun onDestroy() {
        DeviceBridge.stop()
        EngineController.stop()
        super.onDestroy()
    }

    /** 任务从最近列表划掉时也要清理，否则引擎残留占着端口。 */
    override fun onTaskRemoved(rootIntent: Intent?) {
        EngineController.stop()
        super.onTaskRemoved(rootIntent)
    }
}
