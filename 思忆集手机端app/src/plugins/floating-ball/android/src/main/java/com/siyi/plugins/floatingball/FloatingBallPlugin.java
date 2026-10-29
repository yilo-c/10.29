package com.siyi.plugins.floatingball;

import android.content.Intent;
import android.net.Uri;
import android.provider.Settings;
import android.util.Log;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "FloatingBall")
public class FloatingBallPlugin extends Plugin {

    private static final String TAG = "FloatingBall";
    private static final int REQUEST_OVERLAY_PERMISSION = 1001;

    @PluginMethod
    public void show(PluginCall call) {
        Intent intent = new Intent(getContext(), FloatingBallService.class);
        intent.setAction(FloatingBallService.ACTION_SHOW);
        getContext().startForegroundService(intent);
        FloatingBallService.setEventCallback(this::onBallEvent);
        call.resolve();
    }

    @PluginMethod
    public void hide(PluginCall call) {
        Intent intent = new Intent(getContext(), FloatingBallService.class);
        intent.setAction(FloatingBallService.ACTION_HIDE);
        getContext().startService(intent);
        call.resolve();
    }

    @PluginMethod
    public void setMood(PluginCall call) {
        String mood = call.getString("mood", "idle");
        FloatingBallService.postCommand(cmd -> cmd.setMood(mood));
        call.resolve();
    }

    @PluginMethod
    public void setBadge(PluginCall call) {
        int count = call.getInt("count", 0);
        FloatingBallService.postCommand(cmd -> cmd.setBadge(count));
        call.resolve();
    }

    @PluginMethod
    public void checkPermission(PluginCall call) {
        boolean granted = Settings.canDrawOverlays(getContext());
        JSObject ret = new JSObject();
        ret.put("granted", granted);
        call.resolve(ret);
    }

    @PluginMethod
    public void requestPermission(PluginCall call) {
        if (Settings.canDrawOverlays(getContext())) {
            JSObject ret = new JSObject();
            ret.put("granted", true);
            call.resolve(ret);
            return;
        }
        // Save call for the activity result callback
        saveCall(call);
        Intent intent = new Intent(
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            Uri.parse("package:" + getContext().getPackageName())
        );
        getActivity().startActivityForResult(intent, REQUEST_OVERLAY_PERMISSION);
    }

    @Override
    protected void handleOnActivityResult(int requestCode, int resultCode, Intent data) {
        super.handleOnActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_OVERLAY_PERMISSION) {
            PluginCall saved = getSavedCall();
            if (saved != null) {
                boolean granted = Settings.canDrawOverlays(getContext());
                JSObject ret = new JSObject();
                ret.put("granted", granted);
                saved.resolve(ret);
            }
        }
    }

    // ── Event bridge: service → JS ──

    private void onBallEvent(String type, JSObject payload) {
        Log.d(TAG, "Event: " + type + " " + payload);
        notifyListeners(type, payload);
    }
}
