/** All pet pose identifiers */
export type XiaoWenPose =
  | 'idle'
  | 'clickDefend'
  | 'shy'
  | 'dragFly'
  | 'land'
  | 'combo3'
  | 'chatOpen'
  | 'notice'
  | 'glance'
  | 'hmph'
  | 'blink'

/** All state-machine action identifiers */
export type XiaoWenAction =
  | 'idle'
  | 'clickDefend'
  | 'shy'
  | 'dragFly'
  | 'land'
  | 'combo3'
  | 'combo5'
  | 'chatOpen'
  | 'notice'
  | 'glance'
  | 'hmph'
  | 'blink'
  | 'sleepy'
  | 'startup'

export interface XiaoWenFrame {
  key: string
  src: string
}

export interface XiaoWenPoseClip {
  frames: XiaoWenFrame[]
  durations: number[] | number
  loop: boolean
  fallbackKey: 'sprite_idle' | 'xiaowen_fullbody'
}

export interface PetMachineRefs {
  curPoseRef: React.MutableRefObject<XiaoWenPose>
  isFlyingRef: React.MutableRefObject<boolean>
  holdTimerRef: React.MutableRefObject<number>
  mouseDownRef: React.MutableRefObject<boolean>
  mouseStartRef: React.MutableRefObject<{ x: number; y: number }>
}

export interface PetMachine {
  refs: PetMachineRefs
  line: string | null
  tick: (dt: number) => void
  onClick: () => void
  onDoubleClick: () => void
  onMouseDown: (e: React.MouseEvent) => void
  onMouseMove: (e: React.MouseEvent) => void
  onMouseUp: () => void
  trigger: (action: XiaoWenAction) => void
}

export interface Particle {
  x: number; y: number; vx: number; vy: number
  life: number; maxLife: number; size: number
  color: string; opacity: number
}
