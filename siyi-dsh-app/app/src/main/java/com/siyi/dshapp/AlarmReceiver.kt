package com.siyi.dshapp

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat

/**
 * 闹钟触发后的接收器（B3 配套）。
 *
 * 为什么用 BroadcastReceiver 而不是 Activity：
 * 闹钟到点时 App 多半在后台，从后台启动 Activity 在 Android 10+ 会被拦截
 * （需要 SYSTEM_ALERT_WINDOW 之类的特殊豁免）。发通知是唯一稳妥的路径，
 * 用户点通知再进 App。
 *
 * 必须是 manifest 里静态注册的 receiver，不能运行时注册 ——
 * 运行时注册的 receiver 会随进程退出而消失，而闹钟恰恰要在进程不在时触发。
 */
class AlarmReceiver : BroadcastReceiver() {

    companion object {
        const val ACTION_FIRE = "com.siyi.dshapp.ALARM_FIRE"
        private const val CHANNEL_ID = "siyi-alarm"
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_FIRE) return
        val title = intent.getStringExtra("title") ?: "思忆集提醒"
        val text = intent.getStringExtra("text") ?: ""
        val code = intent.getIntExtra("requestCode", 1)

        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        // 渠道要幂等创建：每次触发都建一次，系统会自动忽略重复 id
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(CHANNEL_ID, "闹钟提醒", NotificationManager.IMPORTANCE_HIGH)
            ch.description = "思忆集设置的定时提醒"
            ch.enableVibration(true)
            nm.createNotificationChannel(ch)
        }

        // 点击通知回到 App
        val open = Intent(context, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        }
        val pi = PendingIntent.getActivity(
            context, code, open,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val n = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle(title)
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build()

        runCatching { nm.notify(code, n) }
    }
}
