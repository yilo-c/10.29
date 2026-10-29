/**
 * 思忆集 VRM 骨骼系统 — "小妹妹" 猫娘角色
 *
 * 基于解析的 VRM 文件 (406042710567621037.vrm) 的精确骨骼结构：
 * - 195 节点, 54 个人形骨骼
 * - 猫耳、猫尾、头发、裙摆、胸部等次生骨骼
 * - 14 个混合变形 (blend shapes)
 *
 * 此模块定义：
 * 1. VRM 骨骼索引常量
 * 2. 生物力学关节约束
 * 3. 11 个关键姿态参数
 * 4. 姿态插值引擎
 */

// ─── VRM 骨骼索引 ───────────────────────────────────────────────

/** VRM 人形骨骼名称 → 节点索引 */
export const VRM_BONE: Record<string, number> = {
  // 核心躯干 (5)
  hips: 1,
  spine: 2,
  chest: 3,
  upperChest: 4,
  neck: 9,

  // 头部 (3)
  head: 10,
  leftEye: 11,
  rightEye: 12,

  // 左臂 (7)
  leftShoulder: 68,
  leftUpperArm: 69,
  leftLowerArm: 74,
  leftHand: 75,
  leftThumb1: 88,
  leftIndex1: 76,
  leftMiddle1: 82,

  // 右臂 (7)
  rightShoulder: 91,
  rightUpperArm: 92,
  rightLowerArm: 97,
  rightHand: 98,
  rightThumb1: 111,
  rightIndex1: 99,
  rightMiddle1: 105,

  // 左腿 (4)
  leftUpperLeg: 114,
  leftLowerLeg: 127,
  leftFoot: 128,
  leftToes: 129,

  // 右腿 (4)
  rightUpperLeg: 130,
  rightLowerLeg: 143,
  rightFoot: 144,
  rightToes: 145,

  // 猫尾 (8 段)
  tail1: 146,
  tail2: 147,
  tail3: 148,
  tail4: 149,
  tail5: 150,
  tail6: 151,
  tail7: 152,
  tail8: 153,

  // 猫耳 (每耳 2 段)
  leftEar1: 13,
  leftEar2: 14,
  rightEar1: 16,
  rightEar2: 17,
} as const

/** 骨骼索引 → VRM 人形骨骼名称 (反向映射) */
export const BONE_NAME: Record<number, string> = {}
for (const [name, idx] of Object.entries(VRM_BONE)) {
  BONE_NAME[idx] = name
}

// ─── 角色比例 (单位: VRM 坐标系, Y-up) ──────────────────────────

export const CHARACTER_PROPS = {
  /** 角色总高度 (Hips→Head) */
  bodyHeight: 0.78,
  /** Hips 位置 Y */
  hipY: 0.45,
  /** 头部位置 Y */
  headY: 1.23,
  /** 肩宽 */
  shoulderWidth: 0.14,
  /** 上臂长度 */
  upperArmLength: 0.11,
  /** 小臂长度 */
  lowerArmLength: 0.10,
  /** 大腿长度 */
  upperLegLength: 0.17,
  /** 小腿长度 */
  lowerLegLength: 0.20,
  /** 尾巴总长 */
  tailLength: 0.44,
} as const

// ─── 关节约束 (弧度制) ──────────────────────────────────────────
//
// 三级防护线：
// 1. 姿态定义时遵守约束 (本文件 POSE_TARGETS)
// 2. AI 决策时钳制输出 (decisionEngine.ts)
// 3. 每帧渲染前 clamp (poseInterpolator.ts)
//
// 每个关节定义 [min, max] 范围。
// 坐标系统: Three.js Y-up, X=pitch(俯仰), Y=yaw(偏航), Z=roll(翻滚)

export interface JointLimits {
  /** 最小/最大 X 旋转 (俯仰: 前倾/后仰), 弧度 */
  x: [number, number]
  /** 最小/最大 Y 旋转 (偏航: 左转/右转), 弧度 */
  y: [number, number]
  /** 最小/最大 Z 旋转 (翻滚: 侧倾), 弧度 */
  z: [number, number]
}

