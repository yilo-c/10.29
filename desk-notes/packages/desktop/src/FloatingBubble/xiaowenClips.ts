import type { XiaoWenPose, XiaoWenFrame, XiaoWenPoseClip } from './xiaowenTypes'

const frame = (folder: string, key: string): XiaoWenFrame => ({
  key,
  src: `pet/xiaowen/frames/${folder}/${key}.png`,
})

const frames = (folder: string, keys: string[]): XiaoWenFrame[] => (
  keys.map(key => frame(folder, key))
)

export const XIAOWEN_FALLBACK_FRAMES: XiaoWenFrame[] = [
  { key: 'sprite_idle', src: 'pet/sprite_idle.png' },
  { key: 'xiaowen_fullbody', src: 'pet/xiaowen_fullbody.png' },
  { key: 'xiaowen_reference_fullbody', src: 'pet/xiaowen/references/xiaowen_fullbody.png' },
]

export const XIAOWEN_POSE_CLIPS: Record<XiaoWenPose, XiaoWenPoseClip> = {
  idle: {
    frames: frames('idle_ns', [
      'idle_ns_01', 'idle_ns_02', 'idle_ns_03', 'idle_ns_04', 'idle_ns_05', 'idle_ns_06',
      'idle_ns_07', 'idle_ns_08', 'idle_ns_09', 'idle_ns_10', 'idle_ns_11', 'idle_ns_12',
    ]),
    durations: 135,
    loop: true,
    fallbackKey: 'sprite_idle',
  },
  clickDefend: {
    frames: frames('click_defend', [
      'click_defend_01', 'click_defend_02', 'click_defend_03',
      'click_defend_04', 'click_defend_05', 'click_defend_06',
    ]),
    durations: [120, 100, 100, 90, 90, 350],
    loop: false,
    fallbackKey: 'sprite_idle',
  },
  shy: {
    frames: frames('shy', [
      'shy_01', 'shy_02', 'shy_03',
      'shy_04', 'shy_05', 'shy_06',
    ]),
    durations: [220, 140, 160, 180, 200, 350],
    loop: false,
    fallbackKey: 'sprite_idle',
  },
  dragFly: {
    frames: frames('drag_fly', [
      'drag_fly_01', 'drag_fly_02', 'drag_fly_03', 'drag_fly_04',
    ]),
    durations: 110,
    loop: true,
    fallbackKey: 'sprite_idle',
  },
  land: {
    frames: frames('land', ['land_01', 'land_02', 'land_03', 'land_04', 'land_05', 'land_06']),
    durations: [120, 100, 120, 140, 160, 240],
    loop: false,
    fallbackKey: 'sprite_idle',
  },
  combo3: {
    frames: frames('combo3', [
      'combo3_01', 'combo3_02', 'combo3_03',
      'combo3_04', 'combo3_05', 'combo3_06', 'combo3_07',
    ]),
    durations: [80, 80, 80, 90, 100, 120, 220],
    loop: false,
    fallbackKey: 'sprite_idle',
  },
  chatOpen: {
    // 无专属分镜素材，复用 idle_ns 帧保持形象一致
    frames: frames('idle_ns', [
      'idle_ns_01', 'idle_ns_02', 'idle_ns_03', 'idle_ns_04', 'idle_ns_05', 'idle_ns_06',
      'idle_ns_07', 'idle_ns_08', 'idle_ns_09', 'idle_ns_10', 'idle_ns_11', 'idle_ns_12',
    ]),
    durations: 135,
    loop: true,
    fallbackKey: 'sprite_idle',
  },
  notice: {
    frames: frames('notice', [
      'notice_01', 'notice_02', 'notice_03', 'notice_04',
    ]),
    durations: [160, 140, 220, 160],
    loop: false,
    fallbackKey: 'sprite_idle',
  },
  glance: {
    frames: frames('i2_glance', [
      'i2_glance_01', 'i2_glance_02', 'i2_glance_03', 'i2_glance_04',
      'i2_glance_05', 'i2_glance_06', 'i2_glance_07', 'i2_glance_08',
    ]),
    durations: [150, 150, 150, 150, 150, 160, 200, 340],
    loop: false,
    fallbackKey: 'sprite_idle',
  },
  hmph: {
    frames: frames('i3_hmph', [
      'i3_hmph_01', 'i3_hmph_02', 'i3_hmph_03', 'i3_hmph_04',
      'i3_hmph_05', 'i3_hmph_06', 'i3_hmph_07', 'i3_hmph_08',
    ]),
    durations: [160, 160, 160, 160, 160, 170, 210, 360],
    loop: false,
    fallbackKey: 'sprite_idle',
  },
  blink: {
    frames: frames('idle_i1_ns', [
      'idle_i1_ns_01', 'idle_i1_ns_02', 'idle_i1_ns_03',
      'idle_i1_ns_04', 'idle_i1_ns_05', 'idle_i1_ns_06',
    ]),
    durations: [40, 35, 45, 35, 40, 75],  // 一瞬间完成（全流程 ~270ms）
    loop: false,
    fallbackKey: 'sprite_idle',
  },
}
