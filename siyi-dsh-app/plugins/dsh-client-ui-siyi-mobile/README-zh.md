# dsh-client-ui-siyi-mobile

DeepSeek Harness 的窄屏移动端布局补丁。

## 修什么

- **图标栏**：手机上左侧轨道占掉 12–14% 屏宽，主内容被挤压。
  本插件给 `<html>` 打上 `data-windows-titlebar`，让布局把折叠轨道宽度
  算成 0，侧边栏变成抽屉 —— 与官方 DSH 手机端一致。
- **设置面板**：376px 视口下固定 188px 左栏只剩约 140px 给内容，中文被迫逐字换行。
  在 `max-width: 720px` 时面板改为单列，左栏变成横向可滚动的标签栏。
- **通用弹窗**：放宽宽度上限，避免按钮点不到。

## 结构

| 文件 | 作用 |
|---|---|
| `lib/index.js` | 服务端半身。不注册任何服务；存在是因为客户端插件包必须有 `main` 入口。 |
| `lib/client.js` | 浏览器半身。注入一个 `<style>`，并用 `MutationObserver` 维持 `<html>` 属性。 |

## 安装

把包放进 profile 的 `node_modules`，在 `cordis.patch.yml` 里引用：

```yaml
- insert:
    - id: siyi-mobile-layout
      name: 'dsh-client-ui-siyi-mobile'
```

然后重启，让浏览器半身重新加载。

## 注意

样式表依赖官方构建产出的哈希类名（`.VOzbGW_panel`、`.pI_x6G_sidebarCol`、
`.hHd-Xa_toggle`）。上游一旦重新生成这些哈希，补丁会静默失效
（样式不再匹配，但不报错）。每次升级 DSH 后都要复查这个文件。

## 许可

MIT