export const JOINT_CONSTRAINTS: Record<string, JointLimits> = {
  // ── 躯干 ──
  hips: {
    x: [-0.35, 0.45], // 前倾最多 20°, 后仰最多 26°
    y: [-0.26, 0.26], // 左右转 ±15°
    z: [-0.17, 0.17], // 侧倾 ±10°
  },
  spine: {
    x: [-0.17, 0.26], // 前弯 10°, 后弯 15°
    y: [-0.09, 0.09], // ±5°
    z: [-0.12, 0.12], // 侧弯 ±7°
  },
  chest: {
    x: [-0.12, 0.12], // ±7°
    y: [-0.09, 0.09], // ±5°
    z: [-0.07, 0.07], // ±4°
  },
  upperChest: {
    x: [-0.09, 0.09], // ±5°
    y: [-0.05, 0.05], // ±3°
    z: [-0.07, 0.07], // ±4°
  },

  // ── 颈部 & 头部 ──
  neck: {
    x: [-0.52, 0.52], // 点头 ±30°
    y: [-0.79, 0.79], // 转头 ±45°
    z: [-0.44, 0.44], // 歪头 ±25°
  },
  head: {
    x: [-0.26, 0.35], // 俯仰 -15°/+20°
    y: [-0.44, 0.44], // ±25°
    z: [-0.26, 0.26], // ±15°
  },

  // ── 眼睛 (LookAt 骨骼，用于微调) ──
  leftEye: { x: [-0.05, 0.05], y: [-0.05, 0.05], z: [-0.03, 0.03] },
  rightEye: { x: [-0.05, 0.05], y: [-0.05, 0.05], z: [-0.03, 0.03] },

  // ── 肩部 ──
  leftShoulder: {
    x: [-0.17, 0.52], // -10°/+30°
    y: [-0.26, 0.35], // -15°/+20°
    z: [-0.35, 0.35], // ±20°
  },
  rightShoulder: {
    x: [-0.17, 0.52],
    y: [-0.35, 0.26],
    z: [-0.35, 0.35],
  },

  // ── 上臂 ──
  leftUpperArm: {
    x: [-1.57, 2.09], // -90°(后摆)/+120°(前举)
    y: [-0.52, 1.57], // -30°(内收)/+90°(外展)
    z: [-1.05, 1.05], // 扭转 ±60°
  },
  rightUpperArm: {
    x: [-1.57, 2.09],
    y: [-1.57, 0.52],
    z: [-1.05, 1.05],
  },

  // ── 前臂 ──
  leftLowerArm: {
    x: [-2.44, 0.09], // 弯曲 -140°/伸展 +5° (肘不反弯)
    y: [0, 0],
    z: [0, 0],
  },
  rightLowerArm: {
    x: [-2.44, 0.09],
    y: [0, 0],
    z: [0, 0],
  },

  // ── 手部 ──
  leftHand: {
    x: [-0.87, 0.87], // ±50°
    y: [-0.35, 0.35], // ±20°
    z: [-0.44, 0.44], // ±25°
  },
  rightHand: {
    x: [-0.87, 0.87],
    y: [-0.35, 0.35],
    z: [-0.44, 0.44],
  },

  // ── 大腿 ──
  leftUpperLeg: {
    x: [-0.79, 1.57], // 后摆 -45°/前踢 +90°
    y: [-0.35, 0.35], // ±20°
    z: [-0.26, 0.26], // ±15°
  },
  rightUpperLeg: {
    x: [-0.79, 1.57],
    y: [-0.35, 0.35],
    z: [-0.26, 0.26],
  },

  // ── 小腿 ──
  leftLowerLeg: {
    x: [-0.09, 2.44], // 反弯 -5°/弯曲 +140° (膝不反弯)
    y: [0, 0],
    z: [0, 0],
  },
  rightLowerLeg: {
    x: [-0.09, 2.44],
    y: [0, 0],
    z: [0, 0],
  },

  // ── 脚部 ──
  leftFoot: {
    x: [-0.44, 0.61], // -25°/+35°
    y: [-0.17, 0.17], // ±10°
    z: [-0.17, 0.17], // ±10°
  },
  rightFoot: {
    x: [-0.44, 0.61],
    y: [-0.17, 0.17],
    z: [-0.17, 0.17],
  },

  // ── 尾巴根 (整体方向控制) ──
  tail1: {
    x: [-0.52, 1.05], // -30°/+60°
    y: [-0.70, 0.70], // ±40°
    z: [-0.52, 0.52], // ±30°
  },
  tail2: { x: [-0.35, 0.35], y: [-0.26, 0.26], z: [-0.26, 0.26] },
  tail3: { x: [-0.35, 0.35], y: [-0.26, 0.26], z: [-0.26, 0.26] },
  tail4: { x: [-0.35, 0.35], y: [-0.26, 0.26], z: [-0.26, 0.26] },

  // ── 猫耳 ──
  leftEar1: { x: [-0.35, 0.44], y: [-0.26, 0.35], z: [-0.35, 0.35] },
  rightEar1: { x: [-0.35, 0.44], y: [-0.35, 0.26], z: [-0.35, 0.35] },
  leftEar2: { x: [-0.17, 0.17], y: [-0.09, 0.09], z: [-0.17, 0.17] },
  rightEar2: { x: [-0.17, 0.17], y: [-0.09, 0.09], z: [-0.17, 0.17] },
}

