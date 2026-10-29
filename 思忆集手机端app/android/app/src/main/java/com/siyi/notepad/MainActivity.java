package com.siyi.notepad;

import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;

import com.getcapacitor.BridgeActivity;
import com.siyi.notepad.plugin.SiyiNativePlugin;
import com.siyi.plugins.floatingball.FloatingBallPlugin;

/**
 * 主 Activity。
 *
 * 关键处理：系统导航栏避让。
 *
 * 背景：targetSdk 36（Android 16）起系统强制 edge-to-edge，WebView 会铺到
 * 系统导航栏下方。Capacitor 默认不向 CSS 分发 inset，于是
 * `env(safe-area-inset-bottom)` 在三键导航模式下为 0，
 * App 自己的底部 Tab 栏就画进系统导航键区域 —— Redmi 实测方块键盖住 AI 按钮，
 * 且触控热区与系统导航冲突。
 *
 * 处理策略（双保险）：
 *   1. 关闭 edge-to-edge 布局：给 WebView 容器加系统栏 padding，
 *      让网页内容根本不到导航栏下方（首选，Web 侧无需改版面）
 *   2. 同时把 inset 值通过插件暴露给 JS（SiyiNative.getInsets），
 *      前端 useSafeAreaInsets 会写成 CSS 变量，覆盖手势导航等边界情况
 *
 * 为什么不只用方案 2：仅靠 CSS 变量时，若 JS 启动前有一帧渲染，
 * 底栏会先闪在导航键上；原生层先避让可消除这一帧。
 */
public class MainActivity extends BridgeActivity {
  @Override
  public void onCreate(Bundle savedInstanceState) {
    // 插件必须在 super.onCreate 之前注册，否则 Capacitor 初始化时找不到它们，
    // 前端 registerPlugin('SiyiNative' | 'FloatingBall') 会静默拿到 undefined，
    // 表现为「调用无反应、不报错」——悬浮球此前就是这个症状。
    registerPlugin(SiyiNativePlugin.class);
    registerPlugin(FloatingBallPlugin.class);
    super.onCreate(savedInstanceState);

    applySystemBarInsets();
  }

  /**
   * 让 WebView 容器避开系统栏。
   *
   * 注意：不能对整个 decorView 设 padding，那会把状态栏区域也涂成黑边。
   * 这里只给 WebView 的父容器加 padding，背景仍由主题控制。
   */
  private void applySystemBarInsets() {
    try {
      final View webView = this.getBridge() != null ? this.getBridge().getWebView() : null;
      if (webView == null) return;

      final ViewGroup parent = (webView.getParent() instanceof ViewGroup)
          ? (ViewGroup) webView.getParent()
          : null;
      final View target = parent != null ? parent : webView;

      final int basePadLeft = target.getPaddingLeft();
      final int basePadTop = target.getPaddingTop();
      final int basePadRight = target.getPaddingRight();
      final int basePadBottom = target.getPaddingBottom();

      if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
        target.setOnApplyWindowInsetsListener((v, insets) -> {
          android.graphics.Insets bars = insets.getInsets(
              WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout()
          );
          v.setPadding(
              basePadLeft + bars.left,
              basePadTop + bars.top,
              basePadRight + bars.right,
              basePadBottom + bars.bottom
          );
          return insets;
        });
        target.requestApplyInsets();
      } else {
        target.setOnApplyWindowInsetsListener((v, insets) -> {
          @SuppressWarnings("deprecation")
          int top = insets.getSystemWindowInsetTop();
          @SuppressWarnings("deprecation")
          int bottom = insets.getSystemWindowInsetBottom();
          @SuppressWarnings("deprecation")
          int left = insets.getSystemWindowInsetLeft();
          @SuppressWarnings("deprecation")
          int right = insets.getSystemWindowInsetRight();
          v.setPadding(
              basePadLeft + left,
              basePadTop + top,
              basePadRight + right,
              basePadBottom + bottom
          );
          return insets;
        });
        target.requestApplyInsets();
      }
    } catch (Throwable ignored) {
      // 任何异常都不能影响 App 启动；前端 CSS 变量方案仍会兜底
    }
  }
}
