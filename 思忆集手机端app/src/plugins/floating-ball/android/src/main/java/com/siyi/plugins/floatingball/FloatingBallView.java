package com.siyi.plugins.floatingball;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.os.Build;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.util.DisplayMetrics;
import android.util.TypedValue;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;

import java.util.HashMap;
import java.util.Map;

/**
 * 悬浮球 View — Canvas 渲染、呼吸动画、触控拖拽、情绪系统。
 *
 * 渲染层级（从下到上）：
 *   1. 情绪光晕（径向渐变，半透明）
 *   2. 主球体（径向渐变蓝）
 *   3. 描边
 *   4. 中央字符 "思"
 *   5. 情绪指示器（右下角小圆点）
 *   6. 角标（右上角，可选）
 */
public class FloatingBallView extends View {

    // ── 尺寸常量（dp，在 init 中转 px） ──
    public static int BALL_SIZE_PX;
    private static final int BALL_SIZE_DP = 56;
    private static final int GLOW_EXTRA_DP = 20;  // 光晕比球大出的部分
    private static final int MOOD_DOT_SIZE_DP = 8;
    private static final int CHAR_SIZE_SP = 18;
    private static final int BADGE_SIZE_DP = 18;
    private static final int BADGE_TEXT_SIZE_SP = 10;
    private static final float BORDER_WIDTH_DP = 1.5f;

    // ── 情绪颜色 ──
    private static final Map<String, int[]> MOOD_COLORS = new HashMap<>();
    static {
        MOOD_COLORS.put("idle",    new int[]{0xFF22C55E, 0x3322C55E}); // 绿
        MOOD_COLORS.put("thinking",new int[]{0xFFF59E0B, 0x33F59E0B}); // 琥珀
        MOOD_COLORS.put("happy",   new int[]{0xFF3B82F6, 0x333B82F6}); // 蓝
        MOOD_COLORS.put("alert",   new int[]{0xFFEF4444, 0x33EF4444}); // 红
    }

    // ── 球体渐变颜色 ──
    private static final int BALL_CENTER_COLOR = 0xFFD6E8FF;
    private static final int BALL_EDGE_COLOR   = 0xFFA0C4F0;
    private static final int BALL_BORDER_COLOR = 0x667AA6DE;
    private static final int CHAR_COLOR        = 0xCC3A6A9E;

    // ── 触控常量 ──
    private static final int TAP_THRESHOLD_MS = 300;
    private static final int LONG_PRESS_MS = 600;
    private static final int DOUBLE_TAP_MS = 350;

    // ── 状态 ──
    private String mood = "idle";
    private int badgeCount = 0;

    // ── 绘制工具 ──
    private final Paint ballPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint glowPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint borderPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint charPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint moodDotPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint badgeBgPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint badgeTextPaint = new Paint(Paint.ANTI_ALIAS_FLAG);

    // ── 动画 ──
    private ValueAnimator breatheAnim;
    private float breatheScale = 1f;

    // ── 触控 ──
    private float touchStartX, touchStartY;
    private long touchStartTime;
    private boolean isDragging = false;
    private static final int TAP_THRESHOLD_DP = 8;
    private int tapThresholdPx;
    private long lastTapTime = 0;
    private long lastDragMoveTime = 0;
    private static final long DRAG_THROTTLE_MS = 16; // ~60fps
    private boolean isSnapping = false;

    // ── 窗口管理 ──
    private WindowManager windowManager;
    private WindowManager.LayoutParams params;
    private int screenWidth;
    private int screenHeight;

    // ── 事件回调 ──
    public interface EventListener {
        void onTap();
        void onDoubleTap();
        void onLongPress();
        void onDragEnd(int x, int y, String edge);
    }
    private EventListener eventListener;

    public void setOnEventListener(EventListener l) { this.eventListener = l; }
    public void setWindowManager(WindowManager wm) { this.windowManager = wm; }

    // ── 构造 ──

    public FloatingBallView(Context context) {
        super(context);
        init(context);
    }

