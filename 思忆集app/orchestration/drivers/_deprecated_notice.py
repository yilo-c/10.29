# ---------------------------------------------------------------------------
# 弃用说明（deprecation notice）
# ---------------------------------------------------------------------------
#
# 本目录下的 openclaw_driver.py / hermes_driver.py 属于「已下线」驱动：
#
#   - OpenClawDriver —— 依赖外部 `openclaw` CLI
#   - HermesDriver   —— 依赖外部 `hermes` CLI
#
# 二者都只是把任务原样丢给一个外部子进程，本机通常并不存在对应的可执行文件，
# 于是 shutil.which() 永远返回 None，agent_info.is_available 恒为 False。
# 注册进 AgentRegistry 之后除了让能力列表变长、让路由多做无谓的可用性探测之外，
# 不产生任何实际收益，因此 orchestration/setup.py 已不再注册它们。
#
# 保留源码而不是直接删除，原因有二：
#   1. 它们是 AbstractAgentDriver 协议的完整实现范例，新增 CLI 驱动时可直接照抄；
#   2. 若将来这两个 CLI 真正落地，只需在 setup.py 重新注册即可恢复，
#      无需从头再写一遍子进程调度 / 超时 / 取消 / JSON 解析逻辑。
#
# 维护约定：
#   - 不要给这两个驱动新增功能，只在 AbstractAgentDriver 协议变更时同步修改；
#   - 真正在用的驱动是 claude_code_driver / codex_driver / trae_driver，
#     新逻辑请加在那边；
#   - 本文件不导出任何符号，import 它没有副作用，仅作为文档存在。
#
# ---------------------------------------------------------------------------
