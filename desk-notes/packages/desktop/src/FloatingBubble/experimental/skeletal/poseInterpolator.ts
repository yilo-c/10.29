/**
 * 姿态插值引擎 — 运行时驱动骨骼动画
 *
 * 使用方式 (每帧):
 *   const blend = getBlendedPose(engine, deltaMs)
 *   const boneMap = poseToBoneMap(blend)
 *   // 应用 boneMap 到 Three.js VRM 骨骼
 *   for (const [nodeIdx, rot] of boneMap) {
 *     const bone = vrmNodes[nodeIdx]
 *     bone.rotation.set(rot[0], rot[1], rot[2])
 *   }
 */

import {
  type PoseTarget,
  POSE_TARGETS,
  lerpPose,
  applyMicroMotion,
  JOINT_CONSTRAINTS,
  clampEuler,
  type ControlBone,
} from './boneSystem'

// ─── 引擎状态 ────────────────────────────────────────────────────

export interface InterpolationState {
  /** 当前姿态 ID */
  currentPoseId: string
  /** 上一姿态 ID */
  previousPoseId: string
  /** 过渡进度 0-1 */
  transitionProgress: number
  /** 过渡速度 (0-1/ms) */
  transitionSpeed: number
  /** 是否正在过渡 */
  transitioning: boolean
  /** 当前帧的混合姿态 (缓存) */
  blendedPose: PoseTarget
  /** 累计时间 ms */
  elapsed: number
}

export function createInterpolationState(initialPose: string = 'idle'): InterpolationState {
  return {
    currentPoseId: initialPose,
    previousPoseId: initialPose,
    transitionProgress: 1,
    transitionSpeed: 0.004, // 默认 250ms 过渡
    transitioning: false,
    blendedPose: POSE_TARGETS[initialPose],
    elapsed: 0,
  }
}

// ─── 公开 API ────────────────────────────────────────────────────

/**
 * 请求切换到新姿态
 *
 * @param state 插值状态
 * @param poseId 目标姿态 ID
 * @param speed 过渡速度 (0-1/ms), 0 表示立即切换
 */
export function requestPose(
  state: InterpolationState,
  poseId: string,
  speed?: number
): void {
  const targetPose = POSE_TARGETS[poseId]
  if (!targetPose) return

  if (speed === 0 || state.currentPoseId === poseId) {
    // 立即切换
    state.previousPoseId = poseId
    state.currentPoseId = poseId
    state.transitionProgress = 1
    state.transitioning = false
    state.blendedPose = targetPose
    return
  }

  state.previousPoseId = state.currentPoseId
  state.currentPoseId = poseId
  state.transitionProgress = 0
  state.transitioning = true
  if (speed !== undefined) {
    state.transitionSpeed = speed
  }
}

/**
 * 每帧调用 — 更新插值并返回混合姿态
 *
 * @param state 插值状态
 * @param deltaMs 帧间隔 ms
 * @returns 当前帧的混合姿态 (可直接用于 poseToBoneMap)
 */
export function updatePose(state: InterpolationState, deltaMs: number): PoseTarget {
  state.elapsed += deltaMs

  // 推进过渡
  if (state.transitioning) {
    state.transitionProgress += state.transitionSpeed * deltaMs
    if (state.transitionProgress >= 1) {
      state.transitionProgress = 1
      state.transitioning = false
    }
  }

  // 获取源和目标姿态
  const from = POSE_TARGETS[state.previousPoseId] ?? POSE_TARGETS.idle
  const to = POSE_TARGETS[state.currentPoseId] ?? POSE_TARGETS.idle

  // 插值
  const basePose = state.transitioning
    ? lerpPose(from, to, state.transitionProgress)
    : to

  // 应用微动
  state.blendedPose = applyMicroMotion(basePose, state.elapsed)

  return state.blendedPose
}

/**
 * 获取当前混合姿态 (不推进时间)
 */
export function getBlendedPose(state: InterpolationState): PoseTarget {
  return state.blendedPose
}

/**
 * 获取当前过渡进度 0-1
 */
export function getTransitionProgress(state: InterpolationState): number {
  return state.transitionProgress
}