    private void init(Context context) {
        DisplayMetrics metrics = context.getResources().getDisplayMetrics();

        // 尺寸
        BALL_SIZE_PX = dpToPx(BALL_SIZE_DP, metrics);
        int glowExtraPx = dpToPx(GLOW_EXTRA_DP, metrics);
        tapThresholdPx = dpToPx(TAP_THRESHOLD_DP, metrics);
        int borderPx = dpToPx(BORDER_WIDTH_DP, metrics);
        int dotSizePx = dpToPx(MOOD_DOT_SIZE_DP, metrics);
        int badgeSizePx = dpToPx(BADGE_SIZE_DP, metrics);
        int badgeTextSizePx = spToPx(BADGE_TEXT_SIZE_SP, metrics);
        int charSizePx = spToPx(CHAR_SIZE_SP, metrics);

        // 屏幕尺寸
        screenWidth = metrics.widthPixels;
        screenHeight = metrics.heightPixels;

        // ── 画笔 ──
        // 球体渐变（延后到 onSizeChanged 设置准确的 cx/cy）
        ballPaint.setStyle(Paint.Style.FILL);

        // 光晕
        glowPaint.setStyle(Paint.Style.FILL);

        // 描边
        borderPaint.setStyle(Paint.Style.STROKE);
        borderPaint.setStrokeWidth(borderPx);
        borderPaint.setColor(BALL_BORDER_COLOR);

        // 字符
        charPaint.setColor(CHAR_COLOR);
        charPaint.setTextSize(charSizePx);
        charPaint.setTypeface(Typeface.create("Microsoft YaHei UI", Typeface.BOLD));
        charPaint.setTextAlign(Paint.Align.CENTER);

        // 情绪指示器
        moodDotPaint.setStyle(Paint.Style.FILL);

        // 角标
        badgeBgPaint.setStyle(Paint.Style.FILL);
        badgeBgPaint.setColor(0xFFEF4444);
        badgeTextPaint.setColor(Color.WHITE);
        badgeTextPaint.setTextSize(badgeTextSizePx);
        badgeTextPaint.setTypeface(Typeface.DEFAULT_BOLD);
        badgeTextPaint.setTextAlign(Paint.Align.CENTER);

        // 计算总 View 大小（球 + 光晕）
        int totalSize = BALL_SIZE_PX + glowExtraPx * 2;
        setMeasuredDimension(totalSize, totalSize);

        // 呼吸动画
        breatheAnim = ValueAnimator.ofFloat(0f, 1f);
        breatheAnim.setDuration(2400);
        breatheAnim.setRepeatCount(ValueAnimator.INFINITE);
        breatheAnim.setRepeatMode(ValueAnimator.RESTART);
        breatheAnim.addUpdateListener(anim -> {
            float phase = (float) anim.getAnimatedValue();
            // sin 映射到 1.0 ~ 1.05
            breatheScale = 1f + (float) (Math.sin(phase * Math.PI * 2) * 0.025f);
            invalidate();
        });
        breatheAnim.start();

        setLayerType(LAYER_TYPE_HARDWARE, null);
    }

    // ── 公开 API（由 Service 通过 Command 调用） ──

    public void setMood(String newMood) {
        if (!MOOD_COLORS.containsKey(newMood)) newMood = "idle";
        this.mood = newMood;
        invalidate();
    }

    public void setBadge(int count) {
        this.badgeCount = Math.max(0, count);
        invalidate();
    }