// ─── 混合变形 (表达式) ───────────────────────────────────────────

/** VRM blend shape 名称 → 索引 */
export const BLEND_SHAPE: Record<string, number> = {
  neutral: 0,
  happy: 1,
  angry: 2,
  sad: 3,
  relaxed: 4,
  surprised: 5,
  aa: 6,
  ih: 7,
  ou: 8,
  ee: 9,
  oh: 10,
  blink: 11,
  blinkLeft: 12,
  blinkRight: 13,
}

// ─── 姿态定义 ────────────────────────────────────────────────────

/**
 * 姿态参数
 *
 * 每个骨骼的旋转值以 [x, y, z] euler (弧度) 表示，
 * 相对于 VRM T-pose 的偏移量。
 *
 * 坐标约定 (Three.js Euler, 'YXZ' 顺序):
 *   X = pitch  (俯仰: 正值 = 前倾, 负值 = 后仰)
 *   Y = yaw    (偏航: 正值 = 右转, 负值 = 左转)
 *   Z = roll   (翻滚: 正值 = 右倾, 负值 = 左倾)
 */
export interface PoseTarget {
  /** 显示名称 */
  label: string
  /** 图标 */
  icon: string
  /** 语义标签 */
  tags: string[]
  /** 能量水平 0-1 */
  energy: number
  /** 持续时间 ms */
  duration: number
  /** 是否循环 */
  loop: boolean
  /** 骨骼旋转偏移 (boneName → [x, y, z] euler) */
  rotations: Partial<Record<keyof typeof VRM_BONE, [number, number, number]>>
  /** 混合变形权重 (0-1) */
  blendShapes: Partial<Record<string, number>>
}

// ─── 11 个关键姿态 ──────────────────────────────────────────────
//
// 参数基于 VRM 骨骼结构和生物力学约束设计。
// 使用 Pose Tuner (pose-tuner.html) 进行可视化调参。

