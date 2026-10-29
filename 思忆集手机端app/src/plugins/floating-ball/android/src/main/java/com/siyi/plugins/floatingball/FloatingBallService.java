package com.siyi.plugins.floatingball;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;
import android.view.Gravity;
import android.view.WindowManager;
import android.view.WindowMetrics;

import androidx.core.app.NotificationCompat;

import com.getcapacitor.JSObject;

/**
 * 前台服务 — 管理悬浮球生命周期。
 * 负责：通知栏常驻、WindowManager 注册、命令分发。
 */
public class FloatingBallService extends Service {

    static final String TAG = "FloatingBallSvc";

    static final String CHANNEL_ID = "siyi_floating_ball";
    static final int NOTIFICATION_ID = 1001;

    static final String ACTION_SHOW = "show";
    static final String ACTION_HIDE = "hide";

    // ── Command interface for thread-safe updates ──
    public interface BallCommand {
        void execute(FloatingBallView view);
    }

    private static FloatingBallService instance;
    private static FloatingBallView ballView;
    private static EventCallback eventCallback;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private WindowManager windowManager;

    interface EventCallback {
        void onEvent(String type, JSObject payload);
    }

    static void setEventCallback(EventCallback cb) {
        eventCallback = cb;
    }

    /** Post a command to be executed on the main thread against the ball view */
    public static void postCommand(BallCommand cmd) {
        if (instance == null) return;
        instance.mainHandler.post(() -> {
            if (ballView != null) {
                cmd.execute(ballView);
            }
        });
    }

    // ── Lifecycle ──

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) return START_STICKY;

        if (ACTION_SHOW.equals(intent.getAction())) {
            startForeground(NOTIFICATION_ID, buildNotification());
            if (ballView == null) {
                createBallView();
            }
            if (ballView != null) {
                ballView.setVisibility(android.view.View.VISIBLE);
            }
        } else if (ACTION_HIDE.equals(intent.getAction())) {
            destroyBallView();
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
        }

        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        destroyBallView();
        instance = null;
        super.onDestroy();
    }

    // ── Ball view management ──

    private void createBallView() {
        try {
            ballView = new FloatingBallView(this);
            ballView.setMood("idle");

            // Listen for events from the view
            ballView.setOnEventListener(new FloatingBallView.EventListener() {
                @Override
                public void onTap() {
                    if (eventCallback != null) {
                        eventCallback.onEvent("onTap", new JSObject());
                    }
                }

                @Override
                public void onDoubleTap() {
                    if (eventCallback != null) {
                        eventCallback.onEvent("onDoubleTap", new JSObject());
                    }
                }

                @Override
                public void onLongPress() {
                    if (eventCallback != null) {
                        eventCallback.onEvent("onLongPress", new JSObject());
                    }
                }

                @Override
                public void onDragEnd(int x, int y, String edge) {
                    if (eventCallback != null) {
                        JSObject payload = new JSObject();
                        payload.put("x", x);
                        payload.put("y", y);
                        payload.put("edge", edge);
                        eventCallback.onEvent("onDragEnd", payload);
                    }
                }
            });

            int flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                      | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL;

            int LAYOUT_TYPE;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                LAYOUT_TYPE = WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY;
            } else {
                LAYOUT_TYPE = WindowManager.LayoutParams.TYPE_PHONE;
            }

            WindowManager.LayoutParams params = new WindowManager.LayoutParams(
                FloatingBallView.BALL_SIZE_PX,
                FloatingBallView.BALL_SIZE_PX,
                LAYOUT_TYPE,
                flags,
                PixelFormat.TRANSLUCENT
            );

            params.gravity = Gravity.TOP | Gravity.START;
            params.x = getDefaultX();
            params.y = getDefaultY();

            ballView.setLayoutParams(params);
            ballView.setWindowManager(windowManager);
            windowManager.addView(ballView, params);

            Log.d(TAG, "Floating ball created at (" + params.x + "," + params.y + ")");
        } catch (Exception e) {
            Log.e(TAG, "Failed to create floating ball", e);
        }
    }

    private void destroyBallView() {
        if (ballView != null && ballView.isAttachedToWindow()) {
            try {
                windowManager.removeView(ballView);
            } catch (Exception e) {
                Log.w(TAG, "removeView failed", e);
            }
        }
        ballView = null;
    }

    private int getDefaultX() {
        WindowMetrics metrics = getWindowManager().getMaximumWindowMetrics();
        int width = metrics.getBounds().width();
        return width - FloatingBallView.BALL_SIZE_PX - 24;
    }

    private int getDefaultY() {
        WindowMetrics metrics = getWindowManager().getMaximumWindowMetrics();
        int height = metrics.getBounds().height();
        return height / 3;
    }

    private WindowManager getWindowManager() {
        if (windowManager == null) {
            windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
        }
        return windowManager;
    }

    // ── Notification ──

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "思忆集悬浮球",
                NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription("思忆集悬浮球后台运行通知");
            channel.setShowBadge(false);
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(channel);
        }
    }

    private Notification buildNotification() {
        return new NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("思忆集")
            .setContentText("悬浮球运行中")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build();
    }
}