    // ── 测量 ──

    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        int glowExtraPx = dpToPx(GLOW_EXTRA_DP, getResources().getDisplayMetrics());
        int totalSize = BALL_SIZE_PX + glowExtraPx * 2;
        setMeasuredDimension(totalSize, totalSize);
    }

    // ── 绘制 ──

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);

        int w = getWidth();
        int h = getHeight();
        float cx = w / 2f;
        float cy = h / 2f;
        float ballRadius = BALL_SIZE_PX / 2f * breatheScale;
        int glowExtraPx = dpToPx(GLOW_EXTRA_DP, getResources().getDisplayMetrics());

        // ── 1. 情绪光晕 ──
        int[] moodColors = MOOD_COLORS.get(mood);
        if (moodColors != null) {
            float glowRadius = ballRadius + glowExtraPx;
            int glowColor = moodColors[1]; // 半透明版本
            RadialGradient glowGradient = new RadialGradient(
                cx, cy, glowRadius,
                new int[]{glowColor, Color.TRANSPARENT},
                new float[]{0.3f, 1f},
                Shader.TileMode.CLAMP
            );
            glowPaint.setShader(glowGradient);
            canvas.drawCircle(cx, cy, glowRadius, glowPaint);
        }

        // ── 2. 主球体 ──
        RadialGradient ballGradient = new RadialGradient(
            cx - ballRadius * 0.3f, cy - ballRadius * 0.3f, ballRadius,
            new int[]{BALL_CENTER_COLOR, BALL_EDGE_COLOR},
            new float[]{0f, 1f},
            Shader.TileMode.CLAMP
        );
        ballPaint.setShader(ballGradient);
        canvas.drawCircle(cx, cy, ballRadius, ballPaint);

        // ── 3. 描边 ──
        float borderRadius = ballRadius - borderPaint.getStrokeWidth() / 2f;
        canvas.drawCircle(cx, cy, borderRadius, borderPaint);

        // ── 4. 中央字符 ──
        float charY = cy - ((charPaint.descent() + charPaint.ascent()) / 2f);
        canvas.drawText("思", cx, charY, charPaint);

        // ── 5. 情绪指示器（右下角） ──
        if (moodColors != null) {
            int dotSizePx = dpToPx(MOOD_DOT_SIZE_DP, getResources().getDisplayMetrics());
            float dotR = dotSizePx / 2f;
            float dotCx = cx + ballRadius * 0.7f;
            float dotCy = cy + ballRadius * 0.7f;
            moodDotPaint.setColor(moodColors[0]);
            canvas.drawCircle(dotCx, dotCy, dotR, moodDotPaint);
            // 小白边
            moodDotPaint.setColor(0xFFFFFFFF);
            canvas.drawCircle(dotCx, dotCy, dotR - 1, moodDotPaint);
            moodDotPaint.setColor(moodColors[0]);
            canvas.drawCircle(dotCx, dotCy, dotR - 2.5f, moodDotPaint);
        }

        // ── 6. 角标 ──
        if (badgeCount > 0) {
            int badgeSizePx = dpToPx(BADGE_SIZE_DP, getResources().getDisplayMetrics());
            float badgeCx = cx + ballRadius * 0.75f;
            float badgeCy = cy - ballRadius * 0.75f;
            String text = badgeCount > 99 ? "99+" : String.valueOf(badgeCount);

            float textW = badgeTextPaint.measureText(text);
            float bgW = Math.max(badgeSizePx, textW + dpToPx(6, getResources().getDisplayMetrics()));
            float bgH = badgeSizePx;

            RectF bgRect = new RectF(
                badgeCx - bgW / 2f,
                badgeCy - bgH / 2f,
                badgeCx + bgW / 2f,
                badgeCy + bgH / 2f
            );
            canvas.drawRoundRect(bgRect, bgH / 2f, bgH / 2f, badgeBgPaint);

            float textY = badgeCy - (badgeTextPaint.descent() + badgeTextPaint.ascent()) / 2f;
            canvas.drawText(text, badgeCx, textY, badgeTextPaint);
        }
    }

    // ── 触控处理 ──

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        if (windowManager == null || params == null || isSnapping) return false;
        // isSnapping 阻止动画期间的触摸事件，避免触发 ROM 悬浮窗保护

        float x = event.getRawX();
        float y = event.getRawY();

        switch (event.getAction()) {
            case MotionEvent.ACTION_DOWN: {
                touchStartX = x;
                touchStartY = y;
                touchStartTime = System.currentTimeMillis();
                isDragging = false;
                return true;
            }
            case MotionEvent.ACTION_MOVE: {
                float dx = x - touchStartX;
                float dy = y - touchStartY;
                float dist = (float) Math.sqrt(dx * dx + dy * dy);

                if (dist > tapThresholdPx) {
                    isDragging = true;

                    long now = System.currentTimeMillis();
                    if (now - lastDragMoveTime < DRAG_THROTTLE_MS) {
                        // 节流：不超过 60fps，减少 updateViewLayout 频率以避开 ROM 检测
                        touchStartX = x;
                        touchStartY = y;
                        return true;
                    }
                    lastDragMoveTime = now;

                    // 更新窗口位置
                    params.x += (int) (x - touchStartX);
                    params.y += (int) (y - touchStartY);
                    // 边界限制
                    params.x = Math.max(0, Math.min(params.x, screenWidth - getWidth()));
                    params.y = Math.max(0, Math.min(params.y, screenHeight - getHeight()));
                    windowManager.updateViewLayout(this, params);

                    touchStartX = x;
                    touchStartY = y;
                }
                return true;
            }
            case MotionEvent.ACTION_UP: {
                long elapsed = System.currentTimeMillis() - touchStartTime;

                if (!isDragging && elapsed < TAP_THRESHOLD_MS) {
                    // 检测双击
                    long now = System.currentTimeMillis();
                    if (now - lastTapTime < DOUBLE_TAP_MS) {
                        lastTapTime = 0;
                        if (eventListener != null) eventListener.onDoubleTap();
                    } else {
                        lastTapTime = now;
                        postDelayed(() -> {
                            if (lastTapTime != 0) {
                                lastTapTime = 0;
                                if (eventListener != null) eventListener.onTap();
                            }
                        }, DOUBLE_TAP_MS);
                    }
                    vibrate(30);
                } else if (!isDragging && elapsed >= LONG_PRESS_MS) {
                    // 长按
                    vibrate(50);
                    if (eventListener != null) eventListener.onLongPress();
                }

                // 释放 → 吸附边缘
                if (isDragging) {
                    isDragging = false;
                    snapToEdge();
                }
                return true;
            }
        }
        return false;
    }
    // ── 工具 ──

    private void snapToEdge() {
        if (windowManager == null || params == null) return;

        int viewW = getWidth();
        int currentX = params.x;
        int midX = currentX + viewW / 2;
        boolean snapLeft = midX < screenWidth / 2;
        int targetX = snapLeft ? 0 : screenWidth - viewW;

        String edge = snapLeft ? "left" : "right";
        if (eventListener != null) {
            eventListener.onDragEnd(targetX, params.y, edge);
        }

        // 动画吸附 — 动画期间屏蔽触摸以防触发 ROM 保护
        isSnapping = true;
        ValueAnimator snapAnim = ValueAnimator.ofInt(currentX, targetX);
        snapAnim.setDuration(250);
        snapAnim.addUpdateListener(anim -> {
            params.x = (int) anim.getAnimatedValue();
            params.y = Math.max(0, Math.min(params.y, screenHeight - getHeight()));
            if (windowManager != null && isAttachedToWindow()) {
                windowManager.updateViewLayout(this, params);
            }
        });
        snapAnim.addListener(new android.animation.AnimatorListenerAdapter() {
            @Override
            public void onAnimationEnd(android.animation.Animator animation) {
                isSnapping = false;
            }
        });
        snapAnim.start();
    }

    // ── 工具 ──

    private void vibrate(int ms) {
        Vibrator v = (Vibrator) getContext().getSystemService(Context.VIBRATOR_SERVICE);
        if (v != null && v.hasVibrator()) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE));
            } else {
                v.vibrate(ms);
            }
        }
    }

    private int dpToPx(float dp, DisplayMetrics metrics) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, dp, metrics);
    }

    private int spToPx(float sp, DisplayMetrics metrics) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, sp, metrics);
    }
}
