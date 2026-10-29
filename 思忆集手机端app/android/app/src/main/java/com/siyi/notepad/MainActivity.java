package com.siyi.notepad;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;
import com.siyi.notepad.plugin.SiyiNativePlugin;

public class MainActivity extends BridgeActivity {
  @Override
  public void onCreate(Bundle savedInstanceState) {
    registerPlugin(SiyiNativePlugin.class);
    super.onCreate(savedInstanceState);
  }
}