export const POSE_TARGETS: Record<string, PoseTarget> = {
  // ── 1. 待机 ──
  idle: {
    label: '待机',
    icon: '🧍',
    tags: ['neutral', 'low'],
    energy: 0.05,
    duration: 0,
    loop: true,
    rotations: {
      hips: [0, 0, 0],
      spine: [0.03, 0, 0],
      head: [0.02, 0, 0],
      leftUpperArm: [0.08, -0.05, 0],
      rightUpperArm: [0.08, 0.05, 0],
      tail1: [0.3, 0, 0],
    },
    blendShapes: { neutral: 1 },
  },

  // ── 2. 开心 ──
  happy: {
    label: '开心',
    icon: '😊',
    tags: ['happy', 'mid'],
    energy: 0.35,
    duration: 2000,
    loop: false,
    rotations: {
      head: [0, 0, 0.12],
      leftUpperArm: [-0.3, -0.2, 0],
      rightUpperArm: [-0.3, 0.2, 0],
      leftLowerArm: [-1.2, 0, 0],
      rightLowerArm: [-1.2, 0, 0],
      tail1: [0.7, 0, 0],
      tail2: [0.2, 0.1, 0],
      leftEar1: [0, 0.1, 0],
      rightEar1: [0, -0.1, 0],
    },
    blendShapes: { happy: 1, eyeBlink: 0.3 },
  },

  // ── 3. 悲伤 ──
  sad: {
    label: '悲伤',
    icon: '😢',
    tags: ['sad', 'low'],
    energy: 0.08,
    duration: 2500,
    loop: false,
    rotations: {
      head: [0.15, 0, 0],
      spine: [-0.1, 0, 0],
      leftUpperArm: [0.1, -0.3, 0],
      rightUpperArm: [0.1, 0.3, 0],
      leftEar1: [-0.3, 0, 0],
      rightEar1: [-0.3, 0, 0],
      tail1: [-0.3, 0, 0],
    },
    blendShapes: { sad: 1 },
  },

  // ── 4. 生气 ──
  angry: {
    label: '生气',
    icon: '😤',
    tags: ['angry', 'high'],
    energy: 0.55,
    duration: 2000,
    loop: false,
    rotations: {
      head: [-0.05, 0, 0],
      spine: [0.05, 0, 0],
      leftUpperArm: [-0.5, -0.5, 0.2],
      rightUpperArm: [-0.5, 0.5, -0.2],
      leftLowerArm: [-1.6, 0, 0],
      rightLowerArm: [-1.6, 0, 0],
      leftEar1: [-0.2, 0.15, 0],
      rightEar1: [-0.2, -0.15, 0],
      tail1: [0.5, 0.3, 0],
      tail2: [0.2, -0.2, 0],
    },
    blendShapes: { angry: 1 },
  },

  // ── 5. 惊讶 ──
  surprised: {
    label: '惊讶',
    icon: '😲',
    tags: ['surprise', 'high'],
    energy: 0.6,
    duration: 1200,
    loop: false,
    rotations: {
      head: [-0.15, 0, 0],
      spine: [0.08, 0, 0],
      leftUpperArm: [0.6, -0.1, 0],
      rightUpperArm: [0.6, 0.1, 0],
      leftLowerArm: [-0.8, 0, 0],
      rightLowerArm: [-0.8, 0, 0],
      leftEar1: [0.3, 0, 0],
      rightEar1: [0.3, 0, 0],
      tail1: [0.1, 0, 0],
      tail2: [0, 0, 0],
    },
    blendShapes: { surprised: 1 },
  },

  // ── 6. 好奇 ──
  curious: {
    label: '好奇',
    icon: '🤔',
    tags: ['curious', 'mid'],
    energy: 0.25,
    duration: 2000,
    loop: false,
    rotations: {
      head: [0, 0, 0.22],
      leftUpperArm: [-0.2, -0.3, 0],
      rightUpperArm: [0.4, 0.1, 0],
      rightLowerArm: [-1.4, 0, 0],
      leftEar1: [0, 0.2, 0],
      rightEar1: [0, -0.1, 0],
      tail1: [0.2, 0.1, 0],
      tail2: [0.1, -0.1, 0],
    },
    blendShapes: {},
  },

  // ── 7. 困倦 ──
  sleepy: {
    label: '困倦',
    icon: '😴',
    tags: ['calm', 'low'],
    energy: 0.04,
    duration: 3500,
    loop: false,
    rotations: {
      head: [0.18, 0, 0.05],
      spine: [-0.08, 0, 0],
      leftUpperArm: [-0.8, -0.1, 0],
      rightUpperArm: [-0.8, 0.1, 0],
      leftEar1: [-0.2, 0, 0],
      rightEar1: [-0.2, 0, 0],
      tail1: [-0.15, 0, 0],
    },
    blendShapes: { blink: 0.8, relaxed: 0.5 },
  },

  // ── 8. 兴奋 ──
  excited: {
    label: '兴奋',
    icon: '⭐',
    tags: ['happy', 'high'],
    energy: 0.7,
    duration: 1800,
    loop: false,
    rotations: {
      head: [0, 0, -0.08],
      spine: [0.1, 0, 0],
      leftUpperArm: [1.0, -0.3, 0],
      rightUpperArm: [1.0, 0.3, 0],
      leftLowerArm: [-0.3, 0, 0],
      rightLowerArm: [-0.3, 0, 0],
      leftEar1: [0.35, 0.05, 0],
      rightEar1: [0.35, -0.05, 0],
      tail1: [0.8, 0, 0],
      tail2: [0.25, 0.1, 0],
      tail3: [0.15, 0.05, 0],
    },
    blendShapes: { happy: 0.8, surprised: 0.3 },
  },

  // ── 9. 害羞 ──
  shy: {
    label: '害羞',
    icon: '😳',
    tags: ['nervous', 'mid'],
    energy: 0.2,
    duration: 2200,
    loop: false,
    rotations: {
      head: [0.22, 0, 0],
      spine: [-0.05, 0, 0],
      leftUpperArm: [-0.4, -0.6, 0.3],
      rightUpperArm: [-0.4, 0.6, -0.3],
      leftLowerArm: [-1.5, 0, 0],
      rightLowerArm: [-1.5, 0, 0],
      leftHand: [0.2, 0, 0],
      rightHand: [0.2, 0, 0],
      leftEar1: [-0.25, 0, 0],
      rightEar1: [-0.25, 0, 0],
      tail1: [0.1, 0, 0],
      tail2: [0, 0.2, 0],
    },
    blendShapes: { sad: 0.3, blink: 0.4 },
  },

  // ── 10. 害怕 ──
  scared: {
    label: '害怕',
    icon: '😱',
    tags: ['surprise', 'high'],
    energy: 0.65,
    duration: 1500,
    loop: false,
    rotations: {
      head: [-0.12, 0, 0],
      spine: [0.12, 0, 0],
      hips: [-0.1, 0, 0],
      leftUpperArm: [0.7, -0.5, 0.2],
      rightUpperArm: [0.7, 0.5, -0.2],
      leftLowerArm: [-1.0, 0, 0],
      rightLowerArm: [-1.0, 0, 0],
      leftUpperLeg: [-0.3, 0, 0],
      rightUpperLeg: [-0.3, 0, 0],
      leftEar1: [-0.4, 0.1, 0],
      rightEar1: [-0.4, -0.1, 0],
      tail1: [-0.4, 0, 0],
      tail2: [-0.2, 0, 0],
      tail3: [-0.1, 0, 0],
    },
    blendShapes: { surprised: 0.8, angry: 0.2 },
  },

  // ── 11. 思考 ──
  thinking: {
    label: '思考',
    icon: '💭',
    tags: ['curious', 'low'],
    energy: 0.12,
    duration: 2800,
    loop: false,
    rotations: {
      head: [0, 0, 0.15],
      leftUpperArm: [0.25, -0.6, 0],
      rightUpperArm: [-0.3, 0.1, 0],
      rightLowerArm: [-1.6, 0, 0],
      rightHand: [0, 0, 0.2],
      leftEar1: [0.1, 0.15, 0],
      rightEar1: [0.1, -0.05, 0],
      tail1: [0.1, 0.05, 0],
      tail2: [0, 0.2, 0],
    },
    blendShapes: {},
  },
}

