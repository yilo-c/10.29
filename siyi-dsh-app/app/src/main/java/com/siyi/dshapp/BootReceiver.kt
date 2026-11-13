package com.siyi.dshapp

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

/**
 * 开机 / 覆盖安装后预拉引擎服务，让 Agent 在用户进 App 前就已经常驻。
 *
 * 覆盖安装（MY_PACKAGE_REPLACED）后 Android 会清掉旧进程，引擎随之消失，
 * 下次打开 App 必然冷启动（几十秒读条）。这里在装完那一刻就重新拉起引擎，
 * 用户点开图标时直接接管，0 等待。
 *
 * 用户可关自启动，那时收不到 BOOT_COMPLETED，属预期行为。
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val a = intent.action
        if (a != Intent.ACTION_BOOT_COMPLETED &&
            a != "android.intent.action.QUICKBOOT_POWERON" &&
            a != Intent.ACTION_MY_PACKAGE_REPLACED) return
        runCatching {
            val i = Intent(ctx, EngineService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                ctx.startForegroundService(i)
            } else {
                ctx.startService(i)
            }
        }
    }
}
