#!/system/bin/sh
# 重启后验证脚本：确认路径补丁与 vault 工具都已生效
# 用法：sh /storage/emulated/0/思忆集/app/sif/scripts/post-restart-verify.sh

LOG=/storage/emulated/0/思忆集/app/engine-stderr.log
PIDFILE=/data/data/com.siyi.dshapp/files/engine.pid

echo "=== 1. 引擎 PID（应与重启前不同）==="
cat "$PIDFILE" 2>/dev/null || echo "(无 pid 文件)"
echo

echo "=== 2. vault 工具是否注册 ==="
if grep -aq 'siyi-vault-tools' "$LOG" 2>/dev/null; then
  grep -a 'siyi-vault-tools' "$LOG" | tail -3
else
  echo "FAIL: 日志里没有 siyi-vault-tools，插件未加载"
fi
echo

echo "=== 3. 用户插件扫描数量 ==="
grep -a 'siyi-user-plugins.*扫描' "$LOG" 2>/dev/null | tail -1 || echo "(无扫描记录)"
echo

echo "=== 4. 插件加载清单 ==="
grep -a '已加载' "$LOG" 2>/dev/null | tail -8
echo

echo "=== 5. 是否有插件加载失败 ==="
if grep -aq 'siyi-user-plugins.*warn' "$LOG" 2>/dev/null; then
  grep -a 'siyi-user-plugins.*warn' "$LOG" | tail -5
else
  echo "OK: 无 warn"
fi
echo

echo "=== 6. 引擎 3180 是否就绪 ==="
if command -v curl >/dev/null 2>&1; then
  curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:3180/ || echo "curl 失败"
else
  echo "(无 curl，用 node 探测)"
  node -e 'const http=require("http");const r=http.request({host:"127.0.0.1",port:3180,path:"/",method:"GET",timeout:3000},res=>{console.log("HTTP",res.statusCode);res.resume()});r.on("error",e=>console.log("ERR",e.code||e.message));r.on("timeout",()=>{console.log("TIMEOUT");r.destroy()});r.end()'
fi
