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
        /** 通知栏「停止引擎」的动作标识。 */
        const val ACTION_STOP = "com.siyi.dshapp.action.STOP_ENGINE"
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
        // 用户点了通知栏的「停止引擎」：这才是主动停止，要真的收掉进程。
        if (intent?.action == ACTION_STOP) {
            EngineController.stop()
            DeviceBridge.stop()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
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
            .addAction(buildStopAction())
            .build()

    private fun update(text: String) {
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, notif(text))
    }

    /**
     * 通知栏的「停止引擎」按钮。
     *
     * 引擎常驻意味着它会一直跑，所以必须给用户一个明确的关闭入口，
     * 否则就是「关不掉的常驻进程」——成熟产品不能这样。
     */
    private fun buildStopAction(): Notification.Action {
        val intent = Intent(this, EngineService::class.java).setAction(ACTION_STOP)
        val pi = android.app.PendingIntent.getService(
            this, 1, intent,
            android.app.PendingIntent.FLAG_UPDATE_CURRENT or
                (if (Build.VERSION.SDK_INT >= 23) android.app.PendingIntent.FLAG_IMMUTABLE else 0)
        )
        return NotificationCompat.Action.Builder(
            android.R.drawable.ic_menu_close_clear_cancel, "停止引擎", pi
        ).build()
    }

    /**
     * 服务销毁时不杀引擎。
     *
     * 引擎是「常驻资源」而不是「Activity 的附属品」：
     * 用户从最近列表划掉 App、Activity 被回收、甚至 App 进程被系统杀，
     * 引擎都应该继续活着 —— 下次打开 App 直接连上去，0 等待。
     *
     * 之前这里会 EngineController.stop()，导致每次打开 App 都要重新起引擎，
     * 白等 4~5 秒。实测 boot→URL 可用 4.1~9.2 秒，这就是那段等待的来源。
     *
     * 引擎真正的退出只有两个入口：
     *   1. 用户在通知栏点「停止引擎」
     *   2. 看门狗发现它自己崩了（那是故障，不是主动停止）
     */
    override fun onDestroy() {
        DeviceBridge.stop()
        super.onDestroy()
    }

    /**
     * 任务从最近列表划掉：同样不杀引擎。
     *
     * 只停掉与 UI 绑定的部分（设备桥的连接），引擎进程留着。
     */
    override fun onTaskRemoved(rootIntent: Intent?) {
        DeviceBridge.stop()
        super.onTaskRemoved(rootIntent)
    }
}
