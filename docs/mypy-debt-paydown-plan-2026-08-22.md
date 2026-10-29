# mypy 存量债分期偿还计划（2026-08-22）

**背景**：`python-tests.yml` 的 mypy 步骤已改为 `scripts/mypy_ratchet.py --check-changed`（T1.2 补全），
存量 955 错误（251 文件）锁入 `scripts/mypy-baseline.txt`，**仅拦截变更文件中基线外的新错误**，
存量不再误杀、也不再膨胀。本计划定义存量如何分期偿还。

## 实测分布（955 错误 / 243 文件）

| 错误码 | 数量 | 性质 | 偿还风险 |
|--------|------|------|---------|
| attr-defined | 366 | 多为动态属性/运行时设置，非真错误 | 低（加 `# type: ignore` 或 `cast`） |
| arg-type | 151 | 调用方类型不匹配，可能暴露真 bug | 中（需看上下文） |
| assignment | 101 | 赋值类型不符，可能暴露真 bug | 中 |
| union-attr | 92 | Optional 未收窄，加 `if x:` 即可 | 低 |
| misc | 44 | 杂项，逐处评估 | 低-中 |
| operator | 32 | 运算符操作数类型模糊 | 低 |
| var-annotated | 26 | 缺变量注解，纯加 annotation | 极低 |
| return-value | 25 | 返回值类型不符 | 中 |
| index | 22 | 下标访问类型模糊 | 低 |
| call-arg | 12 | 调用参数不匹配 | 中 |
| override | 12 | 重写签名不一致 | 中 |
| name-defined | 11 | 未定义名，可能真 bug | 中-高 |
| import-untyped | 11 | 第三方无类型，加 `type: ignore` | 极低 |
| valid-type | 9 | 类型表达式非法 | 低 |

**Top 文件**：`__init__.py`(63)、`_constants.py`(26)、`_note_sync_mixin.py`(19)、`chat_importer.py`(19)、
`test_p0_10_app_context_builder.py`(19)、`headless_main.py`(18)、`web_bridge.py`(17)、`react_executor.py`(17)。

## 分期策略

### 第 1 期（极低风险，纯标注，建议 1–2 次 PR）
- `var-annotated`(26) + `import-untyped`(11) + `union-attr`(92 中简单处) → 加注解/`# type: ignore`
- 预计消除 ~120–130 处，零行为风险。

### 第 2 期（中风险，需看调用上下文）
- `arg-type`(151) + `assignment`(101)：逐文件修，可能暴露真实类型 bug（优先 `config.py`/`environment.py`/`app_context.py`）。
- 预计消除 ~250 处。

### 第 3 期（契约级）
- `return-value`(25) + `call-arg`(12) + `override`(12) + `name-defined`(11)：接口签名对齐。
- 预计消除 ~60 处。

### 长尾
- `attr-defined`(366)：动态属性为主，逐模块加 `cast`/协议类，或确认后 `# type: ignore[attr-defined]`。
- `misc`/`operator`/`index` 等分散项随各模块改造顺带处理。

## 执行纪律
- 每期 PR 只需改对应文件，**棘轮保证不引入新错误**；从 `mypy-baseline.txt` 中删除已修行即"解锁"该错误重新受监控。
- 禁止一次性全量 `# type: ignore` 刷屏——每处 ignore 需注释理由。
- 目标：3 期内将存量从 955 压到 <300，之后转持续维持模式。
