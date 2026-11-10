# 设备桥原生辅助：只编一个极小的 so，不引入任何第三方依赖。
#
# 关键：必须用 c++_static（甚至纯 C 不链 libc++），否则 AGP 会把 NDK 自带的
# libc++_shared.so 打进 APK，覆盖官方 APK 里那份 9.3MB 的完整版，
# 而 libnode.so 依赖完整版，替换后引擎直接崩。
cmake_minimum_required(VERSION 3.18)
project(siyi_bridge C)

add_library(siyi_bridge SHARED bridge.c)

# 纯 C 实现，不链 libc++；只链系统 log
find_library(log-lib log)
target_link_libraries(siyi_bridge ${log-lib})

# 明确不打包任何 STL 运行时
set_target_properties(siyi_bridge PROPERTIES
    ANDROID_STL "none"
    POSITION_INDEPENDENT_CODE ON
)
