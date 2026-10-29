import { CapacitorConfig } from '@capacitor/core'

const config: CapacitorConfig = {
  appId: 'com.siyi.notepad',
  appName: '思忆便签',
  webDir: 'dist',
  server: {
    androidScheme: 'http',
    allowNavigation: ['192.168.*.*', '10.*.*.*', '172.*.*.*'],
  },
  plugins: {
    DeepLinks: {
      schemes: ['siyi'],
    },
    SplashScreen: {
      launchShowDuration: 1000,
      launchAutoHide: true,
      backgroundColor: '#FFFFFF',
      androidScaleType: 'CENTER_CROP',
      showSpinner: false,
    },
    LocalNotifications: {
      smallIcon: 'ic_stat_icon_config_sample',
      iconColor: '#007AFF',
    },
    Badge: {
      persist: true,
      autoClear: false,
    },
    FloatingBall: {
      // 悬浮球配置
    },
  },
}

export default config