// ─── 辅助函数 ────────────────────────────────────────────────────

/** 将度转换为弧度 */
export function deg(r: number): number {
  return (r * Math.PI) / 180
}

/** 线性插值 */
export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

/** 钳制值到范围 */
export function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v))
}

/** 缓入缓出 (cubic ease) */
export function ease(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 3, 3) / 2
}

/** 对 [x, y, z] euler 三元组进行线性插值 */
export function lerpEuler(
  a: [number, number, number],
  b: [number, number, number],
  t: number
): [number, number, number] {
  return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)]
}

/** 根据约束钳制 euler 旋转 */
export function clampEuler(
  rot: [number, number, number],
  limits: JointLimits
): [number, number, number] {
  return [
    clamp(rot[0], limits.x[0], limits.x[1]),
    clamp(rot[1], limits.y[0], limits.y[1]),
    clamp(rot[2], limits.z[0], limits.z[1]),
  ]
}

/**
 * 在两个姿态之间插值
 *
 * @param from 起始姿态
 * @param to 目标姿态
 * @param t 插值因子 0-1 (会经过 ease 处理)
 * @returns 插值后的姿态参数
 */
export function lerpPose(from: PoseTarget, to: PoseTarget, t: number): PoseTarget {
  const et = ease(clamp(t, 0, 1))

  const allBones = new Set<keyof typeof VRM_BONE>()
  for (const k of Object.keys(from.rotations)) allBones.add(k as keyof typeof VRM_BONE)
  for (const k of Object.keys(to.rotations)) allBones.add(k as keyof typeof VRM_BONE)

  const rotations: PoseTarget['rotations'] = {}
  for (const bone of allBones) {
    const a: [number, number, number] = from.rotations[bone] ?? [0, 0, 0]
    const b: [number, number, number] = to.rotations[bone] ?? [0, 0, 0]
    rotations[bone] = lerpEuler(a, b, et)
  }

  const allBS = new Set<string>()
  for (const k of Object.keys(from.blendShapes)) allBS.add(k)
  for (const k of Object.keys(to.blendShapes)) allBS.add(k)

  const blendShapes: Record<string, number> = {}
  for (const bs of allBS) {
    blendShapes[bs] = lerp(from.blendShapes[bs] ?? 0, to.blendShapes[bs] ?? 0, et)
  }

  return {
    label: to.label,
    icon: to.icon,
    tags: to.tags,
    energy: lerp(from.energy, to.energy, et),
    duration: to.duration,
    loop: to.loop,
    rotations,
    blendShapes,
  }
}

