package com.siyi.notepad;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;
import com.siyi.notepad.plugin.SiyiNativePlugin;
import com.siyi.plugins.floatingball.FloatingBallPlugin;

public class MainActivity extends BridgeActivity {
  @Override
  public void onCreate(Bundle savedInstanceState) {
    // 插件必须在 super.onCreate 之前注册，否则 Capacitor 初始化时找不到它们，
    // 前端 registerPlugin('SiyiNative' | 'FloatingBall') 会静默拿到 undefined，
    // 表现为「调用无反应、不报错」——悬浮球此前就是这个症状。
    registerPlugin(SiyiNativePlugin.class);
    registerPlugin(FloatingBallPlugin.class);
    super.onCreate(savedInstanceState);
  }
}
