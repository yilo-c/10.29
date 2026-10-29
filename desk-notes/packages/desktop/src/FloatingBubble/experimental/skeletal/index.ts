/**
 * 思忆集 VRM 骨骼动画系统
 *
 * 三层架构:
 *   GPT-5.5 (低频决策) → decisionEngine (中频评分) → poseInterpolator (60fps 插值)
 *
 * 快速开始:
 *   import { createDecisionEngine, decidePose, createInterpolationState, updatePose, poseToBoneMap } from './skeletal'
 *
 *   const engine = createDecisionEngine('playful')
 *   const interp = createInterpolationState('idle')
 *
 *   // 决策循环 (每 500ms)
 *   setInterval(() => {
 *     const decision = decidePose(engine, inputContext)
 *     if (decision.changed) requestPose(interp, decision.pose, decision.transitionSpeed)
 *   }, 500)
 *
 *   // 渲染循环 (每帧)
 *   function animate(deltaMs: number) {
 *     const blended = updatePose(interp, deltaMs)
 *     const boneMap = poseToBoneMap(blended)
 *     // 应用 boneMap 到 Three.js VRM 骨骼...
 *   }
 */

export {
  // 骨骼数据
  VRM_BONE,
  BONE_NAME,
  CHARACTER_PROPS,
  JOINT_CONSTRAINTS,
  BLEND_SHAPE,
  POSE_TARGETS,
  CONTROL_BONES,
} from './boneSystem'
export type { JointLimits, PoseTarget, ControlBone } from './boneSystem'

export {
  // 插值引擎
  createInterpolationState,
  requestPose,
  updatePose,
  getBlendedPose,
  getTransitionProgress,
  isTransitioning,
  blendPoses,
  validatePoseConstraints,
  clampPoseToConstraints,
} from './poseInterpolator'
export {
  lerpPose,
  applyMicroMotion,
  poseToBoneMap,
  poseToBlendMap,
  lerpEuler,
  clampEuler,
} from './boneSystem'
export type { InterpolationState } from './poseInterpolator'

export {
  // 决策引擎
  createDecisionEngine,
  decidePose,
  setPersonalityMode,
  forcePose,
  handleUserAction,
  getCurrentPoseTarget,
  getDebugInfo,
} from './decisionEngine'
export type {
  PersonalityMode,
  DecisionInput,
  Decision,
  DecisionEngineState,
} from './decisionEngine'