/**
 * 对姿态应用每帧的微动 (idle animation)
 *
 * @param pose 基础姿态
 * @param time 累计时间 ms
 * @returns 应用微动后的姿态
 */
export function applyMicroMotion(pose: PoseTarget, time: number): PoseTarget {
  const t = time * 0.001 // 转换为秒
  const rotations = { ...pose.rotations } as Record<string, [number, number, number]>

  // 呼吸：轻微的身体起伏
  const breathe = Math.sin(t * 2.5) * 0.04
  if (rotations.spine) rotations.spine[0] += breathe

  // 尾巴微摆
  const tailWag = Math.sin(t * 3.0) * 0.15
  if (rotations.tail1) rotations.tail1[1] += tailWag

  // 耳朵微动
  const earFlutter = Math.sin(t * 4.5 + 1) * 0.08
  if (rotations.leftEar1) rotations.leftEar1[0] += earFlutter
  if (rotations.rightEar1) rotations.rightEar1[0] += earFlutter * 0.7

  // 眨眼 (每 4-6 秒一次)
  const blinkCycle = (time % 5000) / 5000
  const blinkWeight = blinkCycle < 0.05 ? Math.sin(blinkCycle * Math.PI * 20) * 0.8 : 0
  const blendShapes = { ...pose.blendShapes }
  if (blinkWeight > 0.01) {
    blendShapes.blink = blinkWeight
  }

  return { ...pose, rotations, blendShapes }
}

/**
 * 获取姿态中所有骨骼，按 VRM 节点索引索引化
 *
 * 用于直接注入 Three.js VRM 骨骼系统。
 *
 * @param pose 姿态
 * @returns Map<nodeIndex, [x, y, z]>
 */
export function poseToBoneMap(pose: PoseTarget): Map<number, [number, number, number]> {
  const map = new Map<number, [number, number, number]>()
  for (const [boneName, rot] of Object.entries(pose.rotations)) {
    const idx = VRM_BONE[boneName]
    if (idx !== undefined) {
      map.set(idx, rot as [number, number, number])
    }
  }
  return map
}

/**
 * 获取姿态中所有混合变形的索引化映射
 */
export function poseToBlendMap(pose: PoseTarget): Map<number, number> {
  const map = new Map<number, number>()
  for (const [name, weight] of Object.entries(pose.blendShapes)) {
    const idx = BLEND_SHAPE[name]
    if (idx !== undefined) {
      map.set(idx, weight as number)
    }
  }
  return map
}

// ─── 控制器骨骼列表 (AI 只控制这些, 其余为 SpringBone/physics) ───

/** AI 主动控制的核心骨骼 */
export const CONTROL_BONES = [
  'hips',
  'spine',
  'chest',
  'upperChest',
  'neck',
  'head',
  'leftShoulder',
  'rightShoulder',
  'leftUpperArm',
  'rightUpperArm',
  'leftLowerArm',
  'rightLowerArm',
  'leftHand',
  'rightHand',
  'leftUpperLeg',
  'rightUpperLeg',
  'leftLowerLeg',
  'rightLowerLeg',
  'leftFoot',
  'rightFoot',
  'tail1',
  'tail2',
  'tail3',
  'leftEar1',
  'rightEar1',
] as const

export type ControlBone = (typeof CONTROL_BONES)[number]
