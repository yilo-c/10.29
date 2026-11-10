# 设备桥原生辅助：socketpair(2) 的 JNI 封装。
#
# 核心约束：绝对不能把 NDK 自带的精简版 libc++_shared.so 打进 APK。
# 官方 APK 里那份是 8.9MB 完整版，libnode.so 依赖它；换成 NDK 的 1.3MB
# 精简版后引擎启动即崩。
#
# 两道保险：
#   1. build.gradle 传 -DANDROID_STL=none（构建期不链 STL，不产出该 so）
#   2. 本文件再显式声明一次，防止 IDE 或其他入口绕过 gradle 参数
cmake_minimum_required(VERSION 3.18)

# 必须在 project() 之前设置
set(ANDROID_STL none CACHE STRING "" FORCE)

project(siyi_bridge C)

add_library(siyi_bridge SHARED bridge.c)

# 纯 C 实现，只链系统 log 与 libc
find_library(log-lib log)
target_link_libraries(siyi_bridge ${log-lib})

set_target_properties(siyi_bridge PROPERTIES POSITION_INDEPENDENT_CODE ON)
