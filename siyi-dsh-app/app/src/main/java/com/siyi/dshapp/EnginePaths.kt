package com.siyi.dshapp

import android.content.Context
import java.io.File

/**
 * 路径约定：
 *  - 引擎树：filesDir/engine（来自 assets/engine.zip）
 *  - DSH_HOME：filesDir/dsh-home（来自 assets/web-profile.tgz，含 profiles/web）
 *  - native lib：applicationInfo.nativeLibraryDir（APK 内 .so）
 */
object EnginePaths {
    fun engineRoot(ctx: Context): File = File(ctx.filesDir, "engine")
    fun dshHome(ctx: Context): File = File(ctx.filesDir, "dsh-home")
    fun webProfile(ctx: Context): File = File(dshHome(ctx), "profiles/web")
}
