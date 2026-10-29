# {release_version} ({release_channel})

发布日期: {release_date}

## 功能变化
- {feature_changes}

## 已知问题
- {known_issues}

## 回滚步骤
1. 停止当前发布批次（灰度扩容暂停）。
2. 将应用版本回退到 {rollback_version}。
3. 导入最近一次快照并重启应用。
4. 复核启动、同步、配置保存、布局应用链路。