/**
 * 是否正在过渡中
 */
export function isTransitioning(state: InterpolationState): boolean {
  return state.transitioning
}

// ─── 直接姿态混合 (高级: 手动多姿态混合) ─────────────────────────

/**
 * 按权重混合多个姿态
 *
 * @param poseWeights Map<poseId, weight> — 所有权重会归一化
 * @returns 混合后的姿态
 */
export function blendPoses(poseWeights: Map<string, number>): PoseTarget {
  let totalWeight = 0
  for (const w of poseWeights.values()) totalWeight += w
  if (totalWeight === 0) return POSE_TARGETS.idle

  // 收集所有涉及的骨骼和混合变形
  const allBones = new Set<string>()
  const allBS = new Set<string>()
  for (const [pid] of poseWeights) {
    const pose = POSE_TARGETS[pid]
    if (!pose) continue
    for (const b of Object.keys(pose.rotations)) allBones.add(b)
    for (const b of Object.keys(pose.blendShapes)) allBS.add(b)
  }

  // 加权平均
  const rotations: Record<string, [number, number, number]> = {}
  for (const bone of allBones) {
    let x = 0,
      y = 0,
      z = 0
    for (const [pid, w] of poseWeights) {
      const pose = POSE_TARGETS[pid]
      if (!pose) continue
      const r = pose.rotations[bone as ControlBone]
      if (r) {
        x += r[0] * w
        y += r[1] * w
        z += r[2] * w
      }
    }
    rotations[bone] = [
      x / totalWeight,
      y / totalWeight,
      z / totalWeight,
    ]
  }

  const blendShapes: Record<string, number> = {}
  for (const bs of allBS) {
    let v = 0
    for (const [pid, w] of poseWeights) {
      const pose = POSE_TARGETS[pid]
      if (!pose) continue
      v += (pose.blendShapes[bs] ?? 0) * w
    }
    blendShapes[bs] = v / totalWeight
  }

  // 取第一个非 idle 姿态的元数据
  let metaPose = POSE_TARGETS.idle
  for (const [pid] of poseWeights) {
    if (pid !== 'idle' && POSE_TARGETS[pid]) {
      metaPose = POSE_TARGETS[pid]
      break
    }
  }

  return {
    label: metaPose.label,
    icon: metaPose.icon,
    tags: metaPose.tags,
    energy: 0,
    duration: 0,
    loop: false,
    rotations,
    blendShapes,
  }
}

// ─── 约束验证 ────────────────────────────────────────────────────

/**
 * 验证姿态是否在生物力学约束内
 *
 * @returns 违规列表 (空 = 通过)
 */
export function validatePoseConstraints(
  pose: PoseTarget
): { bone: string; axis: string; value: number; limit: [number, number] }[] {
  const violations: {
    bone: string
    axis: string
    value: number
    limit: [number, number]
  }[] = []

  for (const [boneName, rot] of Object.entries(pose.rotations)) {
    const limits = JOINT_CONSTRAINTS[boneName]
    if (!limits) continue

    const r = rot as [number, number, number]
    if (r[0] < limits.x[0] || r[0] > limits.x[1]) {
      violations.push({ bone: boneName, axis: 'x', value: r[0], limit: limits.x })
    }
    if (r[1] < limits.y[0] || r[1] > limits.y[1]) {
      violations.push({ bone: boneName, axis: 'y', value: r[1], limit: limits.y })
    }
    if (r[2] < limits.z[0] || r[2] > limits.z[1]) {
      violations.push({ bone: boneName, axis: 'z', value: r[2], limit: limits.z })
    }
  }

  return violations
}

/**
 * 自动修复越界姿态 — 将所有旋转钳制到约束范围内
 */
export function clampPoseToConstraints(pose: PoseTarget): PoseTarget {
  const clampedRotations: Record<string, [number, number, number]> = {}
  for (const [boneName, rot] of Object.entries(pose.rotations)) {
    const limits = JOINT_CONSTRAINTS[boneName]
    clampedRotations[boneName] = limits
      ? clampEuler(rot as [number, number, number], limits)
      : (rot as [number, number, number])
  }
  return { ...pose, rotations: clampedRotations }
}
