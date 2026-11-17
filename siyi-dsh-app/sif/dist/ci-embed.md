# CI 固化说明（给仓库维护者）

## 为什么需要这一步

思忆集当前所有修复都落在两个位置：

```
/storage/emulated/0/思忆集/plugins/*.js     用户插件
/storage/emulated/0/思忆集/app/sif/core/*   领域核心模块
```

这两个位置**不在 APK 内**。一旦覆盖安装 APK：

- `EngineInstaller` 会删除并重解压 `dsh-home`
- `/sdcard/思忆集/plugins/` 虽然保留，但**若用户清理存储或换机就会丢失**
- `sif/core/` 完全依赖手工维护

结论：**这些改动必须进仓库 + CI，否则不可持续。**

## 需要固化进仓库的文件

```
siyi-dsh-app/
  plugins/
    siyi-vault.js               ← 含 4 个补丁
    siyi-vault-tools.js
    siyi-fallback.js
    siyi-quota.js
    siyi-permission-guide.js
  sif-core/                     ← 新增目录
    vault/service.mjs
    vault/indexer.mjs
    vault/atomic.mjs
    vault/paths.mjs
    vault/capabilities.mjs
    bridge/codec.mjs
    bridge/journal.mjs
    bridge/server.mjs
```

## CI 需要新增的步骤

### 步骤 1：把 sif-core 打进 web-profile.zip

在 `Build web profile` 步骤中追加：

```yaml
- name: Embed SIF core
  run: |
    set -e
    PROF="${{ github.workspace }}/siyi-dsh-app/profile-build/profiles/web"
    SRC="${{ github.workspace }}/siyi-dsh-app/sif-core"
    test -d "$SRC" || { echo "缺少 sif-core"; exit 1; }
    DST="$PROF/node_modules/@siyi/sif-core"
    mkdir -p "$DST"
    cp -r "$SRC/." "$DST/"
    # 校验关键文件存在
    test -f "$DST/vault/service.mjs" || { echo "缺 service.mjs"; exit 1; }
    test -f "$DST/vault/indexer.mjs" || { echo "缺 indexer.mjs"; exit 1; }
    echo "sif-core 已嵌入: $(ls "$DST/vault" | wc -l) 个文件"
```

### 步骤 2：把用户插件打进 web-profile.zip

```yaml
- name: Embed siyi user plugins
  run: |
    set -e
    PROF="${{ github.workspace }}/siyi-dsh-app/profile-build/profiles/web"
    PLUG="${{ github.workspace }}/siyi-dsh-app/plugins"
    DST="$PROF/node_modules/dsh-user-plugins/bundled"
    mkdir -p "$DST"
    cp "$PLUG"/siyi-*.js "$DST/"
    echo "已打包插件: $(ls "$DST" | wc -l) 个"
```

### 步骤 3：安装时铺到 /sdcard

在 `EngineInstaller.kt` 的 `ensureAll()` 末尾追加：把 `bundled` 目录复制到
`/sdcard/思忆集/plugins/`，**但跳过已存在的文件**（保留用户手工修改）。

或者更保守：只复制不存在的文件。

### 步骤 4：校验

```yaml
- name: Verify embedded bundle
  run: |
    set -e
    Z="siyi-dsh-app/app/src/main/assets/web-profile.zip"
    C=$(unzip -p "$Z" 'node_modules/@siyi/sif-core/vault/service.mjs' | grep -c 'createIndexer' || true)
    echo "sif-core createIndexer: $C"
    test "$C" -gt 0 || { echo "sif-core 未正确嵌入"; exit 1; }
    P=$(unzip -l "$Z" | grep -c 'bundled/siyi-' || true)
    echo "bundled 插件数: $P"
    test "$P" -ge 5 || { echo "插件未全部嵌入"; exit 1; }
```

## 版本与校验

`dist/manifest.json` 记录了每个文件的 sha256。CI 构建后应比对：

```yaml
- name: Compare against manifest
  run: |
    node -e '
      const m = require("./siyi-dsh-app/sif-dist-manifest.json");
      // 与仓库内文件逐项比对 sha256
    '
```

## 迁移注意事项

1. **不要直接复制运行目录**。运行目录里可能有临时调试代码。
   以 `sif/dist/` 为准，它经过 `--check` 校验。

2. **插件里的绝对路径要改**。当前 `siyi-vault-tools.js` 用绝对路径
   import sif-core：

   ```js
   import { createVaultService } from '/storage/emulated/0/思忆集/app/sif/core/vault/service.mjs'
   ```

   固化进包后应改为相对包名：

   ```js
   import { createVaultService } from '@siyi/sif-core/vault/service.mjs'
   ```

   或保持绝对路径但在部署脚本里创建软链。

3. **数据目录不能进包**。`/sdcard/思忆集/vault/` 是用户数据，
   任何情况下不能打进 APK。

4. **测试也要进仓库**。`sif/tests/contract/` 共 146 项，
   是防止回归的唯一保障。CI 应跑一遍。
